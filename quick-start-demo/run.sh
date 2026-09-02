#!/usr/bin/env bash
# Run every recipe (or the ones named), capturing each layer's transcript and
# a snapshot of both stores after every command, into out/. Then build the page.
#
#   ./run.sh              every recipe, then build
#   ./run.sh 03 11        two recipes, then build
#   ./run.sh --no-build   transcripts only
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
build=1; picks=()
for a in "$@"; do case $a in --no-build) build=0 ;; *) picks+=("$a") ;; esac; done

for r in "$here"/recipes/*.sh; do
  name=$(basename "$r" .sh)
  if [ ${#picks[@]} -gt 0 ]; then
    hit=0; for p in "${picks[@]}"; do case $name in $p*) hit=1 ;; esac; done
    [ $hit -eq 1 ] || continue
  fi
  for layer in quick full; do
    snap="$here/out/$name.$layer"
    rm -rf "$snap"; mkdir -p "$snap"
    printf '%-28s %-5s ' "$name" "$layer"
    if SNAP="$snap" bash "$r" "$layer" > "$here/out/$name.$layer.txt" 2>&1; then
      printf '%s lines, %s snapshots\n' "$(wc -l < "$here/out/$name.$layer.txt")" "$(ls "$snap" | wc -l)"
    else
      printf 'FAILED — see out/%s.%s.txt\n' "$name" "$layer"; exit 1
    fi
  done
done
[ $build -eq 1 ] && python3 "$here/build.py"
exit 0
