#!/usr/bin/env bash
# q: How do I start a graph?
# part: build
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh_empty
  run dg init
  run dg task init
  run dg add --id D01 --area storage --title "What is the note store format?" \
    --note "Plain files, or a database the CLI owns. Everything else rests on this."
  run dg apply
  run dg
}

full() {
  fresh_empty
  note "A store you already have is the input format — there is no conversion step."
  cat > prepared.json <<'JSON'
{"areas": ["storage"],
 "vertices": [{"id": "D01", "title": "What is the note store format?",
               "area": "storage", "status": "OPEN", "owner": "me"}],
 "edges": []}
JSON
  run cat prepared.json
  run dg import prepared.json
  quietly sed -i 's/, "owner": "me"//' prepared.json
  run dg import prepared.json
  run dg
  run cat .gitignore
  note "And the other direction: the export round-trips, derived blocks and all."
  run dg export
}

layer "$@"
