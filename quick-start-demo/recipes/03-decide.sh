#!/usr/bin/env bash
# q: How do I settle a question?
# part: build
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  run dg decide D08 \
    --answer "The notes folder is synced by whatever the user already uses; notelit never syncs." \
    --source "discussion" \
    --falsifier "a user asks for notelit to resolve a conflict it did not cause"
  run dg apply
  run dg node D08
}

full() {
  fresh
  note "An answer that opens further questions must name them, and needs a falsifier."
  run dg add --id D09 --area storage --title "How is a rebuild reported to the user?" --after D06
  run dg apply
  run dg decide D06 \
    --answer "Refuse to open it. Print the rebuild command and exit 3." \
    --source "discussion" \
    --opens D09
  run dg decide D06 \
    --answer "Refuse to open it. Print the rebuild command and exit 3." \
    --source "discussion" \
    --falsifier "a corrupted index is seen in the wild more than once a month" \
    --opens D09
  run dg apply
  run dg
  note "The note above is the other order: an answer first, its evidence later. D04 was settled in June; measure it now."
  run dg task add --id T12 --area storage --title "Measure index.db size on the 50k folder" --evidence-for D04
  run dg apply
  run dg task start T12
  run dg task done T12 --outcome "bench/index-size.md — 38 MB beside 400 MB of notes"
  run dg apply
  run dg check
  run dg confirm D04 --against T12 --note "Under a tenth of the notes themselves; beside them is fine."
  run dg apply
  run dg check
  run dg node D04
}

layer "$@"
