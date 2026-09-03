"""The staging area: compose decisions, review them, then apply atomically.

Staged ops live in `.dgraph-pending.json` (gitignored) and do not touch the
store until `apply`. Apply mutates a copy, validates it, and only then writes —
a tool that can corrupt the source of truth is worse than no tool.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import threading
from collections.abc import Callable
from dataclasses import replace as _dc_replace
from datetime import date as _date
from pathlib import Path

from dgraph import areas as _areas
from dgraph import env, limits, project, ranges
from dgraph.model import (CLAIM, PAYLOAD, SIMPLE_STATUSES, UNSETTLED, Bind,
                          Edge, Graph, Probe, Vertex, bind_fault, probe_fault,
                          status_fault)
from dgraph.violation import Violation

OPS = {"close", "reopen", "add_vertex", "add_edge", "remove_edge",
       "remove_vertex", "set_status", "set_fields", "reject", "reprobe",
       "bind", "unbind"}

#: What `set_fields` may write, in **both** stores. One tuple, imported by
#: `task_pending`, because a decision and a task differ in everything except
#: these four and a second copy is how one store came to accept a field the
#: other refused — the shape most of this tool's audit findings took.
#:
#: What is deliberately absent is everything that is a *claim*: an answer, a
#: falsifier, an outcome, a reason work stopped. Those are dated assertions
#: with an archival record behind them, and rewriting one in place is the act
#: this whole model exists to refuse. A title and an area are not claims — a
#: title is how a question is referred to, not something the question says.
FIELDS = ("title", "area", "note", "format")

#: Everything else a `set_fields` op may carry: which record it addresses, and
#: the tray's own bookkeeping. Named so that any *other* key can be refused —
#: `{"op": "set_fields", "vertex": "D01", "answer": "…"}` would otherwise stage
#: cleanly, report success and write nothing of the sort, which is a worse
#: failure than the refusal it is trying to get past.
FIELD_KEYS = ("op", "vertex", "task", "ref", "saw", "by")

# ---- areas ---------------------------------------------------------------
#
# `dgraph/areas.py` is what an area *is* -- the registry, how two spellings are
# compared, and the order records render in. What is here is the one thing that
# needs a *writer*: whether this caller may file under an area nobody has used
# yet, which is a question about `pending.owner()` and about the launcher's
# `$DG_AREA`, and therefore belongs at the staging door with the other two.
#
# An area is a **label on a record**, not a schema, and `FIELDS` above is where
# that was already decided: `dg amend D05 --area corpus` supersedes nothing and
# archives nothing, because "a title and an area are not claims". A label wants
# a *registry*, not a whitelist -- so `areas` accumulates in first-use order,
# written by the op that first uses one, and membership is checked nowhere.
#
# WHAT WENT WRONG WITH THE WHITELIST. No op wrote `areas`: it was the one field
# of either store that only `init` and `import` set, which `integrate.py` had
# to file as `unexpressible` -- reported, never fixable. A scout that found a
# new corner of a project could not file anything under it, and the failure
# arrived as a wall of identical refusals rather than as one nameable cause.
# Worse, the two stores' lists were independent while `dg areas` and
# `/api/areas` both said in as many words that "the stores share their areas",
# so three commands were enough to reach a store pair whose lists disagreed:
#
#     dg init --areas corpus,harness      # decisions.json gets three
#     dg task init                        # tasks.json defaulted to General
#     dg task add --area corpus           # unknown area. one of: General
#
# WHAT REPLACES IT. Membership was catching typos, and dropping it without a
# replacement would let a multi-writer fan-out fragment `corpus` into `Corpus`
# with nothing to notice. So a **genuinely new** area is checked for similarity
# against the ones already in use; an area that already exists is silent, which
# is both the common case and the one that matters -- an `amend` *toward* an
# existing area is the fix for a typo, and a guard that refused it because it
# resembles the typo would be backwards.
#
# WHAT IT DOES NOT REPLACE, and this paragraph used to claim otherwise. The
# guard catches what a machine can be certain of from two strings: a spelling
# that normalises to one in use, and a slip of a character or two. It does not
# catch `corpus-design` under a project that has `corpus`, at any threshold
# that does not also flag unrelated short names -- and should not, because that
# is a *sub-area* and not a misspelling. Whether an agent may coin one at all
# is `$DG_AREA`, set by the launcher and refusing an agent every area new to
# the union; whether anybody should look at the one it coined is a question for
# the surfaces that review staged work, not for a refusal. See
# `dgraph/areas.py`; audit `R-F3`.
#
# WHERE IT IS JUDGED. At `stage_all`, the one staging function -- for both
# trays and every door, exactly as `$DG_TERSE` is, and for the same sentence.
# It was on three composing doors and missing from the one that takes ops as
# data, which is `POST /api/pending` and every op adopted from another writer's
# contribution. Audit `R-F2`.
#
# READ THE UNION, WRITE YOUR OWN STORE. The guard reads both stores' areas,
# because sharing the areas is the point; but every op appends only to the
# store it touches. A composite op spanning both trays would let one batch land
# and the other fail -- leaving the two lists divergent *because of* the
# feature meant to unify them -- and `dg apply`'s independence of the two
# batches predates this problem and is load-bearing.


#: The primitives, under the names the staging modules ask for them by. One
#: definition in `dgraph/areas.py`; this is the door, not a second copy.
area_counts = _areas.counts
stored_area_counts = _areas.stored_counts
normal_area = _areas.normal
similar_areas = _areas.similar


def refuse_area(area: str, *, own: dict[str, int], other: dict[str, int],
                owner: str | None, new_area: bool = False,
                chosen: str | None = None) -> str | None:
    """Why this area may not be filed under yet -- or `None` if it may.

    `own` is the registry of the store being written and `other` its twin's;
    the union of the two is what "already in use" means, so an area known only
    to `tasks.json` suppresses the guard on a decision. Divergence between the
    two files stops mattering the moment nothing validates membership.

    `owner` is `pending.owner()`, and `None` is the supervisor, exactly as in
    `cross.refuse_close` and `limits.refuse_write`: `$DG_AREA` is a rule a
    launcher sets for the agents it spawns, and a person at a terminal is never
    refused by it. The similarity guard, by contrast, applies to **everybody** --
    it is catching a typo, and a supervisor makes those too.

    Stage-time, not apply-time, for the reason `vet_fields` gives: a tray may be
    shared, so an op that cannot apply wedges it for every other writer until
    somebody drops it. That is also why `new_area` does not persist into the
    op -- `apply` never rechecks, and a flag written into the tray would be a
    permission travelling with a record.
    """
    if not (area or "").strip():
        return None
    known = {**own, **other}
    if area in known:
        return None
    if owner is not None and env.area_policy(chosen) == "strict":
        # Not overridable by `--new-area`, deliberately. The flag answers "is
        # this new area intentional?", which is the author's question;
        # `$DG_AREA` answers "may an agent invent areas at all?", which is the
        # launcher's, and a rule the thing it constrains can switch off is not
        # a rule. The refusal names its own fix the way the `evidence` one
        # does: the area is a proposal, and `dg pending` is where it is read.
        return (f"${env.AREA_ENV}=strict: {owner} may file only under an area "
                f"already in use, and {area!r} is new. The area you want is a "
                f"proposal for a person — say so in a note on a record filed "
                f"under one of "
                f"{', '.join(sorted(known)) or '(none yet — a person has to start the vocabulary)'}"
                f", and `dg pending --agent {owner}` is where they will read "
                f"it")
    if new_area:
        return None
    close = _areas.similar(area, known)
    if not close:
        return None
    rows = []
    for a in close:
        counts = [f"{own[a]} in this store"] if own.get(a) else []
        if other.get(a):
            counts.append(f"{other[a]} in the other")
        rows.append(f"    {a}" + (f"  ({', '.join(counts)})" if counts else
                                  "  (registered, nothing filed under it)"))
    return ("area {!r} is new, and close to areas already in use:\n{}\n"
            "If it is genuinely a different area, restage with --new-area. "
            "Areas accumulate: nothing has to be declared first."
            .format(area, "\n".join(rows)))


#: How a removal reconnects what the vertex sat between.
#:
#: `sever` is the default and the only one that cannot lie: it drops claims and
#: invents none, leaving a state the validators already describe — a new root,
#: an orphan, a premise that vanished. The other two **assert an edge nobody
#: wrote**, which is why they are gated to a human (see `dgraph/gate.py`) and
#: why they refuse to write into a decided edge: attaching an answer to a
#: question it never opened is the one claim this model must not manufacture.
REMOVAL_MODES = ("sever", "splice", "into")

#: An extra validator over a proposed graph — see `apply_all`.
Checker = Callable[[Graph], list[Violation]]


class ApplyError(RuntimeError):
    pass


class Collision(ApplyError):
    """An op refused because **another writer already applied it**.

    A subclass, so every existing `except ApplyError` keeps catching it and the
    only thing that changes is what a caller may say when it wants to. What it
    wants to say is different in the way that matters most: an ordinary
    `ApplyError` means the batch was refused and the work is still only in the
    tray, while this one means the work is *in the store* — somebody else put it
    there — and the tray holds a duplicate.

    The distinction is not cosmetic. Both hosts head an `ApplyError` with
    "aborted, nothing written", which is true of the apply and false of the
    work, and a model that reads it as "my work failed" re-stages under a fresh
    id and puts two vertices behind one question. That was audit F17, and the
    message alone did not fix it: the headline is what gets acted on.
    """


def _clip(text: str) -> str:
    """An answer's first line, cut to label length — the fallback for the two
    places a short label is filed when none was given."""
    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    return first if len(first) <= 60 else first[:59] + "…"


# ---- store ---------------------------------------------------------------


def load(path: Path | None = None) -> list[dict]:
    p = path or project.find().pending
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def save(ops: list[dict], path: Path | None = None) -> None:
    p = path or project.find().pending
    if ops:
        project.write_atomic(p, json.dumps(ops, indent=2, ensure_ascii=False) + "\n")
    elif p.exists():
        p.unlink()


def held(path: Path | None = None, *, wait: float = project.LOCK_WAIT):
    """Hold the tray for a read-modify-write, so two writers cannot lose one.

    Every mutation below is load-then-save, and without this two of them
    interleaving means the second one writes a list built before the first
    one's op existed — the op is gone, with no error and nothing in any diff.
    That is the failure the whole staging area exists to prevent from `git`;
    it should not be reachable from the tool itself. It is not hypothetical:
    `dg serve` is threaded, and `commands/serve.md` tells the user to work
    in the browser and a terminal at once.

    The lock itself is `project.held` — the same one `dgraph/applying.py` holds
    over the *store*, since a tray protected against a lost update feeding a
    store that is not only moves the loss one file along.
    """
    return project.held(path or project.find().pending, wait=wait)


# ---- stable ids for staged ops -------------------------------------------
#
# A tray op used to be addressed only by its position, and position is not
# stable: `discard` takes applied ops out **by value from wherever they sit**,
# which is what keeps a concurrent writer's work staged, and every index after
# them shifts. An index read off an earlier `dg pending` could therefore name a
# different op by the time it was used — audit F29, where `dg drop 2` removed
# `D06` after another writer applied two ops, and said `dropped op 2`.
#
# So each op gets an id when it enters the tray, and `resolve` below accepts
# either. The id is what a second reader should quote; the index stays because
# every message and doc in the tool names one, and because it is what a single
# writer sees and reasons about.

#: Letters only, so an id can **never** be read as an index — `dg drop 12` is
#: unambiguously a position and `dg drop kfnq` unambiguously an id. An alphabet
#: that could produce `12` would put the ambiguity back where the bug was.
#: `ilo` are left out because they are the characters a terminal font makes
#: hardest to tell from `1` and `0`.
REF_ALPHABET = "abcdefghjkmnpqrstuvwxyz"
REF_LEN = 4


#: Who this process stages as, when nothing says otherwise. An **env var** so a
#: launcher can hand each agent an identity without every command growing a
#: flag, and **unset means nobody** — which is what keeps a single-writer
#: project on exactly the path it was always on. Same construction as
#: `.dgraph-range.json`: absent, and nothing here fires.
#:
#: Spelled in `dgraph/env.py` with the rest of the family and imported back
#: under the name every call site here already uses, so that the binary which
#: composes an agent's environment and the one which obeys it read the same
#: table. What stays here is what the identity *means*.
AGENT_ENV = env.AGENT_ENV

#: An explicit identity for this thread, overriding the environment. Thread-
#: local because `dg serve` is a `ThreadingHTTPServer` and one request must not
#: set the identity of another; and needed at all because a *process*-wide
#: setting cannot express what the browser needs — see `as_owner`.
_as = threading.local()
_INHERIT = object()


def owner() -> str | None:
    """Who is staging, or `None` for nobody.

    `None` is not a lesser identity, it is the supervisor: a person, at whichever
    door. `dg apply` from an unowned caller is the one that may take a whole
    tray, and the one that is refused when somebody else's work is in it.
    """
    named = getattr(_as, "name", _INHERIT)
    if named is not _INHERIT:
        return named
    return (os.environ.get(AGENT_ENV) or "").strip() or None


def _identity_source() -> str:
    """Which door handed this thread its identity, for a refusal to name.

    An agent is told to change `$DG_AGENT` and a caller driving the API is told
    to change its header, because telling either one the other's answer is a
    refusal it cannot act on. `_as` is set only by `as_owner`, which only the
    server uses — so the branch is exactly "was this a request or a process".
    """
    return ("the `" + server_header() + "` header"
            if getattr(_as, "name", _INHERIT) is not _INHERIT
            else "$" + AGENT_ENV)


def server_header() -> str:
    """The header `dgraph/server.py` reads an identity from.

    Named here rather than imported from there: `pending` is the lower module
    and must not import the server, and a refusal that guessed the header name
    would be one more string free to drift from the one that is read.
    """
    return "X-DG-Agent"


@contextlib.contextmanager
def as_owner(name: str | None):
    """Stage as `name` for the duration of a block, whatever the environment says.

    Exists for one case, and it is not a convenience. `dg serve --detach` is a
    `subprocess.Popen` and inherits its environment, so an agent that set
    `$DG_AGENT` and launched the server would hand its own identity to every
    person who later clicked in that browser — and that person's terminal
    `dg apply`, being unowned, would then refuse to apply what they had just
    staged. The browser is a person's door, so it stages as nobody unless a
    request says otherwise. See `server.Handler`.
    """
    prev = getattr(_as, "name", _INHERIT)
    _as.name = name
    try:
        yield
    finally:
        if prev is _INHERIT:
            del _as.name
        else:
            _as.name = prev


#: Whether this call may file under an area nobody has used yet. Thread-local
#: for the reason `_as` above is: `dg serve` is a `ThreadingHTTPServer`, and two
#: requests are two threads that must not see each other's permission.
_fresh_area = threading.local()


@contextlib.contextmanager
def new_area_allowed(allowed: bool = True):
    """Let this call file under a genuinely new area, for the duration of a block.

    **A carrier, not a field on the op**, and that is the whole design. `apply`
    never rechecks the area, so a `new_area` written into the tray would be a
    *permission travelling with a record* — staged by one writer, applied by
    another, in a tray this tool has always treated as shared. `refuse_area`
    settled that when the flag was born; this is what lets the check move to the
    one staging door without taking it back.

    Scoped to the call rather than passed down it because the alternative is a
    `new_area=False` parameter on every function between a command and
    `stage_all` — `expand`, the editor's composers, `integrate.adopt`'s injected
    `stage` — which is the list-of-doors problem the move is meant to end. A
    door that says nothing gets the guard, which is the safe default; a door
    that means it says so here.

    `as_owner`'s twin in every respect, including that only the doors offering
    `--new-area` (and `dg areas rename`, where the person typed the target) use
    it. Audit `R-F2`.
    """
    prev = getattr(_fresh_area, "ok", False)
    _fresh_area.ok = allowed
    try:
        yield
    finally:
        _fresh_area.ok = prev


def new_area_ok() -> bool:
    """Whether the caller has said this area is deliberate. False by default,
    which is what makes a door that has never heard of this a guarded one."""
    return getattr(_fresh_area, "ok", False)


def mine(ops: list[dict], me=_INHERIT) -> tuple[list[dict], list[dict]]:
    """`ops` split into the caller's and everybody else's.

    An unowned caller owns the unowned ops, which is what makes a single-writer
    tray whole: nothing in it carries `by`, so `theirs` is empty and every
    caller in that project takes the lot.
    """
    me = owner() if me is _INHERIT else me
    return ([o for o in ops if o.get("by") == me],
            [o for o in ops if o.get("by") != me])


#: What an op nobody owns is *called*, wherever a reader has to be able to name
#: one. `_scope`'s refusal already printed this word, and a filter that would
#: not take back the name it prints is a dead end for whoever just read it — so
#: the two share a constant rather than repeating a string.
#:
#: **Therefore reserved**, and refused at the staging door by `_with_refs`. A
#: writer actually called `unowned` made the reading and the write disagree:
#: `named` falls back to this word for an unsigned op while `addressed` maps it
#: to `None`, so `dg pending --agent unowned` listed the named writer's ops
#: *and* the unsigned ones while `dg apply --agent unowned` wrote only the
#: unsigned ones. A reader reviewed two and accepted one, with nothing saying
#: so — which is the failure the roster exists to prevent, one layer down.
#:
#: In `env.py` too, because `dg-agent env` has to report `$DG_AGENT=unowned` as
#: the bad *configuration* it is, and a second copy of the word is a second
#: thing free to drift.
UNOWNED = env.UNOWNED


def named(op: dict) -> str:
    """One op's owner as a name, `UNOWNED` where nobody is named."""
    return op.get("by") or UNOWNED


