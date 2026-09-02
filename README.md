<img src="assets/logo.svg" width="76" alt="">

# dear-guide

> *Dear guide, why did we make this decision?*
>
> ```sh
> dg why D06
> ```

Track a project's development as two linked graphs: the **decisions** it has
settled — what each rests on and what evidence would reopen it — and the
**work** that follows from them.

Six months on, that question has an answer: the premise the decision rested on,
the evidence that reached it, the falsifier that would overturn it, and whether
any of it still holds. `dg` is the guide; you are the one who writes it.

A long-running project accumulates decisions in prose — plans, notes, memory
files — and prose drifts. Two documents disagree about whether something was
settled; a decision gets quietly reversed and the three conclusions built on it
stay standing, along with the work already underway on top of them. This keeps
both in structured stores, renders readable views of them, and makes the drift
a test failure — the drift between what is settled and what rests on it, which
is the kind no amount of proofreading catches. A view that has merely fallen
behind its store is a warning: it is generated, and `dg render` rebuilds it.

The two are deliberately separate stores joined at one seam: a task records the
decision it exists `because` of, or the decision its outcome is `evidence_for`.
Nothing else crosses. That is what lets `dg` answer the question neither store
can answer alone — *is this work still resting on something we believe?*

## Model

**Two graphs**, each a plain store of vertices and edges, and one link between
them. They are separate stores on purpose — `dg init` and `dg task init` are
independent, and **either works without the other**. Track only decisions, only
work, or both.

### The decision graph — `decisions.json`

- A **vertex** is a decision the project must make, with an explicit status:
  `DECIDED` · `OPEN` · `BLOCKED:<id>` · `REOPENED` · `PROVISIONAL`.
- An **edge** is a dependency, which gains a decision payload — answer,
  falsifier, source, date — once that decision is made. An edge with no answer
  means *B depends on A, and A is not settled yet*.

Three things follow from that shape rather than from convention:

- **Dependency is the graph structure**, never a stored field. Storing it twice,
  in two directions, is how documents come to contradict themselves.
- **Superseded is not a status.** It is an edge with `active: false`, kept
  forever. Reversals are the most valuable thing a decision log holds.
- **Reopening propagates.** Every decided descendant of a reopened decision
  rests on a premise under review and becomes `PROVISIONAL`. `dg reopen`
  computes that set; `dg check` refuses a graph where it was not applied.

Two invariants do most of the work. **Status is explicit** — never inferred from
out-degree, because a decision can have consequences and still be reopened. And
**every closed decision records a falsifier**: what evidence would overturn it,
written *before* that evidence arrives. Afterwards it is rationalisation.

### The task graph — `tasks.json`

- A **task** is a unit of work, with an explicit status: `TODO` · `DOING` ·
  `PARKED` · `DONE` · `DROPPED`. Finishing one records an **outcome** — a path,
  a PR, a note — and stopping one records **why**, whichever way it stopped.
- An **edge** says which of two things it means. `precedes` is a prerequisite:
  the task it points from must be resolved first. `prompted` is provenance:
  doing that task turned this one up. Provenance makes nothing wait — a chore
  noticed mid-task is usually startable at once, and often has to land *before*
  the task that revealed it can finish. The two can even run opposite ways
  between the same pair — doing `A` turned up `B` (`prompted`), and `B` may or
  may not block `A` (`precedes`, or no edge at all). That looks odd and is
  deliberate: `prompted` records that the discovery happened, and only
  `precedes` — recorded when it is true — says whether the discovered work is
  held back by the work that found it or independent of it.

It is deliberately **not** a copy of the decision graph, and every difference is
the point:

- **Blocked is derived, never stored.** A task is ready when its prerequisites
  are resolved, so there is no status to keep up to date and none to go stale.
  Decisions keep status explicit because a decision can have consequences and
  still be under review; readiness genuinely *is* a function of dependencies.
- **Supersession in two places, and no falsifier.** Two questions here can stop
  being *current* and still be worth having — *why is this work not being done*
  and *what did it produce* — so every stoppage and every completion appends to
  the task and nothing ever clears either; work put down three times says so,
  and work finished twice keeps both results. Which entry is live is derived
  from the status, never stored, which is what stops a record outliving the
  claim it supported. Every other field is current-state. An outcome is a
  record of what happened, not a claim about the world, so nothing can falsify
  it — and a later outcome does not falsify an earlier one either.
- **Two ways to stop, differing downstream and not in the record.** `PARKED` is
  work nobody is doing; `DROPPED` is work nobody is going to do. Both write the
  same entry. What separates them is whether the work that waited on this is
  now free: dropping says yes and releases it, parking says no and holds it.
- **An unconnected task is ordinary.** An unconnected decision is a smell.

### Areas — the vocabulary, in both stores

An area is a **label on a record**, not a schema, and the two stores share the
vocabulary rather than each holding their own: the same corner of a project,
seen as what is undecided and as what is outstanding. So `areas` is an
**accumulating registry in first-use order**. The op that first files a record
under a name registers it, in the store that op writes, and every reader takes
the union — nothing has to be declared first, and neither `init` takes an
`--areas` flag any more.

It used to be a whitelist, and the whitelist could not be added to: no op wrote
the list, so `dg init --areas corpus` followed by `dg task init` left the two
stores disagreeing and the third command refusing an area the first had
declared. Declaring a vocabulary at `init` is declaring it when the project
knows least; in a graph elaborated backwards from a sink, the areas are a
finding.

What the whitelist was really catching was typos, and that is bought back at
the door instead: a **genuinely new** area is compared against the ones in use,
and a near match is refused naming what it resembles, with `--new-area` as the
override. An area that already exists is silent — an `amend` *toward* an
existing area is the fix for a typo, and refusing it would be backwards.
`dg areas rename` refiles a whole area across both stores when one gets in
anyway, and `dg areas prune` drops registered names holding nothing.

