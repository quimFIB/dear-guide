# Demo: one hard question, three agents, one development graph

`demo/` shows what this tool **keeps** — one of every record, a reversal, a
soundness finding with three exits. It is complete as that, and it has exactly
one writer.

This one has three, and a maintainer who launched them. It exists because the
useful question about agents is not whether they *may* share a graph but what
happens when they do:

> A question too big for one pass, split across three areas. Three agents on
> it at once. Which of the things that then go wrong does the tool handle, and
> which does it hand back to you?

Six scenes, one continuous day, in the order the day ran into them.

## The day

A sponsor donates cluster time — the exact event the oldest decision's
falsifier named — so *where the evaluation weights come from* is back in play,
and it touches Core, Tooling and Release at once.

| | the scene | the concurrency problem | what happens |
|---|---|---|---|
| **1** | one hard question, three areas | — | `dg show` turns a reopened premise into three questions **with an order between them** |
| **2** | three agents, one staging tray | one agent publishes another's draft | closed, by a name: `$DG_AGENT` |
| **3** | three answers at once | publishing out of dependency order | closed, by the edge: composition parallelises, publication is ordered |
| **4** | the quiet one: a stale premise | an answer composed against a world that moved | **not closed.** One drift line, and the falsifier |
| **5** | two agents, one id | the collision every fan-out has | refused twice, identically — and the two need opposite answers |
| **6** | bringing the work back together | git cannot merge two answers | refused at composition; the seam is where a person decides |

Scenes 2, 3, 5 and 6 end with the tool doing something. **Scene 4 is the one
that does not, and it is why the other five are worth having.**

## Run it

```sh
./demo-agentic/demo.sh          # every scene, in order
./demo-agentic/demo.sh 4        # one scene, on its own
```

The six scenes are one story and the graph accumulates across them. A scene run
on its own still opens on the state the story left it in, because it replays the
earlier beats with their output suppressed (`scenes/story.sh`) rather than
loading a fixture that resembles them — so a change to scene 2 cannot quietly
stop matching scene 4's prose.

Everything happens under `/tmp/dg-demo-agentic` (`$DG_DEMO_DIR` to move it), and
the work directory has to prove it is the demo's before anything in it is
removed. Nothing you do here can be a mistake.

**Or watch it first.** [`slides.html`](slides.html) walks the scenes as a deck —
one lane per agent, stepping through the interleaving with the real output at
each turn. Open it in a browser; no server, no build.

```sh
xdg-open demo-agentic/slides.html
```

## The graph

An imaginary open-source Go engine, three decisions deep. Every vertex is a
question the *team* answers in a design discussion — where the weights are
sourced, how a change gets reviewed, what the release artefact is. None of it
is anything the engine does while playing: this graph guides the development of
a system, and it is not a model of one.

```
D01  Where do the evaluation weights come from?   DECIDED   [Core]     ← agent A
     "Hand-tuned, by us. Three strong players on the team and no GPU budget,
      so the cheap thing is also the only thing."
     falsifier: a GPU budget appears, or hand-tuning stalls for two releases

 ├─ D02  How is a weight change accepted?         OPEN   [Tooling]  ← agent B
 └─ D03  What ships in the release binary?        OPEN   [Release]  ← agent C

T01  Wire the SPRT harness to the volunteer cluster   DOING   evidence for D02
T02  Measure the binary with the weights inlined      DONE    evidence for D03
     → bench/size.md — 412 KB at -Os, of which the weights are 71 KB
```

One premise, two children, one agent each — and a third on the premise itself,
which is what makes the other two wait. That is the smallest shape in which
several agents can be given genuinely separate work and still collide, and
every scene is that shape plus an ordering.

`T02` is `DONE` before anything starts, which is not scenery. It makes the
opening `dg check` say:

```
! [evidence_unharvested] (warning) T02 is DONE and was to inform D03 (What ships
in the release binary?), which is still unsettled — record what it showed with
`dg decide D03`, or drop the link
```

That is agent C's assignment, arriving from the tool rather than from a prompt.
It clears the moment C settles `D03` — which is why the graph at the end of
scene 4 is provably clean while saying something absurd.

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
  agent being clever. It depends on several writers, one store, and an order. A
  shell script is the more honest agent for that, because it removes the reading
  *well, a better model would have noticed*.

