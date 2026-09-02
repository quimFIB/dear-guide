# Running a fan-out

Every step from an empty terminal to several agents proposing into one graph.
`README.md` beside this file is the *why* — the rule, the ownership model, what
each mechanism is defending against. This is the *how*, and it assumes you have
read none of it.

**If you only want to start a run, read `QUICKSTART.md` instead** — three
copy-paste recipes, one of them the session-answers-the-broker arrangement, and
a short list of what to check when a run looks stuck. Come here when a recipe
is not the shape you need.

Two ways to run one. **Manually**, where you claim the names and spawn the
agents yourself, and **with an orchestrator**, where one agent does that for
you. The second is the first with a wrapper around it; nothing else changes,
and the graph cannot tell them apart.

---

## 0 · Initiation, once per project

```sh
cd /path/to/your-project
dg init          # decisions.json
dg task init     # tasks.json — optional, but the loop below needs it
```

Neither declares a vocabulary. Areas accumulate, so the first record filed
under one registers it — which is also why the two stores can no longer be
started with lists that disagree.

Either store works without the other. `dg init` also writes the tool's scratch
files into `.gitignore`.

**Then put something in the graph.** A fan-out is a search over open questions
and outstanding work; with an empty graph there is nothing for an agent to read
and it will invent its own errand.

```sh
dg add --id D01 --title "Which index for 50M vectors?" --area Search
dg task add --id T01 --title "Benchmark recall at ef=64,128,256" --area Search \
            --evidence-for D01
dg apply --all
dg task                      # ends with a computed `ready` line
```

`--evidence-for` is what makes `DG_DECIDE=evidence` mean anything later: it is
the link that lets an agent close a question *because it measured the answer*.

---

## 0.5 · The short way: `dg-agent setup`

Everything in §1 and §2 by hand, or one command that writes both artefacts:

```sh
dg-agent setup                    # a TUI, if you are at a terminal
```

**It opens on one question, not twelve.** The policy block — what an agent may
settle, where it may write, what it may run, how long a field may be — is the
part nobody can answer without having read `agentic/README.md` first, so the
first question is a curated remit that fills all of it:

```
( ) scout        proposes only · no build tools
(•) contributor  settles what evidence backs
( ) maintainer   settles anything
( ) customise    the tool's own defaults
```

It prefills rather than locks: every field it sets is asked again below with
that value as the default, and `dg-agent presets` prints what each one sets.
`--preset scout` takes the same three names from the flags, where any other flag
still overrides one field.

It checks what is ready, asks what it cannot work out, and writes three files:
`fanout/scout.md` (the prompt, no ⟨…⟩ left in it), `fanout/launch.sh` (one line
per agent) and `fanout/env.json` (the remit both of those were generated from).
Read the prompt, then run the launcher.

**The third file is what keeps the first two honest.** The prompt asserts each
policy to the agent in the second person — "You are running under
`$DG_DECIDE=evidence`" — and the launcher sets it separately; edit either
afterwards, which this file expects you to do, and the prompt goes on asserting
a policy nobody is enforcing to an agent with no way to check. `launch.sh` runs
`dg-agent env --check --plan fanout/env.json` before the first agent starts, so
the two are asserted to still agree.

**Most of the prompt fills itself.** The project, the areas in use, which
policies are in force and what each one *means* in practice, the write roots,
the budget — and the full chain behind each focus id, pasted verbatim from
`dg context --full`, which is the single thing a fresh context cannot
reconstruct. Three answers are yours: what the fan-out is for, what the agents
may read, and where findings go.

**Interactive setup always works; the full-screen form is the upgrade.** With
`textual` installed (`pip install 'dear-guide[tui]'`) you get one screen with
every answer visible at once — worth it, because `never` with a 45-minute
budget is a different run from `evidence` with fifteen, and you can only see
that if both are on the screen. Without it, the same eleven questions are asked
one at a time using nothing the tool did not already depend on — the remit
question included. `--plain` picks that deliberately.

