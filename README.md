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
| `.dgraph-edit.org` | editor buffer, like `COMMIT_EDITMSG`; gitignore it |
| `demo/` | a runnable graph + walkthrough for the emacs-from-browser flow |
| `skills/decisions/` | the recording discipline, as a skill both agent hosts load |
| `hooks/`, `.claude-plugin/` | the Claude Code plugin |
| `opencode/` | the same two mechanisms for opencode |

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
dg brief                                 # ...plus provisional work, staging, validity
dg node D06                              # one decision in full
dg path D01 D09                          # the chain of evidence between two
dg tree                                  # the DAG
dg decide D37                            # compose a decision -> staged
dg decide D37 --edit                     # ...in emacs, with context to hand
dg reopen D06                            # stage a reopen + its propagation
dg confirm D12                           # a provisional decision, re-examined and standing
dg pending                               # review
dg apply                                 # validate, then write both files
dg check                                 # every invariant
dg serve                                 # web app on 127.0.0.1:8765
dg edit 0                                # revise a staged op
dg export                                # the graph as JSON
```

Decisions are **staged** first and only reach the store on `apply`, which
validates a copy and aborts without writing if the result would be invalid.

## The web app

`dg serve` gives a layered-DAG view — colour by area, outline by status, faint
edges for dependencies awaiting a decision. Click a vertex to inspect it, or to
fill in a decision; staged ops collect in a tray and apply together. It shares
the CLI's apply path, so there is one implementation of it.

Unstaged work is per-decision and survives navigation: type half an answer,
go read the premise it depends on, come back, and it is still there. The same
holds while an editor is open — the graph stays browsable, and the result is
reported against the decision it was about, not whichever one is on screen when
the editor exits. Drafts are in memory only, so a reload clears them.

### Composing in emacs from the browser

Clicking **Compose in emacs** in the panel opens the same org buffer described
below — the browser writes it, waits, and stages what emacs sends back. Anything
already typed into the form carries over, so switching editors mid-thought costs
nothing. `demo/` is a self-contained walkthrough:

```sh
./demo/demo.sh          # a throwaway graph on http://127.0.0.1:8765
```

Two things are worth knowing about this path:

- `$EDITOR` is **ignored** here; `$DG_GUI_EDITOR` (default `emacs`) is used
  instead. `$EDITOR` names a terminal editor by convention and the server has no
  terminal to lend it, so honouring it would hang the request. With no display
  the button is not offered at all.
- Mutating routes require a token that `dg serve` mints per run and embeds in the
  page. Any page in your browser can POST to a localhost server — it just cannot
  read the response — which was tolerable while the API only moved data around
  and is not once a route can start a process.

## Composing in emacs

A one-line prompt is a poor place to write an answer that is supposed to carry
its evidence, and it shows you nothing of what you are deciding *on top of*.
`--edit` works like `git commit`: `dg` writes an org buffer, opens your editor,
waits, and stages what comes back.

```sh
dg decide D37 --edit     # also: dg reopen --edit, dg add --edit, dg edit N
export DG_EDIT=1         # make the editor the default; --no-edit overrides
```

The buffer has two halves. `* Input` is what you fill in — answer, source,
falsifier, and checkboxes for what this decision opens. `* Context` is reference
material: the edge that led here, each premise with its own answer and
falsifier, the ancestor chain. **Only `* Input` is ever read back**, so nothing
you do to the context can change what gets staged.

In emacs you also get:

| key | |
|---|---|
| `C-c C-c` | stage it and return to the shell |
| `C-c C-k` | abort — nothing is staged |
| `C-c C-o` | follow a `dg:` link to that decision (plain `org-open-at-point`) |
| `C-c C-p` · `C-c C-a` | jump to a premise · list every premise it rests on |

The elisp ships with the package and is loaded by `dg` itself, so there is
nothing to install. It is strictly read-only — it can look up decisions, never
change them; staging happens in the CLI after emacs exits.

Any other editor works too: set `$DG_EDITOR` (or `$VISUAL`/`$EDITOR`) and you get
the same buffer as plain text, with the baked-in context but no navigation.

There is one buffer per project, so only one compose session can be open at a
time — the same property `COMMIT_EDITMSG` has. The web app refuses a second one
rather than overwriting a buffer you are typing in.

### Prose in answers

Answers are stored exactly as you type them, and emacs users get the whole of
org — tables, `dg:` and `file:` links, verbatim markers, source blocks. The
generated views convert what they can: org links become markdown links, org
table rules become markdown rules, `=verbatim=` becomes backticks.

One thing does not convert. `*single asterisks*` mean bold in org and italic in
markdown — the same syntax with two meanings — so no renderer can tell them
apart, and the views show italic either way. Write `**bold**` if you care how it
looks outside emacs.

## Checking it in CI

`dg check` exits nonzero on any error. For pytest, one file is enough — the
tool supplies the tests, so a project never restates the invariants and there
is no list to keep in sync:

```python
# tests/test_decision_graph.py
from dgraph.testing import *  # noqa: F401,F403
```

That yields one test per rule, plus one that surfaces advisory findings — an
isolated vertex, a stale `PROVISIONAL` — through pytest's warning summary
without failing the build; the normal look of work in progress stays green. A
check added to the tool shows up in every project automatically.

## What `dg check` enforces

Well-formed unique IDs · legal statuses · no dangling edge or `BLOCKED:`
references · at most one active edge per vertex · every `DECIDED` vertex has a
date, source, falsifier and decision edge · `OPEN`/`BLOCKED` vertices carry no
answer · no `DECIDED` vertex resting on an unsettled premise · nothing
`BLOCKED` on something already settled · nothing left `PROVISIONAL` once its
premises are settled again · no orphans · acyclic · the rendered markdown
matches the store.

## Driving it from a coding agent

A CLI only records what somebody remembers to run, and the thing that forgets is
an agent working across many sessions. The tool ships as a plugin for **Claude
Code** and **opencode**, which turns three of the four habits into mechanisms:

| | how |
|---|---|
| **read the frontier first** | the brief is injected at the start of every session, and again after a compaction — no rule in an instructions file, no "read this first" |
| **know the discipline** | the `decisions` skill: the model, the rules, the flag-complete commands. Loaded on demand, not carried in every context |
| **refuse the contradictions** | a `git commit` that would leave the graph invalid is denied, quoting the rule that broke and the command that fixes it. Work staged and never applied asks the human instead — `.dgraph-pending.json` is gitignored, so committing over it loses the record silently |

What stays a habit is **recording a decision at the moment it is made**. Nothing
a host can observe reveals that something was settled — it is a property of the
reasoning, not of a tool call — and the two obvious implementations both fail. A
turn-end prompt asking "did anything get decided?" is wrong on almost every turn
and teaches the model to say no; a nag on every commit that does not touch
`decisions.json` is wrong on most commits, and the habituation would cost the two
mechanisms that do work. So the brief arrives with ids and titles, the skill is
one hop away, and the compaction boundary asks once, at the moment prose is
actually about to be lost.

### Install

```sh
pip install -e ~/workspace/random/decision-graph-assistant   # the CLI

