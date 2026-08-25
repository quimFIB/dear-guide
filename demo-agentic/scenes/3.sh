#!/usr/bin/env bash
# Scene 3 — the quiet one: a stale premise, still legal.
#
# The scene the drift stamp exists for, and the reason this demo is worth
# having. Everything here is legal, every command succeeds, `dg check` ends
# clean, and the graph ends up describing a release process that cannot produce
# a release. One line of output, printed once, is the whole warning.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
two_clones

scene "Scene 3 — the quiet one: a stale premise, still legal"
say "One clone each now: the isolation a harness gives parallel agents. Separate
trays, separate worktrees, no shared staging area. Scene 1 cannot happen here.

This is where both agents start:"

A dg check

say "That warning is agent A's assignment, and it came from the tool rather
than from a prompt: T02 measured the binary, D03 is still unsettled, so somebody
has to write down what the number meant. A does exactly that, reasoning from
D01 as it stands — hand-tuned weights, 71 KB of them."

A dg decide D03 \
  --answer "One binary, no runtime files. The weights compile in as a generated header — 412 KB at -Os, so there is nothing to download and nothing to version separately." \
  --source bench/size.md \
  --falsifier "the weights outgrow what a header can carry, or a release ever needs a second file"

say "Staged, not applied. In the other clone, B has the sponsor mail — and this
time B does not stop at reopening. B settles it the other way:"

B dg reopen D01 \
  --why "A sponsor donated cluster time on 2026-03-18. The falsifier named this exact event: the GPU budget appeared." \
  --yes
B dg apply

say "B applies the reopen on its own, before it has anything to put in its
place — so the two halves of the reversal are two records, in the order they
were actually known. Six weeks later the training run comes back:"

B dg decide D01 \
  --answer "A trained net, 40 MB. Six weeks of donated cluster time buys more strength than a year of hand-tuning, and the tuning had stalled twice." \
  --source bench/net-vs-handtuned.md \
  --falsifier "the net fails to beat the hand-tuned build by 30 Elo after a full training run" \
  --opens D02,D03
B dg apply

git_commit "$B_DIR" "D01: a trained net"
push "$B_DIR"
say "B commits and pushes. A, still holding the batch it composed before any of
that existed, pulls and applies:"

pull "$A_DIR"
A dg apply

say "There is the whole warning: one line. D01 never left DECIDED, so no
invariant fired and the batch landed. Now ask the graph whether it is sound —"

A dg check
A dg why D03

say "Four lines apart, in one command's output: \"the weights compile in as a
generated header\", and \"a trained net, 40 MB\". Underneath them, every premise
under this is settled. Not one warning stands — the evidence_unharvested this
scene opened with is gone, because A did harvest it.

\`dg check\` is not wrong. The structure is valid; the two answers contradict
each other in the prose, where no invariant can reach. This graph is as clean as
the tool can certify and it describes a release process that cannot produce a
release.

What saves it is the falsifier A wrote before it had any reason to — \"the
weights outgrow what a header can carry\". They now have, so the exit is a
command rather than a judgement call:"

A dg reopen D03 --why "its falsifier fired: D01 moved to a 40 MB net" --yes
A dg apply

say "The claim this scene makes is narrow and true. Drift does not prevent the
stale answer; it is the one chance anybody gets to notice — printed once, at
apply time, to a process that may not have been reading."
