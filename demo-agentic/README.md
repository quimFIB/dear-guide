# Demo: two agents, one development graph

`demo/` shows what this tool **keeps** — one of every record, a reversal, a
soundness finding with three exits. It is complete as that, and it has exactly
one writer.

This one has two. It exists because the README's most important claim about
agents is the only one a reader cannot check:

> **Whether two agents may work one graph.** Not yet, and the honest answer is
> *one writer at a time* — though what a second writer can do to you is now
> reported rather than silent.

Five scenes, each an interleaving of two agents over the same graph. What they
show is which part of that sentence the tool enforces, which part it merely
reports, and which part is left to you.

## Run it

```sh
./demo-agentic/demo.sh          # every scene, in order
./demo-agentic/demo.sh 3        # one scene, on its own
```

Each scene rebuilds the project from scratch under `/tmp/dg-demo-agentic`
(`$DG_DEMO_DIR` to move it), so none of them inherits the last one's graph and
any one can be read cold. Nothing you do here can be a mistake.

**Or watch it first.** [`slides.html`](slides.html) walks the same five scenes as
a deck — two lanes, one per agent, stepping through the interleaving with the
real output at each turn. Open it in a browser; no server, no build.

```sh
xdg-open demo-agentic/slides.html
```

## The graph

An imaginary open-source chess engine, three decisions deep. Every vertex is a
question the *team* answers in a design discussion — where the weights are
sourced, how a change gets reviewed, what the release artefact is. None of it
is anything the engine does while playing: this graph guides the development of
a system, and it is not a model of one.

```
D01  Where do the evaluation weights come from?   DECIDED   [Core]
     "Hand-tuned, by us. Three strong players on the team and no GPU budget,
      so the cheap thing is also the only thing."
     falsifier: a GPU budget appears, or hand-tuning stalls for two releases

 ├─ D02  How is a weight change accepted?         OPEN   [Tooling]   ← agent A
 └─ D03  What ships in the release binary?        OPEN   [Release]   ← agent B

T01  Wire the SPRT harness to the volunteer cluster   DOING   evidence for D02
T02  Measure the binary with the weights inlined      DONE    evidence for D03
     → bench/size.md — 412 KB at -Os, of which the weights are 71 KB
```

One premise, two children, one agent per child. That is the smallest shape in
which two agents can be given genuinely separate work and still collide, and
every scene is that shape plus an ordering.

`T02` is `DONE` before anything starts, which is not scenery. It makes the
opening `dg check` say:

```
! [evidence_unharvested] (warning) T02 is DONE and was to inform D03 (What ships
in the release binary?), which is still unsettled — record what it showed with
`dg decide D03`, or drop the link
```

That is agent A's assignment, arriving from the tool rather than from a prompt.
It clears the moment A settles `D03` — which is why the graph at the end of
scene 3 is provably clean while saying something absurd.

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
  agent being clever. It depends on two writers, one store, and an order. A
  shell script is the more honest agent for that, because it removes the reading
  *well, a better model would have noticed*.

So: **this demo shows what `dg` does when two writers meet. It is not evidence
about how a model behaves, and it does not claim to be.**

## The five scenes

### 1 — the tray has no idea whose ops are whose

*One project, two agents in it.* A stages a decision, meaning to review it. B,
working on something else, reopens the premise underneath and applies.

Three things happen, none of them a bug. B's `dg reopen` prints *"1 decided
descendant(s) rest on it"* — describing A's work, which exists only in the tray
and which B has never seen. `dg pending` shows three ops with two authors and no
record of which is which. And B's `dg apply` publishes A's decision.

**The resulting graph is correct.** `D02` is `PROVISIONAL`, propagated properly,
`dg check` clean. What went wrong is that A's answer was published at a moment A
did not choose. And A gets no signal:

```
$ dg pending
nothing staged
```

The obvious reading of that is *my staging failed*, and the obvious repair is to
stage again — where the tool finally catches it, accurately, naming both exits:

```
D02 already has an answer, and is PROVISIONAL — `dg confirm` it if it still
holds, or `dg reopen` it
```

