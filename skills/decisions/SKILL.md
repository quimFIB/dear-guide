---
name: decisions
description: >-
  Read, record and reverse this project's decisions using the `dg` CLI and
  decisions.json. Use when settling a design question, when asked what was
  already decided or why, when reversing or revisiting an earlier decision, when
  a plan touches something already settled, or when a commit is refused because
  the decision graph is invalid. Covers the model (explicit status, a mandatory
  falsifier, append-only history), the command table, and the staging workflow.
---

# Decisions

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
| `dg show` | the frontier — everything still open or blocked |
| `dg node ID` | one decision in full, including its superseded history |
| `dg path A B` | the chain of evidence between two decisions |
| `dg tree` | the graph as a tree |
| `dg areas` | counts by area and status |
| `dg export ID` | the same data as JSON, for machine reading |
| `dg check` | every invariant, and it names the rule that broke |

## Recording a decision

Pass every field as a flag — the command prompts for anything missing, and a
prompt with nobody to answer it is a failed command.

```sh
dg decide D37 \
  --answer "32k BPE, from the sweep in report/tokenizer-sweep.md" \
  --source "report/tokenizer-sweep.md" \
  --falsifier "held-out perplexity gets worse when the corpus grows" \
  --opens "D40,D41"
dg apply
```

`--falsifier` is required whenever the decision opens anything. Two shapes:

- measurable — `"held-out WER goes above 12% on the next full run"`
- analytic — `"ANALYTIC — follows from the corpus choice; no measurement bears on it"`

`--opens` lists the decisions this one now makes answerable; leave it off for a
terminal decision. Nothing is written until `dg apply`, which validates a copy
first and refuses to write a graph that would be invalid. Apply your own work —
leaving it staged means it exists only in a gitignored file.

## Reversing one

```sh
dg reopen D06 --yes --why "the 3B run contradicts it"
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
dg add --id D42 --title "Serving format for the first release" --area Infra \
       --after D37 --note "blocked on the tokenizer choice"
dg decide D42 --answer … --source … --falsifier …
dg apply
```

`dg pending` reviews staged work, `dg drop N` removes one op, `dg clear` all of
it.

## Recording work

Only if the project has a `tasks.json`; if it does not, this section does not
apply and you should not create one uninvited.

Tasks are a second, independent graph — `T` ids, their own store and view, their
own commands. A task is a unit of work with an `outcome`; a decision is a
question with a falsifier. They never share a store.

```sh
dg task                                   # outstanding work, and what is startable
dg task add --id T14 --title "Migrate the database" --area Backend \
            --after T09 --because D02
dg task done T14 --outcome "PR #241"
dg apply
```

- `--after` names tasks that must be resolved first. Blocked is derived, so
  there is no blocked status to set and none to clear.
- `--because D02` names the decision this work exists because of. Use it
  whenever the work follows from a recorded decision — it is what lets
  `dg reopen` report the work now resting on a premise under review.
- `--evidence-for D05` names a decision this work will *inform* — a spike, or a
  chore that turned up a question. `dg task link T14 --evidence-for D08` adds it
  after the fact, which is the usual case when work reveals a new question.
- If work turns up a question nobody had written down: add the decision, then
  link the task to it. Do not leave it in prose.

Corrections have commands; never hand-edit `tasks.json`:

```sh
dg task dep T14 --after T09        # a prerequisite discovered later
dg task undep T14 --after T09      # ...and removing one
dg task unlink T14 --because       # drop a link recorded against the wrong decision
```

`dg check` warns when work rests on a decision that has been reopened, and when
a finished `--evidence-for` task's decision is still unsettled — that second one
means a spike ran and its conclusion was never recorded. Both are warnings and
never block a commit.

Two things about the link are errors rather than warnings: a link naming a
decision that does not exist, and a cycle across the two graphs (work that must
finish before a decision that the work exists because of). `dg apply` refuses a
batch that would create either, names the op, and writes nothing — so the fix is
always `dg task drop-op N` or restating the link, never editing the store.

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
