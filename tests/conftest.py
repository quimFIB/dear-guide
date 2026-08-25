"""A small synthetic graph exercising every shape the model allows."""

import json

import pytest

from dgraph import project
from dgraph.model import Graph

FIXTURE = {
    "areas": ["Alpha", "Beta"],
    "vertices": [
        {"id": "D01", "title": "Root question", "area": "Alpha", "status": "DECIDED"},
        {"id": "D02", "title": "A consequence", "area": "Alpha", "status": "DECIDED"},
        {"id": "D03", "title": "A terminal one", "area": "Alpha", "status": "DECIDED"},
        {"id": "D04", "title": "Downstream", "area": "Beta", "status": "DECIDED"},
        {"id": "D05", "title": "Still open", "area": "Beta", "status": "OPEN",
         "note": "Nobody has decided this yet."},
        {"id": "D06", "title": "Waiting on D05", "area": "Beta",
         "status": "BLOCKED:D05"},
    ],
    "edges": [
        {"from": "D01", "to": ["D02", "D03"], "active": True,
         "answer": "The root answer.", "falsifier": "new evidence appears",
         "source": "discussion", "date": "2026-01-01"},
        {"from": "D01", "to": ["D02"], "active": False,
         "answer": "An older answer.", "summary": "older answer",
         "replaced_by": "the root answer", "why": "it was measured wrong",
         "date": "2025-12-01"},
        {"from": "D02", "to": ["D04"], "active": True,
         "answer": "Second answer.", "falsifier": "ANALYTIC — follows by argument",
         "source": "discussion", "date": "2026-01-02"},
        {"from": "D03", "to": [], "active": True,
         "answer": "Terminal answer, opens nothing.",
         "source": "discussion", "date": "2026-01-03"},
        {"from": "D04", "to": ["D05"], "active": True,
         "answer": "Third answer.", "falsifier": "the corpus changes",
         "source": "report/x.md", "date": "2026-01-04"},
        {"from": "D05", "to": ["D06"], "active": True},
    ],
}


#: What the tray adds to an op that is not part of the op: `saw` is
#: `pending.stamp`'s drift fingerprint, `ref` is the stable id `dg drop` and
#: `dg edit` address it by. A test comparing a staged op against the dict it
#: wrote is otherwise comparing against the tray's own bookkeeping.
TRAY_KEYS = ("saw", "ref")


def bare(ops):
    """`ops` without the tray's bookkeeping. Takes one op or a list of them."""
    if isinstance(ops, dict):
        return {k: v for k, v in ops.items() if k not in TRAY_KEYS}
    return [bare(o) for o in ops]


def finished(t, date, outcome, status="DONE"):
    """Set a task up as finished, the way an applied op leaves it.

    A helper rather than three lines in twenty tests, because `done` and
    `outcome` are derived — the pair a test wants to state lives in a
    `Completion`, and the status is what makes it live. Appends, so calling it
    twice is the second-completion case rather than a rewrite of the first.
    """
    from dgraph.tasks import Completion
    t.status = status
    t.completions.append(Completion(date=date, outcome=outcome))
    return t


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A project directory holding the fixture graph."""
    (tmp_path / "decisions.json").write_text(
        json.dumps(FIXTURE, indent=2), encoding="utf-8"
    )
    monkeypatch.setattr(project, "_override", tmp_path)
    return tmp_path


@pytest.fixture
def g(store):
    return Graph.load()


#: A task graph over the same areas, exercising every status and a chain deep
#: enough to have a middle. T04 is unconnected, which is ordinary for tasks.
TASK_FIXTURE = {
    "areas": ["Alpha", "Beta"],
    "tasks": [
        {"id": "T01", "title": "First, a prerequisite", "area": "Alpha",
         "status": "DONE", "done": "2026-01-05", "outcome": "PR #1"},
        {"id": "T02", "title": "Second, now startable", "area": "Alpha",
         "status": "TODO"},
        {"id": "T03", "title": "Third, still waiting", "area": "Beta",
         "status": "TODO"},
        {"id": "T04", "title": "Unconnected, which is fine", "area": "Beta",
         "status": "DOING", "note": "Nobody has finished this yet."},
    ],
    "edges": [
        {"from": "T01", "to": ["T02"], "kind": "precedes"},
        {"from": "T02", "to": ["T03"], "kind": "precedes"},
    ],
}


@pytest.fixture
def task_store(tmp_path, monkeypatch):
    """A project directory holding the task fixture, and no decision store."""
    (tmp_path / "tasks.json").write_text(
        json.dumps(TASK_FIXTURE, indent=2), encoding="utf-8"
    )
    monkeypatch.setattr(project, "_override", tmp_path)
    return tmp_path


@pytest.fixture
def tg(task_store):
    from dgraph.tasks import TaskGraph
    return TaskGraph.load(task_store / "tasks.json")


@pytest.fixture
def tty(monkeypatch):
    """Pretend there is a person at a terminal.

    `CliRunner` owns stdin, so a test cannot make it a tty. Anything that would
    block on a human — a prompt, a confirmation, `$DG_EDIT` — goes through
    `cli._interactive`, and the interactive tests say so explicitly rather than
    getting it by accident.
    """
    from dgraph import cli
    monkeypatch.setattr(cli, "_interactive", lambda: True)
