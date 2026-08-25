#!/usr/bin/env bash
# The story, as beats. Sourced by every scene; never run on its own.
#
# One day on an open-source Go engine, with three software agents on it. The
# thing that drives every scene is **work**: the task graph says what is ready,
# an agent picks it up, doing it produces evidence, and evidence is what settles
# a decision. Nobody in this demo invents an answer — every answer is somebody's
# task reporting what it found.
#
# That ordering is the whole point. A demo in which agents simply announce
# decisions is a demo about staging races with a graph bolted on; the
# concurrency problems here arise from three agents doing real work and then
# having to join it up, which is the only place they arise in practice.
#
# **Why the beats are functions.** A scene has to be readable cold — `demo.sh 4`
# is a thing people run — and the story has to accumulate, or seven scenes with
# one cast are still seven unrelated examples. Both, by replaying the earlier
# beats with `silently` and playing this one aloud. The state a scene opens on
# is therefore the state the story really left, not a fixture that resembles it
# — which also means a change to an early beat cannot quietly stop matching the
# prose of a later one.

# ---- the assignment ------------------------------------------------------
#
# Nothing is staged here. The graph is asked what is outstanding and three
# different jobs come back: an answer that is owed (T02 reported and D03 is
# unsettled), work that is ready (T01), and work that cannot start (T03 waits on
# D03). Three agents, three jobs, and a person wrote none of it.

beat_the_queue() {
  M dg check
  M dg task
}

# ---- agent B opens the work up -------------------------------------------
#
# The parallelism here is **created by the work**, not by the script. B picks up
# one ready task, finds it is three, and the moment those subtasks exist there is
# more startable work than there were agents.

beat_decompose() {
  B dg task start T01
  B dg task add --id T04 --title "Get cluster credentials from the sponsor" --area Tooling
  B dg task add --id T05 --title "Port the SPRT runner to the cluster scheduler" --area Tooling --after T04
  B dg task dep T01 --after T04,T05
  B dg apply --mine
}

# ---- agent C takes what the decomposition freed --------------------------

beat_c_takes_a_subtask() {
  C dg task start T04
  C dg apply --mine
  C dg task done T04 --outcome "sponsor issued a service account; credentials are in the CI secret store"
  C dg apply --mine
}

# ---- agent A harvests evidence that was already there --------------------
#
# `T02` reported in March. The number has been in the store ever since and
# nothing was written down about what it *meant* — which is what the opening
# `evidence_unharvested` is telling somebody to fix.

beat_a_harvests() {
  A dg decide D03 \
    --answer "One binary, no runtime files. The weights compile in as a generated header — 412 KB at -Os, so there is nothing to download and nothing to version separately." \
    --source "T02: bench/size.md" \
    --falsifier "the weights outgrow what a header can carry, or a release ever needs a second file"
  A dg apply --mine
}

# ---- B's work finishes, and the answer follows from it -------------------

beat_b_reports() {
  B dg task done T05 --outcome "runner ported; a 20k-game batch schedules in 4h"
  B dg task done T01 --outcome "harness live against the cluster; SPRT verdict lands in the CI log per run"
  B dg apply --mine
}

beat_b_answers() {
  B dg decide D02 \
    --answer "SPRT against the previous build, 20k games on the volunteer cluster. Every weight has a name and a human can explain it, so a regression is debuggable by reading the diff." \
    --source "T01: harness live, verdict in the CI log" \
    --falsifier "a weight change stops being reviewable by reading it"
  B dg apply --mine
}

# ---- the premise moves under all of it -----------------------------------

beat_the_sponsor() {
  M dg reopen D01 \
    --why "A sponsor donated cluster time on 2026-03-18. The falsifier named this exact event: the GPU budget appeared." \
    --yes
  M dg apply
}

# ---- the day, replayed into a clone --------------------------------------
#
# The annexes open on the graph the seven scenes built, not on a fixture that
# resembles it: the work done, both questions settled on the evidence it
# produced. Pushed, so the other clones start from it too.
#
# Written as a replay rather than as a second `decisions.json` for the reason
# `demo.sh` gives about the scenes themselves: a fixture drifts from the story
# silently, and a replay cannot.
beat_the_day_so_far() { # beat_the_day_so_far <dir>
  local d=$1 keep_M=$M_DIR keep_A=$A_DIR keep_B=$B_DIR keep_C=$C_DIR
  M_DIR=$d; A_DIR=$d; B_DIR=$d; C_DIR=$d
  silently beat_decompose
  silently beat_c_takes_a_subtask
  silently beat_a_harvests
  silently beat_b_reports
  silently beat_b_answers
  M_DIR=$keep_M; A_DIR=$keep_A; B_DIR=$keep_B; C_DIR=$keep_C
  git_commit "$d" "the day's work: D02 and D03 settled on the evidence T01 and T02 produced"
  push "$d"
}
