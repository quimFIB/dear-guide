---
name: dear-guide
description: >-
  Read, record and reverse this project's decisions, and track the work that
  follows from them, using the `dg` CLI over decisions.json and tasks.json. Use
  when settling a design question, when asked what was already decided or why,
  when reversing or revisiting an earlier decision, when a plan touches
  something already settled, when planning or picking up a piece of work, when
  work's premise is reopened and its status must follow, when finished work
  leaves a question still open, or when a commit is refused because the graph
  is invalid. Covers the model (explicit status, a mandatory falsifier,
  append-only history), the link between the two graphs, the command table, and
  the staging workflow.
---

# The development graph

`decisions.json` is the sole source of truth for what this project has decided.
`decision-graph.md` is a **generated view** of it — read it, never edit it.
Nothing else in the repo records decision state, and nothing else should start
to. Work through `dg`.

If there is no `decisions.json` at or above the working directory, this project
does not track decisions this way. Nothing here applies, and you should not
create a graph uninvited.

## The model

Vertices and edges, nothing else.

- A **vertex** is a decision the project must make, carrying an explicit status:
  `DECIDED` · `OPEN` · `BLOCKED:<id>` · `REOPENED` · `PROVISIONAL`.
- An **edge** is a dependency, which gains a payload — answer, falsifier, source,
  date — once that decision is made. An edge with no answer means *B depends on
  A, and A is not settled yet*.
- **Dependency is the graph structure**, never a stored field. It used to be
  stored twice, in opposite directions, and 11 of 55 nodes disagreed.
- **Superseded is not a status.** It is an edge marked inactive and kept forever.
  The reversals are the most valuable thing the graph holds.
- A **terminal** decision is one whose edge opens nothing.

## Rules

1. **Status is explicit, never inferred.** A decision can have consequences and
   still be under review, so out-degree never means "done".
2. **Append-only.** Never delete a vertex or an edge, and never rewrite an
   answer in place — a reversal marks the old edge inactive and adds a new one.
3. **Every closed decision records a falsifier**: what evidence would overturn
   it, written *before* that evidence arrives. Afterwards it is
   rationalisation. If nothing could overturn it, write `ANALYTIC — <why>`
   rather than leaving it blank.
4. **Every decision cites a source** — a file in the repo, a script, or
   `discussion`.
5. **Reopening propagates.** Every decided descendant of a reopened decision
   rests on a premise under review and becomes `PROVISIONAL`. `dg reopen`
   computes that set; do not work it out by hand.
6. **One active answer per decision.** A decision that already has one must be
   reopened before it can be answered again.
7. **Decisions only in this store** — not milestones, not file lists. The graph
   stops being useful the moment it becomes a tracker. Work belongs in the task
   store, which is a separate graph with its own ids: see "Recording work"
   below. The test is whether you can write a falsifier (a decision) or a
   definition of done (a task).
8. **Update the graph in the same commit** as the work that changed it, and
   commit `decisions.json` and `decision-graph.md` together.

## Reading

| Command | Gives |
|---|---|
| `dg brief` | what matters right now: the frontier, anything provisional, staged work, validity |
| `dg show` | the frontier — everything still open or blocked, one line each |
| `dg find QUERY` | decisions and work by what they *say* — the only reading that starts from a word rather than the frontier or an id. `dg find 'is:decidable'`, `dg find 'under:D04 is:unsettled'`, `--ids` to pipe. `answer:`/`falsifier:`/`source:` read superseded edges too and label a hit `superseded answer`; `--active` narrows them to what stands |
| `dg node ID` | one decision in full, each superseded edge with its own targets, falsifier, source and archived answer. `--active` for the answer that stands, which says how many records it left out |
| `dg context ID` | the chain of premises it rests on. Takes a `T` id too; `--full` for the answers, sources and falsifiers |
| `dg path A B` | the chain of evidence between two decisions |
| `dg tree` | the graph as a tree |
| `dg areas` | counts by area and status, one table per store |
| `dg export ID` | the same data as JSON, for machine reading. `dg import` reads it back unchanged |
| `dg check` | every invariant, and it names the rule that broke |
| `dg import FILE` | adopt a `decisions.json` prepared elsewhere or exported from another project, refusing one that breaks invariants |

### Before building on something, or handing it off

`dg node` tells you what a decision says. `dg context` tells you **why**, and
what would make it stop being true: every premise underneath it, ending with
the reading — whether anything in the chain is still under review.

It has two lengths, and which you want depends on who is reading:

- **the default is schematic.** A `CHAIN` line showing the shape of the
  reasoning, oldest premise first, with `!` on any link that is not settled;
  then one line per premise, its answer clipped to a sentence. Use this when
  *you* are the one asking.
- **`--full` prints the chain in full** — each answer, the evidence that
  reached it, the falsifier that would overturn it. Use this when the output is
  going somewhere that cannot ask a follow-up question.

Run it in two situations:

- **before writing code that depends on a decision**, so you know which
  falsifiers you must not quietly trip;
- **before dispatching a subagent**, and paste the `--full` output into the
  prompt. A fresh context knows the task and nothing about why it exists;
  without the chain it cannot tell a constraint from an implementation detail,
  and a clipped answer is exactly the detail it will get wrong.

```sh
dg context D02          # a decision and its premises, schematically
dg context D02 --full   # ...with every answer, source and falsifier
dg context T14          # the work, then the chain behind the decision it exists for
```

