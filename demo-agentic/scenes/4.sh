#!/usr/bin/env bash
# Scene 4 — the quiet one: a stale premise, still legal.
#
# The scene the drift stamp exists for, and the reason this demo is worth
# having. Everything here is legal, every command succeeds, `dg check` ends
# clean, and the graph describes a release process that cannot produce a
# release. One line of output, printed once, is the whole warning.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/story.sh"
one_checkout
silently beat_reopen
# C is the fastest of the three and finishes first — which is what makes its
# answer the stale one. It composed against a store in which D01 was still
# REOPENED and nothing was staged against it, so the reading it stamped is the
# one the whole project had until A landed.
silently beat_c_composes
silently beat_a_composes
silently A dg apply --mine
silently beat_b_composes
silently B dg apply --mine

scene "Scene 4 — the quiet one: a stale premise, still legal"
say "Carrying straight on from scene 3, with one detail that matters: C was the
fastest of the three and finished first. Its answer to D03 has been sitting
staged ever since, while D01 and D02 were settled around it.

Look at what C composed it against. When C wrote it, D01 was REOPENED and
nobody had staged anything against it, so the only reading available was the one
the project had had since March — hand-tuned weights, measured by T02 at 71 KB
inside a 412 KB binary. \"The weights compile in as a generated header\" is the
right answer to that premise. It is the *only* answer C could have reached.

A has since settled D01 at 40 MB. C applies:"

C dg apply --mine

say "There is the whole warning: one line, printed once, to a process that may
not have been reading. Nothing was refused, because nothing was invalid — D02
became DECIDED under a settled premise and D03 does too. Ask the graph whether
it is sound:"

M dg check
M dg why D03

say "Four lines apart, in one command's output: \"the weights compile in as a
generated header\", and \"a trained net, 40 MB\". Underneath them: *every premise
under this is settled*.

And read what \`dg check\` did say. One warning, and it is about T01 not having
reported into D02 — a real thing, worth acting on, and **nothing whatever to do
with the contradiction**. The graph's honesty machinery is working perfectly and
pointed somewhere else.

\`dg check\` is not wrong. The structure is valid; the two answers contradict
each other in the prose, where no invariant can reach. This graph is as clean as
the tool can certify and it describes a release that cannot be built.

**This is the one concurrency problem a lock cannot touch**, and it is worth
being exact about why. Scene 2's failure was attribution and closed with a name.
Scene 3's was ordering and closed with an edge. This one is neither: every op
was owned, every premise was settled before it was built on, and the batch was
still composed against a reading of the world that stopped being true while it
sat there. No amount of isolation helps — a clone of its own would have made it
*more* likely, not less.

What saves it is the falsifier C wrote before it had any reason to — \"the
weights outgrow what a header can carry\". They now have, so the exit is a
command rather than an argument:"

C dg reopen D03 --why "its falsifier fired: D01 moved to a 40 MB net" --yes
C dg apply --mine
M dg show

say "The claim is narrow and true. Drift does not prevent the stale answer; it
is the one chance anybody gets to notice, and the falsifier is what makes it
findable six months later by somebody who was not here."