def addressed(name: str) -> str | None:
    """The `by` value a roster name stands for.

    `UNOWNED` is a label and never a stamp — an unowned op has no `by` key at
    all, which `test_no_identity_leaves_the_op_exactly_as_it_was` pins — so a
    filter that handed the word straight to `mine` would match nothing and then
    report an empty selection as a legitimate one.
    """
    return None if name == UNOWNED else name


def roster(*trays: list[dict] | None) -> dict[str, int]:
    """Who has work staged across these trays, and how many ops each of them has.

    **Spans the trays rather than reading one.** They are staged independently
    and reviewed as a pair, so an agent with no decision ops and five task ops
    is present, and a per-tray roster is the reading that calls it absent.
    `_scope` already unions them to name the owners it refused over; this is
    that union offered *before* the refusal instead of inside it.

    A tray that could not be read is `None` and contributes nothing, for the
    same reason it does everywhere else: a roster is a reading, and an
    unparseable task tray must not take the decision one down with it.

    Ordered, named owners first and `UNOWNED` last — it is the one entry that
    is not a name, and sorting it in among them reads as an agent called
    "unowned".
    """
    counts: dict[str, int] = {}
    for ops in trays:
        for op in ops or []:
            who = named(op)
            counts[who] = counts.get(who, 0) + 1
    return {k: counts[k]
            for k in sorted(counts, key=lambda n: (n == UNOWNED, n))}


def refuse_apply_for(name: str, *trays: list[dict] | None) -> str | None:
    """Why this caller may not write what `name` staged — `None` if it may.

    One rule, both doors. The terminal renders it with the flags beside it and
    the browser returns it as a 400; what neither of them owns is the judgement,
    because a rule about who may write somebody else's draft that held at one
    door and not the other is the drift every shared helper in this file exists
    to stop.

    Two refusals, about different things.

    **Authority.** Applying for a named writer is `--all`'s sibling, not
    `--mine`'s: it writes somebody else's half-composed batch, which is
    `C-F16` — a draft `close` applied by another writer is a DECIDED answer
    whose only exit is a `reopen`, filing a reversal nobody made. An *unowned*
    caller is the supervisor and is usually right to do it; one agent reaching
    into another's drafts is the failure the ownership stamp exists to stop,
    and naming the victim does not make it a different act. Naming **yourself**
    is allowed, since refusing it would be refusing `--mine` spelled out.

    **The name.** A roster name nobody staged under is almost always a typo,
    and the damage is that it is *silent*: `mine` matches nothing, the scope
    comes back empty, and an empty selection reports as a legitimate one —
    "nothing staged by agnet-b", which is true and useless. So an unknown name
    is refused **with the roster**, which is the list the caller meant to type
    from.
    """
    me = owner()
    if me is not None and addressed(name) != me:
        whose = "the unowned drafts" if name == UNOWNED else f"{name}'s drafts"
        return f"you are staging as {me}, and {whose} are not yours to write"
    known = roster(*trays)
    if name not in known:
        listed = " · ".join(f"{n} {c}" for n, c in known.items()) or "nobody"
        return f"nobody called {name} has work staged — staged by  {listed}"
    return None


def refuse_apply(staged: int = 0, chosen: str | None = None) -> str | None:
    """Why this caller may not write the store at all — `None` if it may.

    `$DG_APPLY=never`, and the reading it needs is the same one every other
    remit rule takes: **a caller with no `$DG_AGENT` is never refused**, because
    that caller *is* the supervisor this policy is holding the ops for.

    Beside `refuse_apply_for` rather than inside it, because the two refuse
    different things and a caller can meet one and not the other. That one is
    about *authority* — whose half-composed batch this is. This is about
    *permission* — whether an agent writes the store at all, or only ever
    proposes into it. Under `never` an agent is refused its own ops, which is
    exactly the case `refuse_apply_for` is written to allow.

    One rule, both doors: the CLI renders it with the flags beside it and the
    browser returns it as a 400, for the reason given on `refuse_apply_for`.
    """
    me = owner()
    if me is None:
        return None
    if env.apply_policy(chosen) != "never":
        return None
    held = f"{staged} op(s) stay staged" if staged else "nothing was staged"
    return (f"${env.APPLY_ENV}=never: {me} may not write the store — {held} "
            f"for a caller with no ${AGENT_ENV} to apply")


