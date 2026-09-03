# A session, start to finish

[The agent quickstart](quickstart-agents.md) lists the three mechanisms. This is
one whole session that uses all of them, in a project called `retrieval-index` —
a semantic search service over a crawled corpus — so you can see what arrives
unasked, what the agent reaches for on its own, and where it stops to ask you.

Every block below is real output from `dg`, captured while walking this session.
Two cosmetic edits: the project path is shortened to `/home/you/retrieval-index`,
and the dates are the ones the story implies — the capture happened in one
sitting. What Claude *says* is paraphrased; what it *runs* is not.

## The project before the session

Four decisions, three of them settled back in February, and a task graph
alongside:

```
D01  Exact or approximate search?            DECIDED  → opens D02, D03
D02  Which distance metric?                  DECIDED
D03  How are queries spread across cores?    DECIDED  → opens D04
D04  How does the index absorb new documents? OPEN

T01  Provision the serving box, load the array   DONE   because D01
T02  Pin the scan threads to the perf cores      TODO   because D03
T03  Check in CI that the encoder normalises     TODO   because D02
```

D01's answer was *exact brute-force search over 2.1M vectors*, and whoever wrote
it recorded a falsifier: **"the corpus passes ~10M vectors, where a full scan
stops holding p99 under the 50 ms budget"**. That sentence is what makes the rest
of this session work. It was written in February, before anybody knew whether the
crawl would ever get there — and it is a threshold on a number that is already on
a dashboard, which is the only kind of falsifier that ever fires on its own.

---

## Before you type anything

You open Claude Code in the project. The `SessionStart` hook runs `dg brief` and
puts the result into the session's context:

```
DECISION GRAPH  /home/you/retrieval-index  4 decisions: DECIDED 3, OPEN 1
Record what gets settled with `dg` -- see the dear-guide skill.

FRONTIER (1) -- not settled
  D04  OPEN  How does the index absorb new documents?  [Index]
       decidable now

RESTING ON A PREMISE UNDER REVIEW (0)

STAGED BUT NOT APPLIED: 0 decision, 0 task

TASKS  3: DONE 1, TODO 2   (2 ready, 0 blocked)
CHECK: clean
```

You do not see this in your terminal — it goes into Claude's context, not into
the transcript. Run `dg brief` yourself if you want to read it. It costs about
fifteen lines per session, and it fires on startup, on `--resume`, and on
`/clear`.

**In a project with no `decisions.json`, nothing happens at all**: no output, no
error, no cost. The same hook runs and exits silently.

---

## Turn 1 — a number crosses a threshold somebody wrote down

> **You:** The crawl finished overnight. We're at 48M vectors. Work out what that
> breaks.

Claude has the frontier in context but not the discipline, so it loads the
`dear-guide` skill — the model, the rules, the command table — and then reads the
decision the number bears on, before touching anything:

```sh
dg node D01
```

```
╭──────────────────────────────── D01 ─────────────────────────────────╮
│ Exact or approximate search?                                         │
│                                                                      │
│ status      DECIDED                                                  │
│ area        Search                                                   │
│ depends on  —                                                        │
│ opens       D02, D03                                                 │
│ falsifier   the corpus passes ~10M vectors, where a full scan stops  │
│ holding p99 under the 50 ms budget                                   │
│ source      bench/scan-latency.md   (2026-02-11)                     │
│                                                                      │
│ Answer                                                               │
│ Exact: one brute-force scan over the 2.1M-vector array, 8 ms a query │
│ across eight cores. While the scan is exhaustive a recall regression │
│ can only be the encoder's, never the index's, which is worth a lot   │
│ while the encoder is still moving.                                   │
╰──────────────────────────────────────────────────────────────────────╯
```

48M is not near the ~10M threshold, it is five times past it. This is not a
judgement call about whether the decision still stands — it is the case the
falsifier was written for. Claude stages the reversal:

```sh
dg reopen D01 --yes \
  --why "the crawl finished at 48M vectors, five times the ~10M threshold in the falsifier"
```

```
╭───────────────────────────── reopen D01 ─────────────────────────────╮
│ Exact or approximate search?                                         │
│                                                                      │
│ Its answer becomes superseded; its dependencies stay.                │
│                                                                      │
│ 2 decided descendant(s) rest on it and become PROVISIONAL:           │
│   D02, D03                                                           │
│                                                                      │
│ 2 unfinished task(s) rest on a premise under review:                 │
│   T02, T03                                                           │
╰──────────────────────────────────────────────────────────────────────╯
```

