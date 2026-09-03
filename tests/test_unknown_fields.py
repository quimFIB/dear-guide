"""R0: a field this version cannot read is carried, named, and never dropped.

The case is version skew, which is this tool's ordinary state: the plugin
cache does not refresh itself, so two clones of one graph run two versions
of `dg` for weeks. Before `extra` existed an older install silently dropped
an unknown *edge* key on its next save and crashed on an unknown *vertex*
key — a blocking `store_loads`, and the commit gate then denied every commit
in that clone. Both were verified against the loader before this was built.

What is pinned: the field survives a load and a save on every record kind,
survives the ops that rewrite the record around it, is reported as a
*warning* by both validators, cannot travel as an op and says so at the
integration seam, and is still refused by `dg import`, which adopts a
document rather than keeping a record.
"""

import copy
import json

import pytest

from dgraph import integrate, pending, task_pending
from dgraph.check import run
from dgraph.model import Graph
from dgraph.tasks import TaskGraph
from tests.conftest import FIXTURE, TASK_FIXTURE

#: A field no version reads. It was `probe` until `probe` landed on the edge
#: (T50), at which point the test that proves an unknown field survives was
#: asserting about a known one — so the example is now a name nothing in the
#: proposal plans to use.
SEAL = {"kind": "rocq.statement_unchanged", "args": {"sha": "abc"}}
STAMP = [{"kind": "rocq.constant", "ref": "Closure.closed_under_step"}]


def _decisions() -> dict:
    raw = copy.deepcopy(FIXTURE)
    raw["vertices"][0]["stamp"] = STAMP
    raw["edges"][0]["seal"] = SEAL
    return raw


def _tasks() -> dict:
    raw = copy.deepcopy(TASK_FIXTURE)
    raw["tasks"][1]["seal"] = SEAL
    raw["edges"][0]["weight"] = 3
    return raw


# ---- carried ---------------------------------------------------------------


def test_an_unknown_edge_field_survives_a_load_and_a_save():
    """The silent half of the old behaviour: it used to vanish here."""
    g = Graph.from_dict(_decisions())
    assert g.active_edge("D01").extra == {"seal": SEAL}
    out = g.to_dict()
    assert next(e for e in out["edges"] if e["from"] == "D01"
                and e["active"])["seal"] == SEAL


def test_an_unknown_vertex_field_no_longer_crashes_the_loader():
    """The loud half: `Vertex(**v)` raised, and the gate denied every commit."""
    g = Graph.from_dict(_decisions())
    assert g.vertices["D01"].extra == {"stamp": STAMP}
    assert next(v for v in g.to_dict()["vertices"]
                if v["id"] == "D01")["stamp"] == STAMP


def test_an_unknown_task_and_task_edge_field_survive():
    tg = TaskGraph.from_dict(_tasks())
    assert tg.tasks["T02"].extra == {"seal": SEAL}
    assert tg.edges[0].extra == {"weight": 3}
    out = tg.to_dict()
    assert next(t for t in out["tasks"] if t["id"] == "T02")["seal"] == SEAL
    assert next(e for e in out["edges"] if e["from"] == "T01")["weight"] == 3


def test_the_round_trip_is_byte_stable():
    """Load, save, load, save: the second file is the first."""
    first = json.dumps(Graph.from_dict(_decisions()).to_dict(), indent=2)
    second = json.dumps(Graph.from_dict(json.loads(first)).to_dict(), indent=2)
    assert first == second


# ---- named, as a warning ----------------------------------------------------


def test_neither_validate_knows_the_rule():
    """As `verbose_field`: not a store invariant, so no write path consults
    it and `apply_all` cannot refuse to carry a field for a newer install."""
    assert not [v for v in Graph.from_dict(_decisions()).validate()
                if v.check == "unknown_field"]
    assert not [v for v in TaskGraph.from_dict(_tasks()).validate()
                if v.check == "unknown_field"]