def _new_ref(taken: set[str]) -> str:
    """An id no op in this tray is using.

    Uniqueness is enforced rather than hoped for, because it is cheap here: the
    caller holds the lock and has the whole tray in hand. What is left is the
    small residual the docstring on `resolve` names — an id can be reused once
    the op carrying it is gone.
    """
    import secrets
    while True:
        ref = "".join(secrets.choice(REF_ALPHABET) for _ in range(REF_LEN))
        if ref not in taken:
            return ref


def _with_refs(ops: list[dict], current: list[dict]) -> list[dict]:
    """`ops` with an id each, unique against `current` and against each other.

    Never mutates: an op is copied before it gains one, so a caller that kept a
    reference to what it staged is not surprised by a key appearing in it.
    """
    taken = {o["ref"] for o in current if o.get("ref")}
    who = owner()
    if who == UNOWNED:
        # Refused **here**, not in `owner()`: reading a graph under a bad
        # identity harms nothing, and a `dg show` that failed because of an
        # environment variable would be a refusal aimed at the wrong act. This
        # is the one place the name is written down, so it is the one place it
        # has to be a name.
        raise ApplyError(
            f"`{UNOWNED}` is reserved — it is how this tool names ops nobody "
            f"signed, so a writer cannot also be called that. Set "
            f"{_identity_source()} to something else.")
    # **One call, one group** — and a group only where there is one to have.
    #
    # `stage_all` is the act boundary and its docstring already says so: it
    # exists because a reopen and the statuses it propagates, or an
    # `add_vertex` and the edges that attach it, "only make sense together".
    # That made *staging* atomic. Nothing carried the fact forward, so
    # `dg drop-op` could take one member out and `dg apply` write the rest —
    # leaving, in the docstring's own example, a task that "reads as startable
    # to everything that asks". Audit `G-F11`.
    #
    # Stamped only for a real group, and that is not an optimisation. A single
    # op is a group of one whether it says so or not, and an **absent** group
    # already has to read as one — for the trays staged before this existed.
    # Giving a lone op a ref would create a distinction nothing acts on while
    # changing the shape of every tray in the tool.
    group = _new_ref(taken) if len(ops) > 1 else None
    if group is not None:
        taken = taken | {group}
    out = []
    for op in ops:
        if not op.get("ref") or op["ref"] in taken:
            op = {**op, "ref": _new_ref(taken)}
        if group is not None and not op.get("group"):
            op = {**op, "group": group}
        # `by` joins `ref` and `saw`: tray bookkeeping, and of `saw`'s family
        # rather than a derived field — a record of who staged this, which
        # nothing can later make untrue. Set **once**, here, and never
        # rewritten: `discard` takes applied ops out of the tray *by value*, so
        # a stamp that moved between staging and applying would make that match
        # nothing and leave an applied op staged. An op that already carries one
        # keeps it, which is what makes `dg edit` and `replace_group` safe.
        #
        # Absent, not null, where nobody is named. Every existing project's tray
        # then stays byte-identical, and the whole ownership path is unreachable
        # in a project that never sets an identity.
        if who is not None and not op.get("by"):
            op = {**op, "by": who}
        taken.add(op["ref"])
        out.append(op)
    return out


def resolve(ops: list[dict], ref: str | int) -> int:
    """Where `ref` sits in `ops` — by op id, else by index.

    **Call it under the lock, on the tray you are about to write.** That is the
    whole point: resolving an id against the current tray lands on the op the
    id names however much the positions have moved, where an index resolved
    against a tray read earlier lands on whatever has drifted into that slot.

    Ids and indices cannot be confused (see `REF_ALPHABET`), so accepting both
    costs nothing. Raises `LookupError` for an id nothing carries and
    `IndexError` — a subclass, so one `except LookupError` covers both — for a
    position the tray does not have.

    Known residual: an id belongs to an op, not to history, so once an op is
    applied or dropped its id can be handed to a later one. A reader quoting a
    stale id is then wrong again, with probability one in `len(REF_ALPHABET) **
    REF_LEN` per op staged since. That is a different order of risk from an
    index, which after a single concurrent apply is wrong essentially always.
    """
    text = str(ref).strip()
    for i, op in enumerate(ops):
        if op.get("ref") == text:
            return i
    try:
        i = int(text)
    except ValueError:
        raise LookupError(
            f"no staged op {text!r} — `dg pending` lists the id of each"
        ) from None
    if not 0 <= i < len(ops):
        raise IndexError(f"no staged op {i}")
    return i


def stage(op: dict, path: Path | None = None, *,
          against: Graph | None = None) -> list[dict]:
    """Stage one op. `against` is the effective graph it was composed against,
    and stamps it — see `stamp`. Omitted for a task op, which has no premise
    this module may resolve."""
    return stage_all([op], path, against=against)


def _refuse_new_areas(ops: list[dict], tray: list[dict],
                      path: Path | None) -> None:
    """Raise if any op files under an area this project has not used.

    The registries are read the way `vet_new` reads them and then widened by
    the tray, which is what makes a second record under an area staged a minute
    ago silent: `own` is the store this tray feeds plus every area already
    staged into it, `other` is the twin store. `stored_counts` caches on the
    file's mtime and size, so the ordinary path — no `area` on any op — costs
    one `.get` per op and no read at all.

    Sequential, accumulating, exactly as `vet_all` vets against the graph the
    ops before it produce: a group filing two records under one new area is one
    new area, and the refusal names it once.

    Which store is which comes from the tray path, since that is the only thing
    `stage_all` is told — against `project.TASK_PENDING_NAME` rather than
    `task_pending.path()`, because `task_pending` imports this module and the
    name is the constant both of them already resolve through.
    """
    if not any((op.get("area") or "").strip() for op in ops):
        return
    proj = project.find()
    p = path or proj.pending
    is_task = p.name == project.TASK_PENDING_NAME
    own_store, other_store = ((proj.tasks, proj.store) if is_task
                              else (proj.store, proj.tasks))
    own = dict(_areas.stored_counts(own_store))
    for staged in tray:
        area = (staged.get("area") or "").strip()
        if area:
            own[area] = own.get(area, 0) + 1
    other = _areas.stored_counts(other_store)
    allowed, me = new_area_ok(), owner()
    for op in ops:
        area = (op.get("area") or "").strip()
        if not area:
            continue
        why = refuse_area(area, own=own, other=other, owner=me,
                          new_area=allowed)
        if why is not None:
            raise ApplyError(why)
        own[area] = own.get(area, 0) + 1


def stage_all(ops: list[dict], path: Path | None = None, *,
              against: Graph | None = None) -> list[dict]:
    """Stage a group of ops as one write. The plural of `stage`, and the one to
    reach for whenever the ops only make sense together.

    Every derived group in this tool — a reopen and the `set_status` ops that
    propagate it, a close and the blocks it releases, an `add_vertex` and the
    edges that attach it — used to be staged in a `for` loop, one `held` and one
    load-modify-save per op. Between two of those iterations the tray on disk
    holds half a group, and anything reading it then sees a batch that means
    something other than what the command staged.

    That was safe only by coincidence: a half-applied propagation leaves a graph
    `validate` calls invalid, so an apply landing mid-group was refused rather
    than written. The coincidence does not cover every group — `dg add --after`
    staged a vertex and then its edges, and a vertex with no edges is only a
    `no_orphans` *warning*, so that one could be applied halfway and pass. One
    lock, one load, one save makes the atomicity a property of the call instead
    of a property of which invariants happen to be blocking.
    """
    if not ops:
        return load(path)
    # `$DG_TERSE`, and here rather than in either store's `vet` for the reason
    # the watermark below is here: this is the one staging function, for both
    # trays and every door, and a launcher's rule that a second door did not
    # consult is not a rule. It is also the *only* guard in this tool that
    # cannot run before composition — it judges the prose, which does not exist
    # until it has been written. The tray is still left untouched, which is
    # what the stage-time guards were actually protecting.
    #
    # A supervisor is never refused, and an unset `$DG_TERSE` is every project
    # that has never heard of this, so the ordinary path adds one dict lookup.
    me = owner()
    for op in ops:
        why = limits.refuse_verbose(op, me)
        if why is not None:
            raise ApplyError(why)
    if against is not None:
        ops = [stamp(against, op) for op in ops]
    with held(path):
        current = load(path)
        # `$DG_AREA` and the similarity guard, here for the reason `$DG_TERSE`
        # above is and `area_known` no longer can be. The invariant used to
        # catch an unknown area in `validate`, which is one place by
        # construction; dropping it for a stage-time check turned that into a
        # list of call sites, and the list was missing the door that takes ops
        # **as data** — `POST /api/pending`, and every op adopted from another
        # writer's contribution. That is the multi-writer fan-out fragmenting
        # one area into two, arriving through the one door built for another
        # writer's work. Audit `R-F2`.
        #
        # Inside the lock and before the extend, so the tray it judges against
        # is the tray it is about to be part of, and a refusal leaves the file
        # untouched — the property every other stage-time guard is for.
        _refuse_new_areas(ops, current, path)
        # Under the lock, so uniqueness is judged against the tray as it is —
        # another writer may have staged since this call started.
        current.extend(_with_refs(ops, current))
        save(current, path)
        # The watermark, raised where an id is *committed to*. Here rather than
        # in `next_id` because all four of that function's callers only offer
        # an id — one of them answers `/api/graph` on every page load, so
        # bumping there would burn an id per refresh. And here rather than in
        # each command because this is the one staging function, for both trays
        # and every door.
        #
        # The lock held here is **not** what protects it, and a comment here
        # used to claim it was: the tray's lock is one of two, and the range
        # file is one file that both trays write. `ranges.issue` takes its own.
        # Audit W-F3.
        #
        # The root from the tray's own directory when one was passed, not
        # from `project.find()`: a caller that named a tray outside the current
        # project would otherwise raise this clone's watermark for somebody
        # else's grant, which is precisely the write the file exists to stop.
        ranges.note_ops(ops, path.parent if path is not None else None)
        return current


def group_of(ops: list[dict], op: dict) -> list[dict]:
    """Every op staged in the same act as `op`, `op` included.

    `[op]` where it carries no group, which is both a single-op command and
    every tray staged before groups existed — see `_with_refs` for why those
    two read the same.
    """
    gid = op.get("group")
    return [o for o in ops if o.get("group") == gid] if gid else [op]


def refuse_split(ops: list[dict], op: dict, *, kind: str = "op") -> str | None:
    """Why this op may not be removed on its own — or `None`.

    The other half of `stage_all`'s atomicity. Staging a group as one write
    stops a *reader* seeing half of one; nothing stopped a **writer** taking a
    member out and applying the rest, which is how `dg task add --after T01`
    came to land the task without its edge, reading as startable to everything
    that asks. Audit `G-F11`.

    A refusal rather than a silent cascade, because the group is somebody's
    judgement and the two ways out mean different things: dropping the whole
    act turns their proposal down, and keeping it whole leaves it to be applied
    or cleared as one. Choosing between those is not this function's to do.
    """
    rest = [o for o in group_of(ops, op) if o.get("ref") != op.get("ref")]
    if not rest:
        return None
    shown = ", ".join(f"{o.get('ref')} {o.get('op')}" for o in rest)
    return (f"{op.get('ref')} was staged together with {len(rest)} other "
            f"{kind}(s), as one act: {shown}\n"
            f"Removing it alone would leave the rest to be applied as though "
            f"they meant something they do not. Drop the whole act, or leave "
            f"it whole and turn it down with `clear`.")