**That output is the answer to your question.** You asked what the crawl breaks;
the graph computed it. Two settled decisions now rest on a premise under review,
and two pieces of work in flight exist because of them — T02 is somebody spending
this week pinning scan threads to cores for a scan you are about to stop doing.

Nothing is written yet. Claude reviews the batch, then applies it:

```sh
dg pending
```

```
STAGED  3 op(s)
  0  kfnq  reopen      D01  the crawl finished at 48M vectors, five times the ~10M threshold in the…
  1  bwsp  set_status  D02  → PROVISIONAL (from D01)
  2  hjza  set_status  D03  → PROVISIONAL (from D01)
`dg apply` to write, `dg drop <id>` to unstage
  `dg pending --full` for the table, nothing clipped
```

Two ways to name an op, and both are in the row: the position, which is what
the tool's own messages say, and the short id, which survives another writer
applying a batch out from under this one. `(from D01)` marks the ops the reopen
derived rather than ones you wrote.

```sh
dg apply
```

```
✓ applied 3 op(s) → decisions.json; the view is regenerated on demand with `dg render`
```

Agents apply their own work rather than leaving it for you. `apply` validates a
copy of the graph before writing anything and refuses a batch that would leave it
invalid, the store is append-only, and the result is a git diff you review like
any other — whereas an op staged into a gitignored file and then committed over
is gone with no trace. See [Still open](open-questions.md) for the
argument.

---

## Turn 2 — deciding it again

> **You:** Run the ANN sweep and take the best config that holds recall.

D01 is `REOPENED`, so it can be answered again. Rule 6 — one active answer per
decision — is why the reopen had to come first. Skip it and the CLI says so and
exits nonzero, rather than quietly overwriting February:

```
D01 already has an answer, and is DECIDED — `dg reopen` it first
```

Claude runs the sweep, writes it up, and records the decision against it:

```sh
dg decide D01 \
  --answer "Approximate: HNSW with M=32, efConstruction=200, efSearch=96. Recall@10 against exact search is 0.962 on the held-out queries, at 11 ms p99 -- the sweep is in bench/ann-sweep.md." \
  --source "bench/ann-sweep.md" \
  --falsifier "recall@10 against exact search falls below 0.95 on the held-out query set" \
  --opens "D02,D03"
dg apply
```

Every flag is mandatory in practice: `dg decide` prompts for anything missing,
and a prompt with nobody at the keyboard is a hung command. The falsifier is the
one Claude cannot generate from the answer alone — it is a claim about the
future — so this is the moment to read what it wrote. "Recall@10 against exact
search falls below 0.95" is a number the eval job already produces every night.
"The index might degrade" would not be.

Note what that falsifier quietly commits you to: keeping the exact scanner
around as an oracle. That is the sort of consequence that lives in somebody's
head until a graph makes it a sentence.

---

## Turn 3 — the two decisions that were resting on it

`dg check` now says what is left over:

```
! [stale_provisional] (warning) D02 is PROVISIONAL but every premise it rests
  on is settled again — re-examine it, then `dg confirm D02`
! [stale_provisional] (warning) D03 is PROVISIONAL but every premise it rests
  on is settled again — re-examine it, then `dg confirm D03`
! [link_premise_under_review] (warning) T02 is TODO and exists because of D03,
  which is PROVISIONAL — the reason for this work is under review; re-check it
  before carrying on
! [link_premise_under_review] (warning) T03 is TODO and exists because of D02,
  which is PROVISIONAL — the reason for this work is under review; re-check it
  before carrying on
✓ 4 vertices, 4 edges; 3 tasks, all invariants hold, 4 warning(s)
```

Warnings, not errors — a graph mid-reversal is a normal state and does not fail
your build. But `PROVISIONAL` is a promise to go back and look, and each of the
two has exactly one honest resolution.

D02 — *cosine, on vectors the encoder has already L2-normalised* — was argued
from the encoder, not from the corpus size, and HNSW builds a cosine graph as
happily as a scan reads one. It survives the new premise untouched:

```sh
dg confirm D02
```

```
staged 1 op(s) — D02 back to DECIDED, review with `dg pending`, then `dg apply`
```

D03 does not. Read its answer again with the new premise in mind:

> No sharding. One process holds the whole array and each query fans out across
> the eight cores: *an exhaustive scan is embarrassingly parallel*, and 2.1M × 768
> fp32 is 6.4 GB, which fits the serving box with room to spare.

