"""Every whole-store aggregate against the per-item accessor it batches.

The efficiency work's own named pattern is *a function that computes the whole
answer, a caller that keeps one row, and a loop around the caller* — and the
fix for it is always a batched form. That fix creates a **second
implementation of one question**, and where the answer is order-dependent the
two drift: `E-F1` was `all_depths` and `depth` reporting different numbers for
one vertex, on a store `dg check` loads and does not call cyclic.

Three constructions exist here and only two of them make drift impossible:

- **One implementation, two code paths.** The aggregate is an *index*
  (`by_src`, `_reverse`, `_adjacency`) and the accessor takes it as an optional
  argument. The accessor is the definition and the index is an optimisation —
  but both branches are written by hand, so `MATRIX A` runs every accessor both
  ways.
- **Delegation.** The per-item form calls the batched one, or the reverse.
  `cross.late_evidence` was rewritten this way *"so the two cannot disagree"*.
  Structural, so `MATRIX C` asserts the call still happens: an optimisation
  that quietly re-splits one of these turns it back into the third kind.
- **Twins.** Two independent walks over one relation. Nothing prevents drift
  and only a corpus can catch it, which is `MATRIX B`.

**Both lists are derived, not written down**, for the reason
`test_write_lock_matrix.py` gives about doors: a matrix somebody has to
maintain is the thing that failed. `_index_takers` and `_aggregates` read the
signatures, so an accessor that gains an index parameter tomorrow is covered
the day it lands, and an aggregate nobody has classified **fails**
`test_every_aggregate_is_accounted_for` rather than passing silently.

**And the corpus is the other half of the finding.** `E-F1` was invisible to
every existing differential test because they all run on stores the tool would
*write*. These run on stores it would only ever *load*: a git text-merge of two
clones leaves two active edges on one vertex, which is what `rival_answers`
exists for. The generators assert they reached that shape, because an
equality assertion over a corpus that never exercises the branch is a passing
test that pins nothing.
"""

from __future__ import annotations

import ast
import inspect
import random
import textwrap
from collections import Counter

import pytest

from dgraph import cross
from dgraph.model import Edge, Graph, Vertex
from dgraph.tasks import KINDS, Task, TaskEdge, TaskGraph

#: The parameter names this codebase uses for "an index the caller already has".
INDEX_PARAMS = {"_by": "by_src", "_into": "_reverse", "_adj": "_adjacency"}

D_STATUSES = ("OPEN", "DECIDED", "PROVISIONAL", "REOPENED", "TERMINAL")
T_STATUSES = ("TODO", "DOING", "PARKED", "DONE", "DROPPED")

#: Enough seeds to reach every shape the guards below assert, and few enough to
#: stay a test. The audit that found `E-F1` ran 4,000; 300 still fails on it.
SEEDS = 300


# ---- the corpus: stores the tool would load and never write ---------------


def decision_store(seed: int) -> Graph:
    r = random.Random(seed)
    ids = [f"D{i:02d}" for i in range(1, r.randint(2, 9) + 1)]
    verts = {}
    for i in ids:
        s = r.choice(D_STATUSES)
        verts[i] = Vertex(i, "t", "a", s)
    edges = []
    for _ in range(r.randint(0, 14)):
        src = r.choice(ids + ["D99"])            # a source naming no vertex
        to = r.sample(ids, r.randint(0, min(3, len(ids))))
        if r.random() < 0.25:
            to.append("DXX")                     # a target naming no vertex
        if r.random() < 0.12:
            to.append(src)                       # an edge to itself
        # Several active edges out of one vertex: `one_active_edge` refuses it
        # and no `dg apply` writes it, but a git text-merge of two clones that
        # each settled the same inherited question produces exactly this.
        edges.append(Edge(src, to, active=r.random() < 0.65,
                          answer=r.choice([None, "a"]),
                          date=r.choice([None, "2026-01-01"]),
                          from_source=r.choice([None, None, "c"])))
    return Graph(areas=["a"], vertices=verts, edges=edges)


def task_store(seed: int) -> TaskGraph:
    r = random.Random(seed + 100_000)
    ids = [f"T{i:02d}" for i in range(1, r.randint(2, 9) + 1)]
    tasks = {}
    for i in ids:
        st = r.choice(T_STATUSES)
        tasks[i] = Task(id=i, title="t", area="a", status=st,
                        because=r.sample(ids, r.randint(0, 1)),
                        stops=[{"why": "w"}] if st == "DROPPED" else [])
    edges = []
    for _ in range(r.randint(0, 14)):
        src = r.choice(ids + ["T99"])
        to = r.sample(ids, r.randint(0, min(3, len(ids))))
        if r.random() < 0.25:
            to.append("TXX")
        edges.append(TaskEdge(src, to, r.choice(KINDS)))
    return TaskGraph(areas=["a"], tasks=tasks, edges=edges)


