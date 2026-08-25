#!/usr/bin/env bash
# Scene 1 — the tray has no idea whose ops are whose.
#
# One project, two agents. A stages and means to review before applying; B,
# working on something else entirely, applies. Nothing here is a bug: every
# command does exactly what it says, the store stays valid, and A's model of
# it goes wrong anyway.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
one_project

scene "Scene 1 — the tray has no idea whose ops are whose"
say "One project, two agents in it. Agent A is settling how a weight change
gets accepted; agent B is watching for anything that moves the premise
underneath. They share a directory, so they share a staging tray."

A dg decide D02 \
  --answer "SPRT against the previous build, 20k games on the volunteer cluster. Every weight has a name and a human can explain it, so a regression is debuggable by reading the diff." \
  --source notes/sprt.md \
  --falsifier "a weight change stops being reviewable by reading it"

say "A has staged it, not applied it. Meanwhile the sponsor mail arrives, and
agent B does the obvious right thing:"

B dg reopen D01 \
  --why "A sponsor donated cluster time on 2026-03-18. The falsifier named this exact event: the GPU budget appeared." \
  --yes

say "Read that box again. D02 is OPEN in the store — it is decided only in the
tray, by A, and unapplied. B's command has just described a consequence of work
that is not B's, does not exist yet, and that B has never seen. B has no way to
tell: \`dg reopen\` correctly reasons over the effective graph, and the
effective graph is shared."

B dg pending

say "Three ops, two authors, and nothing in the tray records which is which.
B applies what B believes is B's batch:"

B dg apply

say "The graph that results is correct. D02 is PROVISIONAL, the propagation is
right, \`dg check\` is clean, nothing is corrupt. What went wrong is that A's
answer was published at a moment A did not choose, by a process that did not
know it was publishing it."

A dg pending

say "That is the failure: no signal at all. Not that the work landed, not that
the ground moved under it. The obvious reading of \"nothing staged\" is 'my
staging failed', and the obvious repair is to do it again —"

A dg decide D02 \
  --answer "SPRT against the previous build, 20k games on the volunteer cluster. Every weight has a name and a human can explain it, so a regression is debuggable by reading the diff." \
  --source notes/sprt.md \
  --falsifier "a weight change stops being reviewable by reading it"

say "— and here the tool catches it. The refusal is accurate and names both
exits. So the honest reading of this scene is not that the store breaks, because
it does not. It is that the store stays right while an agent's model of it goes
wrong, and the agent finds out only by trying to write. One writer at a time."