Every clause after the comma is now false. An HNSW traversal touches a few
thousand nodes along a path, which is not an embarrassingly parallel scan, and
48M × 768 is not 6.4 GB. The sentence still parses; the reason is gone:

```sh
dg reopen D03 --yes \
  --why "the array no longer fits one box, and an HNSW traversal is not the embarrassingly parallel scan this answer was reasoning about"
dg decide D03 \
  --answer "Four shards by document id, each with its own HNSW graph on its own box, merged by the query node. The merge costs 1.5 ms and the shards rebuild independently." \
  --source "bench/shard-merge.md" \
  --falsifier "a shard's graph stops fitting its box's RAM, or the merge starts dominating p99" \
  --opens "D04"
dg apply
```

Note what Claude did **not** do: overwrite D03's answer. February's answer is
still in the store, marked inactive, with the reason it was replaced:

```sh
dg node D03
```

```
╭──────────────────────────────── D03 ─────────────────────────────────╮
│ How are queries spread across cores?                                 │
│                                                                      │
│ status      DECIDED                                                  │
│ area        Serving                                                  │
│ depends on  D01                                                      │
│ opens       D04                                                      │
│ falsifier   a shard's graph stops fitting its box's RAM, or the      │
│ merge starts dominating p99                                          │
│ source      bench/shard-merge.md   (2026-08-19)                      │
│                                                                      │
│ Answer                                                               │
│ Four shards by document id, each with its own HNSW graph on its own  │
│ box, merged by the query node. The merge costs 1.5 ms and the shards │
│ rebuild independently.                                               │
│ implemented by T02 (DROPPED), T05 (TODO)                             │
│                                                                      │
│ Superseded                                                           │
│   “No sharding. One process holds the whole array and each que…” →   │
│ Four shards by document id, each with its own HNSW graph on…         │
│     the array no longer fits one box, and an HNSW traversal is not   │
│ the embarrassingly parallel scan this answer was reasoning about     │
╰──────────────────────────────────────────────────────────────────────╯
```

That is the whole point of the thing. In six months, *"why are we sharding at
48M when a single HNSW graph would fit one box?"* has an answer that is not
somebody's memory. `implemented by` reads across to the task graph, so the
record shows the work that was abandoned as well as the work that survived.

---

## Turn 4 — the work that was built on the old answer

The reopen named T02 and T03 back in turn 1. T03 is fine — D02 was confirmed, and
checking the encoder still normalises matters more under HNSW than it did under
the scan. T02 is a week of work on a scan you are no longer going to run:

```sh
dg task drop T02 --why "the scan those threads were being pinned for is not being run"
dg task add --id T04 --title "Build the HNSW index and sweep efSearch" \
            --area Search --because D01
dg task add --id T05 --title "Split the corpus into four shards and merge results" \
            --area Serving --because D03
dg apply
```

`--why` is not optional and the command will not proceed without it — bare, it
prompts, and a prompt with nobody at the keyboard is a hung command. It is the
same rule `--falsifier` follows: the sentence is the whole record, and written
afterwards there is nothing to write it from. Nothing waited on T02, so no
verdict was needed; had anything been left standing, the drop would have listed
it and refused until each one had `--keep` or `--drop-too`.

The other verb for stopping is `dg task park T14 --why "…"`, which records the
same entry and settles nothing downstream — work nobody is doing, as against
work nobody is going to do. T02 is the second kind.

```sh
dg task
```

```
TASKS  3 outstanding of 5   DONE 1  DROPPED 1  TODO 3
  T03  TODO  Check in CI that the encoder still normalises        ·  because D02 · Search
  T04  TODO  Build the HNSW index and sweep efSearch              ·  because D01 · Search
  T05  TODO  Split the corpus into four shards and merge results  ·  because D03 · Serving
ready T03, T04, T05
  `dg task --full` for the table, nothing clipped
```

`--because` is the link that made turn 1 useful. Without it the reopen would have
reported two decisions and stayed quiet about the week of work underneath them.
Tasks are a separate store with separate ids — a task never lands in
`decisions.json`, and the test is whether you can write a falsifier for it (a
decision) or a definition of done (a task).

---

## Turn 5 — the commit, and the gate

> **You:** Commit that.

```sh
git add decisions.json tasks.json
git commit -m "Approximate search: reopen D01, reshard the query workload"
```

The `PreToolUse` hook hands every Bash command containing `commit` to `dg gate`.
This one comes back **deny**, and Claude sees the reason:

