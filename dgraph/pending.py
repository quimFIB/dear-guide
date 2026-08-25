"""The staging area: compose decisions, review them, then apply atomically.

Staged ops live in `.dgraph-pending.json` (gitignored) and do not touch the
store until `apply`. Apply mutates a copy, validates it, and only then writes —
a tool that can corrupt the source of truth is worse than no tool.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import replace as _dc_replace
from datetime import date as _date
from pathlib import Path

from dgraph import project, ranges
from dgraph.model import (SIMPLE_STATUSES, UNSETTLED, Edge, Graph, Vertex,
                          status_fault)
from dgraph.violation import Violation

OPS = {"close", "reopen", "add_vertex", "add_edge", "remove_edge",
       "remove_vertex", "set_status", "set_fields"}

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
FIELD_KEYS = ("op", "vertex", "task", "ref", "saw")

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
    out = []
    for op in ops:
        if not op.get("ref") or op["ref"] in taken:
            op = {**op, "ref": _new_ref(taken)}
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
    if against is not None:
        ops = [stamp(against, op) for op in ops]
    with held(path):
        current = load(path)
        # Under the lock, so uniqueness is judged against the tray as it is —
        # another writer may have staged since this call started.
        current.extend(_with_refs(ops, current))
        save(current, path)
        # The watermark, raised where an id is *committed to*. Here rather than
        # in `next_id` because all four of that function's callers only offer
        # an id — one of them answers `/api/graph` on every page load, so
        # bumping there would burn an id per refresh. And here rather than in
        # each command because this is the one staging function, for both trays
        # and every door. Outside the lock's purpose but inside its scope, so
        # two writers in one clone cannot interleave a read and a write of it.
        # The root from the tray's own directory when one was passed, not
        # from `project.find()`: a caller that named a tray outside the current
        # project would otherwise raise this clone's watermark for somebody
        # else's grant, which is precisely the write the file exists to stop.
        ranges.note_ops(ops, path.parent if path is not None else None)
        return current


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
        gone = ops.pop(resolve(ops, ref))
        save(ops, path)
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


def vet(g: Graph, op: dict) -> None:
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
        vet_fields(op, areas=g.areas, record="decision",
                   current={k: getattr(v, k) for k in FIELDS})
    if op.get("op") == "set_status" and "derived_from" not in op:
        if status != "DECIDED":
            raise ApplyError(
                f"a status is not set directly — {status} is derived from a "
                f"reopen or from settling a premise, and the only status a "
                f"caller may write is DECIDED, to re-affirm a PROVISIONAL "
                f"decision")
        compose_confirm(g, vid=op.get("vertex"))


def _and_(names: list[str]) -> str:
    """`a`, `a and b`, `a, b and c` — for a refusal naming several fields."""
    return (names[0] if len(names) == 1
            else " and ".join([", ".join(names[:-1]), names[-1]]))


def vet_fields(op: dict, *, areas: list[str], current: dict,
               record: str) -> None:
    """The stage-time floor for a `set_fields` op, shared by both stores.

    Here rather than in each store's `vet` for the reason `already` and
    `matches` are shared: the four fields are the same four, and a rule applied
    in one store and not its twin is the shape most of this tool's audit
    findings took.

    Refused at *stage* time rather than left to `validate`, though `area_known`
    would catch an unknown area at apply. Two reasons. A tray may be shared, so
    an op that cannot apply wedges it for every other writer until somebody
    drops it — audit `F30`, and the argument for `vet_all` existing at all. And
    there is no invariant at all for a blank title: nothing in either store's
    `validate` reads one, so an op that empties a title would land, and the
    record would then be referred to by nothing.

    `current` is what the record holds now, so that an op writing the values
    already there is refused rather than staged. The caller is often an agent
    that has lost track, which is the same reading the no-op status guard makes.
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
    if "area" in op and op["area"] not in areas:
        raise ApplyError("unknown area. one of: " + ", ".join(areas))
    if all(op[k] == current.get(k) for k in named):
        one = len(named) == 1
        raise ApplyError(
            f"{op.get('vertex') or op.get('task')} already has "
            f"{'that ' + named[0] if one else 'those values'}")


