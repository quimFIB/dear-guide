# Setting up an agentic session

Copy-paste recipes. `RUNNING.md` is the procedure step by step; `README.md` is
the *why* — the rule each mechanism defends, and the argument for it. This is
neither: it is the shortest thing that gets a run going, and it links out where
you need to understand rather than type.

```
agentic/QUICKSTART.md   you are here — three recipes, and what to check
agentic/RUNNING.md      the procedure, step by step
agentic/README.md       what each rule is for, and what it is defending against
```

---

## Once per project

```sh
cd /path/to/your-project
dg init          # decisions.json
dg task init     # tasks.json — the frontier agents pick their own work off
```

## The shape of every run

Four moves, whichever recipe you pick.

```
1. dg-agent setup …      write fanout/scout.md, launch.sh and env.json
2. dg-agent broker …     something to answer what the agents get stopped on
3. ./fanout/launch.sh    the agents
4. dg pending → dg apply what they proposed, and what you take
```

Step 2 is the one people skip. **With no broker, an agent stopped by the gate
gets a refusal nobody chose** — `dg gate` can only answer `ask`, and in a
headless run there is nobody to ask.

---

## Recipe 1 — you, at a terminal

The default, the fewest moving parts, and the only one with no channel an agent
could reach: the front end is a terminal prompt, which nothing on the filesystem
can forge.

```sh
dg-agent setup --preset contributor \
  --brief "settle the Search frontier" \
  --read "bench/README.md:how the sweep is run" \
  --findings "findings/<task-id>.md"

dg-agent broker          # terminal A — answer [a]llow / [s]cope / [d]eny here
./fanout/launch.sh       # terminal B
```

Then review, one writer at a time:

```sh
dg-agent list                      # who holds what, budgets, who has gone quiet
dg pending --agent brisk-beacon    # read one proposal alone
dg apply   --agent brisk-beacon    # ...take it, leave the others staged
```

A bare `dg apply` **refuses** while the tray holds another writer's work. That
refusal is the review step, not an obstacle.

---

## Recipe 2 — Claude Code or opencode answers the broker

For when you are working inside a session and do not want a second window. The
session hosts the broker, decides the command escalations itself, and passes the
rare out-of-scope write to you.

```sh
# 1. set up
dg-agent setup --preset contributor --focus T04,T07 \
  --brief "…" --read "path:what it is" --findings "findings/<task-id>.md"

# 2. start the broker. `--detach` puts it in its own session, so it outlives
#    the turn that started it — which is the one thing it must do.
dg-agent broker --relay --detach --exec-rung auto --write-rung scoped \
  --plan fanout/env.json

# 3. and launch. `launch.sh` asks whether a broker is listening before the
#    first agent starts, so a skipped step 2 is a line you see rather than an
#    agent that stops.
./fanout/launch.sh
```

**Step 3 no longer needs a terminal**, and step 2 is why. A broker started from
a session's own shell call dies when that call returns, so this recipe used to
put the launcher in a second window — which made *"do not want a second
window"* only half true. `--detach` is idempotent and reports a broker that is
already there, so running it twice costs nothing.

The session then answers each question as it arrives:

```sh
dg-agent consent                     # what is waiting, and what answering means
dg-agent consent --allow --why "the project's own test suite"
dg-agent consent --deny  --why "not what this task needs"
```

**Set the two ladders apart, which is why there are two.** `--exec-rung auto`
lets the session decide commands — frequent, mostly dull, and already bounded by
the allowlist and the floor. `--write-rung scoped` keeps a write outside the
launch scope for a person: rare, and worth your eyes.

**The rung decides what the answer is called**, and both the broker's banner and
`dg-agent consent` print it before you answer anything:

| rung | who answers | logged as |
|---|---|---|
| `user` · `scoped` | a person, however they reach it | `person` |
| `auto` | whatever is attached, a model included | `auto` |

So `.dgraph-consent.jsonl` afterwards separates them: every `by: auto` line the
session decided, every `by: person` line you did. That distinction is the point
of the split — see *"...and letting the session answer"* in `README.md`.

**Two things to know before relying on this.** Answer within about a minute and
a half: the gate gives up at 100 seconds and the agent never sees a later
verdict. And the relay's safety rests on the confinement floor — without one,
only the cooperative gate stands between an agent and the channel.

**`--relay` still does not refuse without a floor, and the log says so instead.**
An unconfined agent shares the uid that owns the socket, so it could answer its
own request; refusing to relay would shut that small hole beside the open one,
since such an agent can already write the project at leisure. What the run may
not do is *claim* more than it can show — so a verdict relayed for a run whose
plan declares no floor is logged `relayed` rather than `person`, the broker says
which at the door, `dg-agent consent` says which before you answer, and a
supervisor reading the log afterwards can tell the two apart. The same person
answered either way; what differs is the warrant.

**The broker learns about the floor from `--plan fanout/env.json`** — the same
file `dg-agent run` applies it from, so the two cannot disagree — and never
from its own shell, which nothing sets. Start it without the plan and every
relayed verdict is `relayed`, whatever the agents are running under.

---

## Recipe 3 — discovery, where nothing lands unapproved

