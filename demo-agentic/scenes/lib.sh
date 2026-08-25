# Shared plumbing for the five scenes. Sourced, never run.
#
# Two things it is responsible for, and both are about the reader rather than
# about correctness:
#
# - **Whose turn it is has to be visible.** The demo's whole subject is an
#   interleaving, so every command is printed under the agent that ran it,
#   before its output. `A` and `B` are that, and they are the only way a scene
#   is allowed to run `dg`.
# - **A scene has to be readable top to bottom as the order things happened.**
#   That is why the agents are not two scripts in `agents/`, which was the
#   first plan: two files would put A's line and B's line on different pages
#   and hide the one thing worth seeing.

set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

# ---- the work directory --------------------------------------------------
#
# Scenes need clones built from scratch each run, which means removing
# directories — the one thing `demo/demo.sh` deliberately never does. So the
# work directory is claimed before anything is removed: a marker file is
# written into it, and the marker is only written into a directory that is
# either ours already or empty. `$DG_DEMO_DIR=$HOME` therefore stops the demo
# instead of clearing four names out of a home directory, and every `rm -rf`
# below is a fixed name under a directory that has proved it is ours.

work=${DG_DEMO_DIR:-${TMPDIR:-/tmp}/dg-demo-agentic}
marker=.dg-demo-agentic

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

# ---- narration -----------------------------------------------------------

# Colour only when a terminal is going to interpret it. The transcript is
# routinely piped — into `less`, into a file, into a test — and escape codes in
# a captured transcript are noise that outlives the session.
if [ -t 1 ]; then
  bold=$(tput bold 2>/dev/null || true); off=$(tput sgr0 2>/dev/null || true)
  dim=$(tput dim 2>/dev/null || true)
  hueA=$(tput setaf 4 2>/dev/null || true); hueB=$(tput setaf 5 2>/dev/null || true)
  hueC=$(tput setaf 2 2>/dev/null || true); hueM=$(tput setaf 3 2>/dev/null || true)
else
  bold=""; off=""; dim=""; hueA=""; hueB=""; hueC=""; hueM=""
fi

scene() { printf '\n%s%s%s\n%s\n' "$bold" "$*" "$off" \
                 "$(printf '─%.0s' $(seq 1 ${#1}))"; }

# Prose is wrapped where it is written, not here: a scene's narration is edited
# as prose and a re-flow at print time would fight the author over where the
# line breaks fall.
say()   { printf '\n%s\n' "$*"; }

# `A cmd…` / `B cmd…` — run a command as that agent, in that agent's directory,
# printing it first. Output is indented so the transcript reads as a session.
# What the agent typed, quoted so it could be retyped. `printf %q` would escape
# every space in a sentence-long `--answer`, which is unreadable; a plain pair of
# quotes round any argument that needs one is what a person would have written.
_typed() {
  local out="" arg
  for arg in "$@"; do
    case $arg in
      *[!A-Za-z0-9./_=:,-]*) out+=" '${arg//\'/\'\\\'\'}'" ;;
      *)                    out+=" $arg" ;;
    esac
  done
  printf '%s' "${out# }"
}

_run() {
  local who=$1 colour=$2 dir=$3; shift 3
  if [ -n "${QUIET-}" ]; then ( cd "$dir" && "$@" >/dev/null 2>&1 ) || true; return; fi
  printf '\n%s%s ▸ %s%s\n' "$colour" "$bold" "$who" "$off"
  printf '%s  $ %s%s\n' "$dim" "$(_typed "$@")" "$off"
  ( cd "$dir" && "$@" 2>&1 ) | sed 's/^/  /' || true
}

