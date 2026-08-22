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
script, or a coding-agent session running `/serve` — can open the app at all.
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
tasks, with an ✕ on each to drop it. **Apply** validates each batch against a
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
- [The CLI](quickstart-cli.md) — the same operations, scriptable.
- [The agent plugin](quickstart-agents.md) — for Claude Code and opencode.
