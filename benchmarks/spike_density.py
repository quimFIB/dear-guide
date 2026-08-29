"""Does a compiled graph library gain on DENSER graphs?

The first spike used degree 6 (~5.9 arcs/vertex), which is sparse. This
sweeps out-degree at a fixed 2,000 vertices and re-prices the same three
traversals, so the comparison varies edge count alone.
"""
import sys, time
sys.path.insert(0, "/tmp/dg-bench/libs")
sys.path.insert(0, "/home/feynman/workspace/random/tools/dear-guide")
from pathlib import Path
import rustworkx as rx
from dgraph.model import Graph

def t(fn, r=2):
    b=[]
    for _ in range(r):
        t0=time.perf_counter(); o=fn(); b.append(time.perf_counter()-t0)
    return min(b), o

print(f"{'arcs/v':>7}{'arcs':>9}  |{'all_depths':>22}  |{'prov_causes':>22}  |{'ancestorsxV':>22}")
print(f"{'':>16}  |{'cur':>10}{'rx':>7}{'x':>5}  |{'cur':>10}{'rx':>7}{'x':>5}  |{'cur':>10}{'rx':>7}{'x':>5}")
print("-"*94)
for name in ("n2000d6","n2000d16","n2000d32","n2000d64"):
    p = Path(f"/tmp/dg-bench/{name}")
    if not p.exists(): continue
    g = Graph.load(p/"decisions.json"); V=list(g.vertices)
    arcs = sum(len(e.to) for e in g.edges)
    G = rx.PyDiGraph(); IDX={v:G.add_node(v) for v in V}; RID={i:v for v,i in IDX.items()}
    G.add_edges_from_no_data([(IDX[e.src],IDX[x]) for e in g.edges if e.active
                              for x in e.to if x in g.vertices and e.src in g.vertices])
    def rx_depths():
        d={}
        for i in rx.topological_sort(G):
            pr=G.predecessor_indices(i)
            d[i]=0 if not pr else 1+max(d[x] for x in pr)
        return d
    unset={IDX[v] for v in V if not g.vertices[v].settled}
    def rx_prov():
        return {v: sorted(RID[a] for a in rx.ancestors(G,IDX[v]) & unset)
                for v in V if g.vertices[v].base_status=="PROVISIONAL"}
    into=g._reverse()
    a1,_=t(g.all_depths);        b1,_=t(rx_depths)
    a2,_=t(g.provisional_causes);b2,_=t(rx_prov)
    a3,_=t(lambda:[g.ancestors(v,into) for v in V],1)
    b3,_=t(lambda:[rx.ancestors(G,IDX[v]) for v in V],1)
    print(f"{arcs/len(V):7.1f}{arcs:9,}  |{a1*1e3:10.1f}{b1*1e3:7.1f}{a1/b1:5.1f}"
          f"  |{a2*1e3:10.1f}{b2*1e3:7.1f}{a2/b2:5.1f}"
          f"  |{a3*1e3:10.1f}{b3*1e3:7.1f}{a3/b3:5.1f}", flush=True)
