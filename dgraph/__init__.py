"""`dg` — a project's development graph.

Two stores, kept apart on purpose: `decisions.json` holds what the project has
settled, what each answer rests on and what evidence would reopen it;
`tasks.json` holds the work that follows from those decisions. They meet in one
module, `dgraph.cross`, and nowhere else.
"""

from __future__ import annotations

#: The distribution name, in one place. It has been renamed once already, and
#: the symptom of a stale copy is `dg --version` reporting "unknown" — which an
#: adapter reads as "too old to have the command I want".
DIST = "development-graph-assistant"
TOOL = "dg"


def version() -> str:
    """The installed version, or `"unknown"` from a bare checkout."""
    from importlib.metadata import PackageNotFoundError, version as _v
    try:
        return _v(DIST)
    except PackageNotFoundError:
        return "unknown"
