"""Behaviour of the tool: model queries, rendering, staging, propagation, apply."""

import json

import pytest

from dgraph import pending, project
from dgraph.model import Graph
from dgraph.render import render, write

# ---- model ---------------------------------------------------------------


def test_fixture_is_valid(g):
    assert g.validate() == []


def test_dependency_is_derived_from_edges(g):
    assert g.depends("D02") == ["D01"]
    assert g.depends("D01") == []
    assert g.children("D01") == ["D02", "D03"]


def test_superseded_edges_do_not_create_dependencies(g):
    """The inactive D01 edge must not contribute to the live structure."""
    assert len(g.history("D01")) == 1
    assert g.active_edge("D01").answer == "The root answer."
    assert g.children("D01") == ["D02", "D03"]


def test_payload_less_edge_is_a_dependency_without_a_decision(g):
    e = g.active_edge("D05")
    assert e is not None and not e.decided
    assert g.depends("D06") == ["D05"]


def test_terminal_decision(g):
    assert g.active_edge("D03").terminal
    assert g.children("D03") == []


def test_descendants_and_depth(g):
    assert g.descendants("D01") == {"D02", "D03", "D04", "D05", "D06"}
    assert g.depth("D01") == 0
    assert g.depth("D06") == 4


def test_frontier(g):
    assert g.frontier() == ["D05", "D06"]


def test_path(g):
    assert g.path("D01", "D05") == ["D01", "D02", "D04", "D05"]
    assert g.path("D03", "D05") is None


# ---- validation ----------------------------------------------------------


def _check(g, name):
    return [v for v in g.validate() if v.check == name]


def test_detects_dangling_edge(g):
    g.active_edge("D02").to.append("D99")
    assert _check(g, "no_dangling_refs")


def test_detects_two_active_edges(g):
    from dgraph.model import Edge
    g.edges.append(Edge(src="D01", to=["D04"], active=True))
    assert _check(g, "one_active_edge")


def test_detects_missing_falsifier(g):
    g.active_edge("D01").falsifier = None
    assert _check(g, "decided_complete")


def test_detects_unpropagated_reopen(g):
    from dataclasses import replace
    g.vertices["D02"] = replace(g.vertices["D02"], status="REOPENED")
    hits = _check(g, "propagation")
    assert hits and "D04" in str(hits[0])


def test_detects_stale_block(g):
    from dataclasses import replace
    g.vertices["D05"] = replace(g.vertices["D05"], status="DECIDED")
    assert _check(g, "stale_block")


def test_detects_cycle(g):
    g.active_edge("D04").to.append("D01")
    assert _check(g, "acyclic")


# ---- rendering -----------------------------------------------------------


def test_render_is_deterministic(g):
    assert render(g) == render(Graph.load())


def test_render_round_trips(g, tmp_path):
    p = tmp_path / "copy.json"
    g.save(p)
    assert Graph.load(p).to_dict() == g.to_dict()


