# Composing in an editor

A one-line prompt is a poor place to write an answer that is supposed to carry
its evidence. This page is the editor path end to end: the org buffer and its
two halves, the emacs keys, the same buffer opened from the browser, and what
happens to org markup on the way into a generated view.

## From the terminal

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

Any other editor works too: set `$DG_EDITOR` (or `$VISUAL`/`$EDITOR`) and you
get the same buffer **as markdown** — `.dgraph-edit.md`, with `# Input` and
`## Answer` for the headings, `<!-- -->` around the guidance, the metadata as
front matter, and the same baked-in context — but no navigation. Org is what
emacs makes pleasant, and outside emacs it is a file whose headings and
checkboxes nothing understands; so the buffer's dialect follows the editor,
and so does the prose's tag: what is typed in the markdown buffer is stored
untagged, which is what markdown is in the store, while the org buffer's prose
is tagged `org` so the views convert its emphasis. `$DG_EDIT_FORMAT=org` or
`=markdown` overrides the rule, for an editor that speaks the other dialect.

There is one buffer per project, so only one compose session can be open at a
time — the same property `COMMIT_EDITMSG` has. A second session, from the CLI
or the web app alike, is refused rather than allowed to overwrite a buffer you
are typing in; a session that crashed leaves a `.dgraph-edit.lock` naming
its pid, which the next compose reclaims on its own. One lock for both
dialects, since there is one buffer however it is written.

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
form, an import, an agent, or the markdown buffer stays markdown and keeps
markdown's meaning. Which door you type into is the only thing that decides.

One tag covers a whole record — a decision's note and its rule, a task's note,
outcome, criterion and every reason it stopped — and the rule for it is **the
tag follows the last writer, and the rest follows the tag**. Write an outcome
as markdown on a task whose note was composed in org and the note is
converted to markdown beside it, and the record is untagged; compose an org
outcome on a task whose note came from the web form and the note becomes org.
The conversion is the one the views already apply, in either direction
(`orgmd.convert`), so nothing you see changes — what changes is that the tag
is true of every field under it, where before it could be true of only one.
The same rule shows a record in the buffer you open it in: an op composed in
emacs and revised in vim arrives as markdown, and goes back as markdown.


## From the browser

Clicking **Compose in emacs** in the panel opens the same org buffer described
above — the browser writes it, waits, and fills the form with what emacs sends
back; the form's own button stages. Anything already typed into the form
carries over, so switching editors mid-thought costs nothing. Every button
answers a `--edit` on the CLI side — the decision forms, the new-task and
Reword forms, and the task panel's three boxes — Outcome, and the reasons a
task is parked or dropped, which are `dg task done`, `park` and `drop` with
`--edit` from the browser — and a field gets the editor on both surfaces or on
neither. [`demo/`](../demo/) is a self-contained walkthrough over a graph
arranged so that every kind of record here — a reversal, a reopen, a park, a
drop, evidence that landed late — is in it somewhere, and this button is one of
the things it shows:

```sh
./demo/demo.sh          # a throwaway graph on http://127.0.0.1:8765
```

Two things are worth knowing about this path:

- The editor is the one `dg` is configured with — `$DG_EDITOR`, `$VISUAL`,
  `$EDITOR`, then `emacs` — with two variables in front that exist only for
  this door: `$DG_EDIT_CMD` (an exact command, `{file}` substituted) and
  `$DG_GUI_EDITOR` (an editor promised to draw its own window). A terminal
  editor such as `vim` is opened in a terminal window that blocks until it
  exits; `$DG_TERMINAL` names which (e.g. `xfce4-terminal --disable-server
  -x`), else `$TERMINAL`, else the first of the usual ones on `PATH`. `emacs
  -nw` becomes plain `emacs`, which draws its own window. Where none of this
  can work — no display, no terminal emulator, an editor not on `PATH` — the
  button is withheld and the panel says why, naming the variable to set.
- Mutating routes require a token that `dg serve` mints per run and embeds in the
  page. Any page in your browser can POST to a localhost server — it just cannot
  read the response — which was tolerable while the API only moved data around
  and is not once a route can start a process.

The rest of what the browser does around a compose — one editor at a time
across both doors, and Apply held back while one is open — is in
[the web quick start](quickstart-web.md#composing-in-emacs-from-the-browser).


## Where to go next

- [The CLI quick start](quickstart-cli.md) — where `--edit` first appears.
- [The web interface](quickstart-web.md) — the panel the **Compose in emacs**
  button sits in.
- [Reference](reference.md) — every command, and what `dg check` enforces.
