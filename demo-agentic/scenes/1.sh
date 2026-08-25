#!/usr/bin/env bash
# Scene 1 — the work queue is the assignment.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/story.sh"
one_checkout

scene "Scene 1 — nobody writes the plan"
say "An open-source Go engine, three decisions deep, with work outstanding
against two of them. A maintainer is about to put three software agents on it,
and the first question is what to give them.

They do not ask for it. They ask the graph:"

beat_the_queue

say "Three different jobs came back, and a person wrote none of them.

  · **An answer is owed.** T02 measured the binary in March and reported
    412 KB. Nobody ever wrote down what that meant, so D03 sits unsettled on
    evidence that already exists. That is not a nag — it is a piece of work,
    and \`dg check\` is the thing that noticed.

  · **A task is ready.** T01 has no prerequisites and nothing is blocking it.
    \`ready T01\` is the queue saying so; readiness is computed from the edges,
    not stored anywhere and not maintained by anyone.

  · **A task cannot start.** T03 waits on D03 — there is nothing to put in a
    release note until somebody says what ships. It is not blocked by *work*;
    it is blocked by a *question*, and the graph is the only place that shows
    both kinds of waiting in one list.

So: agent A takes the answer that is owed, agent B takes the ready task, and
agent C has nothing yet. That is a real state and it is where the next scene
starts."
