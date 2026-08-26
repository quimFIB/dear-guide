"""One staging command, one tray write.

`pending.stage_all` exists because a group of ops that only means something
together must reach the tray as **one** write. Between two writes the tray on
disk holds half a group, and anything reading it then — a second agent, the
commit gate, an apply already in flight — sees a batch that means something
other than what the command staged.

That rule was audit F18. It was implemented as `stage_all` and then applied by
hand, site by site, which is why F28 and F31 found four more places still
looping. Nothing stopped the next command from looping either, so the rule is
checked here rather than remembered: the spy counts tray writes, the list is
every staging command the tool has, and a new command joins it by being added
to `CASES`.

The counter is `pending.save`, which is the single funnel every tray write goes
through — `stage`, `stage_all`, `drop`, `replace_group`, `clear` and `discard`
all end there. Counting it rather than `project.write_atomic` keeps store and
view writes out of the number.
"""

import json
import pathlib
import subprocess

import pytest
from typer.testing import CliRunner

from dgraph import pending, project
from dgraph.cli import app
from tests.conftest import FIXTURE, TASK_FIXTURE

runner = CliRunner()


@pytest.fixture
def both_stores(tmp_path, monkeypatch):
    """A project with both stores, committed.

    Committed because `dg rm` refuses to stage a removal while the store has
    uncommitted changes — git is the only record of what a removal takes away,
    and `_archived` makes that archive real rather than assumed.
    """
    (tmp_path / "decisions.json").write_text(json.dumps(FIXTURE, indent=2),
                                             encoding="utf-8")
    tasks = json.loads(json.dumps(TASK_FIXTURE))
    for t in tasks["tasks"]:
        if t["id"] in ("T01", "T02"):
            t["because"] = "D01"
    (tmp_path / "tasks.json").write_text(json.dumps(tasks, indent=2),
                                         encoding="utf-8")
    monkeypatch.setattr(project, "_override", tmp_path)
    for argv in (["git", "init", "-q", "."], ["git", "add", "-A"],
                 ["git", "-c", "user.email=a@b", "-c", "user.name=a",
                  "commit", "-qm", "fixture"]):
        subprocess.run(argv, cwd=tmp_path, capture_output=True)
    return tmp_path


