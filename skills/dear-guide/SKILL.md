---
name: dear-guide
description: >-
  Read, record and reverse this project's decisions, and track the work that
  follows from them, using the `dg` CLI over decisions.json and tasks.json. Use
  when settling a design question, when asked what was already decided or why,
  when reversing or revisiting an earlier decision, when a plan touches
  something already settled, when planning or picking up a piece of work, when
  work's premise is reopened and its status must follow, when finished work
  leaves a question still open, or when the commit gate stops a commit. Covers the model (explicit status, a mandatory falsifier,
  append-only history), the link between the two graphs, the command table, and
  the staging workflow.
---

# The development graph

`decisions.json` is the sole source of truth for what this project has decided.
`decision-graph.md` is a **generated view** of it — read it, never edit it.
Nothing else in the repo records decision state, and nothing else should start
to. Work through `dg`.

If there is no `decisions.json` at or above the working directory, this project
does not track decisions this way. Nothing here applies, and you should not
create a graph uninvited.

## The model

Vertices and edges, nothing else.

- A **vertex** is a decision the project must make, carrying an explicit status:
  `DECIDED` · `OPEN` · `BLOCKED:<id>` · `REOPENED` · `PROVISIONAL`.
- An **edge** is a dependency, which gains a payload — answer, falsifier, source,
  date — once that decision is made. An edge with no answer means *B depends on
  A, and A is not settled yet*.
- **Dependency is the graph structure**, never a stored field. It used to be
  stored twice, in opposite directions, and 11 of 55 nodes disagreed.
- **Superseded is not a status.** It is an edge marked inactive and kept forever.
  The reversals are the most valuable thing the graph holds.
- A **terminal** decision is one whose edge opens nothing.

## Rules

1. **Status is explicit, never inferred.** A decision can have consequences and
   still be under review, so out-degree never means "done".
2. **Append-only.** Never delete a vertex or an edge, and never rewrite an
   answer in place — a reversal marks the old edge inactive and adds a new one.
3. **Every closed decision records a falsifier**: what evidence would overturn
   it, written *before* that evidence arrives. Afterwards it is
   rationalisation. If nothing could overturn it, write `ANALYTIC — <why>`
   rather than leaving it blank.
4. **Every decision cites a source** — a file in the repo, a script, or
   `discussion`.
5. **Reopening propagates.** Every decided descendant of a reopened decision
   rests on a premise under review and becomes `PROVISIONAL`. `dg reopen`
   computes that set; do not work it out by hand.
6. **One active answer per decision.** A decision that already has one must be
   reopened before it can be answered again.
7. **Decisions only in this store** — not milestones, not file lists. The graph
   stops being useful the moment it becomes a tracker. Work belongs in the task
   store, which is a separate graph with its own ids: see "Recording work"
   below. The test is whether you can write a falsifier (a decision) or a
   definition of done (a task).
8. **Update the graph in the same commit** as the work that changed it, and
   commit `decisions.json` and `decision-graph.md` together.

## Sharing a clone with another writer

Only where something has actually put two of you in one checkout. In an
ordinary project none of this fires and there is nothing to set.

The staging tray is **one file that everybody shares**, and `dg apply` writes
what is in it. So a draft you left staged is a draft somebody else's `dg apply`
can turn into a decision — and a decision is the one thing this graph makes
hard to take back.

**`$DG_AGENT` is a name that is yours**, and you are given one rather than
choosing one — every value it has gone wrong on was a value somebody invented.
If you were launched alongside other writers, whatever launched you set it. Do
not make one up.

**If you are the one launching them**, take a name per agent and hand it down:

```sh
DG_AGENT=$(dg agent claim) claude -p "…"      # one per agent
dg agent list                                 # who holds what, and what is staged
```

`dg agent claim` prints a free name and nothing else, so it composes into a
variable. It never hands out a name that is held or that has ops in either
tray, so two agents cannot end up sharing one. Claiming it yourself is no use:
your shell state does not survive between tool calls, so the name has to come
from whatever spawns you.

Nothing is ever reused behind your back — a claim does not expire. `dg agent
release <name>` gives one back, `dg agent prune` releases every name with
nothing staged under it, and both are things a person does deliberately. If the
pool ever runs out, `claim` **refuses** and says what to empty; it will not
invent a name.

With a name set, either way:

- `dg apply` writes **your** ops and leaves everyone else's staged, saying how
  many it left and whose.
- `dg pending` shows who staged each op, and counts every writer — across
  **both** trays — under the listing.
