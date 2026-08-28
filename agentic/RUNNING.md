# Running a fan-out

The shortest path from an empty terminal to several agents proposing into one
graph. `README.md` beside this file is the *why* — the rule, the ownership
model, what each mechanism is defending against. This is the *how*, and it
assumes you have read none of it.

Two ways to run one. **Manually**, where you claim the names and spawn the
agents yourself, and **with an orchestrator**, where one agent does that for
you. The second is the first with a wrapper around it; nothing else changes,
and the graph cannot tell them apart.

---

## 0 · Initiation, once per project

```sh
cd /path/to/your-project
dg init      --areas "Search,Serving,Index"    # decisions.json
dg task init --areas "Search,Serving,Index"    # tasks.json — optional, but the
                                               # loop below needs it
```

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

## 0.5 · The short way: `dg agent setup`

Everything in §1 and §2 by hand, or one command that writes both artefacts:

```sh
dg agent setup                    # a TUI, if you are at a terminal
```

It checks what is ready, asks what it cannot work out, and writes
`fanout/scout.md` (the prompt, no ⟨…⟩ left in it) and `fanout/launch.sh` (one
line per agent, environment already right). Read the prompt, then run the
launcher.

**Most of the prompt fills itself.** The project, the areas in use, which
policies are in force and what each one *means* in practice, the write roots,
the budget — and the full chain behind each focus id, pasted verbatim from
`dg context --full`, which is the single thing a fresh context cannot
reconstruct. Three answers are yours: what the fan-out is for, what the agents
may read, and where findings go.

**The TUI is optional and so is its dependency.** `pip install 'dear-guide[tui]'`
adds `textual`; without it the command still works, because every answer is also
a flag:

```sh
dg agent setup --focus T04,T07 --agents 3 --decide evidence --write launch \
  --budget 30m --brief "settle the Search frontier" \
  --read "bench/README.md:how the sweep is run" --findings "findings/<id>.md"
```

**That flag form is how this works from inside Claude Code or opencode.** An
agent cannot drive a full-screen app, so it reads `dg agent setup --json` —
readiness, the defaults, and the three things it must still ask — puts those
questions to the person, and calls back with flags. Both paths run the same
code and produce the same bytes, which a test asserts; a wizard whose two doors
disagreed would set up a run one way and describe it the other.

`--dry-run` prints both files and writes nothing.

The rest of this file is what the wizard automates, and is worth reading once
before trusting it.

---

## 1 · Launching agents manually

One line per agent. **You** claim the name; the agent never claims its own —
shell state does not survive between an agent's tool calls, so a name it
claimed in one call is gone by the next.

```sh
DG_AGENT=$(dg agent claim --budget 30m) DG_DECIDE=evidence DG_WRITE=launch \
  timeout 1800 claude -p "$(cat agentic/prompts/scout.md)" &

DG_AGENT=$(dg agent claim --budget 30m) DG_DECIDE=evidence DG_WRITE=launch \
  timeout 1800 opencode run "$(cat agentic/prompts/scout.md)" &

dg agent list          # who holds what, what each has staged, time left
```

Mixing hosts is fine — two agents under different scaffolds are two names in one
tray. The whole contract with the host is these variables:

| | |
|---|---|
| `DG_AGENT` | the writer's name, from `dg agent claim`. Never invent one: `claim` refuses a name that is held or that has ops in a tray, so two agents cannot conflate their work. |
| `DG_DECIDE` | `evidence` — may close a question only where a **finished** `--evidence-for` task backs it · `never` — may not close at all · unset — may close anything. |
| `DG_WRITE` | `launch` — may write in the project and `/tmp`; anywhere else stops and asks the person · unset/`open` — anywhere, which is what the tool has always done. Reads are never judged. |
| `DG_BUDGET` | how long before its work is handed back — `1800`, `30m`, `2h`, or `infinite`. `dg agent claim --budget` records it on the lease, which is what `dg agent list` and `dg agent expire` read; the variable is how the *agent* learns its own budget. |

Set `DG_DECIDE` **here and not in the prompt.** A variable is a refusal; a
sentence in a prompt is a request, and the moment a finished task leaves a
question owed an answer is exactly when the temptation to write one arrives.

`DG_WRITE` is the same shape, and reaches the agent's host through `dg gate`
rather than through the CLI: a write does not go through `dg`, so the two host
adapters ask the gate about it — `dg gate --write PATH`, one policy, every
host. `agentic/README.md` has the reasoning.

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

**What makes an orchestrator work, and it is one rule:** it claims a name and
puts it in the *child's* environment only.

```sh
DG_AGENT=$(dg agent claim --budget 30m) DG_DECIDE=evidence DG_WRITE=launch \
  timeout 1800 claude -p "$(cat ...)" &
```

This is a per-command assignment. It never exports into the orchestrator's own
shell, so the orchestrator stays a supervisor and can still apply, clear and
decide. An orchestrator that runs `export DG_AGENT=…` has made itself an agent
and will be refused by its own policy.

**What an orchestrator must not do:** decide. It can spawn, watch, report,
apply and clear — but the last call on an answer is the one thing the fan-out
exists to leave with a person. Say so in its prompt; do not rely on it.

---

## 3 · Watching, from your own shell

```sh
dg agent list     # names held, what each holds and has staged, time left
dg pending        # the roster: who proposed what, across both trays
dg task           # the frontier, what is held, what is ready
```

`dg agent list` is where a stalled run becomes visible, two different ways. A
budget reading `SPENT` beside a held task is an agent that ran out of time. A
`Seen` reading `silent 40m` is one that stopped *early* — a quota limit, a crash
— which a budget cannot catch until it elapses.

The two are not the same kind of fact and are not treated the same. An elapsed
budget is the clock, and `dg agent expire` acts on it. Silence is a guess: an
agent in a long build looks exactly like a dead one, so it has no command at
all. Read it, then decide. `$DG_SILENT_AFTER` widens the window — default 15
minutes — and only agents *holding work* are ever reported.

## 4 · When an agent stops without saying so

An agent that finishes cleanly parks or completes its own work. One that is
killed, rate-limited or hung does not, and leaves a task `DOING` that nobody
will pick up because it looks taken.

```sh
dg agent list          # who is over budget, and what they still hold
dg agent expire        # stage a park for each, naming the budget
dg apply --agent brisk-beacon
```

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
dg serve                          # ...or as a graph, with staging applied
dg apply --agent brisk-beacon     # take it
dg clear --agent agile-azimuth    # turn it down — the others are untouched
```

Then **you** decide, with the proposals as input:

```sh
dg decide D01 --answer "HNSW, M=32." --source "bench/sweep.md" \
              --falsifier "recall below 0.9 at ef=128"
dg agent prune                    # give back every name with nothing staged
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
