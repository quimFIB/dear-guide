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
from collections import Counter
import subprocess

import pytest
from typer.testing import CliRunner

from dgraph import pending, project
from dgraph.agent_cli import app as agent_app
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
            t["because"] = ["D01"]
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
          "--after", "D05")),
    ([], ("decide", "D05", "-a", "a", "-s", "s", "-f", "f")),
    ([], ("reopen", "D01", "-w", "why", "--yes")),
    ([], ("dep", "D06", "--after", "D01,D02")),
    ([], ("undep", "D06", "--after", "D05")),
    ([], ("amend", "D05", "--title", "reworded")),
    ([], ("reprobe", "D05", "--probe", '{"kind": "prose.rule", "args": {}}')),
    ([], ("bind", "D05", "rocq.constant:X")),
    ([("bind", "D05", "rocq.constant:X")], ("unbind", "D05", "rocq.constant:X")),
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
    ([], ("task", "unlink", "T01", "--because", "D01")),
    ([], ("task", "amend", "T02", "--title", "reworded")),
    ([], ("task", "reprobe", "T02", "--probe",
          '{"kind": "prose.done", "args": {}}')),
    ([], ("task", "bind", "T02", "rocq.file:a.v")),
    ([("task", "bind", "T02", "rocq.file:a.v")],
     ("task", "unbind", "T02", "rocq.file:a.v")),
    ([], ("task", "rm", "T03", "--yes")),
    # Across both stores, and therefore two writes — one per tray. That is the
    # rule rather than an exception to it: `dg apply` keeps the two batches
    # independent so one that cannot apply can never stop one that can, and a
    # rename that tried to be a single act across both would give that up for
    # the one command whose whole job is undoing a divergence.
    ([], ("areas", "rename", "Alpha", "Gamma")),
]


@pytest.mark.parametrize("setup,argv", CASES,
                         ids=[" ".join(c[1]) for c in CASES])