def refuse_partial(batch: list[dict], tray: list[dict], *,
                   kind: str = "op") -> str | None:
    """Why this batch may not be written -- or `None`: it holds part of an act
    whose other members would stay staged.

    `refuse_split` guards the door that *removes* from a tray; this guards the
    one that *writes from* it, and it sits on `applying` rather than on any
    caller because the callers select ops by different predicates -- a writer,
    a ref, what `limits.mechanical` admits -- and any predicate other than the
    act can cut one. The broker's mechanical apply did, one commit after `G11`
    closed this for `drop-op`: `add_task` qualified, its `add_dep` did not, and
    the task landed without its edge, startable to everything that asks. Audit
    `X-F1`.

    Judged against the tray as it stands under the lock, like `refuse_split`.
    A member whose siblings have already left is a group of one -- they were
    applied or dropped by a route that judged the act -- and is not refused for
    company it has not got.
    """
    refs = {o.get("ref") for o in batch}
    for op in batch:
        gid = op.get("group")
        if not gid:
            continue
        rest = [o for o in tray
                if o.get("group") == gid and o.get("ref") not in refs]
        if rest:
            shown = ", ".join(f"{o.get('ref')} {o.get('op')}" for o in rest)
            return (f"{op.get('ref')} {op.get('op')} was staged as one act with "
                    f"{len(rest)} other {kind}(s) that this batch leaves "
                    f"behind: {shown}\n"
                    f"Writing part of an act would land the rest as though "
                    f"they meant something they do not. Apply the act whole -- "
                    f"`dg apply --group {op.get('ref')}` -- or drop it whole.")
    return None


def drop(ref: str | int, path: Path | None = None) -> dict:
    """Unstage op `i`, and return **the op that was removed**.

    The removed op rather than what is left, because the caller's job is to say
    what went. An index is only meaningful against the tray as it was listed,
    and this tray is shared: `discard` takes applied ops out by value from
    wherever they sit, so another writer's apply renumbers everything after
    them and `i` can name a different op by the time it is used. A caller that
    printed only the number it was given could not tell the two apart — see
    audit F29 — and it cannot re-read the tray to find out, because by then the
    op is gone. So it comes back from under the lock, where it is still known.

    What is left is one `load` away, and both callers that want it already do
    that.
    """
    with held(path):
        ops = load(path)
        i = resolve(ops, ref)
        # Under the lock, against the tray as it is: another writer may have
        # applied or cleared since this call started, and a group judged
        # against a stale reading is the check answering about ops that are no
        # longer there.
        why = refuse_split(ops, ops[i])
        if why is not None:
            raise ApplyError(why)
        gone = ops.pop(i)
        save(ops, path)
        return gone


def drop_group(ref: str | int, path: Path | None = None) -> list[dict]:
    """Unstage the whole act `ref` belongs to, and return what went.

    `drop`'s plural, and the way out its refusal names. A group of one is an
    ordinary drop, so a caller never has to know which it had.
    """
    with held(path):
        ops = load(path)
        going = {o.get("ref") for o in group_of(ops, ops[resolve(ops, ref)])}
        gone = [o for o in ops if o.get("ref") in going]
        save([o for o in ops if o.get("ref") not in going], path)
        return gone


def replace(ref: str | int, op: dict, path: Path | None = None, *,
            against: Graph | None = None) -> list[dict]:
    """Swap one staged op for a revised version, in place. The singular of
    `replace_group`, for a revision that really is one op."""
    return replace_group(ref, [op], path, against=against)


def replace_group(ref: str | int, ops: list[dict], path: Path | None = None, *,
                  against: Graph | None = None,
                  supersede: Callable[[dict], dict | None] | None = None,
                  ) -> list[dict]:
    """Swap staged op `i` for a whole group, and settle what the group replaces.

    `dg edit N` composes a replacement rather than dropping and re-staging,
    because re-staging would move the op to the end of the batch and any derived
    `set_status` ops would then apply before the thing they were derived from.
    The group lands where op `i` sat, for that same reason.

    **A group, not an op**, because for an `add_vertex` that is what comes back:
    `editor._parse_add` returns the vertex plus one `add_edge` per parent named
    in the buffer. Writing back only the first of those discarded everything the
    edit said about structure, silently, while reporting success — audit F26.

    `supersede(op)` is applied to every *other* staged op and returns it as it
    should remain, or `None` to drop it: an edit that re-states a vertex's
    parents has to take out the edges the old version staged, or the tray ends
    up holding both readings. It is a callable rather than a list of indices
    because it is evaluated **under the lock**, over the tray as it then is —
    indices computed by a caller from a tray it read earlier are exactly what
    audit F29 is about, and re-introducing that here would be the same defect
    one command along.
    """
    if against is not None:
        ops = [stamp(against, op) for op in ops]
    with held(path):
        current = load(path)
        i = resolve(current, ref)
        ops = _with_refs(ops, current)
        out: list[dict] = []
        for j, op in enumerate(current):
            if j == i:
                out.extend(ops)
                continue
            kept = supersede(op) if supersede is not None else op
            if kept is not None:
                out.append(kept)
        save(out, path)
        return out


def clear(path: Path | None = None) -> None:
    """Discard the whole tray. What `dg clear` means, and nothing else uses it.

    Applying does **not** come through here — see `discard`. Clearing after an
    apply throws away whatever was staged while the apply was running, which
    for a threaded server and a terminal sharing one project is a real window
    and a silent loss.
    """
    with held(path):
        save([], path)


def clear_agent(name: str, path: Path | None = None) -> int:
    """Discard one writer's ops, leave everybody else's, and say how many went.

    The reject verb for a shared tray. `clear` above takes the whole file
    whoever runs it, which is right for the single writer it was written for
    and is a blunt instrument once several agents stage into one tray — reading
    one proposal and turning it down should not cost the other three.

    Re-read **inside** the lock, not filtered from a list loaded outside it: the
    window `clear` names is the same window here, and an op staged while this
    ran belongs to whoever staged it.
    """
    me = addressed(name)
    with held(path):
        gone, keep = mine(load(path), me)
        save(keep, path)
        return len(gone)


def discard(applied: list[dict], path: Path | None = None) -> list[dict]:
    """Remove the ops that were just applied, and leave everything else.

    The tray is re-read under the lock rather than assumed: `apply` reads it,
    validates a copy, renders, and writes the store, and anything staged during
    that window belongs to the *next* batch. Clearing the file would drop it
    with no error and no trace in any diff.

    Removal is one occurrence per applied op, by value, wherever it sits — not
    a prefix strip. That tolerates a concurrent `dg drop` (the op is already
    gone; nothing to do) and keeps a second identical op staged rather than
    guessing which of the two was the one applied. Erring toward leaving an op
    staged is the safe direction: a re-applied `close` is refused loudly by
    `_apply_one`, while a dropped one is silent.
    """
    with held(path):
        remaining = load(path)
        for op in applied:
            try:
                remaining.remove(op)
            except ValueError:
                pass        # dropped or edited meanwhile; not ours to restore
        save(remaining, path)
        return remaining


def preview(g: Graph, path: Path | None = None, *, skip: int | None = None) -> Graph:
    """The graph as it will stand once the staged ops apply.

    What every stage-time guard consults. A guard that reads the store alone
    judges a graph the user has already moved past: it refuses the chain they
    just staged (`add --after` a staged vertex), accepts a duplicate of it
    (`dg decide` twice), demands a reopen that is already staged, and lets
    `expand` miss a descendant whose decision is staged but not applied.
    `skip` leaves one op out — `dg edit N` revises op N against the graph as
    it stands *without* that op.

    Deliberately not validated: mid-batch the combined state may be
    legitimately transitional (a child closed before its premise, with the
    premise's close still to come). Raises ApplyError only if a staged op
    cannot apply at all — the staging area itself needs attention before
    anything more goes into it.
    """
    out = copy.deepcopy(g)
    for i, op in enumerate(load(path)):
        if i == skip:
            continue
        try:
            _apply_one(out, op)
        except KeyError as exc:
            raise ApplyError(
                f"staged op {i} is missing required field {exc.args[0]!r}"
            ) from None
    return out


def retargets(g: Graph, op: dict) -> list[tuple[str, list[str], list[str]]]:
    """Decided vertices whose `opens` this op changes: `(id, before, after)`.

    **Temporary, and it should say so where it prints.** `D57` settled that a
    decided edge's targets belong to the graph rather than to the answer, which
    retired two refusals that had stood since the model was written. That is a
    change of *meaning*, and its falsifier — somebody relying on `to` as a claim
    about what an answer raised — fires quietly: an audit is simply wrong, with
    nothing to error on. So the edits that would have been refused now say what
    they are about to reinterpret, and somebody who answers *but that answer
    raised it* has just fired the falsifier out loud.

    It comes off once it has had a run. A notice kept forever on an operation
    `D57` calls ordinary is the noise `opencode/dear-guide.ts` argues against,
    where *a notice on each would train the reader to ignore the one that
    matters* — and this one is deliberately loud, which is exactly what makes it
    unaffordable as furniture.

    Only the **write** half of the problem. A reader who takes `opens` for a
    claim about the answer gets nothing from here; what reaches them is where
    `dg node` puts the line, which `T38` moved out of the answer block for the
    same reason.
    """
    kind = op.get("op")
    vid = op.get("vertex") or op.get("from")

    def row(src: str, after: list[str]):
        e = g.active_edge(src)
        if e is None or not e.decided or sorted(e.to) == sorted(after):
            return None
        return (src, list(e.to), sorted(after))

    out = []
    if kind == "remove_edge":
        gone = set(op.get("to", ()))
        e = g.active_edge(vid)
        if e is not None:
            out.append(row(vid, [t for t in e.to if t not in gone]))
    elif kind == "remove_vertex":
        for e in g.edges:
            if e.active and vid in e.to:
                out.append(row(e.src, [t for t in e.to if t != vid]))
        mode = op.get("mode", "sever")
        if mode in ("splice", "into"):
            dsts = (g.children(vid) if mode == "splice"
                    else [op.get("into")])
            for parent in g.depends(vid):
                e = g.active_edge(parent)
                if e is not None:
                    gained = [d for d in dsts if d and d not in e.to]
                    if gained:
                        out.append(row(parent, sorted(set(e.to) | set(gained))))
    elif kind == "add_edge":
        e = g.active_edge(vid)
        if e is not None:
            out.append(row(vid, sorted(set(e.to) | set(op.get("to", ())))))
    return [r for r in out if r is not None]


