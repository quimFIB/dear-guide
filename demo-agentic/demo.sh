#!/usr/bin/env bash
# The five scenes, run against a throwaway copy of demo-agentic/'s graph.
#
#   ./demo-agentic/demo.sh        every scene, in order
#   ./demo-agentic/demo.sh 3      one scene, on its own
#
# The six scenes are one continuous day, and each one is also readable cold:
# a scene replays the beats before it with their output suppressed (see
# `scenes/story.sh`), so `demo.sh 4` opens on the state scene 3 really left
# rather than on a fixture that resembles it. Nothing outside the work
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
  all) scenes=(1 2 3 4 5 6) ;;
  [1-6]) scenes=("$want") ;;
  *) echo "usage: $(basename "$0") [1|2|3|4|5|6]" >&2; exit 2 ;;
esac

if [ "$want" = all ]; then
  cat <<'TXT'

  One hard question, three agents, one development graph.

  An imaginary open-source Go engine, three decisions deep. A sponsor donates
  cluster time -- the exact event the oldest decision's falsifier named -- so
  where the evaluation weights come from is back in play, and it touches Core,
  Tooling and Release at once. Too much for one pass, so the maintainer fans
  out three agents onto it.

  What follows is one day, in order, and each scene is a concurrency problem
  that day runs into:

    1  one hard question, three areas            the graph is the plan
    2  three agents, one staging tray            who staged what, and who may apply it
    3  three answers at once                     composition parallelises; publication is ordered
    4  the quiet one: a stale premise            <- the one a lock cannot reach
    5  two agents, one id                        twice, and they need opposite answers
    6  bringing the parallel work back           what git cannot merge, and the seam

  Scenes 2, 3, 5 and 6 end with the tool doing something about it. Scene 4 is
  the one that does not, and it is the reason the other five are worth having.

  The agents are shell scripts. There is no model here and nothing in it is a
  claim about how one behaves -- what it shows is what `dg` does when several
  writers meet, which is a property of the tool and reproducible to the line.

TXT
fi

for s in "${scenes[@]}"; do
  bash "$here/scenes/$s.sh"
done

printf '\n%s\n\n' "  Work directory: ${DG_DEMO_DIR:-${TMPDIR:-/tmp}/dg-demo-agentic}"
