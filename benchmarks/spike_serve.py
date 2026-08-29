"""SPIKE (throwaway): does holding a library graph across requests pay?

`dg serve` is the one process that loads a store once and answers many
questions from it, so the build cost that sinks a library for one-shot CLI
commands is amortised here. This measures whether that changes the answer.

Not production code. Answers are checked against the current ones; nothing
else about it is meant to survive.
"""
import sys, time
sys.path.insert(0, "/tmp/dg-bench/libs")
sys.path.insert(0, "/home/feynman/workspace/random/tools/dear-guide")
from pathlib import Path
import rustworkx as rx
from dgraph.model import Graph
from dgraph import server

STORE = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dg-bench/n10000d6"
REQS = int(sys.argv[2]) if len(sys.argv) > 2 else 5
g = Graph.load(Path(STORE)/"decisions.json")
V = list(g.vertices)
print(f"store {len(V):,} vertices; simulating {REQS} requests\n", flush=True)

def t(fn, r=1):
    b=[]
    for _ in range(r):
        t0=time.perf_counter(); out=fn(); b.append(time.perf_counter()-t0)
    return min(b), out

# ---- the library graph, built once and held --------------------------------
t0=time.perf_counter()
G = rx.PyDiGraph(); IDX = {v: G.add_node(v) for v in V}; RID = {i:v for v,i in IDX.items()}
G.add_edges_from_no_data([(IDX[e.src], IDX[x]) for e in g.edges if e.active
                          for x in e.to if x in g.vertices and e.src in g.vertices])
build = time.perf_counter()-t0
print(f"  build the PyDiGraph (once)          {build*1e3:8.1f} ms", flush=True)

# ---- the four library-addressable pieces, both ways ------------------------
def rx_depths():
    d={}
    for i in rx.topological_sort(G):
        p=G.predecessor_indices(i)
        d[i]=0 if not p else 1+max(d[x] for x in p)
    return {RID[i]:v for i,v in d.items()}

def rx_depends_all():
    return {RID[i]: sorted(RID[p] for p in G.predecessor_indices(i))
            for i in G.node_indices()}

def rx_prov_causes():
    unsettled = {IDX[v] for v in V if not g.vertices[v].settled}
    out={}
    for v in V:
        if g.vertices[v].base_status != "PROVISIONAL": continue
        anc = rx.ancestors(G, IDX[v])
        out[v] = sorted(RID[a] for a in anc & unsettled)
    return out

cur_d,  d_cur = t(g.all_depths)
rx_d,   d_rx  = t(rx_depths)
cur_p,  p_cur = t(g.provisional_causes)
rx_p,   p_rx  = t(rx_prov_causes)
into = g._reverse()
cur_r,  _     = t(lambda: {v: g.depends(v, into) for v in V})
rx_r,   _     = t(rx_depends_all)
cur_i,  _     = t(g._reverse)

print(f"\n  CORRECTNESS  depths match: {d_cur==d_rx} | provisional match: {p_cur==p_rx}")
print(f"\n  {'piece':<26}{'current':>11}{'rustworkx':>12}")
print(f"  {'all_depths':<26}{cur_d*1e3:>11.1f}{rx_d*1e3:>12.1f}")
print(f"  {'provisional_causes':<26}{cur_p*1e3:>11.1f}{rx_p*1e3:>12.1f}")
print(f"  {'depends x V':<26}{(cur_r+cur_i)*1e3:>11.1f}{rx_r*1e3:>12.1f}")
cur_tot = cur_d+cur_p+cur_r+cur_i; rx_tot = rx_d+rx_p+rx_r
print(f"  {'-- traversal subtotal':<26}{cur_tot*1e3:>11.1f}{rx_tot*1e3:>12.1f}")

# ---- what that means for a whole request -----------------------------------
whole,_ = t(lambda: server.graph_payload(g))
print(f"\n  whole payload now                   {whole*1e3:8.1f} ms")
print(f"  whole payload with rustworkx        {(whole-cur_tot+rx_tot)*1e3:8.1f} ms  (projected)")
print(f"  saving per request                  {(cur_tot-rx_tot)*1e3:8.1f} ms")
print(f"  requests to repay the build         {build/(cur_tot-rx_tot):8.1f}")
print(f"\n  over {REQS} requests: "
      f"{(whole*REQS)*1e3:9.1f} ms  vs  "
      f"{(build + (whole-cur_tot+rx_tot)*REQS)*1e3:9.1f} ms")
