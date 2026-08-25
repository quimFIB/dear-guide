# Demo: three agents, one development graph, a day's work

`demo/` shows what this tool **keeps** — one of every record, a reversal, a
soundness finding with three exits. It is complete as that, and it has exactly
one writer.

This one has three software agents and a maintainer. It exists because the
useful question about agents is not whether they *may* share a graph, but what
happens to the work when they do.

**The work is what drives it.** The task graph says what is ready, an agent
picks it up, doing it produces evidence, and evidence is what settles a
question. Nobody here invents an answer — every answer cites the task whose
outcome produced it. The concurrency problems then arrive the way they actually
do: out of three agents doing real work and having to join it up.

## The day

| | the scene | what it is about |
|---|---|---|
| **1** | nobody writes the plan | three different jobs come out of `dg check` and `dg task`, and a person wrote none of them |
| **2** | the work opens up | B decomposes its own task and **produces startable work for somebody else** |
| **3** | no status is updated | C finishes a subtask; two other tasks re-rank. *Blocked is derived, never stored* |
| **4** | an answer is a piece of work | evidence → answer → **and answering releases a blocked task** |
| **5** | three agents, one staging tray | one agent publishing another's draft — closed by a name |
| **6** | one fact arrives | the premise moves and **every answer is under review at once**, as a computed list |
| **7** | the two nothing prevents | a question with no route to an answer; and an answer composed against a world that moved |

Scenes 1 to 4 are the loop working. 5 and 6 are what a fan-out costs and what
the tool does about it. **7 is what it does not, which is why the rest matter.**

## Run it

```sh
./demo-agentic/demo.sh          # the day, in order
./demo-agentic/demo.sh 7        # one scene, on its own
./demo-agentic/demo.sh annex    # the two extra examples — see below
```

The seven scenes are one story and the graph accumulates across them. A scene
run on its own still opens on the state the story left it in, because it
replays the earlier beats with their output suppressed (`scenes/story.sh`)
rather than loading a fixture that resembles them — so a change to scene 2
cannot quietly stop matching scene 4's prose.

Everything happens under `/tmp/dg-demo-agentic` (`$DG_DEMO_DIR` to move it), and
the work directory has to prove it is the demo's before anything in it is
removed. Nothing you do here can be a mistake.

**Or watch it first.** [`slides.html`](slides.html) walks the same seven scenes
as a deck, with the annex after them — a lane per agent, stepping through with
the real output at each turn. Open it in a browser; no server, no build.

```sh
xdg-open demo-agentic/slides.html
```

## The graph, and the work against it

An imaginary open-source Go engine, three decisions deep. Every vertex is a
question the *team* answers in a design discussion. None of it is anything the
engine does while playing: this graph guides the development of a system, and it
is not a model of one.

```
D01  Where do the evaluation weights come from?   DECIDED   [Core]
     "Hand-tuned, by us. Three strong players on the team and no GPU budget,
      so the cheap thing is also the only thing."
     falsifier: a GPU budget appears, or hand-tuning stalls for two releases

 ├─ D02  How is a weight change accepted?         OPEN   [Tooling]
 └─ D03  What ships in the release binary?        OPEN   [Release]

T01  Wire the SPRT harness to the volunteer cluster   TODO   evidence for D02
     → nothing blocks it. Nobody has looked at what it involves.
T02  Measure the binary with the weights inlined      DONE   evidence for D03
     → bench/size.md — 412 KB at -Os, of which the weights are 71 KB
T03  Draft the release note                           TODO   because D03
     → cannot start. Blocked by a question, not by work.
```

**Those three tasks are the demo's engine.** Between them they produce the whole
of scene 1's assignment without anybody writing a plan:

- `T02` is `DONE` against an unsettled `D03`, so `dg check` says an answer is
  owed. That is agent A's job, and it came from the tool.
- `T01` has no prerequisites, so `dg task` says `ready T01`. That is agent B's.
- `T03` is `because D03` — work waiting on an *answer* rather than on other
  work — so it appears in the same list as blocked, and nothing can start it
  until somebody settles the question.

Agent C has nothing on day one. That is deliberate, and it is what scene 2 is
for: the third agent's work is created by the second agent looking at its own.

## The agents are shell scripts

Each scene is one file that reads top to bottom as the order things happened,
with every command printed under the agent that ran it. There is no model
anywhere in this demo, and that is deliberate:

- **A race has to be deterministic to be a demo.** Every scene turns on an exact
  interleaving. A model asked to settle `D03` arrives there at a different
  moment every run, and a demo whose central claim reproduces four times in five
  is worse than none.
- **It has to run in CI, offline, in a second.** `demo/` went stale in the quiet
  way once — every command still worked and the prose had drifted — which is why
  `tests/test_demo.py` exists. The same test has to be possible here, and it
  cannot be if running the demo costs API calls.
- **The races belong to `dg`, not to the model.** Nothing below depends on an
  agent being clever. It depends on several agents, one store, and an order. A
  shell script is the more honest agent for that, because it removes the reading
  *well, a better model would have noticed*.

