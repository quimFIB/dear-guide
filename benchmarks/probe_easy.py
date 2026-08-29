#!/usr/bin/env python3
"""The three fixes that need no invalidation story, measured on their own.

`probe_index.py` puts a number on indexing the whole `Graph`, which is the big
win and the one that has to decide where a cached index dies. These three are
smaller and strictly local — each is confined to one function, or to an object
that is already built per invocation and thrown away — so none of them can go
stale, and none of them touches a write path.

    A. `query.decision_lens.values()` asks the graph for the active edge, the
       rival answers and the history **once per field per vertex**. Seven prose
       fields means roughly twenty full edge-list scans per vertex to answer one
       search. The lens is constructed per invocation and dropped with it —
       `_walk`'s memo already lives there for exactly this reason — so a
       per-vertex cache on it is safe by construction.

    B. `Graph.validate`'s `stale_provisional` rule calls `provisional_because`,
       and so `ancestors`, once per PROVISIONAL vertex. One reverse walk from
       the unsettled set answers every vertex at once. This is the cubic term.

    C. `Graph.roots` and `Graph.unpropagated` call `depends` once per vertex,
       and each call rescans every edge. Building the reverse adjacency once at
       the top of the function is a four-line change inside each.

Run: python probe_easy.py --store <generated project> [--reps N]
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dgraph import cross, query  # noqa: E402
from dgraph.model import Graph  # noqa: E402
from dgraph.tasks import TaskGraph  # noqa: E402


def timed(fn, reps):
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t0)
    return min(ts)


# ---- A: cache the three edge lookups on the per-invocation lens ------------

def patched_decision_lens(g, **kw):
    """`query.decision_lens`, with the per-vertex edge lookups cached.

    `values` and `label` are reimplemented exactly as `query.py:669-707` writes
    them, with the one change proposed: the active edge, the rival answers and
    the history are fetched once per vertex through `edges_of` instead of once
    per field. The cache lives on this lens, which the caller builds per
    invocation and drops — the same lifetime `_walk`'s memo already relies on.
    """
    archived = kw.get("archived", True)
    lens = _orig_lens(g, **kw)
    _texts, _ARCH = query._texts, query._ARCHIVED_PROSE
    cache: dict[str, tuple] = {}

    def edges_of(vid):
        got = cache.get(vid)
        if got is None:
            got = cache[vid] = (g.active_edge(vid), g.rival_answers(vid),
                                g.history(vid))
        return got

    def values(vid, name):
        v = g.vertices[vid]
        out = _texts([getattr(v, name, None)])
        if name == "id":
            out = [vid]
        if name == "status":
            out = _texts(dict.fromkeys([v.base_status, v.status]))
        e, rivals, hist = edges_of(vid)
        if e is not None:
            out += _texts([getattr(e, name, None)])
        out += _texts(getattr(other, name, None) for other in rivals)
        if name in ("summary", "why") or (archived and name in _ARCH):
            out += _texts(getattr(h, name, None) for h in hist)
        return out

    def label(vid, name, text):
        if name not in _ARCH:
            return name
        e = edges_of(vid)[0]
        return name if e is not None and getattr(e, name, None) == text \
            else f"superseded {name}"

    import dataclasses
    return dataclasses.replace(lens, values=values, label=label)


_orig_lens = query.decision_lens


# ---- B: one reverse walk instead of one per PROVISIONAL vertex -------------

def stale_provisional_pervertex(g) -> list[str]:
    """What `validate` does now: `provisional_because` per PROVISIONAL vertex."""
    return [vid for vid, v in g.vertices.items()
            if v.base_status == "PROVISIONAL" and not g.provisional_because(vid)]


def stale_provisional_once(g) -> list[str]:
    """The same answer from one walk down from the unsettled vertices.

    A vertex rests on an unsettled premise exactly when it is reachable, along
    active edges, from some unsettled vertex. So collect the descendants of the
    unsettled set in a single pass and every vertex's question is answered.
    """
    kids = defaultdict(list)
    for e in g.edges:
        if e.active:
            for t in e.to:
                if t in g.vertices:
                    kids[e.src].append(t)
    seen: set[str] = set()
    stack = [v for v in g.vertices if not g.vertices[v].settled]
    while stack:
        cur = stack.pop()
        for nxt in kids.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return [vid for vid, v in g.vertices.items()
            if v.base_status == "PROVISIONAL" and vid not in seen]


# ---- C: reverse adjacency built once per call -----------------------------

def roots_scanning(g):
    return sorted(v for v in g.vertices if not g.depends(v))


def _reverse(g):
    into = defaultdict(set)
    for e in g.edges:
        if e.active and e.src in g.vertices:
            for t in e.to:
                into[t].add(e.src)
    return into


def roots_once(g):
    into = _reverse(g)
    return sorted(v for v in g.vertices if not into.get(v))


def unpropagated_scanning(g):
    return g.unpropagated()


def unpropagated_once(g):
    into = _reverse(g)
    return [(vid, p) for vid, v in sorted(g.vertices.items())
            if v.base_status == "DECIDED"
            for p in sorted(into.get(vid, ()))
            if not g.vertices[p].settled]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--store", type=Path, required=True)
    p.add_argument("--reps", type=int, default=2)
    p.add_argument("--skip", nargs="*", default=[],
                   help="Fixes to leave out (A/B/C). B's *baseline* is the "
                        "cubic term itself — 100 full ancestor walks on the "
                        "10,000-vertex store — so it is skipped there and the "
                        "measured `validate` timing stands in for it.")
    a = p.parse_args()

    g = Graph.load(a.store / "decisions.json")
    tg = TaskGraph.load(a.store / "tasks.json")
    print(f"store: {len(g.vertices):,} vertices, {len(g.edges):,} edge records")

    def find(q):
        def go():
            ls = [l for l in cross.lenses(g, tg) if l.kind == "decisions"]
            parsed = query.parse(q)
            query.vet(parsed, ls)
            for l in query.scope(parsed, ls):
                query.select(parsed, l)
        return go

    rows = []
    skip = {x.upper() for x in a.skip}

    # A — same query, same code, one line of caching on the lens.
    if "A" not in skip:
        base = timed(find("quokka"), a.reps)
        query.decision_lens = patched_decision_lens
        fixed = timed(find("quokka"), a.reps)
        query.decision_lens = _orig_lens
        rows.append(("A  find prose (lens cache)", base, fixed))

    # Correctness, not just speed: the two must select the same rows.
    def selected():
        ls = [l for l in cross.lenses(g, tg) if l.kind == "decisions"]
        pq = query.parse("quokka")
        query.vet(pq, ls)
        return [query.select(pq, l) for l in query.scope(pq, ls)]
    before = selected()
    query.decision_lens = patched_decision_lens
    after = selected()
    query.decision_lens = _orig_lens
    assert before == after, "the cached lens changed the answer"

    # B — the cubic term.
    if "B" not in skip:
        b1 = timed(lambda: stale_provisional_pervertex(g), a.reps)
        b2 = timed(lambda: stale_provisional_once(g), a.reps)
        assert stale_provisional_pervertex(g) == stale_provisional_once(g)
        rows.append(("B  stale_provisional", b1, b2))

    # C — two whole-graph derivations.
    if "C" not in skip:
        c1 = timed(lambda: roots_scanning(g), a.reps)
        c2 = timed(lambda: roots_once(g), a.reps)
        assert roots_scanning(g) == roots_once(g)
        rows.append(("C  roots", c1, c2))

        d1 = timed(lambda: unpropagated_scanning(g), a.reps)
        d2 = timed(lambda: unpropagated_once(g), a.reps)
        assert unpropagated_scanning(g) == unpropagated_once(g)
        rows.append(("C  unpropagated", d1, d2))

    print(f"\n{'fix':<28}{'now (ms)':>13}{'after (ms)':>13}{'speedup':>10}")
    for name, x, y in rows:
        print(f"{name:<28}{x * 1e3:>13,.1f}{y * 1e3:>13,.1f}{x / y:>9.1f}x")
    print("\nevery variant asserted to return the identical answer.")


if __name__ == "__main__":
    main()