def test_dg_check_names_every_field_as_a_warning_with_its_store(
        tmp_path, monkeypatch):
    from dgraph import project
    (tmp_path / "decisions.json").write_text(json.dumps(_decisions()))
    (tmp_path / "tasks.json").write_text(json.dumps(_tasks()))
    monkeypatch.setattr(project, "_override", tmp_path)
    found = [v for v in run() if v.check == "unknown_field"]
    where = {v.message.split(" carries ")[0]: v.origin for v in found}
    assert where == {"D01": "decision", "the edge from D01": "decision",
                     "T02": "task", "the precedes edge from T01": "task"}
    assert all(not v.blocking for v in found)
    assert "`stamp`" in next(v.message for v in found
                             if v.message.startswith("D01"))
    assert not [v for v in run() if v.blocking], "a legal store must commit"


# ---- kept across the ops that rewrite the record ---------------------------


def test_an_amend_and_a_close_keep_what_they_do_not_know():
    g = Graph.from_dict(_decisions())
    out = pending.apply_all(g, [
        {"op": "set_fields", "vertex": "D01", "title": "retitled"},
        {"op": "close", "vertex": "D05", "answer": "a", "source": "s",
         "falsifier": "f", "to": ["D06"]},
    ])
    assert out.vertices["D01"].title == "retitled"
    assert out.vertices["D01"].extra == {"stamp": STAMP}
    assert out.active_edge("D01").extra == {"seal": SEAL}


def test_a_reopen_keeps_the_field_on_the_live_edge_and_archives_none():
    """What an unknown field *means* under a reopen is exactly what this
    version does not know; the install that knows puts it in `PAYLOAD`."""
    g = Graph.from_dict(_decisions())
    out = pending.apply_all(g, pending.expand(
        g, {"op": "reopen", "vertex": "D01", "why": "moved"}))
    live = out.active_edge("D01")
    assert live.answer is None and live.extra == {"seal": SEAL}
    assert all(e.extra == {} for e in out.history("D01"))


def test_a_task_status_change_keeps_the_field():
    tg = TaskGraph.from_dict(_tasks())
    out = task_pending.apply_all(tg, [
        {"op": "set_status", "task": "T02", "status": "DOING"}])
    assert out.tasks["T02"].status == "DOING"
    assert out.tasks["T02"].extra == {"seal": SEAL}


# ---- cannot travel as an op, and says so ------------------------------------


def test_the_seam_reports_an_arriving_unknown_field_as_unexpressible():
    base = Graph.from_dict(FIXTURE)
    theirs = Graph.from_dict(_decisions())
    d = integrate.decisions(base, theirs)
    assert d.ops == [], "nothing about the store changed that an op can say"
    assert len(d.unexpressible) == 2
    assert any(u.startswith("D01 arrives with `stamp`") for u in d.unexpressible)
    assert any(u.startswith("the edge from D01 arrives with `seal`")
               for u in d.unexpressible)

    d = integrate.tasks(TaskGraph.from_dict(TASK_FIXTURE),
                        TaskGraph.from_dict(_tasks()))
    assert any(u.startswith("T02 arrives with `seal`") for u in d.unexpressible)


def test_an_unchanged_unknown_field_is_not_reported():
    """Only a difference is unexpressible; a field both sides carry is silence."""
    both = Graph.from_dict(_decisions())
    assert integrate.decisions(both, copy.deepcopy(both)).unexpressible == []


# ---- the import door still refuses -----------------------------------------


def test_import_refuses_what_load_carries(tmp_path):
    """Two doors, two jobs: a load keeps the record, an import adopts a
    document, and a key nobody named is the writer's to fix there."""
    from dgraph.json_import import ShapeError, read
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(_decisions()))
    with pytest.raises(ShapeError, match='decision D01 has "stamp"'):
        read(path, "decisions")


def test_import_accepts_a_parked_task_and_a_reading(tmp_path):
    """`stops` and `readings` were missing from the import schema for as long
    as the store has held them: found while deriving the edge list from the
    store's own tuple, fixed the same way."""
    from dgraph.json_import import read
    raw = copy.deepcopy(TASK_FIXTURE)
    raw["tasks"][2].update({"status": "PARKED",
                            "stops": [{"why": "later", "date": "2026-01-01"}]})
    raw["tasks"][3].update({"evidence_for": "D01", "readings": [
        {"date": "2026-01-02", "note": "confirms", "against": "D01"}]})
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(raw))
    tg = read(path, "tasks").graph
    assert tg.tasks["T03"].stopped_because == "later"
    assert tg.tasks["T04"].read_against("D01") == "2026-01-02"
