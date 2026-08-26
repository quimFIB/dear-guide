# Running a fan-out against the graph

Several agents proposing into one graph, and a person deciding.

The graph already supports it: the tray is shared, every staged op records who
staged it, `dg agent claim` hands out names that cannot collide, and `--agent`
reads and applies one writer's proposal at a time. What was left to improvise
was the procedure around those, which is what this is.

```
agentic/README.md  the procedure — sections 1 to 6 below
agentic/bin/dg     OPTIONAL: a capture, for when you want the run itself
                   afterwards. Nothing in the procedure needs it.
```

The capture exists because one run needed to become a demo. It is genuinely
useful for that and for auditing what a set of agents proposed, and it is
genuinely not part of the workflow — a fan-out leaves behind exactly what it
decided, which is what the graph is for.

---

## The rule the whole thing rests on

> **Agents may `dg add` and `dg task add`. By default, only the supervisor runs
> `dg decide` — and `$DG_DECIDE` can make that a rule instead of a habit.**

A fan-out is a *search*: agents proposing questions, work, and structure. The
graph is a *record*. Keeping them apart is what stops one turning into the other.

An agent adding an OPEN decision says "here is a question somebody has to
answer" — cheap, reversible, exactly what fan-out is good at. An agent running
`dg decide` writes a falsifier for a question nobody made it live with, and the
skill's own warning applies: *a manufactured decision that nobody actually had
to make is worse than no record.* A decided edge is also the expensive one to
retract — `dg undep` works only on a bare edge, so reversing a hasty answer
means a `reopen` that files a reversal nobody made.

The tray is what makes the search safe: nothing an agent stages exists until
somebody applies it, and `dg serve` renders the graph **with the staged ops
applied**, so a proposal can be read as a graph before it is one.

### ...and the real reason, which is narrower than "agents judge badly"

The graph has two exits from a decision and both are wrong for a premature one.
`dg reopen` files a reversal — but a reversal means *we changed our mind*, not
*that should not have been written*, and the skill calls a reversal that never
happened a lie in the record. `dg rm` erases, is explicitly for things that
should never have been written, and `dg gate` answers `ask` on it, so a person
decides anyway. There is no vocabulary for "an agent decided this too early".

Which means the restraint that is right depends on the DECISION, not on who is
asking:

- a falsifier that is **a measurement the agent made** — the benchmark ran, the
  number is 0.62 — is a fact being recorded, and the falsifier writes itself;
- a **judgement between defensible alternatives** is where a falsifier written
  by something that never had to live with the consequence comes out as
  rationalisation.

Only the first is mechanically recognisable, which is what `$DG_DECIDE` checks:

| `$DG_DECIDE` | an agent may close… |
|---|---|
| `open` *(default)* | anything — what the tool has always done |
| `evidence` | only a decision a **finished** `--evidence-for` task backs |
| `never` | nothing; it stages, a person applies |

A supervisor — anyone with no `$DG_AGENT` — is never refused by any value. And
it is cooperative, like `$DG_AGENT` itself: an agent could unset it, at which
point it *is* the supervisor. Nothing here is a security boundary; it is a rule
the launcher sets so an honest mistake is caught.

---

## 1. The graph, and what you already decided

```sh
dg init          # if there is not one
dg task init
```

Then **seed it from the prose you already have.** Every project has decisions
recorded somewhere that is not a graph — a design note, a comment block at the
top of a config, a paragraph in a README. Lift those first. A fan-out against a
blank graph rediscovers them badly and expensively; against a seeded one it
works on what is actually open.

The test for whether something belongs is the skill's: can you write a falsifier
for it? Then it is a decision. A definition of done? Then it is a task.

```sh
dg add --id D01 --title "…" --area …
dg decide D01 --answer "…" --source "path/to/the/prose.md" --falsifier "…"
dg apply
```

Where the prose records a decision that was already *reversed* once, record it
as a reversal rather than as a fresh answer. Those are the most valuable thing
the graph holds and the easiest to flatten by accident.

## 2. The goal, as a task

Whatever the fan-out is for, it is work with a definition of done, so it is a
task and not a decision.

