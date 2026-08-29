#!/usr/bin/env python3
"""What an edge index would be worth, measured rather than guessed.

`Graph.active_edge` and `Graph.depends` both scan `self.edges` from the front,
so every traversal built on them is a linear scan nested inside a walk. This
probe re-times the same ops against a `Graph` subclass that builds two
dictionaries — `src -> first active edge` and `target -> sources` — once, at
construction.

It is **not a proposed patch**. Nothing here is cache-invalidated, and the
stores are mutated in place by the staging layer, so a real fix has to decide
where the index dies. The point is only to put a number on the prize before
anyone argues about the cost.

    python probe_index.py --store /path/to/a/generated/project
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dgraph import cross, query  # noqa: E402
from dgraph.model import Graph  # noqa: E402
from dgraph.tasks import TaskGraph  # noqa: E402


class IndexedGraph(Graph):
    """`Graph`, with the two scans replaced by dictionary lookups."""

    def _build(self) -> None:
        self._active: dict[str, object] = {}
        self._rivals: dict[str, list] = {}
        self._into: dict[str, set[str]] = {}
        self._hist: dict[str, list] = {}
        for e in self.edges:
            if e.active:
                if e.src in self._active:
                    self._rivals.setdefault(e.src, []).append(e)
                else:
                    self._active[e.src] = e
                    for t in e.to:
                        self._into.setdefault(t, set()).add(e.src)
            elif e.from_source is None:
                self._hist.setdefault(e.src, []).append(e)

    def active_edge(self, vid):
        return self._active.get(vid)

    def rival_answers(self, vid):
        return self._rivals.get(vid, [])

    def history(self, vid):
        return sorted(self._hist.get(vid, []), key=lambda e: e.date or "")

    def depends(self, vid):
        return sorted(s for s in self._into.get(vid, ()) if s in self.vertices)


def timed(fn, reps=3):
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t0)
    return min(ts), statistics.median(ts)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--store", type=Path, required=True)
    p.add_argument("--reps", type=int, default=3)
    p.add_argument("--exclude", nargs="*", default=[],
                   help="Ops to leave out — `validate` on a big store costs "
                        "minutes on the scanning side alone.")
    a = p.parse_args()

    plain = Graph.load(a.store / "decisions.json")
    idx = IndexedGraph.load(a.store / "decisions.json")
    t0 = time.perf_counter()
    idx._build()
    build_s = time.perf_counter() - t0
    tg = TaskGraph.load(a.store / "tasks.json")

    ids = sorted(plain.vertices)
    root = plain.roots()[0]
    sink = next(v for v in reversed(ids)
                if not plain.children(v) and plain.depends(v))
    mid = next(v for v in ids[len(ids) // 2:]
               if plain.vertices[v].base_status == "DECIDED")

    def find(g, q):
        def go():
            ls = [l for l in cross.lenses(g, tg) if l.kind == "decisions"]
            parsed = query.parse(q)
            query.vet(parsed, ls)
            for l in query.scope(parsed, ls):
                query.select(parsed, l)
        return go

    cases = [
        ("descendants.root", lambda g: g.descendants(root)),
        ("ancestors.sink", lambda g: g.ancestors(sink)),
        ("depths.sink", lambda g: g.depths(sink)),
        ("roots", lambda g: g.roots()),
        ("unpropagated", lambda g: g.unpropagated()),
        ("validate", lambda g: g.validate()),
        ("find.prose_rare", lambda g: find(g, "quokka")()),
        ("find.waits_mid", lambda g: find(g, f"waits:{mid}")()),
        ("find.is_orphaned", lambda g: find(g, "is:orphaned")()),
    ]

    print(f"store: {a.store}  ({len(plain.vertices):,} vertices, "
          f"{len(plain.edges):,} edge records)")
    print(f"index build: {build_s * 1e3:.1f} ms (once, at load)\n")
    print(f"{'op':<22}{'scan (ms)':>14}{'indexed (ms)':>15}{'speedup':>10}")
    for name, fn in cases:
        if name in a.exclude:
            print(f"{name:<22}{'excluded':>14}")
            continue
        b1, _ = timed(lambda: fn(plain), a.reps)
        b2, _ = timed(lambda: fn(idx), a.reps)
        print(f"{name:<22}{b1 * 1e3:>14,.1f}{b2 * 1e3:>15,.1f}"
              f"{b1 / b2:>9.1f}x")


if __name__ == "__main__":
    main()