For agents exploring a graph nobody aimed them at. `scout` is the preset: it
proposes and settles nothing, runs no build tools, and — the part that matters —
**writes nothing to the store at all**.

With one exception worth knowing before you rely on it: `dg serve` runs outside
the confinement floor and writes both stores, and any process that can reach it
on localhost can ask it to. A panel open beside a confined run is a writer the
floor does not cover — see *"one thing the panel is not"* in `README.md`.

```sh
dg-agent setup --preset scout --brief "…" --findings "findings/<task-id>.md"
dg-agent broker --relay --write-rung scoped --exec-rung scoped --plan fanout/env.json
./fanout/launch.sh
```

Under `scout` every op waits for you:

```sh
dg pending --agent <name>          # the whole proposal, before any of it is real
dg apply   --agent <name>
```

**Why the preset matters here and not elsewhere.** `$DG_DECIDE` guards what an
agent may *answer* and nothing about what it may *add* — an `add` is ungated,
and `dg apply` normally writes an owned caller's own ops with nothing consulted.
So the tray keeps writers apart; it is not by itself approval. `scout` sets
`$DG_APPLY=never`, which is what turns it into an approval queue.

---

## Recipe 4 — the session spawns the agents itself

**Read what this gives up before choosing it.** The three recipes above run
each agent as a child of `dg-agent run`, which is what enforces its name, its
floor, its budget and its write scope. `--mode session` has the session spawn
them with its own subagent tool instead, and a subagent has no such parent — so
those rules become *advisory*, and the mode exists to say so rather than to
pretend otherwise.

```sh
dg-agent setup --mode session --preset contributor --focus T04,T07 \
  --brief "…" --read "path:what it is" --findings "findings/<task-id>.md"
```

It prints what is advisory before it writes anything, and the prompt tells the
agents the same list. There is **no launcher**: `fanout/launch.sh` runs the two
checks that still apply and says the rest is the session's to do.

One refusal comes with it — `--confine require` is rejected, because a plan
asserting a floor its agents cannot get is a prompt promising something no
invocation can deliver. Everything else is stated.

**This mode is the plugin's workflow.** It exists for `/dg:fanout`, where the
session that runs `setup` is the session that spawns. Nothing refuses it from a
terminal — `setup` cannot tell a session from a person without sniffing a host
variable — but that is not the intended use, and outside Claude Code or
opencode only `--mode process` has guaranteed behaviour.

**A roster in this mode reaches the agents through you.** You passed
`--roster`, so you hold the list; nothing sets `$DG_TASK` for a subagent. Name
each agent's task in its spawn instructions, one agent per id, in order — the
report prints the roster to remind you, and the prompt tells the agent to look
there rather than in a variable (`D61`).

**On opencode it is weaker still**, and this is worth a second look rather than
a footnote: `tool.execute.before` does not fire for `task`-tool subagents
([opencode#5894](https://github.com/sst/opencode/issues/5894)), so the write
scope and the commit gate are *absent* there rather than advisory — and
`commit` and `rm` are the two irreversible things. A fan-out launched as
separate `opencode run` processes is unaffected, which is Recipe 1 or 2.

**Prefer Recipe 2 where you can.** A session can hold the tray, broker consent
and drive the whole run without spawning anything itself, and that gives up
nothing at all. Reach for this one when you have a reason the other three do not
serve — not to avoid a second window, which Recipe 2 already handles.

---

## Which preset

```sh
dg-agent presets       # this table, from the code
```

| | settles | writes the store | may run |
|---|---|---|---|
| `scout` | nothing | never — you apply | readers only |
| `contributor` *(default)* | what finished evidence backs | its own ops | + build tools |
| `maintainer` | anything | its own ops | + build tools |

Same in all three: writes confined to the project and `/tmp`, new areas
allowed, fields capped at 400 characters, and a confinement floor wherever a
backend is available. None of them sets a budget — that follows the size of the
work, and the wizard asks it.

Run `dg-agent setup` with no flags to pick one in a form instead.

---

## When it looks stuck

```sh
dg-agent env       # what is ACTUALLY in force, and what was mistyped
dg-agent list      # who holds what, time left, and who has gone quiet
dg-agent consent   # a question waiting on a relayed answer
dg pending         # what has been proposed and not applied
```

Four things that look like a broken tool and are not:

- **An agent says it may not write somewhere.** `$DG_WRITE=launch`. Put the
  file in the project or `/tmp`, or approve it where you are standing.
- **An agent says a close was refused.** `$DG_DECIDE`. Record what you measured
  with `dg task done --outcome` and leave the answer to a person.
- **`dg apply` refuses.** Either the tray holds somebody else's work (use
  `--agent`), or `$DG_APPLY=never` and you are the agent, not the supervisor.
- **Everything is being denied at once.** Almost always no broker, or a rung
  typo. `dg-agent env --check` exits non-zero on anything set and not
  understood — three of these variables **fail open**, so a typo does not
  weaken a rule by a notch, it removes it.

## Then

- `RUNNING.md` — the same ground step by step, including orchestrators, parking
  work an agent dropped, and recording a run.
- `README.md` — why each rule exists, which is worth reading before you widen
  one.
