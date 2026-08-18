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
    "open_not_overspecified",
    "propagation",
    "stale_provisional",
    "stale_block",
    "no_orphans",
    "acyclic",
    "stale_view",
)


def run(proj: _project.Project | None = None) -> list[Violation]:
    proj = proj or _project.find()

    if not proj.exists:
        return [Violation(
            "store_loads",
            f"no {proj.store.name} under {proj.root}",
        )]
    try:
        g = Graph.load(proj.store)
    except Exception as exc:  # malformed JSON, bad shape
        return [Violation("store_loads", f"{proj.store} could not be read: {exc}")]

    try:
        problems = g.validate()
    except Exception as exc:
        # A validator bug must degrade to a violation, never to a crash: the
        # commit gate treats a crash as "no verdict" and would fail open on
        # exactly the store damage validation exists to catch.
        return [Violation(
            "store_loads",
            f"internal: validate() itself failed on {proj.store.name} "
            f"({exc!r}) — the graph cannot be judged valid",
        )]

    if not proj.view.exists():
        problems.append(Violation(
            "stale_view", f"{proj.view.name} is missing — run `dg render`"
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
                "stale_view", f"{proj.view.name} could not be read: {exc}"
            ))
        if current is not None and current != render.render(g):
            problems.append(Violation(
                "stale_view",
                f"{proj.view.name} does not match {proj.store.name}. It is "
                f"generated — run `dg render` rather than editing it.",
            ))

    unknown = sorted({p.check for p in problems} - set(CHECKS))
    if unknown:
        problems.append(Violation(
            "store_loads",
            f"internal: checks emitted but not declared in CHECKS: {unknown}",
        ))
    return problems


def errors(proj: _project.Project | None = None) -> list[Violation]:
    return [p for p in run(proj) if p.blocking]
