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
  all) scenes=(1 2 3 4 5 6 7) ;;
  [1-7]) scenes=("$want") ;;
  # The annexes are not part of the day: two agents in two *clones*, which is a
  # different set of problems and a different configuration. Kept runnable and
  # kept out of `all`, so the story reads as one thing and the extra examples
  # are there for whoever wants them.
  annex) scenes=(a1 a2) ;;
  a1|a2) scenes=("$want") ;;
  *) echo "usage: $(basename "$0") [1-7 | annex | a1 | a2]" >&2; exit 2 ;;
esac

if [ "$want" = all ]; then
  cat <<'TXT'

  Three software agents, one development graph, and a day's work.

  An imaginary open-source Go engine. The graph holds three decisions and the
  work outstanding against them, and that work is what drives every scene: the
  task graph says what is ready, an agent picks it up, doing it produces
  evidence, and evidence is what settles a question. Nobody here invents an
  answer.

  The concurrency problems arrive the way they actually do -- out of three
  agents doing real work and then having to join it up:

    1  nobody writes the plan                the queue is the assignment
    2  the work opens up                     decomposition makes the parallelism
    3  no status is updated                  and the work still joins up
    4  an answer is a piece of work          and answering frees other work
    5  three agents, one staging tray        whose op is whose
    6  one fact arrives                      and every answer is under review
    7  the two nothing prevents              a quiet agent, and a stale answer

  Scenes 1 to 4 are the loop working. 5 and 6 are what a fan-out costs and what
  the tool does about it. 7 is what it does not, which is why the rest matter.

  All seven happen in one checkout, which is what a fan-out gets by default.
  Two annexes cover the other configuration -- a clone per agent -- and are not
  part of the day:

    a1 two agents, one id                    the collision, twice, needing opposite answers
    a2 bringing two clones back together     what git cannot merge, and the seam

    ./demo-agentic/demo.sh annex

  The agents are shell scripts. There is no model here and nothing in it is a
  claim about how one behaves -- what it shows is what `dg` does when several
  writers meet, which is a property of the tool and reproducible to the line.

TXT
fi

for s in "${scenes[@]}"; do
  bash "$here/scenes/$s.sh"
done

if [ "$want" = all ]; then
  cat <<'TXT'

  That is the day. Two more, if you want them -- a clone per agent instead of
  one shared checkout, which trades the problems in scenes 5 and 6 for a
  different pair:

    ./demo-agentic/demo.sh annex

TXT
fi

printf '\n%s\n\n' "  Work directory: ${DG_DEMO_DIR:-${TMPDIR:-/tmp}/dg-demo-agentic}"
