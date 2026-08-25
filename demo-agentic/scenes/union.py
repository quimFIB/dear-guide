"""Resolve a conflicted `decisions.json` as the union of both sides.

Written once so scene 5 can run unattended. **It is not a merge driver and
should not become one** — a real resolution is a person or an agent reading
both sides and deciding, and the interesting cases (two answers to one
question, a reopen against a close) have no union. What this handles is the
case scene 5 actually produces: two additions that do not overlap.

The point of the scene survives either way, because the acceptance test is not
this script. It is `dg check`, run afterwards, over whatever came out.
"""

import json
import subprocess
import sys


def side(stage: int) -> dict:
    """One side of the conflict, read from the index rather than the worktree —
    the worktree copy has conflict markers in it and is not JSON at all."""
    out = subprocess.run(["git", "show", f":{stage}:decisions.json"],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def main() -> int:
    ours, theirs = side(2), side(3)

    have = {v["id"] for v in ours["vertices"]}
    ours["vertices"] += [v for v in theirs["vertices"] if v["id"] not in have]

    # Both sides added a target to the same edge's `to` list; take both. Edges
    # are matched on source and liveness, which is enough here and is exactly
    # the assumption that makes this a scene prop rather than a merge driver.
    for a in ours["edges"]:
        for b in theirs["edges"]:
            if a.get("from") == b.get("from") and a.get("active") == b.get("active"):
                a["to"] = sorted(set(a.get("to", [])) | set(b.get("to", [])))

    with open("decisions.json", "w", encoding="utf-8") as fh:
        json.dump(ours, fh, indent=2)
        fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
