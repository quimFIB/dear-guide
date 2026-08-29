# How `dear-guide` scales

Measured 2026-08-28/29 against synthetic stores from 200 to 10,000 decisions,
on Python 3.14.6 / Linux 6.18 / x86-64.

> **This report describes the tool before the fixes it argues for.** Most of
> them were taken on 2026-08-29 — three of the four candidates here, plus four
> more the report does not name. Across 117 ops measured against the state
> below: **62 improved, 55 unchanged, 0 regressed**, and nothing is cubic any
> more (`dg check` 3.03 → 1.92, and ~17 minutes → 21.6 s at 10,000 vertices).
>
> Every number below is the *problem*, not the current state — which is the
> point of keeping it. The current state is
> [`results/final.txt`](results/final.txt): **67 ops improved, 50 unchanged, 0
> regressed**, and `dg find` at 10,000 vertices went 48.7 s → 0.54 s. The
> account of what changed and why is [`HANDOFF.md`](HANDOFF.md).
>
> One thing this report does not contain, because nobody measured it: **`dg
> serve` was far worse than anything here** — 139.8 s to build the payload at
> 1,000 vertices, and it did not finish at 2,500. It is now 117 ms.

About 50 minutes of wall-clock measurement across three runs. Raw data in
[`results/`](results/); every number below is reproducible with the commands at
the end.

The question was: **how do find operations and graph traversals hold up as the
stores grow?** The short answer is that they do not. Nearly everything that
touches the graph is quadratic in the number of decisions, `dg check` is
*cubic*, and the ceiling for comfortable use lands between 700 and 2,000
vertices depending on which command you run most.

None of this is a bug in the sense of a wrong answer. Every number here comes
from a store that `dg check` calls clean, and every result was correct. It is a
statement about where the current data structures run out.

---

## The short version

**1. Every graph-touching operation is quadratic — α ≈ 2.00, measured, not
assumed.** Fitting `log(time) = a + α·log(vertices)` over the top three sizes
gives α between 1.96 and 2.04 for *every* traversal and *every* `dg find`
variant. `dg find quokka` costs 442 ms at 1,000 vertices and 48.5 s at 10,000 —
a 10× store for 110× the time.

**2. `dg check` is cubic, and it is the one in the commit gate.** Measured, not
extrapolated: α = 3.03 between the last two sizes.

| vertices | `Graph.validate` | ×  | `dg check` (CLI) |
|---|---|---|---|
| 1,000 | 1.10 s | | 1.93 s |
| 2,500 | 16.33 s | 14.9× | – |
| 5,000 | **121.4 s** | 7.4× | **132.5 s** |
| 10,000 | **988.9 s** (16.5 min) | 8.1× | ~17 min |

`dgraph/gate.py` runs `check.errors` on every commit and the session-start hook
runs `dg brief`, which validates too. At 5,000 decisions a commit blocks for
over two minutes; at 10,000, for a quarter of an hour.

**3. The cubic term is one line, and the profiler says so.** `model.py:571` —
the `stale_provisional` rule calls `provisional_because(vid)` for each
PROVISIONAL vertex, and that calls `ancestors(vid)`, which is already an O(V·E)
walk because `depends()` scans the whole edge list at every step. Vertices ×
walk × scan = V³. On the 2,500-vertex store, `depends` accounts for **15.9 of
`validate`'s 16.4 seconds** (96.9%, 55,334 calls), and 15.4 of those seconds
are reached through just **25 calls to `ancestors`** — one per PROVISIONAL
vertex. Nothing else in `validate` is worse than quadratic.

**4. A search that finds nothing costs twice a search that finds something.**
An empty result triggers the *did you mean* pass, and `query.words()`
(`cli.py:821`) re-reads every piece of prose in both stores to build a
vocabulary. At 5,000 vertices `dg find quokka` takes 11.8 s and `dg find
zzzabsent` takes 23.3 s. The typo is the worst case, which is the wrong way
round — a typo is exactly when a person is already going to re-run.

