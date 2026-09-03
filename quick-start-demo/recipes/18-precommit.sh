#!/usr/bin/env bash
# q: How do I write down what would settle a question — in a form a machine could read?
# part: annex
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  note "An open question born with its rule for settling: prose, and a typed twin beside it."
  run dg add --id D09 --area sync --title "Which edit wins when two machines change one note?" --after D08 \
    --rule "a week of the team's own notes syncing with no edit lost" \
    --probe '{"kind": "prose.rule", "args": {"text": "seven days, zero lost edits"}}'
  run dg apply
  note "Work born with its definition of done, as evidence for that question."
  run dg task add --id T12 --area sync --title "Run two machines against one folder for a week" \
    --evidence-for D09 --done-when "seven days, zero lost edits, the log in bench/sync-week.md"
  run dg apply
  note "Every pre-commitment, beside what it will be judged against. Nothing judges it: the verdict is the command you run next."
  run dg probe D09 T12
}

full() {
  fresh
  run dg add --id D09 --area sync --title "Which edit wins when two machines change one note?" --after D08 \
    --rule "a week of the team's own notes syncing with no edit lost" \
    --probe '{"kind": "prose.rule", "args": {"text": "seven days, zero lost edits"}}'
  run dg apply
  note "A rule is appended, never edited: the earlier one stays, dated, so a rule rewritten to fit the evidence is visible as one."
  run dg reprobe D09 --probe '{"kind": "prose.rule", "args": {"text": "seven days, zero lost edits, on three machines"}}'
  run dg apply
  note "What the question is about, in some domain's terms. An address, not a claim; it accumulates like an edge."
  run dg bind D09 notelit.module:sync
  run dg apply
  run dg node D09
  note "The falsifier's twin travels with the answer, and is archived with it if the answer is ever reopened."
  run dg decide D08 \
    --answer "The notes folder is synced by whatever the user already uses; notelit never syncs." \
    --source "discussion" \
    --falsifier "a user asks for notelit to resolve a conflict it did not cause" \
    --probe '{"kind": "prose.falsifier", "args": {"text": "a conflict notelit did not cause"}}'
  run dg apply
  note "The shape is checked at the door, before anything is asked for."
  run dg reprobe D09 --probe '{"kind": "nodot", "args": {}}'
  note "A kind no installed domain claims is presented, not evaluated, and never an error: a plain install must still read a graph written by a richer one."
  run dg reprobe D09 --probe '{"kind": "rocq.no_admit", "args": {"lemma": "sync_merge_total"}}'
  run dg apply
  run dg probe --area sync
  note "A reopened premise: every decision resting on it is presented beside what moved, by the dates, labelled as the heuristic it is."
  run dg reopen D02 --why "the trigram tokenizer misses CJK notes entirely" --yes
  run dg apply
  run dg probe --provisional
  note "The check never evaluates a probe. Its verdict is a function of the store alone, which is what keeps a commit hook honest on a machine with nothing installed."
  run dg check
}

layer "$@"
