#!/usr/bin/env bash
# q: How do I add a question, and say what it rests on?
# part: build
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  run dg add --id D09 --area sync --title "Which files does sync ignore?" --after D08
  run dg pending
  run dg apply
  run dg tree D08
}

full() {
  fresh
  note "Nothing is written until apply. The tray is where you look before that."
  run dg add --id D09 --area sync --title "Which files does sync ignore?" --after D08
  run dg add --id D10 --area sync --title "Is the index file one of them?" --after D09,D04
  run dg pending
  run dg pending --full
  note "Position or short id both address a staged op. Drop the second question."
  run dg drop 2
  run dg drop 2 --group
  run dg pending
  run dg clear
  run dg pending
  note "Areas accumulate — but a near-miss of one in use is refused as a typo."
  run dg add --id D09 --area storge --title "Which files does sync ignore?" --after D08
  run dg add --id D09 --area sync --title "Which files does sync ignore?" --after D08
  run dg apply
  note "A question with no premise is filed, and then flagged: an unconnected decision is a smell."
  run dg add --id D10 --area ux --title "Should search results be paginated?"
  run dg apply
  run dg check
  run dg areas
}

layer "$@"