**5. Density barely matters for `find`; it is the upward walks that feel it.**
Holding vertices at 1,000 and sweeping out-degree 2 → 16 (2,060 → 15,359 arcs):

| op | d=2 | d=16 | α (in degree) |
|---|---|---|---|
| `find` prose | 451 ms | 502 ms | **0.05** |
| `descendants` | 8.9 ms | 17.1 ms | 0.13 |
| `depends` | 0.058 ms | 0.242 ms | 0.79 |
| `find waits:` | 59.8 ms | 254 ms | 0.82 |
| `validate` | 450 ms | 2,689 ms | 0.88 |
| `ancestors` | 40.9 ms | 234 ms | 0.90 |

`find` reads *edge records* (one active edge per vertex, plus reversals), and
their count does not change with density — so a denser graph searches at the
same speed. `depends()` reads every `to` list of every edge, so anything built
on it scales with arcs as well as vertices. Downward walks sit in between:
`descendants` is nearly flat because the reachable set saturates.

**6. The same lookup costs 50× more for a recently-allocated id.** `to_dict`
writes edges sorted by `from`, and `active_edge` scans from the front and stops
at the first match. At 10,000 vertices, looking up the *first* id takes 0.006 ms
and the *last* one 0.285 ms. Ids are allocated monotonically, so the decisions
people are actually working on are the slowest ones to read.

**7. What stays fast.** Loading a 9.1 MB store is 105 ms. `frontier()` is
1.2 ms at 10,000 — it reads statuses and never touches an edge. `dg node` is
0.4 s. And the memo in `query._walk` is doing its job: `under:D00001` at 10,000
vertices costs 1.56 s, which is one downward walk and not one per row. Its
upward twin `above:` costs 9.4 s at the same size — also exactly one walk. The
memo is not the problem any more; the walk itself is.

**8. Where the practical ceilings are.** Extrapolating each CLI command from
its two largest measured points (`dg check` from its measured α = 3.03, the
rest from α ≈ 2):

| command | 1 s at | 10 s at |
|---|---|---|
| `dg brief` | ~460 vertices | ~1,700 |
| `dg check` | ~800 | ~1,750 |
| `dg find <word>` | ~1,500 | ~4,600 |
| `dg context` | ~1,930 | ~6,500 |
| `dg render` | ~2,030 | ~6,300 |
| `dg find waits:` | ~2,790 | ~9,150 |

A project can live with a store of a few hundred decisions today, which is
comfortably more than this repo's own graph holds. Somewhere around a thousand
the session-start hook and the commit gate start to be felt, and past two or
three thousand they stop being usable.

---

## Where the time actually goes

Two functions account for nearly all of it, and neither is doing anything
clever wrong — they are linear scans standing where a dictionary lookup would
do.

```python
# model.py:250
def active_edge(self, vid):
    for e in self.edges:            # O(E), early exit
        if e.src == vid and e.active:
            return e

# model.py:322
def depends(self, vid):
    return sorted({e.src for e in self.edges     # O(E), no early exit
                   if e.active and vid in e.to and e.src in self.vertices})
```

Everything else inherits from those:

- `children()` is one `active_edge`; `descendants()` calls it once per vertex
  reached → **O(V·E)**.
- `ancestors()` calls `depends()` once per vertex reached → **O(V·E)**, and
  about 6× slower than `descendants()` in absolute terms because `depends` has
  no early exit and must test membership in every `to` list.
- `roots()` and `unpropagated()` call `depends()` once per vertex → **O(V·E)**.
- `query.decision_lens.values()` (`query.py:682-693`) calls `active_edge`,
  `rival_answers` *and* `history` for **every field of every vertex** it is
  asked about. `select()` walks all ids, so a prose search is three edge-list
  scans per vertex → **O(V·E)**. This is why `find` and `descendants` have the
  same exponent and `find` has the larger constant.
