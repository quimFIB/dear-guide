#!/usr/bin/env bash
# q: How does one agent work the frontier?
# part: agents
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  capture name dg-agent claim
  note "Every command below runs with DG_AGENT=$name — every op it stages carries the name."
  as "$name" dg task
  as "$name" dg task start T04
  as "$name" dg apply --mine
  note "...the work happens..."
  as "$name" dg task done T04 --outcome "src/index/open.py — a checksum in the header, PR #40"
  as "$name" dg apply --mine
  note "Under DG_DECIDE=evidence an agent may settle only what a finished evidence task backs."
  as "$name" DG_DECIDE=evidence dg decide D06 \
    --answer "Refuse to open it. Print the rebuild command and exit 3." \
    --source "T04" \
    --falsifier "a corrupted index is seen in the wild more than once a month"
  as "$name" dg apply --mine
  run dg-agent list
  run dg task
}

full() {
  fresh
  capture name dg-agent claim --budget 30m
  note "What the environment actually says, fallbacks named as fallbacks."
  as "$name" DG_DECIDE=evidence DG_WRITE=launch dg-agent env
  note "The policy is a refusal, not a habit: no finished evidence, no answer."
  as "$name" DG_DECIDE=evidence dg decide D03 \
    --answer "The small local model; the hosted one costs more than the notes are worth." \
    --source "discussion" --falsifier "recall under 0.8 on the 50k set"
  note "Writes outside the launch scope stop and ask; inside it they pass; reads are never judged."
  as "$name" DG_WRITE=launch dg gate --write ./notes/todo.md
  as "$name" DG_WRITE=launch dg gate --write /etc/hosts
  note "A budget is real when the launcher is the agent's parent. This agent takes T05 and never finishes."
  cat > agent.sh <<'SH'
#!/usr/bin/env bash
echo "I am $DG_AGENT"
dg task start T05 && dg apply --mine
sleep 30
SH
  chmod +x agent.sh
  run cat agent.sh
  run dg-agent run --budget 2 -- ./agent.sh
  run dg-agent list
  run dg task
  note "The park is staged under the agent's own name, for the supervisor to read and land."
  run dg pending --agent agile-bearing
  run dg apply --agent agile-bearing
  run dg task node T05
  note "For an agent nobody was watching, expire is the backstop; then prune the idle names."
  run dg-agent expire
  run dg-agent prune
  run dg-agent list
}

layer "$@"
