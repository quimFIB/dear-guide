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


def test_a_validator_crash_degrades_to_a_violation(store, g, monkeypatch):
    """Audit A1, belt and braces: whatever future bug makes `validate()` raise,
    `run()` must answer with a blocking violation — a crash here is read by the
    commit gate as "no verdict" and fails open."""
    write(g)

    def boom(self):
        raise KeyError("D99")

    monkeypatch.setattr(Graph, "validate", boom)
    hits = run()
    assert [v.check for v in hits] == ["store_loads"]
    assert hits[0].blocking and "validate()" in str(hits[0])


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


def _emitting_source() -> str:
    """Tool source with the CHECKS declaration itself cut out.

    The declaration is a run of string literals naming every check, so leaving
    it in makes "is this name emitted anywhere?" true by construction — which
    is why the first version of the guard below could not fail for any input.
    """
    import inspect

    from dgraph import check as check_mod
    from dgraph import model
    src = inspect.getsource(check_mod)
    start = src.index("CHECKS: tuple")
    end = src.index("\n)\n", start) + len("\n)\n")
    return inspect.getsource(model) + src[:start] + src[end:]


def test_the_reachability_guard_can_actually_fail():
    """Guard the guard: the declaration must really be gone from the haystack.

    Without this, `test_every_declared_check_is_reachable` silently degrades
    back into a tautology the moment the excision stops matching.
    """
    src = _emitting_source()
    assert "CHECKS: tuple" not in src
    assert '"stale_view"' in src          # emitted in check.run
    assert '"totally_dead_check"' not in src


@pytest.mark.parametrize("name", CHECKS)
def test_every_declared_check_is_reachable(name):
    """CHECKS is what pytest parametrises over; a name nobody emits is dead."""
    assert f'"{name}"' in _emitting_source(), (
        f"{name} is declared in CHECKS but never emitted"
    )


def test_no_check_is_emitted_without_being_declared(store, g):
    """The guard that would have caught stale_block going unlisted."""
    write(g)
    bad = Graph.load()
    bad.active_edge("D01").to.append("D99")
    bad.active_edge("D01").falsifier = None
    bad.save()
    assert {v.check for v in run()} <= set(CHECKS)


def test_provisional_with_settled_premises_is_reported(store, g):
    """PROVISIONAL means "rests on a premise under review". Once the premise is
    settled again the status is no longer true, and nothing else notices: it
    counts as settled, so `propagation` is satisfied. A warning, not an error —
    it is unfinished work, not a broken graph."""
    from dataclasses import replace
    g.vertices["D02"] = replace(g.vertices["D02"], status="PROVISIONAL")
    g.save()
    write(Graph.load())
    hits = [v for v in run() if v.check == "stale_provisional"]
    assert hits and not hits[0].blocking
    assert "dg confirm D02" in str(hits[0])


def test_provisional_under_a_reopened_premise_is_not_reported(store, g):
    """The legitimate case: D01 is under review, so D02 resting on it is exactly
    what PROVISIONAL is for."""
    from dataclasses import replace
    g.vertices["D01"] = replace(g.vertices["D01"], status="REOPENED")
    g.vertices["D02"] = replace(g.vertices["D02"], status="PROVISIONAL")
    g.save()
    write(Graph.load())
    assert not [v for v in run() if v.check == "stale_provisional"]
