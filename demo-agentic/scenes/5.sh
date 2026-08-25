#!/usr/bin/env bash
# Scene 5 — isolation moves the race, it does not remove it.
#
# Two agents do nothing wrong at all: unrelated questions, different ids, valid
# batches, clean applies. The race left the tray when scenes 3-5 gave each agent
# its own clone, and here is where it went.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
two_clones

scene "Scene 5 — isolation moves the race, it does not remove it"
say "No contention inside dg at all this time. A opens one question, B opens
another, and the two have nothing to do with each other."

A dg add --id D07 --title "Which platforms get a prebuilt binary?" \
    --area Release --after D01
A dg apply
B dg add --id D08 --title "How are volunteer cluster results verified?" \
    --area Tooling --after D01
B dg apply

git_commit "$A_DIR" "open D07"
git_commit "$B_DIR" "open D08"
push "$A_DIR"

say "Both batches applied cleanly. A pushed first; B pulls:"

B git pull --no-rebase origin main

say "Two agents did nothing wrong and the graph will not merge. decisions.json
is a JSON array and git merges it as text; two additions in the same region
conflict. This is what the isolation cost: scenes 1 and 2 cannot happen here,
and this can.

The resolution is two facts about the file layout and one command.

First, decision-graph.md is generated. Never resolve it — take either side, it
is about to be overwritten anyway:"

B git checkout --ours decision-graph.md

say "Second, decisions.json is resolved by hand, as the union of the two vertex
lists. scenes/union.py does that here so the scene can run unattended; a person
would open the file."

B python3 ../union.py

say "And then the step that makes it safe:"

B dg render
B dg check

say "dg check is the merge test. A conflict resolved wrongly — a vertex
dropped, an edge target lost, a status left behind — is a structural fault, and
structural faults are the ones this tool does catch.

So this scene ends somewhere scene 3 does not. There, the graph was clean and
wrong and only a single line of output ever said so. Here the damage a bad
resolution could do is exactly the damage \`dg check\` is built to find."

git_commit "$B_DIR" "merge D07 and D08"
