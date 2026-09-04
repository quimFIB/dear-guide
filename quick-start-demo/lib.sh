# Shared plumbing for the quick-start recipes. Sourced, never run.
#
# Every recipe is two shell functions, `quick` and `full`, each starting from
# a fresh copy of the seed project and running real `dg` commands against it.
# The transcript this prints is the page's source: the HTML is built from what
# these functions printed, so the page cannot show output that did not happen.

set -uo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
seed="$here/seed"

# ---- the work directory --------------------------------------------------
#
# Recipes start from scratch each time, which means removing a directory. So
# the work directory is claimed before anything is removed: a marker is
# written into it, and only into a directory that is ours already or empty.

work=${DG_DEMO_DIR:-${TMPDIR:-/tmp}/dg-quick-start}
marker=.dg-quick-start
project="$work/notelit"

die() { printf '\n  %s\n\n' "$*" >&2; exit 1; }

claim_work() {
  if [ -e "$work" ] && [ ! -f "$work/$marker" ]; then
    [ -n "$(ls -A "$work" 2>/dev/null || true)" ] &&
      die "refusing to use $work: it already has things in it and this demo did not put them there.
  Set DG_DEMO_DIR to somewhere else."
  fi
  mkdir -p "$work"
  : > "$work/$marker"
}

# Output is captured, not read live, so width and colour are pinned: a
# transcript that wraps differently on a wider terminal is a different page.
export COLUMNS=96
export NO_COLOR=1
export TERM=dumb
# The cookbook's demo domain (`grep-domain/`, see its docstring): on the
# path, `dg probe` finds it through the `dgraph.domains` entry-point group
# as if it were installed. Recipes 18 and 20 are the ones that reach it.
export PYTHONPATH="$here/grep-domain${PYTHONPATH:+:$PYTHONPATH}"
# Nothing here is an agent unless a recipe says so.
unset DG_AGENT DG_DECIDE DG_APPLY DG_WRITE DG_TERSE DG_AREA DG_TASK DG_BUDGET DG_PROJECT
export GIT_AUTHOR_NAME=notelit GIT_AUTHOR_EMAIL=notelit@example.invalid
export GIT_COMMITTER_NAME=notelit GIT_COMMITTER_EMAIL=notelit@example.invalid

# Removal (`dg rm`) refuses outside a committed git repo — git is the only
# record of what a removal takes away — so the project is always one.
_git_commit() { ( cd "$project" && git add -A >/dev/null && git commit -qm "$1" >/dev/null ); }

# A fresh project from the seed. Every recipe starts here.
fresh() {
  claim_work
  rm -rf "$project" "$work/notelit-colleague"; mkdir -p "$project"
  cp "$seed/decisions.json" "$seed/tasks.json" "$project/"
  ( cd "$project" && git init -q && dg render >/dev/null && dg task render >/dev/null )
  _git_commit "seed"
  cd "$project"
  _snap_n=-1; _snapshot   # 00: the state before anything ran
}

# An empty directory, for the recipes that start a graph from nothing.
fresh_empty() {
  claim_work
  rm -rf "$project" "$work/notelit-colleague"; mkdir -p "$project"
  ( cd "$project" && git init -q )
  cd "$project"
  _snap_n=-1; _snapshot
}

# ---- running a command, visibly ------------------------------------------

# What was typed, quoted so it could be retyped. A plain pair of quotes round
# any argument that needs one is what a person would have written.
_typed() {
  local out="" arg
  for arg in "$@"; do
    case $arg in
      # An empty argument is the one that has to be quoted to be seen at all:
      # `--domain ''` printed bare reads as the flag with no value, which is
      # the opposite of what recipe 20 is showing (`pending.refuse_blank`).
      "")                        out+=" ''" ;;
      *[!A-Za-z0-9./_=:,-]*) out+=" '${arg//\'/\'\\\'\'}'" ;;
      *)                         out+=" $arg" ;;
    esac
  done
  printf '%s' "${out# }"
}

# After every command both stores are exported, so the page can draw the
# graph as it stood at each step. `$SNAP` is set by run.sh; a recipe run by
# hand snapshots nothing.
_snapshot() {
  [ -n "${SNAP-}" ] || return 0
  _snap_n=$((_snap_n + 1))
  local n; n=$(printf '%02d' "$_snap_n")
  [ -f decisions.json ] && dg export > "$SNAP/$n.decisions.json" 2>/dev/null
  [ -f tasks.json ]     && dg task export > "$SNAP/$n.tasks.json" 2>/dev/null
  return 0
}

# `run cmd…` prints the command, then its output, then the exit status when
# it was not zero — a refusal is a successful demonstration of a refusal, and
# the transcript has to say which it was.
run() {
  printf '$ %s\n' "$(_typed "$@")"
  local rc=0
  "$@" 2>&1 || rc=$?
  [ "$rc" -eq 0 ] || printf '[exit %s]\n' "$rc"
  _snapshot
}

# The same, as a named agent. `DG_AGENT` is what makes a shared tray safe:
# every op it stages is stamped with the name, so nobody applies anybody
# else's half-written proposal.
as() {
  local who=$1; shift
  local envs=()
  while [[ ${1-} == *=* ]]; do envs+=("$1"); shift; done
  printf '%s ▸ $ %s%s\n' "$who" "${envs[*]:+${envs[*]} }" "$(_typed "$@")"
  local rc=0
  env DG_AGENT="$who" "${envs[@]}" "$@" 2>&1 || rc=$?
  [ "$rc" -eq 0 ] || printf '[exit %s]\n' "$rc"
  _snapshot
}

# Setup that is not part of the story: no echo, no snapshot.
quietly() { "$@" >/dev/null 2>&1 || true; }

# A line of narration inside a transcript, for a step the reader should not
# miss. Kept rare: the page carries the prose.
note() { printf '# %s\n' "$*"; }

# ---- the two layers -------------------------------------------------------

layer() {
  case ${1:-both} in
    quick) quick ;;
    full)  full ;;
    both)  printf '── quick ──\n'; quick; printf '\n── full ──\n'; full ;;
    *) die "usage: $0 [quick|full]" ;;
  esac
}

# `capture VAR cmd…` — run a command whose output is a value the recipe needs
# (an agent's name), printing it the way it would be typed. The repo root is
# `$repo`, for recipes that show a file the plugin ships.
capture() {
  local var=$1; shift
  printf '$ %s=$(%s)\n' "$var" "$(_typed "$@")"
  local v; v=$("$@" 2>&1)
  printf -v "$var" '%s' "$v"
  printf '%s\n' "$v"
  _snapshot
}
repo=$(cd "$here/.." && pwd)

# `run_head N cmd…` — the same as `run`, keeping only the first N lines of
# output and saying how many followed. For a command whose whole output is a
# file the reader can open instead.
run_head() {
  local n=$1; shift
  printf '$ %s\n' "$(_typed "$@")"
  local out rc=0
  out=$("$@" 2>&1) || rc=$?
  printf '%s\n' "$out" | head -n "$n"
  local total; total=$(printf '%s\n' "$out" | wc -l)
  [ "$total" -gt "$n" ] && printf '… %s more line(s)\n' "$((total - n))"
  [ "$rc" -eq 0 ] || printf '[exit %s]\n' "$rc"
  _snapshot
}
