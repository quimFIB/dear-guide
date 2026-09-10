#!/usr/bin/env bash
# q: An answer rests on two measurements. How do we notice when either one slips?
# part: annex
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

# notelit is imaginary, so nothing here times a search: `nightly` writes the
# two numbers the story needs, one file per machine, as the nightly benchmark
# would. What matters is that each result is a file, and that the graph names
# the files and never a command.
nightly() {
  mkdir -p bench
  printf 'p95: %s ms\n' "$1" > bench/server.md
  printf 'p95: %s ms\n' "$2" > bench/laptop.md
}

answer="Yes: every keystroke runs a search. Both machines answer in under 100 ms."
falsifier="a search p95 of 100 ms or more on the build server, or on the slowest team laptop"
# One half per file: it fires on a p95 of three digits or more, which is the
# falsifier.
slow() { printf '{"kind": "grep.matches", "args": {"file": "bench/%s.md", "pattern": "^p95: [0-9]{3,} ms"}}' "$1"; }
either="{\"kind\": \"core.all_of\", \"args\": {\"probes\": [$(slow server), $(slow laptop)]}}"

add_d09() {
  run dg add --id D09 --area search --title "Does search run as you type?" --after D02
  run dg decide D09 --answer "$answer" \
    --source "bench/server.md, bench/laptop.md" \
    --falsifier "$falsifier" --probe "$either"
}

quick() {
  fresh
  quietly nightly 60 85
  note "Last night's benchmarks, one file per machine. p95 is the time within which 95 searches in 100 finished."
  run head bench/server.md bench/laptop.md
  note "The decision, with its falsifier in words and beside it a probe: the same condition as a check. Each half asks one file whether it shows 100 ms or more, and core.all_of joins the halves, so the answer holds only while both hold."
  add_d09
  run dg apply
  note "Run after the benchmarks. Both machines are fast, so the answer holds."
  run dg probe D09
  note "Three weeks later notelit starts ranking results by how recently each note changed, and the benchmarks run again."
  quietly nightly 70 140
  run head bench/server.md bench/laptop.md
  note "The server is still fast. The laptop is not, and one machine is enough: the probe names it and exits non-zero, which fails the nightly job. Reopening D09 is still a person's call."
  run dg probe D09
}

full() {
  fresh
  quietly nightly 60 85
  add_d09
  note "A second answer resting on two things, and only one of them is a file. Ranking by recency stands while the laptop stays under 100 ms and the ranking test keeps passing, and that test needs a pytest domain this machine does not have."
  run dg add --id D10 --area search --title "Are results ranked by how recently a note changed?" --after D09
  run dg decide D10 --answer "Yes: recently changed notes first." \
    --source "bench/laptop.md, tests/test_rank.py" \
    --falsifier "a search p95 of 100 ms or more on the slowest team laptop, or the recency ranking test failing" \
    --probe "{\"kind\": \"core.all_of\", \"args\": {\"probes\": [$(slow laptop), {\"kind\": \"pytest.fails\", \"args\": {\"node\": \"tests/test_rank.py::test_recent_first\"}}]}}"
  run dg apply
  note "On every commit, only the cheap checks: the ones the grep domain runs. D10 comes up too, because half of it is a grep and a probe is one condition. Its other half nobody here can check, so D10 is unjudged, never passed, and the missing domain is named once at the bottom."
  run dg probe --domain grep
  note "In the nightly job, where the test plugin would be installed, only the checks that need it. Here it is not, so D10 stays unjudged."
  run dg probe --domain pytest
  note "A domain that hangs must not hang the job. Each one runs in its own process under a deadline: grep declares five seconds, the default is sixty, and --timeout overrides both."
  run dg probe D09 --timeout 0.001
  note "A script that names a domain nothing uses, or passes an empty name because a variable was unset, is refused rather than run as check-nothing or check-everything."
  run dg probe --domain rocq
  run dg probe --domain ''
}

layer "$@"
