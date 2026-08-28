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
| `never` | nothing — the close is refused before it is composed, and a caller with no `$DG_AGENT` writes it |

A supervisor — anyone with no `$DG_AGENT` — is never refused by any value. And
it is cooperative, like `$DG_AGENT` itself: an agent could unset it, at which
point it *is* the supervisor. Nothing here is a security boundary; it is a rule
the launcher sets so an honest mistake is caught.

---

## The other two limits: where an agent may write, and for how long

`$DG_DECIDE` limits what an agent may *record*. Two more variables limit what it
may do to the machine and to the clock, and all three are read the same way —
declared by the launcher, never consulted for a supervisor.

| variable | values | what it does |
|---|---|---|
| `$DG_WRITE` | `open` *(default)* · `launch` | where the agent may write without asking |
| `$DG_BUDGET` | `infinite` *(default)* · `1800` · `30m` · `2h` | how long before its work is handed back |

```sh
DG_AGENT=$(dg agent claim --budget 30m) \
DG_DECIDE=evidence \
DG_WRITE=launch \
  timeout 1800 claude -p "$(cat agentic/prompts/scout.md)"
```

### Why this is not enforced here, and where it is

`$DG_DECIDE` can be a rule because **every decision goes through `dg`**. A write
does not — an agent writes with its host's own tools, and `dg` is not in that
path at all. A check that lived only in this tool would be a rule nothing ever
consulted.

The enforcement point that does exist is **`dg gate`**, and it was already
general: it takes a thing about to happen and answers `allow` / `warn` / `ask` /
`deny` with a reason, and *both host adapters relay that answer holding no
policy of their own*. So the scope is a second question the same gate answers:

```sh
dg gate --write /etc/passwd --json
```

Which means a rule written once is enforced under **every** host at once —
`hooks/prewrite.py` under Claude Code, `tool.execute.before` under opencode, and
a third scaffold earns it by relaying the same verdict. `tests/test_plugin.py`
asserts both adapters ask.

What an adapter still owns is *which of its host's tools writes*, and each has
one tool it cannot judge: a shell redirection under Claude Code (covering it
means parsing arbitrary shell for write intent) and `patch` under opencode
(which names its targets inside a diff rather than in an argument). Both are
stated in the adapter and in `opencode/README.md` rather than papered over — a
half-parser that failed open would be worse than a gap somebody knows about.

**Reads are never judged.** An agent that cannot read the repository it is
reasoning about is blindfolded rather than constrained, and every interesting
thing a fan-out does starts by reading something outside its own directory.

**An out-of-scope write is `ask`, never `deny`.** The rule is consent, not
prohibition: the person approves it where they are standing. `dg gate --write`
has no verdict that refuses outright.

Under `launch` the writable roots are **the project** — where the graph is — and
the system temporary directory. The project rather than the agent's current
directory, because a `cd` is not a change of remit and a scope anchored to the
working directory would widen every time an agent walked somewhere else.

### The budget buys the hand-back, not the stopping

`dg` is not in the agent's process tree and never was. Stopping the process is
the launcher's half and looks like `timeout 1800 …` under any host.

What the budget buys is the thing a fan-out cannot otherwise see. A task left
`DOING` by an agent that died reads exactly like one being worked on — and
`dg task park --why` is the verb that already fixes it:

```sh
dg agent list          # who is over, and what they are still holding
dg agent expire        # stage a park for each, naming the budget
dg apply --agent brisk-beacon
```

```
┃ Name          ┃ Staged ┃ Holding ┃ Budget         ┃ Seen       ┃
│ agile-azimuth │ 0      │ T07     │ SPENT +12m     │ silent 44m │
│ brisk-beacon  │ 4      │ T11     │ 30m (18m left) │ 20s        │
```

Each park is staged **under the agent's own name**, so it lands beside whatever
that agent had already proposed and `dg apply --agent <name>` takes the batch.
Parked work is still outstanding, so nothing downstream is released and the next
agent can pick it up — which is the whole point of parking rather than dropping.

An agent that spent its budget holding *nothing* is reported too. "Died before it
started" is invisible in every other reading of a run, because no task is `DOING`
for a roster of parked work to show.

Run `dg agent expire` even when an agent died on its own; the budget is what
makes the queue honest afterwards, and this is a real case rather than a
hypothetical — a rate limit killed three scouts mid-wave in the run that
`.dgraph-capture/` was built from, and every one of their tasks had to be parked
by hand.

### `Seen`, and why nothing acts on it

A budget catches an agent that ran *out of time*. It does nothing for one killed
at minute five of thirty — a quota limit, a crash — which stays invisible for
the next twenty-five minutes while its row reads perfectly healthy. So every
`dg` call by an agent stamps a heartbeat, and `Seen` is how long ago that was.

The signal is better than "how often does an agent run `dg`" suggests, because
`dg` is not the only door: both host adapters call `dg gate` on the agent's own
tool calls, so **every file write is a heartbeat too**. For a scout that reads,
thinks, writes findings and records them, coverage is good.