def test_one_staging_command_is_one_tray_write(run, tray_writes, setup, argv):
    for pre in setup:
        assert run(*pre).exit_code == 0, f"setup failed: dg {' '.join(pre)}"
    counted = tray_writes()
    res = run(*argv)
    assert res.exit_code == 0, res.output
    # Counted **per tray**, because the two trays are deliberately independent:
    # a command touching both writes each once, and `dg apply` applies them as
    # separate batches so one that cannot apply never stops one that can. What
    # must never happen is one tray written twice, which is a tray somebody can
    # read holding half a group.
    per_tray = Counter(counted)
    assert all(n <= 1 for n in per_tray.values()), (
        f"`dg {' '.join(argv)}` wrote a tray more than once "
        f"({', '.join(f'{k} x{n}' for k, n in per_tray.items() if n > 1)}). A "
        f"group of ops that only means something together must be one write — "
        f"see `pending.stage_all`."
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
        # Writes the two stores directly, and is the one command that does.
        # There is no op for it and there should not be: `areas` is not a
        # record, nothing in either tray addresses it, and an op kind whose
        # whole effect is to forget a label would be a staged act with nothing
        # to review.
        ("areas", "prune"),
        ("task", "init"), ("task", "pending"), ("task", "render"),
        ("task", "node"), ("task", "tree"), ("task", "independent"), ("task", "drop-op"),
        ("task", "clear"), ("task", "import"), ("task", "export"),
    }

    #: The launcher's own commands, walked from its own app. `dg-agent` is a
    #: second entry point over the same package — it stages parks into the task
    #: tray, so it is exactly as subject to this rule as `dg` is, and a check
    #: that only walked `dg` would have stopped covering the one command here
    #: that writes a tray the moment the split landed.
    AGENT_NO_TRAY = {
        # `.dgraph-agents.json` is not a tray: it holds names, not ops, and
        # nothing applies it. `claim` and `prune` *read* both trays to work out
        # what is in use, under the lease file's own lock and never a tray's —
        # which is also what keeps them out of `applying.trays`' lock order.
        ("claim",), ("list",), ("release",), ("prune",),
        # Writes `fanout/scout.md`, `fanout/launch.sh` and `fanout/env.json` —
        # files a person reads and edits, not ops anybody applies. Nothing it
        # produces goes near a tray.
        ("setup",),
        # Reports; writes nothing at all.
        ("env",), ("presets",),
        # A server. It answers consent requests and writes only its own log —
        # never an op, never a tray. The grants it hands out are held in memory
        # and die with it, deliberately: the lease file has to stay writable by
        # agents because the heartbeat stamps it, so a grant written there would
        # be one an agent could award itself.
        ("broker",),
        # Writes a verdict for the broker to collect — a file two processes
        # hand between them, never an op and never a tray. What it answers is
        # a permission, and permissions are not part of the record.
        ("consent",),
    }
    #: Commands that DO write a tray but cannot be expressed as a `CASES` entry,
    #: with the test that covers them instead. Separate from `NO_TRAY` because
    #: that set means "touches no tray", and putting one of these there would be
    #: a false statement that also switched the check off.
    #:
    #: `dg-agent expire` stages only for an agent whose budget has run out, and
    #: a budget runs out by the clock — no sequence of CLI setup commands can
    #: produce one, since `claim` stamps `started` at the moment it is called.
    #: `test_expire_is_one_tray_write_per_agent` below backdates the lease and
    #: arms the same counter. `dg-agent run` is the same shape one step further
    #: out: it parks what a child was holding, and producing one means spawning
    #: a child and letting a budget elapse — `test_limits` covers the park.
    COVERED_ELSEWHERE = {("expire",), ("run",)}
    covered = {tuple(a for a in argv if not a.startswith("-"))[:2]
               for _, argv in CASES}
    covered = {c[:2] if c[0] in ("task", "areas") else c[:1] for c in covered}
    missing = names(app) - NO_TRAY - covered
    assert not missing, (
        f"staging command(s) with no write-count case: {sorted(missing)} — "
        f"add them to CASES, or to NO_TRAY if they touch no tray"
    )
    missing = names(agent_app) - AGENT_NO_TRAY - COVERED_ELSEWHERE
    assert not missing, (
        f"`dg-agent` command(s) with no write-count case: {sorted(missing)} — "
        f"add them to AGENT_NO_TRAY if they touch no tray"
    )


def test_expire_is_one_tray_write_per_agent(both_stores, tray_writes,
                                            monkeypatch):
    """`agent expire`, the one staging command `CASES` cannot express.

    It stages only for an agent whose budget has run out, and a budget runs out
    by the clock — `claim` stamps `started` at the moment it is called, so no
    sequence of CLI setup commands can produce one. The lease is backdated here
    instead, and the same counter armed.

    A hand-back is a group: a tray read between two parks shows half of it. It
    cannot be one write across *all* agents either, because `pending.as_owner`
    stamps a whole call and each batch carries its own agent's name — so the
    floor is one write per agent, and this pins that it is also the ceiling.

    Named by `COVERED_ELSEWHERE` above; if this is renamed, rename it there.
    """
    from dgraph import agents

    name = agents.claim(both_stores, budget=60)
    leases = agents.load(both_stores)
    leases[name]["started"] = "2000-01-01T00:00:00"
    leases[name]["holding"] = ["T01", "T02"]
    agents.save(leases, both_stores)
    for tid in ("T01", "T02"):
        with pending.as_owner(name):
            assert runner.invoke(
                app, ["--project", str(both_stores), "task", "start", tid]
            ).exit_code == 0
        assert runner.invoke(
            app, ["--project", str(both_stores), "apply", "--agent", name]
        ).exit_code == 0

    counted = tray_writes()
    res = runner.invoke(agent_app, ["--project", str(both_stores), "expire"])
    assert res.exit_code == 0, res.output
    assert len(counted) == 1, (
        f"one agent's hand-back wrote a tray {len(counted)} times "
        f"({', '.join(counted)}) — a group of ops that only means something "
        f"together must be one write")
