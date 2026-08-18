"""A broken invariant, shared by every store this tool validates.

Extracted from `dgraph/model.py` so that a second store's validator can report
findings without importing the decision model. The import direction is the
barrier: `model.py` must never learn what a task is, and it cannot, because
nothing it depends on knows either.

`from dgraph.model import Violation` still resolves — `model` imports this name
into its own namespace — so no existing caller changed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Violation:
    """A broken invariant.

    `error` means the store is structurally wrong and must not be written.
    `warning` means it is probably a mistake but is representable and legal —
    an isolated vertex, say, which is exactly what the first vertex of a new
    graph looks like.
    """

    check: str
    message: str
    severity: str = "error"

    def __str__(self) -> str:
        mark = "" if self.severity == "error" else " (warning)"
        return f"[{self.check}]{mark} {self.message}"

    @property
    def blocking(self) -> bool:
        return self.severity == "error"