So the store never breaks. What breaks is an agent's model of it, and the agent
finds out only by trying to write.

### 2 — what one writer at a time actually costs

The same setup played the supported way: B runs `dg pending` before staging,
sees an op that is not B's, and stops. Three lines, no drama, and it is here so
the demo describes a limit rather than arguing against the tool.

It is also a *habit*, and the README is already clear about what becomes of
habits. Two people in two terminals keep it, because they are one intent with
two hands. Two agents are two intents.

### 3 — a stale premise, still legal · **the one to read**

*One clone each* — the isolation a harness gives parallel agents. Scene 1 cannot
happen here.

A settles `D03` reasoning from `D01` as it stands, and writes a falsifier it has
no reason yet to think will matter: *"the weights outgrow what a header can
carry"*. Before A applies, B reopens `D01` — the sponsor donated cluster time,
the exact event `D01`'s falsifier named — and six weeks later settles it the
other way: **a trained net, 40 MB**.

A pulls and applies the batch it composed before any of that existed:

```
· D01 moved since this batch was staged (its answer changed) — op 0 (close D03)
  rests on it
✓ applied 1 op(s) → decisions.json + decision-graph.md
```

`D01` never left `DECIDED`, so no invariant fired and the batch landed. Then:

```
$ dg check
✓ 3 vertices, 3 edges; 2 tasks, all invariants hold

$ dg why D03
D03  DECIDED  What ships in the release binary?  [Release]
  One binary, no runtime files. The weights compile in as a generated header —…

CHAIN  D01 → D03
  D01  DECIDED  Where do the evaluation w…  ·  A trained net, 40 MB. Six weeks…

→ every premise under this is settled
```

Four lines apart: *the weights compile in as a generated header*, and *a trained
net, 40 MB*. Not one warning stands — the `evidence_unharvested` this scene
opened with is gone, because A did harvest it. **This graph is as clean as the
tool can certify and it describes a release process that cannot produce a
release.**

`dg check` is not wrong. The structure is valid; the contradiction is in the
prose, where no invariant can reach. The only thing in the entire system that
said so was that one drift line, printed once, at apply time, to a process that
may not have been reading.

**How it ends.** A's falsifier has fired, so the exit is a command rather than a
judgement call:

```sh
dg reopen D03 --why "its falsifier fired: D01 moved to a 40 MB net" --yes
```

The claim is narrow and true: **drift does not prevent the stale answer; it is
the one chance anybody gets to notice.** Everything after that depends on the
falsifier having been written first — which is the argument the whole tool rests
on, arriving as a consequence rather than as an assertion.

### 4 — the loud one, and the two collisions

Three interleavings, three refusals, deliberately worded three different ways.

**4a. The dangerous case aborts.** Scene 3 except B stops at reopening, so A's
close would put a `DECIDED` vertex on a `REOPENED` premise:

```
✗ aborted, nothing written
would leave the graph invalid:
  [propagation] D02 is DECIDED but rests on D01 (REOPENED) — `dg repair` marks
  it PROVISIONAL, or settle the premise with `dg decide D01`
```

Read beside scene 3, this is the boundary: the same race one notch further, and
the tool goes from a one-line note to a refusal. What separates them is whether
the resulting *structure* is legal — and that does not track how wrong the
answer is. "A human can explain every weight" under a 40 MB net is refused;
"412 KB, compiled into the binary" under the same net lands clean.

**4b. One id, two questions.** Both agents reach for the next free number:

```
✗ aborted, nothing written
D04 already exists, and is not what this op would have created — pick another id
```

**4c. One id, the same question.** Both agents sharing a brief notice the same
gap and file it identically — the commoner case:

```
· refused — another writer got there first
D05 already has this vertex, identical to the op staged — another writer applied
it while this batch was waiting. Nothing of yours was lost; it is in the store,
and the tray holds a duplicate of it.
```

4b and 4c are the same collision and must not read the same. An agent that takes
4c as *my work failed* re-files under a fresh id, and the graph ends up with one
question behind two vertices — a fault nothing downstream can detect, because
both are individually valid. So the message spends four lines saying *nothing of
yours was lost* and names the one op to drop rather than the batch to abandon.

