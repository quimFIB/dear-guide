"""`grep`: the cookbook's demo domain, and the smallest one that judges.

One kind, `grep.matches`, with two args: a `file` under the project root
and an extended-regex `pattern`. The domain runs `grep -E` over that file
and answers `fired` when a line matches, `holds` when none does — so a
falsifier written as a pattern is judged by the machine the moment the
file it names changes.

It exists to show a verdict on the cookbook page; nothing ships it. But it
is also the domain to copy, since `prose` (three no-op methods) cannot
show the two rules a real one lives by:

- the store names data, never a command: `pattern` and `file` are what
  the graph carries; that *grep runs* was this module's author's choice,
  installed by somebody with the authority to install software (R4);
- a domain reads under `root` and nowhere else: a `file` that resolves
  outside it is `unjudged`, not an error and not read.

Found through the `dgraph.domains` entry-point group like any other; the
`dist-info` beside this package is what makes a bare `PYTHONPATH` entry
count as installed, so the recipes need no `pip install`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from dgraph.domains import Item, Relation, Result


class Grep:
    name = "grep"
    kinds = frozenset({"grep.matches"})
    # Optional: seconds one batch may take before its answers are unjudged.
    # Absent, the core's DEADLINE (60 s) applies; `dg probe --timeout`
    # overrides whatever is declared. A grep needs nothing like a minute.
    deadline = 5.0

    def compose(self, kind: str, record: object, root: Path) -> tuple[dict, str]:
        return {}, ""

    def evaluate(self, items: list[Item], root: Path, *,
                 deadline: float) -> dict[str, Result]:
        return {it.id: self._one(it, root.resolve()) for it in items}

    def relations(self, bindings, root: Path) -> Relation:
        return Relation()

    @staticmethod
    def _one(it: Item, root: Path) -> Result:
        file, pattern = it.args.get("file"), it.args.get("pattern")
        if not isinstance(file, str) or not isinstance(pattern, str):
            return Result("unjudged", "grep.matches needs `file` and `pattern`")
        path = (root / file).resolve()
        if root not in path.parents:
            return Result("unjudged", f"{file} is outside the project — not read")
        if not path.is_file():
            return Result("unjudged", f"{file} does not exist yet")
        # `-e` and `--`: the store's pattern and file are data, whatever
        # they begin with. Without them `--help` was an option and its usage
        # text a verdict, and `-f/etc/hostname` read outside root (J-F4).
        r = subprocess.run(["grep", "-E", "-m1", "-e", pattern, "--", str(path)],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return Result("fired", f"{file}: {r.stdout.strip()}")
        if r.returncode == 1:
            return Result("holds", f"no line of {file} matches /{pattern}/")
        return Result("unjudged", f"grep failed: {r.stderr.strip()}")


GREP = Grep()
