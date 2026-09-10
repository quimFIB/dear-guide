# Quick start: dear-guide, by example

A reference of worked examples, as one self-contained page:
[`index.html`](index.html). It answers *how do I do that with dear-guide?*
twenty times. Each answer opens on a situation the team behind one small,
imaginary project runs into, then shows a real transcript of what `dg` does
about it, with a picture of the graph beside it.

```sh
xdg-open quick-start-demo/index.html      # no server, no build
```

The quick path — the short example of each recipe — reads in about half
an hour. Under every one is a fold, *the fuller example*, that walks further
into the same project; open it only where you want more.

| part | recipes |
|---|---|
| **Build** | start a graph · add a question · settle it · add work · link what exists · remove a record made in error |
| **Ask** | the frontier · why a decision is where it is · the backlog and moving work · find by what it says · a fact arrives · changing an answer · keeping it honest |
| **Agents** | one agent works the frontier · which tasks can run at once, and several agents in one tray · what a session gets from the plugin |
| **Beyond** | bringing a colleague's clone in |
| **Annex** | what would settle it, written down before it is settled · what a reader shows while something is staged · an answer that rests on two measurements, and running only some checks at a time |

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
a coding-agent host; and `dg serve`, the web app, is not shown.

Two more are stand-ins. The benchmark results in recipes 18 and 20 are
written by the recipe scripts rather than timed, since notelit is imaginary:
what matters is that a benchmark leaves a file, and that the graph names the
file and never a command. And the `grep`
domain that checks those files is not part of dear-guide: it is forty lines
under `grep-domain/`, which `lib.sh` puts on `PYTHONPATH` so `dg probe`
finds it through the `dgraph.domains` entry-point group as if it were
installed. Only `prose` ships, and it never judges; `grep` is here so the
page can show a verdict, and as the domain an author copies. The other
two demos cover neighbouring ground — [`demo/`](../demo) is one graph holding
every kind of record, served; [`demo-agentic/`](../demo-agentic) is three
agents and a day's work, as a story.