def retargets_all(g: Graph, ops: list[dict]) -> list[tuple[str, list[str], list[str]]]:
    """`retargets` over a batch, walked against the graph the ops build.

    Walked incrementally for the reason `moved` gives about its own walk: an op
    is composed against the store plus everything before it, so judging each one
    against the bare store would report a batch's own earlier edits as
    somebody's surprise. Collapsed per vertex, keeping the first `before` and
    the last `after`, so one command that touches one answer twice says so once.
    """
    probe = copy.deepcopy(g)
    seen: dict[str, tuple[str, list[str], list[str]]] = {}
    for op in ops:
        for vid, before, after in retargets(probe, op):
            first = seen[vid][1] if vid in seen else before
            seen[vid] = (vid, first, after)
        try:
            _apply_one(probe, op)
        except ApplyError:
            break
    return [v for v in seen.values() if sorted(v[1]) != sorted(v[2])]


def vet(g: Graph, op: dict, *, new_area: bool = False) -> None:
    """Raise ApplyError if `op` could not be staged against `g`.

    The stage-time guard for callers that receive ops as data — the web API,
    chiefly. The CLI's commands each run their own richer checks first; this is
    the shared floor: the op must apply to the effective graph, every target it
    names must exist, and any status it writes must be legal. Validation-level
    rules (propagation, falsifiers) stay with `apply`, where a transitional
    mid-batch state is allowed.
    """
    probe = copy.deepcopy(g)
    try:
        _apply_one(probe, op)
    except KeyError as exc:
        raise ApplyError(f"op is missing required field {exc.args[0]!r}") from None
    unknown = [t for t in (op.get("to") or []) if t not in g.vertices]
    if unknown:
        raise ApplyError(f"unknown target(s): {', '.join(unknown)}")
    # A grant is a rule here or it is a discipline. `next_id` only prefills a
    # form and `--id` is an option, so `dg add --id D58` walks straight past a
    # range that lives only in the prompt — which is the thing a grant exists
    # to replace. Silent in every project with no grant.
    if op.get("op") == "add_vertex":
        bad = ranges.fault("D", str(op.get("id") or ""))
        if bad:
            raise ApplyError(bad)
    status = op.get("status")
    if status is not None:
        fault = status_fault(status, g.vertices,
                             of=op.get("vertex") or op.get("id"))
        if fault:
            raise ApplyError(fault)
    # `_apply_one` above copied the probe onto the edge without reading it;
    # `apply_all` would refuse the batch later, naming `probe_wellformed`
    # against an op somebody else may by then have staged beside. Refused
    # here, at the door, the way a status is.
    if (op.get("op") in ("close", "reject", "add_vertex", "reprobe")
            and op.get("probe") is not None):
        fault = probe_fault(op["probe"])
        if fault:
            raise ApplyError(f"probe: {fault}")
    if op.get("op") in ("bind", "unbind"):
        for b in op.get("binds") or ():
            fault = bind_fault(b)
            if fault:
                raise ApplyError(f"bind: {fault}")
    # `set_status` is a **derived** op for decisions: `expand` and `repairs`
    # produce it, and both stamp `derived_from`. The single exception is
    # re-affirming a PROVISIONAL one, which `compose_confirm` composes and
    # guards. So an unstamped `set_status` arriving as data is either that —
    # in which case it must satisfy those guards *now*, at the moment the box
    # was ticked — or it is a hand-written status change, which no door of this
    # tool offers and which `apply` would refuse a batch later, naming a
    # command instead of the act. Audit F2 in the interface pass: the raw op
    # went into a shared tray and came back as somebody else's refusal.
    if op.get("op") == "set_fields":
        # `_apply_one` above proved it resolves, and by the same two keys.
        v = g.vertices[op.get("vertex") or op.get("from")]
        vet_fields(op, own=area_counts(g.areas, g.vertices.values()),
                   other=stored_area_counts(project.find().tasks),
                   record="decision", new_area=new_area,
                   current={k: getattr(v, k) for k in FIELDS})
    if op.get("op") == "set_status" and "derived_from" not in op:
        if status != "DECIDED":
            raise ApplyError(
                f"a status is not set directly — {status} is derived from a "
                f"reopen or from settling a premise, and the only status a "
                f"caller may write is DECIDED, to re-affirm a PROVISIONAL "
                f"decision")
        compose_confirm(g, vid=op.get("vertex"))


def _register(store, area: str | None) -> None:
    """Note an area on the store the op is writing. Append-only, like the model.

    **Not a new op kind.** `OPS` is unchanged, because registering an area is a
    side effect of filing a record under one rather than an act of its own --
    and an `add_area` op would be an act somebody could stage without ever
    filing anything, which is the declared-vocabulary problem this replaced.

    Only the store being written. The union is what every *reader* consults,
    but a composite op spanning both trays could land in one and fail in the
    other, leaving the two lists divergent because of the feature meant to
    unify them -- and `dg apply`'s independence of the two batches predates
    this and is load-bearing.
    """
    if area and area not in store.areas:
        store.areas.append(area)


def _and_(names: list[str]) -> str:
    """`a`, `a and b`, `a, b and c` — for a refusal naming several fields."""
    return (names[0] if len(names) == 1
            else " and ".join([", ".join(names[:-1]), names[-1]]))


def vet_fields(op: dict, *, own: dict, other: dict, current: dict,
               record: str, new_area: bool = False) -> None:
    """The stage-time floor for a `set_fields` op, shared by both stores.

    Here rather than in each store's `vet` for the reason `already` and
    `matches` are shared: the four fields are the same four, and a rule applied
    in one store and not its twin is the shape most of this tool's audit
    findings took.

    Refused at *stage* time, and there is no longer anything behind it: this
    used to argue itself as the earlier of two nets, with `area_known` catching
    an unknown area at apply, and that invariant is gone — the list is a
    registry now, so a record filed under an unlisted area is legal. Two
    reasons it is here rather than later. A tray may be shared, so
    an op that cannot apply wedges it for every other writer until somebody
    drops it — audit `F30`, and the argument for `vet_all` existing at all. And
    there is no invariant at all for a blank title: nothing in either store's
    `validate` reads one, so an op that empties a title would land, and the
    record would then be referred to by nothing.

    `current` is what the record holds now, so that an op writing the values
    already there is refused rather than staged. The caller is often an agent
    that has lost track, which is the same reading the no-op status guard makes.

    `own` and `other` are the two stores' area registries -- see `refuse_area`,
    which is the one place either store's areas are judged.
    """
    # Before "nothing to change", so that an op naming only a field this one
    # cannot write is told *that* rather than told it named nothing — the
    # caller reached for the field it meant and needs to hear why it is not
    # here, not that its op was empty.
    extra = sorted(set(op) - set(FIELDS) - set(FIELD_KEYS))
    if extra:
        raise ApplyError(
            f"a {record} is not amended in {_and_(extra)} — this op writes "
            f"{', '.join(FIELDS)} and nothing else. An answer, an outcome and "
            f"a reason work stopped are dated records: they are superseded by "
            f"a new one, never edited")
    named = [k for k in FIELDS if k in op]
    if not named:
        raise ApplyError(
            f"nothing to change — name at least one of {', '.join(FIELDS)}")
    if "title" in op and not (op["title"] or "").strip():
        raise ApplyError(
            f"a {record} needs a title — it is what every other record, and "
            f"every reader, refers to it by")
    if "area" in op:
        why = refuse_area(op["area"], own=own, other=other, owner=owner(),
                          new_area=new_area)
        if why is not None:
            raise ApplyError(why)
    if all(op[k] == current.get(k) for k in named):
        one = len(named) == 1
        raise ApplyError(
            f"{op.get('vertex') or op.get('task')} already has "
            f"{'that ' + named[0] if one else 'those values'}")


def vet_all(g: Graph, ops: list[dict], *,
            new_area: bool = False) -> None:
    """Raise if these ops could not be staged **as a group**.

    The plural of `vet`, and the twin of `task_pending.vet_all`. Each op is
    vetted against the graph the ones before it produce, because a group
    routinely builds on itself: `editor._parse_add` returns a vertex and then
    the edges attaching it, and vetting an edge against a graph without the
    vertex would refuse a group the first op makes legal.

    It exists because the editor was the one staging route with no stage-time
    guard at all: `dg add --edit` composed a status no other route accepts and
    staged an op that could never apply, wedging a *shared* tray for every
    writer until somebody dropped it. Audit F30.
    """
    probe = copy.deepcopy(g)
    for op in ops:
        vet(probe, op, new_area=new_area)
        _apply_one(probe, op)


# ---- what moved underneath a staged batch --------------------------------
#
# The store is re-read under the lock at apply time, so a batch is always judged
# against the graph as it now is rather than as it was when it was composed —
# and a batch that would build a decided answer on a premise somebody has since
# reopened is refused by `propagation`, loudly, naming the premise. That covers
# the dangerous case.
#
# It does not cover the *quiet* one. Structure that is still legal lands without
# comment: an OPEN question under a reopened premise is ordinary, and so is work
# whose justification is now under review (`link_premise_under_review` is a
# warning on purpose — decisions must never be held hostage by tasks). In both
# the ground moved under the person who staged it and nothing said so until the
# next `dg check`.
#
# So each op records what the premises it names looked like when it was staged,
# and apply compares. Not a store-level generation number, which says only
# *that* something moved and has to be carried from read-time to apply-time by a
# caller who will forget; and not a wall-clock stamp, which two clones and one
# skewed clock make meaningless. This says which of *this* work's premises
# moved, needs nothing threaded through, and is silent otherwise.
#
# It lives in the tray, which is gitignored scratch — so the source of truth
# gains no bookkeeping for it, and `decisions.json` stays a file that holds
# decisions and nothing else.
#
# Decisions only. A task op's premise is a *decision*, which `dgraph/tasks.py`
# may not resolve and this module cannot see; that drift is already reported by
# `cross.under_review`, in `dg check` and in every session's brief, and a second
# implementation of it here is the duplication the barrier exists to prevent.


def fingerprint(g: Graph, vid: str) -> str | None:
    """What a premise looked like: its status, and a digest of its answer.

    The digest rather than the answer itself, because a tray is read by a person
    running `dg pending` and a wall of prose in every op would make it
    unreadable. Status is kept legible so the report can say `DECIDED →
    REOPENED` without a second lookup.
    """
    import hashlib
    v = g.vertices.get(vid)
    if v is None:
        return None
    e = g.active_edge(vid)
    body = e.answer if e is not None and e.decided else None
    digest = (hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
              if body is not None else "-")
    return f"{v.status}|{digest}"


def premises(g: Graph, op: dict) -> list[str]:
    """The vertices whose state this op leans on.

    Not every vertex it mentions: `add_vertex` invents one and leans on nothing,
    and `set_status` is derived from an op that is in the same batch and stamped
    already. What is left is the structure the work attaches to.

    A `close` leans on the vertex it settles *and* on that vertex's premises —
    the second being the case this exists for: an answer composed under
    `D01 DECIDED` and applied after somebody reopened it.

    **`set_fields` is deliberately not here**, and the reason is `fingerprint`
    rather than the op. A retitle leans on the wording it is replacing, and a
    fingerprint records a status and a digest of an answer — so listing the
    vertex would make `drift` report a status change under an op that does not
    care about the status, and go on missing the one thing that op does care
    about. Widening the fingerprint to cover wording would change what every
    stamp already in a tray means. Two writers giving one record different
    titles is a real case; it is the seam's, not this function's.
    """
    kind = op.get("op")
    if kind == "close":
        vid = op.get("vertex")
        return sorted({vid, *g.depends(vid)}) if vid in g.vertices else []
    if kind in ("reopen", "remove_vertex"):
        return [op["vertex"]] if op.get("vertex") in g.vertices else []
    if kind in ("add_edge", "remove_edge"):
        return [op["from"]] if op.get("from") in g.vertices else []
    return []


