#!/usr/bin/env bash
# Scene 2 — an agent decomposes, and that is what creates the parallelism.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/story.sh"
one_checkout

scene "Scene 2 — the work opens up, and now there is more of it than there are agents"
say "Agent B picks up T01 and does the thing an agent actually does first: finds
out what is in it. Wiring a harness to somebody else's cluster turns out to be
three things, and two of them are somebody else's to give."

beat_decompose
B dg task

say "Read what changed. T01 is DOING and now **waits T04, T05** — B did not
guess that, B said \`dg task dep T01 --after T04,T05\` and the graph derived the
rest. T05 waits on T04 because you cannot port a runner to a cluster you have no
credentials for.

And at the bottom: \`ready T04\`.

**That line is the whole scene.** A moment ago there was one ready task and
three agents. B looked at its own work, and in doing so produced startable work
for somebody else. Nobody scheduled that, nobody split anything up in a prompt,
and the agent that will pick T04 up has not been told about it — it will find it
the same way B found T01.

The parallelism in this demo is made by the work. Everything that goes wrong
from here goes wrong because of that, not because a script arranged it."