**Nothing acts on it, and that is the design.** An elapsed budget is a fact
about a clock. Silence is a suspicion — an agent in a forty-minute build is
silent in exactly the way a dead one is, and no amount of tuning fixes that,
because the blind spot is precisely long non-`dg`, non-write work. So silence
gets a column and never a verb: `expire` fires on elapsed budgets only, and a
person decides what a quiet agent means.

Three things keep it honest:

- the window is deliberately generous — 15 minutes, and `$DG_SILENT_AFTER`
  raises it for a fan-out doing long compiles;
- it is reported **only for an agent holding work**, since one that is silent
  and holding nothing has cost nobody anything;
- and if you do act, `expire` still only *stages*, so a park on a live agent is
  reviewed before it lands and `dg clear --agent` throws it away.

The remaining blind spot is worth telling agents about rather than engineering
around: an agent that knows it is about to go quiet for an hour can say so, and
`dg apply --mine` before a long sweep is a heartbeat like any other.

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

**Nobody has to hand an agent a role.** `dg task` ends with a computed `ready`
line, `dg task start` refuses work somebody already claimed, and blocked-ness is
derived — so *read the frontier → claim → do → `dg task done` → repeat* is a loop
an agent runs with nobody in it:

```sh
dg show && dg task                 # the frontier: what is open, and what is ready
dg task start T04                  # claim it
dg apply --mine                    # ...and publish the claim, so others see it
# ...do the work...
dg task done T04 --outcome "where the result is"
dg apply --mine
```

The claim is what makes the loop safe under several agents, and it is a refusal
rather than a warning:

```
$ DG_AGENT=beta dg task start T04
T04 is already DOING
```

Nothing in that loop was assigned. `ready` is derived from the edges every time
it is printed, so an agent that finishes `T04` makes `T05` startable for an agent
that was never told `T04` existed and does not know the first one exists — which
is `demo-agentic/` scene 3, run with no model in it at all.

**Apply the claim rather than leaving it in the tray.** `dg task start` stages
like everything else, and until it is applied `dg task` still prints the task as
`startable` — the listing reads the store, the guard reads the tray. Nobody
double-claims it either way: that refusal above is a *stage-time* one, so the
second agent is turned away before it writes anything. What it costs is that the
queue lies to whoever reads it, and `held by <name>` — the thing that tells a
stalled agent from a slow one — does not exist until the op lands. `dg apply
--mine` immediately after the start is the whole of the fix.

**And an agent that stops has to say so.** A task left `DOING` by an agent that
died reads exactly like one being worked on, and the loop has no other way to
hand it back:

```sh
dg task park T04 --why "the cluster queue is six days deep"
```

A parked task says *what* stopped it, which is the difference between work the
next agent can pick up and work nobody knows is free. And `dg check` reads the
park across the seam — a parked task that was the only evidence for an open
decision comes back as the question nobody is producing an answer for:

```
! [evidence_stalled] D02 is OPEN and waits on evidence nobody is producing —
T01 is parked and no other task meant to inform it is still going
```

Who holds what is kept **outside both graphs**, in `.dgraph-agents.json` beside
the names themselves — scratch, gitignored, gone when the run is. Neither store
records who did anything, and that is deliberate rather than an omission: the
stores are committed and kept forever, agent names are recycled the moment they
are released, and "who finished this" is noise six months on that a recycled
name no longer even identifies. Who holds work is a fact about a run.

**The whole contract with the host is environment variables.** Whatever spawns
an agent has to put `DG_AGENT` in its environment — and, if you want rules
rather than habits, `DG_DECIDE`, `DG_WRITE` and `DG_BUDGET` beside it. Nothing
else about the host matters, because everything the agent does *to the graph* it
does through the `dg` CLI, and the one thing it does outside the CLI — writing
files — reaches the same policy through `dg gate`, which both adapters already
relay. That is what keeps this workflow independent of which model or which
scaffold is running it.

Each agent gets its name from the tool, never one you invent:

```sh
for role in …; do
  DG_AGENT=$(dg agent claim --budget 30m) DG_DECIDE=evidence DG_WRITE=launch \
    timeout 1800 <however you spawn an agent> &
done
dg agent list        # who holds what, and what each has staged
```

```sh
DG_AGENT=$(dg agent claim) claude -p "$(cat agentic/prompts/scout.md)"      # Claude Code
DG_AGENT=$(dg agent claim) opencode run "$(cat agentic/prompts/scout.md)"   # opencode
```

`agentic/prompts/scout.md` is a **template, not a prompt** — everything below
with each project-specific part left as a `⟨TOKEN⟩`. What an agent should be
told depends on what the fan-out is for, so the blanks are the work; running it
unfilled gets you an agent that has been told nothing. `orchestrator.md` beside
it is the same for an agent that spawns and watches rather than works.

