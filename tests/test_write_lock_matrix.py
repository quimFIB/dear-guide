"""Every path this tool writes, against the lock that holds it.

The list of what `dg` writes is exact and maintained — `project.IGNORE`, which
three modules argue their own correctness from. The list of what it *locks* has
only ever existed as call sites, so a new write reaches a new module and the
lock does not follow: `W-F1` (`.dgraph-incoming.json`, no lock at all) and
`W-F3` (`.dgraph-range.json`, guarded by whichever tray lock the caller
happened to hold) were one absence counted twice, and both were found by
reading rather than by anything that could fail.

This is that second list, derived rather than written down. `project.held`
stamps a per-thread depth for the path it holds — and `project.stores` reaches
it through `held` for each store — so at the moment of a write the depth
answers "is this file held by whoever is writing it?" without the test knowing
which route took the lock.

**Why the assertion is on the write and not on the door.** A door-by-door test
is a list of doors somebody has to keep, which is the thing that failed. This
one fails for a door nobody has thought of yet, because the trip point is
`write_atomic` itself.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

from dgraph import agents, project
from tests.conftest import FIXTURE, TASK_FIXTURE

#: The files a second writer in this clone can lose work in: both stores, both
#: trays, and the three scratch files that are read-modify-written. Keyed by
#: the attribute on `Project` so a rename kills the name with it.
GUARDED = ("store", "tasks", "pending", "task_pending", "range", "incoming")

#: Written by name rather than through `Project`, since it has no property.
GUARDED_NAMES = (agents.AGENTS_NAME,)

#: Written deliberately unlocked, each with its reason. A generated view is
#: rebuilt on demand and `dg check` reports one that has fallen behind, so a
#: torn race there costs a warning; the compose buffer has its own *refusing*
#: lock in `editor.py`, because a person is typing in it and waiting is not
#: the right answer; the detached server's run record is one process's note
#: about itself.
UNGUARDED = (project.VIEW_NAME, project.TASK_VIEW_NAME, project.EDIT_NAME,
             ".dgraph-serve.json", ".dgraph-serve.log")


@pytest.fixture
def watch_writes(monkeypatch):
    """Record every `write_atomic`, and whether its path was held at the time.

    Returns the list, so a test drives a command and then reads the matrix off
    it. The wrapper calls through, so the command under test really writes.
    """
    seen: list[tuple[str, bool]] = []
    real = project.write_atomic

    def spy(path, text):
        depth = project._single_depth().get(str(path), 0)
        seen.append((path.name, depth > 0))
        return real(path, text)

    monkeypatch.setattr(project, "write_atomic", spy)
    # The modules that imported the name rather than the module keep their own
    # reference; there is none today, but a `from project import write_atomic`
    # arriving later would silently opt out of this test.
    return seen


def _guarded_names(proj):
    return {getattr(proj, a).name for a in GUARDED} | set(GUARDED_NAMES)


def _matrix(seen, proj):
    """`{name: held}` for every guarded path this run wrote."""
    guarded = _guarded_names(proj)
    return {name: held for name, held in seen if name in guarded}


def test_the_guarded_set_and_the_ignore_list_agree():
    """Every guarded path is one `.gitignore` covers or a store git tracks.

    The two lists are maintained apart, and a scratch file that is locked but
    not ignored is one `git add -A` from the record.
    """
    proj = project.Project(pathlib.Path("/nowhere"))
    for name in _guarded_names(proj) | set(UNGUARDED):
        tracked = name in (project.STORE_NAME, project.TASKS_NAME,
                           project.VIEW_NAME, project.TASK_VIEW_NAME)
        assert tracked or name.startswith(".dgraph-"), \
            f"{name} is neither tracked nor covered by `.dgraph-*`"


def test_a_range_grant_is_written_under_the_range_lock(tmp_path, monkeypatch,
                                                       watch_writes):
    """`dg range --set` is the second writer of `.dgraph-range.json`.

    `ranges.issue` holds the file — audit `W-F3` — and `cli.range_cmd` writes
    the same file through `ranges.save` with nothing held, so the two doors
    onto one file hold different locks, which is the shape `W-F3` closed from
    the other side.
    """
    monkeypatch.setattr(project, "_override", tmp_path)
    (tmp_path / project.STORE_NAME).write_text(json.dumps(FIXTURE))
    from typer.testing import CliRunner

    from dgraph import cli
    res = CliRunner().invoke(
        cli.app, ["--project", str(tmp_path), "range", "--set", "50-99"])
    assert res.exit_code == 0, res.output
    matrix = _matrix(watch_writes, project.find())
    assert matrix.get(project.RANGE_NAME) is True, \
        f"{project.RANGE_NAME} written unheld by `dg range --set`"


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, text=True)


def _contribution(root):
    """A branch holding one decision this clone does not have.

    Real git, because `dg integrate` reads the arriving store out of a ref and
    a fake would stand in for the slow half — the `merge-base` and the four
    `git show`s the lock is now held across. Shape 14.
    """
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    (root / project.STORE_NAME).write_text(json.dumps(FIXTURE))
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "theirs")
    theirs = json.loads(json.dumps(FIXTURE))
    theirs["vertices"].append({"id": "D09", "title": "Theirs", "area": "Alpha",
                               "status": "OPEN", "note": "?"})
    (root / project.STORE_NAME).write_text(json.dumps(theirs))
    _git(root, "commit", "-qam", "theirs")
    _git(root, "checkout", "-q", "-")


def test_a_quarantine_is_written_under_the_quarantine_lock(tmp_path, monkeypatch,
                                                           watch_writes):
    """`dg integrate` is the second writer of `.dgraph-incoming.json`.

    Every `dg incoming` route holds the file — audit `W-F1` — and the command
    that *creates* it did not, so the producer was the one door onto the
    quarantine that took no lock: two integrates both passed the "already
    waiting" check and both said "quarantined", with one contribution gone.
    """
    monkeypatch.setattr(project, "_override", tmp_path)
    _contribution(tmp_path)
    from typer.testing import CliRunner

    from dgraph import cli
    res = CliRunner().invoke(
        cli.app, ["--project", str(tmp_path), "integrate", "theirs"])
    assert res.exit_code == 0, res.output
    matrix = _matrix(watch_writes, project.find())
    assert matrix.get(project.INCOMING_NAME) is True, \
        f"{project.INCOMING_NAME} written unheld by `dg integrate`"


def test_a_lease_is_written_under_the_lease_lock(tmp_path, monkeypatch,
                                                 watch_writes):
    """The roster's own file, which pass 11 found already correct."""
    monkeypatch.setattr(project, "_override", tmp_path)
    (tmp_path / project.TASKS_NAME).write_text(json.dumps(TASK_FIXTURE))
    agents.claim(tmp_path)
    matrix = _matrix(watch_writes, project.find())
    assert matrix.get(agents.AGENTS_NAME) is True, \
        f"{agents.AGENTS_NAME} written unheld by `agent claim`"