# The three agents, and the maintainer who launched them. `DG_AGENT` is what
# makes a shared tray safe to work in: every op it stages records who staged it,
# so nobody applies anybody else's half-written answer. The maintainer sets
# nothing, which is what makes them the supervisor — an unowned `dg apply` takes
# the whole tray, and is refused when it holds work an agent staged.
A() { DG_AGENT=${A_AS-A} _run "agent A · Core"    "$hueA" "$A_DIR" "$@"; }
B() { DG_AGENT=${B_AS-B} _run "agent B · Tooling" "$hueB" "$B_DIR" "$@"; }
C() { DG_AGENT=${C_AS-C} _run "agent C · Release" "$hueC" "$C_DIR" "$@"; }
M() { _run "the maintainer" "$hueM" "$M_DIR" "$@"; }

# The same agents with nobody named — the configuration a harness gets by
# forgetting, and what scene 2 exists to show the cost of.
anonymous() { A_AS=""; B_AS=""; C_AS=""; }
named()     { unset A_AS B_AS C_AS; }

# The same, without the banner — for setup steps that are not part of the story.
quietly() { ( cd "$1" && shift && "$@" >/dev/null 2>&1 ); }

# Identity is set per clone, not per commit, and that is not tidiness. `git
# pull` writes a merge commit of its own in scene 5, and it looks the identity
# up itself -- so a machine with no `user.email` configured globally had the
# scene die inside the pull, before the conflict it exists to show. Whoever
# runs this demo does not have to have configured git.
identify() { # identify <dir> <name> <email-local-part>
  quietly "$1" git config user.name  "$2"
  quietly "$1" git config user.email "$3@example.invalid"
}

git_commit() { # git_commit <dir> <message>
  quietly "$1" git add -A
  quietly "$1" git commit -m "$2"
}

# ---- building the project ------------------------------------------------
#
# `base` is a normal repo holding the store; `origin.git` is cloned from it so
# that the agents' clones have a remote to race through. Built fresh every run,
# so a scene never inherits the last one's graph.

build_base() {
  claim_work
  rm -rf "$work/base" "$work/origin.git" "$work/shared" \
         "$work/A" "$work/B" "$work/C"
  mkdir -p "$work/base"
  cp -f "$here/decisions.json" "$here/tasks.json" "$work/base/"
  cp -f "$here/gitignore.txt" "$work/base/.gitignore"
  quietly "$work/base" git init -q -b main .
  identify "$work/base" "the team" team
  quietly "$work/base" dg render
  quietly "$work/base" dg task render
  git_commit "$work/base" "the graph as both agents found it"
  # Beside the clones rather than inside one: scene 5 runs it from an agent's
  # working copy, and a demo prop committed into the project being demoed would
  # show up in every diff the scene prints.
  cp -f "$here/scenes/union.py" "$work/union.py"
  quietly "$work" git clone -q --bare base origin.git
}

# One checkout, everybody in it. What a fan-out gets by default: the agents
# were launched in the maintainer's working directory, because that is where the
# problem is.
one_checkout() {
  build_base
  quietly "$work" git clone -q origin.git shared
  identify "$work/shared" "the project" project
  A_DIR="$work/shared"; B_DIR="$work/shared"
  C_DIR="$work/shared"; M_DIR="$work/shared"
  named
}

# A clone each. What a harness with worktree isolation gives instead, and it
# trades one set of problems for another rather than removing them.
own_clones() {
  build_base
  for who in A B C; do
    quietly "$work" git clone -q origin.git "$who"
    identify "$work/$who" "agent $who" "agent-$who"
  done
  A_DIR="$work/A"; B_DIR="$work/B"; C_DIR="$work/C"; M_DIR="$work/A"
  named
}

# Backwards-compatible names for the two shapes above.
one_project() { one_checkout; }
two_clones()  { own_clones; }

# Replay an earlier beat with its output suppressed, so a scene run on its own
# still starts from the state the story left it in. `demo.sh 4` and
# `demo.sh all` therefore show the same scene 4, which is the whole reason the
# beats are functions rather than prose inlined in six files.
silently() { QUIET=1 "$@"; }

push()  { quietly "$1" git push -q origin HEAD:main; }
pull()  { quietly "$1" git pull -q --no-rebase origin main; }
