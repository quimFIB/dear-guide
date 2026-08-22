# How it works, and why

An orientation, not a manual. If you want the commands, start with the
[CLI quick start](quickstart-cli.md); this is the shape of the thing they
operate on, told as one project's story.

**There are two graphs**, and this page tells the story of the first one before
it gets to the second. Decisions come first because the task graph is built to
lean on them — but the two are separate stores and either works alone. If you
came for work tracking, [Tracking the work as well](#tracking-the-work-as-well)
is self-contained, and `dg task init` needs no `decisions.json` beside it.

## The problem

A long-running project accumulates decisions in prose — plans, notes, design
docs, memory files, commit messages. Prose drifts. Two documents come to
disagree about whether something was settled. Someone quietly reverses a
decision, and the three conclusions built on top of it stay standing, still
being cited, now resting on nothing.

Nobody notices, because there is nothing to notice *with*. A design doc has no
notion of "this paragraph depended on that one", so when the premise moves the
consequences do not move with it — they just become quietly wrong.

This tool is a store for decisions with enough structure that the drift becomes
a test failure.

## The shape: questions, and what rests on what

Two kinds of thing, and nothing else.

A **vertex** is a question the project must answer — *"Which distance
metric?"*, not *"Build the index"*. It carries an explicit status:

```
DECIDED · OPEN · BLOCKED:<id> · REOPENED · PROVISIONAL
```

An **edge** is a dependency: *this question only becomes answerable once that
one is settled*. When you answer a question, the edge gains a payload — the
answer, a source, a falsifier, a date.

`BLOCKED:<id>` is the one status that names another vertex, and it is a
dependency claim like any other — so it has to be backed by an edge. Saying
`--status BLOCKED:D03` records that edge for you, and `dg check` refuses a
store where the two disagree (`block_is_a_premise`). A dependency kept in a
status field instead of the edge list is the second copy this model exists to
prevent, and the one that can contradict the first.

So a nearest-neighbour search service looks like this:

```
D01 exact or approximate ──┬──▶ D02 distance metric
                           └──▶ D03 queries across cores ──▶ D04 new documents
```

Each arrow is a real constraint, not bookkeeping. You cannot pick a distance
metric before you know whether you are scoring exhaustively or walking a graph —
a brute-force scan can normalise at query time, an HNSW graph bakes the metric
into the edges at build time. You cannot decide how queries spread across cores
before you know what a query *does*, because a scan and a graph traversal
parallelise nothing alike.

The arrows are the whole data model. Dependency is never a field you fill in on
both ends — it is the graph structure itself. (In the format that preceded this
tool it *was* stored twice, in opposite directions, and 11 of 55 nodes ended up
disagreeing with themselves.)

## A project's first week

You start with the questions you know you have, none of them answered:

```sh
dg add --id D01 --title "Exact or approximate search?"        --area Search
dg add --id D02 --title "Which distance metric?"              --area Search  --after D01
dg add --id D03 --title "How are queries spread across cores?" --area Serving --after D01
dg add --id D04 --title "How does the index absorb new documents?" --area Index \
       --after D03 --status BLOCKED:D03
dg apply
```

`dg` on its own shows the **frontier** — everything not yet settled, and what
each item is waiting for:

```
┃ ID  ┃ Decision                          ┃ Status      ┃ Waiting on ┃ Unblocks ┃
│ D01 │ Exact or approximate search?      │ OPEN        │ —          │ —        │
│ D02 │ Which distance metric?            │ OPEN        │ D01        │ —        │
│ D03 │ How are queries spread across co… │ OPEN        │ D01        │ D04      │
│ D04 │ How does the index absorb new do… │ BLOCKED:D03 │ D03        │ —        │
```

That table answers "what can I actually work on?" — only D01 has nothing in
*Waiting on*. This is what gets injected at the start of an agent session, and
what you read yourself on a Monday.

## Answering one

A decision needs three things beyond the answer itself:

```sh
dg decide D01 \
  --answer "Exact: one brute-force scan over the 2.1M-vector array, 8 ms a query across eight cores. While the scan is exhaustive a recall regression can only be the encoder's, never the index's." \
  --source "bench/scan-latency.md" \
  --falsifier "the corpus passes ~10M vectors, where a full scan stops holding p99 under the 50 ms budget" \
  --opens "D02,D03"
dg apply
```

- **source** — where the evidence lives. A path, a script, or `discussion`.
- **opens** — which questions this now makes answerable.
- **falsifier** — *what evidence would overturn this*.

The falsifier is the unusual one, and it is the point of the whole exercise.
It must be written **before** the evidence arrives; written afterwards it is
just rationalisation of whatever happened. It costs you thirty seconds now and
tells you, a year later, whether some new development actually bears on an old
decision or merely feels like it does.

Notice the shape of the one above. It is not "this feels risky" — it is a
threshold on a number somebody is already watching, which is the only kind that
ever fires on its own.

If genuinely nothing could overturn it, say that explicitly — `"ANALYTIC —
cosine and inner product rank identically on L2-normalised vectors; no
measurement can separate them"` — rather than leaving it blank. Blank is what a
rule with no teeth looks like.

Answer D02 and D03 the same way. Notice that D03's answer records *why*, not
just *what*:

> No sharding. One process holds the whole array and each query fans out across
> the eight cores: an exhaustive scan is embarrassingly parallel, and 2.1M × 768
> fp32 is 6.4 GB, which fits the serving box with room to spare.

Hold on to that reasoning — it matters in a minute. With three settled, the
frontier collapses to one line:

```
┃ ID  ┃ Decision                              ┃ Status ┃ Waiting on ┃
│ D04 │ How does the index absorb new docume… │ OPEN   │ —          │
DECIDED 3  OPEN 1
```

D04 went from `BLOCKED:D03` to `OPEN` on its own. Settling D03 released
everything blocked on it — derived, never typed. Working that out by hand
across a real graph is exactly the mistake the tool exists to prevent.

## Six months later: the falsifier fires

The crawl finishes. The corpus is 48M vectors — five times the ~10M threshold,
and precisely the evidence D01's falsifier named. Nobody had to *notice*
anything: the falsifier was a threshold and the number crossed it. So you
reverse it:

```sh
dg reopen D01 --why "the crawl finished at 48M vectors, five times the ~10M threshold in the falsifier"
```

```
╭──────────────── reopen D01 ─────────────────╮
│ Its answer becomes superseded; its          │
│ dependencies stay.                          │
│                                             │
│ 2 decided descendant(s) rest on it and      │
│ become PROVISIONAL:                         │
│   D02, D03                                  │
╰─────────────────────────────────────────────╯
```

**That list is the entire reason this tool exists.** The metric and the
core-parallel scan were both chosen *on top of* the exact-search decision. They
are not wrong yet — but they now rest on a premise under review, and anyone
about to build on them deserves to know. In a design doc, nothing would have
told you; the search page would get an edit and the serving page would sit
there, still confident.

`PROVISIONAL` is that state, and the brief lists it separately from the
frontier, because a provisional decision *has* an answer — it just may not
survive:

```
FRONTIER (2) -- not settled
  D01  REOPENED  Exact or approximate search?  [Search]
       note: the crawl finished at 48M vectors, five times the ~10M threshold …
  D04  OPEN      How does the index absorb new documents?  [Index]

RESTING ON A PREMISE UNDER REVIEW (2) -- PROVISIONAL, so not in the frontier
  D02  Which distance metric?  [Search]  rests on D01
  D03  How are queries spread across cores?  [Serving]  rests on D01
```

## Settling it again

You re-answer the search question — HNSW, M=32, efConstruction=200, efSearch
tuned until recall@10 against exact search sits at 0.962. Now the two
provisional decisions each need a human judgement, and until they get one
`dg check` keeps saying so:

```
! [stale_provisional] D02 is PROVISIONAL but every premise it rests on is
  settled again — re-examine it, then `dg confirm D02`
! [stale_provisional] D03 is PROVISIONAL but every premise it rests on is
  settled again — re-examine it, then `dg confirm D03`
```

Here is where the two of them part company, and why the propagation was worth
computing.

**The metric still stands.** Cosine was chosen because the encoder L2-normalises
its output, and HNSW builds a cosine graph as happily as a scan reads one. The
argument never mentioned corpus size. You re-read it, agree, and record that you
did:

```sh
dg confirm D02
```

**The core-parallel scan does not.** Its answer said, in as many words, that one
process could hold everything *because an exhaustive scan is embarrassingly
parallel and the array was 6.4 GB*. A graph traversal is neither exhaustive nor
embarrassingly parallel, and 48M vectors is not 6.4 GB — so the reason is gone
even though the sentence still parses:

```sh
dg reopen D03 --why "the array no longer fits one box, and an HNSW traversal is not the embarrassingly parallel scan this answer was reasoning about"
dg decide D03 --answer "Four shards by document id, each with its own HNSW graph on its own box, merged by the query node." …
dg apply
```

That is the failure this whole design is aimed at. Nothing about D03 looked
wrong — it was a sensible decision, written down clearly, measured, and it had
quietly stopped being true. Without the graph, you find out in eighteen months
when somebody asks why p99 is pinned to one core.

`dg confirm` exists so that `PROVISIONAL` has an honest way out. The temptation
is to reopen-and-redecide everything just to clear the warning — but that files
a reversal that never happened, and a fake reversal in the record is worse than
an unresolved one.

## What you are left with

The reversals are not gone. They are the most valuable thing here:

```
$ dg node D03
│ Answer                                                               │
│ Four shards by document id, each with its own HNSW graph on its own  │
│ box, merged by the query node. The merge costs 1.5 ms and the shards │
│ rebuild independently.                                               │
│                                                                      │
│ Superseded                                                           │
│   “No sharding. One process holds the whole array and each que…” →   │
│ Four shards by document id, each with its own HNSW graph on…         │
│     the array no longer fits one box, and an HNSW traversal is not   │
│ the embarrassingly parallel scan this answer was reasoning about     │
```

And in the generated `decision-graph.md`, permanently:

| Vertex | Superseded answer | Replaced by | What changed it |
|---|---|---|---|
| D01 | Exact: one brute-force scan over the 2.1M-vector array, 8 m… | Approximate: HNSW with M=32, efConstruction=200, efSearch=9… | the crawl finished at 48M vectors, five times the ~10M threshold in the falsifier |
| D03 | No sharding. One process holds the whole array and each que… | Four shards by document id, each with its own HNSW graph on… | the array no longer fits one box, and an HNSW traversal is not the embarrassingly parallel scan this answer was reasoning about |

A year on, when the new hire asks *"why are we sharding at 48M when a single
HNSW graph would fit?"* — that is the answer, with the reasoning attached.
Nothing was deleted; the store is append-only. A reversal marks the old edge
inactive and adds a new one.

## The four ideas doing the work

1. **Status is explicit, never inferred.** A decision can have consequences and
   still be under review, so "it has outgoing edges" never means "it is
   settled".
2. **Every closed decision records a falsifier**, written before the evidence
   arrives.
3. **Nothing is deleted.** Superseded is not a status — it is an edge kept
   forever. How a project changed its mind is usually worth more than the
   conclusion it landed on.
4. **Reopening propagates**, and the tool computes the blast radius. That is
   the one thing a human reliably gets wrong.

## Two mechanics worth knowing

**Nothing is written until you apply.** Every editing command *stages* an op;
`dg apply` validates the whole batch against a copy and writes only if the
result would be valid. A bad batch costs nothing. The flip side: staged work
lives in a gitignored file, so apply your own work — otherwise it exists
nowhere a diff will ever show.

**`decisions.json` is the truth; `decision-graph.md` is a view.** Never edit
the markdown — it is regenerated from the store on every apply, and `dg check`
fails if the two have drifted apart.

## Taking something back

Three verbs, and picking the wrong one is how a graph loses the thing it is
for.

**`dg reopen`** is for an answer that turned out wrong, and **`dg task drop`**
for work that will not be done. Both *keep the record* — the superseded answer
stays in the store forever, the dropped task keeps its note and its reason —
because how a project changed its mind is worth more than the conclusion it
changed to.

**`dg dep`** records a dependency between two questions that already exist —
the one `dg add --after` cannot say, because it fires only at creation. **`dg
undep`** removes one recorded against the wrong parent. It loses
a claim and invents none. Only a bare edge: a decided edge's targets are part
of its answer, so dropping one claims the answer no longer opens that question.
The command says so and names the way through — reopen strips the payload and
leaves the dependency editable:

```sh
dg reopen D01 --why "..."      # the edge becomes payload-free
dg undep  D02 --after D01      # legal now; no answer is being rewritten
dg decide D01 --opens D03 ...  # restate it, knowing the new target set
```

**`dg rm`** (and `dg task rm`) is for a record that should never have been
written — a duplicate, an import artifact, a heading that was never a decision.
It keeps nothing, which is the point, and it is the only thing here you cannot
undo from inside the tool. So:

- it refuses unless the store is committed, because `git log -p` is the archive
  and this makes that real rather than assumed;
- it refuses without `--yes` when nobody is at a terminal;
- `dg gate` answers **ask** on it, so a host puts it to a person before it runs
  at all.

By default it **severs**: the node goes, its edges go, and what is left is a
state the checks already describe. `--splice` joins what it sat between and
`--into` folds it onto another node — and both *assert an edge nobody wrote*,
which is why they refuse to write into a decided answer. Attaching an answer to
a question it never opened is the one claim this model will not manufacture.

## What belongs in the decision graph

The decision graph stops being useful the moment it becomes a tracker, so it
holds **decisions** and nothing else — not a changelog (git does that better),
not a roadmap, not a file list, and **not tasks**.

Tasks get their own graph, which is a different thing on purpose:

| | decision | task |
|---|---|---|
| answers | a question | nothing; it produces something |
| finished when | somebody settles it, with evidence | the work is done |
| carries | an answer, a source, a **falsifier** | an **outcome** |
| reversed by | new evidence, kept forever as a reversal | nothing — abandoned work is `DROPPED` |
| status | explicit, never inferred | blocked is *derived* from what precedes it |

The test for which store an entry belongs in: **can you write a falsifier for
it?** Then it is a decision. **Can you write a definition of done?** Then it is
a task. An entry that admits both is really two entries — record the decision,
then the work it implies, and link them.

See [tracking the work as well](#tracking-the-work-as-well) below.

## Tracking the work as well

*A second graph, and it stands on its own: everything below works in a
project that has never recorded a decision. The link to the decision graph is
the last part, and it is optional.*

`tasks.json` is a second, independent graph: tasks, and edges saying what has to
be done before what. Separate store, separate view (`tasks.md`), separate ids
(`T01`, not `D01`), so work can never be mistaken for a decision. Either store
alone is enough — you can track work in a project that records no decisions.

```sh
dg task init --areas "Search,Serving,Index"
dg task add --id T01 --title "Provision the serving box and load the flat array" \
            --area Serving --because D01
dg task add --id T02 --title "Pin the scan threads to the performance cores" \
            --area Serving --after T01 --because D03
dg apply
dg task                     # what is outstanding, and what is startable now
```

Blocked is **derived**, never stored: a task is ready when everything before it
is resolved. Finish T01 and T02 simply becomes ready — nothing to update, and no
status that can go stale. Abandoning a prerequisite releases what waited on it,
for the same reason.

That release is a **guess**, though, and the tool says so rather than making it
silently. Work that merely had to come second is genuinely freed; work that was
going to *consume* what the abandoned task produced is not freed but undermined,
and the store cannot tell those apart. So `dg task drop` refuses until every
task it would leave standing has a verdict — `--keep` or `--drop-too` — and
`dg check` goes on asking about four things a drop leaves behind:

```
! [released_by_drop]  T04 became startable only because T03 was abandoned …
! [orphaned_by_drop]  T07 was discovered during T03, which was abandoned …
! [evidence_dropped]  D04 is OPEN and every task meant to inform it was …
! [evidence_dropped_after_deciding]  D02 is DECIDED, but every task meant …
```

The last two are halves of one silence, split on whether the decision is
settled. Unsettled, the cost is that the question reads as waiting on something
that is never coming. Settled, an *answer* is standing on work that never
produced anything — either it was decided without the spike, so the link is
vestigial (`dg task unlink`), or it is owed a re-examination (`dg reopen`). The
store cannot tell those apart, so the warning names both.

All four are warnings and none can deny a commit. They stop once the work is
started, dropped, or given something else to stand on — the same shape as
`stale_provisional` in the decision graph, and for the same reason: the premise
moved, so re-examine the conclusion.

### Two kinds of edge, because ordering is not provenance

`--after` is one of two relations a task edge can carry, and every edge names
which it is:

```sh
dg task add --id T07 --title "Move the sweep off the batch box" --area Serving \
            --discovered-during T03
```

`precedes` — what `--after` writes — makes work wait. `prompted` — what
`--discovered-during` writes — records that doing one task turned another up,
and makes nothing wait at all. The distinction is not pedantry: a chore noticed
mid-task is usually startable at once, and frequently has to land *before* the
task that revealed it can be finished. Those two facts about the same pair point
opposite ways, and as one untyped relation they are a cycle — the store would
refuse them and you would have to delete one of two true things.

Each kind is therefore walked as its own subgraph, and only `precedes` feeds
readiness. What `prompted` is for is the question the ordering cannot answer:
when work is abandoned, which chores existed only because of it.

`dg task tree` draws that separation rather than flattening it: the spine is
`precedes`, so reading down a branch is reading an order of work, and prompted
work hangs off the task that turned it up marked as what it is. A tree that drew
both alike would assert the ordering the second kind exists to avoid asserting.

### What linking the two graphs buys

One optional field ties them: `--because D01` says *this work exists because of
that answer*. It is stored on the task and derived the other way, so
`decisions.json` never mentions a task and a change to it still always means a
decision changed.

Now go back to the moment the crawl lands at 48M vectors:

```
$ dg reopen D01 --why "the crawl finished at 48M vectors"

  2 decided descendant(s) rest on it and become PROVISIONAL:
    D02, D03

  1 unfinished task(s) rest on a premise under review:
    T02
```

**That last line is what neither tool can produce alone.** A task tracker does
not know why T02 exists. A decision log does not know T02 exists at all. Here,
reversing the search strategy tells you somebody is part-way through pinning
scan threads to cores for a scan you are about to stop doing — and it says
nothing about T01, which is already done, because reversing an old decision
should not raise alarms about history.

The link points the other way too. `--evidence-for D04` says *finishing this
work bears on that question* — the benchmark whose result settles something, or
the chore that turned up a question nobody had written down:

```sh
dg task add  --id T06 --title "Time incremental insert against a nightly rebuild" \
             --area Index --evidence-for D04
dg task link T20 --evidence-for D08     # ...or after the fact, once work reveals D08
```

Which makes the frontier honest — an open decision with a benchmark running
reads `waiting on evidence from T06` rather than `decidable now` — and buys the
check worth the whole exercise:

```
! [evidence_unharvested] T06 is DONE and was to inform D04 (How does the index
  absorb new documents?), which is still unsettled — record what it showed with
  `dg decide D04`
```

*The benchmark ran and nobody wrote down the conclusion.* Invisible in both
graphs separately: the task looks finished, the decision merely undecided. It is
the single most ordinary way a project loses a result it paid for.

One rule keeps this from becoming a nuisance: **anything a reopen can cause is a
warning, never an error.** If work resting on a reopened decision made the store
invalid, one `dg reopen` would block every commit in the repository until
somebody triaged the backlog — and the check would be switched off the same day.
Decisions are never held hostage by a backlog.

Two things about the link *are* errors, because they are contradictions rather
than states of play: a link naming a decision that does not exist, and a cycle
across the two graphs — `D01` opens work that has to finish before `D01` can be
answered, so nothing in the loop can start. Both are refused by `dg apply`, in
either store and through either door, so a batch that would create one aborts
with nothing written and the id of the op to drop:

```
✗ task ops aborted, nothing written
would leave the task graph invalid:
  [link_acyclic] cycle across the graphs: D01 -> T01 -> T02 -> D01 — nothing in
  this loop can start. Split the decision into one you can settle now and one
  the evidence settles, or break the task dependency.
`dg task pending` to review; `dg task drop-op <id>` to unstage
```

Refusing at apply is what makes them safe to treat as errors: they are caught
where a `dg` command can still take them back, rather than after the fact by
`dg check`, with the store already written. A store that is *already* invalid
still accepts writes — the guard reports only what a batch introduces, because
`dg` has to stay able to repair what it finds.

### Seeing the join

Two places show both graphs at once, and they are the same reading in two
shapes. `dg serve` has a **joined** tab that ranks decisions and tasks together
over the union of the two graphs, so a task sits below the decision it exists
`because` of and above the one its outcome is `evidence_for`. And `dg context`
prints the same thing as text, from one node's point of view:

```
$ dg context T04
T04  TODO  Wire the shard fan-out and the merge path  [Serving]
  after T03 · waiting on T03

CHAIN  D01 → D02 → D04! → D05! → T04    ! = not settled
  ...

BECAUSE  D05  BLOCKED  How many shards, and how are results merged?
         not settled

→ this work waits on D05 (BLOCKED), which is not settled — starting it now is
  a bet on the answer
```

The `CHAIN` line is the joined tab's ranking, flattened: the same walk over the
union of the two stores, from this node's point of view. And that last line is
the whole graph collapsed into the one sentence somebody about to start work
needs.

It is also what you hand a fresh context — a colleague, or a subagent — that
knows the task and nothing about why it exists, and for that `--full` is the
form to send: the short one clips each premise's answer to a sentence, and a
clipped answer is exactly the constraint a fresh context will get wrong.

## Keeping it honest

```sh
dg check                    # every invariant; names the rule that broke
```

In CI, one file gets you a test per rule, with nothing to keep in sync:

```python
from dgraph.testing import *  # noqa: F401,F403
```

And with the [agent plugin](quickstart-agents.md) installed, a `git commit`
that would leave the graph contradicting itself is refused outright, quoting
the rule and the fix.

## Next

- [CLI quick start](quickstart-cli.md) — the commands, step by step.
- [Web quick start](quickstart-web.md) — the same graph, laid out and clickable.
- [Agent plugin](quickstart-agents.md) — the brief at session start, the commit
  gate and the five slash commands, for Claude Code and opencode.
