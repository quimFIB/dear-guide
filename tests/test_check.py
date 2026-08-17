"""The shared validity entry point — what `dg check` and pytest both call."""

import pytest

from dgraph import project
from dgraph.check import CHECKS, errors, run
from dgraph.model import Graph
from dgraph.render import write


def test_clean_project_has_no_violations(store, g):
    write(g)
    assert run() == []


def test_missing_view_is_reported(store, g):
    assert [v.check for v in run()] == ["stale_view"]


def test_edited_view_is_reported(store, g):
    write(g)
    (store / "decision-graph.md").write_text("hand-edited\n", encoding="utf-8")
    hits = [v for v in run() if v.check == "stale_view"]
    assert hits and "generated" in str(hits[0])


def test_missing_store_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(project, "_override", tmp_path)
    hits = run()
    assert [v.check for v in hits] == ["store_loads"]
    assert hits[0].blocking


def test_malformed_store_is_reported(tmp_path, monkeypatch):
    (tmp_path / "decisions.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(project, "_override", tmp_path)
    hits = run()
    assert [v.check for v in hits] == ["store_loads"]


def test_graph_violations_surface(store, g):
    write(g)
    bad = Graph.load()
    bad.active_edge("D01").to.append("D99")
    bad.save()
    assert "no_dangling_refs" in {v.check for v in run()}


def test_errors_filters_out_warnings(store, g):
    write(g)
    bad = Graph.load()
    bad.vertices["D99"] = bad.vertices["D01"].__class__(
        id="D99", title="Floating", area="Alpha", status="OPEN"
    )
    bad.save()
    all_hits = {v.check for v in run()}
    assert "no_orphans" in all_hits
    assert "no_orphans" not in {v.check for v in errors()}


@pytest.mark.parametrize("name", CHECKS)
def test_every_declared_check_is_reachable(name):
    """CHECKS is what pytest parametrises over; a name nobody emits is dead."""
    import inspect

    from dgraph import check as check_mod
    from dgraph import model
    src = inspect.getsource(model) + inspect.getsource(check_mod)
    assert f'"{name}"' in src, f"{name} is declared but never emitted"


def test_no_check_is_emitted_without_being_declared(store, g):
    """The guard that would have caught stale_block going unlisted."""
    write(g)
    bad = Graph.load()
    bad.active_edge("D01").to.append("D99")
    bad.active_edge("D01").falsifier = None
    bad.save()
    assert {v.check for v in run()} <= set(CHECKS)