```
decisions.json is staged but decision-graph.md is not, so the commit would
record a store and a view that disagree. Run `dg render`, then
`git add decision-graph.md`.
```

Worth being precise about what was caught. The worktree was *valid* — `dg check`
was clean, because it reads the files on disk. The commit would not have been:
the store would move and the generated view would not, and the next person to
read `decision-graph.md` at that commit would read February's answer. The gate
compares the index, which is the only place that mismatch exists.

Claude fixes it the way the message says and retries:

```sh
dg render && dg task render
git add -A
git commit -m "Approximate search: reopen D01, reshard the query workload"
```

```
[master 1a3cfad] Approximate search: reopen D01, reshard the query workload
 6 files changed, 77 insertions(+), 10 deletions(-)
```

Six files: the two stores, their two generated views, and the two benchmark
write-ups the decisions cite as their source. That is rule 8 — the graph moves in
the same commit as the work that changed it.

A clean graph is silence — the gate emits nothing and your own permission rules
decide, exactly as they would without the plugin installed.

---

## Turn 6 — the context runs out

Long session, so `/compact` fires. The same hook runs again with one extra line
in front of the brief:

```
Context was just compacted. Anything settled in what was dropped that is not in
the graph below still needs recording.
DECISION GRAPH  /home/you/retrieval-index  4 decisions: DECIDED 3, OPEN 1
...
```

This is the one place the plugin nags, and it is deliberate: the compaction
boundary is the moment prose is actually about to be lost. Asking at the end of
every turn would be wrong on almost every turn, and would teach the model to say
no.

---

## Turn 7 — a benchmark, and the question it was supposed to settle

D04 has been open all session: how does the index absorb newly crawled documents?
Under the old exact scan it was nearly free — append to the array. Under four
HNSW graphs it is a real question, so you send Claude to measure it first:

```sh
dg task add --id T06 --title "Time incremental insert against a nightly rebuild" \
            --area Index --evidence-for D04
dg apply
```

`--evidence-for` points the other way from `--because`: this work exists to
*inform* a decision rather than to follow from one. The frontier stops claiming
D04 is ready to settle the moment the benchmark is queued:

```
FRONTIER (1) -- not settled
  D04  OPEN  How does the index absorb new documents?  [Index]
       waiting on evidence from T06
```

Claude runs it, then records what it produced:

```sh
dg task done T06 \
  --outcome "bench/rebuild-vs-insert.md -- 40 min rebuild; insert drifts 3 points of recall@10 after 200k"
```

And now the graph knows something you would otherwise have to remember:

```
! [evidence_unharvested] (warning) T06 is DONE and was to inform D04 (How does
  the index absorb new documents?), which is still unsettled — record what it
  showed with `dg decide D04`, or drop the link
```

The brief says it too, so it survives into the next session:

```
TASKS  6: DONE 2, DROPPED 1, TODO 3   (3 ready, 0 blocked)
  evidence in hand, nothing recorded (1):
    T06  Time incremental insert against a nightly rebuild  -> `dg decide D04`
```

A benchmark whose conclusion nobody wrote down is the most ordinary way a project
loses a result it paid for — the numbers are in a file, the reasoning is in
somebody's head, and six weeks later the argument gets had again from scratch.
Claude closes it:

```sh
dg decide D04 \
  --answer "A nightly full rebuild per shard, 40 minutes on the build box, swapped in atomically. Incremental insert drifts 3 points of recall@10 after 200k inserts and there is no cheap way to get them back." \
  --source "bench/rebuild-vs-insert.md" \
  --falsifier "a document needs to be searchable within an hour of being crawled"
dg apply
```

```
DECISION GRAPH  /home/you/retrieval-index  4 decisions: DECIDED 4
...
FRONTIER (0) -- not settled
CHECK: clean
```

---

## What was mechanism and what was you

| | |
|---|---|
| the frontier arrived before turn 1 | **mechanism** — `SessionStart`, and again after compaction |
| Claude knew `--falsifier` is required and never hand-edited the store | **mechanism** — the `dear-guide` skill, loaded on demand |
| the reversal listed both dependent decisions *and* the work in flight | **mechanism** — `dg reopen` computes it; nobody works it out by hand |
| the benchmark's conclusion could not quietly go unrecorded | **mechanism** — `--evidence-for`, and the check behind it |
| the half-staged commit was refused | **mechanism** — `dg gate` on every Bash command containing `commit` |
| **noticing 48M crossed D01's threshold** | **you and Claude**, in turn 1 |
| **judging that D02 survived and D03 did not** | **you and Claude**, in turn 3 |
| **the falsifiers themselves** | **you** — read them; they are the part a model cannot check |

