#!/usr/bin/env bash
# Scene 5 — two agents, one id, and the two ways that reads.
#
# The collision every parallel-agent system has, and it is worth two runs
# because the two cases need different answers from a person and the tool
# cannot tell them apart.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/story.sh"

scene "Scene 5a — two agents, one id, two questions"
own_clones
say "The maintainer has moved the agents into a clone each — the isolation a
harness with worktrees gives. Scenes 2 and 3 cannot happen here: separate trays,
separate stores, nothing shared until a push.

Both agents notice a gap while working, neither can see the other's store, and
both reach for the next free number:"

A dg add --id D04 --title "How is the joseki library distributed?" \
    --area Release --after D01
B dg add --id D04 --title "Which time control does CI run at?" \
    --area Tooling --after D01

A dg apply --mine
git_commit "$A_DIR" "open D04"
push "$A_DIR"
say "A got there first. B pulls and applies:"

pull "$B_DIR"
B dg apply --mine

say "Refused, and nothing was written. Read the sentence: *not what this op
would have created*. The tool has compared the two and found them different, so
it knows this is a genuine collision rather than the same record arriving twice
— and it says what to do, which is pick another id. B's question is still B's
and still staged.

Now the commoner case, and the one that matters."

scene "Scene 5b — two agents, one id, the same question"
own_clones
say "Two agents sharing a brief notice the same missing decision — what becomes
of the tuning history once the weights are trained rather than tuned. They
phrase it differently because they are different agents:"

A dg add --id D04 --title "What happens to the hand-tuning history?" \
    --area Core --after D01
B dg add --id D04 --title "Do we keep the old tuned weights around?" \
    --area Core --after D01

A dg apply --mine
git_commit "$A_DIR" "open D04"
push "$A_DIR"
pull "$B_DIR"
B dg apply --mine

say "The same refusal, and this time the right move is the opposite one: not a
fresh id, but drop it — the question is already open, under A's wording. An
agent that cannot tell 5a from 5b puts two vertices behind one question, which
is exactly the disorder the unique-id rule exists to prevent.

The tool cannot make that call and does not pretend to. What it does is make the
collision rare enough that the report a person reads stays readable:"

M dg range --set 50-99
M dg range

say "One grant per clone, and from then on every door allocates inside it and
refuses an --id outside it. It is prevention, not correctness — the layering
underneath is that a collision is *caught* at integration and *cheap* to rename
there. What the grant buys is that the integration report is not a rename line
per record anybody wrote, which is the volume that trains a reader to stop
reading it."
