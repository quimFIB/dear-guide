#!/usr/bin/env bash
# q: Several agents, one graph — how is that kept safe?
# part: agents
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  capture a dg-agent claim
  capture b dg-agent claim
  as "$a" dg task start T04
  as "$a" dg apply --mine
  as "$b" dg add --id D09 --area sync --title "Which files does sync ignore?" --after D08
  as "$b" dg task add --id T12 --area sync --title "List the files sync must ignore" --because D08 --after T08
  run dg pending
  run dg apply
  run dg pending --agent "$b"
  run dg apply --agent "$b"
  run dg-agent list
}

full() {
  fresh
  note "A remit is one word. Everything else in the prompt comes from the graph."
  run dg-agent presets
  run dg-agent setup --preset contributor --focus D06 --agents 2 --budget 20m \
    --brief "Settle what happens when the index is corrupted, on evidence rather than opinion." \
    --read "docs/index-format.md:how the index file is laid out" \
    --findings "findings/<task-id>.md"
  run ls fanout
  run sed -n 1,40p fanout/scout.md
  run cat fanout/env.json
  run cat fanout/launch.sh
  run dg-agent env --check --plan fanout/env.json
  note "The other flow: the session spawns the agents itself. Same prompt; the rules become advisory, and setup says which."
  run_head 22 dg-agent setup --mode session --preset contributor --focus D06 --agents 2 --budget 20m \
    --brief "Settle what happens when the index is corrupted, on evidence rather than opinion." \
    --findings "findings/<task-id>.md" --dry-run
  note "Turning a proposal down leaves the others where they are."
  capture b dg-agent claim
  as "$b" dg add --id D09 --area ux --title "Should search paginate?" --after D05
  run dg pending
  run dg clear --agent "$b"
  run dg pending
}

layer "$@"
