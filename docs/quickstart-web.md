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

If you have no graph to hand, `./demo/demo.sh` serves a throwaway seven-decision,
ten-task one on the same port and resets it on every run — a reversal, a reopen,
a park, a drop and a late result among them, so every panel here has something
to show. See [`demo/`](../demo/) for a walkthrough.

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
unused one), a title, an area, a status and what it rests on. There is no
"blocked" to choose: a vertex waits on whichever of the decisions it rests on
is unsettled, because a block is a dependency and dependency is the edge list.

A new task takes what `dg task add` takes, including the two links into the
decision store: **because** — the decisions (plural) this work exists for — and
**evidence for** — the one decision it will inform. A task rests on several
premises at once, so linking adds one to the set; `evidence for` is a single
slot, so linking a task to a second of that kind is refused with a
suggestion to unlink the first; work that bears on several is kept as sibling
tasks. Both offer decisions that are
only *staged*, so a question recorded a minute ago can already be linked to.

The two relation controls are deliberately separate. **After** asserts an order
and holds the work back; **discovered during** only records where it came from
and blocks nothing. One control for both would assert an ordering nobody
claimed. They can even coexist between the same pair, pointing opposite ways —
doing `A` turned up `B` (**discovered during**), and `B` may or may not then
block `A` (**after**): the discovery is recorded regardless, and only the
ordering says whether the found work must wait for the work that found it or is
independent of it.

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

One rule the form refuses, and one that changed:

- **Removing a decided premise says what it moves.** It used to be refused —
  its targets were held to be part of the answer — until that was settled the
  other way: they are the graph's, and the answer is the payload beside them.
  The edit lands and names the `opens` it changes.
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

The page opens as canvas alone. The inspector on the right exists only
while it has something to show — it opens when you click a node, start a
form or ask for a reading, and closes when that is cleared, because an empty
inspector is a third of the window saying "click a node". The trays start
folded too: the **tray** chip in the header opens them, remembers a toggle
per browser, and while they are folded carries the staged count — so
staging from the panel never yanks the footer open, and nothing staged goes
unannounced. **Refresh** re-reads the stores and keeps all of this as it
was.

Selecting a node dims everything it is not related to. The **× inspector**
chip in the header, or Escape outside a field, clears the selection, closes
the inspector and lights the whole graph again, the way it opens. A click
on empty canvas does not — the canvas is grabbed to pan, and a pan that
started with a still hand should not throw the reading away. Find, focus
and a preview are their own controls and keep their own clears.

| | |
|---|---|
| **fill colour** | the area |
| **outline colour** | the status — green decided, red open, amber blocked, cyan provisional, violet reopened |
| **dashed outline** | `OPEN` (long dashes) or `PROVISIONAL` (fine dots); on a task, work that is waiting |
| **faint dashed edge** | a dependency whose source is not settled yet |
| **dotted cyan edge** | the link between the two graphs |
| **chevrons along an edge** | its direction: towards the record that rests on, follows, or is evidence for the one the edge leaves |

Edges can be read one at a time. Hovering one says what relation it is —
"D40 rests on D36" with the first line of the answer, "T01 precedes T02",
"T03 is work because of D05" — and clicking it selects the edge: it and its
two ends stay lit, everything else dims, and the inspector opens the record
the answer lives on — the source of a dependency, the task of a link between
the stores — scrolled to that answer's card. A vertex resting on four
premises has four lines into it, and each can be asked which it is. A node
click, the **× inspector** chip or Escape clears the edge selection.

Task outlines follow their status: amber `TODO`, blue `DOING`, cyan `PARKED`,
green `DONE`, grey `DROPPED`. Blocked is never stored on a task — it is derived from the
prerequisites and the premise — so the dashes are computed, and a task whose
only obstacle is an unsettled decision gets them too.

Drag to pan, scroll to zoom. The chips in the header filter by status;
**frontier only** narrows to what is still open or blocked. Clicking a vertex
highlights it with its premises and its consequences, and dims the rest.

### Focus: the second filter, which removes

**⌖ focus** asks a different question, and what it does depends on what is
in front of it. With a node selected, it puts a chip beside the find box
reading `subgraph:1 id:D04`, and the canvas keeps only that node's
neighbourhood — its premises, its dependents and the work resting on it, one
hop out. Everything else is *gone*, not dimmed, and the slice is re-ranked so
it fills the screen. The button stays lit while the slice is up, and the
clicks after it open records inside the slice without moving it, which is how
you read what you just cut out. Pressing it again drops the slice, the same as
the chip's `×`; to seed from another node, drop the slice, click the node, and
press again.