- With no `$DG_AGENT` you are the supervisor: `dg apply` takes the whole tray,
  and **refuses** if it holds work somebody else staged. `dg apply --all`
  writes theirs too; `dg apply --mine` writes only yours; `dg apply --agent
  <name>` writes one named writer's and leaves the rest.

Unset means supervisor, so an agent that forgets to set it is one. Set it
before you stage anything.

### Several agents proposing alternatives

Only where they are proposing *different answers to the same question*. Where
each is working a different area their ops compose, the union is what you want,
and `dg apply` — or `--all` — already is it.

```sh
dg pending --agent b      # read one writer's proposal on its own
dg apply   --agent b      # ...take it, and leave everybody else's staged
dg clear   --agent c      # ...turn one down, without touching the others
```

**How much you may settle on your own** is `$DG_DECIDE`, and it is worth
reading before you close anything in a shared clone. Unset it means what it has
always meant — you may close what you like. `evidence` means you may close only
a decision a *finished* `--evidence-for` task backs, which is the case where the
falsifier is a measurement you made rather than a judgement you are inventing.
`never` means you stage and a person applies. All three are about an agent: a
caller with no `$DG_AGENT` is never refused. You will be told at stage time, by
name, before you have written an answer.

**You can pick your own work, too.** `dg task` ends with a `ready` line —
everything startable right now — and `dg task start` refuses a task somebody
already claimed, so taking work off the frontier is safe without asking. The
claim records who made it, so `dg task` shows `held by <name>`; **park it rather
than abandoning it** if you stop, or the graph goes on saying you have it.

Parking or finishing releases the claim. **Neither store ever records who did
what** — that is deliberate: `tasks.json` is committed and kept forever, agent
names are recycled, and "who finished this" is noise six months on. Who holds
what is scratch, alive only while the run is.

`dg clear` on its own takes the **whole** tray whoever runs it, including work
three other agents were half way through; `--agent` is the narrow form, and the
one to reach for in a shared clone. `--agent unowned` names the ops nobody
signed — which is why **`unowned` is a reserved name**: set `$DG_AGENT` to it
and every `dg` command refuses until you set it to something else. Any other
name works; there is no charset.

Two things to know before using them:

- **`dg apply --agent` is `--all`'s sibling, not `--mine`'s.** It writes
  somebody else's half-composed batch, and a draft `close` applied by another
  writer is a DECIDED answer whose only exit is a `reopen` — a reversal nobody
  made. Only a caller with no `$DG_AGENT` may do it. **As an agent, do not
  reach for it**: apply your own work with a bare `dg apply`, and leave the
  adjudication to whoever is supervising.
- A name nobody staged under is **refused**, with the roster, rather than
  quietly applying nothing. An empty selection and a typo look identical
  afterwards and mean opposite things.

## Reading

| Command | Gives |
|---|---|
| `dg brief` | what matters right now: the frontier, anything provisional, staged work, validity |
| `dg show` | the frontier — everything still open or blocked, one line each |
| `dg find QUERY` | decisions and work by what they *say* — the only reading that starts from a word rather than the frontier or an id. `dg find 'is:decidable'`, `dg find 'under:D04 is:unsettled'`, `--ids` to pipe. `answer:`/`falsifier:`/`source:` read superseded edges too and label a hit `superseded answer`; `--active` narrows them to what stands |
| `dg node ID` | one decision in full, each superseded edge with its own targets, falsifier, source and archived answer. `--active` for the answer that stands, which says how many records it left out |
| `dg context ID` | the chain of premises it rests on. Takes a `T` id too; `--full` for the answers, sources and falsifiers |
| `dg path A B` | the chain of evidence between two decisions |
| `dg tree` | the graph as a tree |
| `dg areas` | counts by area and status, one table per store |
| `dg export ID` | the same data as JSON, for machine reading. `dg import` reads it back unchanged |
| `dg check` | every invariant, and it names the rule that broke |
| `dg import FILE` | adopt a `decisions.json` prepared elsewhere or exported from another project, refusing one that breaks invariants |
| `dg import-md FILE` | rebuild a store from the `decision-graph.md` this tool generated. The recovery path when the *store* is the file that was lost |
| `dg repair` | stage the PROVISIONAL marks a reopen would have derived. What `dg apply` names when a merge, a rebase or a second clone left a decision resting on a premise under review without saying so |
| `dg edit N` | revise a staged op in place, rather than dropping it and retyping what was written |

### Before building on something, or handing it off

