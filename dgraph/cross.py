"""Where the two graphs meet, and the only module that may see both.

`dgraph/model.py` does not know what a task is; `dgraph/tasks.py` does not know
what a decision is. Neither can import the other, and a test enforces it. That
mutual ignorance is what makes the barrier structural rather than a convention
somebody has to remember — so everything that needs both stores lives here, and
`cross.py` appearing in a second module's imports is visible in a diff.

The link is stored on the task and derived in the other direction, which is the
codebase's own rule ("dependency is the graph structure, never a stored field")
applied by cardinality: a task has at most one `because`, a decision has
unboundedly many dependents, so the "one" side is the stored side. The practical
consequence is that `decisions.json` never mentions a task, so a change to it
always means a decision changed — and the one artifact whose git history is
supposed to be readable stays readable.
"""

from __future__ import annotations

from collections.abc import Callable

from dgraph.model import Graph
from dgraph.tasks import TaskGraph
from dgraph.violation import Violation, cycle_from


def rests_on(tg: TaskGraph, did: str) -> list[str]:
    """The tasks that exist because of this decision. The derived reverse."""
    return sorted(t.id for t in tg.tasks.values() if t.because == did)


def evidence(tg: TaskGraph, did: str) -> list[str]:
    """The tasks whose outcome bears on this decision. The derived reverse."""
    return sorted(t.id for t in tg.tasks.values() if t.evidence_for == did)


def pending_evidence(tg: TaskGraph, did: str) -> list[str]:
    """Evidence tasks not finished yet — what an open decision is waiting on."""
    return [t for t in evidence(tg, did) if tg.tasks[t].unfinished]


def gated_by(tg: TaskGraph, g: Graph, tid: str) -> str | None:
    """The decision this task waits on, if it is waiting on one.

    `None` when the task names no premise *or* the premise is already settled —
    the question every caller actually has is "is this work startable?", not
    "does this link exist". A `because` naming an unknown decision is skipped
    rather than returned, for the reason `Graph.depends` documents: a traversal
    helper that trusts ids crashes the validator that was about to report the
    dangling reference.
    """
    because = tg.tasks[tid].because
    if not because or because not in g.vertices:
        return None
    return None if g.vertices[because].settled else because


def ready(tg: TaskGraph, g: Graph, tid: str) -> bool:
    """Startable for real: prerequisites resolved *and* premise settled."""
    return tg.ready(tid) and gated_by(tg, g, tid) is None


def task_link(tg: TaskGraph, g: Graph, tid: str) -> dict:
    """Everything the cross-graph link says about one task, in one reading.

    `dg context` needs all of it at once — the premise, whether that premise
    resolves, whether it gates the work — and assembling it from the task's own
    fields is exactly the second implementation of the rule this module exists
    to prevent. Callers get the ids from here and never read `Task.because`
    themselves.

    `premise` is the `because` decision *that exists*; `dangling` says the link
    names one that does not, which is a different fact from having no link and
    is what `dg check` will report.
    """
    t = tg.tasks[tid]
    resolves = bool(t.because) and t.because in g.vertices
    return {
        "because": t.because,
        "evidence_for": t.evidence_for,
        "premise": t.because if resolves else None,
        "dangling": bool(t.because) and not resolves,
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
            waiting = {vid for vid in g.frontier() if pending_evidence(tg, vid)}
            preds["decidable"] = lambda vid: (
                not g.vertices[vid].settled and not g.waiting_on(vid)
                and vid not in waiting)
            preds["implemented"] = lambda vid: bool(rests_on(tg, vid))
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
        struct["because"] = lambda did: set(rests_on(tg, did))
        struct["evidence"] = lambda did: set(evidence(tg, did))
        if g is not None:
            preds["ready"] = lambda tid: ready(tg, g, tid)
            preds["gated"] = lambda tid: gated_by(tg, g, tid) is not None
            loose = {d["task"] for d in unharvested(tg, g)}
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
                  if t.because in wanted and t.unfinished)


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
        because = tg.tasks[tid].because
        if not because or because not in g.vertices:
            continue
        premise = g.vertices[because]
        if premise.base_status in UNDER_REVIEW:
            out.append({"id": tid, "title": tg.tasks[tid].title,
                        "status": tg.tasks[tid].status,
                        "because": because, "premise_status": premise.status})
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
    for did in sorted(g.vertices):
        if g.vertices[did].settled:
            continue
        ev = evidence(tg, did)
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
    for did in sorted(g.vertices):
        if not g.vertices[did].settled:
            continue
        ev = evidence(tg, did)
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
    for did in sorted(g.vertices):
        if g.vertices[did].settled is not settled:
            continue
        ev = evidence(tg, did)
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

    Nothing here is stored. It clears when the decision is reopened (it is no
    longer settled), when the link goes (`dg task unlink`), or when a later
    answer post-dates the work — never by a field whose only job is to silence
    it, which is the rule `tasks.py` states at length for `released_by_drop`.
    """
    covered = {u["id"] for u in settled_on_dropped_evidence(tg, g)} \
        | {u["id"] for u in settled_on_stalled_evidence(tg, g)}
    out = []
    for did in sorted(g.vertices):
        v = g.vertices[did]
        if not v.settled or did in covered:
            continue
        e = g.active_edge(did)
        when = e.date if e else None
        late = []
        for tid in evidence(tg, did):
            t = tg.tasks[tid]
            if t.unfinished:
                late.append({"id": tid, "status": t.status, "outcome": None})
            elif t.status == "DONE" and when and t.done and t.done > when:
                late.append({"id": tid, "status": "DONE",
                             "outcome": t.outcome, "done": t.done})
        if late:
            out.append({"id": did, "title": v.title, "status": v.status,
                        "date": when, "tasks": late})
    return out


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
    for vid in g.vertices:
        # `children` and `unblocks` both drop ids naming nothing, so a dangling
        # reference cannot enter the union and be walked into as a node.
        adj.setdefault(vid, []).extend(g.children(vid))
    for tid in tg.tasks:
        adj.setdefault(tid, []).extend(tg.unblocks(tid))
    for tid, t in tg.tasks.items():
        if t.because and t.because in g.vertices:
            adj.setdefault(t.because, []).append(tid)
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
        for fld in ("because", "evidence_for"):
            ref = getattr(t, fld)
            if ref and ref not in g.vertices:
                v.append(Violation(
                    "link_resolves",
                    f"{tid}: {fld} names unknown decision {ref}",
                ))
        if t.because and t.because == t.evidence_for:
            v.append(Violation(
                "link_acyclic",
                f"{tid}: because and evidence_for both name {t.because} — a "
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
        v.append(Violation(
            "evidence_after_deciding",
            f"{u['id']} ({u['title']}) is {u['status']}"
            + (f", settled {u['date']}" if u["date"] else "")
            + f", but the work meant to inform it "
            + ("has not reported yet" if running else "reported afterwards")
            + f" ({what}) — read it against the answer, then `dg reopen "
              f"{u['id']}` if it does not hold, or `dg task unlink "
              f"{u['tasks'][0]['id']} --evidence-for` if the answer never "
              f"needed it",
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
