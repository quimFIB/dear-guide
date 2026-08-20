#!/usr/bin/env python3
"""PreToolUse: hand a Bash command to `dg gate` and translate the verdict.

Translation only. Recognising what a shell command is about to do — commit, or
remove a node from the graph — and deciding what to do about it are `dg gate`;
see `dgraph/gate.py`. Doing either here would be a second implementation of the
rule, in a file with no tests, and every further host would add another.

The one piece of knowledge this file does hold is `gate.TRIGGERS`, copied into
the fast path below because a hook may not import the package. It is a copy
that has already gone stale once, so a test asserts it against the real tuple.

**Stdlib only, and never `import dgraph`** — see `hooks/brief.py` for why.
"""

import json
import os
import shutil
import subprocess
import sys

#: The gate's own budget is bounded by `gate.GIT_TIMEOUT` per `git` call, and
#: it makes a handful of them plus a re-render of both views. This has to be
#: comfortably larger than that, because the alternative to waiting is not
#: "gate faster" — it is "run the command ungated", which is the one outcome
#: the gate exists to prevent. `hooks/hooks.json` allows the hook process
#: longer again.
TIMEOUT = 20

#: `dg gate`'s blocking verdicts, in the vocabulary `permissionDecision` uses.
#: `warn` is deliberately absent — it is not a permission decision at all, and
#: emitting one would override the user's own rules; it goes out as a bare
#: `systemMessage` instead. `allow` is absent for the same reason.
DECISION = {"deny": "deny", "ask": "ask"}


def say(message: str) -> None:
    """Show `message` to the user without deciding anything.

    A top-level `systemMessage` with exit 0: Claude Code shows the text and lets
    the call proceed through the user's normal permission flow. Printing to
    stderr does *not* work — a hook that exits 0 has its stderr sent to the
    debug log only, never the transcript, so every advisory this file used to
    print that way was invisible on the host it was written for.
    """
    print(json.dumps({"systemMessage": message}))


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

    # A fast path, not a policy: a command containing none of these cannot earn
    # anything but `allow`, so skipping it can never hide a refusal, and it
    # keeps a subprocess out of every unrelated Bash call.
    #
    # This list is `gate.TRIGGERS` and must stay equal to it — `dg gate
    # --triggers` prints it, and a test asserts the two agree. It read
    # `("commit",)` for as long as commits were all the gate judged, and stayed
    # that way after `dg rm` was added, so the gate's `ask` on a removal was
    # never reached from here. Anything the gate learns to refuse has to be
    # reachable from a word in this tuple.
    if not any(t in command for t in ("commit", "rm")):
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
    except subprocess.TimeoutExpired:
        # Distinguished from every other failure below, and the only one worth
        # a word. `dg gate` is written never to fail open — it catches
        # everything and denies — and a timeout here undoes that from the
        # outside: the commit proceeds having been judged by nothing. Silence
        # would make it indistinguishable from a project with no graph.
        say(f"dg gate did not finish within {TIMEOUT}s — this command was not "
            f"checked against the development graph. `dg check` says whether "
            f"it should have been.")
        return 0
    except Exception:
        # Everything else is a deliberate silence: `dg` not installed, a `dg`
        # too old to know the subcommand, no graph in this directory. A plugin
        # is installed for a user, not for a project.
        verdict = None
    if not verdict:
        return 0

    if verdict.get("verdict") == "warn":
        # Not a permission decision: the command runs either way, and the user
        # is told one thing on the way past. A generated view that has fallen
        # behind its store is the case this exists for.
        say(verdict.get("reason", ""))
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
