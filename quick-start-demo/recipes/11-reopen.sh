#!/usr/bin/env bash
# q: A fact arrived that challenges a decision. What now?
# part: ask
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  run dg reopen D02 --why "a user imported 100k notes: p95 is 260 ms, which is the falsifier" -y
  run dg apply
  run dg
}

full() {
  fresh
  note "Reopen the root, and everything decided on top of it is under review at once."
  run dg reopen D01 --why "three users report the same note edited on two machines in one day; two-machine edits are the common case" -y
  run dg apply
  run dg
  run dg brief
  note "PROVISIONAL has two exits, and neither is available until the premise is settled again."
  run dg confirm D04
  run dg decide D01 \
    --answer "Still plain markdown files, one per note — plus a per-machine journal beside them for conflicts." \
    --source "issues/41, 44, 45" \
    --falsifier "the journal needs a schema migration, or a note needs more than one file" \
    --opens D02,D05,D08
  run dg apply
  run dg check
  note "Re-examined, each one still holds. Confirm says so without inventing a reversal that never happened."
  run dg confirm D02
  run dg confirm D04
  run dg confirm D05
  run dg apply
  run dg check
  run dg node D01
}

layer "$@"
