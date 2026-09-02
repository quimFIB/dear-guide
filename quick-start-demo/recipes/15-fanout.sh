#!/usr/bin/env bash
# q: Several agents, one graph — how is that kept safe?
# part: agents
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  note "A second piece of evidence for D06, beside T04: two tasks that would move the same decision."
  run dg task add --id T13 --area storage --title "Measure how often an index corrupts in practice" --evidence-for D06
  quietly dg apply
  run dg task independent
  note "setup assigns each agent one task from that set. Four agents asked for, three independent tasks: three launch."
  run_head 9 dg-agent setup --preset contributor --agents 4 --budget 20m \
    --brief "Settle what happens when the index is corrupted, on evidence rather than opinion." \
    --findings "findings/<task-id>.md" --dry-run
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
  note "What can be worked on at once: the set setup will assign, one task per agent."
  run dg task independent
  run dg-agent setup --preset contributor --focus D06 --agents 2 --budget 20m \
    --brief "Settle what happens when the index is corrupted, on evidence rather than opinion." \
    --read "docs/index-format.md:how the index file is laid out" \
    --findings "findings/<task-id>.md"
  run ls fanout
  run sed -n 1,40p fanout/scout.md
  run cat fanout/env.json
  run cat fanout/launch.sh
  run dg-agent env --check --plan fanout/env.json
  note "A roster written by hand is obeyed as written; a pair that may collide is said, not refused."
  run dg task add --id T13 --area storage --title "Measure how often an index corrupts in practice" --evidence-for D06
  quietly dg apply
  run_head 8 dg-agent setup --preset contributor --roster T04,T13 --budget 20m \
    --brief "Settle what happens when the index is corrupted, on evidence rather than opinion." \
    --findings "findings/<task-id>.md" --dry-run
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
