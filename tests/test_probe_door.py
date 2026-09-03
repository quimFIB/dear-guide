"""`dg probe`, present mode (T54): scoped, tray-aware, batched.

Every pre-commitment beside what it is judged against, through the batched
evaluator; a plain install has only `prose`, so every verdict here is
`unjudged` unless a fake domain is installed, and the door's exit code is
non-zero only when something fired.
"""

import copy
import json

import pytest
from typer.testing import CliRunner

from dgraph import domains, pending, probing, task_pending
from dgraph.cli import app
from dgraph.model import Graph
from dgraph.tasks import TaskGraph
from tests.conftest import FIXTURE, TASK_FIXTURE
from tests.test_domains import Fake, _EP, _install


@pytest.fixture(autouse=True)
def _fresh():
    domains.forget()
    yield
    domains.forget()


@pytest.fixture
def run(store, task_store, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERM", "dumb")
    runner = CliRunner()
    return lambda *args, input=None: runner.invoke(
        app, ["--project", str(store), *args], input=input)


# ---- the rows ---------------------------------------------------------------


def test_every_decided_edge_with_a_falsifier_is_a_row_and_nothing_else_is():
    g = Graph.from_dict(FIXTURE)
    rows = probing.rows(g, None)
    edges = {r.id for r in rows if r.slot == "edge"}
    assert edges == {v for v in g.vertices
                     if (e := g.active_edge(v)) is not None and e.decided
                     and e.falsifier}
    assert all(r.kind == probing.FALSIFIER for r in rows if r.slot == "edge")
    assert not [r for r in rows if r.slot == "vertex"]


def test_an_open_vertex_with_a_rule_and_a_task_with_done_when_are_rows(store, task_store):
    g = pending.apply_all(Graph.from_dict(FIXTURE), [
        {"op": "set_fields", "vertex": "D05", "rule": "the sweep finishes"}])
    tg = TaskGraph.from_dict(TASK_FIXTURE)
    tid = next(t for t in tg.tasks if tg.tasks[t].unfinished)
    done = next(t for t in tg.tasks if tg.tasks[t].status == "DONE")
    tg = task_pending.apply_all(tg, [
        {"op": "set_fields", "task": tid, "done_when": "the file exists"},
        {"op": "set_link", "task": tid, "evidence_for": "D05"}])
    rows = {r.id: r for r in probing.rows(g, tg)}
    assert rows["D05"].slot == "vertex" and rows["D05"].kind == probing.RULE
    assert any(tid in b for b in rows["D05"].beside)      # its evidence
    assert rows[tid].slot == "task" and rows[tid].kind == probing.DONE_WHEN
    assert done not in rows                                # finished work has no row


def test_a_probe_on_the_record_is_the_kind_and_carries_its_date(store):
    g = pending.apply_all(Graph.from_dict(FIXTURE), [
        {"op": "reprobe", "vertex": "D05", "probe": {"kind": "fake.ok", "args": {}},
         "date": "2026-02-02"}])
    rows = {r.id: r for r in probing.rows(g, None)}
    assert rows["D05"].kind == "fake.ok" and rows["D05"].probe_date == "2026-02-02"


def test_a_staged_op_naming_the_record_is_shown(store):
    g = Graph.from_dict(FIXTURE)
    rows = {r.id: r for r in probing.rows(
        g, None, d_ops=[{"op": "reopen", "vertex": "D01", "why": "moved"}])}
    assert rows["D01"].staged == "reopen staged as op 0"
    assert rows["D02"].staged is None


def test_a_provisional_row_is_shown_beside_the_later_dated_ancestors(store):
    raw = copy.deepcopy(FIXTURE)
    for v in raw["vertices"]:
        if v["id"] == "D02":
            v["status"] = "PROVISIONAL"
    for e in raw["edges"]:
        if e["from"] == "D01" and e["active"]:
            e["date"] = "2026-06-06"          # re-decided after D02's edge
    g = Graph.from_dict(raw)
    row = next(r for r in probing.rows(g, None) if r.id == "D02")
    assert any("heuristic" in b for b in row.beside)
    assert any(b.strip().startswith("D01") and "→ now" in b for b in row.beside)
    # and where nothing is later-dated, it says so rather than guessing
    for e in raw["edges"]:
        if e["from"] == "D01" and e["active"]:
            e["date"] = "2025-01-01"
    row = next(r for r in probing.rows(Graph.from_dict(raw), None) if r.id == "D02")
    assert any("no premise carries a later date" in b for b in row.beside)


# ---- scope ------------------------------------------------------------------


def test_a_bare_call_past_a_screen_asks_for_a_scope(monkeypatch):
    monkeypatch.setattr(probing, "SCREEN", 1)
    rows = probing.rows(Graph.from_dict(FIXTURE), None)
    why = probing.ask_for_scope(rows, probing.Scope())
    assert why and "--all" in why and str(len(rows)) in why
    assert probing.ask_for_scope(rows, probing.Scope(all=True)) is None
    assert probing.ask_for_scope(rows, probing.Scope(ids=["D01"])) is None


def test_select_by_id_provisional_area_and_since(store):
    raw = copy.deepcopy(FIXTURE)
    for v in raw["vertices"]:
        if v["id"] == "D04":
            v["status"] = "PROVISIONAL"
    g = Graph.from_dict(raw)
    rows = probing.rows(g, None)
    assert [r.id for r in probing.select(rows, g, probing.Scope(ids=["D01"]))] == ["D01"]
    assert [r.id for r in probing.select(rows, g, probing.Scope(provisional=True))] == ["D04"]
    beta = probing.select(rows, g, probing.Scope(area="Beta"))
    assert beta and all(g.vertices[r.id].area == "Beta" for r in beta)
    late = probing.select(rows, g, probing.Scope(since="2026-01-01"))
    assert all(g.active_edge(r.id).date >= "2026-01-01" for r in late)


# ---- judged ------------------------------------------------------------------


def test_the_door_batches_through_the_evaluator_and_reports_fired(monkeypatch, store):
    fake = Fake("fired")
    _install(monkeypatch, _EP("fake", "f:F", fake))
    g = pending.apply_all(Graph.from_dict(FIXTURE), [
        {"op": "reprobe", "vertex": "D05", "probe": {"kind": "fake.ok", "args": {}},
         "date": "2026-02-02"},
        {"op": "reprobe", "vertex": "D06", "probe": {"kind": "fake.ok", "args": {}},
         "date": "2026-02-02"}])
    rows = probing.judge(probing.rows(g, None), store, timeout=5)
    assert fake.calls == 1
    by = {r.id: r.verdict for r in rows}
    assert by["D05"] == by["D06"] == "fired" and by["D01"] == "unjudged"
    f = probing.findings(rows)
    assert sum(v.check == "probe_fired" for v in f) == 2
    assert all(v.origin == "domain" for v in f)


# ---- the command --------------------------------------------------------------


def test_dg_probe_presents_and_exits_zero_on_a_plain_install(run, store):
    res = run("probe", "--all")
    assert res.exit_code == 0, res.output
    assert "D01" in res.output and "falsifier" in res.output
    assert "unjudged" in res.output and "fired 0" in res.output


def test_dg_probe_one_id_and_an_unknown_one(run, store):
    res = run("probe", "D01")
    assert res.exit_code == 0 and "D01" in res.output and "D02" not in res.output
    res = run("probe", "D99")
    assert res.exit_code == 1 and "unknown record" in res.output


def test_dg_probe_asks_for_a_scope_past_a_screen(run, store, monkeypatch):
    monkeypatch.setattr(probing, "SCREEN", 0)
    res = run("probe")
    assert res.exit_code == 2 and "Name a scope" in res.output


def test_dg_probe_shows_the_staged_act(run, store):
    run("reopen", "D01", "--yes", "--why", "moved")
    res = run("probe", "D01")
    assert "reopen staged" in res.output


def test_dg_probe_exits_nonzero_when_anything_fired(run, store, monkeypatch):
    _install(monkeypatch, _EP("fake", "f:F", Fake("fired")))
    run("reprobe", "D05", "--probe", json.dumps({"kind": "fake.ok", "args": {}}))
    run("apply")
    res = run("probe", "D05")
    assert res.exit_code == 1 and "fired 1" in res.output, res.output


def test_dg_probe_shows_a_task_beside_the_work(run, store, task_store):
    tg = TaskGraph.load(task_store / "tasks.json")
    tid = next(t for t in tg.tasks if tg.tasks[t].unfinished)
    run("task", "amend", tid, "--done-when", "the file exists"); run("apply")
    res = run("probe", tid)
    assert res.exit_code == 0 and "done when" in res.output and "the file exists" in res.output


def test_dg_probe_writes_nothing(run, store):
    before = (store / "decisions.json").read_bytes()
    run("probe", "--all")
    assert (store / "decisions.json").read_bytes() == before
    assert pending.load(store / ".dgraph-pending.json") == []
