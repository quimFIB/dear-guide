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