def stamp(g: Graph, op: dict) -> dict:
    """`op` with a record of what its premises looked like. Never mutates."""
    seen = {v: fingerprint(g, v) for v in premises(g, op)}
    return {**op, "saw": seen} if seen else op


def drift(g: Graph, ops: list[dict]) -> list[dict]:
    """What **another writer** moved between staging this batch and applying it.

    `g` is the store, which apply has re-read under the lock. An id that is not
    in it is skipped rather than reported: ops are stamped against the
    *effective* graph, so `saw` may name a vertex this very batch is about to
    create.

    **Walked incrementally, not against the bare store**, and that is the whole
    subtlety. `stamp` records what op `i` was composed against, which is the
    store *plus everything already in the tray* — so the honest baseline for op
    `i` is the store plus `ops[:i]`, not the store alone. Comparing against the
    store alone reports a batch's own earlier ops as a stranger's work: `dg
    reopen D01` followed by `dg add --after D01`, one writer, one process, no
    contention, printed `D01 moved since this batch was staged (REOPENED →
    DECIDED)` — the batch's own reopen, with the direction backwards.

    That was not cosmetic. This report is the only thing that says a premise
    moved under an answer already composed, `demo-agentic/` rests a scene on
    somebody reading one, and a line that also fires on the commonest
    single-writer batch in the tool is a line agents learn to skip.

    An op that will not apply to the running copy stops the walk. Nothing is
    lost by it: `apply_all` is about to refuse the same batch and write
    nothing, so there is no apply for the rest of this report to describe.
    """
    out = []
    so_far = copy.deepcopy(g)
    for i, op in enumerate(ops):
        for vid, was in sorted((op.get("saw") or {}).items()):
            if vid not in so_far.vertices or was is None:
                continue
            now = fingerprint(so_far, vid)
            if now == was:
                continue
            before, _, _ = was.partition("|")
            after = so_far.vertices[vid].status
            out.append({
                "op": i, "kind": op.get("op"),
                "subject": op.get("vertex") or op.get("from"),
                "premise": vid, "was": before, "now": after,
                "answer_changed": before == after,
            })
        try:
            _apply_one(so_far, op)
        except ApplyError:
            break
    return out


def describe(d: dict) -> str:
    """One line for a host to print. Here so both hosts say the same thing."""
    what = ("its answer changed" if d["answer_changed"]
            else f"{d['was']} → {d['now']}")
    return (f"{d['premise']} moved since this batch was staged ({what}) — "
            f"op {d['op']} ({d['kind']} {d['subject']}) rests on it")


# ---- collisions ----------------------------------------------------------
#
# Two writers on one tray is not a supported case (see `README.md`), but it is a
# reachable one: `commands/serve.md` tells a user to work in the browser and a
# terminal at once, and `dg serve` is threaded. When it happens the loser of the
# race gets an op refused, and the words it is refused in decide what happens
# next.
#
# `D01 already exists` is true and its plain reading is false. The op *is* in the
# store — somebody else applied it — and a reader who takes it as "my work
# failed" re-stages under a fresh id and puts two vertices behind one question.
# The genuine clash, where the id is taken by something else entirely, is a
# different fact and keeps its own words.


def _same_vertex(g: Graph, op: dict) -> bool:
    """Whether the store's vertex is the one this op would have created.

    Only the fields `add_vertex` writes, so a later `dg decide` on the same
    vertex does not make this read as a clash — what is being asked is "did my
    op land", not "is the vertex untouched since".
    """
    v = g.vertices[op["id"]]
    return (v.title == op["title"] and v.area == op["area"]
            and v.status == op.get("status", "OPEN")
            and v.note == op.get("note"))


def already(vid: str, same: bool, what: str) -> ApplyError:
    """The exception for "this is already there", in its two readings.

    Returns rather than raises, so the call site reads `raise already(...)` and
    the two kinds are chosen in one place. Shared with `dgraph/task_pending.py`,
    which has the same collision on `add_task` and must not grow a second
    opinion about how it reads.

    `same` means the store holds exactly what the op would have produced, which
    only happens when another writer applied it — no single sequence of `dg`
    commands can reach it, because `preview` shows a caller its own staged ops
    before they land.
    """
    if same:
        return Collision(
            f"{vid} already has this {what}, identical to the op staged — "
            f"another writer applied it while this batch was waiting. Nothing "
            f"of yours was lost; it is in the store, and the tray holds a "
            f"duplicate of it.")
    if what == "vertex":
        return ApplyError(f"{vid} already exists, and is not what this op would "
                          f"have created — pick another id")
    if what == "task":
        return ApplyError(f"{vid} already exists, and is not what this op would "
                          f"have created — pick another id")
    return ApplyError(f"{vid} already has a decided edge — reopen it first")


# ---- composing an add ----------------------------------------------------


def compose_add(g: Graph, *, vid: str, title: str, area: str,
                new_area: bool = False,
                status: str = "OPEN", after: list[str] | None = None,
                note: str | None = None,
                probe: dict | None = None,
                stored: Graph | None = None) -> list[dict]:
    """The op list that records a new decision, validated against `g`.

    Lives here rather than in `dg add` because it is no longer the only door
    onto opening a question: the browser has a form, and `POST /api/add` builds
    the same list through this function. What a second door must not get is a
    second set of rules — the failure this codebase keeps finding is two
    surfaces that agree until the day they do not, and an `add` is exactly the
    shape that hides it, because both doors would produce *something* stageable
    and only one of them would produce the edges.

    Three things this is responsible for, and each was a bug waiting in the
    per-door version:

    - **the edges are part of the op list, not a consequence of it.** A vertex
      staged without them is only a `no_orphans` *warning*, so unlike every
      other group in this module a half-staged add can be applied and pass.
    - **there is no blocked status to stage.** A vertex waits when a premise
      it rests on is unsettled, which is `--after` and nothing else; the
      `BLOCKED:<id>` form is refused with the flag to use instead (`D68`).
    - **the graph-facing refusals happen before anything is staged**, so a typo
      is reported by the act that contains it rather than by `apply`, several
      acts later, in a tray shared with another writer.

    `g` is the *effective* graph — the store with the tray already applied —
    for the same reason every other check here takes one: a parent staged a
    minute ago is a legal parent. `stored` is the same graph without the tray,
    and only sharpens a message: an id taken by an op nobody has applied yet is
    a different problem from an id taken by the record.

    Raises `ApplyError` with the message the surface should show verbatim.
    Missing *flags* are not checked here; that is a question about a command
    line, and `dg add` asks it in typer's own shape before calling this.
    """
    after = list(after or [])
    blank = [name for name, val in (("id", vid), ("title", title),
                                    ("area", area)) if not (val or "").strip()]
    if blank:
        raise ApplyError(f"a decision needs {', '.join(blank)}")
    if vid in g.vertices:
        # "already exists" and "already staged" send a person to different
        # places, and the tray is shared — the id may have been taken by a
        # terminal a minute ago. `stored` is the store without the tray, so
        # the two cases can be told apart wherever both are to hand.
        staged = stored is not None and vid not in stored.vertices
        raise ApplyError(f"{vid} already exists"
                         + (" in the staging area — `dg pending` to review"
                            if staged else ""))
    why = refuse_area(area, own=area_counts(g.areas, g.vertices.values()),
                      other=stored_area_counts(project.find().tasks),
                      owner=owner(), new_area=new_area)
    if why is not None:
        raise ApplyError(why)
    # The area is checked here *as well as* at the write, and the two are not
    # the same check doing the same job. `stage_all` is where the rule is —
    # the one staging function, which no door can miss — and this one runs
    # earlier so the message can name the flag the caller typed, `--area`,
    # rather than an op they never wrote. The same division `editor._parse_add`
    # makes for `** Area`, and the same one the id and status checks have.
    #
    # This comment used to say *"both now check the area too"* of `vet`, which
    # judged a `set_fields` and never an `add_vertex` — so the raw-op door was
    # unguarded and the sentence was why nobody looked. Audit `R-F2`.
    bad = ranges.fault("D", vid)
    if bad:
        raise ApplyError(bad)
    unknown = [x for x in after if x not in g.vertices]
    if unknown:
        raise ApplyError(f"unknown parent(s): {', '.join(unknown)}")
    fault = status_fault(status, g.vertices, of=vid)
    if fault is not None:
        # The fault's own sentence: for the pre-`D68` `BLOCKED:<id>` it names
        # the remedy (`--after`), which "illegal status" would have hidden.
        raise ApplyError(
            f"{fault}\n"
            f"one of {', '.join(sorted(SIMPLE_STATUSES))}")
    if probe is not None:
        fault = probe_fault(probe)
        if fault:
            raise ApplyError(f"--probe: {fault}")
    op = {"op": "add_vertex", "id": vid, "title": title,
          "area": area, "status": status}
    if note:
        op["note"] = note
    if probe is not None:
        op["probe"] = probe
        op["date"] = _date.today().isoformat()
    return [op] + [{"op": "add_edge", "from": p, "to": [vid]} for p in after]


def compose_reprobe(g: Graph, *, vid: str, probe: dict) -> dict:
    """The op that appends a rule for settling `vid`, checked against `g`.

    The refusals `_apply_one` makes, said before staging and in the flag's
    name — the same division `compose_add` draws for the area and the id.
    """
    if vid not in g.vertices:
        raise ApplyError(f"unknown vertex {vid}")
    v = g.vertices[vid]
    if v.settled:
        raise ApplyError(
            f"{vid} is {v.status} — its criterion is the probe on its answer; "
            f"`dg reopen {vid}` first if the question is open again")
    fault = probe_fault(probe)
    if fault:
        raise ApplyError(f"--probe: {fault}")
    return {"op": "reprobe", "vertex": vid, "probe": probe,
            "date": _date.today().isoformat()}


def compose_dep(g: Graph, *, vid: str,
                after: list[str]) -> tuple[list[dict], list[str], list[str]]:
    """`(ops, fresh, already)` for recording that `vid` rests on `after`.

    `task_pending.relation_ops`'s opposite number, and the same three-part
    return for the same reason: what was already held is not a failure and not
    a no-op, it is something the surface has to say, and a caller that reported
    "staged" for an edge already in the store sends its reader to `dg pending`
    looking for an op that is not there.

    Adding a premise to an *answered* question is allowed, and is deliberately
    not the mirror of `compose_undep` refusing to remove one: recording that an
    answer also opened something is additive, and every part of the answer
    still stands. Removing a target says the answer never opened it, which
    contradicts what was written down.
    """
    if vid not in g.vertices:
        raise ApplyError(f"unknown vertex {vid}")
    unknown = [p for p in after if p not in g.vertices]
    if unknown:
        raise ApplyError(f"unknown premise(s): {', '.join(unknown)}")
    if vid in after:
        raise ApplyError(f"{vid} cannot rest on itself")
    held = g.depends(vid)
    already = [p for p in after if p in held]
    fresh = [p for p in after if p not in already]
    return ([{"op": "add_edge", "from": p, "to": [vid]} for p in fresh],
            fresh, already)