Every answer is also a flag, and this form needs no terminal at all:

```sh
dg-agent setup --focus T04,T07 --agents 3 --decide evidence --write launch \
  --budget 30m --area-policy open --brief "settle the Search frontier" \
  --read "bench/README.md:how the sweep is run" --findings "findings/<id>.md"

# ...or the same remit in a word, with the budget still yours
dg-agent setup --preset contributor --focus T04,T07 --agents 3 --budget 30m \
  --brief "settle the Search frontier" \
  --read "bench/README.md:how the sweep is run" --findings "findings/<id>.md"

# ...or name the work yourself: one agent per task, in this order
dg-agent setup --preset contributor --roster T04,T07,T11 --budget 30m \
  --brief "settle the Search frontier"
```

**`--mode` says where the agents live**, and `process` — one `dg-agent run` per
agent — is the default and the only one where anything is enforced. `--mode
session` has the launching session spawn them with its own subagent tool, which
gives up the name, the floor, the budget and the assignment, and on opencode the
write scope and the commit gate as well. `dg-agent setup` prints that list
before it writes, the prompt repeats it to the agents, and `--confine require`
is refused outright rather than promising a floor those agents never get. It
writes no launcher, because there is nothing for `dg-agent run` to parent. See
`agentic/QUICKSTART.md` Recipe 4 — and Recipe 2 first, since a session can
supervise a whole run without spawning anything. The mode is the plugin's
workflow: from a terminal nothing refuses it, but outside Claude Code or
opencode only `--mode process` has guaranteed behaviour. A roster in this mode
reaches the agents through the session that passed it — in the spawn
instructions, since nothing sets `$DG_TASK` for a subagent (`D61`).

**Every agent starts on an assigned task, and by default the graph picks
them.** Two ready tasks never block each other, but they can meet at the seam:
one is evidence for a decision the other names, and two agents sent there
collide at the tray — the second `close` is refused — or turn each other's
finished work PROVISIONAL (`D45`). So `dg-agent setup` computes a maximal set
of ready tasks no two of which collide, greedily in id order so the same stores
give the same answer, and assigns one per agent. Fewer independent tasks than
agents asked for launches that many, and the report names the pair holding
each task out; nothing ready launches N agents that read the frontier, the run
this tool always had. The set is maximal and not maximum: that no larger one
exists is not something a person can check by reading, and the cost of a
missed set is one idle slot until the next setup. An assigned agent still
reads the frontier when its task is done, so what assignment gives up against
pure self-selection is only the first pick, and what it buys is that no two
agents open work that will collide. `--roster` names the tasks yourself
instead. Reach for it when you have a reason to say *this* agent does *this* —
a re-run aimed at exactly what a previous one dropped — and read the report:
a pair that may collide is said, with the decision it meets on, and obeyed.

It sets `--agents` rather than sitting beside it, so the two cannot disagree,
and passing both is refused. The ids are checked against the task store before
anything is written: one that names nothing, names finished work, or names the
same task twice is refused there, where you can still fix the line. Work that is
blocked or already `DOING` is **said and not refused** — running ahead of a
prerequisite is a thing a supervisor can legitimately mean, and so is relaunching
onto work a crashed agent left held.

Each agent gets the same `scout.md`, with every focus chain in it and `$DG_TASK`
naming which task is its own. One prompt rather than one per agent, for two
reasons: it stays a single file you can edit before launching — which the
regeneration guard exists to protect — and an agent that can read its siblings'
chains can see what it must not touch.

**That flag form is how this works from inside Claude Code or opencode.** An
agent can drive neither a full-screen app nor a prompt, so it reads
`dg-agent setup --json` — readiness, the defaults, and the three things it must
still ask — puts those questions to the person, and calls back with flags. With
no terminal the command says so and names the flags rather than prompting into
EOF, which is what click's bare `Aborted.` would otherwise amount to.

