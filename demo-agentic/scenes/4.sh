#!/usr/bin/env bash
# Scene 4 — the loop: work makes evidence, evidence settles a question,
# and settling it releases work.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/story.sh"
one_checkout
silently beat_decompose
silently beat_c_takes_a_subtask

scene "Scene 4 — an answer is a piece of work, and it frees other work"
say "Agent A's job from scene 1: T02 reported months ago and D03 was never
settled on it. A reads the outcome and writes down what it meant — citing the
task, because that is where the number came from:"

beat_a_harvests
A dg task

say "\`ready T03, T05\`. **T03 was not ready a moment ago.**

It was \`waits D03 (undecided)\` — work blocked by a question rather than by
other work. A answered the question, so the release note became startable, and
the agent that picks it up will never know why it was blocked or who unblocked
it. And the link runs the other way too — A's answer cites T02, whose outcome
is the only reason there was anything to write.

Now B's side of the same loop. B's subtasks finish, and T01 with them:"

beat_b_reports
B dg check

say "There it is again, pointed at B: T01 was to inform D02, T01 is DONE, and
D02 is still unsettled. The graph is asking for the answer the work just earned:"

beat_b_answers
M dg check

say "Clean, and the day's shape is now visible. Two questions were settled, and
neither answer was invented: each one cites the task whose outcome produced it.
Three tasks were finished by two different agents who never spoke. One question
being answered turned a blocked task into a ready one.

**That is the loop this tool exists for** — and everything from here is what
happens when it is running and something moves underneath it."