def test_an_apply_writes_both_stores_held(tmp_path, monkeypatch, watch_writes):
    """The pair, through the route both hosts share."""
    monkeypatch.setattr(project, "_override", tmp_path)
    (tmp_path / project.STORE_NAME).write_text(json.dumps(FIXTURE))
    (tmp_path / project.TASKS_NAME).write_text(json.dumps(TASK_FIXTURE))
    from dgraph import applying, pending, task_pending
    pending.stage_all([{"op": "add_vertex", "id": "D09", "title": "x",
                        "area": "Alpha"}])
    pending.stage_all([{"op": "add_task", "id": "T09", "title": "y",
                        "area": "Alpha"}], task_pending.path())
    applying.apply_decisions(pending.load(project.find().pending))
    applying.apply_tasks(pending.load(task_pending.path()))
    matrix = _matrix(watch_writes, project.find())
    for name in (project.STORE_NAME, project.TASKS_NAME,
                 project.PENDING_NAME, project.TASK_PENDING_NAME):
        assert matrix.get(name) is True, f"{name} written unheld by apply"


# ---- every command, not the four somebody remembered ----------------------
#
# `M04` asked for a mechanism rather than a list and got one: the spy fails for
# a door nobody has written yet, *provided a test drives that door*. `R-F1` is
# what that proviso costs — `dg areas prune` writes both committed stores
# unheld, went in with the four tests above already green, and is the one
# command in the tool that read-modify-writes a store outside `applying.py`.
#
# So the door list is derived too: walk both apps, run every command against a
# project that has something to write, and assert that whatever guarded path it
# wrote was held while it wrote it. A command that writes nothing passes
# vacuously and costs a subprocess-free invocation; a command added tomorrow is
# covered the day it lands, which is the property the four above only have for
# the doors somebody thought of.

