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

from dgraph.model import Graph
from dgraph.tasks import TaskGraph
from dgraph.violation import Violation


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
    for vid in g.vertices:
        adj.setdefault(vid, []).extend(
            c for c in g.children(vid) if c in g.vertices)
    for tid in tg.tasks:
        adj.setdefault(tid, []).extend(tg.unblocks(tid))
    for tid, t in tg.tasks.items():
        if t.because and t.because in g.vertices:
            adj.setdefault(t.because, []).append(tid)
        if t.evidence_for and t.evidence_for in g.vertices:
            adj.setdefault(tid, []).append(t.evidence_for)
    return adj


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
                    found.append(trail[trail.index(c):] + [c])
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

    for u in under_review(tg, g):
        v.append(Violation(
            "link_premise_under_review",
            f"{u['id']} is {u['status']} and exists because of {u['because']}, "
            f"which is {u['premise_status']} — the reason for this work is "
            f"under review; re-check it before carrying on",
            "warning",
        ))
    return v