**`dg agent setup` fills them.** Most tokens come straight from the graph —
the project, the areas, the policies in force and what each means, the write
roots, the budget, and each focus id's full chain pasted from
`dg context --full`. Three do not: what the fan-out is for, what the agents may
read, where findings go. Three ways to answer them — a full-screen form where
`textual` is installed, a question at a time otherwise (which needs nothing the
tool did not already depend on, so interactive setup always works), and flags,
which is what an agent inside Claude Code or opencode uses since it can drive
neither. All three produce the same bytes. `RUNNING.md` §0.5 has it;
`RUNNING.md` end to end is the procedure it automates.

Both files live in `dgraph/prompts/` and are reached here by symlink, so an
installed `dg` carries them and there is exactly one copy to keep true.

`DG_DECIDE` is the other half of the launch, and the table at the top of this
file is what the values mean. Set it here rather than trusting the prompt: an
agent running the loop above finishes work all day, and the moment a finished
`--evidence-for` task leaves a question owed an answer is exactly when the
temptation to write one arrives. `evidence` lets it record what it measured and
refuses the rest; `never` sends every answer back to a person. Unset is the
default and the widest, which is what `demo-agentic/` is written against.

The flags are the host's business and change between versions; the variables are
not. A host with no way to set them per agent can still be used — run each agent
in its own shell and export them there — and a mixed fan-out is fine, since two
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

Each prompt should carry four things:

1. **The chain, in full.** `dg context <id> --full`, pasted in. A fresh context
   knows the task and nothing about why it exists, and without the chain it
   cannot tell a constraint from an implementation detail.
2. **The loop, and that nothing will be assigned.** `dg show && dg task`, claim
   with `dg task start`, `dg apply --mine`, finish with `dg task done --outcome`
   or `dg task park --why`, then read the frontier again. An agent told only what
   to do this once stops when it is done and leaves the rest of the queue sitting
   there.
3. **The rule** from the top of this file — add questions and work, decide
   nothing — **and which `$DG_DECIDE`, `$DG_WRITE` and `$DG_BUDGET` it is
   running under**, so a refusal at stage time or a prompt about a write reads
   as the policy it is rather than as a broken tool.
4. **What it may read, and where it may write.** Point at the files; it will not
   find them. Under `DG_WRITE=launch` say so — an agent that knows the scope
   puts its findings in the project instead of discovering the rule by being
   stopped.
5. **Its budget, and that expiry parks rather than discards.** An agent told it
   has thirty minutes can decide what to finish; one told nothing runs until
   something kills it. Say `dg task park --why` is how to hand work back early,
   because an agent that stops without parking leaves work that looks alive.

## 4. Review, one proposal at a time

```sh
dg pending                       # the roster: who proposed what, across both trays
dg pending --agent brisk-beacon   # one agent's proposal, alone
dg serve                         # ...or as a graph, with staging applied
dg apply --agent brisk-beacon     # take this one
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
dg decide D07 --answer "…" --source T09 --falsifier "…"
```

The source is the task, not `discussion`: what settled the question is a thing
that was run, and the id is how somebody six months from now gets from the answer
to the measurement. Where `D07` already had an answer and the evidence merely
holds it up, the verb is `dg confirm D07 --against T09 --note "what it showed"`
instead — a confirm needs an answer standing, and against an `OPEN` question it
refuses.

`dg check` warns when an `--evidence-for` task has finished and its decision is
still unsettled — *the measurement ran and nobody recorded its conclusion*.

**This is the loop `DG_DECIDE=evidence` exists for**, and it is why that value is
not simply a weaker `never`. An agent under it may close exactly the decisions a
finished task of its own backs, so the answer it writes is the measurement it
just ran and the falsifier is the number moving. Everything else is refused at
stage time, before an answer, a source and a falsifier have been composed:

```
✗ nothing staged — $DG_DECIDE=evidence: nothing is `--evidence-for D07`, so
there is no measurement for brisk-beacon to be recording. Link the work that
bears on it — `dg task link <id> --evidence-for D07` — or leave the question
open for a person
```

The refusal names its own fix, which is usually the right one: the agent found a
question its work bears on and had not said so. `dg task link` is cheap, is not
an answer, and is exactly what a fan-out is for.

## 6. Close

```sh
dg agent expire       # hand back what any out-of-time agent still holds
dg apply --agent …    # ...and take the parks
dg task done T01 --outcome "…"
dg agent prune        # release the names, now that nothing is staged
dg check
```

**`prune` will not strand work, and says what it kept back.** A name with
nothing staged reads as idle even when its agent is mid-task — that is the first
minute of every agent's life — so releasing it used to strand the task: still
`DOING`, holder no longer recorded, and `dg task start` refusing it as taken.
`prune` now keeps those names and names them:

```
released 1: agile-bearing
kept agile-azimuth — still holds T01
```

`dg agent release` refuses for the same reason, and `--force` overrides either
when the stranding is what you want — a run whose tasks you are about to drop.

**So `expire` first is a convenience, not a rescue.** Expiring turns a kept-back
name into a park that says *why*, which is what the next agent needs; without it
you park by hand, or prune again once the work is settled. A run with no budgets
has nothing to expire and still cannot be stranded.

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
