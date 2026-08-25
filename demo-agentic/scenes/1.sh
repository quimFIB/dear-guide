#!/usr/bin/env bash
# Scene 1 — the fan-out, and the graph that makes it a plan.
#
# Nothing concurrent happens here. It is the scene that earns the other five:
# the problem is named, the falsifier that reopens it is the one written months
# earlier, and `dg show` turns "this is too big for one pass" into three
# questions with an order between them.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/story.sh"
one_checkout

scene "Scene 1 — one hard question, three areas, three agents"
say "An open-source Go engine, three decisions deep. The maintainer starts
where anybody picking a project back up starts — by asking the graph what it is
worried about:"

M dg check

say "A sponsor has just donated cluster time. That is not a new fact to weigh
up; it is the fact D01 was *waiting* for. Its falsifier, written on 2026-03-01
before anybody had reason to think it would fire, says: \"a GPU budget appears,
or hand-tuning stalls for two releases running\".

So the honest first move is not to argue. It is to reopen:"

beat_reopen

say "And now the graph is the plan:"

M dg show

say "Three questions, and an order between them: D01 is decidable now, D02 and
D03 wait on it. That order is not a rule somebody wrote down — it is the edges,
recorded when each question was opened.

This is more than one pass can hold, and each question belongs to a different
area, so the maintainer fans out three agents:

  agent A · Core      D01 — where the weights come from, now that there is a cluster
  agent B · Tooling   D02 — how a weight change is accepted
  agent C · Release   D03 — what ships in the release binary

Two of the three are working on a premise nobody has settled yet. That is what
parallel exploration *is*, and the point of the next five scenes is that the
graph already knows it."
