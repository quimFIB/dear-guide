#!/usr/bin/env bash
# Builds the seed project — notelit, a CLI that indexes a folder of markdown
# notes — from an empty directory, using only `dg`. The result is copied to
# seed/, which every recipe starts from.
#
# It is a script rather than a hand-written JSON so that the seed is itself a
# worked example: every record in it got there through a command a user
# could type. Re-run it to regenerate seed/ after a change; the dates it
# stamps are the day it ran.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
fresh_empty
d() { dg "$@" >/dev/null || die "seed: dg $* failed"; }

d init
d task init

# ---- storage ---------------------------------------------------------------
d add --id D01 --area storage --title "What is the note store format?" \
  --note "Plain files, or a database the CLI owns. Everything else rests on this."
d add --id D02 --area search  --title "How are notes indexed for search?" --after D01
d add --id D05 --area ux      --title "Does the CLI ship a TUI?" --after D01
d add --id D08 --area sync    --title "How do two machines share one notes folder?" --after D01 \
  --note "D01's falsifier already names this case."
d apply
d decide D01 \
  --answer "Plain markdown files on disk, one note per file. The folder is the database." \
  --source "discussion" \
  --falsifier "a user needs attachments larger than 10 MB inside a note, or one note is edited from two machines at once" \
  --opens D02,D05,D08
d apply

# ---- search, first answer ----------------------------------------------------
d add --id D03 --area search  --title "Which embedding model backs semantic search?" --after D02
d add --id D04 --area storage --title "Where does the index live?" --after D02
d apply
d task add --id T01 --area storage --title "Write the file-store loader" --because D01
d task add --id T02 --area search  --title "Benchmark FTS5 on 50k synthetic notes" \
  --evidence-for D02 --after T01
d task add --id T05 --area storage --title "Fix path escaping noticed while writing the loader" \
  --discovered-during T01
d apply
d task start T01; d apply
d task done T01 --outcome "src/store/files.py, PR #12"; d apply
d task start T02; d apply
d task done T02 --outcome "bench/fts5.md — p95 41 ms at 50k notes, one table per notebook"; d apply
d decide D02 \
  --answer "SQLite FTS5, one virtual table per notebook." \
  --source "bench/fts5.md" \
  --falsifier "p95 query latency over 200 ms at 50k notes" \
  --opens D03,D04
d apply
d decide D04 \
  --answer "Beside the notes, in .notelit/index.db. Rebuilt on demand, never synced." \
  --source "discussion" \
  --falsifier "users sync the notes folder and the index file causes conflicts"
d apply
d add --id D06 --area storage --title "What happens when the index is corrupted?" --after D04
d apply
d decide D05 \
  --answer "No. One command per question, plain text out; pipes are the interface." \
  --source "discussion" \
  --falsifier "a second interactive prompt appears in the codebase"
d apply

# ---- search, reversed --------------------------------------------------------
d reopen D02 --why "p95 hit 340 ms once the synthetic set had ten notebooks: one table each meant ten scans a query" -y
d apply
d decide D02 \
  --answer "SQLite FTS5 with a trigram tokenizer, one table for every notebook." \
  --summary "FTS5, trigram tokenizer, one table" \
  --source "bench/fts5-trigram.md" \
  --falsifier "p95 over 200 ms at 100k notes" \
  --opens D03,D04
d apply
d confirm D04; d apply

# ---- the rest of the work ----------------------------------------------------
d add --id D07 --area search --title "How is ranking tuned once embeddings exist?" --after D03
d apply
d task add --id T03 --area storage --title "Wire a file watcher for incremental reindex" \
  --because D04 --after T01
d task add --id T04 --area storage --title "Detect a corrupted index on open" \
  --evidence-for D06 --after T01
d task add --id T06 --area search  --title "Compare three embedding models on the 50k set" \
  --evidence-for D03 --after T02
d task add --id T07 --area ux      --title "Add --json to every search command" \
  --because D05 --after T06
d task add --id T08 --area sync    --title "Write the sync section of the README" \
  --because D08
d task add --id T09 --area storage --title "Rebuild the index automatically when it is corrupt" \
  --because D06 --after T04
d task add --id T10 --area search  --title "Run the benchmark in CI on every merge" --because D02
d task add --id T11 --area ux      --title "Resolve wikilinks in search output"
d apply
d task start T03; d apply
d task drop T03 --why "polling every 2 s is enough, and the watcher crashed on network mounts (upstream #88)"
d apply
d task start T06; d apply
d task park T10 --why "the CI minutes budget is spent until Q4"; d apply
d render; d task render

# Give the project a history. Every record above was stamped with today's date;
# a seed with a past is what lets the recipes show an answer that was settled
# months before its evidence arrived. The dates are the only hand-written data.
python3 - <<'PY'
import json
dates = {"D01": "2026-05-04", "D05": "2026-05-20", "D02:old": "2026-06-03",
         "D04": "2026-06-15", "D02": "2026-07-10",
         "T01": "2026-05-12", "T02": "2026-06-02", "T03": "2026-07-01", "T10": "2026-08-20"}
g = json.load(open("decisions.json"))
for e in g["edges"]:
    if "date" in e:
        e["date"] = dates[e["from"] if e.get("active") else e["from"] + ":old"]
json.dump(g, open("decisions.json", "w"), indent=1); open("decisions.json", "a").write("\n")
t = json.load(open("tasks.json"))
for x in t["tasks"]:
    for k in ("completions", "stops"):
        for entry in x.get(k, []):
            entry["date"] = dates[x["id"]]
json.dump(t, open("tasks.json", "w"), indent=1); open("tasks.json", "a").write("\n")
PY
d check
d render; d task render
cp decisions.json tasks.json "$seed/"
printf 'seed written to %s\n' "$seed"
