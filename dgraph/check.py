"""The one place a graph is judged valid.

`Graph.validate()` covers invariants internal to the graph. This adds the ones
that need the project on disk — chiefly whether the rendered view still matches
the store — and is what `dg check` and the pytest plugin both call, so there is
no second opinion about what "valid" means.
"""

from __future__ import annotations

from dgraph import project as _project
from dgraph import render
from dgraph.model import Graph, Violation
from dgraph.violation import DECISION, LINK, TASK, tag

#: Every check this tool can emit. Parametrising over this rather than over
#: observed violations means a clean graph still runs one test per rule, so a
#: check that silently stops firing is visible.
CHECKS: tuple[str, ...] = (
    "store_loads",
    "ids_wellformed",
    "status_legal",
    "no_dangling_refs",
    "one_active_edge",
    "decided_complete",
    "rejected_complete",
    "open_not_overspecified",
    "propagation",
    "stale_provisional",
    "stale_block",
    "block_is_a_premise",
    "no_orphans",
    "acyclic",
    "stale_view",
    # the task store; absent in most projects, checked only when present
    "task_ids_wellformed",
    "task_area_known",
    "task_status_legal",
    "task_no_dangling_refs",
    "task_edge_kind",
    "task_acyclic",
    "task_done_complete",
    "task_drop_complete",
    "task_park_complete",
    "task_reading_complete",
    "task_reading_stale",
    "parked_holding_work",
    "task_done_before_prerequisite",
    "released_by_drop",
    "orphaned_by_drop",
    "stale_task_view",
    # the relation between the two stores; checked only when both exist
    "link_resolves",
    "link_premise_under_review",
    "link_acyclic",
    "evidence_unharvested",
    "evidence_dropped",
    "evidence_dropped_after_deciding",
    "evidence_stalled",
    "evidence_stalled_after_deciding",
    "evidence_after_deciding",
)

#: Which store each rule belongs to, declared rather than read off the name.
#:
#: The runtime origin does not come from here — `_decisions`, `_tasks` and
#: `_link` stamp what they return, which is structural and cannot go stale.
#: This table is the second belt: `run` uses it for anything that reaches it
#: unstamped, and a test asserts every entry agrees with the module that
#: actually emits the name. The prefix convention it replaces was already a
#: lie for nine of these names.
#:
#: `store_loads` is emitted by all three helpers and so has no fixed entry;
#: it is always stamped at the point of emission.
ORIGIN: dict[str, str] = {
    "store_loads": "",
    "ids_wellformed": DECISION,
    "status_legal": DECISION,
    "no_dangling_refs": DECISION,
    "one_active_edge": DECISION,
    "decided_complete": DECISION,
    "rejected_complete": DECISION,
    "open_not_overspecified": DECISION,
    "propagation": DECISION,
    "stale_provisional": DECISION,
    "stale_block": DECISION,
    "block_is_a_premise": DECISION,
    "no_orphans": DECISION,
    "acyclic": DECISION,
    "stale_view": DECISION,
    "task_ids_wellformed": TASK,
    "task_area_known": TASK,
    "task_status_legal": TASK,
    "task_no_dangling_refs": TASK,
    "task_edge_kind": TASK,
    "task_acyclic": TASK,
    "task_done_complete": TASK,
    "task_drop_complete": TASK,
    "task_park_complete": TASK,
    "task_reading_complete": TASK,
    "task_reading_stale": TASK,
    "parked_holding_work": TASK,
    "task_done_before_prerequisite": TASK,
    "released_by_drop": TASK,
    "orphaned_by_drop": TASK,
    "stale_task_view": TASK,
    "link_resolves": LINK,
    "link_premise_under_review": LINK,
    "link_acyclic": LINK,
    "evidence_unharvested": LINK,
    "evidence_dropped": LINK,
    "evidence_dropped_after_deciding": LINK,
    "evidence_stalled": LINK,
    "evidence_stalled_after_deciding": LINK,
    "evidence_after_deciding": LINK,
}


