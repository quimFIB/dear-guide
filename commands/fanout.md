---
description: Set up a recorded fan-out — several agents proposing into one graph, one person deciding
allowed-tools: Bash(dg agent:*), Bash(dg pending:*), Bash(dg task:*), Bash(command -v dg), Read
---

The graph as somewhere several agents propose and one person decides. Who holds
a name right now:

!`dg agent list`

...whether this project is ready for one, and what `dg agent setup` would use:

!`dg agent setup --json`

...what each of them is holding, and what is ready for whoever asks next:

!`dg task`

...and what is staged, across both trays:

!`dg pending`

Read `agentic/README.md` in the dear-guide checkout before setting one up — it
is the procedure, and three things in it are easy to get wrong by improvising.

**The rule, and the environment variable that makes it one.** Agents may
`dg add` and `dg task add`; by default only the supervisor runs `dg decide`. A
fan-out is a search and the graph is a record: an agent adding an OPEN question
is cheap and reversible, and an agent deciding writes a falsifier for a question
nobody made it live with. `$DG_DECIDE` turns that from a habit into a refusal —
`evidence` lets an agent close only a decision a **finished** `--evidence-for`
task backs, which is the case where the falsifier writes itself, and `never`
sends every answer back to a person. It is checked at stage time and only for a
caller with `$DG_AGENT` set, so a supervisor is never refused.

**Names come from the tool, and the environment is the whole contract with the
host.** `DG_AGENT=$(dg agent claim)` per agent, never a name somebody invented —
`claim` refuses to hand out one that is held or that has ops in a tray, so two
agents cannot share one and conflate their work. Whatever spawns an agent only
has to put these in its environment, which is why a fan-out can mix Claude Code,
opencode and anything else that can run `dg`.

**Two further limits, both optional and both off by default.** `$DG_WRITE=launch`
confines an agent's *writes* to the project and `/tmp`; anywhere else stops and
asks the person, and reads are never judged. It is not enforced by the CLI —
a write does not go through `dg` — but by the same `dg gate` both host adapters
already relay, as `dg gate --write PATH`, so one rule covers every host.
`dg agent claim --budget 30m` records how long an agent may run; pair it with
`timeout` in the launcher, since `dg` is not in the agent's process tree and
cannot stop anything itself.

**Nobody has to hand out the work either.** `dg task` ends with a computed
`ready` line, `dg task start` refuses a task somebody already claimed, and
blocked-ness is derived — so *read the frontier → claim → do → `dg task done` →
repeat* is a loop an agent runs unattended, and one agent finishing makes work
startable for another that was never told it existed. Two things to put in the
prompt: `dg apply --mine` right after the claim, since a `start` sitting in the
tray leaves the queue reading `startable` and shows no `held by`; and
`dg task park --why` when an agent stops, since a task left `DOING` by an agent
that died is indistinguishable from one being worked on.

**Hand back before you release.** `dg agent list` shows who is over budget and
still holding work, and who has gone quiet while holding it; `dg agent expire`
stages a park for each out-of-time agent, under that agent's own name. Do this
before `dg agent prune` — not because prune is dangerous (it keeps back any name
still holding `DOING` work, and `release` refuses one) but because a park that
names *why* is what the next agent needs, and prune only tells you it declined
to release the name.

**Recording the run is OPTIONAL and usually not wanted.** A fan-out leaves
behind what it decided, which is what the graph is for. If this run needs to
become a demo, or somebody has to audit what was proposed rather than what was
taken, `agentic/bin/dg` first on `$PATH` records every call and both trays —
turn it on before the run, not during.

**`dg agent setup` writes the prompt and the launcher.** Most of what a scout
prompt needs is already in the graph — the project, the areas, the full chain
behind each focus id (pasted verbatim, which is the part a fresh context cannot
reconstruct), which policies are in force and what each means, the write roots,
the budget. Three answers are not: what the fan-out is *for*, what the agents
may read, and where findings go.

From a session, ask the person those three, then call it with flags — both
interactive forms need a real terminal and cannot be driven from here, and the
command says so rather than prompting into EOF:

```sh
dg agent setup --focus T04,T07 --agents 3 --decide evidence --write launch \
  --budget 30m --brief "…" --read "path:what it is" --findings "findings/<id>.md"
```

`--dry-run` prints both files without writing them. It produces `fanout/scout.md`
and `fanout/launch.sh`; read the prompt before launching, since the three answers
above are the ones worth checking.

Review one proposal at a time: `dg pending --agent <name>` to read it, `dg serve`
to see it as a graph with staging applied, `dg apply --agent <name>` to take it,
`dg clear --agent <name>` to turn it down without touching the others.

$ARGUMENTS