All three collectors produce the **same bytes**, which a test asserts by
driving each of them with the same answers. A wizard whose doors disagreed
would set a run up one way and describe it another.

`--dry-run` prints the files and writes nothing.

The rest of this file is what the wizard automates, and is worth reading once
before trusting it.

---

## 1 · Launching agents manually

One line per agent. **You** claim the name; the agent never claims its own —
shell state does not survive between an agent's tool calls, so a name it
claimed in one call is gone by the next.

```sh
dg-agent run --decide evidence --write launch --terse on --budget 30m \
  -- claude -p "$(cat fanout/scout.md)" &

dg-agent run --decide evidence --write launch --terse on --budget 30m \
  -- opencode run "$(cat fanout/scout.md)" &

dg-agent list          # who holds what, what each has staged, time left
```

Mixing hosts is fine — two agents under different scaffolds are two names in one
tray, and everything before the `--` is identical under both. **You** claim the
name, or rather `dg-agent run` claims it for you and puts it in that one child's
environment; the agent never claims its own, since shell state does not survive
between an agent's tool calls.

The whole contract with the host is these variables. `dg-agent run` composes
them, `dg` reads them, and `dg-agent env` says what is actually in force:

| | |
|---|---|
| `DG_AGENT` | the writer's name, from `dg-agent claim` — or from `dg-agent run`, which claims one and sets it for the child. Never invent one: `claim` refuses a name that is held or that has ops in a tray, so two agents cannot conflate their work. **Never export it**: an exported name makes the launcher an agent, and its own policy then refuses it. |
| `DG_TASK` | the task this agent was launched for, where a fan-out named one — set by `dg-agent run --task`, for the child only, like `DG_AGENT` and never exported. Unset means *nothing is assigned to you*, which is a run with nothing ready or a launcher written by hand. It is a starting point rather than a fence: the agent works it first, then reads the frontier as any other would. Not part of `env.json`, which holds the remit every agent in a run **shares** — an assignment differs per agent by construction. |
| `DG_DECIDE` | `evidence` — may close a question only where a **finished** `--evidence-for` task backs it · `never` — may not close at all · unset — may close anything. |
| `DG_WRITE` | `launch` — may write in the project and `/tmp`; anywhere else stops and asks the person · unset/`open` — anywhere, which is what the tool has always done. Reads are never judged. |
| `DG_AREA` | `strict` — may file only under an area already in use, and a new one goes back to a person · unset/`open` — any area, with a near-miss of one in use refused by the similarity guard and `--new-area` as the override. |
| `DG_BUDGET` | how long before its work is handed back — `1800`, `30m`, `2h`, or `infinite`. `dg-agent run --budget` records it on the lease *and* stops the child at it, so there is one number rather than a `--budget` and a `timeout` that agree until somebody edits the file. |
| `DG_TERSE` | how long a field may be — `on` (400 characters), a count, or unset/`off` for no limit. The store holds the synopsis somebody reads while deciding; the development goes in a file the record cites. Refused at stage time, before the tray is touched. |
| `DG_MODE` | not a variable either, and deliberately: the mode is a fact about *how the run was launched* rather than a rule the agents read, and a session-spawned agent shares its session's environment, so setting one would be a value nothing could rely on. It is stated at setup, in `fanout/scout.md`, and by `plan_fault` refusing the one pair that cannot be written. |
| `dg-agent env` | not a variable: the **report**. `$DG_DECIDE`, `$DG_WRITE`, `$DG_TERSE` and `$DG_AREA` all fail *open* — a typo does not weaken a rule by a notch, it removes it — so this is what names a fallback as a fallback. `--check` exits non-zero on anything set and not understood. |

Set `DG_DECIDE` **here and not in the prompt.** A variable is a refusal; a
sentence in a prompt is a request, and the moment a finished task leaves a
question owed an answer is exactly when the temptation to write one arrives.

