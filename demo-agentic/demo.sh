#!/usr/bin/env bash
# The five scenes, run against a throwaway copy of demo-agentic/'s graph.
#
#   ./demo-agentic/demo.sh        every scene, in order
#   ./demo-agentic/demo.sh 3      one scene, on its own
#
# Every scene rebuilds the project from scratch, so none of them inherits the
# last one's graph and any one can be read cold. Nothing outside the work
# directory is touched, and the work directory has to prove it is ours before
# anything in it is removed -- see `scenes/lib.sh`.
#
# Unlike `demo/demo.sh` this does not serve. `dg serve` is a single-writer
# interface by construction and has nothing to say about any of this.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

command -v dg >/dev/null || {
  echo "dg is not on PATH -- pip install -e $(dirname "$here")" >&2; exit 1; }

want=${1:-all}
case $want in
  all) scenes=(1 2 3 4 5) ;;
  [1-5]) scenes=("$want") ;;
  *) echo "usage: $(basename "$0") [1|2|3|4|5]" >&2; exit 2 ;;
esac

if [ "$want" = all ]; then
  cat <<'TXT'

  Two agents, one development graph.

  An imaginary open-source chess engine, three decisions deep. One premise --
  where the evaluation weights come from -- with a question hanging off it in
  each of two agents' areas. Every scene is that shape plus an interleaving.

    1  the tray has no idea whose ops are whose      one project, two agents
    2  what one writer at a time actually costs      the supported path
    3  a stale premise, still legal                  <- the one to read
    4  the loud one, and the two collisions          a refusal, twice over
    5  isolation moves the race, it does not remove it

  The agents are shell scripts. There is no model here and nothing in it is a
  claim about how one behaves -- what it shows is what `dg` does when two
  writers meet, which is a property of the tool and reproducible to the line.

TXT
fi

for s in "${scenes[@]}"; do
  bash "$here/scenes/$s.sh"
done

printf '\n%s\n\n' "  Work directory: ${DG_DEMO_DIR:-${TMPDIR:-/tmp}/dg-demo-agentic}"