def _commands(app, prefix=()):
    """`(argv-prefix, callback)` for every command in a typer app, groups too."""
    out = []
    for cmd in app.registered_commands:
        name = cmd.name or cmd.callback.__name__.replace("_", "-")
        out.append((*prefix, name))
    for grp in app.registered_groups:
        out += _commands(grp.typer_instance, (*prefix, grp.name))
    return out


def _seeded(root):
    """A project with both stores, both trays, a grant and a lease.

    Every guarded file present, so a command that would rewrite one has one to
    rewrite. The stores carry an area holding nothing, which is what gives
    `dg areas prune` something to do — a command that no-ops writes nothing and
    proves nothing.
    """
    store = json.loads(json.dumps(FIXTURE))
    store["areas"] = [*store["areas"], "Ghost"]
    (root / project.STORE_NAME).write_text(json.dumps(store))
    tasks = json.loads(json.dumps(TASK_FIXTURE))
    tasks["areas"] = [*tasks["areas"], "Phantom"]
    (root / project.TASKS_NAME).write_text(json.dumps(tasks))


#: Commands this sweep does not run, each because running it is not a test of
#: locking. Nothing here is exempt from the *rule* — `dg serve` blocks forever
#: and `dg-agent run` spawns a child, so they are excluded from the sweep and
#: not from the matrix, and each is reached by its own test elsewhere.
_NOT_SWEPT = {("serve",), ("run",), ("setup",)}


@pytest.mark.parametrize(
    "argv",
    [a for a in _commands(__import__("dgraph.cli", fromlist=["app"]).app)
     if a not in _NOT_SWEPT],
    ids=lambda a: " ".join(a))
def test_no_command_writes_a_guarded_path_unheld(argv, tmp_path, monkeypatch,
                                                 watch_writes):
    """Whatever this command wrote, it held while writing it.

    The assertion is on the writes that happened, so a command that refuses for
    want of a flag is not a failure — it wrote nothing, and a command with
    nothing to write cannot write it unheld. What this catches is the command
    that *does* reach a store and did not take the lock, which is `R-F1`.
    """
    from typer.testing import CliRunner

    from dgraph import cli
    monkeypatch.setattr(project, "_override", tmp_path)
    _seeded(tmp_path)
    CliRunner().invoke(cli.app, ["--project", str(tmp_path), *argv],
                       input="\n" * 4)
    proj = project.find()
    for name, held in _matrix(watch_writes, proj).items():
        assert held, f"`dg {' '.join(argv)}` wrote {name} unheld"


def test_the_sweep_actually_reaches_a_store_writer():
    """The sweep's own falsifier.

    A parametrised test over commands that all decline for want of an argument
    is a green suite asserting nothing — shape 5, and the exact way a door-list
    test rots. So one command in the sweep must be known to write a store, and
    it is the one `R-F1` was about.
    """
    from dgraph import cli
    assert ("areas", "prune") in _commands(cli.app)