def run(proj: _project.Project | None = None) -> list[Violation]:
    """Every finding for this project, across every store it has.

    One list, whichever stores exist: `dgraph/testing.py` parametrises over
    `CHECKS`, so keeping the names in one namespace means a project gets one
    pytest test per rule with no second list to drift out of sync — the failure
    mode `stale_block` nearly hit.
    """
    proj = proj or _project.find()

    if not proj.exists:
        # No store at all, so no store to blame: left unstamped, which the
        # gate reads as "the graphs this project keeps".
        return [Violation(
            "store_loads",
            f"no {proj.store.name} or {proj.tasks.name} under {proj.root}",
        )]

    # Both stores read under one lock, because the link between them is judged
    # here and a link is only meaningful across a matched pair. Without it this
    # can read one store from before an apply and the other from after, and
    # report a `link_resolves` that never existed in any state anyone approved —
    # which the commit gate turns into a denial for every agent in the
    # repository. `project.stores` never refuses to run the body, so a lock that
    # cannot be taken costs the coherence and not the answer. Audit F27.
    with _project.stores(proj):
        problems = _decisions(proj) if proj.has_decisions else []
        problems += _tasks(proj) if proj.has_tasks else []
        if proj.has_decisions and proj.has_tasks:
            problems += _link(proj)

    # The second belt. Everything above is stamped where it was read; this
    # catches a finding that reached here by some path that was not.
    problems = [p if p.origin is not None or not ORIGIN.get(p.check)
                else Violation(p.check, p.message, p.severity,
                               ORIGIN[p.check])
                for p in problems]

    unknown = sorted({p.check for p in problems} - set(CHECKS))
    if unknown:
        problems.append(Violation(
            "store_loads",
            f"internal: checks emitted but not declared in CHECKS: {unknown}",
        ))
    return problems


def _link(proj: _project.Project) -> list[Violation]:
    """The cross-graph invariants, when the project has both stores.

    Silent if either store is unreadable: `_decisions`/`_tasks` have already
    reported that as a blocking `store_loads`, and a second complaint about the
    same file helps nobody.
    """
    from dgraph import cross
    from dgraph.tasks import TaskGraph

    try:
        g = Graph.load(proj.store)
        tg = TaskGraph.load(proj.tasks)
    except Exception:
        return []
    try:
        return tag(cross.validate(tg, g), LINK)
    except Exception as exc:
        return [Violation(
            "store_loads",
            f"internal: the cross-graph check failed ({exc!r}) — the link "
            f"between {proj.store.name} and {proj.tasks.name} cannot be judged",
            origin=LINK,
        )]


def _tasks(proj: _project.Project) -> list[Violation]:
    """The task store's own invariants, plus its view staleness.

    An *absent* `tasks.json` is silence — the overwhelming majority of projects
    will never have one, and the decision graph must be unaffected by its
    absence. An *unreadable* one is a blocking violation, following the
    `store_loads` precedent: a state the tool cannot read is not a state it may
    vouch for.
    """
    from dgraph import task_render
    from dgraph.tasks import TaskGraph

    try:
        tg = TaskGraph.load(proj.tasks)
    except Exception as exc:
        return [Violation("store_loads",
                          f"{proj.tasks} could not be read: {exc}",
                          origin=TASK)]

    try:
        problems = tag(tg.validate(), TASK)
    except Exception as exc:
        return [Violation(
            "store_loads",
            f"internal: validate() itself failed on {proj.tasks.name} "
            f"({exc!r}) — the task graph cannot be judged valid",
            origin=TASK,
        )]

    if not proj.has_decisions:
        # The link fields are checked here too, against an empty decision
        # graph: every link a tasks-only project holds names a decision that
        # cannot exist, which is the same breakage `_link` reports when both
        # stores are present — and reporting it there only would make an
        # absent `decisions.json` the way to hide it.
        # Stamped LINK, not TASK: these are findings about the cross-store
        # relation wherever they are emitted from, and the remedy is the same
        # one `_link` would name.
        from dgraph import cross
        problems += tag(cross.validate(tg, Graph()), LINK)

    # A warning, for the reason `_decisions` gives at length below.
    if not proj.task_view.exists():
        problems.append(Violation(
            "stale_task_view",
            f"{proj.task_view.name} is missing — run `dg task render`",
            "warning", TASK,
        ))
    else:
        try:
            current = proj.task_view.read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(Violation(
                "stale_task_view",
                f"{proj.task_view.name} could not be read: {exc}",
                "warning", TASK,
            ))
        else:
            if current != task_render.render(tg):
                problems.append(Violation(
                    "stale_task_view",
                    f"{proj.task_view.name} does not match {proj.tasks.name}. It "
                    f"is generated — run `dg task render` rather than editing it.",
                    "warning", TASK,
                ))
    return problems