So: **this demo shows what `dg` does when several agents work one graph. It is
not evidence about how a model behaves, and it does not claim to be.**

## The seven scenes

Each is a quotation from `dg`, and `tests/test_demo_agentic.py` asserts the
quotations rather than the prose — rewording an exception fails the suite, which
is the only thing holding the message and the paragraph explaining it together.

### 1 — nobody writes the plan

`dg check` and `dg task`, and three kinds of outstanding thing come back: an
answer that is owed, a task that is ready, and a task blocked by a question.
Readiness is computed from the edges, stored nowhere, maintained by nobody.

### 2 — the work opens up

B picks up `T01`, finds it is three things, and records that:

```
T01  DOING  Wire the SPRT harness to the…  ·  waits T04, T05 · evidence for D02
T04  TODO   Get cluster credentials from…  ·  unblocks T01, T05
T05  TODO   Port the SPRT runner to the…   ·  waits T04 · unblocks T01
ready T04
```

**`ready T04` is the scene.** A moment ago there was one ready task and three
agents. B looked at its own work and produced startable work for somebody else;
the agent that picks it up has not been told about it and will find it the way B
found `T01`. The parallelism is made by the work.

### 3 — no status is updated, and the work still joins up

C finishes `T04`. `T05` was `waits T04` and is now **ready**; `T01` was
`waits T04, T05` and is now `waits T05`. C wrote one outcome for its own task,
did not notify B, and does not know B exists.

**Blocked is derived, never stored** — so no status could have gone stale and no
message could have been missed. Two agents handed work to each other for the
cost of one command by one of them.

### 4 — an answer is a piece of work, and it frees other work

A settles `D03`, citing `T02` — the outcome is the only reason there was
anything to write. Then:

```
ready T03, T05
```

`T03` was `waits D03 (undecided)`. Answering the question made the release note
startable, and whoever picks it up will never know why it was blocked. The loop
runs both ways: work produces evidence, evidence settles a question, and settling
a question releases work.

Then B's subtasks finish, `T01` with them, and `dg check` turns on B:
`T01 is DONE and was to inform D02, which is still unsettled`. B answers it,
citing `T01`.

### 5 — three agents, one staging tray

Everything above passed through one staging area. Unnamed, C's `dg apply` writes
its own op **and both of the others** — an answer A had not finished checking, a
completion B had not verified — and A is left with `nothing staged`, which reads
as *my staging failed*.

With `$DG_AGENT` set before the agents were launched, the same commands leave
each agent's work its own:

```
STAGED  1 op(s)
  0  rfbj  close  D03  One binary, no runtime files…  ·  by A
STAGED TASKS  1 op(s)
  0  ngfq  set_status  T05  → DONE                    ·  by B
```

Nothing is *isolated*: all three still share one graph and still hand tasks to
each other. What changed is that publishing is something an agent does to its
own work.

### 6 — one fact arrives, and every answer is under review

The sponsor's cluster is the event `D01`'s falsifier named in March. Reopening it
says what that drags with it:

```
2 decided descendant(s) rest on it and become PROVISIONAL:  D02, D03
1 unfinished task(s) rest on a premise under review:        T03
```

Three agents worked in parallel on a premise, and one fact put all of it under
review at once. The maintainer did not work that set out — `dg reopen` computed
it, and `dg check` refuses a store where it was not applied.

And nothing *halts*: `T03` is still `ready`. That is the difference between
`OPEN` and `PROVISIONAL` — `D03` has an answer, it just has nobody currently
vouching for it, and blocking the work would claim the answer was gone when it
is not. Instead `dg show` lists both decisions under **RESTING ON A PREMISE
UNDER REVIEW**, so whoever starts `T03` can see what they are building on.

**That is what a reopen costs a fan-out, stated exactly:** not lost work and not
stopped work, but a computed list of what is now standing on a question, with
`dg confirm` as the way each one comes off it.

### 7 — the two nothing prevents · **the one to read**

**7a, a question with no route to an answer.** B parks `T01` — the cluster queue
is six days deep — and the finding is not about B:

```
! [evidence_stalled] D02 is OPEN and waits on evidence nobody is producing —
T01 is parked and no other task meant to inform it is still going
```

Only the seam can see it. The task store knows `T01` stopped; the decision store
knows `D02` is open; the link is what says those are one problem. **That is the
fan-out failure nobody watches for** — not a crash and not a conflict, but an
agent that quietly stopped, leaving a question that looks merely unanswered.

**7b, an answer composed against a world that moved.** A stages its answer to
`D03` from `T02`'s 412 KB measurement. While it sits there the premise is
reopened and re-answered at 40 MB. A applies:

```
· D01 moved since this batch was staged (its answer changed) — op 0 (close D03)
rests on it
✓ applied 1 op(s)
```

*Its answer changed* — not the status, not the structure. Then `dg check` says
`all invariants hold`, and `dg why D03` prints both readings four lines apart
under `→ every premise under this is settled`.