def compose_undep(g: Graph, *, vid: str,
                  after: list[str]) -> list[dict]:
    """The ops for removing premises of `vid`.

    A decided premise is edited like a bare one. It was refused until `D57`,
    which settled that an edge's targets belong to the graph and not to the
    answer beside them on the same row — so dropping one rewrites no answer, and
    `reopen` goes on guarding the half that is one. The caller says what the
    edit reinterprets through `retargets`; this composes it either way.

    This used to append a `set_status` to OPEN when the premise removed was
    the one a `BLOCKED:P` status named — a repair with no judgement in it, made
    in the same write so `block_is_a_premise` could not refuse the batch. There
    is nothing to repair now: whether `vid` waits is read off the edges that
    remain (`D68`).
    """
    if vid not in g.vertices:
        raise ApplyError(f"unknown vertex {vid}")
    held = g.depends(vid)
    unknown = [p for p in after if p not in held]
    if unknown:
        raise ApplyError(f"{vid} does not rest on {', '.join(unknown)}\n"
                         f"`dg node {vid}` lists its premises")
    return [{"op": "remove_edge", "from": p, "to": [vid]} for p in after]


def compose_confirm(g: Graph, *, vid: str) -> list[dict]:
    """The op list that re-affirms a PROVISIONAL decision. `dg confirm`'s.

    PROVISIONAL is the one status this tool can create and could not clear.
    `set_status` is a derived op everywhere else — `expand` above is its only
    other producer — so before this existed the only route back to DECIDED was
    another reopen plus another decide, which files a reversal that never
    happened. Reversals are the most valuable thing the graph holds, and
    inventing one to escape a status would be a lie in the record.

    **Two guards, and they are the reason this is not simply an op the caller
    builds.** Posting a bare `set_status` to a staging route stages happily and
    is then refused at `apply` — the store is safe either way, but the refusal
    arrives one act later and names a command rather than the box that was
    ticked. Both are here so both doors ask them at the same moment:

    - the vertex must actually be PROVISIONAL, or there is nothing to
      re-affirm;
    - nothing it rests on may still be under review. While a premise is
      unsettled, PROVISIONAL is the *accurate* status, and re-affirming would
      claim a conclusion the graph cannot support.

    Expanded, not bare, for the PROVISIONAL marks a reopen derives; nothing
    is released by settling any more, since waiting is read off the edges.
    """
    v = g.vertices.get(vid)
    if v is None:
        raise ApplyError(f"unknown vertex {vid}")
    if v.base_status != "PROVISIONAL":
        raise ApplyError(f"{vid} is {v.status}, not PROVISIONAL — there is "
                         f"nothing to re-affirm")
    unsettled = g.provisional_because(vid)
    if unsettled:
        raise ApplyError(f"{vid} still rests on {', '.join(unsettled)}\n"
                         f"settle the premise first — until then PROVISIONAL "
                         f"is the accurate status")
    return expand(g, {"op": "set_status", "vertex": vid, "status": "DECIDED"})


def introduced(g: Graph, ops: list[dict]) -> list[Violation]:
    """The findings `ops` would **add** to `g`, and not the ones already there.

    A store can be invalid before a correction and still invalid after it for
    an unrelated reason, and reporting the whole list would blame this act for
    both. `dg undep` has computed this difference since it was written; it is
    here so that the browser can show the same set *before* staging rather than
    after — which is what turns "removing this releases T07" from a message
    into a thing a person can decline.
    """
    before = {(v.check, v.message) for v in g.validate()}
    try:
        after = preview_ops(g, ops)
    except ApplyError:
        return []
    return sorted((v for v in after.validate()
                   if (v.check, v.message) not in before),
                  key=lambda v: (v.check, v.message))


def preview_ops(g: Graph, ops: list[dict]) -> Graph:
    """`g` with `ops` applied to a copy. Neither the store nor the tray moves."""
    out = copy.deepcopy(g)
    for op in ops:
        _apply_one(out, op)
    return out


# ---- propagation ---------------------------------------------------------


def _settles(op: dict) -> str | None:
    """The vertex this op settles, if any — the trigger for unblocking."""
    if op["op"] == "close":
        return op["vertex"]
    if op["op"] == "set_status" and op["status"].split(":")[0] not in UNSETTLED:
        return op["vertex"]
    return None


def expand(g: Graph, op: dict) -> list[dict]:
    """Derive the ops a change implies.

    Status propagates in both directions, and neither is safe to leave to the
    person typing the command:

    - Reopening a vertex puts every decided descendant on a premise under
      review, so each becomes PROVISIONAL.
    - Settling a vertex used to release everything `BLOCKED:` on it. It
      releases nothing now: a vertex that rests on a settled premise stops
      waiting by derivation, with no op to write (`D68`).

    Computing either by hand across the graph is the mistake the validator
    exists to catch, so the tool does it.
    """
    out = [op]
    if op["op"] == "reopen":
        for d in sorted(g.descendants(op["vertex"])):
            if g.vertices[d].base_status == "DECIDED":
                out.append({
                    "op": "set_status", "vertex": d, "status": "PROVISIONAL",
                    "derived_from": op["vertex"],
                })

    return out


def repairs(g: Graph) -> list[dict]:
    """The ops that would clear every `propagation` finding in `g`.

    `expand` above derives PROVISIONAL from a **reopen op**, which is the only
    producer of that status anywhere in this codebase. That is fine while every
    reopen goes through the tool, and it leaves a hole the moment one does not:
    a merge, a rebase, a partial checkout or a second clone can land a DECIDED
    vertex on a premise that is REOPENED, and then the rule is broken with no op
    to derive the remedy from.

    `dg check` names two remedies for that state and only one of them exists.
    *Settle the premise* works — `dg decide <premise>` clears it — but it means
    recording an answer nobody reached, in the one artifact this tool exists to
    keep honest. *Mark it PROVISIONAL* is the truthful move and had no command.
    This is that command's body.

    **Derived from the finding, not from a status somebody typed.** The starting
    set is the vertices `validate` is *currently* reporting `propagation` on —
    nothing else qualifies — and from each of their unsettled premises this
    walks exactly what `expand` walks, so a repair produces the batch the reopen
    would have produced had it gone through the tool. That keeps `set_status`
    what it has always been: an op the tool derives, never one a caller invents.
    """
    # `Graph.unpropagated` is the rule itself, shared with `validate`, so this
    # can never repair a vertex the checker is not complaining about — nor miss
    # one it is.
    #
    # From each offending pair, `expand`'s own walk. Transitive, because a
    # decided vertex two levels below the unsettled premise rests on it just as
    # much: `propagation` cannot see that one (the vertex between them is
    # DECIDED and therefore counts as settled), but the reopen would have marked
    # it, and leaving it DECIDED is the same untruth one level down.
    out: dict[str, dict] = {}
    for vid, premise in g.unpropagated():
        for d in sorted({vid, *g.descendants(premise)}):
            if g.vertices[d].base_status == "DECIDED" and d not in out:
                out[d] = {"op": "set_status", "vertex": d,
                          "status": "PROVISIONAL", "derived_from": premise}
    return [out[k] for k in sorted(out)]


# ---- apply ---------------------------------------------------------------


def bind_step(held: list[Bind], op: dict) -> list[Bind]:
    """`held` after a `bind` or `unbind` op, for either store.

    A `bind` adds what is not already held and says nothing about what is;
    an `unbind` naming nothing held is refused, as `remove_edge` is, because
    a removal that removes nothing is a claim about the record that the
    record does not bear out. Naming some held and some not removes the
    held ones — the same partial rule the edge has.
    """
    named = [Bind(kind=b["kind"], ref=b["ref"]) for b in op.get("binds") or ()]
    if not named:
        raise ApplyError(f"{op['op']} names no {{kind, ref}} pair: binds")
    rid = op.get("vertex") or op.get("task")
    if op["op"] == "bind":
        return list(held) + [b for b in named if b not in held]
    gone = set(named)
    if not gone & set(held):
        raise ApplyError(
            f"{rid} is not bound to {', '.join(b.spelled for b in named)} — "
            f"nothing to remove")
    return [b for b in held if b not in gone]


def compose_bind(g: Graph, *, vid: str, binds: list[dict],
                 remove: bool = False) -> tuple[dict | None, list[str], list[str]]:
    """`(op, fresh, already)` for binding `vid` — or unbinding, with `remove`.

    `compose_dep`'s shape and for its reason: what was already held (or
    already absent) is not a failure and not a no-op, it is something the
    surface has to say. `op` is None when nothing would change.
    """
    if vid not in g.vertices:
        raise ApplyError(f"unknown vertex {vid}")
    for b in binds:
        fault = bind_fault(b)
        if fault:
            raise ApplyError(fault)
    held = {b.spelled for b in g.vertices[vid].binds}
    spelled = [f"{b['kind']}:{b['ref']}" for b in binds]
    if remove:
        fresh = [s for s in spelled if s in held]
        already = [s for s in spelled if s not in held]
    else:
        fresh = [s for s in spelled if s not in held]
        already = [s for s in spelled if s in held]
    if not fresh:
        return None, fresh, already
    op = {"op": "unbind" if remove else "bind", "vertex": vid,
          "binds": [b for b, s in zip(binds, spelled) if s in fresh]}
    return op, fresh, already


def probe_entry(op: dict) -> Probe | None:
    """The appended entry an `add_vertex`, `add_task` or `reprobe` op writes.

    The *slot* probe — a rule for settling, a definition of done — and not
    the edge payload's, which `_payload` reads off `PAYLOAD`. Its own
    function so that the apply path reads the payload by the tuple and
    nothing else by name, which `tests/test_payload.py` checks by reading
    the source. Dated by the op, else today: the rule `close` uses.
    """
    probe = op.get("probe")
    if not probe:
        return None
    return Probe(kind=probe["kind"], args=probe["args"],
                 date=op.get("date") or _date.today().isoformat())


def _payload(op: dict) -> dict:
    """What a `close` or `reject` op writes onto an edge, by `PAYLOAD`.

    `answer` and `source` are indexed rather than fetched: an op without them
    is malformed, and a `KeyError` here is the same refusal the hand-written
    version gave.
    """
    out = {k: op.get(k) for k in PAYLOAD}
    out["answer"], out["source"] = op["answer"], op["source"]
    return out


