# Demo: composing a decision in emacs, from the browser

`dg serve` gives you the graph in a browser; `dg decide --edit` gives you an org
buffer in emacs. This demo connects the two: click a decision in the browser and
emacs opens on it, with the context of that decision already in the buffer.

It is the `git commit` model. The browser writes the buffer and waits. Emacs
stages the answer. Nothing is written to `decisions.json` until you press Apply.

## Run it

```sh
./demo/demo.sh          # copies the graph to /tmp/dg-demo and serves it
```

Then open <http://127.0.0.1:8765>. The demo graph is six decisions from an
imaginary training run:

```
D01 corpus ──┬── D02 tokenizer ── D04 parameter count ── D05 lr schedule
             └── D03 dedup                (OPEN)          (BLOCKED:D04)
D06 eval harness
```

`D04` is the open one — red and dashed. `D05` is blocked on it.

## The walkthrough

1. **Click D04.** The panel shows the form it always showed, plus
   **Compose in emacs**.
2. **Type half a sentence into Answer first.** It carries into the buffer, so
   switching to emacs mid-thought costs nothing.
3. **Click Compose in emacs.** A frame opens on `.dgraph-edit.org`:
   - `* Input` holds the fields. Point starts in `** Answer`.
   - `** Opens` already has `[X] D05 … (linked)`, because that edge exists. The
     box cannot lie about it: unticking it does not drop the edge.
   - `* Context` below is reference material — the incoming edge, each premise
     with its answer and falsifier, the ancestor chain as an org table. It is
     read-only, and it is not parsed either way, so mangling it cannot change
     what gets staged.
4. **Write a real answer.** Full org: tables, `=verbatim=`, `#+BEGIN_SRC`,
   footnotes, whatever you use. It is stored as typed; the browser and
   `decision-graph.md` convert it for display.
5. **`C-c C-o` on a `dg:` link** opens that decision, fetched live through
   `dg export`. Under Doom the popup takes focus; `q` comes back.
   `C-c C-p` is the parent, `C-c C-a` the ancestor chain.
6. **`C-c C-c`.** Emacs checks the required fields are filled, saves, exits. The
   browser stages the decision — *and* `D05 → OPEN`, because it was
   `BLOCKED:D04` and nothing blocks it any more. That propagation is derived,
   never typed.
7. **Press Apply.** Now `decisions.json` and `decision-graph.md` are written.

`C-c C-k` cancels. So does closing the frame without saving: the browser says
"Cancelled in the editor — nothing staged."

## What to poke at

- **Leave Source empty and press `C-c C-c`.** Emacs refuses before exiting and
  puts point on the empty field. A decision missing its evidence is the thing
  this tool exists to prevent, so it is loud in both places — Python checks
  again regardless, for editors that cannot check anything.
- **Try to edit under `* Context`.** It signals `text-read-only`.
- **Open a second browser tab and click Compose in emacs there** while the first
  is still open. It is refused: one buffer per project, the same property
  `COMMIT_EDITMSG` has.
- **Start the editor, then click other nodes while it is open.** The graph stays
  browsable; D04's button reads "waiting for emacs", every other node says an
  editor is open for D04, and Apply is held back. When you `C-c C-c`, the
  confirmation is waiting on D04 however far you wandered.
- **Type half an answer and navigate away.** It is still there when you return.
  Cancelling in emacs keeps it too.
- **Reopen a decided one.** Click D02, then Compose in emacs. The buffer leads
  with what reopening drags into `PROVISIONAL` — D04 here — because that set is
  the reason to interrupt someone.

## Notes

- The work directory is `/tmp/dg-demo` (`$DG_DEMO_DIR` to move it). Re-running
  the script resets the graph, so experiment freely.
- `$DG_GUI_EDITOR` picks a different windowed editor. `$EDITOR` is deliberately
  ignored here: it names a terminal editor, and the server has no terminal to
  give it. Without emacs you lose the links, the navigation and the pre-flight
  check, but the buffer still round-trips.
- With no `$DISPLAY` the button is not offered at all, rather than offered and
  hanging.