`dg node` tells you what a decision says. `dg context` tells you **why**, and
what would make it stop being true: every premise underneath it, ending with
the reading — whether anything in the chain is still under review.

It has two lengths, and which you want depends on who is reading:

- **the default is schematic.** A `CHAIN` line showing the shape of the
  reasoning, oldest premise first, with `!` on any link that is not settled;
  then one line per premise, its answer clipped to a sentence. Use this when
  *you* are the one asking.
- **`--full` prints the chain in full** — each answer, the evidence that
  reached it, the falsifier that would overturn it. Use this when the output is
  going somewhere that cannot ask a follow-up question.

Run it in two situations:

- **before writing code that depends on a decision**, so you know which
  falsifiers you must not quietly trip;
- **before dispatching a subagent**, and paste the `--full` output into the
  prompt. A fresh context knows the task and nothing about why it exists;
  without the chain it cannot tell a constraint from an implementation detail,
  and a clipped answer is exactly the detail it will get wrong.

```sh
dg context D02          # a decision and its premises, schematically
dg context D02 --full   # ...with every answer, source and falsifier
dg context T14          # the work, then the chain behind the decision it exists for
```

The same split runs through `dg show`, `dg task` and both staging trays: one
line each by default, `--full` for the table with nothing clipped. Titles and
details get clipped; **ids never do**, so anything named in a short view can be
looked up from it.

## Starting a graph

The two stores are independent — **either works without the other**, so track
only decisions, only work, or both.

```sh
dg init            # decisions.json (view: dg render)
dg task init       # tasks.json (view: dg task render)
```

Run these before anything else in a project that has no graph. Never write
`decisions.json` or `tasks.json` by hand: every command below refuses input the
store would be wrong to hold, and a hand-written file gets none of that.
The `.md` views are generated, not written at bootstrap (`init`, `import`,
`import-md`, `task init`, `task import`): build one on demand with `dg render`
(or `dg task render`) when you want to read it.

## Recording a decision

Pass every field as a flag — the command prompts for anything missing, and a
prompt with nobody to answer it is a failed command.

```sh
dg decide D37 \
  --answer "HNSW with M=32, efConstruction=200, from the sweep in bench/ann-sweep.md" \
  --source "bench/ann-sweep.md" \
  --falsifier "recall@10 against exact search falls below 0.95 on the held-out queries" \
  --opens "D40,D41"
dg apply
```

`--falsifier` is required whenever the decision opens anything. Two shapes:

- measurable — `"p99 query latency goes above 50 ms at the corpus size we serve"`
- analytic — `"ANALYTIC — cosine and inner product rank identically on
  L2-normalised vectors; no measurement bears on it"`

`--opens` lists the decisions this one now makes answerable; leave it off for a
terminal decision. Nothing is written until `dg apply`, which validates a copy
first and refuses to write a graph that would be invalid. Apply your own work —
leaving it staged means it exists only in a gitignored file.

## Reversing one

```sh
dg reopen D06 --yes --why "the crawl finished at 48M vectors, five times the size this was measured at"
dg apply
```

The output lists every decided descendant that just became `PROVISIONAL`. **That
list is the point of the command.** Each one now rests on a premise under review,
and each needs one of two things once the premise is settled again:

- it still holds → `dg confirm D12`, which records that you re-read it under the
  new premise
- it does not → `dg reopen D12`, then decide it again

Do not reach for reopen to escape a status. A reversal that never happened is a
lie in the record, which is worse than an unfinished one.

## Something the graph has no vertex for

If you settle a question that is not in the graph, add it and then decide it —
do not stay silent.

```sh
dg add --id D42 --title "Quantisation for the served vectors" --area Serving \
       --after D37 --note "blocked on the index structure"
dg decide D42 --answer … --source … --falsifier …
dg apply
```

`dg pending` reviews staged work — one line per op, `--full` for the table —
`dg drop <id>` removes one op, `dg clear` all of it.

## Recording work

Only if the project has a `tasks.json`; if it does not, this section does not
apply and you should not create one uninvited.

Tasks are a second, independent graph — `T` ids, their own store and view, their
own commands. A task is a unit of work with an `outcome`; a decision is a
question with a falsifier. They never share a store.

```sh
dg task                                   # outstanding work, and what is startable
dg task node T14                          # one piece in full, with its premise
dg task tree                              # the order of it: prerequisites, then what they release
dg task pending                           # what is staged for this store; `dg pending` is its twin
dg task add --id T14 --title "Build the HNSW index and sweep efSearch" --area Search \
            --after T09 --because D02
dg task start T14                         # → DOING, so the graph says it is live
dg task done T14 --outcome "PR #241"
dg apply
```

