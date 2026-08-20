#!/usr/bin/env bash
# Start the demo: a throwaway copy of demo/decisions.json and demo/tasks.json,
# served locally.
#
# The graph is copied to a work directory rather than served in place, so the
# demo can be run repeatedly and anything staged or applied is discarded on the
# next run. Nothing outside the work directory is touched, and the work
# directory is only ever added to — no recursive delete on a computed path.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
work=${DG_DEMO_DIR:-${TMPDIR:-/tmp}/dg-demo}
port=${DG_DEMO_PORT:-8765}

mkdir -p "$work"
command cp -f "$here/decisions.json" "$work/decisions.json"
command cp -f "$here/tasks.json"     "$work/tasks.json"
rm -f "$work/.dgraph-pending.json" "$work/.dgraph-task-pending.json" \
      "$work/.dgraph-edit.org" "$work/.dgraph-serve.json"

cd "$work"
dg render >/dev/null
dg task render >/dev/null
dg check

cat <<TXT

  project : $work
  buffer  : $work/.dgraph-edit.org
  editor  : ${DG_GUI_EDITOR:-emacs}

  Open http://127.0.0.1:$port and click D04 (the red, dashed node).
  Then "Compose in emacs":

    - emacs opens on an org buffer with D04's context already in it
    - C-c C-o on a dg: link jumps to that decision, fetched live
    - fill in Answer / Source / Falsifier, tick what D04 opens
    - C-c C-c stages it and the browser updates; C-c C-k cancels
    - then press Apply in the browser

  Or try the other two tabs:

    - "tasks"  : T04 is dashed because its premise D05 is not settled.
                 T06 is startable. Mark it done with an outcome.
    - "joined" : the whole chain across both stores, D06 -> T05 -> T03 ->
                 D04 -> D05 -> T04. The dotted cyan edges are the links.

  From a terminal, the same reading as text:

    dg -C $work context T04

TXT
exec dg serve --port "$port"