### The one seam

A task may name **the decisions** it exists **`because`** of — a task can rest
on several at once — and **the one** decision its outcome will be
**`evidence_for`**. A single `evidence_for` per task; work informing a second
decision is kept as a sibling task linked to it. Opposite polarity: `because`
makes the work wait on the answers, `evidence_for` makes the answer wait on the
work. Nothing else crosses —
`decisions.json` never mentions a task, so a change to it always means a
decision changed.

That one link is what lets `dg` answer the question neither store can answer
alone: *is this work still resting on something we believe?* Reopen a decision
and it reports the unfinished work now standing on a premise under review; finish
a spike and forget to record what it showed, and `dg check` says so.

## Files

| File | Role |
|---|---|
| `decisions.json` | the store — source of truth |
| `decision-graph.md` | generated view; **never hand-edit** |
| `tasks.json` · `tasks.md` | the task graph and its view — its own store, and usable on its own |
| `.dgraph-pending.json` · `.dgraph-task-pending.json` | the staging trays |
| `.dgraph-edit.org` | editor buffer, like `COMMIT_EDITMSG` |
| `.dgraph-capture/` | a fan-out's recording, if one is running — scratch, and gitignored with the rest |
| `quick-start-demo/` | **start here** — [the cookbook](quick-start-demo/index.html): seventeen worked examples of *how do I do that with dear-guide?*, every line a real transcript, with the graph drawn beside each step |
| `demo/` | a runnable graph holding one of every record this keeps, served in the web app, and a walkthrough over it |
| `demo-agentic/` | three agents and a day's work on one graph, as seven scenes — what several writers cost, and what the tool does about it |
| `docs/` | [how it works](docs/how-it-works.md), then quick starts: the [CLI](docs/quickstart-cli.md), the [web app](docs/quickstart-web.md), the [agent plugin](docs/quickstart-agents.md) and [a whole session with it](docs/session-walkthrough.md); plus [the design behind `dg find`](docs/query-framework.md) |
| `.dgraph-serve.json` · `.dgraph-serve.log` | a detached `dg serve` |
| `agentic/` | [running a fan-out against the graph](agentic/README.md) — [`QUICKSTART.md`](agentic/QUICKSTART.md) is three copy-paste recipes (including one where a Claude Code or opencode session answers the broker), [`RUNNING.md`](agentic/RUNNING.md) the procedure end to end, `prompts/` the templates it launches — several agents proposing into one tray and a person deciding. Optionally `agentic/bin/dg` and `agentic/bin/dg-agent`, a capture for when the run itself has to survive |
| `skills/dear-guide/` | the recording discipline, as a skill both agent hosts load |
| `commands/` | the slash commands, one set of files for both hosts — `/dg:brief` under Claude Code, `/dg-brief` under opencode |
| `fanout/` | what `dg-agent setup` writes: `scout.md`, `launch.sh`, and `env.json` — the remit both were generated from, and what `dg-agent env --check --plan` asserts they still agree with. Not scratch: a filled prompt is the thing you read back and reuse |
| `hooks/`, `.claude-plugin/` | the Claude Code plugin |
| `opencode/` | the same mechanisms for opencode |

Everything the tool writes beyond the two stores and their views is scratch —
the trays, the compose buffer, the `.lock` files beside them, and the `.dg-tmp`
sibling an interrupted write leaves. `dg init` adds all of it to `.gitignore`,
which several of the tool's own messages depend on being true.

**Reading the source.** Comments cite findings by number — `audit F29`, `F18` —
from an internal audit log kept while the tool was built. The numbers are
bookmarks, not required reading: each comment states the defect and the argument
in full, so nothing is missing without the log.

## Install

```sh
cd /path/to/dear-guide
pip install -e .

pip install -e '.[tui]'      # optional: a full-screen `dg-agent setup`
```

`typer` and `rich` are the whole of the requirement. The `tui` extra adds
`textual` and nothing depends on it: `dg-agent setup` asks its questions one at
a time without it, and takes them as flags with no terminal at all.

`dg` then works in any project directory holding **either** store —
`decisions.json`, `tasks.json`, or both. It is found by walking up from the cwd,
or set explicitly with `--project PATH` or `$DG_PROJECT`.

New here? [**How it works, and why**](docs/how-it-works.md) walks one project's
decisions — a nearest-neighbour search service — from first question to first
reversal. Then the quick starts: the
[CLI](docs/quickstart-cli.md), the [web app](docs/quickstart-web.md), the
[agent plugin](docs/quickstart-agents.md) for Claude Code and opencode.

## Use