- `--after` names tasks that must be resolved first. Blocked is derived, so
  there is no blocked status to set and none to clear.
- `--because D02` names the decision this work exists because of. Use it
  whenever the work follows from a recorded decision — it is what lets
  `dg reopen` report the work now resting on a premise under review.
- `--evidence-for D05` names a decision this work will *inform* — a benchmark,
  a spike, or a chore that turned up a question. `dg task link T14
  --evidence-for D08` adds it after the fact, which is the usual case when work
  reveals a new question.
- If work turns up a question nobody had written down: add the decision, then
  link the task to it. Do not leave it in prose.

**Mark work in flight when you pick it up**, with `dg task start T14`. A task
that goes TODO → DONE never appears as work in progress, and `dg brief` — which
is what a *second* session or a person reads to find out what is live — has
nothing to show for the whole time you were doing it. It also prints the one
warning nobody else will: if a prerequisite was abandoned rather than finished,
starting is the moment that gets said, and the check that reports it is read by
whoever runs it, which at that moment is nobody. A note, never a refusal.

**Put work down with `dg task park T14 --why "…"`, not `dg task drop`.** Both
record why and when, in the same place — `stops`, one of this store's two
archived records, which nothing ever clears. (`completions` is the other:
finishing work appends a dated outcome, so a task finished, picked back up and
finished again keeps both results. Finishing work that is already `DONE` is
refused — `dg task start` first, and the second completion is then a deliberate
record rather than an overwrite.) They differ *downstream*: dropping says
the work is not needed, so it releases everything that waited on it and demands
a verdict on each one; parking says nobody is doing this right now, so it
settles nothing and its dependants go on waiting. Reach for `drop` only when
the work is genuinely not happening.

A park is cheap on purpose, so it is chased instead: while parked work holds up
anything unfinished, `dg check` reports it until somebody picks it up, drops it,
or removes the dependency. A park that blocks nothing is never mentioned.

Two things parking is *not*. It is not a decision: "we resumed T14" has no
falsifier, and a node recording it is the tracker the decision graph refuses to
become. If the work stopped because a *question* is unanswered, that question is
the decision — record it and use `--because`, and the work resumes by itself
when the question settles. And what revived a task is a relation between tasks,
which `dg task dep T14 --discovered-during T09` already says.

Corrections have commands; never hand-edit `tasks.json`:

```sh
dg task dep T14 --after T09        # a prerequisite discovered later
dg task undep T14 --after T09      # ...and removing one
dg task clear                      # unstage every task op; `dg clear` is its twin
dg task render                     # regenerate tasks.md; `dg render` is its twin
dg task export / dg task import    # move a backlog between projects
dg task unlink T14 --because       # drop a link recorded against the wrong decision
dg amend D07 --title "..."         # a typo'd or since-clarified wording
dg task amend T14 --area Eval      # ...the same op, in the other store
dg dep   D07 --after D03           # a premise discovered later
dg undep D07 --after D03           # ...and removing one
```

`dg undep` works only on a **bare** edge. A decided edge's targets are part of
its answer, so `dg reopen` first, then remove, then decide again meaning it.

**If a reading says a question holds more than one current answer, stop.** It
means the store was text-merged rather than integrated: `dg check` refuses it,
and which answer you were shown is arbitrary, so anything composed against it
is composed against a coin flip. Say so and let a person resolve it — the two
answers are in the store and `dg node` shows both.

**When the commit gate says a contribution is waiting, stop and say so.**
`dg integrate <ref>` brings another writer's work in as ops and quarantines
them in `.dgraph-incoming.json` — deliberately not the tray, so nothing you
read here answers with an op nobody has accepted. While that file is
non-empty the gate answers `deny` on every commit, because the file is
gitignored: committing over it drops a second writer's work with nothing
recording that it arrived. `dg incoming` shows what is in it and what is
contested, each contested op with a ref. Answering one is `--take <ref>` for
the arriving version, `--keep <ref>` for this store's, or — where two answers
turn out to be to two different questions worded as one —
`--split <ref> --as <id>`, which moves the arriving answer to a question of its
own. `--adopt` moves the whole contribution into the trays once every one has
an answer — it
refuses while any is open, and there is no flag that answers them for you.

**Do not answer them yourself.** They are the three only a person can settle:
two answers to one question, two completions of one task, two wordings of one
record. Show the report and ask. Where the answer that loses is a *decision*,
keeping this store's records the other one as **offered and not adopted** —
kept, and not filed as a reversal, because nothing was overturned.