- `validate()` does all of the above once, and then adds the cubic term at
  `model.py:571`.

A profile of `validate` on the 2,500-vertex store confirms the attribution
rather than inferring it — see [`results/validate-profile.txt`](results/validate-profile.txt).

---

## What a fix would be worth

`probe_index.py` re-times the same operations against a `Graph` subclass that
builds two dictionaries at construction — `src → active edge` and
`target → sources` — and changes nothing else. It is a probe, not a proposed
patch: nothing there is invalidated when the staging layer mutates a store, and
deciding where such an index dies is the actual design question. The point is
to put a number on the prize before anyone argues about the cost.

Building it costs 23 ms once on the 10,000-vertex store. What it buys, on the
same store and the same ops (full output in
[`results/index-probe.txt`](results/index-probe.txt)):

| op | scanning | indexed | speedup |
|---|---|---|---|
| `dg find <word>` | 49.7 s | 0.26 s | **189×** |
| `roots` | 11.7 s | 17 ms | 682× |
| `ancestors` | 9.4 s | 16 ms | 601× |
| `depths` | 22.4 s | 40 ms | 568× |
| `unpropagated` | 11.2 s | 34 ms | 333× |
| `descendants` | 1.47 s | 10 ms | 143× |
| `find waits:` | 12.0 s | 113 ms | 107× |
| `validate` (at 2,500) | 16.0 s | 0.12 s | 135× |

The exponent does not go away — an indexed `find` is near-linear in vertices
(α ≈ 1.2 across those two sizes) and an indexed `validate` is still quadratic,
since `ancestors` is still called once per PROVISIONAL vertex — but the
constant collapses by two orders of magnitude, which moves the ceiling from
~1,000 vertices to well past 10,000.

The shape of the fix, if it is ever wanted:

- **An index keyed by `from` and by `to`, built in `Graph.from_dict`.** That is
  the one construction path (`json_import` already routes through it), and it
  is where the edge list stops changing for read-only commands — which is every
  command in the READ panel.
- **Invalidation is the hard half, not the index.** `pending.apply_all` and the
  editor mutate `Graph.edges` in place. The honest version is to make the index
  a cached property cleared by any write, or to build it in `load()` and treat
  a mutated graph as un-indexed.
- **`stale_provisional` needs a different shape regardless.** Even with an
  index, calling `ancestors()` once per PROVISIONAL vertex is quadratic. One
  reverse BFS from the unsettled set answers *every* vertex's question in a
  single pass.
- **The empty-result vocabulary should be built from the rows already read**,
  or capped, rather than re-reading both stores from scratch.

None of this is required for the graphs this tool is used on today. It is what
would have to change before a store of several thousand decisions is realistic.

---

## What three of the fixes are worth without an index

The index above is the big prize and the hard decision — it has to say where a
cached structure dies, across twelve sites that mutate the edge lists in place.
Three smaller fixes need none of that. Each is confined to one function, or to
an object the caller already builds per invocation and drops, so there is
nothing to invalidate. `probe_easy.py` measures them, asserting answer-equality
before timing anything; full output in
[`results/easy-fixes.txt`](results/easy-fixes.txt).

| fix | | 2,500 | 10,000 |
|---|---|---|---|
| **A** | `find` prose, edge lookups cached on the lens | 5.6× | 5.7× |
| **B** | `stale_provisional` as one reverse walk | 4,505× | *(is the cubic term)* |
| **C** | `roots`, reverse adjacency built once | 261× | 682× |
| **C** | `unpropagated`, likewise | 98× | 325× |

**B is the cubic term, and it is a rewrite of one rule.** `model.py:571` asks
`provisional_because(vid)` — and so `ancestors(vid)` — once per PROVISIONAL
vertex. But a vertex rests on an unsettled premise exactly when it is reachable
along active edges *from* some unsettled vertex, so one walk down from the
unsettled set answers every vertex at once. At 2,500 that is 14.6 s → 3.2 ms.
It is not measured at 10,000 because its baseline *is* the 16.5-minute
`validate` already in the table above. This is the fix that changes `dg check`
from cubic to quadratic, and `dg check` is the one in the commit gate.

