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

**If you are supervising this run, you can answer its consent requests too.**
An agent that needs a command outside `$DG_EXEC_ALLOW`, or a write outside the
launch scope, blocks on the broker. Started with `dg-agent broker --relay`, it
publishes each request instead of prompting on a terminal you cannot reach:

```sh
dg-agent consent            # what is waiting, and what answering it would mean
dg-agent consent --allow --why "…"
```

**Read the rung before answering — `dg-agent consent` prints it.**

- `user` or `scoped`: the verdict is logged as `person`. Put the request to the
  person, relay their answer, and do not decide it yourself; answering there
  would write `person` for something no person chose.
- `auto`: the verdict is logged as `auto`, and deciding it is what that rung is
  for. Judge it on what the agent is holding, the reason the gate gave, and
  whether the target is inside the floor — and say why in `--why`, because that
  reason is what a supervisor reads afterwards.

Either way, answer within about a minute and a half: the gate gives up at 100
seconds and the agent never sees a later verdict.

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
dg-agent setup --preset contributor --focus T04,T07 --agents 3 --budget 30m \
  --brief "…" --read "path:what it is" --findings "findings/<id>.md"
```

**Offer the remit as a word rather than as six policy questions, and offer it
through this host's question surface.** The `--json` block at the top of this
command already carries `presets` and `default_preset` — do not call for them
again. Put one option per preset, in the order `--json` gives them, using each
one's `row` as its description and marking `default_preset` as the recommended
one, then pass the answer as `--preset`.

Ask it as a question with options rather than in prose wherever the host has a
surface for that. Six policy flags is not a thing to have an opinion about
before the run, and a person reading three named remits side by side picks in a
second what they would otherwise decline to read about — which is the whole of
the decision behind presets. Where the host has no such surface, ask the same
question the same way in prose; the answer is one of three words either way.

**Do not invent a fourth option, and do not fold the other three answers into
this question.** A person who wants something none of the three is gets the
policies spelled out, below — that is the escape hatch, and a preset quietly
edited into something no name describes is the one outcome presets exist to
avoid. The brief, the reads and the findings path are free text and belong in
their own asking:

```sh
dg-agent setup --focus T04,T07 --agents 3 --decide evidence --write launch \
  --budget 30m --terse on --area-policy open \
  --brief "…" --read "path:what it is" --findings "findings/<id>.md"
```

**Start the broker with `--detach` from here.** It puts the broker in its own
session so it outlives the turn, which a plain `dg-agent broker --relay` from a
session's shell call does not — and `launch.sh` now asks whether one is
listening before the first agent, so a skipped broker is a line you read rather
than an agent that stopped. `--detach` is idempotent, so running it twice is
free:

```sh
dg-agent broker --relay --detach --exec-rung auto --write-rung scoped \
  --plan fanout/env.json
```

**Without a confinement floor a relayed verdict is logged `relayed`, not
`person`** — the channel cannot show which hand wrote it. Relaying still works
and the verdict still stands; the log simply stops claiming a warrant it has not
got, and the broker says which you have at the door.

**`--mode session` is a different kind of run, and usually not the one you
want.** Everything above assumes each agent is a child of `dg-agent run`, which
is what enforces its name, floor, budget and write scope. Under `--mode session`
the session spawns them itself and all of that becomes advisory — on opencode
the write scope and commit gate are absent outright (opencode#5894). You can
supervise a whole fan-out from here *without* it: set up, `--detach` the broker,
run `./fanout/launch.sh`, answer consent, read `dg pending`. Reach for `--mode
session` only with a reason that shape does not serve, and read what it prints
before you launch. It is built for this command: run from a terminal it is not
refused, but it is not the intended workflow, and outside Claude Code or
opencode only `--mode process` has guaranteed behaviour.

**With `--roster` in that mode, you are the carrier.** Nothing sets `$DG_TASK`
for a subagent you spawn, so hand each agent its task in the spawn
instructions — one agent per roster id, in the order you gave them. The setup
report prints the roster back, and the prompt tells each agent to look in its
instructions rather than in a variable (`D61`).

**Each agent is assigned a task, and by default the graph chooses them.**
`--agents 3` computes a maximal set of ready tasks no two of which collide —
two tasks collide when one is evidence for a decision the other names
(`D45`) — and launches one agent per task, each reaching its own as
`$DG_TASK`. The report says what was assigned and, where fewer independent
tasks are ready than agents asked for, launches that many and names the pair
holding each task out, so you can break the pair or mean it. With nothing
ready, N agents read the frontier and take what they find. An assigned agent
still reads the frontier when its task is done: the roster says where each
begins, not where it stops. `--roster T04,T07,T11` names the tasks yourself
instead, in this order, and is obeyed as written — a pair that may collide is
said, not refused. It sets the count rather than sitting beside it, so passing
both is refused.

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
