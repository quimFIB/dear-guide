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

A **vertex** is a question the project must answer — *"Which database?"*, not
*"Set up the database"*. It carries an explicit status:

```
DECIDED · OPEN · BLOCKED:<id> · REOPENED · PROVISIONAL
```

An **edge** is a dependency: *this question only becomes answerable once that
one is settled*. When you answer a question, the edge gains a payload — the
answer, a source, a falsifier, a date.

So a small web service looks like this:

```
D01 where it runs ──▶ D02 database ──▶ D03 background jobs ──▶ D04 nightly reports
```

Each arrow is a real constraint, not bookkeeping. You cannot sensibly pick a
database before you know where the thing runs; you cannot pick a job mechanism
before you know the database, because "just use a table in the database" is one
of the candidates.

The arrows are the whole data model. Dependency is never a field you fill in on
both ends — it is the graph structure itself. (In the format that preceded this
tool it *was* stored twice, in opposite directions, and 11 of 55 nodes ended up
disagreeing with themselves.)

## A project's first week

You start with the questions you know you have, none of them answered:

```sh
dg add --id D01 --title "Where does the app run?"  --area Infra
dg add --id D02 --title "Which database?"          --area Backend --after D01
dg add --id D03 --title "How do background jobs run?" --area Backend --after D02
dg add --id D04 --title "How do nightly reports get scheduled?" --area Product \
       --after D03 --status BLOCKED:D03
dg apply
```

`dg` on its own shows the **frontier** — everything not yet settled, and what
each item is waiting for:

```
┃ ID  ┃ Decision                    ┃ Status      ┃ Waiting on ┃ Unblocks ┃
│ D01 │ Where does the app run?     │ OPEN        │ —          │ —        │
│ D02 │ Which database?             │ OPEN        │ D01        │ —        │
│ D03 │ How do background jobs run? │ OPEN        │ D02        │ D04      │
│ D04 │ How do nightly reports…     │ BLOCKED:D03 │ D03        │ —        │
```

That table answers "what can I actually work on?" — only D01 has nothing in
*Waiting on*. This is what gets injected at the start of an agent session, and
what you read yourself on a Monday.

## Answering one

A decision needs three things beyond the answer itself:

```sh
dg decide D01 \
  --answer "A managed platform. Nobody here wants to be on call for servers." \
  --source "notes/hosting-options.md" \
  --falsifier "the monthly bill passes what a small VM cluster would cost, or a customer contract requires our own hardware" \
  --opens D02
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

If genuinely nothing could overturn it, say that explicitly —
`"ANALYTIC — follows from the platform choice"` — rather than leaving it blank.
Blank is what a rule with no teeth looks like.

Answer D02 and D03 the same way. Notice that D03's answer records *why*, not
just *what*:

> A jobs table in Postgres, polled by a worker. The platform bills per add-on
> service, so avoiding a separate broker is worth the lower ceiling.

Hold on to that reasoning — it matters in a minute. With three settled, the
frontier collapses to one line:

```
┃ ID  ┃ Decision                             ┃ Status ┃ Waiting on ┃
│ D04 │ How do nightly reports get scheduled?│ OPEN   │ —          │
DECIDED 3  OPEN 1
```

D04 went from `BLOCKED:D03` to `OPEN` on its own. Settling D03 released
everything blocked on it — derived, never typed. Working that out by hand
across a real graph is exactly the mistake the tool exists to prevent.

## Six months later: the falsifier fires

Sales closes a customer whose contract requires data on your own hardware —
precisely the evidence D01's falsifier named. So you reverse it:

```sh
dg reopen D01 --why "a customer contract requires data on our own hardware"
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

**That list is the entire reason this tool exists.** The database and the job
system were both chosen *on top of* the hosting decision. They are not wrong
yet — but they now rest on a premise under review, and anyone about to build on
them deserves to know. In a design doc, nothing would have told you; the
hosting page would get an edit and the jobs page would sit there, still
confident.

`PROVISIONAL` is that state, and the brief lists it separately from the
frontier, because a provisional decision *has* an answer — it just may not
survive:

```
FRONTIER (2) -- not settled
  D01  REOPENED  Where does the app run?  [Infra]
       note: a customer contract requires data on our own hardware
  D04  OPEN      How do nightly reports get scheduled?  [Product]

RESTING ON A PREMISE UNDER REVIEW (2) -- PROVISIONAL, so not in the frontier
  D02  Which database?  [Backend]  rests on D01
  D03  How do background jobs run?  [Backend]  rests on D01
```

## Settling it again

You re-answer the hosting question — three VMs of your own. Now the two
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

**The database still stands.** Postgres was chosen on its own merits and runs
just as well on your own servers. You re-read it, agree, and record that you
did:

