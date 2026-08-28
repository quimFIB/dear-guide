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


def test_duplicate_vertex_ids_surface_as_store_loads(store, g):
    """The check-level face of audit A5: the refusal in `Graph.load` arrives
    here as a violation naming the id, not as a clean report or a crash."""
    import json
    write(g)
    raw = json.loads((store / "decisions.json").read_text(encoding="utf-8"))
    raw["vertices"].append(dict(raw["vertices"][0]))
    (store / "decisions.json").write_text(json.dumps(raw), encoding="utf-8")
    hits = run()
    assert [v.check for v in hits] == ["store_loads"]
    assert hits[0].blocking and "D01" in str(hits[0])


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


def test_an_unreadable_view_is_reported_not_fatal(store, g):
    """Found by the re-audit: the store's load was guarded, the view's read was
    not — an unreadable view crashed `check.run`, taking `dg check`, `dg brief`
    and the session hook down with it; only the gate's catch-all held."""
    write(g)
    (store / "decision-graph.md").unlink()
    (store / "decision-graph.md").mkdir()
    hits = run()
    assert any(v.check == "stale_view" and "could not be read" in str(v)
               for v in hits)
    assert all(v.check in CHECKS for v in hits)


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


def test_advisory_findings_warn_but_never_fail(store, g):
    """Audit B3. An isolated vertex is the documented-normal look of a fresh
    graph (`model.Violation`), yet the shipped pytest plugin failed CI on it.
    Advisory findings now surface through pytest's warning summary; only
    blocking violations fail, one test per rule."""
    from dgraph import testing
    from dgraph.model import Violation

    with pytest.warns(UserWarning, match="no_orphans"):
        testing.test_decision_graph_advisory_warnings(
            [Violation("no_orphans", "D01 is connected to nothing", "warning")])
    # a clean graph: no warning, and — the point — never an assertion
    testing.test_decision_graph_advisory_warnings([])


def _emitting_source() -> str:
    """Tool source with the CHECKS declaration itself cut out.

    The declaration is a run of string literals naming every check, so leaving
    it in makes "is this name emitted anywhere?" true by construction — which
    is why the first version of the guard below could not fail for any input.

    Every module that may emit a Violation belongs in this list; a check
    emitted from one that is missing looks exactly like a dead check.
    """
    import inspect

    from dgraph import check as check_mod
    from dgraph import cross, model, tasks
    src = inspect.getsource(check_mod)
    start = src.index("CHECKS: tuple")
    end = src.index("\n)\n", start) + len("\n)\n")
    return (inspect.getsource(model) + inspect.getsource(tasks)
            + inspect.getsource(cross) + src[:start] + src[end:])


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


# ---- audit F14: the option `dgraph.testing` documents ----------------------


def test_the_pytest_plugin_registers_its_option():
    """Audit F14. `dgraph/testing.py` is the module projects are told to
    `import *` from, so its docstring is the interface — and it documented
    `--decision-graph PATH` "if the plugin is registered" when there was no
    `pytest_addoption` anywhere in the package and no `pytest11` entry point to
    register one with. The flag was an `unrecognized arguments` error.
    """
    import tomllib
    from pathlib import Path

    from dgraph import testing

    assert hasattr(testing, "pytest_addoption")

    root = Path(__file__).resolve().parent.parent
    meta = tomllib.loads((root / "pyproject.toml").read_text())
    points = meta["project"]["entry-points"]["pytest11"]
    assert "dgraph.testing" in points.values()


def test_the_option_chooses_the_project(tmp_path, monkeypatch):
    """What the option is for: `$DG_PROJECT` is process-wide, so it cannot say
    "this test run, this project" without leaking into everything else the
    shell goes on to do. A repository holding more than one graph needs that.
    """
    from dgraph import testing

    monkeypatch.delenv("DG_PROJECT", raising=False)
    (tmp_path / "decisions.json").write_text(
        '{"areas": [], "vertices": [], "edges": []}\n')

    class _Config:
        rootpath = tmp_path.parent          # deliberately not the project
        def getoption(self, name, default=None):
            return str(tmp_path)

    proj = testing.decision_project.__wrapped__(_Config())
    assert proj.root == tmp_path.resolve()


# ---- verbose_field -------------------------------------------------------


VERBOSE = "x " * 300          # 599 characters


def test_a_long_answer_is_reported_as_a_warning(store, g):
    """Never blocking, for the reason `stale_view` gives: this is not a store
    invariant. A graph written before the rule existed must stay committable —
    a long answer is legal, representable and somebody's actual record."""
    write(g)
    g.active_edge("D01").answer = VERBOSE
    g.save()
    hits = [v for v in run() if v.check == "verbose_field"]
    assert len(hits) == 1
    assert hits[0].severity == "warning" and not hits[0].blocking
    assert "D01" in hits[0].message and "answer" in hits[0].message
    # The finding has to say what makes the prose unnecessary, or it reads as
    # a complaint about length and gets ignored as one.
    assert "dg context D01" in hits[0].message


def test_a_superseded_answer_is_not_reported(store, g):
    """The record of what was believed, never edited. A warning about one names
    something nobody may act on — the store keeps it forever on purpose."""
    write(g)
    [e for e in g.edges if not e.active][0].answer = VERBOSE
    g.save()
    assert not [v for v in run() if v.check == "verbose_field"]


def test_the_environment_does_not_reach_the_check(store, g, monkeypatch):
    """`$DG_TERSE` is a launcher's rule for its agents; this warning is read by
    a supervisor, who never has one set. A check that went quiet exactly when
    nobody had configured it would be silent in every project it is for."""
    write(g)
    monkeypatch.delenv("DG_TERSE", raising=False)
    g.active_edge("D01").answer = VERBOSE
    g.save()
    assert [v for v in run() if v.check == "verbose_field"]


def test_one_finding_per_record_however_many_fields_are_long(store, g):
    """One long record is one finding. A listing per field would rank a single
    verbose decision above every other problem in the graph."""
    write(g)
    e = g.active_edge("D01")
    e.answer = e.falsifier = VERBOSE
    g.save()
    assert len([v for v in run() if v.check == "verbose_field"]) == 1
