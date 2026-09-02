#!/usr/bin/env bash
# q: How do I remove a record made in error?
# part: build
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  run dg task add --id T12 --area storage --title "Check the index for corruption when opening" --after T01
  run dg apply
  run git commit -qam "file T12"
  run dg task rm T12 --into T04 -y
  run dg apply
  run dg task tree
}

full() {
  fresh
  note "Removal keeps nothing, so it needs git to have the record instead."
  run dg add --id D09 --area sync --title "Which files does sync ignore?" --after D08
  run dg apply
  run dg rm D09 -y
  run git commit -qam "file D09"
  run dg rm D09 -y
  run dg pending
  run dg apply
  note "Three shapes: sever (the default), splice, and into."
  run dg add --id D09 --area storage --title "Is corruption detected on open or on query?" --after D04
  run dg add --id D10 --area storage --title "What does the rebuild command print?" --after D09
  run dg apply
  run git commit -qam "file D09, D10"
  run dg tree D04
  run dg rm D09 --splice -y
  run dg apply
  run dg tree D04
  note "A decision that work names cannot go until the work points elsewhere."
  run dg rm D06 -y
  note "And a duplicate task folds into the one it duplicates, edge kind by edge kind."
  run dg task add --id T12 --area storage --title "Check the index for corruption when opening" --after T01
  run dg apply
  run git commit -qam "file T12"
  run dg task rm T12 --into T04 -y
  run dg apply
  run dg check
}

layer "$@"