def test_the_corpus_reaches_the_shapes_the_fixtures_cannot():
    """An equality assertion over a corpus that never exercises the branch is a
    passing test that pins nothing — the efficiency study's probe compared two
    empty lists at every size and printed that every variant agreed."""
    rivals = dangling = cyclic = selfedge = 0
    for seed in range(SEEDS):
        g = decision_store(seed)
        active: Counter = Counter(e.src for e in g.edges if e.active)
        rivals += any(n > 1 for n in active.values())
        dangling += any(t not in g.vertices
                        for e in g.edges if e.active for t in e.to)
        selfedge += any(t == e.src for e in g.edges for t in e.to)
        # A cycle in `depends` — every active edge — which is not the same
        # relation `validate`'s acyclic check walks. That difference is `E-F1`.
        cyclic += g.all_depths() != {
            v: 0 for v in g.vertices} and any(
                v in g.ancestors(v) for v in g.vertices)
    assert rivals > 50, f"no rival active edges in the corpus ({rivals})"
    assert dangling > 50, f"no dangling targets ({dangling})"
    assert selfedge > 10, f"no self-edges ({selfedge})"
    assert cyclic > 10, f"no cycle in `depends` ({cyclic})"

    unfinished = blocked = 0
    for seed in range(SEEDS):
        tg = task_store(seed)
        unfinished += any(t.unfinished for t in tg.tasks.values())
        blocked += bool(tg.blocked_ids())
    assert unfinished > 100, f"no unfinished work ({unfinished})"
    assert blocked > 20, f"nothing blocked ({blocked})"


# ---- the two lists, derived from the signatures ---------------------------


def _index_takers(cls) -> list[tuple[str, str, list[str]]]:
    """`(method, index parameter, its other required args)` for every per-item
    accessor that can be handed an index."""
    out = []
    for name, fn in sorted(vars(cls).items()):
        if not callable(fn) or name.startswith("__"):
            continue
        try:
            params = list(inspect.signature(fn).parameters.values())[1:]
        except (ValueError, TypeError):
            continue
        idx = [p.name for p in params if p.name in INDEX_PARAMS]
        if not idx:
            continue
        extra = [p.name for p in params
                 if p.default is inspect.Parameter.empty
                 and p.name not in INDEX_PARAMS][1:]
        out.append((name, idx[0], extra))
    return out


def _aggregates(cls) -> list[str]:
    """Every method that takes nothing but `self` — the whole-store answers."""
    out = []
    for name, fn in sorted(vars(cls).items()):
        if not callable(fn) or name.startswith("__"):
            continue
        try:
            params = list(inspect.signature(fn).parameters.values())[1:]
        except (ValueError, TypeError):
            continue
        if not params:
            out.append(name)
    return out


# ---- MATRIX B's definitions, written from the accessor and never from the
#      aggregate's own implementation ---------------------------------------

def _reverse_def(g: Graph):
    live = [e for e in g.edges if e.active and e.src in g.vertices]
    return {t: {e.src for e in live if t in e.to}
            for t in {t for e in live for t in e.to}}


GRAPH_DEFS = {
    "all_depths": lambda g: {v: g.depth(v) for v in g.vertices},
    "provisional_causes": lambda g: {
        v: g.provisional_because(v) for v, x in g.vertices.items()
        if x.base_status == "PROVISIONAL"},
    "stale_provisional": lambda g: [
        v for v, x in g.vertices.items()
        if x.base_status == "PROVISIONAL" and not g.provisional_because(v)],
    "roots": lambda g: sorted(v for v in g.vertices if not g.depends(v)),
    "unpropagated": lambda g: [
        (v, p) for v, x in sorted(g.vertices.items())
        if x.base_status == "DECIDED" for p in g.waiting_on(v)],
    "frontier": lambda g: sorted(v for v, x in g.vertices.items()
                                 if not x.settled),
    "by_src": lambda g: {s: [e for e in g.edges if e.src == s]
                         for s in {e.src for e in g.edges}},
    "_reverse": _reverse_def,
    "_forward": lambda g: {v: g.children(v)
                           for v in {e.src for e in g.edges if e.active}},
}

TASK_DEFS = {
    "blocked_ids": lambda tg: sorted(t for t in tg.tasks if tg.blocked(t)),
    "frontier": lambda tg: sorted(t for t, x in tg.tasks.items() if x.unfinished),
    "counts": lambda tg: dict(Counter(x.status for x in tg.tasks.values())),
    "_adjacency": lambda tg: (
        {(s, k): set(tg._out(s, k))
         for s in {e.src for e in tg.edges} for k in KINDS if tg._out(s, k)},
        {(h, k): set(tg._in(h, k))
         for h in {h for e in tg.edges for h in e.to} for k in KINDS
         if tg._in(h, k)}),
}