The same split runs through `dg show`, `dg task` and both staging trays: one
line each by default, `--full` for the table with nothing clipped. Titles and
details get clipped; **ids never do**, so anything named in a short view can be
looked up from it.

## Recording a decision

Pass every field as a flag — the command prompts for anything missing, and a
prompt with nobody to answer it is a failed command.

```sh
dg decide D37 \
  --answer "HNSW with M=32, efConstruction=200, from the sweep in bench/ann-sweep.md" \
  --source "bench/ann-sweep.md" \
  --falsifier "recall@10 against exact search falls below 0.95 on the held-out queries" \
  --opens "D40,D41"
dg apply
```

`--falsifier` is required whenever the decision opens anything. Two shapes:

- measurable — `"p99 query latency goes above 50 ms at the corpus size we serve"`
- analytic — `"ANALYTIC — cosine and inner product rank identically on
  L2-normalised vectors; no measurement bears on it"`

`--opens` lists the decisions this one now makes answerable; leave it off for a
terminal decision. Nothing is written until `dg apply`, which validates a copy
first and refuses to write a graph that would be invalid. Apply your own work —
leaving it staged means it exists only in a gitignored file.

## Reversing one

```sh
dg reopen D06 --yes --why "the crawl finished at 48M vectors, five times the size this was measured at"
dg apply
```

The output lists every decided descendant that just became `PROVISIONAL`. **That
list is the point of the command.** Each one now rests on a premise under review,
and each needs one of two things once the premise is settled again:

- it still holds → `dg confirm D12`, which records that you re-read it under the
  new premise
- it does not → `dg reopen D12`, then decide it again

Do not reach for reopen to escape a status. A reversal that never happened is a
lie in the record, which is worse than an unfinished one.

## Something the graph has no vertex for

If you settle a question that is not in the graph, add it and then decide it —
do not stay silent.

```sh
dg add --id D42 --title "Quantisation for the served vectors" --area Serving \
       --after D37 --note "blocked on the index structure"
dg decide D42 --answer … --source … --falsifier …
dg apply
```

`dg pending` reviews staged work — one line per op, `--full` for the table —
`dg drop <id>` removes one op, `dg clear` all of it.

## Recording work

Only if the project has a `tasks.json`; if it does not, this section does not
apply and you should not create one uninvited.

Tasks are a second, independent graph — `T` ids, their own store and view, their
own commands. A task is a unit of work with an `outcome`; a decision is a
question with a falsifier. They never share a store.

```sh
dg task                                   # outstanding work, and what is startable
dg task tree                              # the order of it: prerequisites, then what they release
dg task add --id T14 --title "Build the HNSW index and sweep efSearch" --area Search \
            --after T09 --because D02
dg task done T14 --outcome "PR #241"
dg apply
```

- `--after` names tasks that must be resolved first. Blocked is derived, so
  there is no blocked status to set and none to clear.
- `--because D02` names the decision this work exists because of. Use it
  whenever the work follows from a recorded decision — it is what lets
  `dg reopen` report the work now resting on a premise under review.
- `--evidence-for D05` names a decision this work will *inform* — a benchmark,
  a spike, or a chore that turned up a question. `dg task link T14
  --evidence-for D08` adds it after the fact, which is the usual case when work
  reveals a new question.
- If work turns up a question nobody had written down: add the decision, then
  link the task to it. Do not leave it in prose.

Corrections have commands; never hand-edit `tasks.json`:

```sh
dg task dep T14 --after T09        # a prerequisite discovered later
dg task undep T14 --after T09      # ...and removing one
dg task unlink T14 --because       # drop a link recorded against the wrong decision
dg dep   D07 --after D03           # a premise discovered later
dg undep D07 --after D03           # ...and removing one
```

`dg undep` works only on a **bare** edge. A decided edge's targets are part of
its answer, so `dg reopen` first, then remove, then decide again meaning it.

**Never reach for `dg rm` or `dg task rm` yourself.** They erase a record
instead of superseding it, they are for things that should never have been
written, and `dg gate` answers `ask` on them — the user decides, not you. If a
node looks wrong, say so and let them run it.

When work turns up a **chore** rather than a question, record where it came
from — `--discovered-during` on `dg task add`, or `dg task dep T14
--discovered-during T09` afterwards. It makes T14 wait on nothing; it says only
that doing T09 revealed it. Use it for the chore and a decision for the
question: a manufactured decision that nobody actually had to make is worse
than no record. Both relations can hold between the same two tasks, so `dg task
undep` requires the flag that names which one you are removing.

`dg check` warns when work rests on a decision that has been reopened, and when
a finished `--evidence-for` task's decision is still unsettled — that second one
means a benchmark ran and its conclusion was never recorded. Both are warnings and
never block a commit.

Two things about the link are errors rather than warnings: a link naming a
decision that does not exist, and a cycle across the two graphs (work that must
finish before a decision that the work exists because of). `dg apply` refuses a
batch that would create either, names the op, and writes nothing — so the fix is
always `dg task drop-op <id>` or restating the link, never editing the store.

## When a commit is refused

A refusal quotes the violations. Run `dg check` — it names the rule that broke
and what to do about it. Two common ones: a stale view is fixed by `dg render`,
and staged work by `dg apply`.

## Never

- Hand-edit `decision-graph.md`. It is regenerated and your edits are lost.
- Delete a vertex or an edge, or overwrite an existing answer.
- Invent a status outside the five above.
- Record a plan step or a file list anywhere, or a task in `decisions.json` —
  work goes in the task store, with a `T` id.
- Close a decision without a falsifier and a source.
