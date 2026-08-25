#!/usr/bin/env bash
# Scene 3 — two agents join up without coordinating, because readiness is derived.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/story.sh"
one_checkout
silently beat_decompose

scene "Scene 3 — nobody updates a status, and the work still joins up"
say "Agent C, which had nothing to do in scene 1, takes the task B's
decomposition freed:"

beat_c_takes_a_subtask
C dg task

say "T04 is done, and look at what moved without anybody touching it:

  · T05 was \`waits T04\`. It is now **ready**.
  · T01 was \`waits T04, T05\`. It is now \`waits T05\`.

C wrote one thing — an outcome for its own task. It did not update T05, did not
notify B, and does not know B exists. **Blocked is derived, never stored**, so
there is no status anywhere that could have gone stale and no message that could
have been missed.

This is the part that makes a fan-out survivable. Two agents just handed work to
each other through the graph, and the handover cost one command by one of them."
