#!/usr/bin/env bash
# Scene 2 — the same fan-out, twice: nobody named, then everybody.
#
# The commands are identical in both halves. The only difference is one
# environment variable, set before the harness launched the agents.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/story.sh"
one_checkout
silently beat_reopen

scene "Scene 2 — three agents, one staging tray"
say "The agents were launched where the problem is: the maintainer's checkout.
So they share a directory, and a directory is one staging tray.

First, the way a harness gets it by forgetting — nobody is named:"

anonymous
beat_a_composes
beat_b_composes

say "Two answers staged, neither applied, and the tray cannot tell them apart:"

C dg pending

say "Agent C now finishes its own question and does the ordinary thing:"

beat_c_composes
C dg apply

say "C applied three ops. One was C's. The other two were half-finished answers
belonging to agents still working on them — and a close is the one op this tool
deliberately makes hard to take back: the way out is \`dg reopen\`, which files a
reversal that never happened. Ask A what it has staged:"

A dg pending

say "Nothing. Not \"your work landed\", not \"the ground moved\" — nothing, which
reads as \"my staging failed\" and invites A to write the answer again.

That is the failure, and it is worth naming precisely: not a broken store,
which it is not, but an answer published at a moment nobody chose, by a process
that did not know it was publishing it.

Now the same morning, with \$DG_AGENT set before the agents were launched. Same
three commands, same order:"

named
one_checkout
silently beat_reopen
beat_a_composes
beat_b_composes
beat_c_composes
C dg pending

say "Same tray, same three answers in a moment — and now each one says whose it
is. Agent A publishes:"

A dg apply

say "One op written, A's own, and A is told exactly what it left and whose. B
and C come back to a tray that still holds their work:"

B dg pending

say "One variable, set by whoever launches the agents, and a shared tray stops
being a place where one agent can publish another's draft.

Nothing here is *isolated*: the three still share one graph, one store and one
tray, and they can still see each other's work. That is the point rather than
the problem, and it is what the next scene is about."