**C's ratios grow with size; A's do not, and that distinction is the point.**
A divides a constant by 5.7 and leaves the exponent alone — the cache removes
repeated lookups per vertex, but each first lookup is still a full edge scan.
C removes an exponent: baseline quadratic against a linear fix, so the gap
widens from 261× to 682× as the store quadruples. Both are worth having; only
one of them changes the shape of the curve.

**For `roots` and `unpropagated`, the four-line local fix is the whole index
win.** Not most of it — the same numbers. The index probe reports 11.7 s →
17 ms and 11.2 s → 34 ms on the 10,000-vertex store; `probe_easy` reports
11,634.5 → 17.0 and 11,067.6 → 34.0. Where the index still earns its keep is
`find`, which A improves 5.7× and the index improves 189×, because A never
stops the first scan per vertex from happening.

So the ordering that falls out is **B, then C, then A** — increasing blast
radius, and decreasing certainty about what it touches. B and C together take
`dg check` off the cubic curve and cost nothing in cached state.

**B and C were adopted**, and the rest of this report describes the tool as it
was before them. `Graph.validate` at 10,000 vertices is now 3.1 s rather than
988.9 s, `dg check` 21.6 s rather than ~17 minutes, and α is 1.99 — the cubic
term is gone and the quadratic one is not. A and D are still open and still
worth what is claimed above, except that D would no longer buy anything for
`roots` and `unpropagated`. The change, its falsifier and what pins it are in
[`HANDOFF.md`](HANDOFF.md#what-was-implemented-2026-08-29).

---

## Method

**The stores** (`gen_store.py`). A layered DAG: vertex `i` sits in layer
`i // width`, and its active edge targets `degree` vertices drawn from the next
two layers. Forward-only edges make it acyclic by construction, and the layer
count is fixed at 20 for every size — so a size sweep varies width and edge
count alone and is not silently a depth sweep as well.

The shape is constrained by what the tool accepts, not chosen freely:

- Unsettled decisions are sinks in the last layer, because `propagation`
  refuses a DECIDED vertex resting on an unsettled premise.
- BLOCKED and PROVISIONAL vertices hang off those sinks, which is the only
  arrangement where both statuses are legal *and* clean.
- 15% of decided vertices carry a superseded edge with its own answer,
  falsifier and `why`, so the archive is in the measurement — `dg find` reads
  it unless `--active` is passed.
- Tasks are generated at half the decision count, with `precedes` and
  `prompted` edges and a third of them linked `because` a decision. Stopped
  work is confined to the last layer, or `parked_holding_work` fires once per
  dependant and `dg check`'s cost becomes a rendering measurement.

Every generated store passes `dg check` with zero findings, at every size.

Marker words are planted so a search can be aimed: `quokka` in ~1% of answers,
`threshold` in most, `zzzabsent` in none.

**The timing** (`bench.py`). Two layers, because they answer different
questions: *library* calls `Graph`/`TaskGraph`/`dgraph.query` in-process on an
already-loaded store, which is the shape of the algorithm; *CLI* runs `dg …` as
a subprocess, which is what a person waits for and includes the 130 ms fixed
cost of interpreter and imports. That fixed cost is measured separately
(`cli.import_only`) and is flat across every store size.

Each op runs until 1.2 s of budget or 7 repetitions, whichever comes first;
best and median are both kept, and the tables quote the best. An op is skipped
at a size when its previous cost, extrapolated quadratically, would exceed 60 s
— which is why `validate` shows as skipped at 5,000 and 10,000 in the main
table and was re-measured separately. Skipped cells name their projection.

**Repeatability.** The 1,000-vertex library sweep was run twice, in separate
processes ~20 minutes apart: 33 ops, median drift 1.8%, worst 9.9%
(`depends.mid`, a 0.1 ms measurement). The exponents are safe to two decimal
places; individual millisecond figures are not.

**Caveats.** One machine, no CPU pinning, no isolation from background load.
The generated prose is synthetic — real answers are longer and more varied,
which would raise `find`'s constant but not its exponent, since the exponent
comes from the edge scans and not from the text matching. The projected figures
in the tables are quadratic extrapolations and therefore *understate* anything
cubic; where a cubic number mattered it was measured instead.

---

There is unfinished work following on from this: see
[`HANDOFF.md`](HANDOFF.md) for the four candidate fixes, what has been measured
of them, and what a next session should pick up.

## Files

| file | what |
|---|---|
| `HANDOFF.md` | Where the efficiency work stopped, and what to do next |
| `gen_store.py` | The synthetic store generator |
| `bench.py` | The sweep: library + CLI + density |
| `analyse.py` | `raw.json` → the tables, with fitted exponents |
| `probe_index.py` | What a full edge index would be worth |
| `probe_easy.py` | The three local fixes that need no invalidation story |
| `results/tables.md` | **Every measured number**, as tables |
| `results/raw.json`, `results/results.csv` | The main sweep |
| `results/density/` | The density sweep |
| `results/validate-profile.txt` | Where `validate` spends its time |
| `results/index-probe.txt` | The index probe's output |
| `results/easy-fixes.txt` | The three local fixes, at 2,500 and 10,000 |
| `results/after-fixes.txt` | The acceptance re-run once B and C were in |
| `results/round-two.txt` | The acceptance re-run after everything else |
| `results/final.txt` | The last one, after fix D |
| `spike_serve.py`, `spike_density.py` | Whether a library pays in `dg serve`, and on denser graphs |
| `results/graph-library.txt` | Pricing networkx and rustworkx, and why the answer is no |
| `spike_library.py`, `spike_library_fair.py` | That spike |
| `results/after/` | Its raw data, alongside the baseline `results/raw.json` |

## Reproducing

**Every "before" number here is against the tag `efficiency-study-baseline`**
(`fef6e25`), which is `main` as it stood before any of this work. Nothing in
`results/` compares against the previous commit; they all compare against that
one tree, which is the only measurement in the project that never rebaselines
— and the reason a lens cache 4.7x slower than the tool had ever been was
caught while 1,981 tests passed.

```sh
git show efficiency-study-baseline --stat | head -3    # the tree behind the "before" column
```

```sh
cd tools/dear-guide

# the main sweep — about 25 minutes
python3 benchmarks/bench.py --work /tmp/dg-bench --out benchmarks/results \
    --sizes 200 500 1000 2500 5000 10000 --degree 6 \
    --cli-sizes 200 1000 5000 10000 --densities 2 4 6 8 16 \
    --cap 60 --deadline 1800

# the tables
python3 benchmarks/analyse.py --raw benchmarks/results/raw.json

# one store on its own, to poke at by hand
python3 benchmarks/gen_store.py /tmp/dg-bench/n1000 --vertices 1000 --degree 6
cd /tmp/dg-bench/n1000 && dg render && dg check

# the three local fixes -- about 3 minutes for the pair
python3 benchmarks/gen_store.py /tmp/dg-bench/n2500d6  --vertices 2500  --degree 6
python3 benchmarks/gen_store.py /tmp/dg-bench/n10000d6 --vertices 10000 --degree 6
python3 benchmarks/probe_easy.py --store /tmp/dg-bench/n2500d6  --reps 2
python3 benchmarks/probe_easy.py --store /tmp/dg-bench/n10000d6 --reps 1 --skip B
```

The generated stores are ordinary projects — `DG_PROJECT=/tmp/dg-bench/n1000 dg
find …` works on them like any other.
