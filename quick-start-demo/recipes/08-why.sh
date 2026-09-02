#!/usr/bin/env bash
# q: Why was this decided — and where does this work come from?
# part: ask
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  run dg why D06
  run dg why T09
}

full() {
  fresh
  note "--full prints every premise's answer, its evidence and its falsifier: the form to paste into a prompt."
  run dg why D06 --full
  note "One decision in full, reversals included. D02 was answered twice."
  run dg node D02
  note "The chain of evidence between two decisions."
  run dg path D01 D07
  note "Work that rests on a question nobody has answered yet."
  run dg why T07
  run dg why T09 --full
}

layer "$@"
