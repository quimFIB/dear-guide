#!/usr/bin/env bash
# Scene 2 — what one writer at a time actually costs.
#
# The short scene, and the only one where nothing goes wrong. It is here so the
# demo is a description of a limit rather than an argument against the tool:
# the protocol that avoids scene 1 exists, is one command, and is a discipline
# — which is exactly why it does not survive two autonomous agents.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
one_project

scene "Scene 2 — what one writer at a time actually costs"
say "Scene 1 again, played the supported way. A stages, as before:"

A dg decide D02 \
  --answer "SPRT against the previous build, 20k games on the volunteer cluster. Every weight has a name and a human can explain it, so a regression is debuggable by reading the diff." \
  --source notes/sprt.md \
  --falsifier "a weight change stops being reviewable by reading it"

say "B, before staging anything of its own, looks at the tray it is about to
stage into:"

B dg pending

say "— and stops, because that op is not B's. That is the whole protocol.
\`dg pending\` costs nothing, needs no flag, and turns scene 1 into a
non-event.

It is also a habit, and the README is already clear about what happens to
habits: \"Nothing a host can observe reveals that something was settled.\"
Nothing observable reveals that an agent skipped this look, either. Two people
in two terminals keep it because they are one intent with two hands. Two agents
are two intents, and the honest answer stays: one writer at a time."
