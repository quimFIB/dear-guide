"""Where the two graphs meet, and the only module that may see both.

`dgraph/model.py` does not know what a task is; `dgraph/tasks.py` does not know
what a decision is. Neither can import the other, and a test enforces it. That
mutual ignorance is what makes the barrier structural rather than a convention
somebody has to remember — so everything that needs both stores lives here, and
`cross.py` appearing in a second module's imports is visible in a diff.

The link is stored on the task and derived in the other direction, which is the
codebase's own rule ("dependency is the graph structure, never a stored field")
applied by cardinality. A task names a bounded handful of decisions — the
premises it rests on, and the one question its outcome informs — while a
decision has unboundedly many dependents, so the task is the stored side. That
`because` became a list on 2026-08-27 does not weaken the argument: bounded and
enumerable on one side, unbounded on the other, is what makes that side the
place to keep it. The practical
consequence is that `decisions.json` never mentions a task, so a change to it
always means a decision changed — and the one artifact whose git history is
supposed to be readable stays readable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from dgraph import env
from dgraph.model import Graph
from dgraph.tasks import TaskGraph
from dgraph.violation import Violation, cycle_from


def reverse(tg: TaskGraph) -> tuple[dict, dict]:
    """`did -> tasks` for both cross-graph relations, in one pass.

    `rests_on` and `evidence` each read every task to answer about one
    decision, and the callers that matter ask about every decision in turn — a
    lens predicate evaluated per vertex, or `late_evidence` walking the whole
    store. Built inside the call that wants it and dropped with it, never
    cached: same reason as `Graph._reverse`, and here the staleness would be
    across two stores rather than one.
    """
    because: dict[str, list[str]] = {}
    backs: dict[str, list[str]] = {}
    for task in tg.tasks.values():
        for did in task.because:
            because.setdefault(did, []).append(task.id)
        if task.evidence_for:
            backs.setdefault(task.evidence_for, []).append(task.id)
    return ({k: sorted(v) for k, v in because.items()},
            {k: sorted(v) for k, v in backs.items()})


def rests_on(tg: TaskGraph, did: str, _rev=None) -> list[str]:
    """The tasks that exist because of this decision. The derived reverse."""
    if _rev is not None:
        return list(_rev[0].get(did, ()))
    return sorted(t.id for t in tg.tasks.values() if did in t.because)


def evidence(tg: TaskGraph, did: str, _rev=None) -> list[str]:
    """The tasks whose outcome bears on this decision. The derived reverse."""
    if _rev is not None:
        return list(_rev[1].get(did, ()))
    return sorted(t.id for t in tg.tasks.values() if t.evidence_for == did)


def pending_evidence(tg: TaskGraph, did: str, _rev=None) -> list[str]:
    """Evidence tasks not finished yet — what an open decision is waiting on."""
    return [t for t in evidence(tg, did, _rev) if tg.tasks[t].unfinished]


#: How much an AGENT may settle on its own, read from `$DG_DECIDE`. Only ever
#: consulted for an owned caller: a supervisor -- anybody with no `$DG_AGENT` --
#: is unaffected by every value, which is the same shape the rest of the
#: ownership model has.
#:
#:   open      (default) an agent may close anything. What the tool has always
#:             done, and what the agentic demo is written against.
#:   evidence  an agent may close a decision only where a FINISHED task is
#:             `--evidence-for` it. The measured half of the distinction below.
#:   never     an agent may not close at all -- refused before an answer is
#:             composed, and a caller with no `$DG_AGENT` decides it.
#:
#: **Why this is a policy and not a default.** The graph has two exits from a
#: decision and both are wrong for a premature one: `dg reopen` files a reversal,
#: and a reversal means *we changed our mind* rather than *that should not have
#: been written*; `dg rm` erases, is for things that should never have been
#: written, and `dg gate` answers `ask` on it, so a person decides anyway. There
#: is no vocabulary for "an agent decided this too early", which is the real
#: argument for restraint -- not that agents judge badly.
#:
#: But the restraint that is right depends on the decision, not on who is asking.
#: A decision whose falsifier is a MEASUREMENT THE AGENT MADE -- the benchmark
#: ran, the number is 0.62 -- is a fact being recorded, and the falsifier writes
#: itself. A judgement between defensible alternatives is where a falsifier
#: written by something that never had to live with the consequence comes out as
#: rationalisation. Only the first of those is mechanically recognisable, and
#: `evidence` is exactly the check for it.
#:
#: **Every value refuses before anything is staged, and the alternative is
#: deferred rather than dismissed.** A refused close leaves the tray empty: the
#: agent does not compose an answer, a source and a falsifier that a person then
#: has to turn down, which is `cli.decide`'s own argument for stage-time guards.
#: The other shape -- `never` meaning *the agent stages and only a person
#: applies* -- is coherent, and is what the tray's review flow (`dg pending
#: --agent`, `dg apply --agent`, `dg clear --agent`) already does for every other
#: op. It is not what happens today, and these messages used to say it did. What
#: it would cost is a policy check at a door that has none -- `apply` -- so that
#: a staged close could not be published by its own author, which is a different
#: mechanism from this one rather than a wording change to it.
#:
#: **This is cooperative, like everything else here.** `$DG_AGENT` is
#: self-declared, so `$DG_DECIDE` is self-declarable too; an agent that unset it
#: would be unowned, and an unowned caller is a supervisor. Nothing here is a
#: security boundary and it is not trying to be one -- it is a rule the launcher
#: sets so that the honest failure is caught, in the same way `dg apply` refuses
#: a tray it did not stage.
#: The name and the words live in `dgraph/env.py` with the rest of the family,
#: and are imported back under the names they have always had. This module is
#: where the *judgement* is; what moved is the parsing, so that the binary
#: which composes an environment and the one which obeys it cannot come to
#: disagree about what `evidence` means.
POLICIES = env.POLICIES
POLICY_ENV = env.POLICY_ENV
policy = env.policy


def refuse_close(tg: TaskGraph | None, did: str, owner: str | None,
                 chosen: str | None = None) -> str | None:
    """Why this caller may not close `did` yet -- or `None` if it may.

    `owner` is `pending.owner()`: `None` is the supervisor and is never refused.
    `tg` is the task store, or `None` where the project has none -- in which
    case `evidence` can never be satisfied, and says so rather than refusing
    with a reason nobody can act on.
    """
    if owner is None:
        return None
    mode = chosen or policy()
    if mode == "never":
        return (f"${POLICY_ENV}=never: {owner} may not close {did} — a caller "
                f"with no $DG_AGENT decides it")
    if mode != "evidence":
        return None
    if tg is None:
        return (f"${POLICY_ENV}=evidence, and this project has no task store, "
                f"so no decision can carry evidence. `dg task init`, or set "
                f"${POLICY_ENV}=open")
    backing = evidence(tg, did)
    if not backing:
        return (f"${POLICY_ENV}=evidence: nothing is `--evidence-for {did}`, so "
                f"there is no measurement for {owner} to be recording. Link the "
                f"work that bears on it — `dg task link <id> --evidence-for "
                f"{did}` — or leave the question open for a person")
    unfinished = [t for t in backing if tg.tasks[t].unfinished]
    if len(unfinished) == len(backing):
        return (f"${POLICY_ENV}=evidence: {did}'s evidence has not finished "
                f"({', '.join(unfinished)}), so the measurement it would be "
                f"recording does not exist yet")
    return None


def gated_by(tg: TaskGraph, g: Graph, tid: str) -> str | None:
    """The first decision this task waits on, if it is waiting on any.

    `task_link`'s `gating` is the whole set. This stays a single id because its
    callers ask "is this startable" and one unsettled premise answers that; a
    reader that means to *name* what is holding work back wants the set, and
    naming one of three is the shape `V-F4` was filed for.

    `None` when the task names no premise *or* every premise is already settled —
    the question every caller actually has is "is this work startable?", not
    "does this link exist". An unknown premise is skipped rather than returned,
    for the reason `Graph.depends` documents: a traversal helper that trusts ids
    crashes the validator that was about to report the dangling reference.
    """
    for did in tg.tasks[tid].because:
        if did in g.vertices and not g.vertices[did].settled:
            return did
    return None


def ready(tg: TaskGraph, g: Graph, tid: str) -> bool:
    """Startable for real: prerequisites resolved *and* premise settled."""
    return tg.ready(tid) and gated_by(tg, g, tid) is None


# ---- what a fan-out may run side by side ----------------------------------
#
# The ready set is already an antichain under `precedes`: a ready task has no
# unresolved prerequisite, so no ready task precedes another. What two ready
# tasks can still do is meet at the seam — both of them naming one decision in
# the other store — and that is the relation `D45` settled, here because it is
# the one place allowed to say what `because` and `evidence_for` mean.


def seam(tg: TaskGraph, tid: str) -> set[str]:
    """The decisions a task names, across the seam: its premises and the one
    it is evidence for. `LINK_FIELDS`, as a set of ids. One hop; `stands_on`
    is the closure."""
    t = tg.tasks[tid]
    out = set(t.because)
    if t.evidence_for:
        out.add(t.evidence_for)
    return out


def stands_on(tg: TaskGraph, g: Graph | None, tid: str) -> dict[str, str]:
    """Every decision a task's work would be under if it moved, and the named
    one it reaches each through: `{decision: seam member}`.

    The seam, closed upward. A task rests on its premises and on everything
    those premises rest on — `pending.expand` marks every decided descendant
    of a reopened decision PROVISIONAL, so a write anywhere in the ancestry
    of a premise reaches the work the same way a write to the premise does.
    The decision a task is evidence for is in it too, with its ancestry: a
    task about to close `D03` is building on `D02` when `D03` rests on it.

    A named decision maps to itself; an ancestor maps to the premise (or the
    write) it sits under, which is what a held row prints as *through*. With
    no decision store there is no ancestry and this is `seam`. An unknown id
    is kept as itself and not walked, for the reason `gated_by` gives.
    """
    out: dict[str, str] = {}
    into = g._reverse() if g is not None else None
    for named in sorted(seam(tg, tid)):
        out.setdefault(named, named)
        if g is not None and named in g.vertices:
            for anc in g.ancestors(named, into):
                out.setdefault(anc, named)
    return out


def collision(tg: TaskGraph, g: Graph | None, a: str, b: str) -> str | None:
    """The decision two tasks would collide on, or `None`. `D45`, re-decided
    2026-09-02 as the ancestry reading (audit `K-F2`).

    Two tasks collide when one *writes* a decision the other's work stands
    on — directly, as a premise or the decision it is itself evidence for, or
    through the ancestry of one of those. Finishing evidence closes or
    reopens its decision; the other task is then either about to close the
    same one (the tray refuses the second `close`, and a paid-for run is
    discarded) or stands on it, at any distance (its finished work turns
    PROVISIONAL under it, because the reopen marks every decided descendant).
    Sharing a premise alone is not a collision: two tasks resting on one
    settled decision read it and move nothing.

    **Kept as one function so the rule can be relaxed in one place.** `D45`'s
    falsifier is that this proves too strict — pairs held apart that would
    both have finished with neither's work under review. If that arrives, the
    one-hop reading is `seam` in place of `stands_on` here, and nothing else
    moves.

    Symmetric, and a single id rather than the set: a caller asking "may these
    two run together?" needs one witness a person can check by reading two
    records, which is the bar `docs/query-framework.md` sets. `through` says
    which named decision the witness sits under, for the row that prints it.
    """
    ta, tb = tg.tasks[a], tg.tasks[b]
    if ta.evidence_for and ta.evidence_for in stands_on(tg, g, b):
        return ta.evidence_for
    if tb.evidence_for and tb.evidence_for in stands_on(tg, g, a):
        return tb.evidence_for
    return None


def through(tg: TaskGraph, g: Graph | None, tid: str, did: str) -> str | None:
    """The decision `tid` names that `did` sits under — or `None` where `tid`
    names `did` itself, or does not stand on it at all. For a held row:
    *shares D02 with T01 (through D03)*."""
    named = stands_on(tg, g, tid).get(did)
    return None if named is None or named == did else named


@dataclass(frozen=True)
class Independent:
    """A set of tasks no two of which collide, and why the rest could not join.

    `held` is one row per candidate left out: `(task, member, decision)` — the
    task, the chosen member it collides with, and the decision they meet on.
    Together the two lists are a proof a person can check by reading pairs:
    that `chosen` is independent, and that nothing outside it can be added.
    They do not claim that no *larger* independent set exists; see
    `maximal_independent`.
    """
    chosen: list[str]
    held: list[tuple[str, str, str]]
    #: What the set was chosen *against* and could not choose: work already
    #: in flight. A `held` row may name one of these as its member.
    fixed: list[str] = field(default_factory=list)


def maximal_independent(candidates: list[str],
                        collide: Callable[[str, str], str | None],
                        *, fixed: list[str] = ()) -> Independent:
    """A maximal set of candidates no two of which collide. Greedy, in order.

    **This is the function to replace when the selection should get smarter.**
    It knows nothing about tasks: it takes ids in the order the caller wants
    them tried and a predicate returning the witness that two collide, and
    returns a set plus the reason each other candidate stayed out. A better
    algorithm keeps that contract — every candidate not chosen collides with a
    named member — and callers need not change.

    `fixed` is what the set is chosen *against* as well as from: members that
    are already taken, cannot be chosen, and hold a candidate out exactly as a
    chosen one does. Without it the set is independent of itself and of
    nothing else — the things filtered out of the candidates are the things a
    new member is most likely to meet (audit `K-F1`). They are never in
    `chosen`; a `held` row may name one.

    Maximal, not maximum. The largest independent set is set packing, which is
    NP-hard, and more to the point is not checkable by hand: that a set is
    independent is confirmed by reading its members, and that no larger one
    exists is confirmed by reading nothing. Only one error direction costs
    anything here — a set that is not independent wastes an agent run, while
    a set that could have been larger idles one slot until the next setup. So
    what is guaranteed is soundness and maximality, both of which `held`
    demonstrates row by row.

    Greedy in the caller's order rather than by degree, so that the same
    stores produce the same set every time: a supervisor comparing two setups
    has to be able to tell a real change from a reshuffle.
    """
    fixed = list(fixed)
    against: list[str] = list(fixed)
    chosen: list[str] = []
    held: list[tuple[str, str, str]] = []
    for c in candidates:
        for m in against:
            d = collide(c, m)
            if d is not None:
                held.append((c, m, d))
                break
        else:
            chosen.append(c)
            against.append(c)
    return Independent(chosen, held, fixed)


def independent(tg: TaskGraph, g: Graph) -> Independent:
    """The ready tasks a fan-out may hand out at once, and why the rest wait.

    Candidates are the ready set in id order — `ready`, so a task whose premise
    is unsettled or whose prerequisite is unfinished is not offered, and sorted,
    so the answer depends on the stores and not on the order rows sit in the
    files. The relation is `collision`; the algorithm is `maximal_independent`.

    Chosen against the `DOING` tasks as well: work somebody is holding is not
    offered and is exactly what a new agent would collide with — a second
    piece of evidence for a decision whose first is being gathered right now,
    which is the ordinary state of a store at a relaunch or when agents are
    added to a run. `PARKED` is not in flight and holds nothing out.
    """
    cands = [t for t in sorted(tg.tasks) if ready(tg, g, t)]
    doing = [t for t in sorted(tg.tasks) if tg.tasks[t].status == "DOING"]
    return maximal_independent(cands, lambda a, b: collision(tg, g, a, b),
                               fixed=doing)


def task_link(tg: TaskGraph, g: Graph, tid: str) -> dict:
    """Everything the cross-graph link says about one task, in one reading.

    `dg context` needs all of it at once — the premise, whether that premise
    resolves, whether it gates the work — and assembling it from the task's own
    fields is exactly the second implementation of the rule this module exists
    to prevent. Callers get the ids from here and never read `Task.because`
    themselves.

    `premises` is every `because` decision *that exists*, in the order the task
    names them; `premise` is the first of those, kept for the readers that still
    render a chain off one. `dangling` is the ids that resolve to nothing — the
    list rather than a flag, because a task can rest on three premises with one
    missing and a reader told only *that* something dangles has to find which.
    A separate fact from having no link at all, and what `dg check` reports.
    """
    t = tg.tasks[tid]
    resolving = [did for did in t.because if did in g.vertices]
    return {
        "because": list(t.because),
        "evidence_for": t.evidence_for,
        "premises": resolving,
        "premise": resolving[0] if resolving else None,
        "dangling": [did for did in t.because if did not in g.vertices],
        "gating": [did for did in resolving if not g.vertices[did].settled],
        "gated_by": gated_by(tg, g, tid),
        "ready": ready(tg, g, tid),
        "unfinished": t.unfinished,
    }


#: The cross-graph link's fields, named here because this is where naming them
#: is allowed. `dgraph/query.py` builds its field table off the dataclasses and
#: would therefore *offer* these without ever mentioning them, so it is handed
#: this list to withhold them — a generic string match on `because` would be a
#: second implementation of `rests_on` below, which looks like a plain
#: comparison and is really the derived reverse of the relation.
LINK_FIELDS = ("because", "evidence_for")


def lenses(g: Graph | None, tg: TaskGraph | None, *,
           archived: bool = True) -> list:
    """Both stores as queryable surfaces, with the cross-graph terms supplied.

    Here rather than in `cli`, though `cli` is allowed to reason about the link
    too, because the browser needs the same surface and `server.py` is *not* on
    the allowlist `tests/test_cross.py` enforces. Two copies of this would be
    the `brief` "is this premise shaky?" bug again, with the copies further
    apart.

    **Two tiers, and they fail differently when a store is missing.** `because`
    and `evidence` read only the task store, so a project with no decisions
    keeps them. The rest need both, and are simply absent otherwise — which is
    the point: `query.vet` then turns a query naming one into a fault rather
    than a quiet half-answer. That matters most for `is:ready`, where
    `gated_by` returning `None` with no decision store is the right answer to
    "can I start this?" and the wrong answer to a predicate asserting a
    property.

    **What is left out is recorded, not just omitted.** A predicate that needs
    the other store goes into `withheld` naming the store it needs, so that
    `query.vet` can say *`is:ready` needs a decision store* rather than *no
    predicate `is:ready`*. The second sentence is a lie of the useful kind: it
    sends a reader to fix their spelling when the truth is that their spelling
    was right and their project is missing a store.
    """
    from dgraph import query as _q

    out = []
    if g is not None:
        preds, absent = {}, {}
        if tg is not None:
            rev = reverse(tg)
            waiting = {vid for vid in g.frontier()
                       if pending_evidence(tg, vid, rev)}
            preds["decidable"] = lambda vid: (
                not g.vertices[vid].settled and not g.waiting_on(vid)
                and vid not in waiting)
            preds["implemented"] = lambda vid: bool(rests_on(tg, vid, rev))
            preds["awaiting-evidence"] = lambda vid: vid in waiting
        else:
            preds["decidable"] = lambda vid: (
                not g.vertices[vid].settled and not g.waiting_on(vid))
            # Plus everything the task lens would have answered: a store that
            # is missing still has a vocabulary, and denying its predicates
            # sends the reader to fix a spelling that was right.
            absent = dict.fromkeys(
                ("implemented", "awaiting-evidence",
                 *(p for p in _q.TASK_PREDICATES
                   if p not in _q.DECISION_PREDICATES)), "task")
        out.append(_q.decision_lens(g, predicates=preds, withheld=absent,
                                    archived=archived))
    if tg is not None:
        preds, struct, absent = {}, {}, {}
        trev = reverse(tg)
        struct["because"] = lambda did: set(rests_on(tg, did, trev))
        struct["evidence"] = lambda did: set(evidence(tg, did, trev))
        if g is not None:
            preds["ready"] = lambda tid: ready(tg, g, tid)
            preds["gated"] = lambda tid: gated_by(tg, g, tid) is not None
            loose = {d["id"] for d in unharvested(tg, g)}
            preds["unharvested"] = lambda tid: tid in loose
        else:
            absent = dict.fromkeys(
                ("ready", "gated", "unharvested",
                 *(p for p in _q.DECISION_PREDICATES
                   if p not in _q.TASK_PREDICATES)), "decision")
        out.append(_q.task_lens(
            tg, predicates=preds, structural=struct, withheld=absent,
            # Both of these are terms on the *task* lens whose argument is a
            # *decision* id, which is the whole point of them. Without saying
            # so, resolving the argument against the task ids would refuse
            # every correct `because:D05` there is.
            arg_kind=dict.fromkeys(("because", "evidence"), "decisions"),
            hide=LINK_FIELDS))
    return out


def blast_radius(tg: TaskGraph, dids: list[str]) -> list[str]:
    """Unfinished work resting on any of these decisions.

    What `dg reopen` reports beyond the decisions it drags into PROVISIONAL:
    pass the reopened decision together with everything the reopen just marked,
    and the answer is the work that is now building on a premise under review.
    """
    wanted = set(dids)
    return sorted(t.id for t in tg.tasks.values()
                  if any(p in wanted for p in t.because) and t.unfinished)


#: The decision statuses that mean "this answer is being re-examined". A task
#: resting on one of these is building on a premise under review. Deliberately
#: excludes OPEN/BLOCKED: work planned ahead of a decision that was never
#: settled is ordinary, and flagging it would produce noise proportional to the
#: backlog, which is how a check teaches people to ignore it.
UNDER_REVIEW = ("REOPENED", "PROVISIONAL")


def under_review(tg: TaskGraph, g: Graph) -> list[dict]:
    """Unfinished work whose premise is being re-examined, with the reason.

    The one cross-graph reading both `dg check` and `dg brief` need, computed
    here so the rule for "is this premise shaky?" exists once.
    """
    out = []
    for tid in tg.frontier():
        t = tg.tasks[tid]
        for did in t.because:
            if did not in g.vertices:
                continue
            premise = g.vertices[did]
            if premise.base_status in UNDER_REVIEW:
                out.append({"id": tid, "title": t.title,
                            "status": t.status,
                            "because": did, "premise_status": premise.status})
    return out


def unharvested(tg: TaskGraph, g: Graph) -> list[dict]:
    """Finished work whose question is still open.

    The spike ran and nobody wrote down what it told us. Invisible in either
    graph alone — the task looks done, the decision looks merely undecided —
    and always actionable, which is what makes it the most valuable reading
    the join produces.
    """
    out = []
    for tid in sorted(tg.tasks):
        t = tg.tasks[tid]
        if t.status != "DONE" or not t.evidence_for:
            continue
        if t.evidence_for not in g.vertices:
            continue
        if not g.vertices[t.evidence_for].settled:
            out.append({"id": tid, "title": t.title, "outcome": t.outcome,
                        "decision": t.evidence_for,
                        "decision_title": g.vertices[t.evidence_for].title})
    return out


def dropped_evidence(tg: TaskGraph, g: Graph) -> list[dict]:
    """Unsettled decisions whose evidence was abandoned, all of it.

    The silence neither store can break alone, and the sharper sibling of
    `unharvested`. `pending_evidence` filters on `unfinished`, which DROPPED is
    not, so a decision that was waiting on a spike reports waiting on nothing
    the moment the spike is given up on; `unharvested` stays quiet too, because
    it only fires on DONE. Between them the decision reads as merely undecided,
    when in fact the thing that was going to settle it is never happening.

    Only when *every* evidence task was dropped. One surviving spike and the
    decision is visibly waiting again, which is not a silence.
    """
    out = []
    rev = reverse(tg)
    for did in sorted(g.vertices):
        if g.vertices[did].settled:
            continue
        ev = evidence(tg, did, rev)
        if ev and all(tg.tasks[t].status == "DROPPED" for t in ev):
            out.append({"id": did, "title": g.vertices[did].title,
                        "status": g.vertices[did].status, "tasks": ev})
    return out


def settled_on_dropped_evidence(tg: TaskGraph, g: Graph) -> list[dict]:
    """Settled decisions whose evidence was abandoned, all of it.

    `dropped_evidence`'s other half, and the sharper one. There the question is
    still open, so the cost is only that it reads as waiting on something that
    is never coming. Here an *answer* is standing, and the work that was to
    inform it never produced anything — the link says "the answer waits on this
    work", and the work was given up on.

    The store cannot tell which of two things happened, so the message names
    both. Either the question turned out to be settleable without the spike, in
    which case the link is vestigial and should go; or the answer rests on
    evidence that never arrived, in which case it is owed a re-examination.
    Both are cheap; the silence is not.

    Only when *every* evidence task was dropped, matching the unsettled half:
    one surviving spike and the answer may yet be backed.
    """
    out = []
    rev = reverse(tg)
    for did in sorted(g.vertices):
        if not g.vertices[did].settled:
            continue
        ev = evidence(tg, did, rev)
        if ev and all(tg.tasks[t].status == "DROPPED" for t in ev):
            out.append({"id": did, "title": g.vertices[did].title,
                        "status": g.vertices[did].status, "tasks": ev})
    return out


def _stalled_evidence(tg: TaskGraph, g: Graph, *, settled: bool) -> list[dict]:
    """Decisions whose evidence has all stopped, with some of it recoverable.

    `parked_holding_work` across the seam. Inside the task store, work parked
    while something waits on it is reported until somebody acts; a decision
    waiting on a spike is held up the same way, and `unblocks` cannot see it
    because the thing waiting is in the other store.

    The partition against the dropped pair is exact and deliberate: they fire
    where *every* evidence task was abandoned, this one where every task has
    stopped and at least one was parked. Nothing satisfies both, and the mixed
    case — one spike dropped, one parked — used to satisfy neither, which was
    the sharpest version of the silence: the decision reads as waiting on work
    that exists, is unfinished, and is not being done.

    Parked rather than dropped is the whole difference in the remedy. Abandoned
    evidence is never coming, so the question is whether to settle without it.
    Stalled evidence *can* be picked up, so the question is whether anyone
    means to.
    """
    out = []
    rev = reverse(tg)
    for did in sorted(g.vertices):
        if g.vertices[did].settled is not settled:
            continue
        ev = evidence(tg, did, rev)
        stopped = [tg.tasks[t].status for t in ev]
        if ev and all(k in ("PARKED", "DROPPED") for k in stopped) \
                and "PARKED" in stopped:
            out.append({"id": did, "title": g.vertices[did].title,
                        "status": g.vertices[did].status, "tasks": ev,
                        "parked": [t for t in ev if tg.tasks[t].parked]})
    return out


def stalled_evidence(tg: TaskGraph, g: Graph) -> list[dict]:
    """Unsettled decisions waiting on evidence nobody is producing."""
    return _stalled_evidence(tg, g, settled=False)


def settled_on_stalled_evidence(tg: TaskGraph, g: Graph) -> list[dict]:
    """Settled decisions whose evidence stopped before it arrived."""
    return _stalled_evidence(tg, g, settled=True)


def deciding_ahead_of_evidence(tg: TaskGraph | None, did: str) -> str | None:
    """What to say when a decision is settled while its evidence is still out.

    One helper, called by `dg decide` and by the browser's compose path, so
    the two doors onto the same act cannot say different things about it — the
    shape `dg task drop` and its `Drop it` button got wrong.

    A **warning and never a refusal**. Deciding ahead of a spike is a
    legitimate call: the answer may be obvious without it, or the spike may be
    confirmatory. What is not legitimate is nobody being told, and nobody
    being told again afterwards — which is what `evidence_after_deciding`
    covers once the answer is in the store.

    `None` where there is no task store, and where the decision has no
    outstanding evidence: silence is the right answer to a decision nothing
    was measuring.
    """
    if tg is None:
        return None
    waiting = pending_evidence(tg, did)
    if not waiting:
        return None
    return (f"{did} is being settled while {', '.join(waiting)} "
            f"{'is' if len(waiting) == 1 else 'are'} still outstanding — "
            f"that is a legitimate call, but read the result against this "
            f"answer when it lands. `dg check` goes on saying so until the "
            f"work finishes before the answer, the link goes, or the "
            f"decision is reopened.")


def _one_line(text: str | None, limit: int = 90) -> str:
    """An outcome as one line, clipped. Local rather than `compact.gist`: this
    module is a validator, and a validator that imports a rendering module has
    a dependency it cannot justify to `tests/test_cross.py`."""
    one = " ".join((text or "").split())
    return one if len(one) <= limit else one[:limit - 1].rstrip() + "…"


def evidence_after_deciding(tg: TaskGraph, g: Graph) -> list[dict]:
    """Settled decisions whose evidence is still running, or landed afterwards.

    The fourth quarter of the settled half, and the only one where the
    measurement *exists*. `settled_on_dropped_evidence` and
    `settled_on_stalled_evidence` between them cover evidence that was
    abandoned or put down — the cases where nothing was produced. The case
    where something **was** produced, after the answer was already written
    down, was reported by nothing at all: an answer settled ahead of its spike
    is a legitimate call, but the result may contradict it, and nobody reading
    the store six weeks later is told to look.

    Two shapes, one finding:

    *still coming* — an evidence task that is unfinished. The answer is
    standing while the work that was to inform it is in flight.

    *landed late* — an evidence task DONE after the active edge's date. The
    outcome exists and has never been read against the answer, which is why
    the message carries the outcome and not just the task id.

    The partition against the other two is by decision, not by task: where
    either of them already fires, this one is silent, so every
    (evidence-status × relative-date) cell has exactly one owner.

    The date it measures against is **the last time this evidence was read
    against this answer**, not the answer's own date. Those are the same until
    somebody reads it, so an unread result behaves exactly as before; the
    difference is that reading it is now something the store can be told, by
    `dg confirm <d> --against <t>`. Without that the finding had no honest
    exit at all in its commonest case — evidence that *confirms* the answer —
    since `reopen` asserts a doubt that is not there and `unlink` deletes the
    measurement from the record.

    That baseline is per evidence task, not per decision: reading one spike
    says nothing about another, so a decision with two late results and one
    reading goes on naming the other one.

    `Task.readings` is not the field this codebase refuses to keep — see
    `tasks.Reading`, which argues it. The short form: a later result post-dates
    the reading and the finding returns by itself, which is what separates a
    dated record of an act from a flag that silences a check.

    It also still clears when the decision is reopened (it is no longer
    settled), when the link goes (`dg task unlink`), or when a later answer
    post-dates the work.
    """
    covered = {u["id"] for u in settled_on_dropped_evidence(tg, g)} \
        | {u["id"] for u in settled_on_stalled_evidence(tg, g)}
    out = []
    rev = reverse(tg)
    by_src = g.by_src()
    for did in sorted(g.vertices):
        v = g.vertices[did]
        if not v.settled or did in covered:
            continue
        e = g.active_edge(did, by_src)
        when = e.date if e else None
        late = []
        for tid in evidence(tg, did, rev):
            t = tg.tasks[tid]
            if t.unfinished:
                # No baseline applies: there is nothing to read yet, so a
                # reading cannot have covered it.
                late.append({"id": tid, "status": t.status, "outcome": None})
                continue
            # `when` still gates: a settled vertex with no active edge has no
            # answer date to be late against, which is what it meant before a
            # reading could move the baseline.
            read = t.read_against(did)
            base = max(when, read) if (when and read) else when
            if t.status == "DONE" and when and t.done and t.done > base:
                late.append({"id": tid, "status": "DONE", "read": read,
                             "outcome": t.outcome, "done": t.done})
        if late:
            out.append({"id": did, "title": v.title, "status": v.status,
                        "date": when, "tasks": late})
    return out


def late_evidence(tg: TaskGraph, g: Graph, did: str) -> list[dict]:
    """The finished evidence for `did` that has never been read against it.

    `evidence_after_deciding` narrowed to one decision and to work that
    actually produced something — evidence still running has nothing to read.
    One helper, so `dg confirm --against`, the browser's control, and the check
    itself cannot disagree about who is outstanding.

    **Ask `late_evidence_all` if you want this for every decision.** This form
    computes the whole answer and returns one row of it, so a caller looping
    over the graph pays for the whole graph once per vertex.
    """
    return late_evidence_all(tg, g).get(did, [])


def late_evidence_all(tg: TaskGraph, g: Graph) -> dict[str, list[dict]]:
    """`late_evidence` for every decision, from one pass.

    `evidence_after_deciding` already computes every decision's answer on its
    way to any one of them — the same shape `Graph.depths` has, and the same
    trap: the single-vertex form throws the rest away, so the joined view's
    payload was paying for the whole cross-graph walk once per vertex. At 2,000
    decisions that was 364 ms per vertex, about twelve minutes for the page.
    """
    return {u["id"]: [t for t in u["tasks"] if t["outcome"] is not None]
            for u in evidence_after_deciding(tg, g)}


def _union_edges(tg: TaskGraph, g: Graph) -> dict[str, list[str]]:
    """The two graphs as one digraph, for cycle detection.

    Four edge kinds, all meaning "the head waits on the tail":
    decision→decision (a premise opens a question), task→task (a prerequisite),
    decision→task (`because`: work waits on the answer), and task→decision
    (`evidence_for`: the answer waits on the work).

    Note the last kind is what makes cross-graph cycles possible at all. With
    `because` alone every cycle would have to lie wholly inside one graph, and
    both graphs already prove themselves acyclic.
    """
    adj: dict[str, list[str]] = {}
    # `unblocks` is the `precedes` edges only, which is what this union wants:
    # every kind here means "the head waits on the tail", and a `prompted` edge
    # means no such thing. Feeding it in would manufacture deadlocks out of
    # provenance — see `KINDS` in `dgraph/tasks.py`.
    # One grouping each, rather than a scan of the edge list per node: this
    # builds a node for *every* vertex and *every* task, so the unindexed form
    # was two full scans per node across both stores.
    by_src = g.by_src()
    tadj = tg._adjacency()
    for vid in g.vertices:
        # `children` and `unblocks` both drop ids naming nothing, so a dangling
        # reference cannot enter the union and be walked into as a node.
        adj.setdefault(vid, []).extend(g.children(vid, by_src))
    for tid in tg.tasks:
        adj.setdefault(tid, []).extend(tg.unblocks(tid, tadj))
    for tid, t in tg.tasks.items():
        for did in t.because:
            if did in g.vertices:
                adj.setdefault(did, []).append(tid)
        if t.evidence_for and t.evidence_for in g.vertices:
            adj.setdefault(tid, []).append(t.evidence_for)
    # Sorted, so the walk depends on what the graphs *are* and not on the order
    # rows happen to sit in the two files. Where several cycles overlap, which
    # one is reported is still whichever the DFS meets first — deterministic
    # now, but a store holding more than one deadlock may see the report change
    # as the graphs grow. The apply guard treats an unrecognised finding as new
    # and refuses, which is the safe direction.
    return {node: sorted(heads) for node, heads in adj.items()}


def _cycles(adj: dict[str, list[str]]) -> list[list[str]]:
    """Iterative DFS colouring, for the reason both validators are iterative:
    a deep but legal graph must not crash the check that guards it."""
    colour: dict[str, int] = {}
    found: list[list[str]] = []
    for start in sorted(adj):
        if colour.get(start):
            continue
        colour[start] = 1
        stack = [(start, iter(adj.get(start, ())))]
        trail = [start]
        while stack:
            node, nxt = stack[-1]
            for c in nxt:
                if colour.get(c) == 1:
                    found.append(cycle_from(trail, c))
                    continue
                if colour.get(c) == 2:
                    continue
                colour[c] = 1
                stack.append((c, iter(adj.get(c, ()))))
                trail.append(c)
                break
            else:
                colour[node] = 2
                stack.pop()
                trail.pop()
    return found


# ---- the apply-time guard -------------------------------------------------
#
# `validate` above is what `dg check` *reads*. What follows is how a write is
# refused: both apply paths pass one of these to `apply_all`, so a batch that
# would leave the join invalid aborts with nothing written. Without it the
# blocking half of `validate` is only ever discovered after the fact, by which
# point the store is written, the commit gate denies every commit in the
# repository, and no `dg` command can undo the link that caused it.
#
# Only blocking findings refuse (`apply_all` filters), so relaxing this later
# means demoting `link_resolves` / `link_acyclic` to warnings in `validate` —
# one severity argument, no change here or in either staging module.


def _root():
    from dgraph import project
    return project.find()


def _stored_tasks() -> TaskGraph | None:
    """The task store as it is on disk, or None if there is not one to read.

    Unreadable counts as absent — meaning "no cross-graph guard" — because an
    apply must not be held up by a second store that is already broken in its
    own right. `check.run` reports that separately, and refusing here would
    leave no way to repair anything.
    """
    proj = _root()
    if not proj.has_tasks:
        return None
    try:
        return TaskGraph.load(proj.tasks)
    except Exception:
        return None


def _stored_decisions() -> Graph | None:
    """The decision store as it is on disk. The mirror of `_stored_tasks`."""
    proj = _root()
    if not proj.has_decisions:
        return None
    try:
        return Graph.load(proj.store)
    except Exception:
        return None


def _staged_task_ops() -> list[dict]:
    from dgraph import pending, task_pending
    try:
        return pending.load(task_pending.path())
    except Exception:
        return []


def _staged_decision_ops() -> list[dict]:
    from dgraph import pending
    try:
        return pending.load(_root().pending)
    except Exception:
        return []


#: **The trays and nothing else**, and specifically not `.dgraph-incoming.json`.
#: Reading the quarantine here was the obvious way to give integration the
#: other half of an arriving contribution, and it is the wrong one twice over.
#: `guard_pair` below has both proposed graphs in hand, which is a better
#: answer than a file — it judges what the replay actually produced rather
#: than what a list of ops is expected to produce. And an *ordinary* apply
#: while a contribution is quarantined would then be validated against ops
#: nobody has accepted, which is precisely the failure `tasks_after` exists to
#: prevent, arriving from the other direction.


def guard_pair() -> Callable[[Graph, TaskGraph], list[Violation]] | None:
    """Judge a proposed **pair** of graphs, reporting only what it introduces.

    `guard_decisions` and `guard_tasks` each hold one side fixed, which is
    right for an apply: the two trays are deliberately independent, and either
    can be refused while the other is written.

    Integration is the caller for which that is wrong. A contribution is
    atomic across the stores — `because` and `evidence_for` hold a bare `D`-id
    in the other file and both link invariants are blocking — so each half has
    to be judged against what the other half **will hold**, not against what
    its store holds now. An arriving `add_task T50 --because D50` whose `D50`
    arrives in the same contribution is otherwise refused for an inconsistency
    the contribution does not have: a false refusal produced by arrival order
    rather than by a conflict, which is the integration twin of `F-F1`.

    Introduced findings only, for the reason `guard_decisions` gives at
    length: a store that is already invalid must stay repairable.
    """
    stored_tg, stored_g = _stored_tasks(), _stored_decisions()
    if stored_tg is None or stored_g is None:
        return None
    before = _seen(validate(stored_tg, stored_g))
    return lambda g, tg: [v for v in validate(tg, g) if str(v) not in before]


def _seen(problems: list[Violation]) -> set[str]:
    """Findings by their text, for the "no worse than before" comparison."""
    return {str(v) for v in problems}


def _tasks_guard(g: Graph, stored_tg: TaskGraph) -> Callable[[TaskGraph], list[Violation]]:
    """Judge a proposed task graph against a fixed decision graph, reporting
    only what it introduces. The shared body of `guard_tasks` and of the
    appliability test in `tasks_after`."""
    before = _seen(validate(stored_tg, g))
    return lambda tg: [v for v in validate(tg, g) if str(v) not in before]


def tasks_after(stored_tg: TaskGraph, g: Graph) -> TaskGraph:
    """The task graph as it will stand once this apply is over.

    The staged task ops are included **only if they would actually apply**
    against the decision graph `g` that is being proposed. That condition is
    the whole finding this function exists to close.

    Judging a decision batch against merely *previewed* task ops is right when
    those ops land, and `dg apply` writes both batches, so most of the time
    they do. But the two batches are deliberately independent — "a task batch
    that will not apply must never stop a decision batch that would" — so
    either can be refused while the other is written. When the task batch was
    refused, the decision batch that landed had been validated against a task
    graph that never existed, and the store is left holding a blocking
    `link_acyclic` or `link_resolves` that no `dg` command produced: `dg check`
    reports it, the commit gate denies every commit in the repository, and the
    only exit is a hand-edit.

    So the ops are put through the same `apply_all` that will judge them for
    real — its own store's invariants *and* the cross-graph guard against `g` —
    and on any refusal this falls back to the stored task graph, which is what
    the store will actually hold. `preview` alone is not enough: it applies ops
    without validating, which is precisely the difference between a batch that
    parses and a batch that lands.
    """
    ops = _staged_task_ops()
    if not ops:
        return stored_tg
    from dgraph import task_pending
    try:
        return task_pending.apply_all(stored_tg, ops, _tasks_guard(g, stored_tg))
    except Exception:
        return stored_tg


def decisions_after(stored_g: Graph) -> Graph:
    """The decision graph as it will stand once this apply is over.

    The mirror of `tasks_after`, and needed in exactly one place: the
    stage-time warning, which asks "would `dg apply` refuse this task batch?"
    *before* anything has run. At apply time the question answers itself —
    decisions are written first, so the file already holds a batch that landed
    and correctly omits one that did not — but at staging time the decision
    batch is still in its tray, and judging against the file alone would warn
    that a link is dangling when the very next command is about to create the
    decision it names.
    """
    ops = _staged_decision_ops()
    if not ops:
        return stored_g
    from dgraph import pending
    try:
        return pending.apply_all(stored_g, ops, guard_decisions())
    except Exception:
        return stored_g


def guard_decisions() -> Callable[[Graph], list[Violation]] | None:
    """The `also` argument for `pending.apply_all`: judge a proposed decision
    graph against the work resting on it.

    Reports only what the batch *introduces*. A store that is already invalid
    must stay repairable: `dg` is the only way out this tool offers, and a
    guard that refused every write while a pre-existing cycle sat in the store
    would freeze both graphs and leave hand-editing as the sole exit — the
    thing the guard exists to make unnecessary. `dg check` keeps reporting the
    pre-existing finding the whole time. That rule is load-bearing well beyond
    convenience: it is what keeps every state reachable through these guards
    recoverable rather than terminal.

    The baseline is the project as it stands on disk — both stores, neither
    tray — so a finding either batch introduces is attributed to the batch that
    introduces it rather than to whichever one happens to apply first.
    """
    stored_tg = _stored_tasks()
    if stored_tg is None:
        return None
    stored_g = _stored_decisions() or Graph()
    before = _seen(validate(stored_tg, stored_g))
    return lambda g: [v for v in validate(tasks_after(stored_tg, g), g)
                      if str(v) not in before]


def guard_tasks(*, staged_decisions: bool = False
                ) -> Callable[[TaskGraph], list[Violation]] | None:
    """The `also` argument for `task_pending.apply_all`: judge a proposed task
    graph against the decisions it points at. Introduced findings only, for the
    reason `guard_decisions` gives.

    Against the decision store **as it is on disk**, not against its staged
    ops, and that is not the asymmetry with `guard_decisions` it looks like.
    Decisions are written first, so by the time a task batch is judged the
    store already holds a decision batch that landed — and correctly does not
    hold one that was refused. Reading the file is therefore both the more
    current answer and the honest one; consulting the decision tray instead
    would mean judging this batch against decisions that may never exist,
    which is how a task op linking to a refused `add_vertex` came to be
    written and to deny every commit in the repository afterwards.

    `staged_decisions` is for the one caller asking the question early — the
    stage-time warning, which runs before either batch has. See
    `decisions_after`.
    """
    g = _stored_decisions()
    if g is None:
        return None
    if staged_decisions:
        g = decisions_after(g)
    return _tasks_guard(g, _stored_tasks() or TaskGraph())


def validate(tg: TaskGraph, g: Graph) -> list[Violation]:
    """The invariants that need both stores.

    **Severity rule: anything a reopen can cause is a warning, never an error.**
    If resting on a reopened decision were blocking, `dg reopen D02` would
    invalidate the store outright and the commit gate would deny every commit in
    the repository until somebody triaged the backlog — and the first thing
    anyone would do is switch the check off. Decisions must never be held
    hostage by tasks.

    That is the deliberate asymmetry with the decisions-only `propagation`
    check, which *is* an error: there the tool derives the remedy, so the error
    is never hit by accident. Here blocked-ness is not stored, so there is no
    remedial op to derive — the derived view is the remedy.
    """
    v: list[Violation] = []

    for tid in sorted(tg.tasks):
        t = tg.tasks[tid]
        for did in t.because:
            if did not in g.vertices:
                v.append(Violation(
                    "link_resolves",
                    f"{tid}: because names unknown decision {did}",
                ))
        if t.evidence_for and t.evidence_for not in g.vertices:
            v.append(Violation(
                "link_resolves",
                f"{tid}: evidence_for names unknown decision {t.evidence_for}",
            ))
        if t.evidence_for and t.evidence_for in t.because:
            v.append(Violation(
                "link_acyclic",
                f"{tid}: because and evidence_for both name {t.evidence_for} — a "
                f"task cannot rest on the answer it exists to produce. Split "
                f"the decision: one question settled cheaply now, another "
                f"settled by this evidence.",
            ))

    for cyc in _cycles(_union_edges(tg, g)):
        # A deadlock: every node in the loop waits on another node in it. The
        # situation it represents ("we must build it to decide it") is real,
        # but it means the decision is at the wrong grain, so the message names
        # the way out rather than only refusing.
        if len({c for c in cyc if c in tg.tasks}) and \
                len({c for c in cyc if c in g.vertices}):
            v.append(Violation(
                "link_acyclic",
                f"cycle across the graphs: {' -> '.join(cyc)} — nothing in "
                f"this loop can start. Split the decision into one you can "
                f"settle now and one the evidence settles, or break the task "
                f"dependency.",
            ))

    for u in unharvested(tg, g):
        v.append(Violation(
            "evidence_unharvested",
            f"{u['id']} is DONE and was to inform {u['decision']} "
            f"({u['decision_title']}), which is still unsettled — record what "
            f"it showed with `dg decide {u['decision']}`, or drop the link",
            "warning",
        ))

    for u in dropped_evidence(tg, g):
        v.append(Violation(
            "evidence_dropped",
            f"{u['id']} ({u['title']}) is {u['status']} and every task meant "
            f"to inform it was abandoned ({', '.join(u['tasks'])}) — no "
            f"evidence is coming. Settle it on what is already known with "
            f"`dg decide {u['id']}`, plan new evidence, or drop the link",
            "warning",
        ))

    for u in settled_on_dropped_evidence(tg, g):
        v.append(Violation(
            "evidence_dropped_after_deciding",
            f"{u['id']} ({u['title']}) is {u['status']}, but every task meant "
            f"to inform it was abandoned ({', '.join(u['tasks'])}) — the "
            f"answer stands on evidence that never arrived. Either it was "
            f"settled without them, so drop the link with `dg task unlink "
            f"{u['tasks'][0]}`, or it is owed a re-examination with `dg reopen "
            f"{u['id']}`",
            "warning",
        ))

    for u in stalled_evidence(tg, g):
        v.append(Violation(
            "evidence_stalled",
            f"{u['id']} ({u['title']}) is {u['status']} and waits on evidence "
            f"nobody is producing — {', '.join(u['parked'])} "
            f"{'is' if len(u['parked']) == 1 else 'are'} parked and no other "
            f"task meant to inform it is still going. Pick "
            f"{'it' if len(u['parked']) == 1 else 'one'} up, settle the "
            f"question on what is already known with `dg decide {u['id']}`, or "
            f"drop the link",
            "warning",
        ))

    for u in settled_on_stalled_evidence(tg, g):
        v.append(Violation(
            "evidence_stalled_after_deciding",
            f"{u['id']} ({u['title']}) is {u['status']}, but the work meant to "
            f"inform it stopped before it arrived ({', '.join(u['parked'])} "
            f"parked) — the answer stands on evidence that was never produced. "
            f"Either it was settled without them, so drop the link with "
            f"`dg task unlink {u['tasks'][0]}`, or somebody meant to finish "
            f"the work and it is still worth picking up",
            "warning",
        ))

    for u in evidence_after_deciding(tg, g):
        # The outcome, not just the id: the outcome is the thing that has to
        # be read against the answer, and an id sends the reader off to look
        # it up before they know whether it is worth looking up.
        what = ", ".join(
            f"{t['id']} is {t['status'].lower()}" if t["outcome"] is None
            else f"{t['id']} finished {t['done']} — {_one_line(t['outcome'])}"
            for t in u["tasks"])
        running = any(t["outcome"] is None for t in u["tasks"])
        # Every late task, not the first one: the advice used to name
        # `tasks[0]` while the sentence above it listed two, so half the
        # finding had no remedy attached to it.
        ids = ",".join(t["id"] for t in u["tasks"])
        v.append(Violation(
            "evidence_after_deciding",
            f"{u['id']} ({u['title']}) is {u['status']}"
            + (f", settled {u['date']}" if u["date"] else "")
            + f", but the work meant to inform it "
            + ("has not reported yet" if running else "reported afterwards")
            + f" ({what}) — read it against the answer, then `dg confirm "
              f"{u['id']} --against {ids}` if it still holds, `dg reopen "
              f"{u['id']}` if it does not, or `dg task unlink {ids} "
              f"--evidence-for` if the answer never needed it",
            "warning",
        ))

    for u in under_review(tg, g):
        v.append(Violation(
            "link_premise_under_review",
            f"{u['id']} is {u['status']} and exists because of {u['because']}, "
            f"which is {u['premise_status']} — the reason for this work is "
            f"under review; re-check it before carrying on",
            "warning",
        ))
    return v
