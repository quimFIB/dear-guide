#!/usr/bin/env bash
# Scene 3 — parallel composition, ordered publication.
#
# The scene that says what the graph is *for* under a fan-out. Three agents
# compose at once and nothing stops them; what is ordered is the moment each
# answer becomes part of the record, and the order is the edges nobody had to
# remember.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/story.sh"
one_checkout
silently beat_reopen

scene "Scene 3 — three answers at once, and the order the graph already knew"
say "Everybody is named now. All three agents work at the same time, and all
three finish within a minute of each other:"

beat_all_three_compose
M dg pending

say "Three answers, three authors, one tray, and none of it written yet.
Agent B publishes first:"

B dg apply --mine

say "Refused, and read what it says. D02 rests on D01, D01 is REOPENED, so an
answer to D02 would be an answer standing on a premise under review. B did
nothing wrong — B was *asked* to answer D02 — and the tool did not guess: that
edge was recorded when the question was opened, months before any of this.

Nothing was written and B's answer is still B's. So the premise goes first:"

A dg apply --mine

say "And now B, unchanged, with the same op it staged before the refusal:"

B dg apply --mine

say "That is the claim this scene makes, and it is the one worth taking away.
**Composition parallelises; publication is ordered by the dependency.** Nobody
had to sequence the agents, nobody had to hold a lock, and nobody had to know
what the others were doing. Three agents ran flat out, one of them was told
\"not yet\" in a sentence naming the premise and the two ways out, and the graph
never held a state anybody would have to unpick.

What it does *not* do is make C's answer right, and C is about to apply it."
