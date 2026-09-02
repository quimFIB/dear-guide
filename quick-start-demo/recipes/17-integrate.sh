#!/usr/bin/env bash
# q: A colleague worked on a clone. How do I bring their graph in?
# part: extra
source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"

quick() {
  fresh
  quietly git clone -q . ../notelit-colleague
  ( cd ../notelit-colleague && quietly dg add --id D09 --area sync --title "Which files does sync ignore?" --after D08 \
      && quietly dg apply && quietly dg render && quietly git commit -qam "ask D09" )
  quietly git remote add colleague ../notelit-colleague
  quietly git fetch -q colleague
  run dg integrate colleague/master
  run dg incoming
  run dg incoming --adopt
  run dg pending
  run dg apply
}

full() {
  fresh
  quietly git clone -q . ../notelit-colleague
  note "Both clones answer D08, differently."
  ( cd ../notelit-colleague && quietly dg decide D08 --answer "notelit syncs nothing; the folder is the user's problem." --source discussion --falsifier "a conflict notelit caused" \
      && quietly dg apply && quietly dg render && quietly git commit -qam "settle D08" )
  run dg decide D08 --answer "A per-machine journal, merged on open." --source "findings/T08.md" --falsifier "the journal needs a migration"
  run dg apply
  run git commit -qam "settle D08 here"
  quietly git remote add colleague ../notelit-colleague
  quietly git fetch -q colleague
  run dg integrate colleague/master
  run dg incoming
}

layer "$@"
