#!/usr/bin/env bash
# Scene 6 — the premise moves under work that is already finished.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/story.sh"
one_checkout
silently beat_decompose
silently beat_c_takes_a_subtask
silently beat_a_harvests
silently beat_b_reports
silently beat_b_answers

scene "Scene 6 — one fact arrives, and every agent's answer is under review"
say "The day's work is done. Two questions settled on evidence two agents
produced, three tasks finished, one blocked task released. Then the maintainer
gets a mail: a sponsor has donated cluster time.

That is not a new thing to weigh up. It is the thing D01's falsifier — written
on 2026-03-01, before anybody had a reason to think it would fire — said to
watch for:"

beat_the_sponsor

say "Read the box. **Both** of the day's answers rest on D01, so both are now
PROVISIONAL, and the maintainer did not work that out — \`dg reopen\` computed
the set and \`dg check\` refuses a store where it was not applied.

This is the cost of a fan-out, stated exactly: three agents worked in parallel
on a premise, and one fact put all of it under review at once. What the graph
gives you is the *list*."

M dg show
M dg task

say "And now look at what did **not** happen. T03 is still \`ready\`.

That is the difference between OPEN and PROVISIONAL, and it is deliberate. D03
has an answer; what it no longer has is an answer anybody is currently vouching
for. Blocking T03 would say the answer was *gone*, and it is not — it is in the
store, it still cites T02, and it may well survive. Stopping the work would be a
stronger claim than the graph is entitled to make.

So nothing halts, and instead \`dg show\` says it in the one place a reader will
look: two decisions under **RESTING ON A PREMISE UNDER REVIEW**. Whoever starts
T03 now is building on something being re-examined, and can see that they are.

That is the whole of what a reopen costs a fan-out. Not lost work, not stopped
work — a list of what is now standing on a question, computed rather than
remembered, with \`dg confirm\` as the way each one comes off it."
