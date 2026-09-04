#!/usr/bin/env bash
# q: How do I judge one criterion against two things, and run only the domains I want?
# part: annex
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

# Two measurements, one script. As in recipe 18 notelit is imaginary, so the
# benchmark stands in for timing it: the trigram index, then the prefix index
# that lands halfway through the story. What matters is that each number goes
# to its own file, and that the graph names the files and never the script.
bench_script() {
  mkdir -p bench
  cat > bench/run.sh <<'SH'
#!/usr/bin/env bash
# Time a search over the 50k-note set, and on the slowest team laptop.
# Append each p95 to its own file. (340 ms and 512 ms with the trigram index;
# 60 ms and 240 ms with the prefix index.)
index=${NOTELIT_INDEX:-trigram}
case $index in trigram) fast=340; slow=512 ;; *) fast=60; slow=240 ;; esac
printf 'p95: %s ms  %s\n' "$fast" "$index" >> bench/search.md
printf 'p95: %s ms  %s\n' "$slow" "$index" >> bench/laptop.md
SH
}

answer="No: search runs on Enter. Neither machine is under 100 ms."
falsifier="p95 under 100 ms on the 50k-note set, or on the slowest team laptop"
# One criterion, two halves, as one probe. Written on one line because that is
# how it would be typed, and the transcript shows what was typed.
member() { printf '{"kind": "grep.matches", "args": {"file": "bench/%s.md", "pattern": "^p95: [0-9][0-9]? ms"}}' "$1"; }
both="{\"kind\": \"core.all_of\", \"args\": {\"probes\": [$(member search), $(member laptop)]}}"

quick() {
  fresh
  bench_script
  note "The answer rests on two measurements, each in its own file."
  run cat bench/run.sh
  run bash bench/run.sh
  run head -n 3 bench/search.md bench/laptop.md
  note "One criterion, two halves: the answer stands while both numbers stay above 100 ms. So the probe is one too — core.all_of, the one kind the core evaluates itself, holding only if every member holds."
  run dg add --id D09 --area search --title "Does search run on every keystroke?" --after D02
  run dg decide D09 --answer "$answer" \
    --source "bench/search.md, bench/laptop.md" \
    --falsifier "$falsifier" --probe "$both"
  run dg apply
  note "Judged, and the sentence is both halves': a composite that holds is every member holding."
  run dg probe D09
  note "The prefix index lands. The 50k set comes under 100 ms; the laptop does not."
  run env NOTELIT_INDEX=prefix bash bench/run.sh
  run head -n 3 bench/search.md bench/laptop.md
  note "One half is enough — and the verdict says which half it was."
  run dg probe D09
}

full() {
  fresh
  bench_script
  quietly bash bench/run.sh
  run dg add --id D09 --area search --title "Does search run on every keystroke?" --after D02
  run dg decide D09 --answer "$answer" \
    --source "bench/search.md, bench/laptop.md" \
    --falsifier "$falsifier" --probe "$both"
  note "A second question, whose rule needs a domain this machine does not have — half of it, at least."
  run dg add --id D10 --area search --title "Is the prefix index the default?" --after D09 \
    --rule "the laptop under 100 ms, and the search tests passing" \
    --probe "{\"kind\": \"core.all_of\", \"args\": {\"probes\": [$(member laptop), {\"kind\": \"pytest.passes\", \"args\": {\"node\": \"tests/test_search.py::test_default\"}}]}}"
  run dg apply
  note "Both records, and one footer line: a prefix nothing installed claims is said once, not once per record. A member nobody can judge leaves the composite unjudged — never an error, and never a holds it did not earn."
  run dg probe --all
  note "--domain runs only what one domain judges: how to run the cheap ones, or reach a slow one by name. A composite is one criterion, so it is reached under every member's prefix — grep finds both records, pytest the one whose other half it would judge."
  run dg probe --domain grep
  run dg probe --domain pytest
  note "Each domain gets a child process of its own and its own deadline — grep declares five seconds, the default is sixty — and --timeout at the door overrides whatever a distribution declared. A domain that has not answered by then is unjudged for everything it was asked."
  run dg probe D09 --timeout 0.001
  note "A scope that names nothing the store holds is refused, saying what it does hold — and a blank is a selection that matched nothing, never everything."
  run dg probe --domain rocq
  run dg probe --domain ''
}

layer "$@"
