#!/usr/bin/env python3
"""PreToolUse: hand a Bash command to `dg gate` and translate the verdict.

Translation only. Recognising a commit inside an arbitrary shell command, and
deciding what to do about it, are `dg gate` — see `dgraph/gate.py`. Doing either
here would be a second implementation of the rule, in a file with no tests, and
every further host would add another.

**Stdlib only, and never `import dgraph`** — see `hooks/brief.py` for why.
"""

import json
import os
import shutil
import subprocess
import sys

TIMEOUT = 10

DECISION = {"deny": "deny", "ask": "ask"}


def off() -> bool:
    return os.environ.get("DG_HOOK_OFF", "").strip() not in ("", "0", "false", "no")


def main() -> int:
    if off():
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") not in (None, "Bash"):
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""

    # A fast path, not a policy. `dg gate` requires the literal token `commit`
    # in the command, so a command without that substring cannot be a commit —
    # this can never hide something the gate would have refused, and it keeps a
    # subprocess out of every unrelated Bash call. Pinned by a test that walks
    # the gate's own detection table.
    if "commit" not in command:
        return 0

    dg = shutil.which("dg")
    if dg is None:
        return 0
    try:
        r = subprocess.run(
            [dg, "gate", "--command", command, "--json"],
            cwd=payload.get("cwd") or os.getcwd(), capture_output=True,
            text=True, errors="replace", timeout=TIMEOUT,
        )
        verdict = json.loads(r.stdout) if r.returncode == 0 else None
    except Exception:
        verdict = None
    if not verdict:
        return 0

    decision = DECISION.get(verdict.get("verdict"))
    if decision is None:
        # Allow is silence. Emitting an explicit "allow" would override the
        # user's own permission rules for every git command they run — a
        # security regression dressed up as a convenience.
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": verdict.get("reason", ""),
    }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