`DG_WRITE` is the same shape, and reaches the agent's host through `dg gate`
rather than through the CLI: a write does not go through `dg`, so the two host
adapters ask the gate about it — `dg gate --write PATH`, one policy, every
host. `agentic/README.md` has the reasoning.

`DG_TERSE` is the one to set if the graph is going to be *read* — in the
browser, by the person choosing between proposals. It is off by default because
it is a house style rather than an invariant, and `dg serve` folds a long field
behind *show all* whether or not anybody set it. `agentic/README.md` has the
reasoning, and the part that matters is not the count: most of what fills a long
answer is the chain, and the chain is edges `dg context` already computes.

**`timeout` is the launcher's half of the budget, and it is not optional.**
`dg` is not in the agent's process tree and cannot stop one. What the recorded
budget buys is the *hand-back* — see §4.

**Your own shell must not have `DG_AGENT` set.** A supervisor is defined as
anybody without it, and that is what makes you exempt from every `DG_DECIDE`
value. Setting it globally makes you an agent and your own policy will refuse
you.

---

## 2 · Launching an orchestrator instead

Same thing, with one agent doing the claiming and spawning. Useful when you want
to say *"three agents on the Search frontier"* rather than write three lines,
and when the work should keep going while you are not watching.

Launch it **with no `DG_AGENT`**, so it is a supervisor:

```sh
opencode run "$(cat agentic/prompts/orchestrator.md)"
# or
claude -p "$(cat agentic/prompts/orchestrator.md)"
```

Under Claude Code you can also open a session and type `/dg:fanout` — the same
briefing, with the current roster, frontier and tray already read in.

**What makes an orchestrator work, and it is one rule:** the name goes in the
*child's* environment only.

```sh
dg-agent run --plan fanout/env.json -- claude -p "$(cat fanout/scout.md)" &
```

`dg-agent run` claims a name and puts it in that child's environment and
nowhere else, so the orchestrator stays a supervisor and can still apply, clear
and decide. That used to be a comment in the generated launcher telling whoever
read it not to export the variable; it is a behaviour now, and a behaviour beats
a comment somebody has to obey. An orchestrator that runs `export DG_AGENT=…`
has still made itself an agent and will still be refused by its own policy —
`dg-agent env` is where it will see why.

**What an orchestrator must not do:** decide. It can spawn, watch, report,
apply and clear — but the last call on an answer is the one thing the fan-out
exists to leave with a person. Say so in its prompt; do not rely on it.

---

## 3 · Watching, from your own shell

```sh
dg-agent list     # names held, what each holds and has staged, time left
dg pending        # the roster: who proposed what, across both trays
dg task           # the frontier, what is held, what is ready
```

`dg-agent list` is where a stalled run becomes visible, two different ways. A
budget reading `SPENT` beside a held task is an agent that ran out of time. A
`Seen` reading `silent 40m` is one that stopped *early* — a quota limit, a crash
— which a budget cannot catch until it elapses.

The two are not the same kind of fact and are not treated the same. An elapsed
budget is the clock, and `dg-agent expire` acts on it. Silence is a guess: an
agent in a long build looks exactly like a dead one, so it has no command at
all. Read it, then decide. `$DG_SILENT_AFTER` widens the window — default 15
minutes — and only agents *holding work* are ever reported.

## 4 · When an agent stops without saying so

An agent that finishes cleanly parks or completes its own work. One that is
killed, rate-limited or hung does not, and leaves a task `DOING` that nobody
will pick up because it looks taken.

```sh
dg-agent list          # who is over budget, and what they still hold
dg-agent expire        # stage a park for each, naming the budget
dg apply --agent brisk-beacon
```

**`dg-agent run` does most of this for you now, and not all of it.** It is the
child's parent, so a child stopped at its budget or one that dies holding work
has that work parked immediately, under its own name, while the information is
freshest. What it cannot see is itself being killed — a `kill -9` on the
process group, the machine going down, the terminal closing — so `dg-agent
expire` is still the backstop and still belongs at the end of a run. The window
is narrower; it is not closed.