```sh
dg confirm D02
```

**The job system does not.** Its answer said, in as many words, that a jobs
table was worth the lower ceiling *because the platform billed per add-on
service*. You are not on that platform any more, so the reason is gone even
though the sentence still parses:

```sh
dg reopen D03 --why "the jobs table was chosen to avoid the platform's per-service billing; on our own servers that reason is gone"
dg decide D03 --answer "Redis with a real worker queue. Delayed retries come free, and the ceiling is much higher." …
dg apply
```

That is the failure this whole design is aimed at. Nothing about D03 looked
wrong — it was a sensible decision, written down clearly, and it had quietly
stopped being true. Without the graph, you find out in eighteen months when
somebody asks why the queue keeps falling over.

`dg confirm` exists so that `PROVISIONAL` has an honest way out. The temptation
is to reopen-and-redecide everything just to clear the warning — but that files
a reversal that never happened, and a fake reversal in the record is worse than
an unresolved one.

## What you are left with

The reversals are not gone. They are the most valuable thing here:

```
$ dg node D03
│ Answer                                                          │
│ Redis with a real worker queue. Delayed retries come free, and  │
│ the ceiling is much higher.                                     │
│                                                                 │
│ Superseded                                                      │
│   “A jobs table in Postgres, polled by a worker. The platform…” │
│     → Redis with a real worker queue…                           │
│     the jobs table was chosen to avoid the platform's           │
│     per-service billing; on our own servers that reason is gone │
```

And in the generated `decision-graph.md`, permanently:

| Vertex | Superseded answer | Replaced by | What changed it |
|---|---|---|---|
| D01 | A managed platform. Nobody here wants… | Our own servers, three VMs in the EU region. | a customer contract requires data on our own hardware |
| D03 | A jobs table in Postgres, polled by a worker… | Redis with a real worker queue… | the jobs table was chosen to avoid the platform's per-service billing |

A year on, when the new hire asks *"why are we running Redis when everything
else is in Postgres?"* — that is the answer, with the reasoning attached.
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

`tasks.json` is a second, independent graph: tasks, and edges saying what has to
be done before what. Separate store, separate view (`tasks.md`), separate ids
(`T01`, not `D01`), so work can never be mistaken for a decision. Either store
alone is enough — you can track work in a project that records no decisions.

```sh
dg task init --areas "Infra,Backend"
dg task add --id T01 --title "Provision the VMs"    --area Infra   --because D01
dg task add --id T02 --title "Migrate the database" --area Backend --after T01 --because D01
dg apply
dg task                     # what is outstanding, and what is startable now
```

Blocked is **derived**, never stored: a task is ready when everything before it
is resolved. Finish T01 and T02 simply becomes ready — nothing to update, and no
status that can go stale. Abandoning a prerequisite releases what waited on it,
for the same reason.

### What linking the two graphs buys

One optional field ties them: `--because D01` says *this work exists because of
that answer*. It is stored on the task and derived the other way, so
`decisions.json` never mentions a task and a change to it still always means a
decision changed.

Now go back to the moment the contract forces you off the managed platform:

```
$ dg reopen D01 --why "a customer contract requires our own hardware"

  2 decided descendant(s) rest on it and become PROVISIONAL:
    D02, D03

  1 unfinished task(s) rest on a premise under review:
    T02
```

**That last line is what neither tool can produce alone.** A task tracker does
not know why T02 exists. A decision log does not know T02 exists at all. Here,
reversing the hosting decision tells you there is work in flight built on it —
and says nothing about T01, which is already done, because reversing an old
decision should not raise alarms about history.

The link points the other way too. `--evidence-for D05` says *finishing this
work bears on that question* — the spike whose result settles something, or the
chore that turned up a question nobody had written down:

```sh
dg task add  --id T09 --title "Benchmark both databases" --area Backend --evidence-for D05
dg task link T20 --evidence-for D08     # ...or after the fact, once work reveals D08
```

Which makes the frontier honest — an open decision with a spike running reads
`waiting on evidence from T09` rather than `decidable now` — and buys the check
worth the whole exercise:

```
! [evidence_unharvested] T09 is DONE and was to inform D05 (Which cache?),
  which is still unsettled — record what it showed with `dg decide D05`
```

*The spike ran and nobody wrote down the conclusion.* Invisible in both graphs
separately: the task looks finished, the decision merely undecided.

One rule keeps this from becoming a nuisance: **anything a reopen can cause is a
warning, never an error.** If work resting on a reopened decision made the store
invalid, one `dg reopen` would block every commit in the repository until
somebody triaged the backlog — and the check would be switched off the same day.
Decisions are never held hostage by tasks.

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
