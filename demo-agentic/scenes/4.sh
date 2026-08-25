#!/usr/bin/env bash
# Scene 4 — the loud one, and the two collisions.
#
# Three interleavings that all end in a refusal, and the refusals are three
# different sentences on purpose. 4a is the same race as scene 3 one notch
# further; 4b and 4c are the same collision, and an agent that cannot tell them
# apart puts two vertices behind one question.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

scene "Scene 4a — the dangerous case aborts"
two_clones
say "Scene 3 again, except B stops at reopening: the sponsor mail has arrived,
the training run has not. A, meanwhile, is settling the question that rests on
it — and does not know either fact."

A dg decide D02 \
  --answer "SPRT against the previous build, 20k games on the volunteer cluster. Every weight has a name and a human can explain it, so a regression is debuggable by reading the diff." \
  --source notes/sprt.md \
  --falsifier "a weight change stops being reviewable by reading it"

B dg reopen D01 --why "A sponsor donated cluster time on 2026-03-18." --yes
B dg apply
git_commit "$B_DIR" "reopen D01"
push "$B_DIR"

pull "$A_DIR"
A dg apply

say "Nothing written, the premise named, two exits offered. Put this beside
scene 3 and the boundary shows: the same race, one notch different, and the
tool goes from a one-line note to a refusal.

The boundary is whether the resulting structure is legal — and that does not
track how wrong the answer is. \"A human can explain every weight\" under a
40 MB net is refused. \"412 KB, compiled into the binary\" under the same net
lands clean, which is scene 3. A reader who sees why is a reader who knows
where they have to supply the judgement themselves."

scene "Scene 4b — two agents, one id, two questions"
two_clones
say "The id race every parallel-agent system has. Both agents notice a gap,
neither can see the other's store, and both reach for the next free number."

A dg add --id D04 --title "How is the opening book distributed?" \
    --area Release --after D01
B dg add --id D04 --title "Which time control does CI run at?" \
    --area Tooling --after D01

B dg apply
git_commit "$B_DIR" "open D04"
push "$B_DIR"
say "B got there first. A pulls and applies:"

pull "$A_DIR"
A dg apply

scene "Scene 4c — two agents, one id, the same question"
two_clones
say "The commoner case, and the one that matters. Two agents sharing a brief
notice the same missing decision — what becomes of the tuning history once the
eval is replaced — and file it identically."

A dg add --id D05 \
    --title "What happens to the tuning history when the eval is replaced?" \
    --area Core --after D01
B dg add --id D05 \
    --title "What happens to the tuning history when the eval is replaced?" \
    --area Core --after D01

B dg apply
git_commit "$B_DIR" "open D05"
push "$B_DIR"

pull "$A_DIR"
A dg apply

say "The same collision as 4b, and it must not read the same. An agent that
takes this one as \"my work failed\" re-files under a fresh id, and the graph
ends up with one question and two vertices — the failure nothing downstream can
detect, because both vertices are individually valid.

So the message spends four lines saying nothing of yours was lost, and names
the one op to drop rather than the batch to abandon. This is the tool written
for a reader who cannot ask a follow-up question, which is the only real
difference between an agent using it and a person."