```sh
dg init                                  # start a graph — areas accumulate, none is declared
dg import prepared.json                  # or adopt one prepared elsewhere
dg                                       # the frontier: what is still open
dg show --full                           # ...as a table, with nothing clipped
dg brief                                 # ...plus provisional work, staging, validity
dg node D06                              # one decision in full, reversals and all
dg why D06                               # ...and the chain of premises underneath it
dg why D06 --full                        # ...with every answer, source and falsifier
dg context D06                           # the same command, its other name
dg context T14                           # the same for a piece of work
dg path D01 D09                          # the chain of evidence between two
dg tree                                  # the DAG
dg areas                                 # counts by area, in both stores
dg areas rename Corpus corpus            # refile everything in one area under another
dg areas prune                           # drop registered areas holding nothing
dg find embedding                        # every decision and task that says so
dg find 'under:D04 is:unsettled'         # ...still open in what D04 opened
dg find 'is:decidable' --ids             # ...ids alone, for a pipe
dg find 'brute force' --active           # ...skipping answers since overturned
dg decide D37                            # compose a decision -> staged
dg decide D37 --edit                     # ...in emacs, with context to hand
dg amend  D06 --title "..."              # correct a wording; nothing else is touched
dg reopen D06                            # stage a reopen + its propagation
dg confirm D12                           # a provisional decision, re-examined and standing
dg confirm D12 --against T14 --note "…"  # ...or a late result read against it, and it holds
dg repair                                # a store a merge broke: stage the missing propagation
dg-agent presets                         # the three curated remits, and what each one sets
dg-agent setup                           # a fan-out's prompt, launcher and remit — asks, or takes flags, or --json
dg-agent setup --preset scout            # ...with the whole policy block filled from one word
dg-agent run -- claude -p "…"            # claim a name, compose the environment, run one agent under it
dg-agent env                             # what every $DG_* actually says — and which one was mistyped
dg-agent env --check                     # ...exit non-zero if anything set was not understood
dg-agent broker                          # answer the consent requests agents block on, at a terminal
dg-agent broker --relay --plan fanout/env.json   # ...or publish them, for a session to answer
dg-agent broker --relay --exec-rung auto # ...with commands answered as `auto`, never as a person
dg-agent consent --allow --why "…"       # ...what is waiting on a relay, and the verdict for it
dg-agent claim                           # a free name for one writer, printed bare
dg-agent claim --budget 30m              # ...and how long it may run before its work is handed back
dg-agent list                            # who holds a name, what they hold, what is staged, time left
dg-agent expire                          # stage a park for whatever an out-of-time agent still holds
dg-agent prune                           # release idle names — keeps back any still holding DOING work
dg gate --write PATH                     # may this agent write here? — what the host adapters ask
dg pending                               # review; `--full` for the table
dg pending --agent b                     # ...one writer's proposal alone
dg apply                                 # validate, then write the store
dg apply --mine                          # ...only what this writer staged
dg apply --agent b                       # ...only what ONE named writer staged
dg clear --agent b                       # turn one writer's proposal down
dg check                                 # every invariant
dg serve                                 # web app on 127.0.0.1:8765
dg edit <id>                             # revise a staged op
dg drop <id>                             # unstage one op
dg export                                # the graph as JSON; `dg import` reads it back
```

### Starting from something you already have

`decisions.json` and `tasks.json` **are** the input format — there is no
conversion step. Write the store (by hand, from a script, or by having an agent
read the notes you already keep), then adopt it:

```sh
dg import prepared.json                  # checks it, then makes it the store
dg task import backlog.json              # the same for the work
```

Adopting is not a formality. It refuses a store that would break an invariant,
rather than writing one and letting `dg check` find out later — a bootstrap that
plants a contradiction on day one is the thing this tool exists to prevent. And
it names what is wrong in the file:

```
✗ not imported
decisions.json: decision D01 has "owner", which a decision does not have.
The fields are: id, title, area, status (required), note, format.
```

The schema is the Model section above; `dg export` prints a real one. `--force`
adopts a graph that breaks invariants so you can repair it with `dg`; it does
**not** overwrite a store that is already there, which is refused outright.

**`dg export` round-trips.** Its payload is the store plus blocks the browser
would otherwise recompute, and the import recognises those by name, drops them
and rebuilds them from the edges — so a copied graph comes back byte-identical:

```sh
dg -C old export      > graph.json
dg -C new import        graph.json    # recomputed from the edges: derived, frontier
dg -C old task export > work.json
dg -C new task import   work.json
```

That is the *only* thing accepted outside the schema, and it is narrow on
purpose: a named derived block has a known meaning and can be reproduced, which
is exactly what an unrecognised field cannot. The import says which blocks it
recomputed rather than passing over them in silence. Scoping an export
(`dg export D04`) gives a fragment whose edges reach vertices it left behind —
importable, but reported as the broken graph it is.

`dg import-md` is a different and much narrower thing: it rebuilds a store from
a `decision-graph.md` **this tool generated**, reconciling the two directions
such a document records dependencies in. It is a migration, not a markdown
parser — a decisions document of your own will not parse, and the fix is to
write the JSON or to drive `dg add` and `dg decide` from your document.

### How much each command says

`dg find` is the only reading that starts from a **word**. Every other one
starts from the frontier or from an id you already have, which is fine until
the frontier stops fitting on a screen and "was this already decided?" has no
command behind it. A bare word searches prose; `field:value` matches one stored
field; `is:name` asks a derived question and delegates to the method that
already answers it, so `is:ready` cannot disagree with `dg task`. The aside on
each row says *why* it matched. Nothing is ranked and nothing is fuzzy-matched:
an empty result means nothing in the store contains that string, which is a
fact worth trusting, and `/regex/` is there when approximation is wanted where
the reader can see it. A malformed query exits 2 and no matches exits 1, so a
script can tell "you asked wrong" from "already settled, nothing found".

The reading commands are **short by default and long on request**. `dg show`,
`dg task` and `dg context` give one line per thing — id, status, title, and what
it waits on or releases, with the prose clipped — because that output is read in
a terminal and paid for in tokens every time an agent runs it. `dg pending` and
`dg task pending` read the same way: one line per staged op, its position and
its short id, the op, what it is about, and the detail clipped. `--full`
restores the rest: the tables with nothing clipped, and for `dg context` every
premise's answer, its evidence and the falsifier that would overturn it. Nothing
is hidden, and every short view ends with the flag that expands it.

Ids are the exception to the clipping. A title may be cut and an answer reduced
to its first sentence, but an id never is — an id is what you follow up with,
and a listing you cannot follow up is not a shorter listing, it is a worse one.

A staged op is addressed by the short id `dg pending` shows beside it, or by its
position. Prefer the id whenever anything else might be writing: applying a
batch takes its ops out of the tray wherever they sit, so every position after
them shifts, and an id does not.