```sh
dg task add --id T01 --title "…" --area …
```

> **Your acceptance criteria go here, and they have to be citable.** Every
> closed decision cites a `--source`, and `discussion` is the weakest one
> available. If the standard the outcome must meet lives in your head or in a
> chat, writing it into the repo is the first prerequisite — not paperwork
> around the work:
>
> ```sh
> dg task add --id T00 --title "Write the spec into the repo" --area …
> dg task dep T01 --after T00
> ```

Prerequisites get added as you find them:

```sh
dg task dep T01 --after T04       # a prerequisite discovered later
```

That is backwards elaboration, recorded as you learn it rather than searched for
inside the store.

## 3. Launching — and letting agents pick their own work

An agent does not have to be handed a role. `dg task` ends with a computed
`ready` line, `dg task start` **refuses work somebody already claimed**, and
blocked-ness is derived — so *read the frontier → claim → do → `dg task done` →
repeat* is a loop an agent can run with nobody in it.

`dg task` shows `held by <name>` for work an agent has claimed, so a stalled
agent can be told from a slow one.

That fact is kept **outside both graphs**, in `.dgraph-agents.json` beside the
names themselves — scratch, gitignored, gone when the run is. Neither store
records who did anything, and that is deliberate rather than an omission: the
stores are committed and kept forever, agent names are recycled the moment they
are released, and "who finished this" is noise six months on that a recycled
name no longer even identifies. Who holds work is a fact about a run.


**The whole contract with the host is one environment variable.** Whatever
spawns an agent has to put `DG_AGENT` in its environment; nothing else about the
host matters, because everything the agent does to the graph it does through the
`dg` CLI. That is what keeps this workflow independent of which model or which
scaffold is running it.

Each agent gets its name from the tool, never one you invent:

```sh
for role in …; do
  DG_AGENT=$(dg agent claim) <however you spawn an agent> &
done
dg agent list        # who holds what, and what each has staged
```

```sh
DG_AGENT=$(dg agent claim) claude -p "$(cat prompts/scout.md)"   # Claude Code
DG_AGENT=$(dg agent claim) opencode run "$(cat prompts/scout.md)"   # opencode
```

The flags are the host's business and change between versions; the variable is
not. A host with no way to set one per agent can still be used — run each agent
in its own shell and export it there — and a mixed fan-out is fine, since two
agents under different scaffolds are just two names in one tray.

`dg agent claim` never hands out a name that is held or that has ops in either
tray, so two agents cannot end up sharing one — including across hosts, since
the tray is the only thing either of them touches. A claim does not expire;
`dg agent release` and `dg agent prune` give names back deliberately.

**And `dg` itself is host-neutral by construction.** The slash commands ship as
one set of files for both — `/dg:fanout` under Claude Code, `/dg-fanout` under
opencode — and the skill that teaches the recording discipline is loaded by
both. An agent under a third scaffold, or none, still has the CLI, which is
where all of this actually happens.

Each prompt should carry three things:

1. **The chain, in full.** `dg context <id> --full`, pasted in. A fresh context
   knows the task and nothing about why it exists, and without the chain it
   cannot tell a constraint from an implementation detail.
2. **The rule** from the top of this file: add questions and work, decide
   nothing.
3. **What it may read.** Point at the files; it will not find them.

## 4. Review, one proposal at a time

```sh
dg pending                       # the roster: who proposed what, across both trays
dg pending --agent brisk-frege   # one agent's proposal, alone
dg serve                         # ...or as a graph, with staging applied
dg apply --agent brisk-frege     # take this one
dg clear --agent agile-azimuth   # turn that one down — the others are untouched
```

Then **you** decide, with the proposals as input:

```sh
dg decide D07 --answer "…" --source "discussion" --falsifier "…"
```

If a proposal turns up a question nobody had written down, add the decision and
link the work to it rather than leaving it in prose:

```sh
dg task link T04 --evidence-for D09
```

## 5. Settle the empirical ones with evidence

The decisions a fan-out cannot settle by arguing are the ones that want a
measurement — a benchmark, a spike, a pilot. That loop is native:

```sh
dg task add --id T09 --title "…" --area … --evidence-for D07
dg task start T09
# ...run the thing...
dg task done T09 --outcome "where the result is"
dg confirm D07 --against T09 --note "what it showed"
```

`dg check` warns when an `--evidence-for` task has finished and its decision is
still unsettled — *the measurement ran and nobody recorded its conclusion*.

## 6. Close

```sh
dg task done T01 --outcome "…"
dg agent prune        # release the names, now that nothing is staged
dg check
```

---

# Optional: recording the run

**Nothing above needs this.** Turn it on when you want the run itself
afterwards: to show somebody how the workflow behaves, to audit what a set of
agents actually proposed, or to build a demo out of a real session instead of a
scripted one. It was written for that last case.

Everything that touches the graph goes through `dg`, so a wrapper first on
`$PATH` catches every interaction — **including the ones that leave no trace**,
which is the point. `dg drop`, `dg clear --agent` and an applied tray all erase
what was there: the graph keeps what landed and deliberately not what was
proposed.

```sh
export PATH="/path/to/dear-guide/agentic/bin:$PATH"
command -v dg          # must print .../agentic/bin/dg
```

The record lands in `.dgraph-capture/dg.jsonl`, which the `.gitignore` `dg init`
writes already covers under `.dgraph-*`.

**Smoke-test it before the run, not during** — both of these fail silently:

```sh
NAME=$(dg agent claim); echo "[$NAME]"          # exactly one name, nothing else
DG_AGENT=$NAME dg add --id D99 --title x --area General
dg drop 0                                        # ...and throw it away again
python3 -c "
import json
for l in open('.dgraph-capture/dg.jsonl'):
    e = json.loads(l)
    print(e['agent'], e['argv'], 'tray:',
          'empty' if not e['tray'] else f\"{len(json.loads(e['tray']))} op(s)\")"
```

The first property is that **stdout stays clean**: `dg agent claim` prints a
bare name precisely so `DG_AGENT=$(dg agent claim)` works, and a wrapper that
prepended a banner would break every launch.

The second is the one the capture exists for. The `add` line shows
`tray: 1 op(s)` and the `drop` line `tray: empty` — so the op that never reached
the graph is still in the log, in full, as the tray recorded on the entry before
it was dropped.

**What it cannot see**, so decide now rather than afterwards:

- **The browser.** `dg serve` is its own process and does not come through the
  wrapper, so a review done by clicking leaves no entry. Either do the recorded
  review in the terminal, or accept the gap and say so — but do not half-do it,
  because the step where you turned a proposal down is the part of the record
  that matters most.
- **Reasoning, and anything that is not a `dg` call.** Whatever host the agent
  ran under keeps that, wherever it keeps it — Claude Code writes one JSONL per
  session under `~/.claude/projects/<slug>/`, opencode keeps its own session
  store. Copy them at the end; nothing else holds them, and in a fan-out across
  two hosts you will be copying from two places.


## Turning a capture into a demo

Be clear about what you have, because it is **not** what `demo-agentic/` is.
That demo is *staged*: scenes in shell, deterministic, replayable, covered by
`test_demo_agentic.py`. Its virtue is that it runs cold and always says the same
thing.

A capture is the opposite: one real run, with dead ends, a proposal that was
rejected, and an agent that misread something. Its virtue is that nobody staged
it. The two are complements, and the captured one is worth most exactly where
the staged one is weakest — nobody believes a scripted demo about whether a
workflow survives contact with real agents.

- **Keep the log raw and narrate separately.** Editing the transcript to read
  better destroys the only property it has. Write the narration as a second file
  that points into the log by timestamp.
- **The refusals are the content.** A `dg apply` that refused because another
  writer's work was in the tray, a `clear --agent` where a proposal was turned
  down, a decision reopened when evidence contradicted it — these are the things
  prose about the tool cannot demonstrate.
- **Sanitise before publishing.** The log holds full model output and your
  prompts; the host's transcripts hold more. Read them, do not skim them.
- **A run where every proposal was accepted demonstrates nothing** about a tool
  whose whole subject is what happens when work is proposed, reviewed, and
  sometimes rejected.
