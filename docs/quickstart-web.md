# Quick start: the web interface

`dg serve` gives you both graphs laid out as DAGs, clickable, with the same
staging and apply path the CLI uses. There is one implementation of apply, so
the two cannot disagree.

## Run it

```sh
cd my-project        # any directory with a decisions.json or a tasks.json
dg serve             # http://127.0.0.1:8765
```

```
development graph → http://127.0.0.1:8765   (ctrl-c to stop)
compose in emacs: click a decision, then "Compose in emacs"
```

`--port` moves it. It binds to 127.0.0.1 only, and never answers to a `Host`
header other than its own — a page that resolved someone else's hostname to
your loopback cannot drive it.

### Without giving up your terminal

```sh
dg serve --detach    # starts it, prints the URL, returns
dg serve --status
dg serve --stop
```

`--detach` exists so that something with a prompt to get back to — a shell
script, or a coding-agent session running `/dg:serve` — can open the app at all.
It is **idempotent**: run it twice and the second run reports the first one's
URL rather than fighting for the port.

`--status` and `--stop` do not trust the run record alone. A recorded pid can
be recycled into an unrelated process, so both ask the port to identify itself
first, and `--stop` refuses to signal anything that does not answer as this
tool. A record whose server has gone is reported as stale, not silently
deleted — if you asked to stop something, you should be told there was nothing
to stop.

## The three views

The tabs in the header:

| | |
|---|---|
| **decisions** | the decision DAG — decide an open question, reopen a settled one |
| **tasks** | the work, ranked by prerequisite depth — start it, finish it, drop it |
| **joined** | both, with the links between them drawn |

**joined** is the one worth opening. It ranks decisions and tasks together over
the union of both graphs, so a task sits below the decision it exists `because`
of, and *above* the decision its outcome is `evidence_for`. The dotted cyan
edges are the links themselves — never solid, so they can never be misread as a
dependency inside either graph. Clicking anything highlights its neighbours in
both stores at once.

In a project with no `tasks.json`, the tasks and joined tabs are disabled rather
than empty: "this project does not track work" and "this project has no work
outstanding" are different facts.

If you have no graph to hand, `./demo/demo.sh` serves a throwaway six-decision,
six-task one on the same port and resets it on every run. See
[`demo/`](../demo/) for a walkthrough.

## What stays in the terminal

One thing, and it is a decision rather than a gap:

| | |
|---|---|
| **removing a record** | `dg rm`, `dg task rm` — removal erases rather than supersedes, which is the one thing this model refuses. Friction is the intended behaviour, and a ✕ is the wrong affordance for it |

Everything else the CLI can do, this page can do, and everything staged here
lands in the same tray `dg pending` reads. Starting or moving a *store* —
`dg init`, `dg import`, `dg export` — is also terminal-only for the obvious
reason: a store has to exist before there is anything to serve.

## Opening a question, and recording work

**+ new** in the header. In the decisions or tasks tab it knows which store you
mean; in **joined** it asks, because the tab has not answered that and guessing
is how work gets recorded as a decision.

A new decision takes what `dg add` takes — an id (prefilled with the next
unused one), a title, an area, a status and what it rests on. Choosing
**BLOCKED** asks which decision it is blocked on and stages that dependency as
an *edge*, because a block is a dependency and dependency is the edge list.

A new task takes what `dg task add` takes, including the two links into the
decision store: **because** — the decision this work exists for — and
**evidence for** — a decision it will inform. Both offer decisions that are
only *staged*, so a question recorded a minute ago can already be linked to.

The two relation controls are deliberately separate. **After** asserts an order
and holds the work back; **discovered during** only records where it came from
and blocks nothing. One control for both would assert an ordering nobody
claimed.

Nothing is written until Apply, as everywhere else here. Both forms stage the
same op list `dg add` and `dg task add` stage — same function, one set of rules,
checked by `tests/test_doors.py`.

## Correcting the structure

**Edit structure** on any panel. A decision offers its premises; a task offers
prerequisites, provenance, and the two links into the decision store.

Four verbs, and the chips at the top of the form choose between them: add a
relation, remove one, set a link, remove a link. Ids come from pickers over the
store, and a removal only offers what the node actually holds — there is no way
to name something that is not there.

Two rules are worth knowing because the form will refuse them:

- **A decided premise cannot be removed.** Its targets are part of the answer,
  so dropping one says the answer never opened that question. Reopen first,
  then remove, then decide again meaning it.
- **The seam is edited from the task side only.** `because` and `evidence_for`
  are fields on a task; the decision store never names work.