@pytest.fixture
def run(both_stores, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERM", "dumb")

    def go(*args):
        return runner.invoke(app, ["--project", str(both_stores), *args])
    return go


@pytest.fixture
def tray_writes(monkeypatch):
    """Arm the counter. Called *after* any setup, so only the command under
    test is measured."""
    real = pending.save

    def arm() -> list[str]:
        counted: list[str] = []

        def save(ops, path=None):
            counted.append(pathlib.Path(path or project.find().pending).name)
            return real(ops, path)

        monkeypatch.setattr(pending, "save", save)
        return counted
    return arm


#: `(setup, argv)`. Setup runs before the counter is armed. Every command that
#: puts something in a tray belongs here — including the ones that stage a
#: single op, so that a command which *grows* a group is caught the day it does.
CASES = [
    ([], ("add", "--id", "D07", "--title", "x", "--area", "Alpha")),
    ([], ("add", "--id", "D07", "--title", "x", "--area", "Alpha",
          "--after", "D01,D05")),
    ([], ("add", "--id", "D07", "--title", "x", "--area", "Alpha",
          "--status", "BLOCKED:D05")),
    ([], ("decide", "D05", "-a", "a", "-s", "s", "-f", "f")),
    ([], ("reopen", "D01", "-w", "why", "--yes")),
    ([], ("dep", "D06", "--after", "D01,D02")),
    ([], ("undep", "D06", "--after", "D05")),
    ([], ("amend", "D05", "--title", "reworded")),
    ([], ("rm", "D06", "--yes")),
    ([], ("task", "add", "--id", "T09", "--title", "x", "--area", "Alpha")),
    ([], ("task", "add", "--id", "T09", "--title", "x", "--area", "Alpha",
          "--after", "T01,T02")),
    ([], ("task", "add", "--id", "T09", "--title", "x", "--area", "Alpha",
          "--after", "T01", "--discovered-during", "T04")),
    ([], ("task", "dep", "T04", "--after", "T01,T02")),
    ([], ("task", "dep", "T04", "--after", "T01",
          "--discovered-during", "T02")),
    ([("task", "dep", "T04", "--after", "T01,T02"), ("apply",)],
     ("task", "undep", "T04", "--after", "T01,T02")),
    ([], ("task", "drop", "T04", "-w", "gone")),
    ([], ("task", "drop", "T02", "-w", "gone", "--keep", "T03")),
    ([], ("task", "drop", "T02", "-w", "gone", "--drop-too", "T03")),
    ([], ("task", "start", "T02")),
    ([], ("task", "park", "T02", "-w", "stuck upstream")),
    ([], ("task", "done", "T02", "-o", "out")),
    ([], ("task", "link", "T02", "--evidence-for", "D05")),
    ([], ("task", "unlink", "T01", "--because")),
    ([], ("task", "amend", "T02", "--title", "reworded")),
    ([], ("task", "rm", "T03", "--yes")),
]


@pytest.mark.parametrize("setup,argv", CASES,
                         ids=[" ".join(c[1]) for c in CASES])
def test_one_staging_command_is_one_tray_write(run, tray_writes, setup, argv):
    for pre in setup:
        assert run(*pre).exit_code == 0, f"setup failed: dg {' '.join(pre)}"
    counted = tray_writes()
    res = run(*argv)
    assert res.exit_code == 0, res.output
    assert len(counted) <= 1, (
        f"`dg {' '.join(argv)}` wrote a tray {len(counted)} times "
        f"({', '.join(counted)}). A group of ops that only means something "
        f"together must be one write — see `pending.stage_all`."
    )


def test_every_staging_command_is_covered():
    """The list is the point: a command that stages and is not here is a rule
    nobody is checking. Compared against the CLI's own command tables so a new
    one has to be listed or explicitly excused."""
    import typer.main

    def names(t, prefix=()):
        out = set()
        for c in t.registered_commands:
            out.add(prefix + (c.name or c.callback.__name__.replace("_", "-"),))
        for grp in t.registered_groups:
            out |= names(grp.typer_instance, prefix + (grp.name,))
        return out

    #: Commands that touch no tray: reads, renders, the gate, and the two
    #: `clear`s (which empty a tray in one write by definition).
    NO_TRAY = {
        ("show",), ("find",), ("tree",), ("node",), ("path",), ("context",),
        ("why",), ("areas",),
        ("brief",), ("gate",), ("check",), ("pending",), ("export",),
        ("apply",), ("render",), ("init",), ("import",), ("import-md",),
        ("serve",),
        # Writes `.dgraph-range.json`, which is not a tray: it holds no ops,
        # nothing applies it, and the watermark inside it is raised by
        # `pending.stage_all` under that tray's own lock.
        ("range",),
        # Writes `.dgraph-incoming.json`, which is deliberately not a tray:
        # quarantined ops nobody has accepted, in one file for both stores
        # because a contribution is atomic across them.
        ("integrate",),
        # `--adopt` stages, but as one `stage_all` per tray — the same call
        # every group command makes, and covered by their cases.
        ("incoming",),
        ("drop",), ("clear",), ("edit",), ("repair",), ("confirm",),
        ("task", "init"), ("task", "pending"), ("task", "render"),
        ("task", "node"), ("task", "tree"), ("task", "drop-op"),
        ("task", "clear"), ("task", "import"), ("task", "export"),
        # `.dgraph-agents.json` is not a tray either: it holds names, not ops,
        # and nothing applies it. `claim` and `prune` *read* both trays to work
        # out what is in use, under the lease file's own lock and never a
        # tray's — which is also what keeps them out of `applying.trays`' lock
        # order.
        ("agent", "claim"), ("agent", "list"), ("agent", "release"),
        ("agent", "prune"),
    }
    covered = {tuple(a for a in argv if not a.startswith("-"))[:2]
               for _, argv in CASES}
    covered = {c[:2] if c[0] == "task" else c[:1] for c in covered}
    missing = names(app) - NO_TRAY - covered
    assert not missing, (
        f"staging command(s) with no write-count case: {sorted(missing)} — "
        f"add them to CASES, or to NO_TRAY if they touch no tray"
    )
