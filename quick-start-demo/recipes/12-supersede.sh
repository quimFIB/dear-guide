#!/usr/bin/env bash
# q: How do I change an answer — and what happens to what rested on it?
# part: ask
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  run dg reopen D02 --why "a user imported 100k notes: p95 is 260 ms, which is the falsifier" -y
  run dg apply
  run dg decide D02 \
    --answer "SQLite FTS5 with a trigram tokenizer, sharded into one table per 20k notes." \
    --summary "FTS5, trigram, sharded" \
    --source "bench/fts5-shards.md" \
    --falsifier "p95 over 200 ms at 500k notes" \
    --opens D03,D04
  run dg apply
  run dg check
  run dg confirm D04
  run dg apply
  run dg node D02
}

full() {
  fresh
  note "D04's falsifier was written in June. It just came true."
  run dg node D04
  run dg reopen D04 --why "three users who sync the folder report index.db conflict copies every day — the falsifier" -y
  run dg apply
  run dg
  run dg check
  note "The old answer is superseded, not deleted: it is still searchable, and its reasoning with it."
  run dg find 'beside the notes'
  run dg decide D04 \
    --answer "In the user's cache directory, keyed by a hash of the notes folder's path. Never inside the synced folder." \
    --summary "cache dir, keyed by path hash" \
    --source "issues/52" \
    --falsifier "a user needs the index to travel with the notes, as on a USB stick" \
    --opens D06
  run dg apply
  run dg check
  run dg node D04
  note "What rests on it reads the new answer from now on."
  run dg why D06 --full
}

layer "$@"
