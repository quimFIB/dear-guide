#!/usr/bin/env bash
# q: What does a Claude Code or opencode session get from the plugin?
# part: agents
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  note "Injected at the start of every session, and again after a compaction."
  run dg brief
  note "And a commit that would leave the graph contradicting itself is refused at the door."
  run dg gate --command 'git commit -m "settle D06"'
}

full() {
  fresh
  run cat "$repo/commands/brief.md"
  note "The commit gate. Break the store by hand — D02 reopened, nothing propagated — and try to commit."
  quietly python3 -c "
import json; g=json.load(open('decisions.json'))
for v in g['vertices']:
    if v['id']=='D02': v['status']='REOPENED'
bare=None
for e in g['edges']:
    if e['from']=='D02' and e.get('active'):
        e['active']=False; e['why']='merged in from a branch that reopened it'; bare={'from':'D02','to':e['to'],'active':True}
g['edges'].append(bare)
json.dump(g, open('decisions.json','w'), indent=1)"
  run dg gate --command 'git commit -m "wip"'
  run dg repair
  run dg apply
  run dg gate --command 'git commit -m "wip"'
  run dg render
  run dg gate --command 'git commit -m "wip"'
  note "The words a host watches for, read from the tool rather than copied."
  run dg gate --triggers
}

layer "$@"
