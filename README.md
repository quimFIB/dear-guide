# decision-graph-assistant

Track a project's decisions as a graph: what is settled, what it rests on, and
what evidence would reopen it.

A long-running project accumulates decisions in prose — plans, notes, memory
files — and prose drifts. Two documents disagree about whether something was
settled; a decision gets quietly reversed and the three conclusions built on it
stay standing. This keeps decisions in one structured store, renders a readable
view of it, and makes the drift a test failure.

## Model

Vertices and edges, nothing else.

- A **vertex** is a decision the project must make, with an explicit status:
  `DECIDED` · `OPEN` · `BLOCKED:<id>` · `REOPENED` · `PROVISIONAL`.
- An **edge** is a dependency, which gains a decision payload — answer,
  falsifier, source, date — once that decision is made. An edge with no answer
  means *B depends on A, and A is not settled yet*.

Three things follow from that shape rather than from convention:

- **Dependency is the graph structure**, never a stored field. Storing it twice,
  in two directions, is how documents come to contradict themselves.
- **Superseded is not a status.** It is an edge with `active: false`, kept
  forever. Reversals are the most valuable thing a decision log holds.
- **Reopening propagates.** Every decided descendant of a reopened decision
  rests on a premise under review and becomes `PROVISIONAL`. `dg reopen`
  computes that set; `dg check` refuses a graph where it was not applied.

Two invariants do most of the work. **Status is explicit** — never inferred from
out-degree, because a decision can have consequences and still be reopened. And
**every closed decision records a falsifier**: what evidence would overturn it,
written *before* that evidence arrives. Afterwards it is rationalisation.

## Files

| File | Role |
|---|---|
| `decisions.json` | the store — source of truth |
| `decision-graph.md` | generated view; **never hand-edit** |
| `.dgraph-pending.json` | staging area; gitignore it |

## Install

```sh
pip install -e ~/workspace/random/decision-graph-assistant
```

`dg` then works in any project directory containing `decisions.json`. It is
found by walking up from the cwd, or set explicitly with `--project PATH` or
`$DG_PROJECT`.

## Use

```sh
dg init --areas "Data,Modelling,Infra"   # start a graph
dg import-md old-decisions.md            # or bootstrap from markdown
dg                                       # the frontier: what is still open
dg node D06                              # one decision in full
dg path D01 D09                          # the chain of evidence between two
dg tree                                  # the DAG
dg decide D37                            # compose a decision -> staged
dg reopen D06                            # stage a reopen + its propagation
dg pending                               # review
dg apply                                 # validate, then write both files
dg check                                 # every invariant
dg serve                                 # web app on 127.0.0.1:8765
```

Decisions are **staged** first and only reach the store on `apply`, which
validates a copy and aborts without writing if the result would be invalid.

## The web app

`dg serve` gives a layered-DAG view — colour by area, outline by status, faint
edges for dependencies awaiting a decision. Click a vertex to inspect it, or to
fill in a decision; staged ops collect in a tray and apply together. It shares
the CLI's apply path, so there is one implementation of it.

## What `dg check` enforces

Well-formed unique IDs · legal statuses · no dangling edge or `BLOCKED:`
references · at most one active edge per vertex · every `DECIDED` vertex has a
date, source, falsifier and decision edge · `OPEN`/`BLOCKED` vertices carry no
answer · no `DECIDED` vertex resting on an unsettled premise · nothing
`BLOCKED` on something already settled · no orphans · acyclic · the rendered
markdown matches the store.