The mechanised half is what a long-running agent forgets. The unmechanised half
is what nothing observable can reveal: whether something just got settled is a
property of the reasoning, not of a tool call.

---

## Things this session did not hit

**Staged work at commit time** → `ask`, not `deny`, because it is a judgement
call about work in progress:

```
1 decision op(s) are staged and not applied. .dgraph-pending.json is gitignored,
so committing now drops them from the record with no trace in the diff.
`dg apply` writes them; `dg clear` discards them.
```

**A view that has fallen behind its store** → `warn`, not `deny`. The commit
runs and you are told once:

```
decision-graph.md no longer matches decisions.json, and this commit records it.
`dg render` rebuilds it; committing as it stands leaves the generated view
behind until someone renders again.
```

The view is generated, so nothing is lost — `dg render` rebuilds it on demand.
It used to be a blocking violation, which
meant one un-rendered file denied *every* commit in the repository, including
commits that never touched the graph. `dg check` still reports it.

**A genuinely invalid store** → `deny`, quoting the rule and the remedy:

```
The decision graph is not valid, so this commit would record a contradiction:
  [propagation] D02 is DECIDED but rests on D01 (REOPENED) — mark it
  PROVISIONAL or settle the premise
Fix it first: `dg check` names every rule that broke.
```

**A commit into another repository** — `git -C /elsewhere commit` is allowed
even with this graph broken, as long as `/elsewhere` resolves to a different git
toplevel. The gate is scoped to the project it runs in, and every doubt (an
unresolvable path, a `--git-dir` override, no git at all) resolves towards
gating rather than away from it.

**A question with no vertex.** If you settle something the graph has no entry
for, the skill says add it and then decide it rather than staying silent:
`dg add --id D05 --title "Quantisation for the served vectors" --area Serving
--after D01`, then `dg decide D05`.

**A project with no graph.** Claude will not run `dg init` uninvited — the skill
says so explicitly. Ask for it.

**Composing in an editor.** `dg decide D04 --edit` opens an org buffer with the
premises and their falsifiers already in it, and `dg task done T03 --edit` does
the same for what a piece of work produced; the [web app](quickstart-web.md)
opens the decision buffer from a browser. All of them are for humans — an agent
passes flags.

**Switching it off.** `DG_HOOK_OFF=1` disables the brief and the gate together.

---

## What it costs, and what it will not do

- **About fifteen lines of context per session start**, plus a subprocess. In a
  project with no `decisions.json`: nothing at all.
- **Nothing here needed a slash command.** The brief is injected and the skill
  loads when a decision is in play, so the seven commands — `/dg:brief`,
  `/dg:frontier`, `/dg:tasks`, `/dg:find`, `/dg:context`, `/dg:serve`,
  `/dg:version`, spelled
  `/dg-brief` and so on under opencode — are for asking rather than waiting to
  be told. `/dg-brief` matters more on opencode, whose brief injection is the
  one part its API does not guarantee.
- **The gate only sees commands containing one of its trigger words** — `dg gate
  --triggers` prints them, `commit` and `rm` today. That is the fast path *and*
  the boundary: everything the gate can refuse contains one of them, so nothing
  slips past that it would have stopped. The list read `commit` alone for a
  while after `dg rm` learned to ask, which is exactly why it is now printable
  and pinned by a test rather than repeated in two adapters as a comment.
- **It cannot tell you a decision was made.** It can tell you what is open, what
  rests on what, what a reversal broke, and that a commit would contradict the
  record. The moment of recording stays yours.
- **Nothing reads falsifiers back.** A falsifier nobody revisits is a comment —
  and the one in turn 1 fired because a human read it and recognised the number,
  not because anything compared 48M against the threshold. Wiring that up is
  [still open](open-questions.md).
- **Version skew is real** — the plugin and the `dg` package install separately.
  `dg --version` exists so you can check — a commit hash while this is beta,
  which `git log -1 --format=%h` in the checkout you installed from answers
  against; the brief hook says so explicitly when it meets a `dg` too old to
  know `dg brief`.

## Where to go next

- [The agent plugin](quickstart-agents.md) — install, the three mechanisms, the
  opencode differences.
- [How it works, and why](how-it-works.md) — the model the agent is maintaining.
- [The CLI](quickstart-cli.md) — every command the agent is driving.
