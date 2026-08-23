#!/usr/bin/env python3
"""SessionStart: put the decision brief into the session's context.

Translation only. What is worth saying, and how it is laid out, is `dg brief` —
see `dgraph/brief.py`. This file decides nothing about the graph; it moves text
from a subprocess to stdout, which Claude Code injects as context.

**Stdlib only, and never `import dgraph`.** `dg` may be installed in a
virtualenv that is not the interpreter running this hook, so the only supported
contact with the tool is the executable on `PATH`. Importing the package would
work on the machine it was written on and nowhere else.

Silence is the default. A plugin is installed for a user, not for a project, so
a directory that has never heard of this tool must pay nothing at all for having
it enabled: no output, no error, exit 0.
"""

import json
import os
import shutil
import subprocess
import sys

TIMEOUT = 10

COMPACTED = (
    "Context was just compacted. Anything settled in what was dropped that is "
    "not in the graph below still needs recording."
)


def off() -> bool:
    return os.environ.get("DG_HOOK_OFF", "").strip() not in ("", "0", "false", "no")


#: Exit 2 from `dg brief` means two different things and only stderr separates
#: them: "no decisions.json at or above here", which is the common case and must
#: stay silent, and "no such command", a `dg` that predates the subcommand. The
#: plugin and the package install separately, so that skew is ordinary.
NO_GRAPH_OR_TOO_OLD = 2

TOO_OLD = ("The dear-guide plugin needs a newer `dg`: "
           "`pip install -U -e <the dear-guide checkout>`.")

BROKEN = ("`dg` is on PATH but exited {code} instead of writing a brief, so the "
          "development graph is not in this session's context. Run `dg brief` "
          "to see why — a checkout moved out from under an editable install is "
          "the usual cause.")


def _why_it_said_nothing(r) -> str:
    """One line for a `dg brief` that failed, or `""` for the ordinary case.

    Classified by exit code rather than by reading stderr for one phrase. That
    sniff caught a `dg` too old and nothing else, so every other way the tool
    can be broken — an import that fails, a half-finished upgrade — left this
    hook silent **permanently**, with the plugin looking exactly like a plugin
    correctly doing nothing in a directory that has no graph.
    """
    if r.returncode == NO_GRAPH_OR_TOO_OLD:
        return TOO_OLD if "No such command" in (r.stderr or "") else ""
    return BROKEN.format(code=r.returncode)


def main() -> int:
    if off():
        return 0
    dg = shutil.which("dg")
    if dg is None:
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    cwd = payload.get("cwd") or os.getcwd()

    try:
        r = subprocess.run(
            [dg, "brief"], cwd=cwd, capture_output=True, text=True,
            errors="replace", timeout=TIMEOUT,
        )
    except Exception:
        # Including a timeout, which is the one failure here that is not
        # permanent. The gate hook says so when its subprocess expires, because
        # a check that did not run let something past; a brief that did not
        # arrive costs context for one session and comes back on the next.
        return 0

    if r.returncode != 0:
        # `""` for the ordinary case — a directory with no graph — which must
        # stay silent down to the newline.
        note = _why_it_said_nothing(r)
        if note:
            print(note)
        return 0

    brief = r.stdout.strip()
    if not brief:
        return 0
    if "--compacted" in sys.argv:
        print(COMPACTED)
    print(brief)
    return 0


if __name__ == "__main__":
    sys.exit(main())
