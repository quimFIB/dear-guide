# Handoff — the `dear-guide` efficiency study

Written 2026-08-29, at the end of the session that produced [`README.md`](README.md).
Updated later the same day: the measurement of the three local fixes is now
**finished at both sizes** and written up — see [What was picked
up](#what-was-picked-up-2026-08-29) below. The measurement phase is complete;
**nothing has been implemented in `dgraph/`**, which remains the open decision.

Read [`README.md`](README.md) first — it is the findings. This file is only
what a next session needs in order to carry on without re-deriving anything.

---

## State in one paragraph

Everything in `dear-guide` that touches the graph is quadratic in store size
(α ≈ 2.00, measured), and `dg check` is cubic (α = 3.03, measured: 989 s on a
10,000-vertex store). The cause is that the graph is a flat `list[Edge]` with
adjacency re-derived by linear scan on every lookup — `Graph.active_edge`
(`model.py:250`) and `Graph.depends` (`model.py:322`). Four candidate fixes
were identified. **Three of them — A, B and C — are now implemented**, along
with four more this file did not name, and the whole of [Round
two](#round-two-2026-08-29-later) below. Nothing is cubic: `dg check` went from
~17 minutes to 21.6 s on the 10,000-vertex store and its exponent from 3.03 to
1.92. **D, the full edge index, is the only candidate here left undone**, and
deliberately — it was deferred pending the library spike, which has now
reported. The paragraph below describing the cost is the state *before* any of
it, and is kept because it is what every measurement was taken against.

---

## What is finished and trustworthy

| | |
|---|---|
| `README.md` | The findings report. Numbers in it are measured unless it says projected. |
| `results/tables.md` | Every measured number, with fitted exponents. |
| `results/raw.json`, `results/results.csv` | Main sweep: 6 sizes × ~33 library ops + 4 sizes × 10 CLI ops. |
| `results/density/` | Density sweep, 1,000 vertices × out-degree 2/4/6/8/16. |
| `results/validate-profile.txt` | `validate` at 5,000 (121 s) and 10,000 (989 s), plus a cProfile at 2,500. |
| `results/index-probe.txt` | The full-index probe at 2,500 and 10,000. |
| `results/sweep.log`, `results/phase2.log` | Raw console output of the runs. |
| `gen_store.py`, `bench.py`, `analyse.py`, `probe_index.py` | All run to completion; reproduction commands are at the bottom of `README.md`. |

Repeatability was checked: the 1,000-vertex library sweep was run twice in
separate processes, 33 ops, median drift 1.8%. Exponents are good to two
decimals; individual millisecond figures are not.

**The generated stores are gone.** They lived in the session scratchpad
(`/tmp/claude-*/…/dgbench/stores/`, 55 MB across ten stores) and do not
survive. Regenerate before running any probe:

```sh
cd tools/dear-guide
python3 benchmarks/gen_store.py /tmp/dg-bench/n2500d6  --vertices 2500  --degree 6
python3 benchmarks/gen_store.py /tmp/dg-bench/n10000d6 --vertices 10000 --degree 6
```

Generation is deterministic (`seed=7` for decisions, `11` for tasks), so the
stores come back byte-identical and the old numbers stay comparable. The
10,000 one takes a few seconds and is 9.1 MB.

---

## What is unfinished

### `probe_easy.py` — done

**This is finished.** Both sizes are measured, the `--skip` branch has been
exercised, and the output is in [`results/easy-fixes.txt`](results/easy-fixes.txt)
with a section added to `README.md`. See [What was picked
up](#what-was-picked-up-2026-08-29) at the end of this file for the numbers and
the two probe caveats it turned up. Nothing here is outstanding.

### B and C are implemented; A and D are not

`dgraph/model.py` and `tests/test_dgraph.py` are changed — see [What was
implemented](#what-was-implemented-2026-08-29) for the diff's shape, the
numbers and the falsifier.

**A and D remain untouched and remain worth what the sections below say**, with
one correction to D's case: for `roots` and `unpropagated` the index would now
buy nothing, because C already reaches its numbers. D's remaining prize is
`find`, and A's 5.7× is the cheap partial version of it.

## The four fixes, ranked by (measured benefit ÷ risk)

### B — `stale_provisional`: one reverse walk. **Do this first.**

`model.py:571`. The rule calls `provisional_because(vid)` → `ancestors(vid)`
once per PROVISIONAL vertex, and each `ancestors` is already an O(V·E) walk.
Vertices × walk × scan = V³. The cProfile at 2,500 is unambiguous: `depends`
accounts for 15.9 of `validate`'s 16.4 seconds, and 15.4 of those are reached
through **25 calls to `ancestors`** — one per PROVISIONAL vertex.

A vertex rests on an unsettled premise exactly when it is reachable along
active edges from some unsettled vertex, so one walk down from the unsettled
set answers every vertex at once. `stale_provisional_once` in `probe_easy.py`
is that walk, asserted equal to the current answer.

This removes the cubic term outright and is the largest single user-facing win,
because `dgraph/gate.py` runs `check.errors` on **every commit** and the
session-start hook runs `dg brief`, which validates too.

### C — `roots` and `unpropagated`: build reverse adjacency once per call

`model.py:383` and `model.py:395`. Both call `depends` once per vertex, and
each call rescans every edge. Building a `defaultdict(set)` at the top of the
function and reading it in the loop is a four-line change *inside each
function* — the structure is created and dropped within the call, so there is
nothing to invalidate. 234× and 82× at 2,500 vertices.

`validate` calls `unpropagated` too, so this compounds with B.

### A — cache the per-vertex edge lookups on the query lens

`query.py:669-707`. `values(vid, name)` calls `active_edge`, `rival_answers`
**and** `history` — and it is called **once per field per vertex**. The
decision lens has seven prose fields, so a single `dg find <word>` does roughly
twenty full edge-list scans per vertex.

`rival_answers` is the sharpest edge of this: it is
`[e for e in self.edges if e.src == vid and e.active][1:]` — a full scan with
no early exit, run seven times per vertex, to return `[]` in every store the
tool can actually write (`one_active_edge` refuses two).

The fix is a per-vertex cache **on the lens**, and the safety argument is
already in the codebase: `query._walk`'s memo lives there for exactly this
reason, and its comment says why — the lens "is built per invocation and
dropped with it… nothing is written down, and nothing outlives the question."
5.6× at 2,500 vertices.

### D — the full edge index. Biggest win, real design question.

`probe_index.py` measures a `Graph` subclass that builds `src → active edge`
and `target → sources` at construction: 23 ms once on the 10,000-vertex store,
and then `dg find` goes 49.7 s → 0.26 s (189×), `roots` 682×, `ancestors` 601×.

**The blocker is invalidation, not the index.** Twelve sites mutate the edge
lists in place, all outside `model.py`:

```
dgraph/pending.py:1682,1732,1767,1779,1826,1841
dgraph/md_import.py:139,149,152
dgraph/task_pending.py:107,124,165
```

Two shapes worth weighing:

- Build it in `Graph.from_dict` (the single construction path — `json_import`
  already routes through it) and make it a cached attribute that any write
  clears. Correct, but every one of those twelve sites has to remember.
- Build it lazily on first read and have the write paths drop it. Fewer places
  to get wrong, at the cost of a rebuild after each staged op.

Note that even with an index, `validate` stays quadratic unless B is done too
— `ancestors` per PROVISIONAL vertex is still V walks. **B is not made
redundant by D.**

### Also noted, not measured

`query.words()` (`cli.py:821`) re-reads every piece of prose in **both** stores
to build the *did you mean* vocabulary, and it runs only when a search finds
nothing. That makes an empty search cost roughly twice a successful one —
23.3 s vs 11.8 s at 5,000 vertices. The typo is the worst case, which is
backwards. Build the vocabulary from rows already read, or cap it.

---

## Should this use a graph library?

*Superseded — this was reasoned, not measured. It was spiked properly later
and the conclusion held, but for different reasons and with a 159× speedup
falling out of it: see [Should this use a graph library? — asked again, and
priced](#should-this-use-a-graph-library--asked-again-and-priced) and
`results/graph-library.txt`. Kept because the argument below is still the
shape of the answer.*

Checked, and the answer is no. There is no `networkx`, `igraph`, `rustworkx` or
`scipy` anywhere in the tree; `pyproject.toml` declares exactly two runtime
dependencies (`typer`, `rich`) and `model.py`/`query.py`/`tasks.py` import only
`json`, `re`, `dataclasses` and `collections.Counter`.

Adding one would not help, for a reason the measurements make concrete: the
workload is not algorithmically hard. 10,000 nodes and 59,000 arcs is a small
graph, and every walk here is a plain reachability query. The cost is entirely
in *re-deriving adjacency from a flat list on every lookup* — and any library
would need that adjacency built first, which is the fix. The probe's two plain
dicts already beat what a library call would cost, and they add nothing to a
dependency list the project has deliberately kept at two.

Where a library would earn its place is a different problem than this one:
layout for the web view, or algorithms the tool does not currently have.
Neither is on the table.

---

## Suggested order for the next session

1. ~~Regenerate the two stores (deterministic, ~10 s).~~ **Done** — and they
   come back byte-identical, so the old numbers stand.
2. ~~Run `probe_easy.py` at 10,000 with `--skip B`; check the `--skip` branch
   actually works. Save to `results/easy-fixes.txt`.~~ **Done** — see below.
3. ~~Decide whether to implement. If yes, **B then C then A**~~ — **B and C
   are implemented**; see [What was implemented](#what-was-implemented-2026-08-29).
   `dg check` on the 10,000-vertex store went from ~17 min to 21.6 s. A and D
   are still open, and D's case is now narrower than it was.
4. ~~Each fix needs a test that pins the *answer*, not the speed.~~ **Done for
   B and C** — seven tests in `tests/test_dgraph.py`, mutation-checked. Note
   that `probe_easy.py`'s own assertions turned out to be weak: on the
   generated stores `stale_provisional` returns `[]` both ways, so the probe
   was comparing two empty lists. The tests use a seeded random corpus instead
   and assert the rule actually fires.
5. ~~`dg check` timing is the acceptance criterion. Re-run `bench.py` and
   compare against `results/raw.json`.~~ **Done** —
   [`results/after-fixes.txt`](results/after-fixes.txt), raw data in
   `results/after/`. `dg check` at 5,000 went 132.5 s → 13.8 s, and nothing in
   the sweep got slower.
6. ~~Record the decision in `dear-guide`'s own graph — with a falsifier.~~
   **Not possible: `dear-guide` has no decision graph of its own.** There is no
   `decisions.json` at the repo root or above it; the only two in the tree are
   `demo/` and `demo-agentic/`, which are fixtures. The tool does not currently
   dogfood itself, and creating a graph uninvited is the one thing its own
   skill says not to do. The falsifier is written down in [What was
   implemented](#what-was-implemented-2026-08-29) instead, and in the commit
   message. **Whether `dear-guide` should track its own decisions is a question
   for a person**, and it is the natural home for this record if the answer is
   yes.


---

## What was picked up (2026-08-29)

Steps 1 and 2 above, and nothing beyond them: **`dgraph/` is still untouched.**

**The stores regenerate byte-identically** (9,105,629 B for the 10,000-vertex
`decisions.json`, matching the 9.1 MB the report quotes), so everything here is
directly comparable with `raw.json`.

**The 2,500 run reproduces the earlier session's numbers** — baselines within
3.2%, the worst being B at 14,133.5 → 14,590.3 ms. The *ratios* move more (B
4,995× → 4,505×, C roots 234× → 261×) purely because their denominators are
3–7 ms measurements. Treat those as order-of-magnitude claims.

**At 10,000 vertices, with `--skip B`:**

```
fix                              now (ms)   after (ms)   speedup
A  find prose (lens cache)       47,472.4      8,351.9      5.7x
C  roots                         11,634.5         17.0    682.4x
C  unpropagated                  11,067.6         34.0    325.2x
```

Two things this settles that the 2,500 run could not:

- **A is a constant-factor win and stays one** — 5.6× at 2,500, 5.7× at 10,000.
  It divides the constant and leaves the exponent alone.
- **C removes an exponent** — 261× → 682× and 98× → 325× as the store
  quadruples. The gap widening *is* the evidence.
- **For `roots` and `unpropagated`, C reaches the same numbers as the full
  index.** `index-probe.txt` has 11.7 s → 17 ms and 11.2 s → 34 ms; this probe
  has 11,634.5 → 17.0 and 11,067.6 → 34.0. Fix D's remaining advantage over
  A+C is essentially `find` alone (189× vs A's 5.7×). That narrows what the
  invalidation decision has to buy.

**The `--skip` branch works.** Verified on a 200-vertex store before the big
run was trusted: `--skip B` drops the B row, `--skip b` does the same
(arguments are upper-cased), `--skip A B C` prints an empty table and exits 0.
Two flaws in the probe found while checking, left as-is and documented rather
than fixed, since they mislead rather than corrupt:

1. **A's correctness check runs outside the `--skip` guard.** The two
   `selected()` calls sit below the `if "A" not in skip` block, so `--skip A`
   still pays one full baseline `find` — ~50 s at 10,000. The flag is not as
   cheap as it looks.
2. **`--skip A B C` still prints "every variant asserted to return the
   identical answer"** beneath an empty table. Only A's assertion ever runs
   unconditionally; B's and C's live inside their guards.

Step 3 — whether to implement, and in what order — is unchanged and still open.
The evidence above only sharpens it: **B then C** is the high-value, low-risk
pair, they need no cached state, and together they take `dg check` off the
cubic curve. A is a separate, smaller call, and D's case now rests mostly on
`find`.

---

## What was implemented (2026-08-29)

**B and C are in `dgraph/model.py`. A and D are not**, and the argument for
each is unchanged above.

| | |
|---|---|
| `Graph.stale_provisional()` | New. One walk down from the unsettled set, replacing one `ancestors` walk per PROVISIONAL vertex. `validate` calls it. |
| `Graph._reverse()` | New, private. `target -> sources` in one pass. Built inside a call and dropped — never cached on the graph, so no write path has to clear it. |
| `Graph.depends(vid, _into=None)` | Takes an optional reverse index. Passing nothing computes it exactly as before. |
| `Graph.waiting_on(vid, _into=None)` | Passes the index through, so `unpropagated` keeps *one* implementation of the rule rather than a second copy. |
| `Graph.roots()`, `Graph.unpropagated()` | Build the index once per call instead of asking `depends` once per vertex. |
| `Graph.ancestors(vid, _into=None)` | Likewise, so a caller doing many walks pays for the adjacency once. |
| `Graph.provisional_because(vid, _into=None)` | Passes the index down to `ancestors`. |
| `Graph.provisional_causes()` | New. Every PROVISIONAL vertex's unsettled premises, over one shared index. |
| `brief.attention()` | Calls it, instead of `provisional_because` per vertex. |

### A third cubic site, which the handoff had misattributed

The last two rows are **beyond the literal B+C**, and were added because the
acceptance run showed why. This file said the session-start hook was slow
because "`dg brief` … validates too" — so fixing `validate` should have fixed
it. It did not: after B and C, `dg check` was 3.7 s at 2,500 vertices and
`dg brief` was **33.7 s**, with a fitted exponent of **2.67**.

A profile said why. `brief.attention` (`brief.py:64`) calls
`provisional_because` once per PROVISIONAL vertex *directly* — 14.2 s of
`brief`'s 18.0 s, through 25 `ancestors` calls, the same cubic shape in a
second place. B removed it from `validate` and could not remove it from here,
because `attention` needs *which* premises hold each vertex and
`stale_provisional` only answers *whether* any do.

`provisional_causes` is the narrow fix: still one walk per vertex, so still
quadratic, but sharing one reverse index across them drops the rescan.
`dg brief` at 2,500 went 33.7 s → 4.0 s, its exponent 2.67 → 1.92, and it now
completes at 5,000 and 10,000 where the harness had been skipping it.

`pending.py:1452` asks about one vertex and is left alone. **`server.py:113`
asks it once per vertex for the whole payload** — the same cubic shape, in
`dg serve`, which was never in the sweep. Unmeasured, not known-slow;
`provisional_causes` is the fix if it turns out to matter.

### What it bought

Measured on the same regenerated stores, `validate` in-process:

| vertices | before | after | |
|---|---|---|---|
| 2,500 | 16.33 s | **0.197 s** | 83× |
| 5,000 | 121.4 s | **0.785 s** | 155× |
| 10,000 | 988.9 s | **3.117 s** | **317×** |

α over the top two sizes is **1.99** — the cubic term is gone and the
quadratic one remains, which is exactly what the analysis predicted and the
reason A and D are still worth what they were worth.

At the CLI, on the 10,000-vertex store: `dg check` goes from ~17 minutes to
**21.6 s**, and `dg brief` — which the harness had been skipping — completes in
**27.6 s**.

The full acceptance re-run is [`results/after-fixes.txt`](results/after-fixes.txt),
with the raw data in `results/after/`. Its headline: across 117 ops comparable
against the baseline, **nothing got slower** — the lowest ratio is 0.97×, on
ops the change does not touch, inside the drift the report already documents.
The exponents:

| | before | after |
|---|---|---|
| `Graph.validate` | 3.03 | **1.99** |
| `dg check` | 3.03 | **1.90** |
| `dg brief` | — | **1.92** (2.67 before the `brief` call site was fixed) |

### Where the time went instead

`dg check` is 21.6 s while `validate` is 3.1 s, so the remaining cost is no
longer in `model.py`. A profile at 5,000 vertices puts it in two other places,
both the same flat-list-scan shape this fix removed from the decision graph:

- `cross.rests_on` and `cross.evidence` (`cross.py:30-37`) scan **every task**
  for **every decision id** — 2.4 s of 5.7 s.
- `TaskGraph._out` / `_in` (`tasks.py:636,651`) scan **every task edge** per
  task — 1.2 s.
- `Graph.active_edge` is still 1.4 s, which is fix D's territory.

None of that is regression: it was always there, hidden underneath a cubic
term. It is where the next measurement should start.

**It did, and all three were fixed** — `cross.reverse`, `TaskGraph._adjacency`
and the lens cache. See [Round two](#round-two-2026-08-29-later).

### The falsifier

**This is slower on a store with a large edge list and nothing PROVISIONAL.**
The old rule short-circuits — no PROVISIONAL vertices means no walks at all —
while the new one always pays one pass over the edges to build the adjacency.
Measured, on the 10,000-vertex store with every vertex forced to OPEN:

```
new stale_provisional()    23.10 ms
old equivalent              1.33 ms      <- 17x faster
```

So the trade is a **~22 ms regression** in that shape against a **986-second**
improvement in the ordinary one, and it is bounded by the edge count rather
than growing with it. What would overturn this decision is a real store where
that shape is the common case and 22 ms is a cost somebody feels — at which
point the fix is to skip the walk when no vertex is PROVISIONAL, not to revert.

The same asymmetry applies to `unpropagated` on a store with no DECIDED
vertices, for the same reason and at the same scale.

### What pins it

`tests/test_dgraph.py` gained 8 tests, and they pin the **answer**, not the
speed: each states the definition the rewrite replaced and asserts the two
agree. A speed assertion would fail on a loaded machine and pass on a rewrite
that returns nonsense quickly.

The one that does the real work is
`test_the_rewrites_agree_on_awkward_random_graphs` — 200 seeded random graphs
with cycles, rival active edges, dangling targets and edges from ids that name
no vertex, asserting all three rewrites against their definitions, and
asserting the rule actually fired more than 50 times so the corpus cannot go
quietly empty. Both halves were checked by mutation: making `stale_provisional`
return `[]` fails three tests, and making the walk follow only the *first*
active edge — the `children` trap the docstring warns about — fails two.

That second mutation is the one to keep in mind: the first version of
`test_stale_provisional_follows_rival_active_edges` did **not** catch it,
because the fixture chain D01 → D02 → D04 → D05 already reaches every fixture
vertex by the first edge. It needed a vertex nothing else points at.

`probe_easy.py`'s own assertions would not have caught either mutation: on the
generated stores `stale_provisional` returns `[]` both ways, so the probe was
comparing two empty lists. A probe that asserts equality is not the same thing
as a test.

---

## Round two (2026-08-29, later)

Eight further candidates were put up, seven taken. The pattern is the same one
throughout: **a derived structure built inside the call that needs it and
dropped with it** — never cached on the object, so no write path has to
remember anything. That constraint is what made all of these takeable without
answering fix D's invalidation question.

| | what | result |
|---|---|---|
| `server.graph_payload` | all depths in one pass; `provisional_causes` | 139,811 ms → **117 ms** at 1,000 (1,193×) |
| `TaskGraph._adjacency` | group task edges by `(src, kind)` once | `blocked_ids` 103.7 → **5.9 ms** at 2,500 |
| `cross.reverse` | decision → task maps in one pass | `rests_on` × V 434.6 → **1.5 ms** at 5,000 |
| `decision_lens` | per-vertex edge lookups, cached lazily | `dg find` 48.7 s → **7.75 s** at 10,000 |
| `Graph.ancestors` | build the reverse index inside the walk | 3,336 → **21 ms** at 10,000 (159×) |
| `Graph.descendants` | a forward index, for the same reason | **8.9 ms** at 10,000 |
| `stale_provisional`, `unpropagated` | short-circuit when there is nothing to find | closes the falsifier below |

### `dg serve` was the worst thing in the tool, and nobody had measured it

`graph_payload` builds a `derived` block per vertex, and three of its entries
recomputed the whole graph each time. At 1,000 vertices it took **139.8
seconds**; at 2,500 it did not finish. That is worse than `dg check` ever was,
and it is the one a person sits in front of waiting.

`depth` was 99.2 s of it. `depths(vid)` already computes every ancestor's depth
on the way and hands back the memo — and `depth(vid)` throws all of it away to
keep one number, so asking for every vertex walked the graph once per vertex.
`provisional_because` was the other 42.2 s, the same cubic shape `dg brief` had.

It was never in the sweep, which is why the study missed it. **An operation
nobody measured was two orders of magnitude worse than every operation
everybody measured.**

### The regression the sweep caught, which the tests did not

The lens cache was written to fetch the active edge, the rival answers and the
history together. Most callers want one of the three, and they are not equally
cheap: `active_edge` stops at the first match, `rival_answers` is a full scan
with no early exit. So `is:terminal` went from one early-exiting scan to three
full ones — **4.7× slower than before any of this work**:

```
find.is_terminal    5,000 vertices    398.9 ms -> 1,884.9 ms    0.21x
find.field_status   5,000 vertices  1,117.2 ms -> 1,908.1 ms    0.58x
```

Every test passed. The answers were right; only the cost was wrong, and no test
asserts cost. Splitting it into three caches filled on demand fixed it
(`is:terminal` now 333.8 ms, better than baseline), and the test that pins it
wraps `rival_answers` and `history` to record their calls and asserts
`is:terminal` makes none.

**Keep the acceptance sweep.** It is the only thing in this project that
compares against the tool as it was rather than against the last commit, and it
is what turned a green suite into a caught regression.

### The falsifier from round one is closed

`stale_provisional` and `unpropagated` now start no walk when nothing is
PROVISIONAL, or nothing DECIDED — the short-circuit the per-vertex forms had
for free. On the 10,000-vertex store forced all-OPEN: 23.10 → 1.56 ms, against
the old form's 1.33 ms. The tests are structural, replacing the edge list with
one that raises when iterated.

### What the sweep says

`results/round-two.txt`, raw data in `results/after2/`. Across 117 ops
comparable against the tool before any of this work: **62 improved, 55
unchanged, 0 regressed.** Exponents, all measured:

| | before | after |
|---|---|---|
| `Graph.validate` | 3.03 | 1.99 |
| `dg check` | 3.03 | 1.92 |
| `dg brief` | — | 1.89 |
| `dg context` | ~2.0 | 1.88 |
| `dg render` | ~2.0 | 1.85 |
| `dg find <word>` | ~2.0 | 1.64 |

### Should this use a graph library? — asked again, and priced

The answer is still no, but the earlier section argues it from the wrong
place. Spiked properly against networkx 3.6.1 and rustworkx 0.18.1:
`results/graph-library.txt`.

The short version: the first measurement showed a 1,350× win for the library
on `ancestors`, and **that was an index result, not a library result**. Against
a graph that keeps an index — which is what the fix does — networkx is at
parity and rustworkx is 7×. Set against a compiled per-platform wheel in a
two-dependency Claude Code plugin, plus three translation problems that do not
go away (the two directions are not transposes; edge payloads live outside a
digraph; superseded edges and dangling targets must be kept and skipped), 7× on
walks that are no longer the bottleneck does not pay.

What the spike *did* buy is the 159× on `ancestors` — pricing the library is
what exposed that it had no index. **A spike that returns "no" and a speedup is
worth running.**

### What is left

- **Fix D, the edge index**, deliberately deferred until the spike reported.
  It now has a narrower case than the study gives it: `roots`, `unpropagated`,
  `ancestors` and `descendants` all reach index speed without it. What is left
  is `active_edge` inside `dg find`, which is real but is a constant, not an
  exponent.
- **A graph library for `dg serve`'s layout** — the one place it clearly earns
  its keep, and a different problem from this one.
- **Nothing is linear.** Every remaining exponent is between 1.6 and 2.0, and
  the ceiling has moved from about 1,000 vertices to comfortably past 10,000.
  Whether that is worth pushing further is a question about what store sizes
  are real, not a question about the code.

---

## Round three: fix D, and the library asked a third time

### Fix D had no blocker

The whole of this file treats the edge index as the fix that needs an
invalidation decision — twelve mutation sites, a cached attribute, a question
about where the structure dies. **That was an assumption, not a measurement.**
It assumed the index had to be *cached* to be worth building.

Grouping 11,000 edge records by source costs **0.7 ms**, which is less than one
of the scans it replaces. So it is built inside the call and dropped, like
everything else here — no cached attribute, no write path touched, none of the
twelve sites changed.

    dg find quokka      10,000 vertices   48.71 s -> 0.54 s     (90x)
    dg find zzzabsent   10,000 vertices   (never finished) -> 0.94 s
    server payload      10,000 vertices   10.8 s -> 3.2 s

It was found by pricing a graph library, not by looking for it. `dg find` was
assumed to be text-bound; a profile put **85% of it in four edge readers and
0.045 s in the actual text matching**. What remained was not a graph algorithm
at all — it was *"hand me the records attached to this vertex, with their
payloads"*, which a digraph discards by design.

### The library, asked a third time and answered properly

`results/graph-library.txt` now has both halves. The short version:

| what looked like a library win | what it actually was | fix |
|---|---|---|
| `ancestors` 1,350× | a missing reverse index | 159× |
| `dg find` "is text-bound" | ungrouped edge records | 90× |
| `provisional_causes` 11× | one upward walk per vertex | 16× |

Three times a measurement showed a large library win, and three times it was an
algorithm gap in the code being compared against. The `dg serve` spike is the
sharpest: it first said **yes, 1.34× on the payload, build repaid immediately**
— a genuine positive result. Replacing the walk-per-vertex with one topological
pass carrying a bitset then made **plain Python faster than rustworkx** at the
same question (63 ms against 101 ms), and the second run of the spike put the
library at 146.5 ms against the current 139.3 ms — marginally *slower*, with
nothing left to win.

rustworkx is still genuinely faster at graph algorithms — 2.4× on `all_depths`,
1.5× on `depends`. Those are now 139 ms of a 3,151 ms payload. **The other 96%
is building the payload dict, which no graph library touches.**

**That is the case for spiking a dependency you expect to refuse.** The verdict
was never the value; being made to look closely at what you already have was.

### What is actually left

- **`dg serve`'s payload is 96% payload construction** — 3.0 s of 3.15 s at
  10,000 vertices, building Python dicts and serialising 11 MB of JSON for a
  page that renders a viewport. Every graph question underneath it is now
  answered in 139 ms. If `dg serve` is to get faster the lever is **sending
  less**, not computing faster, and that is a design question about the page
  rather than an optimisation.
- **A library for layout.** Unchanged, untested, and the one place the case is
  clean.
- **Nothing is cubic and nothing is linear.** Every exponent sits between 1.6
  and 2.0 except `dg find`, which is now dominated by interpreter start-up
  (244 ms) and JSON parsing (106 ms) rather than by store size.

---

## Round four: the renderers, and where the audit stopped

This round began with a question worth writing down, because it was the right
question and its first answer was wrong: **do `dg check` and `dg brief` need
to be quadratic?** Transitive reachability has a genuine lower bound, so a
validator sitting at α ≈ 1.85 with every check linear looks finished.

It was not finished, and the quadratic term was not in any check.

### `dg check` rebuilds both views to see whether they are stale

So the renderers' cost is the commit gate's cost. And both renderers asked
`depends`, `active_edge`, `rival_answers`, `prerequisites`, `unblocks`,
`waiting_on`, `ready`, `discovered_during` and `prompted` **once per record,
with no index** — the pattern this whole branch is about, one layer further
out, in the code that writes the file rather than the code that answers the
question.

Three more per-record questions fell out of chasing what looked like a
regression and turned out to be drift:

| | | |
|---|---|---|
| `find is:orphaned` | 3,369 ms → **17 ms** | 5,000 vertices |
| `find waits:<mid>` | 2,787 ms → **8 ms** | 5,000 vertices |
| `TaskGraph._in` | 1,641 ms → **61 ms** | inside `dg check` |

`waits:` is the one worth understanding. *"Who rests on `arg`?"* was asked of
every vertex in turn; read from `arg`'s own edge records it is a single
lookup. It has to union **every** active edge rather than follow the first —
`depends` sees them all, so a rival answer's targets rest on `arg` too, and
`children` would miss them.

### Where the lower bounds actually are

Since "this has to be quadratic" was the wrong answer twice, here is where it
is the right one:

- **`provisional_causes` is genuinely quadratic**, in (PROVISIONAL ×
  unsettled), because that product *is* its output size. Unavoidable if the
  sets are wanted. `validate` does not want them: `stale_provisional` needs one
  boolean per vertex and gets every one from a single reverse walk.
- **All-pairs reachability** would be Θ(V·E), essentially
  matrix-multiplication-hard. Nothing here asks it.
- **Everything else** — roots, unpropagated, cycle detection, both renderers —
  is Θ(V+E) work producing Θ(V+E) output.

Nothing on a hot path was anywhere near a bound.

### Why the audit stopped here, and not because it ran out

**Six passes, and every one ended with a profile that looked settled.** Each
time the next question turned up another instance of the identical pattern.
The reason is mundane and worth knowing: indexing one call site inside a
function does not index its siblings, and nothing reveals that except
measuring again.

So "the profile looks clean" is not a stopping rule — it was true, and wrong,
six times. The rule used instead is a threshold: `dg check` on 5,000
decisions is 1.56 s against the 132.5 s it started at, α is 1.33, and the
largest single entry in its profile is 61 ms of a 731 ms total. That is past
any bar worth arguing about, and the remaining wins are small.

**The pattern to look for, if anybody picks this up again**, is the one that
turned up five separate times: *a function that computes the whole answer, a
caller that keeps one row, and a loop around the caller.* `Graph.depth` over
`depths`, `provisional_because` per vertex, `late_evidence` per decision,
`depths` itself, and every renderer accessor. If there is a seventh, that is
its signature.

### What is deliberately not done

- **`dg serve`'s payload is 96% payload construction** — building Python dicts
  and serialising 11 MB of JSON for a page that renders a viewport. Every graph
  question underneath it is milliseconds. That is a design question about what
  the page should be sent, not an optimisation, and it is the right place for
  anybody continuing.
- **The edge index is never cached.** Every structure here is built inside the
  call and dropped. It stays that way unless somebody measures a case where the
  build actually hurts — the whole reason fix D was takeable is that nobody had
  measured it (0.7 ms) before assuming it needed a lifetime.
