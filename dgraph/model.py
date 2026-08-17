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
from dataclasses import dataclass, field
from pathlib import Path

from dgraph import project

SIMPLE_STATUSES = {"DECIDED", "OPEN", "REOPENED", "PROVISIONAL"}
UNSETTLED = {"OPEN", "BLOCKED", "REOPENED"}


@dataclass
class Vertex:
    id: str
    title: str
    area: str
    status: str
    note: str | None = None  # prose for a vertex with no decision yet

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

    @property
    def decided(self) -> bool:
        """An edge with a payload records a decision; without one it is only a
        declared dependency awaiting the source vertex being settled."""
        return self.answer is not None

    @property
    def terminal(self) -> bool:
        return self.decided and not self.to


@dataclass
class Violation:
    """A broken invariant.

    `error` means the graph is structurally wrong and must not be written.
    `warn` means it is probably a mistake but is representable and legal — an
    isolated vertex, say, which is exactly what the first vertex of a new graph
    looks like.
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


@dataclass
class Graph:
    areas: list[str] = field(default_factory=list)
    vertices: dict[str, Vertex] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    # ---- construction ----------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> Graph:
        raw = json.loads((path or project.find().store).read_text(encoding="utf-8"))
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
                )
                for e in raw["edges"]
            ],
        )

    def to_dict(self) -> dict:
        def edge_dict(e: Edge) -> dict:
            d: dict = {"from": e.src, "to": e.to, "active": e.active}
            for k in ("answer", "falsifier", "source", "date", "summary", "replaced_by", "why"):
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
        """Derived, never stored: the parents that point at this vertex."""
        return sorted(
            {e.src for e in self.edges if e.active and vid in e.to}
        )

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

    def roots(self) -> list[str]:
        return sorted(v for v in self.vertices if not self.depends(v))

    def frontier(self) -> list[str]:
        return sorted(
            v.id for v in self.vertices.values() if v.base_status in UNSETTLED
        )

    def depth(self, vid: str) -> int:
        """Longest path from any root — the rank used for graph layout."""
        memo: dict[str, int] = {}

        def go(n: str, seen: frozenset[str]) -> int:
            if n in memo:
                return memo[n]
            if n in seen:  # defensive; validate() reports cycles properly
                return 0
            parents = self.depends(n)
            d = 0 if not parents else 1 + max(go(p, seen | {n}) for p in parents)
            memo[n] = d
            return d

        return go(vid, frozenset())

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
            if vert.base_status != "DECIDED":
                continue
            for p in self.depends(vid):
                if not self.vertices[p].settled:
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

        colour: dict[str, int] = {}

        def visit(n: str, trail: list[str]) -> None:
            if colour.get(n) == 2:
                return
            if colour.get(n) == 1:
                add("acyclic", f"cycle: {' -> '.join(trail + [n])}")
                return
            colour[n] = 1
            for c in self.children(n):
                if c in self.vertices:
                    visit(c, trail + [n])
            colour[n] = 2

        for vid in self.vertices:
            visit(vid, [])

        return v