The park lands under the *agent's own name*, so it sits beside whatever that
agent had already proposed and one `dg apply --agent` takes the batch. Parked
work stays outstanding — nothing downstream is released — so the next agent
picks it up, which is the difference between parking and dropping.

Run it even when you saw the agent die: the point is not the diagnosis, it is
that the queue stops lying. An agent that spent its budget holding *nothing* is
reported too, which is the only way "died before it started" is visible at all.

`prune` and `release` will not strand the work in the meantime: both keep back
a name still holding a `DOING` task and say so, and `--force` overrides when
that is what you want.

Without a budget there is nothing to measure against, so `expire` finds nothing
— this is what `--budget` is for. Parking by hand still works:

```sh
dg task park T07 --why "agent stopped at the rate limit, probe half-run"
```

## 5 · Reviewing, one proposal at a time

```sh
dg pending --agent brisk-beacon   # read one alone
dg serve                          # ...or as a graph, with the tray beside it
dg apply --agent brisk-beacon     # take it
dg clear --agent agile-azimuth    # turn it down — the others are untouched
```

Then **you** decide, with the proposals as input:

```sh
dg decide D01 --answer "HNSW, M=32." --source "bench/sweep.md" \
              --falsifier "recall below 0.9 at ef=128"
dg-agent prune                    # give back every name with nothing staged
```

---

## 6 · Optional: recording the run

Only when the run itself has to survive — a demo, or an audit of what was
*proposed* rather than what was taken. A fan-out with this off still leaves
behind everything it decided, which is what the graph is for.

Turn it on **before** the run:

```sh
export PATH="/path/to/dear-guide/agentic/bin:$PATH"
command -v dg                     # must print .../agentic/bin/dg
command -v dg-agent               # ...and .../agentic/bin/dg-agent
git check-ignore .dgraph-capture  # should print the rule that covers it
```

Three things silently gut it, and the first has already cost one session 653
useless entries:

- **Stay in the project directory.** The wrapper reads the trays from `$PWD`, so
  `dg --project /elsewhere` writes an entry whose tray snapshot is `null` — and
  the tray snapshot is the entire mechanism. You get a log that looks fine and
  holds nothing.
- **`dg serve` is not recorded.** A review done by clicking leaves no entry. Do
  the recorded review in the terminal, or accept the gap and say so.
- **Anything with `--edit` is not recorded either.** Same exemption, less
  obvious: `dg decide --edit` and the compose buffer write nothing to the log.
- **`dg-agent run` and `dg-agent setup` are not recorded.** `run` is the parent
  of an agent whose stdout is its own and which may run for half an hour, and
  `setup` without flags is a full-screen form; buffering either through a pipe
  breaks it. What the child does is captured in full — it is the `dg` calls that
  matter — and the launch line itself is on disk in `fanout/env.json` and
  `fanout/launch.sh`.

Check it before you rely on it — and check **`cwd`**, not the tray:

```sh
python3 -c "
import json, os
root = os.getcwd()
rows = [json.loads(l) for l in open('.dgraph-capture/dg.jsonl')]
astray = [r for r in rows if r['cwd'] != root]
print(f'{len(rows)} entries, {len(astray)} recorded from the wrong directory')
for r in astray[:5]:
    print('  ✗', r['cwd'], r['argv'][:3])
print('agents:', {r['agent'] for r in rows})"
```

**A null tray is not the fault**, and this is the trap: `.dgraph-pending.json`
does not exist while nothing is staged, so a healthy capture is full of
`"tray": null` — every read taken between applies has one. The broken case looks
identical in that field and differs only in `cwd`, which the wrapper records on
every entry. Compare that against the project root and the two separate cleanly.

What you want to see: zero entries from the wrong directory, and every agent you
launched present in the roster. A name missing there means that agent never
reached `dg` at all.
