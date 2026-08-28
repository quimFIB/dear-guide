#!/usr/bin/env python3
"""PreToolUse: hand a write's target to `dg gate --write` and translate.

Translation only, exactly like `hooks/precommit.py` beside it. Deciding where
an agent may write is `dgraph/limits.py`, reached through `dg gate`, so the
rule exists once and both hosts relay the same answer — see that module on why
the policy cannot live in a hook.

**The fast path here is `$DG_AGENT`, not a word list.** A path carries no
substring that distinguishes an in-scope write from an out-of-scope one, so
there is nothing to grep for; what there is instead is the fact that the scope
is never consulted for a supervisor. An unowned session — every ordinary use of
Claude Code — returns before it spawns anything, which is the same "a project
that never heard of the tool pays nothing" rule the other hooks follow.

**Stdlib only, and never `import dgraph`** — see `hooks/brief.py` for why.
"""

import json
import os
import shutil
import subprocess
import sys

#: Smaller than `precommit.py`'s. That gate shells out to `git` several times;
#: this one resolves two paths and compares strings, so a second is already
#: generous and the cost of waiting is paid on every single write.
TIMEOUT = 5

#: The tools whose input names a file this hook should judge, and the field the
#: path arrives in. `Read`, `Grep` and `Glob` are deliberately absent: reads are
#: never judged, and an agent that cannot read the repository it is reasoning
#: about is blindfolded rather than constrained.
#:
#: `Bash` is absent too, and that is a known limit rather than an oversight. A
#: write done with `>` or `tee` goes through `precommit.py`, which judges
#: commits and removals and does not look at redirections. Closing that would
#: mean parsing arbitrary shell for write intent — a much larger thing than
#: this, and one that fails open in more places than it closes.
WRITERS = {
    "Write": "file_path",
    "Edit": "file_path",
    "NotebookEdit": "notebook_path",
}


def say(message: str) -> None:
    """Show `message` without deciding anything. See `precommit.say`."""
    print(json.dumps({"systemMessage": message}))


def off() -> bool:
    return os.environ.get("DG_HOOK_OFF", "").strip() not in ("", "0", "false", "no")


def main() -> int:
    if off():
        return 0
    # The supervisor is never restricted, so an unowned session must not pay
    # for a subprocess. This is the same rule `limits.refuse_write` applies,
    # asserted here first purely so the common case costs nothing — the gate
    # would answer `allow` either way, and a test pins that it does.
    if not (os.environ.get("DG_AGENT") or "").strip():
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    field = WRITERS.get(payload.get("tool_name"))
    if field is None:
        return 0
    path = (payload.get("tool_input") or {}).get(field) or ""
    if not path:
        return 0

    dg = shutil.which("dg")
    if dg is None:
        return 0
    try:
        r = subprocess.run(
            [dg, "gate", "--write", path, "--json"],
            cwd=payload.get("cwd") or os.getcwd(), capture_output=True,
            text=True, errors="replace", timeout=TIMEOUT,
        )
    except Exception:
        # Every failure to run the gate is silent here, and that is the
        # opposite of `precommit.py`, which says so. The asymmetry follows
        # `gate.write_verdict`'s: an unjudged commit may record a contradiction
        # permanently, while an unjudged write is an ordinary file operation
        # nobody asked to be consulted about. Announcing every one of those
        # would train the reader to ignore the message that matters.
        return 0
    if r.returncode != 0:
        return 0
    try:
        verdict = json.loads(r.stdout)
    except Exception:
        return 0

    # `ask` is the only decision this hook can reach — see `limits`, the scope
    # answers consent rather than prohibition. `deny` is honoured anyway, so
    # that a future rule which does refuse arrives working rather than silently
    # ignored, which is the mistake `precommit.py` records having made once
    # with `warn`.
    if (verdict or {}).get("verdict") not in ("ask", "deny"):
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": verdict["verdict"],
        "permissionDecisionReason": verdict.get("reason", ""),
    }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
