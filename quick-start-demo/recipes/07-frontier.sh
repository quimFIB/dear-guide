#!/usr/bin/env bash
# q: What can be decided right now?
# part: ask
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  run dg
}

full() {
  fresh
  note "The same frontier, three ways: the table, the ids for a pipe, and the whole tree."
  run dg show --full
  run dg find is:decidable --ids
  run dg tree
  note "And what an agent is handed at the start of a session: the frontier, plus what rests on a premise under review, plus what is staged."
  run dg brief
}

layer "$@"