With nothing selected and a query in the find box, it promotes the query
instead: the chip reads `subgraph:0 is:unsettled or is:outstanding`, the
canvas keeps exactly the nodes the box matched and nothing else, and the box
keeps its text. Zero hops because you typed a set, not a node to grow from;
edit the number in the chip if you want what the set rests on too. So
**frontier only** followed by **⌖ focus** draws the frontier on its own, and
the press after that puts the whole graph back with the frontier lit over it.
With neither a node nor a query, the button does nothing.

The two filters answer different questions and so they compose rather than
compete. The find box dims: everything stays where it was and the matches light
up, which answers *where do these sit in the whole?* The chip removes: the
slice becomes the graph, which answers *what does this piece look like on its
own?* Type in the box while a chip is up and you are filtering **within** the
focus.

The chip is text, and it is the page's spelling of two `dg find` flags:

| chip | shell |
|---|---|
| `subgraph:1 id:D04` | `dg find --subgraph --hops 1 'id:D04'` |
| `subgraph:* id:D04` | `dg find --subgraph 'id:D04'` |
| `subgraph:0 area:consent` | `dg find --subgraph --hops 0 'area:consent'` |

So the seed is any query, not just one node: `id:D04 or id:D12` focuses both,
`area:consent` focuses an area. Edit the chip by hand, or press `×` to drop it.
The hop count is yours and survives the next click; only the seed is replaced.
A click starts at one hop rather than `*` because `*` is the whole connected
cone in every direction, which on a connected graph is the whole graph — a
focus you could not see happen. Type `*` when you do want the cone.

`subgraph:` is **not** a term in the query language — there is no `subgraph`
field and `dg find` would refuse one. It is how the chip writes down a flag, and
the page strips it before the query is sent.

**A node ringed in dashes was reached through the other store.** The slice spans
both graphs, so focusing a decision pulls in the work resting on it; on the
decisions tab that work is not drawn, and a decision reached *through* it would
otherwise sit there with no edge, reading as a node the layout forgot. The ring
says so, and hovering names the hop — *D01 is here through the task store: T01
is evidence for D11, and D01 follows from D11*. The joined tab draws those
links as lines, so nothing is ringed there. When every node on a tab is
bridged, the rings go and the note beside the chip says it once instead: a mark
earns its place by being on some nodes and not others.

## Deciding

Click a vertex. The panel shows everything known about it — status, premises,
what it opens, the answer with its falsifier and source (and its probe, where
the answer carries one), any superseded answers with what overturned them,
and for an open question its rule for settling, typed or in prose, and what
it is bound to. A task's panel likewise shows its definition of done. The
trays below read a `reprobe` as its criterion and a `bind` as the pairs it
adds.

Any single field longer than about 400 characters is **folded** behind
*show all · N chars*, and clicking it opens and closes the field. That is a
reading aid, not a limit: nothing is unreachable and nothing is edited. It
exists because a panel is where somebody chooses between proposals, and one
record carrying its development pushes the falsifier, the evidence and the
buttons off the screen. The convention it goes with is that the store holds the
synopsis and the development goes in a file the record cites — `dg check` says
so about a record that outgrew it, and under a fan-out `$DG_TERSE` can make it
a refusal. `agentic/README.md` has the reasoning.

Below that is the form:

- **Answer** — what was decided, and on what evidence.
- **Source** — a path, a script, or `discussion`.
- **Falsifier** — what evidence would reopen this. Required if the decision
  opens anything; the form refuses without it.
- **Opens** — the questions this answer raises. What the vertex already
  opens is said as text: those edges are in the store and `apply` keeps
  them. Adding one is behind **+ opens another question…**, since it is the
  rarer case — a question normally comes to exist through **+ new** resting
  on this one, edge included. Behind the button is a filter over a
  checklist: type an id or a word of a title, tick what applies, and the
  line under the list says what is picked. The vertex itself and anything
  above it are not offered: opening an ancestor would be a cycle, which
  `dg check` refuses, so the picker does not ask. The same picker, with the
  cycle rule the other way round, is the "rests on" of a new question, the
  relations of a new task, and every list in *Edit structure*.

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
  a record of nothing. The box takes more than one line, and **Compose in
  emacs** beside it opens the buffer `dg task done --edit` opens — the outcome
  field with what the task unblocks and the decision it was for beside it —
  and fills the box with what you wrote; **Mark done** is still the door. A
  field you can compose at the terminal you can compose here: the pair is the
  unit, so neither surface gets the editor for a field without the other.
- **Park it** is the one to reach for when nobody is doing this right now but
  nobody has given up. It settles nothing downstream — everything that waited
  on the task goes on waiting.
- **Drop it** says the work is not happening, and releases what waited on it.
  That is the only difference between the two buttons.