def _apply_one(g: Graph, op: dict) -> None:
    kind = op.get("op")
    if kind not in OPS:
        raise ApplyError(f"unknown op {kind!r}")

    if kind == "add_vertex":
        if op["id"] in g.vertices:
            raise already(op["id"], _same_vertex(g, op), "vertex")
        _register(g, op.get("area"))
        g.vertices[op["id"]] = Vertex(
            id=op["id"], title=op["title"], area=op["area"],
            status=op.get("status", "OPEN"), note=op.get("note"),
            # the tag describes the note; without one it describes nothing
            format=op.get("format") if op.get("note") else None,
            # The first entry of the appended list, dated by the op or today
            # — the same date rule `close` uses for its payload.
            probes=[e for e in (probe_entry(op),) if e is not None],
        )
        return

    vid = op.get("vertex") or op.get("from")
    if vid not in g.vertices:
        raise ApplyError(f"unknown vertex {vid!r}")

    if kind == "remove_vertex":
        mode = op.get("mode", "sever")
        if mode not in REMOVAL_MODES:
            raise ApplyError(
                f"unknown removal mode {mode!r} — one of "
                f"{', '.join(REMOVAL_MODES)}"
            )
        # The edges this removal would *write*, before anything is torn down,
        # so a refusal leaves the graph untouched rather than half-rebuilt.
        if mode == "splice":
            pairs = [(p, c) for p in g.depends(vid) for c in g.children(vid)]
        elif mode == "into":
            into = op["into"]
            if into == vid or into not in g.vertices:
                raise ApplyError(f"cannot merge {vid} into {into!r}")
            pairs = ([(p, into) for p in g.depends(vid)]
                     + [(into, c) for c in g.children(vid)])
        else:
            pairs = []
        # **No refusal here, and there used to be two.** Both said that a
        # decided answer's targets are part of that answer, so a splice adding
        # one or a removal dropping one was rewriting an answer. `D57` settled
        # that they are not: an `Edge` row carries the payload *and* the child
        # list, and only the payload — answer, falsifier, source — is what was
        # decided. The children are dependency structure, contributed by
        # whoever wrote them, and `reopen` already guards the half that is the
        # answer.
        #
        # What the caller is told instead is in `retargets`, which the staging
        # commands print. That note is temporary; this absence is not.
        for src, dst in pairs:
            if src != dst:
                _apply_one(g, {"op": "add_edge", "from": src, "to": [dst]})

        # Active edges only. A superseded edge is the record — "this answer, at
        # the time, opened D02" stays true after D02 is deleted, and editing it
        # is the one thing this model never does. `validate` scopes
        # `no_dangling_refs` to active edges for the same reason.
        for e in g.edges:
            if e.active:
                e.to = [t for t in e.to if t != vid]
        # An edge out of the removed vertex goes with it. Elsewhere, a bare
        # active edge left opening nothing says nothing and goes too, while a
        # decided or superseded one stays: those are the record, and a terminal
        # answer opening nothing is an ordinary, legal thing.
        g.edges = [e for e in g.edges
                   if e.src != vid and (e.to or e.decided or not e.active)]
        del g.vertices[vid]
        return

    if kind == "set_status":
        g.vertices[vid] = _dc_replace(g.vertices[vid], status=op["status"])
        return

    if kind == "set_fields":
        # The only op that edits an applied record's wording, and audit `F-F6`
        # is what it is for: `title` and `area` were mutable fields with no
        # mutator, so an agent finding a typo had no legitimate move — it
        # hand-edited `decisions.json`, which is the one route this
        # architecture exists to make unnecessary, or it left the record wrong.
        #
        # **A retitle leaves no record**, and that is decided rather than
        # overlooked. The case for archiving old titles is that they are
        # quoted — in commits, in `docs/`, in `dg why` output somebody pasted
        # into a review — and a changed title makes those citations describe
        # something that no longer reads that way. But a `titles[]` list
        # reaches none of them: it records the old wording *inside the store*,
        # which is not where the stale citation is. The citation problem is the
        # same wall that is the reason there is no `dg renumber`, and it is
        # unsolvable from in here. So an archive would pay for a fourth
        # archival list to render, diff and explain, and collect none of the
        # benefit. What would reopen it: citations becoming resolvable.
        v = g.vertices[vid]
        _register(g, op.get("area"))
        out = _dc_replace(v, **{k: op[k] for k in FIELDS if k in op})
        if not out.note:
            # The tag describes the note; without one it describes nothing.
            # `add_vertex` applies the same rule above, and this op is the only
            # other way a note reaches a vertex.
            out = _dc_replace(out, format=None)
        g.vertices[vid] = out
        return

    if kind == "add_edge":
        e = g.active_edge(vid)
        targets = list(op["to"])
        if e is None:
            g.edges.append(Edge(src=vid, to=sorted(set(targets))))
        else:
            e.to = sorted(set(e.to) | set(targets))
        return

    if kind == "remove_edge":
        # The undo `add_edge` never had. Until it existed, a dependency
        # recorded against the wrong parent could only be repaired by editing
        # `decisions.json` by hand — the one exit this tool is built to make
        # unnecessary (`dgraph/cross.py`: "`dg` is the only way out this tool
        # offers"). The task store has had `remove_dep` all along.
        e = g.active_edge(vid)
        targets = set(op["to"])
        if e is None or not targets & set(e.to):
            raise ApplyError(
                f"{vid} does not open {', '.join(sorted(targets))} — "
                f"nothing to remove"
            )
        # A decided edge is edited like any other. `D57`: the targets belong to
        # the graph, and the answer is the payload beside them — so removing a
        # dependency here rewrites nothing that was decided. `retargets` says
        # so at stage time, where the person is.
        e.to = sorted(set(e.to) - targets)
        # A bare edge opening nothing says nothing. Dropped so it cannot sit in
        # the store as an active edge with no content — and so `active_edge`
        # goes back to None, letting a later `close` build a fresh one.
        # Superseded edges are never touched: they are the record.
        if not e.to:
            g.edges = [x for x in g.edges if x is not e]
        return

    if kind == "close":
        e = g.active_edge(vid)
        targets = sorted(set(op.get("to", [])) | set(e.to if e else []))
        payload = _payload(op)
        payload["date"] = payload["date"] or _date.today().isoformat()
        if e is None:
            g.edges.append(Edge(src=vid, to=targets, **payload))
        else:
            if e.decided:
                same = all(getattr(e, k) == op.get(k) for k in CLAIM)
                raise already(vid, same, "decision")
            e.to = targets
            for k, v in payload.items():
                setattr(e, k, v)
        # A re-decision closes out whatever it replaced. Without a summary the
        # answer's first line stands in — the same default `reopen` uses for
        # the superseded side. Leaving `replaced_by` empty is worse than a
        # rough label: the reversal record would say "(undecided)" forever
        # about a question that has an answer.
        label = op.get("summary") or _clip(op["answer"])
        for old in g.history(vid):
            if old.replaced_by is None:
                old.replaced_by = label
        g.vertices[vid] = _dc_replace(g.vertices[vid], status="DECIDED",
                                      note=None, format=None)
        return

    if kind == "reject":
        # An answer this project was offered and did not take, kept because
        # the alternative is losing it. When somebody keeps this store's
        # answer at the integration seam, the arriving answer and its
        # falsifier survive only in a branch — and filing it as an ordinary
        # superseded edge would have `dg node` say it was once current, which
        # is a claim about this project's history nobody made.
        #
        # It changes nothing about the graph: no status moves, no target is
        # opened, the active edge is untouched. `children` and `depends` walk
        # active edges only, so this is invisible to every traversal and shows
        # up in exactly two places — `Graph.rejected`, and the renderers that
        # call it.
        e = g.active_edge(vid)
        if e is None or not e.decided:
            raise ApplyError(
                f"{vid} has no answer of its own, so there is nothing this "
                f"one was declined in favour of — decide it first, or adopt "
                f"the answer that arrived")
        # An op-shape rule, beside `--why` on a reopen: whose answer it was is
        # the whole record. Without it the edge says only "somebody thought
        # otherwise", which a later reader can neither act on nor trace.
        if not op.get("from_source"):
            raise ApplyError(
                "a declined answer has to say where it came from: --from")
        g.edges.append(Edge(
            src=vid, to=sorted(op.get("to", [])), active=False,
            **_payload(op),
            summary=op.get("summary") or _clip(op["answer"]),
            from_source=op["from_source"],
        ))
        return

    if kind in ("bind", "unbind"):
        # The union and the difference, the way `add_edge` and `remove_edge`
        # take them — `probe.Bind` has the argument. Order is kept so the
        # store reads as it was written; the set is what the ops mean.
        v = g.vertices[vid]
        v.binds = list(bind_step(v.binds, op))
        return

    if kind == "reprobe":
        # Appended, never assigned — `model.Probe` has the argument. Only
        # while the question is unsettled: a settled one is judged by the
        # probe on its answer, and writing a rule for settling under an
        # answer already given would be a second criterion nothing reads.
        v = g.vertices[vid]
        if v.settled:
            raise ApplyError(
                f"{vid} is {v.status} — its criterion is the probe on its "
                f"answer; `dg reopen {vid}` first if the question is open again")
        entry = probe_entry(op)
        if entry is None:
            raise ApplyError(f"reprobing {vid} needs the criterion: --probe")
        v.probes.append(entry)
        return

    if kind == "reopen":
        e = g.active_edge(vid)
        if e is None or not e.decided:
            raise ApplyError(f"{vid} has no decision to reopen")
        # The answer is superseded; the dependency structure is not. The edge
        # keeps its targets and loses only its payload — every field of it,
        # read off `PAYLOAD` so that a field added there is archived and
        # cleared here without this site being told.
        archived = {k: getattr(e, k) for k in PAYLOAD}
        # `format` is this op's dialect, covering the prose composed here —
        # why and summary. The archived answer keeps no dialect of its own:
        # `e.format` describes an answer this edge no longer owns, and a
        # second field for it would be a schema change for the one place
        # the two dialects differ (a single `*…*` span: bold in org,
        # italic in markdown). The web panel renders the archived answer in
        # this dialect and can be that much wrong about its emphasis.
        archived["format"] = op.get("format")
        g.edges.append(Edge(
            src=vid, to=list(e.to), active=False, **archived,
            summary=op.get("summary") or _clip(e.answer or ""),
            replaced_by=None, why=op["why"],
        ))
        for k in PAYLOAD:
            setattr(e, k, None)
        g.vertices[vid] = _dc_replace(
            g.vertices[vid], status="REOPENED",
            note=op.get("note") or op["why"], format=op.get("format"),
        )
        return


def apply_all(g: Graph, ops: list[dict],
              also: Checker | None = None) -> Graph:
    """Apply to a copy and validate. Raises rather than returning a bad graph.

    `also` is an extra validator run over the result, supplied by the caller so
    this module never learns what a second store is. It exists for the
    cross-graph invariants: they are the only ones a decision batch can break
    that `Graph.validate` cannot see, and a batch that breaks them must abort
    here rather than be discovered later by `dg check`, when the store is
    already written and the commit gate is already denying.

    Only *blocking* findings refuse, so demoting a cross-graph check to a
    warning relaxes this guard on its own, with no change in this module.
    """
    out = copy.deepcopy(g)
    for op in ops:
        _apply_one(out, op)
    problems = [p for p in out.validate() if p.blocking]
    problems += [p for p in also(out) if p.blocking] if also else []
    if problems:
        raise ApplyError(
            "would leave the graph invalid:\n  "
            + "\n  ".join(str(p) for p in problems)
        )
    return out
