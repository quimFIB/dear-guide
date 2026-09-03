"""A broken invariant, shared by every store this tool validates.

Extracted from `dgraph/model.py` so that a second store's validator can report
findings without importing the decision model. The import direction is the
barrier: `model.py` must never learn what a task is, and it cannot, because
nothing it depends on knows either.

`from dgraph.model import Violation` still resolves — `model` imports this name
into its own namespace — so no existing caller changed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

ERROR = "error"
WARNING = "warning"

#: The only two severities. Closed, and enforced in `__post_init__`, because
#: `blocking` is `severity == ERROR` and the field is otherwise a free string:
#: a mistyped `"error"` would silently demote a blocking invariant, and the
#: demotion is invisible everywhere it matters at once — `apply_all` stops
#: refusing the batch, the commit gate stops denying, and the pytest plugin
#: goes green. Every other fail-open in this tool is guarded on purpose
#: (`gate.verdict` catches everything and denies; `check` turns a validator
#: crash into a violation), so this one is too.
#:
#: The drift this closes was already in the store: `no_orphans` was raised
#: with `"warn"` where every other advisory finding said `"warning"`. Both
#: happened to behave alike, which is exactly why nothing caught it.
SEVERITIES = (ERROR, WARNING)

Severity = Literal["error", "warning"]

DECISION = "decision"
TASK = "task"
LINK = "link"
#: A finding about the *world*, not a store: what a domain said when it
#: evaluated a probe. Emitted by `dg probe` through `dgraph.domains` and by
#: nothing `check.run` calls (R2: `dg check` output is a function of the
#: store), so `check.ORIGIN` never learns a name with this origin.
DOMAIN = "domain"

#: Which store a finding is *about*, carried on the finding rather than
#: inferred from its name. The commit gate names the broken store in its
#: refusal, and it used to classify by prefix: `task_`/`stale_task_` meant the
#: task store, `link_` the relation, everything else the decision graph. Nine
#: of the thirty-six check names never carried a prefix, so a corrupt
#: `tasks.json` in a project with no decision store at all was denied with
#: "The decision graph is not valid" — and `store_loads`, emitted by all three
#: validators, cannot be classified by any naming scheme at all.
ORIGINS = (DECISION, TASK, LINK, DOMAIN)

Origin = Literal["decision", "task", "link", "domain"]


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
    severity: Severity = ERROR
    #: The store this finding is about. Defaulted to `None` so that every
    #: existing call site — three validators and a dozen ad-hoc raises — is
    #: unchanged; `dgraph/check.py` stamps it as each validator's findings
    #: come back, which is the only place that knows which store was being
    #: read. See `ORIGINS`.
    origin: Origin | None = None

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(
                f"unknown severity {self.severity!r} for check "
                f"{self.check!r} — one of {', '.join(SEVERITIES)}"
            )
        if self.origin is not None and self.origin not in ORIGINS:
            raise ValueError(
                f"unknown origin {self.origin!r} for check {self.check!r} — "
                f"one of {', '.join(ORIGINS)}"
            )

    def __str__(self) -> str:
        mark = "" if self.severity == ERROR else " (warning)"
        return f"[{self.check}]{mark} {self.message}"

    @property
    def blocking(self) -> bool:
        return self.severity == ERROR


def cycle_from(trail: list[str], node: str) -> list[str]:
    """The cycle a DFS just closed, in canonical form: the loop alone, rotated
    to start at its smallest id, closed by repeating that id.

    Every validator in this tool finds cycles the same way — an iterative DFS
    holding the path it walked — and each one used to report `trail + [node]`,
    which is the *route into* the loop followed by the loop. A node feeding a
    cycle from outside it then appears in the finding, and breaking the graph
    there fixes nothing; the message is read by a model that will act on it.

    Rotating matters for a second reason, which is why this lives beside
    `Violation` rather than beside any one walk: `cross.guard_decisions`
    compares findings **by their text** to tell what a batch introduced from
    what was already broken. Two runs that meet the same loop from different
    entry points have to produce the same string, or a pre-existing cycle reads
    as a new one and the guard refuses a write it should have allowed.
    """
    loop = trail[trail.index(node):]
    k = loop.index(min(loop))
    return loop[k:] + loop[:k] + [min(loop)]


def tag(problems: list[Violation], origin: Origin) -> list[Violation]:
    """Stamp `origin` on findings that do not already carry one.

    Called where the store being read is known — which is the caller of a
    validator, never the validator itself: `Graph.validate` does not know it
    is being run by `dg check` against this project's `decisions.json`, and
    `cross.validate` is run both by the link check and, against an empty
    decision graph, by the task check.

    Already-tagged findings pass through, so a helper that mixes sources can
    stamp the odd one out first and blanket-stamp the rest.
    """
    return [v if v.origin is not None else replace(v, origin=origin)
            for v in problems]
