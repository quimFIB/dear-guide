#!/usr/bin/env bash
# q: How do I find something by what it says?
# part: ask
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  run dg find corrupt
  run dg find is:ready
}

full() {
  fresh
  note "Walk the graph: everything still open below D02."
  run dg find 'under:D02 is:unsettled'
  note "A superseded answer is still searchable — a reversal's reasoning is often the only place a rejected approach is written down."
  run dg find 'per notebook'
  run dg find 'per notebook' --active
  note "Fields, negation, alternation."
  run dg find 'status:PARKED or status:DROPPED'
  run dg find 'area:storage -status:DONE' --tasks
  run dg find 'falsifier:"200 ms"'
  note "Ids alone, for a pipe."
  run dg find 'is:decidable' --ids
  note "Exit 1 is a fact worth trusting: nothing in the store says that. Exit 2 is a question the tool could not answer as asked."
  run dg find zebra
  run dg find 'is:nonsense'
}

layer "$@"
