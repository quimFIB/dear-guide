#!/usr/bin/env bash
# q: How do I know the graph is still honest?
# part: ask
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  run dg check
  run dg task done T04 --outcome "src/index/open.py — a checksum in the header, PR #40"
  run dg apply
  run dg check
}

full() {
  fresh
  note "Evidence landed and nobody wrote down what it meant. The check says so until somebody does."
  run dg task done T04 --outcome "src/index/open.py — a checksum in the header, PR #40"
  run dg apply
  run dg check
  run dg decide D06 \
    --answer "Refuse to open it. Print the rebuild command and exit 3." \
    --source "T04" \
    --falsifier "a corrupted index is seen in the wild more than once a month"
  run dg apply
  run dg render
  run dg task render
  run dg check
  note "A store a merge broke: D02 reopened by hand, and nothing propagated."
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
  run dg check
  run dg gate --command 'git commit -m "wip"'
  run dg repair
  run dg apply
  run dg check
}

layer "$@"