### 5 — isolation moves the race, it does not remove it

Two agents do nothing wrong at all: unrelated questions, different ids, valid
batches, clean applies on both sides. Then both commit and push.

```
CONFLICT (content): Merge conflict in decision-graph.md
CONFLICT (content): Merge conflict in decisions.json
```

`decisions.json` is a JSON array and git merges it as text. **This is what the
isolation cost**: scenes 1 and 2 cannot happen once each agent has its own
clone, and this can — in git, where `dg` has no visibility at all.

The resolution is two facts about the file layout and one command:

- **`decision-graph.md` is generated.** Never resolve it; take either side.
  `git checkout --ours decision-graph.md`.
- **`decisions.json` is resolved by hand**, as a union of the two vertex lists.
  (`scenes/union.py` does it here so the scene can run unattended. It is not a
  merge driver and should not become one — the interesting conflicts, two
  answers to one question, a reopen against a close, have no union.)
- **Then the step that makes it safe:** `dg render && dg check`.

`dg check` is the merge test. A conflict resolved wrongly — a vertex dropped, an
edge target lost — is a *structural* fault, and structural faults are the ones
this tool does catch. So scene 5 ends somewhere scene 3 does not: there, the
graph was clean and wrong and one line ever said so; here, the damage a bad
resolution can do is exactly the damage `dg check` is built to find.

### Since this scene was written: `dg integrate`

**The scene still runs, and it is still worth running, because it is what
happens when somebody reaches for git first.** But the hand union it ends on is
no longer the best answer available, and the parenthesis above is the reason:
the interesting conflicts have no union. `dg integrate <ref>` is the answer
that does not need one.

It reads three graphs — this one, theirs, and what they branched from — and
expresses their contribution as **ops** rather than as a file. Then it replays
them, which is where the difference shows: `union.py` cannot tell a removed
vertex from one it never saw, so a deletion is silently reverted; replay has
the base, so a removal arrives *as a removal* and dropping it is something a
person does on purpose. Two answers to one question, which no union resolves,
becomes one of exactly three questions a person is asked — with the answer that
loses recorded rather than lost.

Read this scene as the cost of the merge nobody should reach for, and
`docs/how-it-works.md` § *Bringing somebody else's work in* for the one they
should.

## What this demo does not show, and why

**The commit gate under two agents.** `docs/quickstart-agents.md` records that on
opencode the gate does not see subagent shell calls
([opencode#5894](https://github.com/sst/opencode/issues/5894)), so a commit made
from inside a spawned agent is not gated. That is the sharpest agent-specific
failure this project knows about and it deserves a scene — but reproducing it
needs opencode and a real subagent, which breaks the rule the rest of this demo
is built on. It is named here rather than faked.

**Anything about model behaviour.** Whether an agent *reads* the drift line in
scene 3 and acts on it is exactly the question this demo cannot answer.

**A retraction.** None of the above makes two agents supported *in one
checkout*. The answer stays *one writer at a time* there — one tray, no notion
of whose ops are whose — and scenes 1 and 2 are the evidence for that sentence
rather than an argument against it.

What has changed since is the other question, the one scenes 3 to 5 are about:
two agents in two clones. `dg range` retires the id collision they produced for
*every* record either added, and `dg integrate` replaces the hand-resolution
scene 5 ends on. Neither touches the tray, so neither makes scene 1 or 2 come
out differently — which is why this page is amended rather than withdrawn.

## Files

| | |
|---|---|
| `decisions.json`, `tasks.json` | the graph as both agents found it |
| `demo.sh` | the driver — `./demo.sh [1-5]` |
| `scenes/1.sh` … `5.sh` | one interleaving each, self-contained |
| `scenes/lib.sh` | the work directory, and who-ran-what narration |
| `scenes/union.py` | scene 5's hand-resolution, so it can run unattended |
| `gitignore.txt` | what `dg` would add itself, committed up front so no scene's diff is about it |
| `slides.html` | the same five scenes as a deck, for reading before running |