So: **this demo shows what `dg` does when several writers meet. It is not
evidence about how a model behaves, and it does not claim to be.**

## The six scenes

### 1 — one hard question, three areas

Nothing concurrent happens. It is the scene that earns the other five.

The maintainer opens by asking the graph what it is worried about, and the
sponsor's cluster is not a new fact to weigh up — it is the fact `D01` was
*waiting* for. Its falsifier, written on 2026-03-01 before anybody had reason to
think it would fire, says *"a GPU budget appears, or hand-tuning stalls for two
releases running"*. So the first move is `dg reopen`, and then:

```
FRONTIER  3 not settled of 3   OPEN 2  REOPENED 1
  D01  REOPENED  Where do the evaluation weights c…  ·  decidable now
  D02  OPEN      How is a weight change accepted?    ·  waits D01 · evidence T01
  D03  OPEN      What ships in the release binary?   ·  waits D01
```

That is the fan-out plan, and the order in it is not a rule anybody wrote down —
it is the edges, recorded when each question was opened. **Two of the three
agents are about to work on a premise nobody has settled.** That is what
parallel exploration is; the rest of the demo is about the graph already
knowing it.

### 2 — three agents, one staging tray

The agents were launched where the problem is: the maintainer's checkout. A
directory is one staging tray.

Played twice, with the *same three commands* both times. Unnamed, agent C's
`dg apply` takes three ops — two of them half-finished answers belonging to
agents still working on them — and A is left with:

```
$ dg pending
nothing staged
```

Not "your work landed", not "the ground moved". Nothing, which reads as *my
staging failed* and invites A to write the answer again. And a `close` is the op
this tool deliberately makes hard to take back: the way out is `dg reopen`,
which files a reversal that never happened.

Then the same morning with `$DG_AGENT` set before the agents were launched:

```
$ dg pending
STAGED  3 op(s)
  0  gqrv  close  D01  A trained net, 40 MB. Six weeks of donated clus…  ·  by A
  1  syum  close  D02  SPRT against the previous build, 20k games on t…  ·  by B
  2  hbnp  close  D03  One binary, no runtime files. The weights compi…  ·  by C

$ dg apply
✓ applied 1 op(s) → decisions.json + decision-graph.md
2 op(s) left staged, by B, C
```

One variable, set by whoever launches the agents. Nothing is *isolated* — the
three still share one graph and can still see each other's work, which is the
point rather than the problem.

### 3 — three answers at once, and the order the graph already knew

All three compose at the same time and nothing stops them. Agent B publishes
first:

```
✗ aborted, nothing written
would leave the graph invalid:
  [propagation] D02 is DECIDED but rests on D01 (REOPENED) — `dg repair` marks
it PROVISIONAL, or settle the premise with `dg decide D01`
2 op(s) left staged, by A, C
```

B did nothing wrong — B was *asked* to answer `D02`. Nothing was written, B's
answer is still B's, and once A settles the premise the same op applies
unchanged.

**Composition parallelises; publication is ordered by the dependency.** Nobody
sequenced the agents, nobody held a lock, and nobody had to know what the others
were doing.

### 4 — the quiet one: a stale premise, still legal · **the one to read**

Agent C was the fastest and finished first, so its answer sat staged while the
others landed. C composed it when `D01` was still `REOPENED` and nothing was
staged against it — so the only reading available was the one the project had
had since March: hand-tuned weights, measured by `T02` at 71 KB inside a 412 KB
binary. *"The weights compile in as a generated header"* is the **only** answer
C could have reached.

`A` has since settled `D01` at 40 MB. C applies:

```
· D01 moved since this batch was staged (REOPENED → DECIDED) — op 0 (close D03)
rests on it
`dg node <id>` shows what it says now; nothing was applied on the old reading
✓ applied 1 op(s) → decisions.json + decision-graph.md
```

One line, printed once, to a process that may not have been reading. Then
`dg check`:

```
✓ 3 vertices, 4 edges; 2 tasks, all invariants hold, 1 warning(s)
```

The one warning is about `T01` not having reported into `D02` — real, worth
acting on, and **nothing whatever to do with the contradiction**. `dg why D03`
prints both readings four lines apart and ends *every premise under this is
settled*.

`dg check` is not wrong. The structure is valid; the two answers contradict each
other in the prose, where no invariant can reach.

