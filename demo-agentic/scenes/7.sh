#!/usr/bin/env bash
# Scene 7 — the two failures nothing in this tool can prevent.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/story.sh"
one_checkout
silently beat_decompose
silently beat_c_takes_a_subtask

scene "Scene 7a — an agent goes quiet, and the work it was holding says so"
say "Agent B, before the sponsor mail, was the only one producing evidence for
D02. Suppose B stops — the process dies, the harness breaks, the run never comes
back. B does the right thing on the way out and parks its task:"

B dg task park T01 --why "cluster queue is six days deep; nothing to run the harness against"
B dg apply --mine
M dg check

say "Two findings, and neither is about B. **\`parked_holding_work\`** says
something else is waiting on the task nobody is doing. **\`evidence_stalled\`**
says a *question* is waiting on evidence nobody is producing — the answer to D02
is now nobody's job and nothing else was ever going to inform it.

That is the fan-out failure nobody watches for: not a crash, not a conflict, but
an agent that quietly stopped and left a question with no route to an answer.
Both findings name the three exits, and one of them is \`dg task drop\`, which
releases what was waiting rather than holding it."

scene "Scene 7b — the one nothing catches"
one_checkout
silently beat_decompose
silently beat_c_takes_a_subtask
say "Now the quiet one, and it is the reason the other six scenes are worth
having.

Agent A composes its answer to D03 from T02's measurement — 412 KB, of which the
weights are 71 KB. That is the only reading of the premise that exists when A
writes it:"

A dg decide D03 \
  --answer "One binary, no runtime files. The weights compile in as a generated header — 412 KB at -Os, so there is nothing to download and nothing to version separately." \
  --source "T02: bench/size.md" \
  --falsifier "the weights outgrow what a header can carry, or a release ever needs a second file"

say "Staged, not applied — A means to check it over. While it sits there the
sponsor mail arrives, the premise is reopened, and it is re-answered:"

M dg reopen D01 \
  --why "A sponsor donated cluster time on 2026-03-18. The falsifier named this exact event: the GPU budget appeared." \
  --yes
M dg apply --mine
M dg decide D01 \
  --answer "A trained net, 40 MB. Six weeks of donated cluster time buys more strength than a year of hand-tuning, and the tuning had stalled twice." \
  --source "notes/sponsor.md" \
  --falsifier "the net fails to beat the hand-tuned build by 30 Elo after a full training run"
M dg apply --mine

say "Note the maintainer's `--mine`: A's answer is staged in the same tray and
is not the maintainer's to write. Scene 5 is still running in the background of
every scene after it.

A, which has been doing something else, comes back and applies:"

A dg apply --mine

say "Two lines, printed once, to a process that may not have been reading — and
the first of them is the whole story: *its answer changed*. Not the status, not
the structure. The premise still says what kind of thing it is; it now says
something else about it. Then:"

M dg check
M dg why D03

say "Four lines apart, in one command's output: *the weights compile in as a
generated header*, and *a trained net, 40 MB*. Underneath them, every premise
under this is settled.

\`dg check\` is not wrong. The structure is valid; the two answers contradict
each other in the prose, where no invariant can reach.

**And this is the one that no amount of the last six scenes prevents.** Scene 5
was attribution and closed with a name. Scene 3 was coordination and closed
because readiness is derived. Scene 6 was propagation and closed because reopen
computes the set. This is none of those: A owned its op, cited real evidence,
and the world moved while the answer sat in a tray. **Giving A a clone of its
own would have made it more likely, not less.**

What saves it is the falsifier A wrote before it had any reason to — *the
weights outgrow what a header can carry*. They now have:"

A dg reopen D03 --why "its falsifier fired: D01 moved to a 40 MB net" --yes
A dg apply --mine
M dg show

say "Drift does not prevent the stale answer. It is the one chance anybody gets
to notice, and the falsifier is what makes it findable six months later by
somebody who was not here."
