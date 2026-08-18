"""The task graph: work, what it waits on, and the invariants over them.

`tasks.json` is the store; `tasks.md` is a rendered view of it. The schema is a
plain graph, like the decision store, but a simpler one:

  task    a unit of work, with an explicit status
  edge    a prerequisite relation — `from` must be resolved before anything in
          `to` can start. Edges carry no payload; there is nothing to record
          about "T01 comes before T02" beyond the fact itself.

This module is deliberately **not** a copy of `dgraph/model.py`, because tasks
are not decisions and the differences are the whole point of keeping two stores:

- **Blocked is derived, never stored.** A task is ready when its prerequisites
  are resolved. The decision graph keeps status explicit because a decision can
  have consequences and still be under review — out-degree says nothing about
  it. Task readiness genuinely *is* a function of dependencies, so storing it
  would only create something that can go stale. `stale_block` has no analogue
  here; it cannot occur.
- **No supersession.** A decision that is overturned keeps its old answer
  forever, because how a project changed its mind is worth more than the
  conclusion. Work that is abandoned is `DROPPED` and that is the whole record.
- **No falsifier, no answer.** A task is finished when it is done, and what it
  produced is an `outcome` — a path, a PR, a note. That is a record, not a
  claim about the world, so nothing can falsify it.
- **No orphan check.** An unconnected task is ordinary; an unconnected decision
  is a smell.

Nothing here imports `dgraph.model`, and nothing in `dgraph.model` imports this.
The two stores meet in exactly one place, `dgraph/cross.py`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from dgraph import project
from dgraph.violation import Violation

STATUSES = ("TODO", "DOING", "DONE", "DROPPED")

#: Work that is still outstanding — what "the backlog" means.
UNFINISHED = frozenset({"TODO", "DOING"})

#: Work that will not be done again, so it no longer blocks anything. A dropped
#: prerequisite releases its dependants: abandoning it *is* the decision that it
#: was not needed.
RESOLVED = frozenset({"DONE", "DROPPED"})

ID_RE = re.compile(r"T\d+")


@dataclass
class Task:
    id: str
    title: str
    area: str
    status: str = "TODO"
    note: str | None = None      # prose: what this involves, why it is parked
    done: str | None = None      # ISO date, required once DONE
    outcome: str | None = None   # what the work produced, required once DONE
    format: str | None = None    # the note's dialect: "org", else markdown

    @property
    def unfinished(self) -> bool:
        return self.status in UNFINISHED

    @property
    def resolved(self) -> bool:
        return self.status in RESOLVED


@dataclass
class TaskEdge:
    """`src` must be resolved before any of `to` can start. No payload."""

    src: str
    to: list[str]


@dataclass
class TaskGraph:
    areas: list[str] = field(default_factory=list)
    tasks: dict[str, Task] = field(default_factory=dict)
    edges: list[TaskEdge] = field(default_factory=list)

    # ---- construction ----------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> TaskGraph:
        raw = json.loads((path or project.find().tasks).read_text(encoding="utf-8"))
        # Refused here rather than in validate(), for the reason the decision
        # store gives: building the dict collapses duplicates silently, so by
        # the time validate() runs the discarded task is already invisible.
        counts = Counter(t["id"] for t in raw["tasks"])
        dupes = sorted(i for i, n in counts.items() if n > 1)
        if dupes:
            raise ValueError(
                f"duplicate task id(s): {', '.join(dupes)} — one entry per id; "
                f"merge or renumber them by hand"
            )
        return cls(
            areas=raw.get("areas", []),
            tasks={t["id"]: Task(**t) for t in raw["tasks"]},
            edges=[
                TaskEdge(src=e["from"], to=list(e.get("to", [])))
                for e in raw.get("edges", [])
            ],
        )

    def to_dict(self) -> dict:
        order = {a: i for i, a in enumerate(self.areas)}
        rows = sorted(self.tasks.values(), key=lambda t: (order.get(t.area, 99), t.id))
        return {
            "areas": self.areas,
            "tasks": [
                {
                    k: val
                    for k, val in (
                        ("id", t.id), ("title", t.title), ("area", t.area),
                        ("status", t.status), ("note", t.note),
                        ("done", t.done), ("outcome", t.outcome),
                        ("format", t.format),
                    )
                    if val is not None
                }
                for t in rows
            ],
            "edges": [
                {"from": e.src, "to": sorted(e.to)}
                for e in sorted(self.edges, key=lambda e: e.src)
                if e.to
            ],
        }

    def save(self, path: Path | None = None) -> None:
        text = json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"
        (path or project.find().tasks).write_text(text, encoding="utf-8")

    # ---- queries ---------------------------------------------------------

    def unblocks(self, tid: str) -> list[str]:
        """The tasks this one is a prerequisite for.

        Unions every edge with this source rather than demanding one, because a
        task edge carries no payload: two edges out of `T01` are two sets of
        successors and mean exactly their union. The decision graph needs
        `one_active_edge` because two answers to one question is a
        contradiction; here there is nothing to contradict.
        """
        return sorted({t for e in self.edges if e.src == tid for t in e.to
                       if t in self.tasks})

    def prerequisites(self, tid: str) -> list[str]:
        """Derived, never stored: what must be resolved before this can start.

        Skips a source naming no task, exactly as `Graph.depends` does: a
        traversal helper that trusts ids crashes the validator that was about to
        report the dangling reference.
        """
        return sorted({e.src for e in self.edges
                       if tid in e.to and e.src in self.tasks})

    def waiting_on(self, tid: str) -> list[str]:
        """The prerequisites that are not resolved yet."""
        return [p for p in self.prerequisites(tid) if not self.tasks[p].resolved]

    def ready(self, tid: str) -> bool:
        """Startable now: not yet begun, and nothing outstanding before it."""
        return self.tasks[tid].status == "TODO" and not self.waiting_on(tid)

    def blocked(self, tid: str) -> bool:
        return self.tasks[tid].unfinished and bool(self.waiting_on(tid))

    def frontier(self) -> list[str]:
        """Everything still outstanding, ready or not."""
        return sorted(t.id for t in self.tasks.values() if t.unfinished)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.tasks.values():
            out[t.status] = out.get(t.status, 0) + 1
        return out

    # ---- invariants ------------------------------------------------------

    def validate(self) -> list[Violation]:
        v: list[Violation] = []

        def add(c, m, severity="error"):
            v.append(Violation(c, m, severity))

        ids = set(self.tasks)

        for tid, t in self.tasks.items():
            if not ID_RE.fullmatch(tid):
                add("task_ids_wellformed",
                    f"malformed id {tid!r} — expected something like T07")
            if t.area not in self.areas:
                add("task_ids_wellformed", f"{tid}: unknown area {t.area!r}")
            if t.status not in STATUSES:
                add("task_status_legal",
                    f"{tid}: illegal status {t.status!r} — one of "
                    f"{', '.join(STATUSES)}")

        for e in self.edges:
            if e.src not in ids:
                add("task_no_dangling_refs", f"edge from unknown task {e.src}")
                continue
            for t in e.to:
                if t not in ids:
                    add("task_no_dangling_refs", f"edge {e.src} -> unknown task {t}")
                if t == e.src:
                    add("task_no_dangling_refs", f"{e.src}: edge to itself")

        for tid, t in self.tasks.items():
            if t.status != "DONE":
                continue
            if not t.done:
                add("task_done_complete", f"{tid}: DONE without a date")
            if not t.outcome:
                add("task_done_complete",
                    f"{tid}: DONE without an outcome. Record what it produced "
                    f"— a path, a PR, a note.")
            for p in self.prerequisites(tid):
                if self.tasks[p].unfinished:
                    add("task_done_before_prerequisite",
                        f"{tid} is DONE but {p} ({self.tasks[p].status}) has to "
                        f"come first — finish {p}, or the dependency is wrong")

        # Iterative, for the reason `model.validate` is: a deep but legal chain
        # must not crash the validator, since a crash is the fail-open the check
        # exists to prevent.
        colour: dict[str, int] = {}
        for tid in self.tasks:
            if colour.get(tid):
                continue
            colour[tid] = 1
            stack = [(tid, iter(self.unblocks(tid)))]
            trail = [tid]
            while stack:
                node, nxt = stack[-1]
                for c in nxt:
                    if colour.get(c) == 1:
                        add("task_acyclic", f"cycle: {' -> '.join(trail + [c])}")
                        continue
                    if colour.get(c) == 2:
                        continue
                    colour[c] = 1
                    stack.append((c, iter(self.unblocks(c))))
                    trail.append(c)
                    break
                else:
                    colour[node] = 2
                    stack.pop()
                    trail.pop()

        return v
