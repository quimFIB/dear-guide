# Quick start: the web interface

`dg serve` gives you the graph laid out as a DAG, clickable, with the same
staging and apply path the CLI uses. There is one implementation of apply, so
the two cannot disagree.

## Run it

```sh
cd my-project        # any directory with a decisions.json
dg serve             # http://127.0.0.1:8765
```

```
decision graph → http://127.0.0.1:8765   (ctrl-c to stop)
compose in emacs: click a decision, then "Compose in emacs"
```

`--port` moves it. It binds to 127.0.0.1 only, and never answers to a `Host`
header other than its own — a page that resolved someone else's hostname to
your loopback cannot drive it.

If you have no graph to hand, `./demo/demo.sh` serves a throwaway six-decision
one on the same port and resets it on every run. See [`demo/`](../demo/) for a
walkthrough.

## Reading the graph

The layout is a layered DAG — rank by longest path from a root, so a decision
always sits below everything it rests on.

| | |
|---|---|
| **fill colour** | the area |
| **outline colour** | the status — green decided, red open, amber blocked, cyan provisional, violet reopened |
| **dashed outline** | `OPEN` (long dashes) or `PROVISIONAL` (fine dots) |
| **faint dashed edge** | a dependency whose source is not settled yet |

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

## The tray, and Apply

Staged ops collect in the footer, with an ✕ on each to drop it. **Apply**
validates the whole batch against a copy and only then writes `decisions.json`
and `decision-graph.md`. If the batch would leave the graph invalid, nothing is
written and the reason appears above the tray.

The tray is the same `.dgraph-pending.json` the CLI uses. Stage in the browser,
run `dg pending` in a terminal, and you see the same list.

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
