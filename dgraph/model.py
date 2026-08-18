"""The decision graph: vertices, edges, and the invariants over them.

`decisions.json` is the source of truth; `decision-graph.md` is a rendered view
of it. The schema is a plain graph:

  vertex  a decision that must be made, with an explicit status
  edge    a dependency, which gains a decision payload once that decision is
          made. An edge with no `answer` means "B depends on A, and A is not
          settled yet".

Dependency is therefore the graph structure and is never stored separately —
storing it twice, in two directions, is what let 11 of 55 nodes disagree under
the previous markdown-native format.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from dgraph import project
from dgraph.violation import Violation  # re-exported: callers import it from here

SIMPLE_STATUSES = {"DECIDED", "OPEN", "REOPENED", "PROVISIONAL"}
UNSETTLED = {"OPEN", "BLOCKED", "REOPENED"}


@dataclass
class Vertex:
    id: str
    title: str
    area: str
    status: str
    note: str | None = None  # prose for a vertex with no decision yet
    format: str | None = None  # the note's dialect: "org", else markdown

    @property
    def base_status(self) -> str:
        return self.status.split(":")[0]

    @property
    def blocker(self) -> str | None:
        return self.status.split(":", 1)[1] if ":" in self.status else None

    @property
    def settled(self) -> bool:
        return self.base_status not in UNSETTLED


@dataclass
class Edge:
    src: str
    to: list[str]
    active: bool = True
    answer: str | None = None
    falsifier: str | None = None
    source: str | None = None
    date: str | None = None
    summary: str | None = None
    replaced_by: str | None = None  # superseded edges: the answer that won
    why: str | None = None  # superseded edges: what overturned it
    #: Provenance of the prose the views render from this record — the
    #: answer/falsifier of an active edge, the why/summary of a superseded one:
    #: "org" when composed through the editor, else markdown. `replaced_by` is
    #: written later by a different op and is deliberately not covered.
    format: str | None = None

    @property
    def decided(self) -> bool:
        """An edge with a payload records a decision; without one it is only a
        declared dependency awaiting the source vertex being settled."""
        return self.answer is not None

    @property
    def terminal(self) -> bool:
        return self.decided and not self.to


@dataclass
class Graph:
    areas: list[str] = field(default_factory=list)
    vertices: dict[str, Vertex] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    # ---- construction ----------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> Graph:
        raw = json.loads((path or project.find().store).read_text(encoding="utf-8"))
        # Refused here, not in validate(): building the vertex dict collapses
        # duplicates (last one wins), so by the time validate() runs, the
        # discarded decision is invisible and "unique ids" can never fire.
        counts = Counter(v["id"] for v in raw["vertices"])
        dupes = sorted(i for i, n in counts.items() if n > 1)
        if dupes:
            raise ValueError(
                f"duplicate vertex id(s): {', '.join(dupes)} — one entry per "
                f"id; merge or renumber them by hand"
            )
        return cls(
            areas=raw.get("areas", []),
            vertices={v["id"]: Vertex(**v) for v in raw["vertices"]},
            edges=[
                Edge(
                    src=e["from"],
                    to=list(e.get("to", [])),
                    active=e.get("active", True),
                    answer=e.get("answer"),
                    falsifier=e.get("falsifier"),
                    source=e.get("source"),
                    date=e.get("date"),
                    summary=e.get("summary"),
                    replaced_by=e.get("replaced_by"),
                    why=e.get("why"),
                    format=e.get("format"),
                )
                for e in raw["edges"]
            ],
        )

    def to_dict(self) -> dict:
        def edge_dict(e: Edge) -> dict:
            d: dict = {"from": e.src, "to": e.to, "active": e.active}
            for k in ("answer", "falsifier", "source", "date", "summary", "replaced_by", "why", "format"):
                v = getattr(e, k)
                if v is not None:
                    d[k] = v
            return d

        order = {a: i for i, a in enumerate(self.areas)}
        verts = sorted(
            self.vertices.values(), key=lambda v: (order.get(v.area, 99), v.id)
        )
        edges = sorted(self.edges, key=lambda e: (e.src, not e.active, e.date or ""))
        return {
            "areas": self.areas,
            "vertices": [
                {
                    k: val
                    for k, val in (
                        ("id", v.id), ("title", v.title), ("area", v.area),
                        ("status", v.status), ("note", v.note),
                        ("format", v.format),
                    )
                    if val is not None
                }
                for v in verts
            ],
            "edges": [edge_dict(e) for e in edges],
        }

    def save(self, path: Path | None = None) -> None:
        text = json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"
        (path or project.find().store).write_text(text, encoding="utf-8")

    # ---- queries ---------------------------------------------------------

    def active_edge(self, vid: str) -> Edge | None:
        for e in self.edges:
            if e.src == vid and e.active:
                return e
        return None

    def history(self, vid: str) -> list[Edge]:
        """Superseded decisions for a vertex, oldest first."""
        return sorted(
            (e for e in self.edges if e.src == vid and not e.active),
            key=lambda e: e.date or "",
        )

    def children(self, vid: str) -> list[str]:
        e = self.active_edge(vid)
        return list(e.to) if e else []

    def depends(self, vid: str) -> list[str]:
        """Derived, never stored: the parents that point at this vertex.

        An edge source that names no vertex is skipped: it cannot be a premise,
        and `validate()` reports the dangling edge itself. Returning it here
        would make every traversal that looks premises up crash on a graph
        `validate()` is in the middle of judging.
        """
        return sorted(
            {e.src for e in self.edges
             if e.active and vid in e.to and e.src in self.vertices}
        )

    def waiting_on(self, vid: str) -> list[str]:
        """The premises this vertex rests on that are not settled yet.

        One implementation because there are three callers with the same
        question — `dg show`'s "Waiting on" column, the `propagation` check
        below, and the brief — and a vertex whose premises differ between two
        of them is exactly the disagreement this tool exists to prevent.
        """
        return [p for p in self.depends(vid) if not self.vertices[p].settled]

    def descendants(self, vid: str) -> set[str]:
        seen: set[str] = set()
        stack = list(self.children(vid))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(self.children(cur))
        return seen

    def ancestors(self, vid: str) -> set[str]:
        seen: set[str] = set()
        stack = list(self.depends(vid))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(self.depends(cur))
        return seen

    def provisional_because(self, vid: str) -> list[str]:
        """The premises under review that a PROVISIONAL vertex rests on.

        The transitive form of `waiting_on`: `pending.expand` marks every decided
        descendant of a reopened vertex PROVISIONAL, so the cause can be any
        distance up the chain.

        The store never records *why* a vertex is PROVISIONAL — `derived_from`
        lives only in the staged op — so this is recomputed, and an empty result
        is meaningful: every premise has been settled again and the vertex is
        waiting for someone to re-examine it (`stale_provisional`).
        """
        return sorted(
            a for a in self.ancestors(vid) if not self.vertices[a].settled
        )

    def roots(self) -> list[str]:
        return sorted(v for v in self.vertices if not self.depends(v))

    def frontier(self) -> list[str]:
        return sorted(
            v.id for v in self.vertices.values() if v.base_status in UNSETTLED
        )

    def depth(self, vid: str) -> int:
        """Longest path from any root — the rank used for graph layout.

        Iterative on an explicit stack: a chain a few hundred decisions deep is
        a legitimate graph and must not hit the recursion limit.
        """
        memo: dict[str, int] = {}
        on_path: set[str] = set()  # cycle guard; validate() reports cycles properly
        stack = [vid]
        while stack:
            n = stack[-1]
            if n in memo:
                stack.pop()
                continue
            on_path.add(n)
            todo = [p for p in self.depends(n)
                    if p not in memo and p not in on_path]
            if todo:
                stack.extend(todo)
                continue
            parents = self.depends(n)
            memo[n] = 0 if not parents else 1 + max(
                memo.get(p, 0) for p in parents  # .get: an in-cycle parent counts 0
            )
            on_path.discard(n)
            stack.pop()
        return memo[vid]

    def path(self, a: str, b: str) -> list[str] | None:
        """Any decision path from a to b, following active edges."""
        stack = [(a, [a])]
        seen = set()
        while stack:
            cur, trail = stack.pop()
            if cur == b:
                return trail
            if cur in seen:
                continue
            seen.add(cur)
            for nxt in self.children(cur):
                stack.append((nxt, trail + [nxt]))
        return None

    # ---- invariants ------------------------------------------------------

    def validate(self) -> list[Violation]:
        v: list[Violation] = []
        def add(c, m, severity="error"):
            v.append(Violation(c, m, severity))
        ids = set(self.vertices)

        for vid, vert in self.vertices.items():
            if not vid.startswith("D") or not vid[1:].isdigit():
                add("ids_wellformed", f"malformed id {vid!r}")
            if vert.area not in self.areas:
                add("ids_wellformed", f"{vid}: unknown area {vert.area!r}")
            if vert.base_status == "BLOCKED":
                tgt = vert.blocker
                if not tgt:
                    add("status_legal", f"{vid}: BLOCKED must name a blocker")
                elif tgt not in ids:
                    add("status_legal", f"{vid}: blocked by unknown vertex {tgt}")
                elif tgt == vid:
                    add("status_legal", f"{vid}: blocked by itself")
                elif self.vertices[tgt].settled:
                    add(
                        "stale_block",
                        f"{vid} is BLOCKED:{tgt} but {tgt} is now "
                        f"{self.vertices[tgt].status} — nothing is blocking it, "
                        f"so it should be OPEN",
                    )
            elif vert.base_status not in SIMPLE_STATUSES:
                add("status_legal", f"{vid}: illegal status {vert.status!r}")

        seen_active: set[str] = set()
        for e in self.edges:
            if e.src not in ids:
                add("no_dangling_refs", f"edge from unknown vertex {e.src}")
                continue
            for t in e.to:
                if t not in ids:
                    add("no_dangling_refs", f"edge {e.src} -> unknown vertex {t}")
                if t == e.src:
                    add("no_dangling_refs", f"{e.src}: edge to itself")
            if e.active:
                if e.src in seen_active:
                    add(
                        "one_active_edge",
                        f"{e.src}: more than one active edge — a question has one "
                        f"current answer; supersede the old one instead",
                    )
                seen_active.add(e.src)

        for vid, vert in self.vertices.items():
            edge = self.active_edge(vid)
            if vert.base_status == "DECIDED":
                if edge is None or not edge.decided:
                    add(
                        "decided_complete",
                        f"{vid}: DECIDED with no decision edge carrying an answer",
                    )
                    continue
                for fld in ("source", "date"):
                    if not getattr(edge, fld):
                        add("decided_complete", f"{vid}: DECIDED without a {fld}")
                if not edge.falsifier and edge.to:
                    add(
                        "decided_complete",
                        f"{vid}: DECIDED without a falsifier. State what evidence "
                        f"would reopen it, or make it terminal.",
                    )
            elif vert.base_status in UNSETTLED and edge is not None and edge.decided:
                add(
                    "open_not_overspecified",
                    f"{vid} is {vert.status} but carries a decided edge",
                )

        for vid, vert in self.vertices.items():
            if vert.base_status == "PROVISIONAL" and not self.provisional_because(vid):
                add(
                    "stale_provisional",
                    f"{vid} is PROVISIONAL but every premise it rests on is "
                    f"settled again — re-examine it, then `dg confirm {vid}`",
                    "warning",
                )

        for vid, vert in self.vertices.items():
            if vert.base_status != "DECIDED":
                continue
            for p in self.waiting_on(vid):
                add(
                    "propagation",
                    f"{vid} is DECIDED but rests on {p} "
                    f"({self.vertices[p].status}) — mark it PROVISIONAL or "
                    f"settle the premise",
                )

        touched = {e.src for e in self.edges} | {
            t for e in self.edges for t in e.to
        }
        for vid in sorted(ids - touched):
            add("no_orphans", f"{vid} is connected to nothing", "warn")

        # Iterative DFS colouring, for the same reason `depth` is iterative: a
        # deep chain is a legal graph, and the validator crashing on one would
        # be the fail-open the check exists to prevent.
        colour: dict[str, int] = {}
        for vid in self.vertices:
            if colour.get(vid):
                continue
            colour[vid] = 1
            stack = [(vid, iter(self.children(vid)))]
            trail = [vid]
            while stack:
                n, kids = stack[-1]
                for c in kids:
                    if c not in self.vertices:
                        continue
                    if colour.get(c) == 1:
                        add("acyclic", f"cycle: {' -> '.join(trail + [c])}")
                        continue
                    if colour.get(c) == 2:
                        continue
                    colour[c] = 1
                    stack.append((c, iter(self.children(c))))
                    trail.append(c)
                    break
                else:
                    colour[n] = 2
                    stack.pop()
                    trail.pop()

        return v
