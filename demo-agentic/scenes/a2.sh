#!/usr/bin/env bash
# Annex 2 — bringing two clones back together.
#
# Where a fan-out ends. Two agents did nothing wrong, git cannot merge the
# result, and the tool has a verb for it that a text merge does not: replay the
# arriving side as ops, contest what genuinely conflicts, and ask a person once.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/story.sh"
own_clones

scene "Annex 2a — what git can do with two agents' work"
say "The agents are in a clone each, and this time there is no contention
inside dg at all: two unrelated questions, different ids, valid batches, clean
applies."

A dg add --id D07 --title "Which platforms get a prebuilt binary?" \
    --area Release --after D01
A dg apply --mine
B dg add --id D08 --title "How are volunteer cluster results verified?" \
    --area Tooling --after D01
B dg apply --mine

git_commit "$A_DIR" "open D07"
git_commit "$B_DIR" "open D08"
push "$A_DIR"

say "A pushed first; B pulls:"

B git pull --no-rebase origin main

say "Two agents did nothing wrong and the graph will not merge. decisions.json
is a JSON array and git merges it as text; two additions in the same region
conflict. **This is what isolation cost**: scene 5 cannot happen in separate
clones, and this can. The race did not go away, it moved.

Resolution here is two facts about the layout and one command. decision-graph.md
is generated, so never resolve it — take either side, it is about to be
overwritten:"

B git checkout --ours decision-graph.md
B python3 ../union.py

say "decisions.json is the union of the two vertex lists; union.py does it here
so the scene runs unattended, and a person would open the file. Then the step
that makes it safe rather than merely merged:"

B dg render
B dg check

say "Clean — and it is worth saying why that is reassuring rather than lucky.
Two additions is the case a union genuinely handles. The next half is the case
it does not."

scene "Annex 2b — the same question, answered twice"
own_clones
beat_the_day_so_far "$A_DIR"
pull "$B_DIR"; pull "$C_DIR"
say "Both clones now hold the day's work — D02 and D03 settled on the evidence
T01 and T02 produced, the tasks finished.

A and B were both asked what happens to the hand-tuning history, and both
answered it. Each side is valid on its own; each agent did the job it was
given:"

A dg add --id D50 --title "What happens to the hand-tuning history?" \
    --area Core --after D01
A dg apply --mine
A dg decide D50 \
  --answer "Kept, in the repo, under history/tuned-2026-03. It is the only record of how the engine played before the net." \
  --source notes/history.md \
  --falsifier "nobody reads it for two releases running"
A dg apply --mine
git_commit "$A_DIR" "D50: keep the tuning history"
push "$A_DIR"

pull "$B_DIR"
B dg decide D50 \
  --answer "Dropped. The tuned weights are reconstructible from the git history and nobody has opened them since the net landed." \
  --source notes/cleanup.md \
  --falsifier "somebody needs a pre-net build and cannot make one"

say "A pushed first. B pulled, and B's answer never even reached the tray:
refused at composition, naming the answer that is already there.

That is worth pausing on. A text merge would have put both edges in the file and
\`dg check\` would have refused the result. A union keyed by id would have picked
one **silently**, which is worse than either. Here the refusal arrives before
anything is staged, let alone merged.

Two answers to one question is not a merge problem to be resolved — it is
a disagreement between two agents, and the only thing that can settle it is a
person who knows which is right.

That is what the seam is for. \`dg integrate\` expresses an arriving
contribution as ops against the graph you have, replays them, and collects every
conflict before asking anything — so a person answers a list once instead of
being interrupted per record. \`dg incoming --take\` and \`--keep\` are the two
answers, and \`--split\` is the third, for when the two turn out to be answers to
different questions.

Which closes the day. The premise was reopened because a falsifier fired, three
agents worked in parallel on what it opened, one of them was told to wait, one
of them went stale and said so, two of them collided on an id, and the graph
came back together with every one of those recorded:"

M dg show
M dg why D50
