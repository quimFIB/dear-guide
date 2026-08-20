# Quick start: the `dg` CLI

Ten minutes from an empty directory to a decision graph that fails CI when it
starts contradicting itself.

## Install

From your checkout of this repository:

```sh
cd /path/to/dear-guide
pip install -e .
```

`dg` then works in any project directory containing `decisions.json`. The
project is found by walking up from the cwd, or named explicitly with
`--project PATH` (`-C`) or `$DG_PROJECT`.

## 1. Start a graph

Areas are just grouping labels for the rendered view. Pick a few; you can only
use ones you declared here.

```sh
cd my-project
dg init --areas "Search,Serving,Index"
```

Two files appear: `decisions.json` (the store — source of truth) and
`decision-graph.md` (a generated view — **never hand-edit it**).

`dg init` also adds the tool's scratch files to `.gitignore` — the staging
trays, the compose buffer, the lock files beside them, and the temp file an
interrupted write leaves behind:

```
# development-graph: staging, locks and temp files
.dgraph-*
*.dg-tmp
decisions.json.lock
tasks.json.lock
```

It says so when it does. If you are adding the tool to a project that already
has a store, paste that block in yourself: several of the tool's own messages —
"`.dgraph-pending.json` is gitignored, so committing now drops them from the
record" — are only true once it is there.

## 2. Add the questions

