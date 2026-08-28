---
description: Set up a recorded fan-out — several agents proposing into one graph, one person deciding
allowed-tools: Bash(dg-agent:*), Bash(dg pending:*), Bash(dg task:*), Bash(command -v dg), Bash(command -v dg-agent), Read
---

The graph as somewhere several agents propose and one person decides. Who holds
a name right now:

!`dg-agent list`

...whether this project is ready for one, and what `dg-agent setup` would use:

!`dg-agent setup --json`

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
host.** `DG_AGENT=$(dg-agent claim)` per agent, never a name somebody invented —
`claim` refuses to hand out one that is held or that has ops in a tray, so two
agents cannot share one and conflate their work. Whatever spawns an agent only
has to put these in its environment, which is why a fan-out can mix Claude Code,
opencode and anything else that can run `dg`.

**`dg-agent env` is where you find out what is actually in force.** Three of
these variables **fail open**: a mistyped `$DG_DECIDE=nevr` is read as `open`,
the widest policy, and looks exactly like a policy somebody chose. `dg-agent
env` names a fallback as a fallback, shows the budget against the lease rather
than the variable, and resolves `$DG_PROJECT` to the graph it actually found;
`dg-agent env --check` exits non-zero if anything set was not understood, and
`fanout/launch.sh` runs it before the first agent starts.

**Four further limits, all optional and all off by default.** `$DG_WRITE=launch`
confines an agent's *writes* to the project and `/tmp`; anywhere else stops and
asks the person, and reads are never judged. It is not enforced by the CLI —
a write does not go through `dg` — but by the same `dg gate` both host adapters
already relay, as `dg gate --write PATH`, so one rule covers every host.
`dg-agent run --budget 30m -- <spawn>` is how long an agent may run, and one
number rather than two: `dg-agent run` is the child's parent, so it stops the
child at the budget and parks whatever that child was holding, under the child's
own name. `dg-agent expire` stays the backstop for what it cannot see — the
launcher itself being killed. And `$DG_TERSE=on` refuses a field longer than 400
characters at stage time: the store holds the synopsis somebody reads while
deciding, and the development goes in a file the record cites — `--source` on a
decision, the `--outcome` on a task. Most of what makes a field long is the
chain, and the chain is edges `dg context` already computes. And `$DG_AREA=strict`
stops an agent filing under an area nobody has used yet — off by default,
because a scout finding a corner nobody had named is a finding rather than a
mistake, and the similarity guard already refuses a near-miss of an area in use.

**Nobody has to hand out the work either.** `dg task` ends with a computed
`ready` line, `dg task start` refuses a task somebody already claimed, and
blocked-ness is derived — so *read the frontier → claim → do → `dg task done` →
repeat* is a loop an agent runs unattended, and one agent finishing makes work
startable for another that was never told it existed. Two things to put in the
prompt: `dg apply --mine` right after the claim, since a `start` sitting in the
tray leaves the queue reading `startable` and shows no `held by`; and
`dg task park --why` when an agent stops, since a task left `DOING` by an agent
that died is indistinguishable from one being worked on.

**Hand back before you release.** `dg-agent list` shows who is over budget and
still holding work, and who has gone quiet while holding it; `dg-agent expire`
stages a park for each out-of-time agent, under that agent's own name. Do this
before `dg-agent prune` — not because prune is dangerous (it keeps back any name
still holding `DOING` work, and `release` refuses one) but because a park that
names *why* is what the next agent needs, and prune only tells you it declined
to release the name.

**Recording the run is OPTIONAL and usually not wanted.** A fan-out leaves
behind what it decided, which is what the graph is for. If this run needs to
become a demo, or somebody has to audit what was proposed rather than what was
taken, `agentic/bin/` first on `$PATH` puts both wrappers — `dg` and `dg-agent` — in
front of the real binaries and records every call and both trays. Turn it on
before the run, not during.

**`dg-agent setup` writes the prompt and the launcher.** Most of what a scout
prompt needs is already in the graph — the project, the areas, the full chain
behind each focus id (pasted verbatim, which is the part a fresh context cannot
reconstruct), which policies are in force and what each means, the write roots,
the budget. Three answers are not: what the fan-out is *for*, what the agents
may read, and where findings go.

From a session, ask the person those three, then call it with flags — both
interactive forms need a real terminal and cannot be driven from here, and the
command says so rather than prompting into EOF:

```sh
dg-agent setup --focus T04,T07 --agents 3 --decide evidence --write launch \
  --budget 30m --terse on --area-policy open \
  --brief "…" --read "path:what it is" --findings "findings/<id>.md"
```

`--dry-run` prints the files without writing them. It produces
`fanout/scout.md`, `fanout/launch.sh` and `fanout/env.json` — the last is the
remit the other two were generated from, which is what lets `dg-agent env
--check --plan fanout/env.json` assert that the prompt's claims and the
launcher's settings still agree. Read the prompt before launching, since the
three answers above are the ones worth checking.

Review one proposal at a time: `dg pending --agent <name>` to read it, `dg serve`
to see it as a graph with staging applied, `dg apply --agent <name>` to take it,
`dg clear --agent <name>` to turn it down without touching the others.

$ARGUMENTS
