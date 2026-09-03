#!/usr/bin/env bash
# q: What does a reader show while something is still in the tray?
# part: annex
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  run dg add --id D09 --area sync --title "Which edit wins when two machines change one note?" --after D08
  note "Nothing applied. Every reader shows the store plus the tray, and says which rows are only proposed."
  run dg tree D08
  run dg node D09
  run dg show
}

full() {
  fresh
  run dg add --id D09 --area sync --title "Which edit wins when two machines change one note?" --after D08
  note "A chain that runs through a staged edge is found, and the hop is marked."
  run dg path D01 D09
  note "One reader ignores the tray on purpose: export is what import reads back, so a proposal must never arrive elsewhere as a stored fact."
  run dg export D09
  note "The check is store-only too, and --staged is how you ask what the tray would leave: what it fixes, what it introduces."
  run dg check
  run dg check --staged
  note "The tray itself, and the store it would produce, are two different things a reader can tell apart."
  run dg pending
  run dg apply
  run dg tree D08
}

layer "$@"