def test_save_is_stable(g, tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    g.save(a)
    Graph.load(a).save(b)
    assert a.read_text() == b.read_text()


def test_view_contains_every_vertex_and_the_frontier(g, store):
    out = render(g)
    for vid in g.vertices:
        assert f"### {vid} —" in out
    assert "D05, D06." in out
    assert "older answer" in out  # superseded history survives


def test_write_targets_the_project(g, store):
    write(g)
    assert (store / "decision-graph.md").exists()


# ---- staging -------------------------------------------------------------


def test_staging_round_trips(store):
    assert pending.load() == []
    pending.stage({"op": "set_status", "vertex": "D05", "status": "OPEN"})
    pending.stage({"op": "set_status", "vertex": "D06", "status": "OPEN"})
    assert len(pending.load()) == 2
    pending.drop(0)
    assert [o["vertex"] for o in pending.load()] == ["D06"]
    pending.clear()
    assert pending.load() == [] and not project.find().pending.exists()


# ---- propagation ---------------------------------------------------------


def test_reopen_propagates_to_exactly_the_decided_descendants(g):
    ops = pending.expand(g, {"op": "reopen", "vertex": "D01", "why": "x"})
    marked = {o["vertex"] for o in ops if o["op"] == "set_status"}
    assert marked == {"D02", "D03", "D04"}  # D05 is OPEN, D06 BLOCKED
    assert all(o["status"] == "PROVISIONAL"
               for o in ops if o["op"] == "set_status")


def test_reopen_keeps_dependencies_but_drops_the_answer(g):
    ops = pending.expand(g, {"op": "reopen", "vertex": "D01", "why": "x"})
    out = pending.apply_all(g, ops)
    assert out.vertices["D01"].status == "REOPENED"
    assert out.children("D01") == g.children("D01")
    assert out.active_edge("D01").answer is None
    assert len(out.history("D01")) == 2


def test_unpropagated_reopen_is_rejected(g):
    with pytest.raises(pending.ApplyError, match="propagation"):
        pending.apply_all(g, [{"op": "reopen", "vertex": "D01", "why": "x"}])


# ---- apply ---------------------------------------------------------------


def test_close_an_open_vertex(g):
    out = pending.apply_all(g, [
        {"op": "close", "vertex": "D05", "answer": "yes", "source": "discussion",
         "falsifier": "ANALYTIC — test", "to": [], "date": "2026-02-01"},
        {"op": "set_status", "vertex": "D06", "status": "OPEN"},
    ])
    assert out.vertices["D05"].status == "DECIDED"
    assert "D05" not in out.frontier()


def test_close_preserves_existing_dependants(g):
    out = pending.apply_all(g, [
        {"op": "close", "vertex": "D05", "answer": "a", "source": "s",
         "falsifier": "f", "to": [], "date": "2026-02-01"},
        {"op": "set_status", "vertex": "D06", "status": "OPEN"},
    ])
    assert "D06" in out.children("D05")


def test_apply_rejects_unknown_target(g):
    with pytest.raises(pending.ApplyError, match="unknown vertex D99"):
        pending.apply_all(g, [{"op": "close", "vertex": "D05", "answer": "a",
                               "source": "s", "falsifier": "f", "to": ["D99"]}])


def test_apply_rejects_deciding_twice(g):
    with pytest.raises(pending.ApplyError, match="reopen it first"):
        pending.apply_all(g, [{"op": "close", "vertex": "D01", "answer": "a",
                               "source": "s", "falsifier": "f", "to": []}])


def test_apply_leaves_the_input_graph_untouched(g):
    before = json.dumps(g.to_dict())
    with pytest.raises(pending.ApplyError):
        pending.apply_all(g, [{"op": "reopen", "vertex": "D01", "why": "x"}])
    assert json.dumps(g.to_dict()) == before


def test_close_fills_in_what_it_replaced(g):
    ops = pending.expand(g, {"op": "reopen", "vertex": "D01", "why": "x",
                             "summary": "the root answer"})
    out = pending.apply_all(g, ops)
    out = pending.apply_all(out, [
        {"op": "close", "vertex": "D01", "answer": "A newer answer.",
         "source": "discussion", "falsifier": "f", "to": ["D02", "D03"],
         "summary": "newer answer", "date": "2026-03-01"},
    ] + [{"op": "set_status", "vertex": v, "status": "DECIDED"}
         for v in ("D02", "D03", "D04")])
    hist = out.history("D01")
    assert len(hist) == 2
    # only the edge left open by the reopen is filled in; the older one keeps
    # the answer that actually replaced it
    assert hist[0].replaced_by == "the root answer"
    assert hist[1].replaced_by == "newer answer"


def test_add_vertex_then_link(g):
    out = pending.apply_all(g, [
        {"op": "add_vertex", "id": "D99", "title": "New", "area": "Beta",
         "status": "OPEN"},
        {"op": "add_edge", "from": "D05", "to": ["D99"]},
    ])
    assert "D05" in out.depends("D99")


def test_add_duplicate_vertex_is_rejected(g):
    with pytest.raises(pending.ApplyError, match="already exists"):
        pending.apply_all(g, [{"op": "add_vertex", "id": "D01", "title": "x",
                               "area": "Alpha"}])


# ---- project resolution --------------------------------------------------


def test_project_is_found_by_walking_up(store, monkeypatch):
    monkeypatch.setattr(project, "_override", None)
    nested = store / "a" / "b"
    nested.mkdir(parents=True)
    assert project.find(nested).root == store


def test_env_var_wins_over_cwd(store, monkeypatch, tmp_path):
    monkeypatch.setattr(project, "_override", None)
    monkeypatch.setenv("DG_PROJECT", str(store))
    assert project.find(tmp_path).root == store
