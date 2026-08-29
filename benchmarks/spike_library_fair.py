"""Is the library's win a *library* win, or an *index* win?"""
import sys, time
sys.path.insert(0, "/tmp/dg-bench/libs")
sys.path.insert(0, "/home/feynman/workspace/random/tools/dear-guide")
from pathlib import Path
import networkx as nx, rustworkx as rx
from dgraph.model import Graph

g = Graph.load(Path("/tmp/dg-bench/n10000d6")/"decisions.json")
V = list(g.vertices); mid = V[len(V)//2]

def t(fn, reps=3):
    b=[]
    for _ in range(reps):
        t0=time.perf_counter(); fn(); b.append(time.perf_counter()-t0)
    return min(b)

GNX = nx.DiGraph(); GNX.add_nodes_from(V)
GNX.add_edges_from((e.src,x) for e in g.edges if e.active
                   for x in e.to if x in g.vertices and e.src in g.vertices)
GRX = rx.PyDiGraph(); IDX={v:GRX.add_node(v) for v in V}
GRX.add_edges_from_no_data([(IDX[e.src],IDX[x]) for e in g.edges if e.active
                            for x in e.to if x in g.vertices and e.src in g.vertices])

print("ancestors of one mid vertex, 10,000-vertex store\n")
print(f"  current, no index                 {t(lambda: g.ancestors(mid))*1e3:9.2f} ms")
print(f"  current, index built + reused     {t(lambda: g.ancestors(mid, g._reverse()))*1e3:9.2f} ms")
into = g._reverse()
print(f"  current, index already in hand    {t(lambda: g.ancestors(mid, into))*1e3:9.2f} ms")
print(f"  networkx  (graph already built)   {t(lambda: nx.ancestors(GNX, mid))*1e3:9.2f} ms")
print(f"  rustworkx (graph already built)   {t(lambda: rx.ancestors(GRX, IDX[mid]))*1e3:9.2f} ms")
print()
print(f"  building _reverse()               {t(lambda: g._reverse())*1e3:9.2f} ms")
print(f"  answering EVERY vertex, current   "
      f"{t(lambda: [g.ancestors(v, into) for v in V], 1)*1e3:9.1f} ms")
print(f"  answering EVERY vertex, rustworkx "
      f"{t(lambda: [rx.ancestors(GRX, IDX[v]) for v in V], 1)*1e3:9.1f} ms")
