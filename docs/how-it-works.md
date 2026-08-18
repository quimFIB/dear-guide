# How it works, and why

An orientation, not a manual. If you want the commands, start with the
[CLI quick start](quickstart-cli.md); this is the shape of the thing they
operate on, told as one project's story.

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

A **vertex** is a question the project must answer — *"Which corpus do we train
on?"*, not *"Download the corpus"*. It carries an explicit status:

```
DECIDED · OPEN · BLOCKED:<id> · REOPENED · PROVISIONAL
```

An **edge** is a dependency: *this question only becomes answerable once that
one is settled*. When you answer a question, the edge gains a payload — the
answer, a source, a falsifier, a date.

So a project looks like this:

```
D01 corpus ──▶ D02 tokenizer ──▶ D03 parameter count ──▶ D04 serving format
```

The arrows are the whole data model. Dependency is never a field you fill in on
both ends — it is the graph structure itself. (In the format that preceded this
tool it *was* stored twice, in opposite directions, and 11 of 55 nodes ended up
disagreeing with themselves.)

## A project's first week

You start with the questions you know you have, none of them answered:

```sh
dg add --id D01 --title "Which corpus do we train on?" --area Data
dg add --id D02 --title "Tokenizer: BPE or unigram?" --area Modelling --after D01
dg add --id D03 --title "How many parameters?" --area Modelling --after D02
dg add --id D04 --title "Serving format for the first release" --area Infra \
       --after D03 --status BLOCKED:D03
dg apply
```

`dg` on its own shows the **frontier** — everything not yet settled, and what
each item is waiting for:

```
┃ ID  ┃ Decision                 ┃ Status      ┃ Waiting on ┃ Unblocks ┃
│ D01 │ Which corpus…            │ OPEN        │ —          │ —        │
│ D02 │ Tokenizer: BPE or…       │ OPEN        │ D01        │ —        │
│ D03 │ How many parameters?     │ OPEN        │ D02        │ D04      │
│ D04 │ Serving format…          │ BLOCKED:D03 │ D03        │ —        │
```

That table answers "what can I actually work on?" — only D01 has nothing in
*Waiting on*. This is what gets injected at the start of an agent session, and
what you read yourself on a Monday.

## Answering one

A decision needs three things beyond the answer itself:

```sh
dg decide D01 \
  --answer "CommonCrawl 2024-26 plus the internal support corpus." \
  --source "report/corpus-sweep.md" \
  --falsifier "held-out perplexity gets worse as we add more of the internal corpus" \
  --opens D02
dg apply
```

- **source** — where the evidence lives. A path, a script, or `discussion`.
- **opens** — which questions this now makes answerable.
- **falsifier** — *what evidence would overturn this*.

The falsifier is the unusual one, and it is the point of the whole exercise.
It must be written **before** the evidence arrives; written afterwards it is
just rationalisation of whatever happened. It costs you thirty seconds now and
tells you, months later, whether a new result actually bears on an old
decision or merely feels like it does.

If genuinely nothing could overturn it, say that explicitly —
`"ANALYTIC — follows from the corpus choice"` — rather than leaving it blank.
Blank is what a rule with no teeth looks like.

Answer D02 and D03 the same way, and the frontier collapses to one line:

```
┃ ID  ┃ Decision                            ┃ Status ┃ Waiting on ┃
│ D04 │ Serving format for the first release│ OPEN   │ —          │
DECIDED 3  OPEN 1
```

Notice D04 went from `BLOCKED:D03` to `OPEN` on its own. Deciding D03 released
everything blocked on it — derived, never typed. Working that out by hand
across a real graph is exactly the mistake the tool exists to prevent.

## Six weeks later: the falsifier fires

The ablation comes back. The internal corpus makes things *worse* — precisely
the evidence D01's falsifier named. So you reverse it:

```sh
dg reopen D01 --why "the internal corpus made held-out perplexity worse at 1.4B"
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

**That list is the entire reason this tool exists.** The tokenizer and the
parameter count were both chosen *on top of* the corpus decision. They are not
wrong yet — but they now rest on a premise under review, and anyone about to
build on them deserves to know. In a design doc, nothing would have told you.
Here it is computed from the graph and cannot be forgotten.

`PROVISIONAL` is that state, and the brief surfaces it separately from the
frontier, because a provisional decision *has* an answer — it just may not
survive:

```
FRONTIER (2) -- not settled
  D01  REOPENED  Which corpus do we train on?  [Data]
       note: the internal corpus made held-out perplexity worse at 1.4B
  D04  OPEN      Serving format for the first release  [Infra]

RESTING ON A PREMISE UNDER REVIEW (2) -- PROVISIONAL, so not in the frontier
  D02  Tokenizer: BPE or unigram?  [Modelling]  rests on D01
  D03  How many parameters?  [Modelling]  rests on D01
```

## Settling it again

You re-answer the corpus question. Now the two provisional decisions each need
a human judgement — and until they get one, `dg check` keeps saying so:

```
! [stale_provisional] D02 is PROVISIONAL but every premise it rests on is
  settled again — re-examine it, then `dg confirm D02`
! [stale_provisional] D03 is PROVISIONAL but every premise it rests on is
  settled again — re-examine it, then `dg confirm D03`
```

You re-read them under the new premise. The tokenizer still stands:

```sh
dg confirm D02        # re-examined; it holds
```

The parameter count does not — 1.4B assumed the bigger corpus:

```sh
dg reopen D03 --why "1.4B assumed the larger corpus; recompute against the smaller one"
dg decide D03 --answer "900M. The smaller corpus moves the compute-optimal point down." …
dg apply
```

`dg confirm` exists so that `PROVISIONAL` has an honest way out. The temptation
is to reopen-and-redecide everything just to clear the warning — but that files
a reversal that never happened, and a fake reversal in the record is worse than
an unresolved one.

## What you are left with

The reversal is not gone. It is the most valuable thing here:

```
$ dg node D01
│ Answer                                                        │
│ CommonCrawl 2024-26 only. The internal corpus is dropped.     │
│                                                               │
│ Superseded                                                    │
│   “CommonCrawl 2024-26 plus the internal support corpus.”     │
│     → CommonCrawl 2024-26 only. The internal corpus is dropped│
│     the internal corpus made held-out perplexity worse at 1.4B│
```

And in the generated `decision-graph.md`, permanently:

| Vertex | Superseded answer | Replaced by | What changed it |
|---|---|---|---|
| D01 | …plus the internal support corpus. | CommonCrawl 2024-26 only. | the internal corpus made held-out perplexity worse at 1.4B |
| D03 | 1.4B, the largest that fits the budget. | 900M. | 1.4B assumed the larger corpus |

Six months on, when somebody asks *"why aren't we using the internal corpus?"* —
that is the answer, with the evidence attached. Nothing was deleted; the store
is append-only. A reversal marks the old edge inactive and adds a new one.

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

## What this is not

The graph stops being useful the moment it becomes a tracker. It holds
**decisions**, not:

- tasks or milestones — those have a start and an end; a decision has evidence
- a changelog — git already does that, better
- a file list, a roadmap, or a set of notes

The test is whether the entry answers a question the project had to settle, and
whether you can say what would change your mind about it. If you cannot write a
falsifier, it is probably not a decision.

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
- [Agent plugin](quickstart-agents.md) — the brief at session start, and the
  commit gate, for Claude Code and opencode.