def vet_all(g: Graph, ops: list[dict]) -> None:
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
        vet(probe, op)
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
                status: str = "OPEN", after: list[str] | None = None,
                note: str | None = None,
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
    - **`BLOCKED:<id>` stages its edge too.** A block asserts a dependency, and
      dependency is the edge list — never a second copy in a status field. The
      blocker joins `after` here so that `dg pending` shows the structure a
      person is about to write, rather than having it materialise at apply time
      out of a status string.
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
    if area not in g.areas:
        raise ApplyError("unknown area. one of: " + ", ".join(g.areas))
    # Checked here *as well as* in `vet`, and both are needed. This function is
    # the flag path and `/api/add`; `vet` is the raw-op path and the editor.
    # The area rule above can be checked in one place because `area_known`
    # refuses the batch at apply if it slips through — a grant has no invariant
    # behind it, so a route that skips the check is a route that writes an id
    # inside another writer's range.
    bad = ranges.fault("D", vid)
    if bad:
        raise ApplyError(bad)
    unknown = [x for x in after if x not in g.vertices]
    if unknown:
        raise ApplyError(f"unknown parent(s): {', '.join(unknown)}")
    fault = status_fault(status, g.vertices, of=vid)
    if fault is not None:
        raise ApplyError(
            f"illegal status {status!r}\n"
            f"one of {', '.join(sorted(SIMPLE_STATUSES))}, "
            f"or BLOCKED:<existing id>")
    if status.startswith("BLOCKED:"):
        blocker = status.split(":", 1)[1]     # `status_fault` proved it exists
        if blocker not in after:
            after.append(blocker)
    op = {"op": "add_vertex", "id": vid, "title": title,
          "area": area, "status": status}
    if note:
        op["note"] = note
    return [op] + [{"op": "add_edge", "from": p, "to": [vid]} for p in after]


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
                  after: list[str]) -> tuple[list[dict], str | None]:
    """`(ops, blocker_released)` for removing premises of `vid`.

    Only a **bare** edge, one whose source has not been decided. A decided
    edge's targets are part of its answer, so dropping one claims the answer no
    longer opens that question — reopen first, and decide again meaning it.

    The `set_status` in the op list is the one repair here with no judgement in
    it, so it is made rather than asked about: `BLOCKED:P` asserts a dependency
    on P, and the edge carrying that dependency is being removed, so the status
    is false the moment this applies. It goes in the **same list**, and
    therefore the same write, because `block_is_a_premise` is an error and
    `apply_all` would otherwise refuse the whole batch over an invariant the
    user did not break — audit F31, and the reason this is composed in one
    place rather than assembled at each door.
    """
    if vid not in g.vertices:
        raise ApplyError(f"unknown vertex {vid}")
    held = g.depends(vid)
    unknown = [p for p in after if p not in held]
    if unknown:
        raise ApplyError(f"{vid} does not rest on {', '.join(unknown)}\n"
                         f"`dg node {vid}` lists its premises")
    decided = [p for p in after
               if (e := g.active_edge(p)) is not None and e.decided]
    if decided:
        raise ApplyError(
            f"{', '.join(decided)} is decided, and its targets are part of "
            f"that answer\n`dg reopen {decided[0]}` first — that strips the "
            f"payload and leaves the dependency editable — then remove the "
            f"edge and decide again with the targets you mean")
    ops = [{"op": "remove_edge", "from": p, "to": [vid]} for p in after]
    blocker = g.vertices[vid].blocker
    released = (g.vertices[vid].base_status == "BLOCKED"
                and blocker in after)
    if released:
        ops.append({"op": "set_status", "vertex": vid, "status": "OPEN",
                    "derived_from": blocker})
    return ops, (blocker if released else None)


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

    Expanded, not bare: settling a vertex releases everything `BLOCKED:` on it,
    and leaving that to the caller is how a block goes stale and `apply`
    refuses the whole batch.
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
    - Settling a vertex releases everything `BLOCKED:` on it. Without this the
      block goes stale, `apply` refuses the whole batch, and the remedy — one
      `set_status` per blocked vertex — is left to be worked out by hand.

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

    settled = _settles(op)
    if settled is not None:
        for vid, v in sorted(g.vertices.items()):
            if v.base_status == "BLOCKED" and v.blocker == settled:
                out.append({
                    "op": "set_status", "vertex": vid, "status": "OPEN",
                    "derived_from": settled,
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


def _apply_one(g: Graph, op: dict) -> None:
    kind = op.get("op")
    if kind not in OPS:
        raise ApplyError(f"unknown op {kind!r}")

    if kind == "add_vertex":
        if op["id"] in g.vertices:
            raise already(op["id"], _same_vertex(g, op), "vertex")
        g.vertices[op["id"]] = Vertex(
            id=op["id"], title=op["title"], area=op["area"],
            status=op.get("status", "OPEN"), note=op.get("note"),
            # the tag describes the note; without one it describes nothing
            format=op.get("format") if op.get("note") else None,
        )
        # `BLOCKED:X` asserts a dependency, and dependency is the graph
        # structure — a status field holding one is the second copy this model
        # exists to refuse. Recorded here rather than in the callers so that
        # every route in (the CLI, the editor, the web app) agrees by
        # construction and `block_is_a_premise` cannot be broken by whichever
        # one forgets. `add_edge` unions, so a caller that stages the edge
        # itself — as `dg add` does, to keep it visible in the tray — costs
        # nothing.
        #
        # A blocker that names no vertex, or names this one, is left alone:
        # `status_legal` reports both, and inventing an edge to an id that does
        # not resolve would turn a clear finding into a dangling reference.
        blocker = g.vertices[op["id"]].blocker
        if blocker and blocker != op["id"] and blocker in g.vertices:
            _apply_one(g, {"op": "add_edge", "from": blocker,
                           "to": [op["id"]]})
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
        for src, dst in pairs:
            e = g.active_edge(src)
            if e is not None and e.decided and dst not in e.to:
                raise ApplyError(
                    f"this would make {src}'s answer open {dst}, which it "
                    f"never did — `dg reopen {src}` first, then decide again "
                    f"with the targets you mean"
                )
        # A decided answer's targets are part of that answer. Dropping one says
        # the answer never opened this vertex, which contradicts what was
        # written down — `remove_edge` refuses the identical edit in as many
        # words, and this used to make it silently, with no reversal filed and
        # nothing in the confirmation to say so. Checked before anything is torn
        # down, like the splice/into check above, so a refusal leaves the graph
        # untouched.
        #
        # The way through is the way it always is: reopen, which strips the
        # payload and leaves a bare edge this op may then edit, then decide
        # again with the targets you mean.
        rewrites = sorted({e.src for e in g.edges
                           if e.active and e.decided and vid in e.to})
        if rewrites:
            one = len(rewrites) == 1
            raise ApplyError(
                f"{', '.join(rewrites)} {'is' if one else 'are'} decided and "
                f"{'its answer opens' if one else 'their answers open'} {vid} — "
                f"removing it would rewrite {'that answer' if one else 'those '
                'answers'} to say {'it' if one else 'they'} never did. "
                f"`dg reopen {rewrites[0]}` first, then remove {vid} and decide "
                f"again with the targets you mean"
            )

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
        # `BLOCKED:<removed>` names a vertex that no longer exists. No
        # judgement is available — the blocker is gone — so the status is
        # repaired here rather than left for `status_legal` to refuse a batch
        # over an invariant the caller did not break. `dg confirm` releases
        # blocked vertices to OPEN the same way.
        for other, v in list(g.vertices.items()):
            if v.blocker == vid:
                g.vertices[other] = _dc_replace(v, status="OPEN")
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
        if e.decided:
            # A decided edge's targets are part of the answer: dropping one
            # claims the answer no longer opens that question. Rewriting an
            # answer in place is the one thing this model never does, so the
            # way through is the way it always is — reopen, which strips the
            # payload and leaves a bare edge this op may then edit.
            raise ApplyError(
                f"{vid} is decided, and its targets are part of that answer — "
                f"`dg reopen {vid}` first, then remove the edge and decide "
                f"again with the targets you mean"
            )
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
        payload = dict(
            answer=op["answer"], falsifier=op.get("falsifier"),
            source=op["source"], date=op.get("date") or _date.today().isoformat(),
            format=op.get("format"),
        )
        if e is None:
            g.edges.append(Edge(src=vid, to=targets, **payload))
        else:
            if e.decided:
                same = (e.answer == op["answer"] and e.source == op["source"]
                        and e.falsifier == op.get("falsifier"))
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

    if kind == "reopen":
        e = g.active_edge(vid)
        if e is None or not e.decided:
            raise ApplyError(f"{vid} has no decision to reopen")
        # The answer is superseded; the dependency structure is not. The edge
        # keeps its targets and loses only its payload.
        g.edges.append(Edge(
            src=vid, to=list(e.to), active=False,
            answer=e.answer, falsifier=e.falsifier, source=e.source,
            date=e.date, summary=op.get("summary") or _clip(e.answer or ""),
            replaced_by=None, why=op["why"],
            # `format` is this op's dialect, covering the prose composed here —
            # why and summary. The archived answer keeps no dialect of its own:
            # `e.format` describes an answer this edge no longer owns, and a
            # second field for it would be a schema change for the one place
            # the two dialects differ (a single `*…*` span: bold in org,
            # italic in markdown). The web panel renders the archived answer in
            # this dialect and can be that much wrong about its emphasis.
            format=op.get("format"),
        ))
        e.answer = e.falsifier = e.source = e.date = None
        e.format = None
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