# Claude Code
/plugin marketplace add ~/workspace/random/decision-graph-assistant
/plugin install decision-graph
```

For opencode it is three symlinks — see [`opencode/README.md`](opencode/README.md).
Two install steps rather than one because the hosts distribute plugins their own
way and neither of them installs Python packages; `dg --version` exists so an
adapter can tell when the two halves have drifted apart.

Nothing happens in a directory with no `decisions.json`: no output, no error, no
cost. `DG_HOOK_OFF=1` switches both mechanisms off.

### One implementation, two hosts

The interesting parts are in `dg`, not in either adapter:

- `dg brief` — what is worth knowing right now: the frontier with what each item
  waits on and what deciding it releases, anything `PROVISIONAL`, the staging
  area, the validity check. `--json` for adapters.
- `dg gate --command "<cmd>"` — is this shell command about to record a
  contradiction? Out comes `allow`, `ask` or `deny` with a reason.

So each adapter is a few dozen lines of translation with no policy of its own,
and `skills/decisions/SKILL.md` is one file both hosts read — opencode uses the
same `name`/`description` frontmatter and will even read `.claude/skills/`
directly. A test asserts the adapters name no `dg` subcommand but those two, and
that the skill's command table only names commands that exist.

## Still open

- **Whether an agent should `apply` unattended.** It does, today: `apply`
  validates a copy before writing anything, the store is append-only, and the
  result is a git-tracked diff reviewed like any other — whereas an op staged
  into a gitignored file and then committed over is unrecoverable. Staging for a
  human to confirm would also mean the commit gate stopped to ask on nearly every
  commit, which is how a gate stops being read.
- **Whether the store stays per-repo.** Probably: decisions are about a codebase.
  A cross-project view would need a different addressing scheme.
- **Whether the falsifier can be checked rather than merely recorded.** A
  falsifier nobody revisits is a comment. Nothing here reads them back.
- **Whether the unrecorded-decision nag is worth trying behind a flag**, and
  whether `decision-graph.md` should eventually be org rather than markdown.