- Each reason has its own **Compose in emacs**, as Outcome does: `dg task park
  --edit` and `dg task drop --edit` from the browser, filling the box for the
  button to stage. The verdicts a drop asks about other work are not in the
  buffer; they are asked after **Drop it**, as they are flags at the terminal.

**Reword this record**, folded shut under the buttons, corrects the title, the
area or the note — `dg amend`'s op, through the same rules. Blank fields are
left alone, so only what you type is written. It reaches nothing that carries a
claim: an answer, an outcome and a reason work stopped are dated records, and
those are superseded by a new one rather than edited.

Both need a reason, and both write the same record: a dated entry appended to
the task, never cleared. **Pick it up again** keeps it, so work stopped three
times shows all three, the last tagged *still* or *abandoned* while the status
claims it.

**Mark done** writes the same kind of record. Each outcome is appended with its
date and the last is tagged *the result* while the status claims one, so work
finished, picked back up and finished again shows both — the first result is
not overwritten by the second, and neither is deleted by a restart. These two
lists are what outlive the state describing them; every other field on the
panel is current-state.

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

Ops that one command staged together — an add and the edges that attach it,
a reopen and the statuses it propagates — are **one act**, drawn as one block
with a rule down its left edge and a single set of controls: the ✓ applies
the whole act and leaves the rest staged, and the ✕ drops all of it, because
dropping one member would leave the others to apply as something nobody
proposed. `dg pending` draws the same block as a rail down the rows.

What a tray action did — an act applied, everything applied, an act dropped,
a tray discarded, an op revised — is said in **one line above the trays**,
not in the inspector, so the record you were reading stays open and the
sentence is there whether the trays are folded or not. It stays until the
next tray action says something else or you dismiss it; nothing here fades on
a timer, because that line is also where Apply reports what moved under a
batch while it was staged, and that is a list to be read, not glimpsed. A
refusal is different: it is about rows still in the tray, so it is said
beside them.

### Previewing on the canvas

The canvas draws the store, and nothing staged moves it — that is deliberate,
so a pan, a zoom and an open inspector are never pulled from under you. But
you can ask: the **◎** on an act's row draws the graph as it would be with
that act applied, and **preview all** in the tray heading does the same for
everything staged — narrowed to one writer if the tray is, in which case
that writer's ops may rest on another writer's act, and it is drawn the way
an act's prerequisite is, below. What the act
would add is a **ghost** — a glowing dashed box, a dashed blue edge — what it
would remove is **struck**, and what it would move glows with `was → now` in
place of its status. The change and one hop around it are lit and the rest
of the graph is dimmed, the way the find box dims what does not match, so a
two-box change is not lost among fifty. A bar over the canvas says what is
being previewed, and the open inspector reads the previewed record with a
line saying so.

An act may name a record an earlier act adds — an edge from a vertex still
in the tray — because staging vets each op against the store *plus what is
before it*. Previewing such an act draws the act it rests on first, as
**assumed**: a ghost without the glow, dimmer, so it reads as context and
not as this act's change, and the bar names it. The ✓ on such an act is
refused by that name — "rests on act ckqz, take it first or apply
everything" — never quietly widened to take the prerequisite too, and the
bar says so while the preview is on.

The other way round: dropping an act that a later act rests on is never
refused — a writer may withdraw a proposal somebody else built on — but it
is said. The tray names the act it stranded, and that act's rows carry
*will not apply — names D90, which nothing staged or stored adds* until it
is dropped or what it names is staged again. `dg drop` and `dg pending` say
the same.

**Clear preview** in that bar, or the ◉ on the row that turned it on, draws
the store again; pan, zoom and the inspector are kept. A preview also ends by
itself when its act leaves the tray — applied with the ✓, or dropped — and
the bar says why. The store is never written by a preview, and `/api/graph`
still answers the store: previewing is a request, not a mode the page
falls into.

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

- **It is your editor.** The button runs what `dg` is configured with —
  `$DG_EDITOR`, `$VISUAL`, `$EDITOR`, then `emacs` — and is labelled with its
  name. A terminal editor such as `vim` is opened in a terminal window that
  blocks until it exits (`$DG_TERMINAL` says which, else `$TERMINAL`, else the
  first usual one on `PATH`); `emacs -nw` draws its own window instead. Any
  editor but emacs gets the buffer as markdown, and its prose is stored as
  markdown, as [the editor page](emacs.md) explains. Two
  variables exist only for this door and win over the rest: `$DG_EDIT_CMD`, an
  exact command with `{file}` substituted, and `$DG_GUI_EDITOR`, an editor you
  promise draws a window. Where none of it can work — no display, no terminal
  emulator, an editor not on `PATH` — the button is withheld rather than
  offered and hanging, and the panel says why, naming the variable to set.
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
- [Composing in an editor](emacs.md) — the buffer behind the **Compose in
  emacs** button, and its keys.