def _decisions(proj: _project.Project) -> list[Violation]:
    try:
        g = Graph.load(proj.store)
    except Exception as exc:  # malformed JSON, bad shape
        return [Violation("store_loads",
                          f"{proj.store} could not be read: {exc}",
                          origin=DECISION)]

    try:
        problems = tag(g.validate(), DECISION)
    except Exception as exc:
        # A validator bug must degrade to a violation, never to a crash: the
        # commit gate treats a crash as "no verdict" and would fail open on
        # exactly the store damage validation exists to catch.
        return [Violation(
            "store_loads",
            f"internal: validate() itself failed on {proj.store.name} "
            f"({exc!r}) — the graph cannot be judged valid",
            origin=DECISION,
        )]

    # **A warning, not an error**, and the severity is the whole argument.
    #
    # The view is generated. Nothing is lost when it lags: `dg render` rebuilds
    # it from the store, and every `dg apply` already does, before it writes the
    # store at all. So a stale view is transient by construction — it survives
    # until the next apply, at which point the corrected file is sitting in the
    # worktree waiting to be committed with everything else.
    #
    # Blocking made that transient state deny **every commit in the repository**,
    # since `check.run` is repo-global: a commit touching only `src/` was refused
    # over a generated file it had nothing to do with, and frequently the person
    # refused was not the one who caused it — a merge or a checkout moves the
    # store and the view independently. Disproportionate for something one
    # mechanical command fixes and the next apply fixes by itself.
    #
    # It stays *reported*, which is the part worth keeping: `dg check` lists it,
    # `dg brief` carries it into every session, the pytest plugin raises it in
    # the warning summary, and the commit gate says so — as a `warn`, which
    # `dgraph/gate.py` added for exactly this. What changed is that none of them
    # refuses any more.
    #
    # Safe to demote because this is not a store invariant: it lives here and
    # never in `Graph.validate`, so no write path consults it and `apply_all`
    # cannot be relaxed by this change.
    if not proj.view.exists():
        problems.append(Violation(
            "stale_view", f"{proj.view.name} is missing — run `dg render`",
            "warning", DECISION,
        ))
    else:
        try:
            current = proj.view.read_text(encoding="utf-8")
        except OSError as exc:
            # The store's load is guarded above; the view's read must be too,
            # or an unreadable view crashes every caller that promised not to
            # crash — `dg brief` and the session hook chief among them.
            current = None
            problems.append(Violation(
                "stale_view", f"{proj.view.name} could not be read: {exc}",
                "warning", DECISION,
            ))
        if current is not None and current != render.render(g):
            problems.append(Violation(
                "stale_view",
                f"{proj.view.name} does not match {proj.store.name}. It is "
                f"generated — run `dg render` rather than editing it.",
                "warning", DECISION,
            ))
    return problems


def errors(proj: _project.Project | None = None) -> list[Violation]:
    return [p for p in run(proj) if p.blocking]
