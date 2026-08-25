#!/usr/bin/env bash
# Scene 5 — three agents, one staging tray.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/story.sh"
one_checkout
silently beat_decompose
silently beat_c_takes_a_subtask

scene "Scene 5 — three agents, one staging tray"
say "The agents were launched where the work is: the maintainer's checkout. A
directory is one staging tray, so everything scenes 2 to 4 composed passed
through the same file.

Here is that morning again with nobody named — the way a harness gets it by
forgetting. A is mid-answer and B is mid-report:"

anonymous
A dg decide D03 \
  --answer "One binary, no runtime files. The weights compile in as a generated header — 412 KB at -Os." \
  --source "T02: bench/size.md" \
  --falsifier "the weights outgrow what a header can carry"
B dg task done T05 --outcome "runner ported; a 20k-game batch schedules in 4h"
C dg pending
C dg task pending

say "Two agents' work across the two trays, and no way to tell whose is
whose — the stores are separate but the staging is shared. Agent C,
meanwhile, has noticed a chore of its own while reading T02 — the release note
will need the verdict format written down somewhere. C records it and applies:"

C dg task add --id T06 --title "Document the SPRT verdict format for the release note" \
    --area Release --discovered-during T02
C dg apply

say "C wrote its own op and **both of the others**, including an answer A had
not finished checking and a completion B had not verified. A \`close\` is the op
this tool deliberately makes hardest to take back: the way out is \`dg reopen\`,
which files a reversal that never happened. Ask A what it has staged:"

A dg pending

say "Nothing — which reads as *my staging failed*, and invites A to write the
answer a second time. B is in the same position about a completion it had not
finished verifying.

Now the same morning with \$DG_AGENT set before the agents were launched. Same
commands, same order:"

named
one_checkout
silently beat_decompose
silently beat_c_takes_a_subtask
A dg decide D03 \
  --answer "One binary, no runtime files. The weights compile in as a generated header — 412 KB at -Os." \
  --source "T02: bench/size.md" \
  --falsifier "the weights outgrow what a header can carry"
B dg task done T05 --outcome "runner ported; a 20k-game batch schedules in 4h"
C dg pending
C dg task pending

say "Same two trays, same two ops, and each now says whose it is. C adds the same
chore and applies:"

C dg task add --id T06 --title "Document the SPRT verdict format for the release note" \
    --area Release --discovered-during T02
C dg apply

say "C's own op written, and C is told exactly what it left and whose. A's
answer is still A's to finish:"

A dg pending

say "One variable, set by whoever launches the agents. Nothing here is
*isolated* — all three still share one graph, still see each other's work, and
still hand tasks to one another the way scene 3 showed. What changed is only
that publishing is now something an agent does to its own work."
