#!/usr/bin/env bash
# q: How do I link things that already exist?
# part: build
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  run dg dep D08 --after D04
  run dg task link T11 --because D05
  run dg apply
  run dg why T11
}

full() {
  fresh
  note "Decisions: a premise added later. D04 is decided, and adding a target to its answer is allowed."
  run dg dep D08 --after D04
  run dg apply
  run dg node D04
  note "And undone the same way. The answer's targets follow the graph."
  run dg undep D08 --after D04
  run dg apply
  note "Tasks: a premise, a prerequisite, a provenance — each after the fact."
  run dg task link T11 --because D05
  run dg task dep T11 --after T07
  run dg task dep T05 --discovered-during T02
  run dg apply
  run dg task node T11
  run dg task node T05
  note "Undoing a prerequisite releases the task if that was all it waited on."
  run dg task undep T09 --after T04
  run dg task unlink T11 --because D05
  run dg apply
  run dg task
}

layer "$@"
