"""The task graph: derived readiness, its own invariants, and the barrier.

Most of what is pinned here is the ways a task is *not* a decision — blocked is
derived rather than stored, abandoning work releases what waited on it, and an
unconnected task is ordinary. The rest guards the barrier: a task store must
never be reachable through a decision command, or vice versa.
"""

import json

import pytest
from typer.testing import CliRunner

from dgraph import pending, project, task_pending, task_render
from dgraph.check import CHECKS, run
from dgraph.cli import app
from dgraph.tasks import TaskGraph

runner = CliRunner()


@pytest.fixture
def run_cli(task_store, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERM", "dumb")

    def go(*args, input=None):
        return runner.invoke(app, ["--project", str(task_store), *args],
                             input=input)
    return go


# ---- the model -----------------------------------------------------------


def test_fixture_is_valid(tg):
    assert tg.validate() == []


def test_dependency_is_derived_from_edges(tg):
    assert tg.prerequisites("T02") == ["T01"]
    assert tg.prerequisites("T01") == []
    assert tg.unblocks("T01") == ["T02"]


def test_readiness_is_derived_not_stored(tg):
    """The task graph's one deliberate departure from the decision model. T02's
    prerequisite is DONE, so it is ready; nothing stores that, and nothing has
    to be updated when it changes."""
    assert tg.ready("T02")
    assert not tg.ready("T03")          # waits on T02, which is TODO
    assert tg.blocked("T03")
    assert tg.waiting_on("T03") == ["T02"]


def test_a_done_task_is_not_ready(tg):
    """Ready means startable, so work already finished is not in the list."""
    assert not tg.ready("T01")
    assert not tg.blocked("T01")


def test_dropping_a_prerequisite_releases_what_waited_on_it(tg):
    """Abandoning work *is* the decision that it was not needed, so it must not
    block forever. There is no status to update — the next query just sees it."""
    tg.tasks["T02"].status = "DROPPED"
    assert tg.waiting_on("T03") == []
    assert tg.ready("T03")


def test_an_unconnected_task_is_not_a_violation(tg):
    """The deliberate asymmetry with `no_orphans`: an isolated decision is a
    smell, an isolated chore is Tuesday."""
    assert tg.prerequisites("T04") == [] and tg.unblocks("T04") == []
    assert not [v for v in tg.validate() if "orphan" in v.check]


def test_frontier_is_unfinished_work(tg):
    assert tg.frontier() == ["T02", "T03", "T04"]     # T01 is DONE


def test_multiple_edges_from_one_task_union(tg):
    """A task edge carries no payload, so two edges out of one task mean their
    union — unlike a decision, where two active edges is a contradiction."""
    from dgraph.tasks import TaskEdge
    tg.edges.append(TaskEdge(src="T01", to=["T04"]))
    assert tg.unblocks("T01") == ["T02", "T04"]
    assert tg.validate() == []


def test_round_trip_is_stable(tg, tmp_path):
    p = tmp_path / "copy.json"
    tg.save(p)
    assert TaskGraph.load(p).to_dict() == tg.to_dict()


def test_duplicate_task_ids_are_refused(task_store):
    raw = json.loads((task_store / "tasks.json").read_text())
    raw["tasks"].append(dict(raw["tasks"][0]))
    (task_store / "tasks.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="T01"):
        TaskGraph.load(task_store / "tasks.json")


# ---- invariants ----------------------------------------------------------


def _check(tg, name):
    return [v for v in tg.validate() if v.check == name]


def test_detects_malformed_id_and_unknown_area(tg):
    from dgraph.tasks import Task
    tg.tasks["X9"] = Task(id="X9", title="bad", area="Nope")
    hits = _check(tg, "task_ids_wellformed")
    assert len(hits) == 2


def test_detects_illegal_status(tg):
    tg.tasks["T02"].status = "WOBBLY"
    assert _check(tg, "task_status_legal")


def test_detects_dangling_and_self_edges(tg):
    from dgraph.tasks import TaskEdge
    tg.edges.append(TaskEdge(src="T04", to=["T99", "T04"]))
    assert len(_check(tg, "task_no_dangling_refs")) == 2


def test_detects_a_cycle(tg):
    from dgraph.tasks import TaskEdge
    tg.edges.append(TaskEdge(src="T03", to=["T01"]))
    assert _check(tg, "task_acyclic")


def test_a_deep_chain_does_not_recurse(tmp_path):
    """Same reason the decision validator is iterative: a long but legal chain
    must not crash the validator, since a crash is a fail-open."""
    from dgraph.tasks import Task, TaskEdge
    tg = TaskGraph(areas=["A"])
    ids = [f"T{i:05d}" for i in range(1, 1101)]
    for tid in ids:
        tg.tasks[tid] = Task(id=tid, title="w", area="A")
    for a, b in zip(ids, ids[1:]):
        tg.edges.append(TaskEdge(src=a, to=[b]))
    assert not _check(tg, "task_acyclic")


def test_done_requires_a_date_and_an_outcome(tg):
    tg.tasks["T02"].status = "DONE"
    hits = _check(tg, "task_done_complete")
    assert len(hits) == 2
    assert any("outcome" in str(h) for h in hits)


def test_done_before_its_prerequisite_is_a_contradiction(tg):
    tg.tasks["T03"].status = "DONE"
    tg.tasks["T03"].done, tg.tasks["T03"].outcome = "2026-01-09", "PR #2"
    hits = _check(tg, "task_done_before_prerequisite")
    assert hits and "T02" in str(hits[0])


def test_a_dropped_prerequisite_does_not_make_done_a_contradiction(tg):
    """Dropping releases; it must not then accuse the work that proceeded."""
    tg.tasks["T02"].status = "DROPPED"
    tg.tasks["T03"].status = "DONE"
    tg.tasks["T03"].done, tg.tasks["T03"].outcome = "2026-01-09", "PR #2"
    assert not _check(tg, "task_done_before_prerequisite")


def test_every_task_check_is_declared(tg):
    from dgraph.tasks import Task, TaskEdge
    tg.tasks["X9"] = Task(id="X9", title="bad", area="Nope", status="WOBBLY")
    tg.edges.append(TaskEdge(src="T04", to=["T99"]))
    assert {v.check for v in tg.validate()} <= set(CHECKS)


# ---- the store on disk ---------------------------------------------------


def test_check_runs_on_a_tasks_only_project(task_store):
    """Either store makes a directory a project."""
    task_render.write(TaskGraph.load(task_store / "tasks.json"),
                      task_store / "tasks.md")
    assert run() == []


def test_a_stale_task_view_is_reported(task_store):
    (task_store / "tasks.md").write_text("hand-edited\n", encoding="utf-8")
    hits = [v for v in run() if v.check == "stale_task_view"]
    assert hits and "dg task render" in str(hits[0])


def test_a_missing_task_view_is_reported(task_store):
    assert [v.check for v in run()] == ["stale_task_view"]


def test_an_unreadable_task_store_is_a_blocking_violation(task_store):
    (task_store / "tasks.json").write_text("{not json", encoding="utf-8")
    hits = run()
    assert [v.check for v in hits] == ["store_loads"]
    assert hits[0].blocking


def test_an_absent_task_store_is_silence(store, g):
    """The overwhelming majority of projects will never have one; the decision
    graph must be completely unaffected by its absence."""
    from dgraph.render import write
    write(g)
    assert run() == []


# ---- the command surface -------------------------------------------------


def test_task_init_works_with_no_decision_store(tmp_path, monkeypatch):
    monkeypatch.setattr(project, "_override", tmp_path)
    res = runner.invoke(app, ["--project", str(tmp_path), "task", "init",
                              "--areas", "Infra"])
    assert res.exit_code == 0
    assert (tmp_path / "tasks.json").exists() and (tmp_path / "tasks.md").exists()


def test_a_chain_of_adds_stages_in_one_batch(run_cli, task_store):
    """The A3 lesson, applied to tasks: `--after` must see a task whose add is
    staged but not yet applied."""
    assert run_cli("task", "add", "--id", "T10", "-t", "First",
                   "--area", "Alpha").exit_code == 0
    assert run_cli("task", "add", "--id", "T11", "-t", "Second",
                   "--area", "Alpha", "--after", "T10").exit_code == 0
    assert run_cli("apply").exit_code == 0
    tg = TaskGraph.load(task_store / "tasks.json")
    assert tg.prerequisites("T11") == ["T10"]


def test_task_add_refuses_a_decision_id(run_cli):
    """The barrier, at stage time rather than at apply."""
    res = run_cli("task", "add", "--id", "D99", "-t", "x", "--area", "Alpha")
    assert res.exit_code == 1
    assert "T07" in res.output


def test_task_add_refuses_a_duplicate_and_an_unknown_area(run_cli):
    assert run_cli("task", "add", "--id", "T01", "-t", "x",
                   "--area", "Alpha").exit_code == 1
    assert run_cli("task", "add", "--id", "T20", "-t", "x",
                   "--area", "Nope").exit_code == 1


def test_done_records_an_outcome(run_cli, task_store):
    assert run_cli("task", "done", "T02", "-o", "PR #7").exit_code == 0
    assert run_cli("apply").exit_code == 0
    t = TaskGraph.load(task_store / "tasks.json").tasks["T02"]
    assert t.status == "DONE" and t.outcome == "PR #7" and t.done


def test_drop_reports_what_it_releases(run_cli):
    res = run_cli("task", "drop", "T02", "-w", "not needed")
    assert res.exit_code == 0
    assert "T03" in res.output and "released" in res.output


def test_task_list_names_only_ready_work(run_cli):
    out = run_cli("task").output
    assert "ready T02" in out


# ---- the barrier ---------------------------------------------------------


def test_task_ops_do_not_break_decision_commands(store, g, tmp_path, monkeypatch):
    """The specific regression separate pending files exist to prevent:
    `pending.preview` walks every op in a file and `_apply_one` raises on any it
    does not know, so a task op in the decision staging file would break every
    decision command."""
    from dgraph.render import write
    monkeypatch.setenv("COLUMNS", "200")
    write(g)
    (tmp_path / "tasks.json").write_text(json.dumps(TASK_FIXTURE_MIN),
                                         encoding="utf-8")
    r = runner.invoke(app, ["--project", str(tmp_path), "task", "add",
                            "--id", "T50", "-t", "work", "--area", "Alpha"])
    assert r.exit_code == 0
    for cmd in (["show"], ["pending"], ["check"],
                ["decide", "D05", "-a", "y", "-s", "s", "-f", "f"]):
        res = runner.invoke(app, ["--project", str(tmp_path), *cmd])
        assert res.exit_code in (0, 1), f"{cmd} crashed: {res.output}"
        assert "unknown op" not in res.output


TASK_FIXTURE_MIN = {"areas": ["Alpha"], "tasks": [], "edges": []}


def test_the_two_staging_files_are_distinct(run_cli, task_store):
    run_cli("task", "add", "--id", "T30", "-t", "x", "--area", "Alpha")
    assert pending.load(task_pending.path())
    assert pending.load(project.find().pending) == []