#: Aggregates deliberately outside MATRIX B, each with its reason.
EXEMPT = {
    # The serialiser. It is not a derivation of the records, it *is* the
    # records, and `to_dict`/`from_dict` are pinned as a round trip elsewhere.
    "to_dict": "serialiser, not a per-record derivation",
    # The aggregate of the rules rather than of the store, and it has no
    # per-item twin to drift from: every rule under it is pinned by its own
    # test, and the whole of `test_check.py` is its definition.
    "validate": "the rules themselves; pinned by the check suite",
    # Writes a file. Not an answer about the records at all.
    "save": "a write, not a reading",
}


@pytest.mark.parametrize("cls,defs", [(Graph, GRAPH_DEFS), (TaskGraph, TASK_DEFS)],
                         ids=["Graph", "TaskGraph"])
def test_every_aggregate_is_accounted_for(cls, defs):
    """The completeness half, and the reason this is a matrix rather than a
    list of tests. A whole-store answer added tomorrow is either given a
    definition to be checked against or argued into `EXEMPT` — it cannot
    quietly arrive as a second implementation nobody compared."""
    found = set(_aggregates(cls))
    accounted = set(defs) | set(EXEMPT)
    missing = found - accounted
    assert not missing, (
        f"{cls.__name__} has whole-store aggregates that nothing checks against "
        f"a per-item definition: {sorted(missing)}. Add each to the definitions "
        f"above, or to EXEMPT with the reason it has no per-item twin.")
    stale = set(defs) - found
    assert not stale, f"{cls.__name__}: definitions for methods that are gone: {sorted(stale)}"


# ---- MATRIX A -------------------------------------------------------------


@pytest.mark.parametrize(
    "cls,name,idx,extra",
    [(c, n, i, e) for c in (Graph, TaskGraph) for n, i, e in _index_takers(c)],
    ids=[f"{c.__name__}.{n}" for c in (Graph, TaskGraph)
         for n, _, _ in _index_takers(c)])
def test_an_accessor_answers_the_same_with_and_without_its_index(cls, name, idx, extra):
    """The index is an optimisation and nothing else, so the two branches must
    answer identically — including for a record the index has no key for, where
    one returns empty and the other misses the key."""
    make = decision_store if cls is Graph else task_store
    for seed in range(SEEDS):
        g = make(seed)
        index = getattr(g, INDEX_PARAMS[idx])()
        fn = getattr(g, name)
        ids = g.vertices if cls is Graph else g.tasks
        combos = [(k,) for k in KINDS] if extra == ["kind"] else [()]
        for rid in ids:
            for combo in combos:
                assert fn(rid, *combo, index) == fn(rid, *combo), (
                    f"{cls.__name__}.{name}({rid}) differs with and without "
                    f"{idx} at seed {seed}")


# ---- MATRIX B -------------------------------------------------------------


@pytest.mark.parametrize(
    "cls,name",
    [(Graph, n) for n in GRAPH_DEFS] + [(TaskGraph, n) for n in TASK_DEFS],
    ids=[f"Graph.{n}" for n in GRAPH_DEFS] + [f"TaskGraph.{n}" for n in TASK_DEFS])
def test_an_aggregate_agrees_with_the_per_item_definition(cls, name):
    """`E-F1` is this assertion failing for `all_depths`, on a store carrying
    two active edges out of one vertex."""
    make = decision_store if cls is Graph else task_store
    define = (GRAPH_DEFS if cls is Graph else TASK_DEFS)[name]
    for seed in range(SEEDS):
        g = make(seed)
        assert getattr(g, name)() == define(g), (
            f"{cls.__name__}.{name} disagrees with its per-item definition at "
            f"seed {seed}")


# ---- MATRIX C -------------------------------------------------------------


@pytest.mark.parametrize("mod,batched,single", [
    (cross, "late_evidence_all", "late_evidence"),
])
def test_a_delegating_pair_still_delegates(mod, batched, single):
    """The construction that makes drift impossible, pinned as a construction.

    `late_evidence` computes nothing: it reads one row out of
    `late_evidence_all`. That is why it needs no corpus and cannot be `E-F1` —
    and it is exactly what a later optimisation would undo, by giving the
    single-decision form its own faster path."""
    src = textwrap.dedent(inspect.getsource(getattr(mod, single)))
    called = {
        node.func.id for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    # The *call*, not the name. A substring check passes on the docstring
    # here — which says "Ask `late_evidence_all` if you want this for every
    # decision" — and so survives the whole body being replaced by a second
    # implementation. That is shape 5 in the audit guide, and this test was
    # written that way first and caught by mutating the thing it guards.
    assert batched in called, (
        f"`{single}` no longer calls `{batched}` — it has become a second "
        f"implementation of one question, which is what `E-F1` was")
