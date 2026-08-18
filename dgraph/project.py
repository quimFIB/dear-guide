"""Where the graph lives.

The tool is installed once and run against any project. A project is simply a
directory containing `decisions.json`; the rendered view and the staging file
sit beside it.

Resolution order:
  1. an explicit path (``dg --project PATH``)
  2. ``$DG_PROJECT``
  3. the nearest ancestor of the cwd containing ``decisions.json``
  4. the cwd
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

STORE_NAME = "decisions.json"
VIEW_NAME = "decision-graph.md"
PENDING_NAME = ".dgraph-pending.json"
EDIT_NAME = ".dgraph-edit.org"

_override: Path | None = None


@dataclass(frozen=True)
class Project:
    root: Path

    @property
    def store(self) -> Path:
        return self.root / STORE_NAME

    @property
    def view(self) -> Path:
        return self.root / VIEW_NAME

    @property
    def pending(self) -> Path:
        return self.root / PENDING_NAME

    @property
    def edit(self) -> Path:
        """The editor buffer. A stable name, not a tempfile, so emacs can key
        `auto-mode-alist` off it the way it does for COMMIT_EDITMSG."""
        return self.root / EDIT_NAME

    @property
    def exists(self) -> bool:
        return self.store.exists()


def use(path: str | Path | None) -> None:
    """Pin the project for this process (used by ``dg --project``)."""
    global _override
    _override = Path(path).expanduser().resolve() if path else None


def find(start: Path | None = None) -> Project:
    if _override is not None:
        return Project(_override)
    env = os.environ.get("DG_PROJECT")
    if env:
        return Project(Path(env).expanduser().resolve())
    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        if (d / STORE_NAME).exists():
            return Project(d)
    return Project(cur)
