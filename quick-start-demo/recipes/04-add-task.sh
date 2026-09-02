#!/usr/bin/env bash
# q: How do I add work, and tie it to a decision?
# part: build
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  run dg task add --id T12 --area sync --title "Write the conflict-file naming rule" \
    --because D08 --after T08
  run dg apply
  run dg task
}

full() {
  fresh
  note "Three links, three different claims. --because: this work exists because of that answer."
  run dg task add --id T12 --area sync --title "Write the conflict-file naming rule" \
    --because D08 --after T08
  note "--evidence-for: this work's outcome will settle that question."
  run dg task add --id T13 --area sync --title "Measure how often two machines edit the same note" \
    --evidence-for D08
  note "--discovered-during: doing that task turned this one up. Provenance blocks nothing."
  run dg task add --id T14 --area search --title "Embedding cache is never invalidated" \
    --discovered-during T06
  run dg apply
  run dg task
  run dg task tree
  run dg task node T14
  run dg why T12
}

layer "$@"
