<img src="assets/logo.svg" width="76" alt="">

# dear-guide

> *Dear guide, why did we make this decision?*
>
> ```sh
> dg why D06
> ```

Track a project's development as two linked graphs: the **decisions** it has
settled — what each rests on and what evidence would reopen it — and the
**work** that follows from them.

Six months on, that question has an answer: the premise the decision rested on,
the evidence that reached it, the falsifier that would overturn it, and whether
any of it still holds. `dg` is the guide; you are the one who writes it.

Prose drifts. Two documents come to disagree about whether something was
settled; a decision gets quietly reversed and the conclusions built on it stay
standing, along with the work already underway on top of them. Keeping both in
structured stores makes that drift a test failure.

## Install

```sh
pip install -e /path/to/dear-guide
```

`typer` and `rich` are the whole of the requirement. `dg` then works in any
directory holding `decisions.json`, `tasks.json`, or both — found by walking up
from the cwd, or set with `--project PATH` or `$DG_PROJECT`.

**This is beta, and there are no release numbers yet.** `dg --version` prints
the commit the installed copy was built from — as does `dg-agent --version`,
`/dg:version` in either host, and the stamp in the browser page's header. To
see whether an install is current, compare it against the checkout it came
from:

```sh
dg --version                                   # e.g. 8ec4171
git -C /path/to/dear-guide log -1 --format=%h  # the same, if it is current
```

A trailing `-dirty` means that checkout has uncommitted changes to tracked
files, so the hash names the last commit rather than the code being run. A
number here would have to be bumped on every change to say as much, and one
that has to be bumped by hand is one that gets skipped — which reports a copy
from March and a copy from today as the same thing.

## Five minutes

```sh
dg init                                   # start a graph
dg add --id D01 --title "Exact or approximate search?" --area Search
dg add --id D02 --title "Which distance metric?" --after D01
dg apply                                  # nothing is written until you say so

dg decide D01 \
  --answer "Exact: one brute-force scan, 8 ms a query across eight cores." \
  --source "bench/scan-latency.md" \
  --falsifier "the corpus passes ~10M vectors, where a scan stops holding p99 under 50 ms" \
  --opens D02
dg apply

dg                                        # the frontier: what is still open
dg why D02                                # the chain of premises underneath it
dg check                                  # every invariant
```

Six months later the corpus passes 10M vectors, the falsifier fires, and
`dg reopen D01` marks every decision resting on it `PROVISIONAL` — including
the ones nobody remembered were resting on it.

## What it is, in one screen

**Two stores, and one link between them.** `dg init` and `dg task init` are
independent: track only decisions, only work, or both.

A **decision** is a question the project must answer, with an explicit status —
`DECIDED` · `OPEN` · `BLOCKED:<id>` · `REOPENED` · `PROVISIONAL`. Answering one
requires a **falsifier**: what evidence would overturn it, written before that
evidence arrives. Dependency is the graph structure, never a stored field, and
a reversal is kept forever rather than deleted.

A **task** is a unit of work — `TODO` · `DOING` · `PARKED` · `DONE` ·
`DROPPED`. Blocked-ness is derived from the edges, so there is no status to go
stale. Finishing records an outcome; stopping records why, either way.

The **seam** is one thing: a task names the decisions it exists `because` of,
and the one decision its outcome is `evidence_for`. Nothing else crosses. That
is what answers the question neither store can answer alone — *is this work
still resting on something we believe?*

There is a web app (`dg serve`), an org-mode compose buffer that works like
`git commit`, and a plugin for Claude Code and opencode that injects the
frontier at session start and refuses a commit that would leave the graph
contradicting itself.

## Where to start

| | |
|---|---|
| [**The cookbook**](quick-start-demo/index.html) | seventeen worked examples of *how do I do that?*, every line a real transcript, with the graph drawn beside each step. **Start here.** |
| [How it works, and why](docs/how-it-works.md) | one project's decisions from first question to first reversal — the orientation, not a manual |
| [The CLI](docs/quickstart-cli.md) | the commands in the order you meet them |
| [The web app](docs/quickstart-web.md) | `dg serve`: the DAG, the trays, and what stays in the terminal |
| [Coding agents](docs/quickstart-agents.md) | the Claude Code and opencode plugin, and [a whole session with it](docs/session-walkthrough.md) |
| [Reference](docs/reference.md) | the model, the repo layout, every command, and every rule `dg check` enforces |

Then, as you need them: [composing in an editor](docs/emacs.md), [the design
behind `dg find`](docs/query-framework.md), [running a fan-out of several
agents](agentic/README.md) (three copy-paste recipes in
[`agentic/QUICKSTART.md`](agentic/QUICKSTART.md)), and the questions this design
has [not settled](docs/open-questions.md).

Two runnable demos: [`demo/`](demo/) is a graph holding one of every record this
keeps, served in the web app; [`demo-agentic/`](demo-agentic/) is three agents
and a day's work on one graph, as seven scenes.

## Licence and provenance

[MIT](LICENSE). Use it, change it, ship it; it comes with no warranty of any
kind, which the licence says at more length and in capitals.

**Written by Claude** — Anthropic's coding model — working from my direction
over a long series of sessions: I set the scope and the constraints, made the
calls the design turns on, and decided what got fixed and in what order. Claude
wrote essentially all of the code, the tests and the prose, including this
sentence. Saying so seems better than letting anyone guess, and better than
implying a kind of authorship a model cannot hold: copyright vests in people, so
whatever subsists here is mine, and the licence above is how I hand it on.

The design is what it is because of that process, not in spite of it. The
comments argue for themselves at unusual length because the argument is the part
that was expensive; the invariants are strict because a model writing code
against them is exactly the reader they were built for.
