#!/usr/bin/env bash
# The story, as beats. Sourced by every scene; never run on its own.
#
# One day on an open-source Go engine. A sponsor donates cluster time — the
# exact event `D01`'s falsifier named — and everything about where the
# evaluation weights come from is back in play at once, across three areas. It
# is more than one pass can hold, so the maintainer fans out three agents and
# keeps the graph as the thing that says what each of them may assume.
#
# **Why the beats are functions.** A scene has to be readable cold — `demo.sh 4`
# is a thing people run — and the story has to accumulate, or six scenes with
# one cast are still six unrelated examples. Both, by replaying the earlier
# beats with `silently` and playing this one aloud. The state a scene opens on
# is therefore the state the story actually left, not a fixture that resembles
# it — which also means a change to an early beat cannot quietly stop matching
# the prose of a later one.

# ---- the fan-out ---------------------------------------------------------

beat_reopen() {
  M dg reopen D01 \
    --why "A sponsor donated cluster time on 2026-03-18. The falsifier named this exact event: the GPU budget appeared." \
    --yes
  M dg apply
}

# ---- three agents composing at once --------------------------------------
#
# Played twice by scene 2 — once with nobody named and once with everybody —
# and the commands are identical both times. The only difference is whether the
# harness set `$DG_AGENT` before it launched them.

beat_a_composes() {
  A dg decide D01 \
    --answer "A trained net, 40 MB. Six weeks of donated cluster time buys more strength than a year of hand-tuning, and the tuning had stalled twice." \
    --source bench/net-vs-handtuned.md \
    --falsifier "the net fails to beat the hand-tuned build by 30 Elo after a full training run"
}

beat_b_composes() {
  B dg decide D02 \
    --answer "SPRT against the previous build, 20k games on the volunteer cluster. Every weight has a name and a human can explain it, so a regression is debuggable by reading the diff." \
    --source notes/sprt.md \
    --falsifier "a weight change stops being reviewable by reading it"
}

# C reasons from `D01` **as the store still has it** — hand-tuned weights, 71 KB
# of them, measured by `T02`. That is not carelessness: it is the only reading
# of the premise that exists when C composes, and the graph says so. What makes
# it stale is A's answer landing afterwards, which is scene 4.
beat_c_composes() {
  C dg decide D03 \
    --answer "One binary, no runtime files. The weights compile in as a generated header — 412 KB at -Os, so there is nothing to download and nothing to version separately." \
    --source bench/size.md \
    --falsifier "the weights outgrow what a header can carry, or a release ever needs a second file"
}

beat_all_three_compose() {
  beat_a_composes
  beat_b_composes
  beat_c_composes
}

# ---- the day, replayed into a clone --------------------------------------
#
# Scene 6 opens on the graph the first five scenes built, not on a fixture that
# resembles it: `D01` reopened and answered at 40 MB, `D02` settled, `D03`
# settled on the stale reading and reopened when its falsifier fired. Pushed, so
# the other clones start from it too.
#
# Written as a replay rather than as a second `decisions.json` for the reason
# `demo.sh` gives about rebuilding: a fixture drifts from the story silently,
# and a replay cannot.
beat_the_day_so_far() { # beat_the_day_so_far <dir>
  local d=$1 keep_M=$M_DIR keep_A=$A_DIR keep_B=$B_DIR keep_C=$C_DIR
  M_DIR=$d; A_DIR=$d; B_DIR=$d; C_DIR=$d
  silently beat_reopen
  silently beat_c_composes
  silently beat_a_composes
  silently A dg apply --mine
  silently beat_b_composes
  silently B dg apply --mine
  silently C dg apply --mine
  silently C dg reopen D03 --why "its falsifier fired: D01 moved to a 40 MB net" --yes
  silently C dg apply --mine
  M_DIR=$keep_M; A_DIR=$keep_A; B_DIR=$keep_B; C_DIR=$keep_C
  git_commit "$d" "the day's work: D01 reopened and re-answered, D02 settled, D03 reopened"
  push "$d"
}
