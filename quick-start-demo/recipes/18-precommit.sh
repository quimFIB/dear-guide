#!/usr/bin/env bash
# q: How do I say in advance what would settle a question, finish a task, or overturn an answer?
# part: annex
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

# notelit is imaginary, so the benchmark the recipe runs is a stand-in: a
# script that writes the figure the story needs. What matters is that it is
# a script, that it leaves a file, and that the graph never names it.
bench_script() {
  mkdir -p bench tests
  printf 'from dgraph.testing import *  # noqa: F401,F403\n' > tests/test_graph.py
  cat > bench/search.sh <<'SH'
#!/usr/bin/env bash
# Time a search over the 50k-note set; append the p95 to bench/search.md.
# (notelit is imaginary, so this stands in for timing it: 340 ms with the
# trigram index, 60 ms with the prefix index.)
index=${NOTELIT_INDEX:-trigram}
case $index in trigram) ms=340 ;; *) ms=60 ;; esac
printf 'p95: %s ms  %s\n' "$ms" "$index" >> bench/search.md
SH
}

rule="Time a search over the 50k-note set. p95 under 100 ms: search as you type. Over: search on Enter."

quick() {
  fresh
  bench_script
  note "A question one number will settle, born with its rule: which side of 100 ms picks which answer."
  run dg add --id D09 --area search --title "Does search run on every keystroke?" --after D02 --rule "$rule"
  run dg apply
  run dg node D09
  note "The measurement, as work: born with its definition of done, and linked as evidence for the question."
  run dg task add --id T12 --area search --title "Time a search over the 50k-note set" \
    --evidence-for D09 --done-when "the p95 over 50k notes, written to bench/search.md"
  run dg apply
  note "The benchmark: a script in the project, which leaves a file. The graph names neither."
  run cat bench/search.sh
  run bash bench/search.sh
  run cat bench/search.md
  note "The definition of done is read back before the outcome is asked for."
  run dg task done T12 --outcome "p95 340 ms on 50k notes"
  run dg apply
  note "The rule, beside the outcome of its evidence. Nothing here judges: the verdict is the command you run next."
  run dg probe D09
  note "The rule again, above the answer, at the one moment it could be bent. The answer brings the third pre-commitment, a falsifier: what would overturn it."
  run dg decide D09 \
    --answer "No: search runs on Enter. At 340 ms a keystroke would stutter." \
    --source T12 \
    --falsifier "p95 under 100 ms on the 50k-note set"
  run dg apply
}

full() {
  fresh
  bench_script
  run dg add --id D09 --area search --title "Does search run on every keystroke?" --after D02 --rule "$rule"
  run dg task add --id T12 --area search --title "Time a search over the 50k-note set" \
    --evidence-for D09 --done-when "the p95 over 50k notes, written to bench/search.md"
  run dg apply
  run bash bench/search.sh
  run dg task done T12 --outcome "p95 340 ms on 50k notes"
  run dg apply
  note "The falsifier, and beside it a typed twin: a pattern over the bench file, for a domain called grep to judge. The graph carries the file and the pattern; that grep runs was the domain author's choice."
  run dg decide D09 \
    --answer "No: search runs on Enter. At 340 ms a keystroke would stutter." \
    --source T12 \
    --falsifier "p95 under 100 ms on the 50k-note set" \
    --probe '{"kind": "grep.matches", "args": {"file": "bench/search.md", "pattern": "^p95: [0-9][0-9]? ms"}}'
  run dg apply
  note "Judged, this time: no line of the file has a p95 of one or two digits, so the falsifier holds."
  run dg probe D09
  note "The world moves. A prefix index lands, and the same script runs again."
  run env NOTELIT_INDEX=prefix bash bench/search.sh
  run cat bench/search.md
  note "The falsifier came true, and the door says so — and does nothing else. The act it calls for is yours."
  run dg probe D09
  note "The same verdict where a benchmark job would meet it: one test file, one line, and the run fails naming the decision. This much runs without you; the reopen still does not."
  run cat tests/test_graph.py
  run env CI=1 pytest -q --tb=no --decision-graph-probe -k probe
  run dg reopen D09 --why "bench/search.md: p95 60 ms with the prefix index, which is the falsifier" -y
  run dg apply
  note "Open again, the question can carry a typed rule too; and a probe is appended, never edited, so a criterion rewritten to fit the evidence stays visible as one."
  run dg reprobe D09 --probe '{"kind": "prose.rule", "args": {"text": "p95 under 100 ms: as you type; else on Enter"}}'
  run dg reprobe D09 --probe '{"kind": "prose.rule", "args": {"text": "p95 under 100 ms on the slowest team laptop: as you type; else on Enter"}}'
  run dg apply
  run dg node D09
  note "The shape is checked at the door, before any domain is asked."
  run dg reprobe D09 --probe '{"kind": "nodot", "args": {}}'
  note "A kind no installed domain claims is presented, not evaluated, and never an error: a plain install must still read a graph a richer one wrote."
  run dg reprobe D09 --probe '{"kind": "pytest.passes", "args": {"node": "tests/test_search.py::test_p95_under_100ms"}}'
  run dg apply
  run dg probe D09
}

layer "$@"
