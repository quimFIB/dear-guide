# Quick start: dear-guide, by example

A reference of worked examples, as one self-contained page:
[`index.html`](index.html). It answers *how do I do that with dear-guide?*
seventeen times, each answer a real transcript against one small synthetic
project with a picture of the graph beside it.

```sh
xdg-open quick-start-demo/index.html      # no server, no build
```

The quick path — the short example of each recipe — reads in about twenty
minutes. Under every one is a fold, *the fuller example*, that walks further
into the same project; open it only where you want more.

| part | recipes |
|---|---|
| **Build** | start a graph · add a question · settle it · add work · link what exists · remove a record made in error |
| **Ask** | the frontier · why a decision is where it is · the backlog and moving work · find by what it says · a fact arrives · changing an answer · keeping it honest |
| **Agents** | one agent works the frontier · which tasks can run at once, and several agents in one tray · what a session gets from the plugin |
| **Beyond** | bringing a colleague's clone in |

## Everything on the page ran

Nothing on the page was typed in by hand except the prose. Each recipe is a
script under `recipes/` with two functions, `quick` and `full`; `run.sh`
runs every one against a fresh copy of the seed, captures the transcript and
exports both stores after every command; `build.py` turns that into the page.
So a command, an output line or a picture is there because it happened.

```sh
./quick-start-demo/run.sh            # every recipe, then build index.html
./quick-start-demo/run.sh 03 11      # two of them, then build
bash quick-start-demo/recipes/03-decide.sh quick   # one layer, to the terminal
```

Everything happens under `/tmp/dg-quick-start` (`$DG_DEMO_DIR` to move it),
and the work directory has to prove it is the demo's before anything in it
is removed. Nothing you do here can be a mistake.

**The highlighted lines are the test.** `build.RECIPES` holds one regex per
highlighted line; `tests/test_quick_start_demo.py` runs each recipe for real
and searches its transcript for every one. Reword a `dg` message the page
points at and the test fails, which is the point: the message and the page
explaining it are one artefact.

## The seed

`notelit`, an imaginary CLI that indexes a folder of markdown notes. Eight
decisions, one reversed; eleven tasks, one in every status; a decidable
question, one awaiting evidence, and one blocked on a premise. `seed.sh`
builds it from an empty directory with `dg` commands and then dates its
records, so the project has a history; the dates are the only hand-written
data. Re-run it after changing it; the result is committed under `seed/`.

## What is not run

Two things the page shows but does not execute, and says so: the launcher
`dg-agent setup` writes is printed rather than run, because running it needs
a coding-agent host; and `dg serve`, the web app, is not shown. The other
two demos cover neighbouring ground — [`demo/`](../demo) is one graph holding
every kind of record, served; [`demo-agentic/`](../demo-agentic) is three
agents and a day's work, as a story.
