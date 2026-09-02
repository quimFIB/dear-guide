#!/usr/bin/env bash
# q: What is ready to work on — and how do I move it along?
# part: ask
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  run dg task
  run dg task start T04
  run dg apply
  run dg task done T04 --outcome "src/index/open.py — a checksum in the header, PR #40"
  run dg apply
  run dg task
}

full() {
  fresh
  run dg task tree
  note "Two ways to stop. Parking keeps everything downstream waiting."
  run dg task park T08 --why "no second machine to test on until the laptop is back"
  run dg apply
  run dg task
  run dg task start T08
  run dg apply
  note "Dropping releases what waited — and asks for a verdict on each piece, now, while the reason is in mind."
  run dg task drop T06 --why "the hosting budget fixes the model; comparing three is moot"
  run dg task drop T06 --why "the hosting budget fixes the model; comparing three is moot" --keep T07
  run dg apply
  run dg check
  run dg task start T07
  run dg apply
  run dg task node T06
  run dg task node T08
}

layer "$@"