Work has its own graph — separate store, separate ids, so a task can never be
mistaken for a decision. Start it with or without a decision graph beside it;
`dg task init` needs nothing else, and a project that tracks only work is an
ordinary one:

```sh
dg task init
dg task add --id T02 --title "Build the HNSW index" --after T01 --because D01
dg task                                  # outstanding work, and what is startable
dg task --full                           # ...as a table, with nothing clipped
dg task node T02                         # one task in full, with its premise
dg task tree                             # the order of the work, from what can start
dg task independent                      # the ready tasks that can be worked at once, and why the rest wait
dg task start T02                        # ...pick it up
dg task park T02 --why "stuck on the upstream bug"   # put down, not given up on
                                         # ...both keep the reason; only drop releases
dg task done T02 --outcome "PR #241"
dg task drop T02 --why "the index ships with the library"
dg task amend T02 --title "Build the HNSW index over the 48M corpus"
dg task pending                          # the task tray; `dg task clear` empties it
dg task export                           # the backlog as JSON
dg task import backlog.json              # adopt a backlog prepared elsewhere
```

`--because D01` is what makes the two worth keeping together: reopening a
decision reports the unfinished work resting on it, which neither a decision log
nor a task tracker can do alone. `--evidence-for D05` points the other way — a
benchmark whose result settles a question — and `dg check` says so when it
finished and the conclusion was never recorded. See
[how it works](docs/how-it-works.md#tracking-the-work-as-well).

Decisions are **staged** first and only reach the store on `apply`, which
validates a copy and aborts without writing if the result would be invalid.

## The web app

`dg serve` gives a layered-DAG view — colour by area, outline by status, faint
edges for dependencies awaiting a decision. Click a node to inspect it, decide
it, or move a piece of work along; staged ops collect in a tray and apply
together. It shares the CLI's apply path, so there is one implementation of it.

Three views behind the tabs in the header:

| | |
|---|---|
| **decisions** | the decision DAG: decide an open question, reopen a settled one |
| **tasks** | the work, ranked by prerequisite depth. Start it, finish it with an outcome, or drop it with a reason |
| **joined** | both graphs at once, with the `because` and `evidence_for` links drawn between them — the reading neither store gives on its own |

**Every write the CLI has, bar one.** `+ new` records a question or a piece of
work — a graph you can answer but not grow is a viewer with buttons, and the
moment you have to leave to write something down is the moment prose wins.
**Edit structure** corrects premises, prerequisites, provenance and the seam,
and a removal says what it sets loose *before* it stages anything. **Reword
this record** corrects a title, an area or a note — `dg amend`'s op, the same
rules, on both panels. A
`PROVISIONAL` decision can be **re-affirmed** as well as reopened, so the
interface that manufactures the status can also clear it. A soundness chip
appears when `dg check` has anything to say and stages `dg repair` when that is
the remedy. The readings — the chain behind a decision, the path between two,
counts by area — are computed by the functions the CLI calls, and both doors
stage the same op list for the same intent, which `tests/test_doors.py` asserts.

The one exception is **removing a record**: `dg rm` and `dg task rm` stay
terminal-only, because removal erases instead of superseding, and friction is
the intended behaviour. Starting or moving a store — `init`, `import`,
`export` — is terminal-only for the obvious reason.

The two staging trays stay separate all the way through **Apply**, exactly as
`dg apply` treats them: a task batch that will not apply cannot stop a decision
batch that would.

```sh
dg serve                # blocks, as a terminal wants
dg serve --detach       # ...or start it in the background and get the URL back
dg serve --status
dg serve --stop
```

`--detach` exists so a coding-agent session can open the app without giving up
its prompt. It is idempotent — run it twice and the second run reports the
first one's URL — and `--status`/`--stop` verify the server on the port is
actually this one before believing, or signalling, anything.

Unstaged work is per-decision and survives navigation: type half an answer,
go read the premise it depends on, come back, and it is still there. The same
holds while an editor is open — the graph stays browsable, and the result is
reported against the decision it was about, not whichever one is on screen when
the editor exits. Drafts are in memory only, so a reload clears them.

### Composing in emacs from the browser

Clicking **Compose in emacs** in the panel opens the same org buffer described
below — the browser writes it, waits, and stages what emacs sends back. Anything
already typed into the form carries over, so switching editors mid-thought costs
nothing. `demo/` is a self-contained walkthrough over a graph arranged so that
every kind of record here — a reversal, a reopen, a park, a drop, evidence that
landed late — is in it somewhere:

```sh
./demo/demo.sh          # a throwaway graph on http://127.0.0.1:8765
```

Two things are worth knowing about this path:

- `$EDITOR` is **ignored** here; `$DG_GUI_EDITOR` (default `emacs`) is used
  instead. `$EDITOR` names a terminal editor by convention and the server has no
  terminal to lend it, so honouring it would hang the request. With no display
  the button is not offered at all.
- Mutating routes require a token that `dg serve` mints per run and embeds in the
  page. Any page in your browser can POST to a localhost server — it just cannot
  read the response — which was tolerable while the API only moved data around
  and is not once a route can start a process.

## Composing in emacs

A one-line prompt is a poor place to write an answer that is supposed to carry
its evidence, and it shows you nothing of what you are deciding *on top of*.
`--edit` works like `git commit`: `dg` writes an org buffer, opens your editor,
waits, and stages what comes back.

```sh
dg decide D37 --edit     # also: dg reopen --edit, dg add --edit, dg edit N
dg task done T14 --edit  # and the work: dg task add --edit
export DG_EDIT=1         # make the editor the default; --no-edit overrides
```

The buffer has two halves. `* Input` is what you fill in — answer, source,
falsifier, and checkboxes for what this decision opens. `* Context` is reference
material: the edge that led here, each premise with its own answer and
falsifier, the ancestor chain. **Only `* Input` is ever read back**, so nothing
you do to the context can change what gets staged.

Work gets its own templates, not the decision one with different labels. A
decision's buffer exists because a decision has fields you have to *argue* —
the falsifier above all. A task's exists because of one field: `dg task done
--edit` gives you `** Outcome` alone under Input, with what the work unblocks
and the decision it was for beside it as context, because an outcome written
without the question in view says what was done rather than what it showed.
`dg task add --edit` takes the whole record, with the backlog and the open
questions listed for the fields that name them. There is one buffer per
project but never one template: two stores that compose through one renderer
are two stores that can drift into each other.

`dg task drop` has no `--edit` on purpose. Its prose is a line, and the real
work of dropping is the verdict on each task it releases or orphans, which the
command asks for directly.

In emacs you also get:

| key | |
|---|---|
| `C-c C-c` | stage it and return to the shell |
| `C-c C-k` | abort — nothing is staged |
| `C-c C-o` | follow a `dg:` link to that decision (plain `org-open-at-point`) |
| `C-c d p` · `C-c d a` | jump to a premise · list every premise it rests on |
| `C-c d v` | look up any decision by id, with completion over the graph |

The last three are gated on what they actually need, and the two needs differ:
`p` and `a` resolve the vertex this buffer is composing, so they appear only
where there is one; `v` prompts over the whole graph, so it appears wherever
there is a decision store to read — including `dg add` and the task buffers,
which have no premise to walk to and are therefore where looking one up is
worth most. Each buffer's header lists what it has, checked both ways, so a key
cannot be advertised without working or bound without being named.

**They sit under `C-c d` rather than `C-c C-…` because this is an org buffer**,
and `C-c C-<letter>` is org's namespace: taking `C-c C-v` would shadow the
whole `org-babel` prefix — forty-odd commands — in a buffer whose Context can
hold source blocks. Only `C-c C-c` and `C-c C-k` shadow org, and they earn it
by making the buffer behave like the commit buffer it is modelled on.

The elisp ships with the package and is loaded by `dg` itself, so there is
nothing to install. It is strictly read-only — it can look up decisions, never
change them; staging happens in the CLI after emacs exits.

Any other editor works too: set `$DG_EDITOR` (or `$VISUAL`/`$EDITOR`) and you get
the same buffer as plain text, with the baked-in context but no navigation.

There is one buffer per project, so only one compose session can be open at a
time — the same property `COMMIT_EDITMSG` has. A second session, from the CLI
or the web app alike, is refused rather than allowed to overwrite a buffer you
are typing in; a session that crashed leaves a `.dgraph-edit.org.lock` naming
its pid, which the next compose reclaims on its own.

### Prose in answers

Answers are stored exactly as you type them, and emacs users get the whole of
org — tables, `dg:` and `file:` links, verbatim markers, source blocks. The
generated views convert on the way out: org links become markdown links, org
table rules become markdown rules, `=verbatim=` becomes backticks, and org
emphasis becomes markdown emphasis — `*bold*` renders bold, `/italic/` renders
italic, everywhere.

That last conversion is possible because the store records **provenance**:
`*single asterisks*` mean bold in org and italic in markdown — the same syntax
with two meanings — so anything composed through the editor is tagged
`format: "org"` and converted with org's meaning, while prose from the web
form, an import, or an agent stays markdown and keeps markdown's meaning,
untouched. Which door you type into is the only thing that decides, and the
stored bytes are never rewritten either way.

A task records one dialect for its whole record — its note, its outcome and its
reason for being dropped are converted through the same field — so composing an
outcome in the editor makes the record org, and the command says so when the
prose already there was typed as a flag.

## Checking it in CI

`dg check` exits nonzero on any error. For pytest, one file is enough — the
tool supplies the tests, so a project never restates the invariants and there
is no list to keep in sync:

```python
# tests/test_development_graph.py
from dgraph.testing import *  # noqa: F401,F403
```

That yields one test per rule, plus one that surfaces advisory findings — an
isolated vertex, a stale `PROVISIONAL` — through pytest's warning summary
without failing the build; the normal look of work in progress stays green. A
check added to the tool shows up in every project automatically.

## What `dg check` enforces

It checks whichever stores the project has, and the seam between them when it
has both. Every rule prints its own name, so a refusal is greppable.

**The decisions.** Well-formed unique IDs · legal statuses · no dangling edge or
`BLOCKED:` references · a `BLOCKED:` premise backed by a real edge · at most one
active edge per vertex · every `DECIDED` vertex has a date, source, falsifier and
decision edge · `OPEN`/`BLOCKED` vertices carry no answer · no `DECIDED` vertex
resting on an unsettled premise · nothing `BLOCKED` on something already settled
· acyclic. Two warnings: an orphan, and a vertex left `PROVISIONAL` once its
premises are settled again.

**The work.** The same well-formedness — ids, statuses, no dangling reference,
acyclic — plus: an edge says which kind it is · a `DONE` task has a
completion with a date and an outcome, and did not finish before its own
prerequisite · a `PARKED`
or `DROPPED` task records why · a reading records its date, note and the
decision it was read against, and is reported if the link it named has since
moved. Three warnings, each about what stopping work left behind: something
released only because a prerequisite was abandoned, something orphaned by that
abandonment, and parked work holding up something unfinished.

**The seam**, and this is what neither store can check alone. Two errors: a
`because`/`evidence_for` naming a decision that does not exist, and a cycle
across the two graphs. The rest are warnings, because *anything a reopen can
cause must never block a commit* — work resting on a premise under review, and
five ways evidence and an answer can come apart: the work finished and its
conclusion was never recorded; every task meant to inform a question was
abandoned, before or after it was settled; the work stopped without being
abandoned, before or after; and the work reported *after* the answer was
settled, which `dg confirm --against` is the honest exit from.

**The view.** `decision-graph.md` and `tasks.md` are generated, so a view that
has fallen behind its store is a warning and `dg render` rebuilds it.

## Driving it from a coding agent

A CLI only records what somebody remembers to run, and the thing that forgets is
an agent working across many sessions. The tool ships as a plugin for **Claude
Code** and **opencode**, which turns three of the four habits into mechanisms:

| | how |
|---|---|
| **read the frontier first** | the brief is injected at the start of every session, and again after a compaction — no rule in an instructions file, no "read this first" |
| **know the discipline** | the `dear-guide` skill: the model, the rules, the flag-complete commands. Loaded on demand, not carried in every context |
| **refuse the contradictions** | a `git commit` that would leave the graph invalid is denied, quoting the rule that broke and the command that fixes it. Work staged and never applied asks the human instead — `.dgraph-pending.json` is gitignored, so committing over it loses the record silently |

Plus seven slash commands, one set of files for both hosts: `/dg:brief`,
`/dg:frontier`, `/dg:tasks`, `/dg:find <query>`, `/dg:context <id>`,
`/dg:serve` and `/dg:fanout` — spelled `/dg-brief` and so on in opencode, which has no plugin
namespace to hang them under. Two matter most. `/dg:context` prints every
premise a decision or a task rests on, which is exactly what a subagent's fresh
context is missing; `/dg:find` is the only reading that starts from a word, and
so the only one that answers *was this already decided?*

### Several agents, and nobody handing out the work

`dg task` ends with a computed `ready` line, `dg task start` refuses a task
somebody already claimed, and blocked-ness is derived from the edges rather than
stored — so *read the frontier → claim → do → `dg task done` → repeat* is a loop
an agent runs with nobody in it. One agent finishing its work makes another's
startable without either of them knowing the other exists, which is the whole of
the coordination: `dg task` prints `held by <name>` for work that is claimed, so
a stalled agent can be told from a slow one, and `dg task park --why` is how an
agent that stops hands the work back rather than leaving it looking busy.

What an agent may *settle* on its own is `$DG_DECIDE`, and the argument for it is
narrower than "agents judge badly". A falsifier that is a measurement the agent
made — the benchmark ran, the number is 0.62 — writes itself; a judgement between
defensible alternatives, written by something that never had to live with the
consequence, comes out as rationalisation. Only the first is mechanically
recognisable, and `--evidence-for` already says which those are: `evidence` lets
an agent close only a decision a **finished** evidence task backs, `never` sends
every answer back to a person, and unset is the default and the widest. Refused
at stage time, before an answer and a falsifier have been composed, and never for
a caller with no `$DG_AGENT`. Like `$DG_AGENT` it is cooperative rather than a
boundary — an agent that unset it would be the supervisor — which is a rule the
launcher sets so an honest mistake is caught, not a lock.

Four further limits sit beside it, all off by default. `$DG_WRITE=launch` keeps
an agent's *writes* inside the project and `/tmp` and puts anything else to the
person; reads are never judged. It cannot be checked inside `dg` the way
`$DG_DECIDE` is, because a write never goes through this CLI — so it is checked
by `dg gate`, the same host-neutral judge the commit gate uses, which means one
rule written once is enforced under every host that relays a verdict.
`$DG_AREA=strict` stops an agent filing under an area nobody has used yet, and
sends the new area back to a person as a proposal. And `--budget 30m` records
how long an agent may run *and*, under `dg-agent run`, stops it there: that
command is the child's parent, so it parks whatever the child was holding under
the child's own name. `dg-agent expire` remains the backstop for what it cannot
see — itself being killed. A task left `DOING` by something that stopped is
indistinguishable from work in progress, and that is the failure the budget
exists to make visible.

**And `dg-agent env` is where you find out which of them is actually in force.**
All four fail *open*: a mistyped `$DG_DECIDE=nevr` is read as `open`, the widest
policy, and looks identical to a policy somebody chose — a typo does not weaken
a rule by a notch, it removes it. Failing open is right, because these are read
on the path of every judged write including the supervisor's, and a launcher's
typo must not take the graph away from the person reviewing the run. What makes
it defensible is that something reports it, and `dg-agent env --check` is that
report — it names a fallback as a fallback, shows the budget against the lease
rather than the variable, and resolves `$DG_PROJECT` to the graph it actually
found.

`$DG_TERSE` is the third, and it is about the person rather than the machine:
**the store holds the synopsis, and the development goes in a file.** A fan-out
fills a graph with prose, and whoever then has to choose between three proposals
is reading a wall of it in a panel. So a field longer than the limit is refused
at staging — the one door both trays pass through, so `dg decide`, `dg task
done` and the browser's API alike — and the refusal names the door that record
already has for a file: `--source` on a decision, the `--outcome` on a task.
There is deliberately no new field for it; a second way to name a file is a
second thing that can disagree with the first.

The sharper half is not about length at all. The premises a decision rests on,
the questions it opens, the work resting on it and the evidence against it are
all *edges*, and `dg context` computes the chain from them on demand. Prose
re-narrating any of that is a second copy of the graph in the one place nothing
can check it against the first — so the fix is usually `dg dep` or `dg task
link`, not a shorter paragraph. `dg serve` folds a field past 400 characters
behind *show all* and `dg check` warns above the same 400, which is how the rule
reaches a graph written before it existed and the records a person wrote by hand
— neither of which any launcher policy can touch. Both read that constant rather
than `$DG_TERSE`, because both are read by a supervisor, who never has the
variable set; `on` and `off` leave all three numbers agreeing, and a custom
count does not.

Neither store learns who did anything. Holdings live in `.dgraph-agents.json`,
gitignored beside the names themselves, because `tasks.json` is kept forever and
agent names are recycled the moment they are released — a name written there
would become a record that is actively wrong about who did something. Who holds
work is a fact about a run.

[`agentic/README.md`](agentic/README.md) is the procedure end to end — seeding the
graph from prose you already have, launching, reviewing one writer's proposal at a
time, and settling the empirical questions with evidence — and `/dg:fanout` puts
the state of it in front of you at launch time.

What stays a habit is **recording a decision at the moment it is made**. Nothing
a host can observe reveals that something was settled — it is a property of the
reasoning, not of a tool call — and the two obvious implementations both fail. A
turn-end prompt asking "did anything get decided?" is wrong on almost every turn
and teaches the model to say no; a nag on every commit that does not touch
`decisions.json` is wrong on most commits, and the habituation would cost the two
mechanisms that do work. So the brief arrives with ids and titles, the skill is
one hop away, and the compaction boundary asks once, at the moment prose is
actually about to be lost.

### Install

```sh
pip install -e /path/to/dear-guide         # the CLI
pip install -e '/path/to/dear-guide[tui]'  # ...and a full-screen `dg-agent setup`

# Claude Code
/plugin marketplace add /path/to/dear-guide
/plugin install dg
```

For opencode it is symlinks — see [`opencode/README.md`](opencode/README.md).
Two install steps rather than one because the hosts distribute plugins their own
way and neither of them installs Python packages; `dg --version` exists so an
adapter can tell when the two halves have drifted apart.

Nothing happens in a directory with no `decisions.json`: no output, no error, no
cost. `DG_HOOK_OFF=1` switches both mechanisms off.

[**A session, start to finish**](docs/session-walkthrough.md) walks one whole
Claude Code session — evidence arriving that falsifies a settled decision, the
reversal propagating through the work in flight, and the commit gate refusing a
half-staged diff — with the real output of every command.

### One implementation, two hosts

The interesting parts are in `dg`, not in either adapter:

- `dg brief` — what is worth knowing right now: the frontier with what each item
  waits on and what deciding it releases, anything `PROVISIONAL`, the staging
  area, the validity check. `--json` for adapters.
- `dg gate --command "<cmd>"` — is this shell command about to record a
  contradiction? Out comes `allow`, `warn`, `ask` or `deny` with a reason.

So each adapter is a few dozen lines of translation with no policy of its own,
and `skills/dear-guide/SKILL.md` is one file both hosts read — opencode uses the
same `name`/`description` frontmatter and will even read `.claude/skills/`
directly. A test asserts the adapters name no `dg` subcommand but those two, and
that the skill's command table only names commands that exist.

## Still open

- **Whether an agent should `apply` unattended.** It does, today: `apply`
  validates a copy before writing anything, the store is append-only, and the
  result is a git-tracked diff reviewed like any other — whereas an op staged
  into a gitignored file and then committed over is unrecoverable. Staging for a
  human to confirm would also mean the commit gate stopped to ask on nearly every
  commit, which is how a gate stops being read.
- **Whether two agents may work one graph.** They do, and what is still open is
  *isolation* rather than whether it can be done at all —
  [`agentic/README.md`](agentic/README.md) is the fan-out end to end, with the
  rule it rests on and the cost it carries. The honest summary of that cost is
  still *one writer at a time* inside a single checkout, and what a second writer
  can do to you is now reported rather than silent. Each staged op records what the premises it names
  looked like when it was composed, and `dg apply` says which of them moved
  while the batch waited:

  ```
  · D01 moved since this batch was staged (DECIDED → REOPENED) — op 1
    (add_edge D01) rests on it
  ✓ applied 2 op(s) → decisions.json; the view is regenerated on demand with
    `dg render`
  ```

  Never a refusal: the invariants already refuse the case that matters (a
  decided answer on a reopened premise is a blocking `propagation` finding, and
  that batch aborts naming the premise). This covers what is still *legal* after
  the ground moves and would otherwise land in silence. What holds under contention has been tested and does
  hold: the locks work across processes, the atomic writes hold, a collision is
  always a refusal and never a corrupt store, and an op refused because somebody
  else applied it says so rather than reading as "your work failed". What does
  **not** hold is isolation — the staging tray is one file per project, and
  what two agents can still do is refuse each other's guards and reason about a
  graph the other is halfway through changing. One person in two terminals, or
  a browser and a terminal, is fine and is what `commands/serve.md` describes:
  that is two writers with one intent. Two agents are two intents.

  What has stopped happening is the silent half of that. **Set `$DG_AGENT` and
  each staged op records who staged it** — and the name itself now comes from
  `dg-agent claim` rather than from whoever was launching, because every value
  that variable went wrong on was one somebody invented. A claim is checked
  against the leases *and* both trays, so two agents cannot share a name; it
  never expires; and if the 7004 ever run out, `claim` refuses and says what to
  empty instead of inventing one, `dg apply` writes yours and leaves the
  rest, and an unowned `dg apply` refuses a tray holding somebody else's work
  rather than sweeping their draft into the store — `--all` and `--mine` say
  which you meant, and `--agent <name>` names one of them. That last one is for
  the review the other two cannot express: several agents proposing
  *alternatives* into one tray, where the supervisor means to write one of them
  and turn the rest down. `dg pending` counts them under the listing so the
  names are discoverable, `dg pending --agent b` reads one proposal on its own,
  and `dg clear --agent b` is the reject verb — a bare `dg clear` takes the
  whole file whoever runs it, which is blunt once four agents share it. The one
  constraint on a name: **`unowned` is reserved**, because it is what the tool
  calls an op nobody signed, and a writer by that name made the reading and the
  write disagree about who was meant. Anything else goes. Where
  agents propose *complementary* pieces of one elaboration the union is what
  you want, and `--all` always was. That mattered most for a `close`: applied by mistake it is a
  decision, and the only way back is a `reopen` that files a reversal nobody
  made. The tray deliberately stays **one file**, so `dg brief`'s "staged and
  about to be lost" still counts everybody's; splitting it per agent would push
  every conflict from stage time — where the second answer is refused before it
  is written — to apply time, where it is refused after. Unset the variable and
  none of this exists: every op is unowned and every apply takes the tray, which
  is what a single writer has always had.

  All of that is runnable rather than asserted — [`demo-agentic/`](demo-agentic/)
  is a day's work on one graph with three agents on it, driven by the *task*
  side: the queue says what is ready, an agent picks it up, doing it produces
  evidence, and evidence settles a question. The concurrency problems arrive out
  of joining that work up rather than as the subject. `./demo-agentic/demo.sh`. Six of the seven scenes close with the tool doing
  something; the seventh is the stale premise, which no locking discipline
  reaches and which the falsifier is there for.

  One class of collision is now off the table, though, and it is worth naming
  because it was the worst-behaved: two clones of one graph both computed the
  next id as `max(stored) + 1`, so on a shared base they did not *sometimes*
  pick the same id — they picked it **every time, for every record either of
  them added**. `dg range --set 50-99` grants a clone a range of its own, and
  from then on every door allocates inside it and refuses an `--id` outside it.
  It is prevention, not correctness: what it buys is that a future integration
  report is not a rename line per record anyone wrote, which is the volume that
  trains a reader to stop reading it. Nothing fires without a grant, which is
  every single-writer project.

  **And bringing two clones together no longer needs a hand-edit.** `dg
  integrate <ref>` expresses an arriving contribution as *ops* against the
  graph you have — derived from what its writer started from, which is what
  makes a removal a removal — and replays them, collecting every conflict
  before asking anything:

  ```
  3 op(s) from worker against 32ec2d15d9 — 0 clean, 3 contested, 0 blocking

  contested — it applies, but this graph says otherwise.
    d0  D01 title differs — here 'Which index structure?', arriving 'Which index, at 48M?'
    d1  D01 was answered here too — 'IVF-PQ' against 'HNSW, M=32'
    t0  T01 was finished here too (2026-06-01, 'recall 0.91') — arriving 'recall 0.94'
  ```

  Those three are the ones only a person can settle, and they arrive together
  rather than one refusal at a time. Everything else is mechanical or is a
  refusal quoting a rule. The ops wait in `.dgraph-incoming.json` — **not the
  tray**, because the tray is what every stage-time guard consults and an
  unadjudicated op there would have this clone answering `dg node` with a
  title nobody accepted — and the commit gate denies while it is non-empty,
  since that file is gitignored and a commit over it drops the contribution
  with nothing saying it arrived.

  Each contested op is answered by ref — `dg incoming --take d1` for the
  arriving version, `--keep d1` for this store's, or `--split d1 --as D51`
  where the two answers turn out to be to two different questions worded as
  one — and `--adopt` moves the whole contribution into the trays once every
  one has an answer. It refuses
  while any is open and there is no `--force`: a flag that adopted everything
  would answer those three questions by not asking them. Taking an arriving
  answer inserts the `reopen` the store would demand of anybody, so this
  store's answer becomes history rather than being overwritten. **Keeping
  yours records theirs** as *offered and not adopted* — a third kind of edge,
  with no `why` and no `replaced_by`, because nothing was overturned. Without
  it the seam would be a choice between losing an answer and claiming the
  project once believed something it never did.

  **A removal is contested too, where this clone moved the record.** The
  arriving side deleting `D07` while you retitled it, moved its status, or hung
  a fresh question on it is two writers disagreeing about whether `D07` should
  exist — so it is reported and answered like the other three, rather than the
  removal simply landing. Add-wins, and *declared*: the alternative is
  defensible and what it replaced was remove-wins by accident, with the report
  printing `nothing contested` over an edit somebody lost. Where nobody here
  touched the record, a removal is still just a removal.

  An arriving id this store already holds is **not** one of the three: it is
  renamed inside the contribution, where an edge still knows which vertex it
  meant, and reported rather than asked about. Only ids the merge introduces
  move — an established one is cited in commits and docs this store cannot
  reach. And the report ends with the warnings the contribution introduces
  and the records it touched: several of those warnings depend on which side
  integrated first, so they are advisory and nothing should key off them, and
  a clean `dg check` afterwards is not evidence that the work arrived.

  What this does **not** change is isolation inside one clone: the tray is
  still shared and still has no notion of whose ops are whose. Two agents in
  one checkout is the same as it was. Two agents in two checkouts is now a
  mechanism rather than a hand-edit.
- **Whether the store stays per-repo.** Probably: decisions are about a codebase.
  A cross-project view would need a different addressing scheme.
- **Whether the falsifier can be checked rather than merely recorded.** A
  falsifier nobody revisits is a comment. Nothing here reads them back.
- **Whether the unrecorded-decision nag is worth trying behind a flag**, and
  whether `decision-graph.md` should eventually be org rather than markdown.

## Licence and provenance

[MIT](LICENSE). Use it, change it, ship it; it comes with no warranty of any
kind, which the licence says at more length and in capitals.

**Written by Claude** — Anthropic's coding model — working from my direction
over a long series of sessions: I set the scope and the constraints, made the
calls the design turns on, and decided what got fixed and in what order. Claude
wrote essentially all of the code, the tests and the prose, including this
sentence. Saying so seems better than letting anyone guess, and better than
implying a kind of authorship a model cannot hold: copyright vests in people, so
whatever subsists here is mine, and the licence above is how I hand it on.

The design is what it is because of that process, not in spite of it. The
comments argue for themselves at unusual length because the argument is the part
that was expensive; the invariants are strict because a model writing code
against them is exactly the reader they were built for.