**A removal says what it sets loose before it stages anything.** Removing a
prerequisite can make work startable, and dropping a `because` can remove the
only thing holding it back — so the form asks first and lists what changes,
along with any new `dg check` finding the removal would introduce. Nothing is
staged by looking.

Removing a *record* — `dg rm`, `dg task rm` — is still terminal-only, and
deliberately so: it erases rather than supersedes, which is the one thing this
model refuses, so it should cost more than a click.

## A decision under review

A `PROVISIONAL` decision is one whose premise went under review. It has **two**
exits, and the panel offers both:

- **Re-affirm** — the premise settled and this answer still holds. Nothing
  about it changed, so nothing is superseded. This is the ordinary outcome, and
  it is the primary button.
- **Stage reopen** — the answer does not survive what happened. That files a
  reversal, which is the news rather than the norm.

While a premise is *still* under review the panel says which one and offers no
re-affirm button, because until that settles `PROVISIONAL` is the accurate
status and re-affirming would claim a conclusion the graph cannot support.

## Evidence that landed after the answer

Work linked with `evidence for` sometimes finishes *after* the decision it was
meant to inform is already settled. That is a legitimate way to work — but the
result may contradict the answer, and nobody reading the store six weeks later
is told to look.

The panel says so, on the decision: the result, when it finished, and what it
produced. Three things it can mean, and all three are reachable here:

- **it confirms the answer** — say what it showed and record the reading. This
  is the common one, and it stages a *task* op, because the reading is stored
  on the task; the panel says so, and the row appears in the task tray.
- **it refutes it** — reopen, below.
- **the answer never needed it** — remove the link, under **Edit structure**.

The note is required for the reason a drop's reason is: without it the entry
records that somebody clicked a button, not what they found. And a reading is
per result and per date — work that finishes *later* has not been read, so the
finding comes back rather than staying silenced.

## Is the store sound?

A chip appears in the header when `dg check` has anything to say, and does not
when it has nothing — so its appearing is the whole signal. Clicking it lists
the findings verbatim, remedies included, in two groups: what the record says
now, and — when something is staged — what `Apply` would leave behind.

Where a decision rests on a premise under review without saying so, the panel
offers to mark it. That is `dg repair`, and it is the one honesty command that
maps to a single button: it stages those marks and nothing else.

## Why a decision is where it is

**why…** beside a decision's premises walks the whole chain, not one hop, and
leads with the reading that matters: whether anything underneath it is still
unsettled. An answer resting on an unsettled premise is a bet, not a conclusion,
and that is invisible from a list of ids.

From any premise in that chain, **the chain from here…** shows the path between
the two — `dg path`, which is a single line of reasoning rather than the
neighbourhood a click highlights.

**areas** in the header gives counts by area and status, one block per store.
Never one table: the two stores share their areas and not their vocabularies.

## Reading the graph

The layout is a layered DAG — rank by longest path from a root, so a node
always sits below everything it rests on.

| | |
|---|---|
| **fill colour** | the area |
| **outline colour** | the status — green decided, red open, amber blocked, cyan provisional, violet reopened |
| **dashed outline** | `OPEN` (long dashes) or `PROVISIONAL` (fine dots); on a task, work that is waiting |
| **faint dashed edge** | a dependency whose source is not settled yet |
| **dotted cyan edge** | the link between the two graphs |

Task outlines follow their status: amber `TODO`, blue `DOING`, cyan `PARKED`,
green `DONE`, grey `DROPPED`. Blocked is never stored on a task — it is derived from the
prerequisites and the premise — so the dashes are computed, and a task whose
only obstacle is an unsettled decision gets them too.

Drag to pan, scroll to zoom. The chips in the header filter by status;
**frontier only** narrows to what is still open or blocked. Clicking a vertex
highlights it with its premises and its consequences, and dims the rest.

## Deciding

Click a vertex. The panel shows everything known about it — status, premises,
what it opens, the answer with its falsifier and source, and any superseded
answers with what overturned them.

Below that is the form:

- **Answer** — what was decided, and on what evidence.
- **Source** — a path, a script, or `discussion`.
- **Falsifier** — what evidence would reopen this. Required if the decision
  opens anything; the form refuses without it.
- **Opens** — ctrl-click to multi-select. Entries marked *(linked — stays)* are
  edges that already exist in the store; they stay selected because `apply`
  unions them back in, and a box that pretended otherwise would be lying.

**Stage decision** puts it in the tray at the bottom. Nothing has been written
yet.

