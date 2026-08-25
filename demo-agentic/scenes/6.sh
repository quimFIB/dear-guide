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

say "And the work follows the answers. T03 — the release note C was about to
start — is waiting again, because what ships is a question again.

Nothing here is wrong and nothing is lost. The two answers are still in the
store, still cite the tasks that produced them, and \`dg confirm\` is what says
*re-read under the new premise and it still holds*. What has changed is that
none of them may be relied on until somebody says so."