A vertex is a **decision the project must make** — a question, not a task.
(Work belongs in the task graph, which is separate: `dg task --help`, and
[how it works](how-it-works.md#tracking-the-work-as-well).)

```sh
dg add --id D01 --title "Exact or approximate search?" --area Search
dg add --id D02 --title "Which distance metric?" --area Search --after D01
```

`--after D01` says D02 depends on D01 — you cannot pick a metric before you know
whether you are scoring exhaustively or walking a graph, because a scan can
normalise at query time and an HNSW graph bakes the metric into its edges at
build time. Dependency is the graph structure, never a stored field, so this is
the only place it is written down.

## 3. Staging: nothing is written until you say so

Everything above is *staged*, not applied. This is the one habit worth learning
first:

```sh
dg pending          # review what is staged
dg apply            # validate a copy, then write both files
```

```
✓ applied 3 op(s) → decisions.json + decision-graph.md
```

`apply` mutates a copy, validates it, and refuses to write at all if the result
would be invalid — so a bad batch costs you nothing. `dg drop <id>` unstages one
op, `dg clear` all of them.

Staged work lives in `.dgraph-pending.json`, which is gitignored — so **apply
your own work**. Leaving it staged means it exists only in a file no diff will
ever show. (The commit gate will nag you about exactly this.)

## 4. Record a decision

Pass every field as a flag. The command prompts for anything missing, which is
fine for a human and a hung command for a script.

```sh
dg decide D01 \
  --answer "Exact: one brute-force scan over the 2.1M-vector array, 8 ms a query across eight cores." \
  --source "bench/scan-latency.md" \
  --falsifier "the corpus passes ~10M vectors, where a full scan stops holding p99 under the 50 ms budget" \
  --opens D02
dg apply
```

Three fields carry the weight:

- **`--source`** — where the evidence lives: a path, a script, or `discussion`.
- **`--falsifier`** — what evidence would overturn this, written *before* that
  evidence arrives. Required whenever the decision opens something. The good
  ones are thresholds on a number somebody already watches. If nothing could
  overturn it, say so explicitly: `"ANALYTIC — cosine and inner product rank
  identically on L2-normalised vectors"`.
- **`--opens`** — the decisions this one now makes answerable. Leave it off for
  a terminal decision.

The falsifier is the rule people push back on and the one that pays. Written
afterwards it is rationalisation; written first it is a commitment.

## 5. Read the graph

```sh
dg                  # the frontier: everything still open or blocked
dg brief            # ...plus provisional work, staging, validity
dg node D01         # one decision in full, with its superseded history
dg context D04      # ...and every premise it rests on, with their falsifiers
dg path D01 D04     # the chain of evidence between two decisions
dg tree             # the DAG
dg areas            # counts by area and status
```

`dg node D01` after the decision above:

```
╭──────────────────────────────── D01 ─────────────────────────────────╮
│ Exact or approximate search?                                         │
│                                                                      │
│ status      DECIDED                                                  │
│ area        Search                                                   │
│ depends on  —                                                        │
│ opens       D02                                                      │
│ falsifier   the corpus passes ~10M vectors, where a full scan stops  │
│ holding p99 under the 50 ms budget                                   │
│ source      bench/scan-latency.md   (2026-04-02)                     │
│                                                                      │
│ Answer                                                               │
│ Exact: one brute-force scan over the 2.1M-vector array, 8 ms a query │
│ across eight cores.                                                  │
╰──────────────────────────────────────────────────────────────────────╯
```

### `dg context` — why a node is where it is

`dg node` says what a decision holds. `dg context` says what it **stands on**:
every premise underneath it, nearest last, each with the answer it reached, the
evidence that reached it and the falsifier that would overturn it.

```
$ dg context D04
D04  OPEN  efSearch for the recall target  [Serving]
  rests on D02 · opens D05

RESTS ON (2) — nearest premise last
  D01  DECIDED     Exact or approximate search?
       Approximate. A brute-force scan over 48M x 768 fp32 reads
       140 GB a query and lands at 400 ms, eight times the latency budget.
       falsifier:
         a brute-force scan lands under the 50 ms budget at this corpus size
       source: bench/scan-latency.md  ·  2026-02-03
       also opened: D03
  D02  DECIDED     Which index structure?
       *HNSW*, M=32, efConstruction=200.
       falsifier: recall@10 against exact search falls below 0.95
       source: bench/ann-sweep.md  ·  2026-02-14
```

It takes a task id too, and then it does the thing that makes it worth having:
the work, then the whole chain behind the decision that work exists *because*
of, and a closing line saying whether any of it is still under review.

```
$ dg context T04
T04  TODO  Wire the shard fan-out and the merge path  [Serving]
  after T03
  waiting on T03

BECAUSE  D05  BLOCKED  How many shards, and how are results merged?

WHICH RESTS ON (3) — nearest premise last
  …
→ this work waits on D05 (BLOCKED), which is not settled — starting it now is
  a bet on the answer
```

Output is plain and pipe-safe, because the point of it is to be pasted
somewhere — an issue, a handover note, or a subagent's prompt. `--json` gives
the same walk as data.

## 6. Reverse one

This is what the graph is really for. Reversals are kept forever, never
deleted.

```sh
dg reopen D01 --why "the crawl finished at 48M vectors" --yes
```

```
╭───────────────────────────── reopen D01 ─────────────────────────────╮
│ Exact or approximate search?                                         │
│                                                                      │
│ Its answer becomes superseded; its dependencies stay.                │
│                                                                      │
│ 1 decided descendant(s) rest on it and become PROVISIONAL:           │
│   D02                                                                │
╰──────────────────────────────────────────────────────────────────────╯
```

**That list is the point of the command.** Every decided descendant of a
reopened decision now rests on a premise under review, so each becomes
`PROVISIONAL` — computed for you, never worked out by hand. `dg check` refuses
a graph where the propagation was not applied.

Once the premise is settled again, each provisional decision needs one of two
things:

```sh
dg decide D01 --answer … --source … --falsifier …   # settle the premise
dg apply
dg confirm D02      # re-read it; it still holds
# or: dg reopen D02 && dg decide D02 …              # it does not
```

`dg confirm` exists so that `PROVISIONAL` has an honest exit. Do not reach for
`reopen` to escape a status — a reversal that never happened is a lie in the
record.

**The way in has one too.** `PROVISIONAL` is derived: `dg reopen` stages it for
every decided descendant, and nothing else produces it. A merge, a rebase or a
partial checkout can land the reopened premise *without* the ops it implies, and
then `dg check` reports a decision resting on an unsettled premise with no op to
derive the remedy from:

```
✗ [propagation] D02 is DECIDED but rests on D01 (REOPENED) — `dg repair` marks
  it PROVISIONAL, or settle the premise with `dg decide D01`
```

`dg repair` stages exactly what the reopen would have staged. Reach for it
rather than `dg decide D01`, unless you have genuinely settled D01 — recording
an answer nobody reached to escape a blocking check is the same lie in the same
record. It repairs that one rule and only where the checker is reporting it.

Until you do one or the other, `dg check` says so:

```
! [stale_provisional] (warning) D02 is PROVISIONAL but every premise it rests
  on is settled again — re-examine it, then `dg confirm D02`
```

## 7. Keep it honest

```sh
dg check            # every invariant; exits nonzero on any error
```

It names the rule that broke and what fixes it. The two you will actually meet:
a stale view (`dg render`) and staged-but-unapplied work (`dg apply`).

For CI, one file is enough — the tool supplies the tests, so your project never
restates the invariants:

```python
# tests/test_development_graph.py
from dgraph.testing import *  # noqa: F401,F403
```

That yields one test per rule, plus one that surfaces advisory findings as
warnings without failing the build. A check added to the tool shows up in every
project automatically.

## 8. Compose in an editor instead of flags

A one-line prompt is a poor place to write an answer meant to carry its
evidence. `--edit` works like `git commit`:

```sh
dg decide D37 --edit        # also: dg reopen --edit, dg add --edit, dg edit N
export DG_EDIT=1            # make it the default; --no-edit overrides
```

`dg` writes an org buffer, opens your editor, waits, and stages what comes
back. The buffer has an `* Input` half you fill in and a `* Context` half —
the premise you are deciding on top of, with its own answer and falsifier, and
the ancestor chain. Only `* Input` is ever read back.

In emacs you also get `C-c C-c` to stage, `C-c C-k` to abort, and `C-c C-o` to
follow a `dg:` link to another decision. Any editor works via `$DG_EDITOR`;
you get the same buffer without the navigation.

Prose is stored exactly as typed. Org composed in the editor is tagged as such,
so `*bold*` and `/italic/` render with org's meaning in the generated views,
while markdown typed anywhere else keeps markdown's meaning.

## Where to go next

- [How it works, and why](how-it-works.md) — the ideas behind the
  commands, as one project's story.
- [The web interface](quickstart-web.md) — the same graph, laid out and
  clickable.
- [The agent plugin](quickstart-agents.md) — the brief at session start, and a
  commit gate, for Claude Code and opencode.