A decided vertex shows a reopen form instead — *why* it is being reopened, and
a short label for the answer being superseded. Staging it also stages the
`PROVISIONAL` marks for every decided descendant, exactly as `dg reopen` does.

## Working on a task

Click a task. The panel shows its prerequisites, what it unblocks, the decision
it exists `because` of and the one it is `evidence_for` — both clickable, and
both jumping across to the other store's view if you are not in **joined**.

If the work is not startable, the panel says which of the two reasons applies
before offering any button: a prerequisite that is not finished, or a premise
that is not settled. Those are different problems and only `cross` can tell
them apart.

- **Start it** → `DOING`.
- **Mark done** needs an outcome — a path, a PR, a measurement. The form
  refuses without one, as `dg task done` does: a `DONE` task with no outcome is
  a record of nothing.
- **Park it** is the one to reach for when nobody is doing this right now but
  nobody has given up. It settles nothing downstream — everything that waited
  on the task goes on waiting.
- **Drop it** says the work is not happening, and releases what waited on it.
  That is the only difference between the two buttons.

Both need a reason, and both write the same record: a dated entry appended to
the task, never cleared. **Pick it up again** keeps it, so work stopped three
times shows all three, the last tagged *still* or *abandoned* while the status
claims it. It is the one record here that outlives the state describing it.

Parked work is offered one button, **Pick it up again**: finishing or dropping
work nobody is doing skips a step somebody has to take deliberately.

Finished and dropped work has no buttons. Reopening it is a correction, and
corrections live in `dg task` where each one can carry a sentence of
explanation.

## The trays, and Apply

Staged ops collect in the footer in **two labelled tables**, decisions and
tasks, with an ✕ on each to drop it, a ✎ to revise one in the editor, and a
**clear** per tray that says how many it is about to discard — the trays are
shared with the CLI, so some of them may not be yours.

Revising *replaces* rather than re-stages, as `dg edit` does: re-staging would
move the op to the end of the batch, and any derived status change would then
apply before the change it was derived from. **Apply** validates each batch against a
copy and only then writes.

The two stay independent all the way through, exactly as `dg apply` treats
them: a task batch that will not apply cannot stop a decision batch that would.
If one is refused you are told which, and its ops stay staged while the other's
do not.

The trays are the same `.dgraph-pending.json` and `.dgraph-task-pending.json`
the CLI uses. Stage in the browser, run `dg pending` or `dg task pending` in a
terminal, and you see the same list.

## Drafts survive navigation

Type half an answer, go read the premise it depends on, come back — it is still
there. Drafts are per-decision and in memory only, so a reload clears them
(a draft that outlived a reload is a draft you have forgotten writing).

The same holds while an editor is open: the graph stays browsable, and the
result is reported against the decision it was about, not whichever one happens
to be on screen when the editor exits.

## Composing in emacs, from the browser

Click **Compose in emacs** in the panel. The browser writes the same org buffer
`dg decide --edit` writes, opens your editor on it, waits, and stages what comes
back. Anything already typed into the form carries over, so switching editors
mid-thought costs nothing.

Three things are worth knowing:

- **`$EDITOR` is ignored here**; `$DG_GUI_EDITOR` (default `emacs`) is used
  instead. `$EDITOR` names a terminal editor by convention and the server has
  no terminal to lend it, so honouring it would hang the request. With no
  display the button is not offered at all, rather than offered and hanging.
- **One editor at a time.** There is a single buffer per project — the property
  `COMMIT_EDITMSG` has — so a second compose is refused rather than allowed to
  overwrite a buffer someone is typing in. This holds across the browser and
  the CLI alike.
- **Apply is held back** while an editor is open, since applying would move the
  graph out from under a compose that was validated against the old one.

Prose composed this way is tagged as org, so its `*bold*` and `/italic/` render
with org's meaning in the panel and in `decision-graph.md`. Prose typed into
the web form is markdown and keeps markdown's meaning.

## A note on the token

Mutating routes require a token that `dg serve` mints per run and embeds in the
page. Any page in your browser can POST to a localhost server — it just cannot
read the response — which was tolerable while the API only moved data around,
and is not once a route can start a process.

The practical consequence: **restarting `dg serve` invalidates any page left
open**. Reload it and the new token comes with it.

## Where to go next

- [How it works, and why](how-it-works.md) — the ideas behind the buttons.
- [The CLI](quickstart-cli.md) — every operation, scriptable, including the
  ones above that only it has.
- [The agent plugin](quickstart-agents.md) — for Claude Code and opencode.
