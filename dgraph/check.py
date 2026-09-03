"""The one place a graph is judged valid.

`Graph.validate()` covers invariants internal to the graph. This adds the ones
that need the project on disk — chiefly whether the rendered view still matches
the store — and is what `dg check` and the pytest plugin both call, so there is
no second opinion about what "valid" means.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dgraph import limits
from dgraph import project as _project
from dgraph import render
from dgraph.model import Graph, Violation
from dgraph.violation import DECISION, LINK, TASK, tag

if TYPE_CHECKING:  # `dgraph.tasks` is imported lazily below, where it is used
    from dgraph.tasks import TaskGraph

#: Every check this tool can emit. Parametrising over this rather than over
#: observed violations means a clean graph still runs one test per rule, so a
#: check that silently stops firing is visible.
CHECKS: tuple[str, ...] = (
    "store_loads",
    # the tray, under `--staged` only: a batch that will not preview is the
    # answer to "what would apply leave", and the answer is that it refuses
    "tray_applies",
    "ids_wellformed",
    "status_legal",
    "no_dangling_refs",
    "one_active_edge",
    "decided_complete",
    "rejected_complete",
    "open_not_overspecified",
    "propagation",
    "stale_provisional",
    "no_orphans",
    "acyclic",
    "stale_view",
    # both stores; stamped where it is emitted, like `store_loads`
    "verbose_field",
    # both stores: a field the store holds and this version cannot read,
    # carried verbatim and named. A warning, for the reason `model.unknown_field`
    # gives.
    "unknown_field",
    # the task store; absent in most projects, checked only when present
    "task_ids_wellformed",
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
    # Emitted by `_decisions` and `_tasks` alike, so it has no fixed entry
    # either: a long answer and a long outcome are the same finding about two
    # stores, and splitting the name in two would make the rule twice as easy
    # to let rot in one of them.
    "verbose_field": "",
    # As `verbose_field`: emitted by both validators, stamped by the caller.
    "unknown_field": "",
    # Either tray; stamped where it is emitted, like `store_loads`.
    "tray_applies": "",
    "ids_wellformed": DECISION,
    "status_legal": DECISION,
    "no_dangling_refs": DECISION,
    "one_active_edge": DECISION,
    "decided_complete": DECISION,
    "rejected_complete": DECISION,
    "open_not_overspecified": DECISION,
    "propagation": DECISION,
    "stale_provisional": DECISION,
    "no_orphans": DECISION,
    "acyclic": DECISION,
    "stale_view": DECISION,
    "task_ids_wellformed": TASK,
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
    return _belt(problems)


def _belt(problems: list[Violation]) -> list[Violation]:
    """The second belt, shared by `run` and `staged`."""
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


def staged(proj: _project.Project | None = None,
           ) -> tuple[list[Violation], list[Violation]]:
    """The findings as the store sits, and as `dg apply` would leave it.

    `run` answers *is the record valid*, and that reading is load-bearing: the
    commit gate, the pytest plugin and `dg brief` all take it, and none of them
    may be told a broken store is fine because the tray happens to mend it. So
    this is a second function and not a flag on the first — `D70`. It returns
    the pair so a caller can say what the tray *changes*, which is the reading
    a person wants before applying: what it fixes, what it introduces, and what
    it leaves alone. `diff` computes those three from the pair.

    The stored side is `run` with the view checks left out. A view is
    generated from the store, so it is stale against the previewed graph by
    construction, and reporting that as something the tray would introduce
    would blame every batch for a file that `dg apply` regenerates on the way.

    A tray that will not preview is reported as a blocking `tray_applies`, not
    hidden behind a fallback. `cli._reading` falls back to the store because a
    reader refusing to print is the opposite of useful; here the failed preview
    *is* the answer — `dg apply` would refuse — and the stored findings for
    that side are carried across unchanged, since that is what would remain.

    Both graphs are read under one lock with both trays, so the link is judged
    across a matched pair, as in `run`.
    """
    from dgraph import pending, task_pending
    from dgraph.tasks import TaskGraph

    proj = proj or _project.find()
    if not proj.exists:
        only = run(proj)
        return only, list(only)

    before: list[Violation] = []
    after: list[Violation] = []
    g = eg = tg = etg = None
    with _project.stores(proj):
        if proj.has_decisions:
            try:
                g = Graph.load(proj.store)
            except Exception as exc:
                v = Violation("store_loads",
                              f"{proj.store} could not be read: {exc}",
                              origin=DECISION)
                before.append(v)
                after.append(v)
            else:
                mine = _decisions(proj, g, views=False)
                before += mine
                try:
                    eg = pending.preview(g, proj.pending)
                except (pending.ApplyError, OSError, ValueError) as exc:
                    after += mine
                    after.append(_tray_applies(
                        "dg pending", "dg drop <id>", exc, DECISION))
                else:
                    after += _decisions(proj, eg, views=False)
        if proj.has_tasks:
            try:
                tg = TaskGraph.load(proj.tasks)
            except Exception as exc:
                v = Violation("store_loads",
                              f"{proj.tasks} could not be read: {exc}",
                              origin=TASK)
                before.append(v)
                after.append(v)
            else:
                mine = _tasks(proj, tg, views=False)
                before += mine
                try:
                    etg = task_pending.preview(tg, proj.task_pending)
                except (pending.ApplyError, OSError, ValueError) as exc:
                    after += mine
                    after.append(_tray_applies(
                        "dg task pending", "dg task drop-op <id>", exc, TASK))
                else:
                    after += _tasks(proj, etg, views=False)
        if g is not None and tg is not None:
            before += _link(proj, g, tg)
            # A side whose tray would not preview is judged as it sits.
            after += _link(proj, eg if eg is not None else g,
                           etg if etg is not None else tg)
    return _belt(before), _belt(after)


def _tray_applies(review: str, drop: str, exc: Exception,
                  origin: str) -> Violation:
    return Violation(
        "tray_applies",
        f"the staged ops no longer apply cleanly, so `dg apply` would refuse "
        f"them: {exc} — `{review}` to review, `{drop}` or `dg edit <id>` to fix",
        origin=origin,
    )


def diff(before: list[Violation], after: list[Violation],
         ) -> tuple[list[Violation], list[Violation], list[Violation]]:
    """`(fixed, introduced, kept)` — what the tray changes, keyed the way
    `pending.introduced` keys it, on `(check, message)`.

    `kept` is drawn from `after` rather than `before` so its origins are those
    of the graph the caller is reporting on; the two agree by construction.
    """
    key = lambda v: (v.check, v.message)  # noqa: E731
    was = {key(v) for v in before}
    now = {key(v) for v in after}
    fixed = [v for v in before if key(v) not in now]
    introduced = [v for v in after if key(v) not in was]
    kept = [v for v in after if key(v) in was]
    return fixed, introduced, kept


def _verbose(rows: list[tuple[str, dict]], origin: str) -> list[Violation]:
    """`verbose_field` for one store's records, as `(id, prose fields)` pairs.

    **A warning, and the severity is the whole argument**, for the reason
    `stale_view` gives at length below: this is not a store invariant. It lives
    here and never in either `validate`, so no write path consults it and
    `apply_all` is untouched — a long answer is a legal, representable record,
    and a graph written before this rule existed must not become uncommittable.

    Judged at `limits.TERSE_DEFAULT` rather than at whatever `$DG_TERSE` says,
    because the two doors have different readers. The refusal is aimed at an
    agent whose launcher set a number; the warning is read by a supervisor, who
    never has one set — and a check that went quiet exactly when nobody had
    configured it would be silent in every project that has not heard of this,
    which is every project the finding is actually for.

    Only the first over-long field per record, matching `limits.refuse_verbose`:
    one long record is one finding, and a listing per field would rank a single
    verbose decision above every other problem in the graph.
    """
    out = []
    for rid, fields in rows:
        found = limits.overlong(fields)
        if not found:
            continue
        name, size = found[0]
        out.append(Violation(
            "verbose_field",
            f"{rid}'s {name} is {size} characters — the store holds the "
            f"synopsis a person reads while deciding. Put the development in a "
            f"file and cite it; the chain is already edges, and "
            f"`dg context {rid}` says it.",
            "warning", origin,
        ))
    return out


def _unknown(rows: list[tuple[str, dict]], origin: str) -> list[Violation]:
    """`unknown_field` for one store's records, as `(where, extra)` pairs.

    A field the store holds and this version cannot read — `Vertex.extra`,
    `Edge.extra`, `Task.extra`, `TaskEdge.extra` — carried verbatim by the
    loader and named here. **A warning, from here and never from either
    `validate`**, for the reason `_verbose` gives: this is not a store
    invariant. The store is legal and representable, the field is kept exactly
    as written, and the reader is told there is something an install of this
    version cannot interpret. Blocking would deny every commit in a clone
    whose only fault is being a version behind, which is the failure `extra`
    exists to end; and `apply_all` must not refuse to write a field it is
    carrying for somebody else.

    One finding per field rather than per record, unlike `_verbose`: two
    unknown fields are two things a newer install means, and the remedy — run
    that install — is the same, so listing both costs nothing and hides
    nothing.
    """
    return [Violation(
        "unknown_field",
        f"{where} carries `{name}`, which this version of dg does not read — "
        f"kept as written, never dropped; an install that reads it is the "
        f"one to say what it means (`dg --version`)",
        "warning", origin)
        for where, extra in rows for name in sorted(extra)]


def _link(proj: _project.Project, g: Graph | None = None,
          tg: TaskGraph | None = None) -> list[Violation]:
    """The cross-graph invariants, when the project has both stores.

    Silent if either store is unreadable: `_decisions`/`_tasks` have already
    reported that as a blocking `store_loads`, and a second complaint about the
    same file helps nobody. `staged` passes the graphs it already holds — the
    previewed pair — and the judgement is the same.
    """
    from dgraph import cross
    from dgraph.tasks import TaskGraph

    try:
        g = g if g is not None else Graph.load(proj.store)
        tg = tg if tg is not None else TaskGraph.load(proj.tasks)
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


def _tasks(proj: _project.Project, tg: TaskGraph | None = None, *,
           views: bool = True) -> list[Violation]:
    """The task store's own invariants, plus its view staleness.

    An *absent* `tasks.json` is silence — the overwhelming majority of projects
    will never have one, and the decision graph must be unaffected by its
    absence. An *unreadable* one is a blocking violation, following the
    `store_loads` precedent: a state the tool cannot read is not a state it may
    vouch for.

    `tg` is the graph to judge when the caller already holds one — `staged`
    passes the previewed store — and `views=False` leaves the view check out,
    for the reason `staged` gives.
    """
    from dgraph import task_render
    from dgraph.tasks import TaskGraph

    if tg is None:
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

    # The note, and the live end of each archive. `completions` and `stops`
    # are append-only history; only the last of each is a record somebody is
    # still reading, and the earlier ones are as unactionable as a superseded
    # answer.
    problems += _verbose(
        [(t.id, {"note": t.note,
                 "outcome": t.completions[-1].outcome if t.completions else None,
                 "why": t.stops[-1].why if t.stops else None})
         for t in tg.tasks.values()],
        TASK)
    problems += _unknown(
        [(t.id, t.extra) for t in tg.tasks.values()]
        + [(f"the {e.kind} edge from {e.src}", e.extra) for e in tg.edges],
        TASK)

    if not views:
        return problems

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


def _decisions(proj: _project.Project, g: Graph | None = None, *,
               views: bool = True) -> list[Violation]:
    """The decision store's own invariants, plus its view staleness. `g` and
    `views` as in `_tasks`."""
    if g is None:
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

    # A vertex's note, and each ACTIVE edge's prose. Superseded edges are
    # skipped: they are the record of what was believed and are never edited,
    # so a warning about one names something nobody may act on.
    problems += _verbose(
        [(v.id, {"note": v.note}) for v in g.vertices.values()]
        + [(e.src, {"answer": e.answer, "falsifier": e.falsifier,
                    "summary": e.summary, "why": e.why})
           for e in g.edges if e.active],
        DECISION)
    problems += _unknown(
        [(v.id, v.extra) for v in g.vertices.values()]
        + [(f"the edge from {e.src}", e.extra) for e in g.edges],
        DECISION)

    if not views:
        return problems

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
