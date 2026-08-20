"""The two stores are locked, read and written as a pair.

A task names a decision, so the *pair* can be invalid while each half is fine.
`dg apply` runs two batches — decisions, then tasks — and used to take the lock
inside each of them, leaving the pair on disk in a state nothing had validated
between the two writes: the decision batch is judged against `cross.tasks_after`,
the task graph as it will stand once the task batch lands, and half of that is
not a state anyone approved. `dg check` reports it and the commit gate turns it
into a denial for every agent in the repository. That was audit F27.

Two halves to closing it, and neither works alone: the writer holds the pair
across both batches, and the reader takes the same lock.
"""

import json
import subprocess
import threading

import pytest
from typer.testing import CliRunner

from dgraph import applying, check, pending, project, task_pending
from dgraph.cli import app
from dgraph.model import Graph
from tests.conftest import FIXTURE, TASK_FIXTURE

runner = CliRunner()


@pytest.fixture
def linked(tmp_path, monkeypatch):
    """Both stores, with `T01` resting on `D06` — one link, so one batch on
    each side can break the pair and the other can repair it.

    `D06` because it is opened by a *bare* edge (`D05` is OPEN), so removing it
    rewrites no decided answer — the refusal audit F21 put there, which would
    otherwise stop this batch for an unrelated reason.
    """
    (tmp_path / "decisions.json").write_text(json.dumps(FIXTURE, indent=2),
                                             encoding="utf-8")
    tasks = json.loads(json.dumps(TASK_FIXTURE))
    tasks["tasks"][0]["because"] = "D06"
    (tmp_path / "tasks.json").write_text(json.dumps(tasks, indent=2),
                                         encoding="utf-8")
    monkeypatch.setattr(project, "_override", tmp_path)
    subprocess.run(["dg", "--project", str(tmp_path), "render"],
                   capture_output=True)
    subprocess.run(["dg", "--project", str(tmp_path), "task", "render"],
                   capture_output=True)
    return project.Project(tmp_path)


def _stage_a_repair(proj):
    """A decision batch that breaks the link and a task batch that repairs it.

    Removing `D06` leaves `T01.because` dangling — a blocking `link_resolves` —
    until the task batch clears it. Staged through `pending` directly because
    that is the route the web app takes: `pending.vet` is its only stage-time
    guard, where `dg rm` additionally consults the task store.
    """
    pending.stage({"op": "remove_vertex", "vertex": "D06", "mode": "sever"},
                  proj.pending)
    pending.stage({"op": "set_link", "task": "T01", "clear": ["because"]},
                  task_pending.path())


# ---- the lock itself -----------------------------------------------------


def test_stores_locks_both_halves(linked):
    assert not (linked.store.with_suffix(".json.lock")).exists()
    with project.stores(linked):
        assert (linked.root / "decisions.json.lock").exists()
        assert (linked.root / "tasks.json.lock").exists()
    assert not (linked.root / "decisions.json.lock").exists()
    assert not (linked.root / "tasks.json.lock").exists()


def test_stores_nests_without_deadlocking(linked):
    """`held`'s in-process lock is not re-entrant, so a naive second
    acquisition would hang here — and the file lock would be worse: the inner
    release would delete the outer holder's lock file and admit a third
    writer."""
    with project.stores(linked):
        with project.stores(linked):
            assert (linked.root / "decisions.json.lock").exists()
        # the inner exit must NOT have released the outer holder's lock
        assert (linked.root / "decisions.json.lock").exists()
    assert not (linked.root / "decisions.json.lock").exists()


def test_an_apply_inside_a_writing_span_does_not_deadlock(linked):
    """The property the whole fix rests on: `apply_decisions` takes the pair for
    itself when called alone, and is a no-op about it when called inside a
    span."""
    _stage_a_repair(linked)
    with applying.writing(linked):
        applying.apply_decisions(pending.load(linked.pending))
        applying.apply_tasks(pending.load(task_pending.path()))
    assert check.errors() == []
    assert "D06" not in Graph.load(linked.store).vertices


# ---- what a second agent can see ----------------------------------------


def _reader_during(linked, span):
    """Run `check.errors()` from another thread while `span` is being applied,
    and return what it saw.

    Handshaked rather than timed: the reader is released once the decision
    batch has landed, and the task batch waits for the reader to have finished
    *or* for a generous timeout. If the lock works the reader is still blocked
    when that timeout expires, and it completes after the span ends.
    """
    landed, done = threading.Event(), threading.Event()
    seen = []

    def reader():
        landed.wait(5)
        seen.append([str(v) for v in check.errors()])
        done.set()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    span(landed, done)
    t.join(timeout=5)
    return seen


def test_no_reader_sees_the_pair_half_applied(linked):
    _stage_a_repair(linked)

    def span(landed, done):
        with applying.writing(linked):
            applying.apply_decisions(pending.load(linked.pending))
            landed.set()
            done.wait(0.5)          # the reader must NOT get through here
            applying.apply_tasks(pending.load(task_pending.path()))

    seen = _reader_during(linked, span)
    assert seen == [[]], f"a second agent saw {seen}"


def test_without_the_span_the_reader_sees_it(linked):
    """The other side of the same test, so the one above cannot pass by
    accident — this is the pre-fix arrangement, each apply locking for itself,
    and it is what F27 reported."""
    _stage_a_repair(linked)

    def span(landed, done):
        applying.apply_decisions(pending.load(linked.pending))
        landed.set()
        done.wait(2)                # the reader gets the lock and reads
        applying.apply_tasks(pending.load(task_pending.path()))

    seen = _reader_during(linked, span)
    assert seen == [["[link_resolves] T01: because names unknown decision D06"]]


def test_dg_apply_holds_the_pair(linked):
    """End to end, through the command. Nothing is left half-applied and the
    project is valid afterwards."""
    _stage_a_repair(linked)
    res = runner.invoke(app, ["--project", str(linked.root), "apply"])
    assert res.exit_code == 0, res.output
    assert check.errors() == []
    assert pending.load(linked.pending) == []
    assert pending.load(task_pending.path()) == []
