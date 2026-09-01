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

# 2. in the session, backgrounded so it outlives the turn
dg-agent broker --relay --exec-rung auto --write-rung scoped

# 3. a terminal
./fanout/launch.sh
```

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
only the cooperative gate stands between an agent and the channel. `--relay` does
not check, deliberately; that is `D40`, open.

---

## Recipe 3 — discovery, where nothing lands unapproved

For agents exploring a graph nobody aimed them at. `scout` is the preset: it
proposes and settles nothing, runs no build tools, and — the part that matters —
**writes nothing to the store at all**.

```sh
dg-agent setup --preset scout --brief "…" --findings "findings/<task-id>.md"
dg-agent broker --relay --write-rung scoped --exec-rung scoped
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