**This is the one concurrency problem a lock cannot touch**, and the reason why
is worth being exact about. Scene 2's failure was *attribution* and closed with
a name. Scene 3's was *ordering* and closed with an edge. This one is neither:
every op was owned, every premise was settled before it was built on, and the
batch was still composed against a world that moved while it sat there. **A
clone of its own would have made it more likely, not less.**

What saves it is the falsifier C wrote before it had any reason to — *"the
weights outgrow what a header can carry"*. They now have, so the exit is a
command rather than an argument.

### 5 — two agents, one id

The maintainer has moved the agents into a clone each. Scenes 2 and 3 cannot
happen here.

**5a**, two agents notice different gaps and reach for the same number. **5b**,
they notice the *same* gap and phrase it differently. The refusal is identical:

```
✗ aborted, nothing written
D04 already exists, and is not what this op would have created — pick another id
```

and the right answer is opposite — a fresh id in 5a, a `dg drop` in 5b, because
the question is already open under the other agent's wording. An agent that
cannot tell them apart puts two vertices behind one question.

The tool does not make that call and does not pretend to. What `dg range --set
50-99` does is make the collision *rare*, so the integration report a person
reads is not a rename line per record anybody wrote.

### 6 — bringing the parallel work back together

**6a** is what git does with two agents' unrelated additions: a text conflict in
`decisions.json`, because it is a JSON array and two additions land in one
region. **This is what isolation cost** — the race did not go away, it moved.
Resolution is two facts about the layout (`decision-graph.md` is generated;
`decisions.json` is the union) and then `dg render && dg check`, which is what
makes it *safe* rather than merely merged.

**6b** is the case a union cannot handle. Both agents answered the same
question. A text merge would put both edges in the file and `dg check` would
refuse the result; a union keyed by id would pick one **silently**, which is
worse than either. Instead the refusal arrives before anything is staged:

```
D50 already has an answer, and is DECIDED — `dg reopen` it first
```

Two answers to one question is not a merge problem to be resolved. It is a
disagreement between two agents, and only a person who knows which is right can
settle it — which is what `dg integrate` and `dg incoming --take/--keep/--split`
are for: every conflict collected first, then one person answers a list once.

## What this demo does not show, and why

**The commit gate under two agents.** `docs/quickstart-agents.md` records that on
opencode the gate does not see subagent shell calls
([opencode#5894](https://github.com/sst/opencode/issues/5894)), so a commit made
from inside a spawned agent is not gated. That is the sharpest agent-specific
failure this project knows about and it deserves a scene — but reproducing it
needs opencode and a real subagent, which breaks the rule the rest of this demo
is built on. It is named here rather than faked.

**Anything about model behaviour.** Whether an agent *reads* the drift line in
scene 4 and acts on it is exactly the question this demo cannot answer.

**Isolation of the guards.** Agents share one `preview`, so while `B` holds a
staged `close D02` the maintainer's `dg decide D02` is refused. That refusal is
correct and it is the *early* one — per-agent trays would push it from scene 3's
position (before the answer is written) to scene 4's (after) — but it does mean
agents can block each other's composition, and nothing here softens that.

**The claim this demo now makes, and it is narrower than "supported".** Several
agents on one graph is a configuration the tool *copes with* rather than one it
guarantees. Attribution is closed (scene 2) and ordering is closed (scene 3);
collisions are refused loudly and made rare (scene 5); two answers to one
question are refused at the door and handed to a person (scene 6). What is not
closed is scene 4, and no locking discipline can close it — which is why the
falsifier, written before the evidence, is the part of this tool that does the
most work under a fan-out.

## Files

| | |
|---|---|
| `decisions.json`, `tasks.json` | the graph as both agents found it |
| `demo.sh` | the driver — `./demo.sh [1-6]` |
| `scenes/1.sh` … `6.sh` | one scene each; each replays the beats before it |
| `scenes/story.sh` | the day as beats, so the scenes accumulate and still read cold |
| `scenes/lib.sh` | the work directory, and who-ran-what narration |
| `scenes/union.py` | scene 6a's hand-resolution, so it can run unattended |
| `gitignore.txt` | what `dg` would add itself, committed up front so no scene's diff is about it |
| `slides.html` | a deck of the earlier five-scene cut — **not yet updated to this story** |