**One writer at a time, unless somebody has granted this clone a range.**
`dg range` says whether one has. With no grant, ids come from the whole
sequence and nothing changes — that is right for one writer and is what almost
every project is. With one, `dg add` and `dg task add` allocate inside it and
refuse an `--id` outside it, because two clones on a shared base otherwise
compute the same next id *every time*, not sometimes. Never set or clear a
grant yourself: it is a fact about how this checkout was set up, and changing
it silently is how two workers end up holding one range.

**`dg amend` reaches a title, an area and a note, and nothing else.** That is
the line, and it is worth knowing which side of it a field is on: those three
are how a record is *referred to* and where it is filed, so nothing is
superseded when one changes and nothing is archived — a changed title leaves no
record, deliberately. An answer, a falsifier, an outcome and a reason work
stopped are dated claims about what happened, and none of them is ever edited
in place: `dg reopen` and decide again, or `dg task start` and finish again.
Before this op existed a retitle had no legal route at all, which made an
ordinary correction one of the hand-edits everything else here works to
prevent.

**Never reach for `dg rm` or `dg task rm` yourself.** They erase a record
instead of superseding it, they are for things that should never have been
written, and `dg gate` answers `ask` on them — the user decides, not you. If a
node looks wrong, say so and let them run it.

When work turns up a **chore** rather than a question, record where it came
from — `--discovered-during` on `dg task add`, or `dg task dep T14
--discovered-during T09` afterwards. It makes T14 wait on nothing; it says only
that doing T09 revealed it. Use it for the chore and a decision for the
question: a manufactured decision that nobody actually had to make is worse
than no record. Both relations can hold between the same two tasks, so `dg task
undep` requires the flag that names which one you are removing.

`dg check` warns when work rests on a decision that has been reopened, and when
a finished `--evidence-for` task's decision is still unsettled — that second one
means a benchmark ran and its conclusion was never recorded. Both are warnings and
never block a commit.

It also warns when evidence lands *after* the answer was settled, which is a
legitimate order to work in but leaves a result nobody has read against the
answer. Three ways out, one per thing the result can turn out to mean: it
refutes the answer → `dg reopen`; the answer never needed it → `dg task unlink
T01 --evidence-for`; **it confirms the answer** → `dg confirm D01 --against T01
--note "what it showed"`, which is the common one. The reading is per task and
per decision, dated, and kept — so confirming against one of two late results
leaves the warning naming the other, and a *later* result post-dates the
reading and brings the warning back on its own.

Two things about the link are errors rather than warnings: a link naming a
decision that does not exist, and a cycle across the two graphs (work that must
finish before a decision that the work exists because of). `dg apply` refuses a
batch that would create either, names the op, and writes nothing — so the fix is
always `dg task drop-op <id>` or restating the link, never editing the store.

## When a commit is stopped

`dg gate` judges the command before it runs and answers one of four ways. Only
one of them is a refusal, and the other two that say anything are the common
ones — so read which you got before deciding what to do.

**`deny` — the commit is refused.** The graph contradicts itself, and committing
would record the contradiction. The refusal quotes the violations; `dg check`
names every rule that broke and exits non-zero. Fix the store, then commit.

**`ask` — work is staged and not applied.** Not a refusal: the commit is legal
and the question is whether you meant it. The staging tray is gitignored, so
committing now drops those ops with nothing in the diff to show for it. Read
them with `dg pending` before anything else. If they are yours, `dg apply`; if
you did not stage them, they belong to whoever did — say so and let them decide,
because applying somebody else's half-composed batch writes a decision they had
not finished making, and this tool deliberately makes a decision hard to take
back. **`dg check` cannot see this at all** — it reports every invariant holding
while the ops sit in the tray. `dg pending` and `dg brief` are the two readings
that show it.

**`warn` — a generated view has fallen behind its store.** Not a refusal either;
the commit proceeds and you are told once on the way past. `dg render` (or
`dg task render`) rebuilds it, then `git add` the view so store and view land
together. `dg check` reports this as a warning and still exits 0.

**`allow` — nothing to say**, and nothing is said.

## Never

- Hand-edit `decision-graph.md`. It is regenerated and your edits are lost.
- Delete a vertex or an edge, or overwrite an existing answer.
- Invent a status outside the five above.
- Record a plan step or a file list anywhere, or a task in `decisions.json` —
  work goes in the task store, with a `T` id.
- Close a decision without a falsifier and a source.
