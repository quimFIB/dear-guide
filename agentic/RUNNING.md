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

## 1 · Launching agents manually

One line per agent. **You** claim the name; the agent never claims its own —
shell state does not survive between an agent's tool calls, so a name it
claimed in one call is gone by the next.

```sh
DG_AGENT=$(dg agent claim) DG_DECIDE=evidence \
  claude -p "$(cat agentic/prompts/scout.md)" &

DG_AGENT=$(dg agent claim) DG_DECIDE=evidence \
  opencode run "$(cat agentic/prompts/scout.md)" &

dg agent list          # who holds what, and what each has staged
```

Mixing hosts is fine — two agents under different scaffolds are two names in one
tray. The whole contract with the host is those two variables:

| | |
|---|---|
| `DG_AGENT` | the writer's name, from `dg agent claim`. Never invent one: `claim` refuses a name that is held or that has ops in a tray, so two agents cannot conflate their work. |
| `DG_DECIDE` | `evidence` — may close a question only where a **finished** `--evidence-for` task backs it · `never` — may not close at all · unset — may close anything. |

Set `DG_DECIDE` **here and not in the prompt.** A variable is a refusal; a
sentence in a prompt is a request, and the moment a finished task leaves a
question owed an answer is exactly when the temptation to write one arrives.

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
DG_AGENT=$(dg agent claim) DG_DECIDE=evidence claude -p "$(cat ...)" &
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
dg agent list     # names held, since when, how many ops each has staged
dg pending        # the roster: who proposed what, across both trays
dg task           # the frontier, what is held, what is ready
```

## 4 · Reviewing, one proposal at a time

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

## 5 · Optional: recording the run

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

Check it before you rely on it:

```sh
python3 -c "
import json
for l in open('.dgraph-capture/dg.jsonl'):
    e = json.loads(l)
    print(e['agent'], e['argv'][:3], 'tray:',
          'NULL — wrong directory' if e['tray'] is None
          else f\"{len(json.loads(e['tray']))} op(s)\")"
```

Every line should name an agent and a tray. A column of `NULL` means the
capture recorded nothing worth keeping.
