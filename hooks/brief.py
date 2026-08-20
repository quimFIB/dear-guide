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
        return 0

    if r.returncode != 0:
        # Exit 2 is also "no decisions.json here", which is the common case and
        # must stay silent. The one failure worth a word is a `dg` that predates
        # `dg brief`: the plugin and the package install separately, so skew is
        # ordinary, and its only other symptom would be this hook going quiet
        # forever.
        if "No such command" in (r.stderr or ""):
            print("The development-graph plugin needs a newer `dg`: "
                  "`pip install -U -e <the development-graph-assistant checkout>`.")
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
