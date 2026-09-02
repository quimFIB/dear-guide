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
# Three findings stand in this store on purpose, and all three are warnings,
# so this exits 0. An *error* here means the copy is broken, and stopping is
# the right thing: there is nothing worth serving.
dg check

cat <<TXT

  project : $work
  buffer  : $work/.dgraph-edit.org
  editor  : ${DG_GUI_EDITOR:-emacs}

  Open http://127.0.0.1:$port

  Seven decisions and eleven tasks from an imaginary nearest-neighbour search
  service, arranged so that every kind of record this tool keeps is in it:

    D04  the open one — red and dashed. This is the one to decide.
    D02  decided, and its first answer superseded: IVF-PQ, then HNSW
    D03  reopened — the falsifier it was written with came true
    D07  provisional: decided, then D03 went back under review
    T03  the spike D04 is waiting on;  T04 waits on D05, which is not settled
    T07  dropped, after being parked;  T10 parked;  T09 evidence that arrived
         after the answer it was meant to inform;  T11 the same, read against
         D06, and it held — the reading is on the record

  A soundness chip sits in the header, because four findings stand. Each has
  its exits in the panel, and the same ones in the terminal:

    D02 was settled on 02-14; T09 measured it on 03-02  →  read it, re-affirm
    T08 exists because of D07, which is under review    →  settle D03, confirm D07
    T10 is parked and T04 still waits on it             →  pick it up, or drop it
    D02's answer pastes the sweep it cites              →  cite it, do not copy it

  From a terminal, against the same store:

    dg -C $work brief
    dg -C $work node D02        # the reversal, kept forever
    dg -C $work context T08     # D01 → D03! → D07! → T08, across both stores
    dg -C $work find recall     # one word, both graphs

TXT
exec dg serve --port "$port"
