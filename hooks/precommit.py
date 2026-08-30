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

#: Exit codes from `dg gate` that this hook passes over in silence.
#:
#: `dg gate` is written never to fail open — it catches everything and denies —
#: and it **always exits 0**, so that an adapter can tell a refusal from a
#: crash; its docstring says so, and `tests/test_plugin.py` pins it. Every other
#: exit therefore means the gate did not run, and the command is about to
#: proceed having been judged by nothing.
#:
#: One question decides whether each of those is worth a word: **could this be a
#: project that has never heard of the tool?** A plugin is installed for a user,
#: not for a project, and such a directory must pay nothing at all. Two cases
#: can be, and they are the two that stay silent — `dg` absent, which
#: `shutil.which` catches before this, and exit 2, which is typer's code for a
#: subcommand or an option this `dg` does not know. The plugin and the package
#: install separately, so that skew is ordinary.
#:
#: Nothing else can be. A `dg` that exits 1 is on `PATH`, knows the subcommand,
#: and broke — an editable install whose checkout moved is the everyday way in —
#: and its silence is indistinguishable from a clean allow.
#:
#: Stated as a class on purpose. This file already held the argument and applied
#: it to the timeout branch alone for a pass, which is exactly how one failure
#: mode came to be answered and the rest to be waved through.
SILENT_EXITS = (2,)

#: What every "the gate did not run" message says, given how it did not run.
#: One sentence for all of them, because they differ in nothing that matters to
#: a reader: the command was not checked, and `dg check` is how to find out
#: whether it should have been.
UNCHECKED = ("{how} — this command was not checked against the development "
             "graph. `dg check` says whether it should have been.")


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
    #
    # **An agent skips the fast path entirely**, and the asymmetry is the whole
    # of what makes the command gate an allowlist. Whether an agent may run
    # `cargo` depends on `$DG_EXEC_ALLOW`, not on any word in the command, so
    # no word list can be sound for it — widening one means guessing more
    # words, which is what this project refuses to do with shell. A person is
    # unaffected and pays exactly what they paid before.
    #
    # `$DG_AGENT` is the same fast path `hooks/prewrite.py` uses, and for the
    # same reason: it is the one thing that distinguishes a session the scope
    # applies to from every ordinary use of the host.
    owned = bool((os.environ.get("DG_AGENT") or "").strip())
    if not owned and not any(t in command for t in ("commit", "rm")):
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
    except subprocess.TimeoutExpired:
        # The gate's budget expiring undoes "never fail open" from the outside.
        say(UNCHECKED.format(how=f"dg gate did not finish within {TIMEOUT}s"))
        return 0
    except Exception:
        # `dg` could not be run at all — a shebang pointing at an interpreter
        # that is gone, a directory that no longer exists. Indistinguishable
        # here from `dg` never having been installed, so it is treated as that.
        return 0

    if r.returncode in SILENT_EXITS:
        return 0
    if r.returncode != 0:
        say(UNCHECKED.format(how=f"dg gate exited {r.returncode}"))
        return 0
    try:
        verdict = json.loads(r.stdout)
    except Exception:
        # Exit 0 is the gate promising a verdict. Something else on stdout is
        # that promise broken, not a quiet allow.
        say(UNCHECKED.format(how="dg gate answered with something that is not "
                                 "a verdict"))
        return 0

    decision = DECISION.get((verdict or {}).get("verdict"))
    if decision is None:
        # Everything this hook cannot turn into a permission decision, in one
        # rule: **say it if it carries a reason, and stay silent if it does
        # not.** That is `warn` — the command runs either way and the user is
        # told one thing on the way past, a generated view that has fallen
        # behind its store being the case it exists for. It is also `allow`,
        # which carries no reason and must stay silent, because an explicit
        # "allow" would override the user's own permission rules for every git
        # command they run — a security regression dressed up as a convenience.
        #
        # And it is a verdict added to `dg gate` after this hook was written.
        # `warn` was such a verdict once, and until the hook learned the word
        # the reason went nowhere. Keying on the reason rather than on the name
        # means the next one is passed on unread rather than dropped unread.
        reason = (verdict or {}).get("reason") or ""
        if reason:
            say(reason)
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": verdict.get("reason", ""),
    }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
