"""Spike: back the decision graph with networkx / rustworkx, and measure.

Compares three backings of the *same* questions on the same store:
  current   the dicts now in model.py (built per call, nothing cached)
  networkx  a DiGraph built once from the active edges
  rustworkx a PyDiGraph built once from the active edges

It measures the questions that map cleanly onto a plain digraph. Where the
mapping is *not* clean that is reported rather than benchmarked, because a
faster wrong answer is not the thing being priced.
"""
import sys, time
sys.path.insert(0, "/tmp/dg-bench/libs")
sys.path.insert(0, "/home/feynman/workspace/random/tools/dear-guide")
from pathlib import Path
import networkx as nx
import rustworkx as rx
from dgraph.model import Graph

STORE = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dg-bench/n10000d6"
g = Graph.load(Path(STORE) / "decisions.json")
V = list(g.vertices)
print(f"store: {len(V):,} vertices, {len(g.edges):,} edge records, "
      f"{sum(len(e.to) for e in g.edges):,} arcs\n")

def t(fn, reps=3):
    b = []
    for _ in range(reps):
        t0 = time.perf_counter(); r = fn(); b.append(time.perf_counter() - t0)
    return min(b), r

# ---- build ---------------------------------------------------------------
def build_nx():
    G = nx.DiGraph()
    G.add_nodes_from(V)
    G.add_edges_from((e.src, x) for e in g.edges if e.active
                     for x in e.to if x in g.vertices and e.src in g.vertices)
    return G

def build_rx():
    G = rx.PyDiGraph()
    idx = {v: G.add_node(v) for v in V}
    G.add_edges_from_no_data([(idx[e.src], idx[x]) for e in g.edges if e.active
                              for x in e.to
                              if x in g.vertices and e.src in g.vertices])
    return G, idx

bnx, GNX = t(build_nx); brx, (GRX, IDX) = t(build_rx)
print(f"{'build the structure':<34}{'':>12}{bnx*1e3:>12.1f}{brx*1e3:>12.1f}")
print(f"{'':<34}{'current':>12}{'networkx':>12}{'rustworkx':>12}")
print("-"*70)

REV = {v: i for i, v in enumerate(V)}
RIDX = {i: v for v, i in IDX.items()}

rows = []
# roots -- every vertex with no active predecessor
c,_ = t(lambda: g.roots())
n,_ = t(lambda: sorted(v for v in V if GNX.in_degree(v) == 0))
r,_ = t(lambda: sorted(RIDX[i] for i in GRX.node_indices()
                       if GRX.in_degree(i) == 0))
rows.append(("roots (whole graph)", c, n, r))

# all ancestors of one mid vertex
mid = V[len(V)//2]
c,_ = t(lambda: g.ancestors(mid))
n,_ = t(lambda: nx.ancestors(GNX, mid))
r,_ = t(lambda: rx.ancestors(GRX, IDX[mid]))
rows.append(("ancestors (one vertex)", c, n, r))

# every vertex's depth (longest path from a root)
c,_ = t(lambda: g.all_depths(), 1)
def nx_depths():
    d = {}
    for v in nx.topological_sort(GNX):
        p = list(GNX.predecessors(v))
        d[v] = 0 if not p else 1 + max(d[x] for x in p)
    return d
n,_ = t(nx_depths, 1)
def rx_depths():
    d = {}
    for i in rx.topological_sort(GRX):
        p = GRX.predecessor_indices(i)
        d[i] = 0 if not p else 1 + max(d[x] for x in p)
    return d
r,_ = t(rx_depths, 1)
rows.append(("all depths", c, n, r))

for name, c, n, r in rows:
    print(f"{name:<34}{c*1e3:>12.2f}{n*1e3:>12.2f}{r*1e3:>12.2f}")
print("\n(milliseconds, best of 3; 'build' is paid once per structure)")
