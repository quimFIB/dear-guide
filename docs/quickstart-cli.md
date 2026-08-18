# Quick start: the `dg` CLI

Ten minutes from an empty directory to a decision graph that fails CI when it
starts contradicting itself.

## Install

```sh
pip install -e ~/workspace/random/decision-graph-assistant
```

`dg` then works in any project directory containing `decisions.json`. The
project is found by walking up from the cwd, or named explicitly with
`--project PATH` (`-C`) or `$DG_PROJECT`.

## 1. Start a graph

Areas are just grouping labels for the rendered view. Pick a few; you can only
use ones you declared here.

```sh
cd my-project
dg init --areas "Data,Modelling,Infra"
```

Two files appear: `decisions.json` (the store — source of truth) and
`decision-graph.md` (a generated view — **never hand-edit it**).

Add the staging files to `.gitignore` now:

```
.dgraph-pending.json
.dgraph-edit.org
```

## 2. Add the questions

A vertex is a **decision the project must make** — a question, not a task.

```sh
dg add --id D01 --title "Which corpus do we train on?" --area Data
dg add --id D02 --title "Tokenizer: BPE or unigram?" --area Modelling --after D01
```

`--after D01` says D02 depends on D01. Dependency is the graph structure, never
a stored field, so this is the only place it is written down.

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
would be invalid — so a bad batch costs you nothing. `dg drop N` unstages one
op, `dg clear` all of them.

Staged work lives in `.dgraph-pending.json`, which is gitignored — so **apply
your own work**. Leaving it staged means it exists only in a file no diff will
ever show. (The commit gate will nag you about exactly this.)

## 4. Record a decision

Pass every field as a flag. The command prompts for anything missing, which is
fine for a human and a hung command for a script.

```sh
dg decide D01 \
  --answer "CommonCrawl 2024-26 plus the internal corpus." \
  --source "report/corpus-sweep.md" \
  --falsifier "held-out perplexity worsens when the corpus grows" \
  --opens D02
dg apply
```

Three fields carry the weight:

- **`--source`** — where the evidence lives: a path, a script, or `discussion`.
- **`--falsifier`** — what evidence would overturn this, written *before* that
  evidence arrives. Required whenever the decision opens something. If nothing
  could overturn it, say so explicitly: `"ANALYTIC — follows from the corpus
  choice"`.
- **`--opens`** — the decisions this one now makes answerable. Leave it off for
  a terminal decision.

The falsifier is the rule people push back on and the one that pays. Written
afterwards it is rationalisation; written first it is a commitment.

## 5. Read the graph

```sh
dg                  # the frontier: everything still open or blocked
dg brief            # ...plus provisional work, staging, validity
dg node D01         # one decision in full, with its superseded history
dg path D01 D09     # the chain of evidence between two decisions
dg tree             # the DAG
dg areas            # counts by area and status
```

`dg node D01` after the decision above:

```
╭─────────────────── D01 ────────────────────╮
│ Which corpus do we train on?               │
│                                            │
│ status      DECIDED                        │
│ area        Data                           │
│ depends on  —                              │
│ opens       D02                            │
│ falsifier   held-out perplexity worsens …  │
│ source      report/corpus-sweep.md  (…)    │
│                                            │
│ Answer                                     │
│ CommonCrawl 2024-26 plus the internal …    │
╰────────────────────────────────────────────╯
```

## 6. Reverse one

This is what the graph is really for. Reversals are kept forever, never
deleted.

```sh
dg reopen D01 --why "the 3B run contradicts it" --yes
```

```
╭──────────────── reopen D01 ─────────────────╮
│ Its answer becomes superseded; its          │
│ dependencies stay.                          │
│                                             │
│ 1 decided descendant(s) rest on it and      │
│ become PROVISIONAL:                         │
│   D02                                       │
╰─────────────────────────────────────────────╯
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
# tests/test_decision_graph.py
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

- [The web interface](quickstart-web.md) — the same graph, laid out and
  clickable.
- [The agent plugin](quickstart-agents.md) — the brief at session start, and a
  commit gate, for Claude Code and opencode.