`dg check` is not wrong. The structure is valid; the two answers contradict each
other in the prose, where no invariant can reach.

**And this is the one the other six do not prevent.** Scene 5 was attribution and
closed with a name. Scene 3 was coordination and closed because readiness is
derived. Scene 6 was propagation and closed because reopen computes the set.
This is none of those: A owned its op, cited real evidence, and the world moved
while the answer sat in a tray. **A clone of its own would have made it more
likely, not less.**

What saves it is the falsifier A wrote before it had any reason to. Drift does
not prevent the stale answer; it is the one chance anybody gets to notice, and
the falsifier is what makes it findable six months later by somebody who was not
here.

## Annex: a clone per agent

Not part of the day, and runnable on their own. The seven scenes put three
agents in one checkout; these two are what changes when a harness gives each
agent a clone instead — which trades scene 5's problem for a different pair.

```sh
./demo-agentic/demo.sh annex     # both
./demo-agentic/demo.sh a1        # or one
```

### a1 — two agents, one id

Two agents cannot see each other's store, and both reach for the next free
number. **a1a**, the questions really are different; **a1b**, they are the same
question in two wordings. The refusal is identical:

```
✗ aborted, nothing written
D04 already exists, and is not what this op would have created — pick another id
```

and the right answer is opposite — a fresh id in the first, `dg drop` in the
second, because the question is already open under the other agent's wording. An
agent that cannot tell them apart puts two vertices behind one question.

The tool does not make that call. What `dg range --set 50-99` does is make the
collision *rare*, so an integration report is not a rename line per record
anybody wrote.

### a2 — bringing two clones back together

**a2a**: two unrelated additions, and git cannot merge them — `decisions.json`
is a JSON array and two additions land in one region. Resolution is two facts
about the layout (`decision-graph.md` is generated; `decisions.json` is the
union) and then `dg render && dg check`, which is what makes it *safe* rather
than merely merged.

**a2b**: both agents answered the same question. A text merge would put both
edges in the file and `dg check` would refuse the result; a union keyed by id
would pick one **silently**, which is worse. Instead the refusal arrives before
anything is staged — `D50 already has an answer, and is DECIDED`.

Two answers to one question is not a merge problem to be resolved. It is a
disagreement between two agents, and `dg integrate` / `dg incoming
--take/--keep/--split` is the seam where a person settles it, having been shown
every conflict at once rather than interrupted per record.

**Put the annex beside the day and the trade is visible.** One checkout: agents
can publish each other's drafts, and the fix is a name. A clone each: they
cannot, and instead they collide on ids and produce merges git cannot do.
*Isolation moves the race; it does not remove it.*

## What this demo does not show, and why

**The commit gate under a fan-out.** `docs/quickstart-agents.md` records that on
opencode the gate does not see subagent shell calls
([opencode#5894](https://github.com/sst/opencode/issues/5894)), so a commit made
from inside a spawned agent is not gated. That is the sharpest agent-specific
failure this project knows about and it deserves a scene — but reproducing it
needs opencode and a real subagent, which breaks the rule the rest of this demo
is built on. It is named here rather than faked.

**Anything about model behaviour.** Whether an agent *reads* the drift line in
scene 7 and acts on it is exactly the question this demo cannot answer.

**Two clones, in the main run.** Everything in the seven scenes happens in one
checkout, because that is what a fan-out gets by default and where the
interesting joins are. The other configuration — a clone per agent — is in the
**annex** below rather than in the day.

**Isolation of the guards.** The agents share one `preview`, so while B holds a
staged `close D02` the maintainer's `dg decide D02` is refused. That refusal is
correct and it is the *early* one — per-agent trays would push it from before
the answer is written to after — but it does mean agents can block each other's
composition, and nothing here softens that.

**The claim, and it is narrower than "supported".** Several agents on one graph
is a configuration this tool *copes with* rather than one it guarantees. The
work is handed out by the graph (1) and opened up by the agents (2); it joins
back up without coordination (3) and closes the loop into answers (4);
attribution is closed with a name (5) and propagation is computed rather than
remembered (6). **What is not closed is scene 7, and no locking discipline
closes it** — which is why the falsifier, written before the evidence, is the
part of this tool that does the most work under a fan-out.

## Files

| | |
|---|---|
| `decisions.json`, `tasks.json` | the graph and the work the agents found |
| `demo.sh` | the driver — `./demo.sh [1-7]` |
| `scenes/1.sh` … `7.sh` | one scene each; each replays the beats before it |
| `scenes/a1.sh`, `a2.sh` | the annex — a clone per agent, outside the day |
| `scenes/story.sh` | the day as beats, so the scenes accumulate and still read cold |
| `scenes/lib.sh` | the work directory, the three agents, and who-ran-what narration |
| `scenes/union.py` | annex 2a's hand-resolution, so it can run unattended |
| `gitignore.txt` | what `dg` would add itself, committed up front so no scene's diff is about it |
| `slides.html` | the seven scenes and the annex as a deck, for reading before running |
