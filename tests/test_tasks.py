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
from conftest import finished
from dgraph.tasks import (Reading, Stop, TaskEdge, TaskGraph,
                          starting_on_abandoned_work)

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
    tg.edges.append(TaskEdge(src="T01", to=["T04"], kind="precedes"))
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
    """Two rules, two names: the plugin gives a project one test per check, so
    filing an area problem under the id check loses the distinction there."""
    from dgraph.tasks import Task
    tg.tasks["X9"] = Task(id="X9", title="bad", area="Nope")
    assert len(_check(tg, "task_ids_wellformed")) == 1
    assert len(_check(tg, "task_area_known")) == 1


def test_detects_illegal_status(tg):
    tg.tasks["T02"].status = "WOBBLY"
    assert _check(tg, "task_status_legal")


def test_detects_dangling_and_self_edges(tg):
    from dgraph.tasks import TaskEdge
    tg.edges.append(TaskEdge(src="T04", to=["T99", "T04"], kind="precedes"))
    assert len(_check(tg, "task_no_dangling_refs")) == 2


def test_detects_a_cycle(tg):
    from dgraph.tasks import TaskEdge
    tg.edges.append(TaskEdge(src="T03", to=["T01"], kind="precedes"))
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
        tg.edges.append(TaskEdge(src=a, to=[b], kind="precedes"))
    assert not _check(tg, "task_acyclic")


def test_done_requires_a_date_and_an_outcome(tg):
    tg.tasks["T02"].status = "DONE"
    hits = _check(tg, "task_done_complete")
    assert len(hits) == 2
    assert any("outcome" in str(h) for h in hits)


def test_done_before_its_prerequisite_is_a_contradiction(tg):
    finished(tg.tasks["T03"], "2026-01-09", "PR #2")
    hits = _check(tg, "task_done_before_prerequisite")
    assert hits and "T02" in str(hits[0])


def test_a_dropped_prerequisite_does_not_make_done_a_contradiction(tg):
    """Dropping releases; it must not then accuse the work that proceeded."""
    tg.tasks["T02"].status = "DROPPED"
    finished(tg.tasks["T03"], "2026-01-09", "PR #2")
    assert not _check(tg, "task_done_before_prerequisite")


def test_every_task_check_is_declared(tg):
    from dgraph.tasks import Task, TaskEdge
    tg.tasks["X9"] = Task(id="X9", title="bad", area="Nope", status="WOBBLY")
    tg.edges.append(TaskEdge(src="T04", to=["T99"], kind="precedes"))
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


def test_drop_refuses_until_its_fallout_has_a_verdict(run_cli):
    """Dropping is the one status change that acts on other work. Both defaults
    are wrong somewhere — assuming "keep" leaves work whose whole purpose died
    with the drop sitting in the backlog, assuming "drop" throws away work that
    was only incidentally connected — so neither is taken."""
    res = run_cli("task", "drop", "T02", "-w", "not needed")
    assert res.exit_code == 2
    assert "T03" in res.output and "need a verdict" in res.output
    assert "--keep" in res.output and "--drop-too" in res.output


def test_drop_reports_what_it_releases(run_cli):
    res = run_cli("task", "drop", "T02", "-w", "not needed", "--keep", "T03")
    assert res.exit_code == 0
    assert "T03" in res.output and "kept" in res.output


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


# ---- editing the graph after the fact (audit D2b, E4) --------------------


def test_a_dependency_can_be_added_between_existing_tasks(run_cli, task_store):
    """`add --after` could only say this at creation, so a dependency
    discovered later had no way in at all and meant hand-editing the store."""
    assert run_cli("task", "dep", "T04", "--after", "T02").exit_code == 0
    assert run_cli("apply").exit_code == 0
    assert TaskGraph.load(task_store / "tasks.json").waiting_on("T04") == ["T02"]


def test_a_dependency_can_be_removed(run_cli, task_store):
    assert run_cli("task", "undep", "T03", "--after", "T02").exit_code == 0
    assert run_cli("apply").exit_code == 0
    tg = TaskGraph.load(task_store / "tasks.json")
    assert tg.waiting_on("T03") == [] and tg.ready("T03")


def test_removing_a_dependency_that_is_not_there_is_refused(run_cli):
    res = run_cli("task", "undep", "T03", "--after", "T04")
    assert res.exit_code == 1 and "not a prerequisite" in res.output


def test_a_task_cannot_be_made_to_wait_on_itself(run_cli):
    res = run_cli("task", "dep", "T02", "--after", "T02")
    assert res.exit_code == 1 and "cannot come after itself" in res.output


def test_a_link_can_be_removed(run_cli, task_store):
    """The undo `dg task link` never had. Without it a link recorded against
    the wrong decision could only be fixed by hand-editing tasks.json."""
    tg = TaskGraph.load(task_store / "tasks.json")
    tg.tasks["T02"].because = "D01"
    tg.save(task_store / "tasks.json")
    assert run_cli("task", "unlink", "T02", "--because").exit_code == 0
    assert run_cli("apply").exit_code == 0
    assert TaskGraph.load(task_store / "tasks.json").tasks["T02"].because is None


def test_unlinking_what_is_not_linked_is_refused(run_cli):
    res = run_cli("task", "unlink", "T02", "--because")
    assert res.exit_code == 1 and "no --because" in res.output


# ---- the record (audit E2, E3) -------------------------------------------


def test_dropping_a_task_keeps_the_note_that_described_it(run_cli, task_store):
    """`--why` used to overwrite the note — destroying the only description of
    what the abandoned work was, which is the opposite of keeping a record."""
    res = run_cli("task", "drop", "T04", "--why", "the vendor tool does it")
    assert res.exit_code == 0
    assert run_cli("apply").exit_code == 0
    t = TaskGraph.load(task_store / "tasks.json").tasks["T04"]
    assert t.note == "Nobody has finished this yet."
    assert t.stopped_because == "the vendor tool does it"
    assert "the vendor tool does it" in (task_store / "tasks.md").read_text()


def test_leaving_done_keeps_the_completion_and_stops_claiming_it(run_cli,
                                                                 task_store):
    """What used to be a clearing rule, and is now the same rule as `stops`.

    `tasks.md` printed an outcome under work in progress again, and nothing
    complained — so leaving DONE deleted the pair. Deleting was the wrong half
    to fix: what went stale was the *claim*, not the record. The record stays
    in the store and on the page, and the claim is gone because `done` and
    `outcome` are derived from a status that no longer makes it.
    """
    assert run_cli("task", "start", "T01").exit_code == 0
    assert run_cli("apply").exit_code == 0
    t = TaskGraph.load(task_store / "tasks.json").tasks["T01"]
    assert t.status == "DOING" and t.done is None and t.outcome is None
    assert [c.outcome for c in t.completions] == ["PR #1"]
    md = (task_store / "tasks.md").read_text()
    assert "PR #1" in md and "(the result)" not in md


def test_a_status_that_changes_nothing_is_refused(run_cli):
    res = run_cli("task", "start", "T04")          # already DOING
    assert res.exit_code == 1 and "already DOING" in res.output


def test_a_completion_under_restarted_work_is_kept_and_is_not_a_violation(tg):
    """The rule this replaces, and why it had to go.

    A completion under unfinished work used to be a violation, because `done`
    and `outcome` were live scalars that a status change had to clear or they
    would rot. They are derived now, so the same record means the ordinary
    thing instead: this work was finished once, and has been picked back up.
    Kept, unread, and reported by nothing.
    """
    finished(tg.tasks["T02"], "2026-01-09", "PR #2")
    tg.tasks["T02"].status = "DOING"
    assert not _check(tg, "task_done_complete")
    assert tg.tasks["T02"].done is None and tg.tasks["T02"].outcome is None
    assert tg.tasks["T02"].completions[-1].outcome == "PR #2"


def test_a_completion_missing_a_half_of_itself_is_a_violation(tg):
    """The rule that is left: an entry that cannot say what it records.

    `stops` is checked the same way and for the same reason — an archived
    record with a hole in it is worse than none, because it reads as an
    account of something and gives none."""
    finished(tg.tasks["T02"], "2026-01-09", "")
    hits = _check(tg, "task_done_complete")
    assert hits and "T02" in str(hits[0]) and "outcome" in str(hits[0])


# ---- audit F24: the other thing this store keeps -------------------------
#
# `why` and the completion pair had the same problem, and each had half the
# fix. Leaving DONE *cleared* the completion data, so a result could not
# outlive its status and could not survive a redo either; leaving DROPPED
# cleared nothing, so the reason work was abandoned outlived the abandonment
# and every view printed it anyway. `stops` fixed the second by deriving the
# claim from the status instead of clearing the record — and `F-F5` then
# applied the same fix to the first, which is the section further down.


@pytest.mark.parametrize("resume", [("start", "DOING"), ("done", "DONE")])
def test_leaving_dropped_keeps_the_reason_as_a_record(run_cli, task_store, resume):
    """There used to be a clearing rule here, mirroring the one for completion
    data. Folding the reason into `stops` retires it: the entry describes a
    stoppage that *happened*, so resuming cannot make it false — only the claim
    that it is the current reason, and that claim is derived from the status.

    So the live reading goes and the record stays, which is the property the
    old field could not have.
    """
    verb, want = resume
    # T04 on purpose: unconnected, so the drop leaves nothing standing and this
    # test is about the reason rather than about the fallout prompts.
    assert run_cli("task", "drop", "T04", "--why",
                   "superseded by the rewrite").exit_code == 0
    assert run_cli("apply").exit_code == 0
    assert TaskGraph.load(task_store / "tasks.json").tasks["T04"].stopped_because

    args = ["task", verb, "T04"] + (["-o", "PR #9"] if verb == "done" else [])
    assert run_cli(*args).exit_code == 0
    assert run_cli("apply").exit_code == 0

    t = TaskGraph.load(task_store / "tasks.json").tasks["T04"]
    assert t.status == want
    assert t.stopped_because is None                     # no longer the case
    assert [k.why for k in t.stops] == ["superseded by the rewrite"]   # still true
    view = (task_store / "tasks.md").read_text()
    assert "Not being done" not in view
    assert "superseded by the rewrite" in view           # under *Stopped:*


def test_a_reason_needs_a_status_that_claims_one(tg):
    """The rule that replaced the clearing rule, and it runs the other way. A
    reason left behind is no longer expressible; a status asserting a reason
    that is not there still is, and leaves the live one nowhere."""
    tg.tasks["T04"].status = "DROPPED"          # no stops
    hits = _check(tg, "task_drop_complete")
    assert hits and hits[0].blocking and "T04" in str(hits[0])
    assert "dg task drop T04" in str(hits[0])
    assert not _check(tg, "task_done_complete")     # not the same rule


def test_a_record_under_work_being_done_is_not_a_finding(tg):
    """The inverse of what used to be checked here, and the reason the change
    was worth making: work stopped once and restarted is the ordinary case, not
    drift, so nothing should complain about it."""
    tg.tasks["T04"].stops = [Stop(why="was stuck", date="2026-02-01")]  # DOING
    assert _check(tg, "task_drop_complete") == []
    assert _check(tg, "task_park_complete") == []


def test_no_view_calls_a_past_reason_the_current_one(tg):
    """`stopped_because` is derived from the status, so a view cannot read a
    live reason out of a task that has none — the belt the old field needed is
    now the shape of the data."""
    from dgraph import context, task_render
    tg.tasks["T04"].stops = [Stop(why="was stuck, once", date="2026-02-01")]
    assert "Not being done" not in task_render.render(tg)
    assert context.task(tg, "T04")["why"] is None


def test_the_stage_time_warning_names_the_stuck_batch(run_cli):
    """Audit D3: `_warn_stuck` for the task tray. Staging a finish ahead of its
    prerequisite was accepted with a note, and `apply` then refused the whole
    batch naming a remedy that was not the one that works."""
    res = run_cli("task", "done", "T03", "-o", "PR #12")
    assert res.exit_code == 0                      # a warning, never a refusal
    assert "would currently refuse this task batch" in res.output
    assert "dg task drop-op" in res.output


# ---- the generated view (audit E1) ---------------------------------------


def test_the_view_names_the_decisions_a_task_is_linked_to(task_store):
    """The link the whole cross-graph design exists for was invisible in the
    committed, human-readable view."""
    from dgraph import task_render
    tg = TaskGraph.load(task_store / "tasks.json")
    tg.tasks["T02"].because = "D01"
    tg.tasks["T02"].evidence_for = "D05"
    text = task_render.render(tg)
    assert "**Because:** D01" in text and "**Evidence for:** D05" in text
    assert "| D01 |" in text                       # and in the index


def test_the_view_does_not_claim_readiness_it_cannot_judge(task_store):
    """`tasks.md` is rendered from one store, so "ready" there can only mean
    "nothing outstanding in this graph" — it said so unqualified while `dg
    task`, which joins both, disagreed."""
    from dgraph import task_render
    tg = TaskGraph.load(task_store / "tasks.json")
    tg.tasks["T02"].because = "D01"
    text = task_render.render(tg)
    assert "nothing outstanding *in this graph* before them" in text
    assert "cannot see the decision store" in text


def test_a_task_cycle_finding_names_only_the_cycle(tg):
    """Audit F7, the task store's half. Three validators found cycles the same
    way and all three reported the route into the loop as part of it; the
    canonical form now lives once, in `violation.cycle_from`."""
    from dgraph.tasks import TaskEdge

    tg.tasks["T05"] = type(tg.tasks["T01"])(
        id="T05", title="fifth", area="Alpha")
    tg.tasks["T06"] = type(tg.tasks["T01"])(
        id="T06", title="sixth", area="Alpha")
    # T02 feeds the loop without being in it
    tg.edges = [TaskEdge("T02", ["T05"], "precedes"),
                TaskEdge("T05", ["T06"], "precedes"),
                TaskEdge("T06", ["T05"], "precedes")]

    # The kind is named because each is walked as its own subgraph, so a bare
    # "cycle:" would not say which relation is the cyclic one.
    found = [v.message for v in tg.validate() if v.check == "task_acyclic"]
    assert found == ["precedes cycle: T05 -> T06 -> T05"]


# ---- edge kinds: precedes, and prompted -----------------------------------
#
# The fixture's T01 -> T02 -> T03 chain is all `precedes`. What is pinned here
# is the second relation and, mostly, the ways it is *not* the first: it makes
# nothing wait, it is walked as its own subgraph, and it may run the opposite
# way between the same pair without that being a cycle.


def test_kind_is_required_and_never_defaulted(task_store):
    """A store written before kinds existed is refused, not silently read as
    prerequisites. The default would be right, and applying it quietly is still
    wrong: the file would then read one way and behave another."""
    raw = json.loads((task_store / "tasks.json").read_text())
    del raw["edges"][0]["kind"]
    (task_store / "tasks.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError) as exc:
        TaskGraph.load(task_store / "tasks.json")
    assert "kind" in str(exc.value) and '"precedes"' in str(exc.value)


def test_kind_survives_a_round_trip(tg, task_store):
    tg.edges.append(TaskEdge(src="T02", to=["T04"], kind="prompted"))
    tg.save(task_store / "tasks.json")
    written = json.loads((task_store / "tasks.json").read_text())
    assert all("kind" in e for e in written["edges"])
    assert TaskGraph.load(task_store / "tasks.json").prompted("T02") == ["T04"]


def test_an_unknown_kind_is_a_violation(tg):
    tg.edges.append(TaskEdge(src="T02", to=["T04"], kind="causes"))
    found = [v for v in tg.validate() if v.check == "task_edge_kind"]
    assert len(found) == 1 and "causes" in found[0].message


def test_an_unknown_kind_does_not_suppress_the_id_checks(tg):
    """One bad field should not hide every other finding about the same edge."""
    tg.edges.append(TaskEdge(src="T02", to=["T99"], kind="causes"))
    checks = {v.check for v in tg.validate()}
    assert {"task_edge_kind", "task_no_dangling_refs"} <= checks


def test_prompted_makes_nothing_wait(tg):
    """The property the whole kind exists for. A chore noticed while doing T01
    is startable at once; treating provenance as an ordering asserts otherwise."""
    tg.edges.append(TaskEdge(src="T01", to=["T04"], kind="prompted"))
    assert tg.prerequisites("T04") == [] and tg.waiting_on("T04") == []
    assert tg.unblocks("T01") == ["T02"]          # unchanged by the new edge
    assert tg.prompted("T01") == ["T04"]
    assert tg.discovered_during("T04") == ["T01"]


def test_the_two_kinds_may_run_opposite_ways_between_one_pair(tg):
    """The ordinary case, and the one an untyped edge list cannot hold: doing
    T02 turned up a cleanup that has to land before T02 can be finished. As one
    relation this is a cycle, and one of two true facts would have to go."""
    tg.edges.append(TaskEdge(src="T02", to=["T04"], kind="prompted"))
    tg.edges.append(TaskEdge(src="T04", to=["T02"], kind="precedes"))
    assert [v for v in tg.validate() if v.check == "task_acyclic"] == []
    assert tg.discovered_during("T04") == ["T02"]
    assert tg.waiting_on("T02") == ["T04"]        # T04 is DOING, so unresolved
    assert tg.ready("T04") is False               # T04 is DOING, not TODO
    assert tg.prerequisites("T04") == []          # ...but it waits on nothing


def test_a_prompted_cycle_is_caught_and_names_its_kind(tg):
    tg.edges.append(TaskEdge(src="T02", to=["T04"], kind="prompted"))
    tg.edges.append(TaskEdge(src="T04", to=["T02"], kind="prompted"))
    found = [v.message for v in tg.validate() if v.check == "task_acyclic"]
    assert found == ["prompted cycle: T02 -> T04 -> T02"]


def test_two_kinds_out_of_one_task_are_not_unioned(tg):
    """Grouping by `src` alone would fold the two relations together and make
    every provenance edge assert an ordering nobody claimed."""
    tg.edges.append(TaskEdge(src="T01", to=["T04"], kind="prompted"))
    assert tg.unblocks("T01") == ["T02"]
    assert tg.prompted("T01") == ["T04"]


# ---- staging and the CLI --------------------------------------------------


def test_add_dep_merges_within_a_kind_only(tg):
    """`add_dep` used to find an edge by `src`; with two kinds that would widen
    a precedes edge with a prompted target and silently order the work."""
    task_pending._apply_one(tg, {"op": "add_dep", "from": "T01",
                                 "to": ["T04"], "kind": "prompted"})
    assert tg.unblocks("T01") == ["T02"] and tg.prompted("T01") == ["T04"]
    assert len([e for e in tg.edges if e.src == "T01"]) == 2


def test_a_staged_edge_op_must_name_its_kind(tg):
    """A tray staged before kinds existed fails here rather than defaulting —
    `dg task pending` is read by a person, and an op that does not say which
    relation it edits cannot be reviewed."""
    with pytest.raises(KeyError):
        task_pending._apply_one(tg, {"op": "add_dep", "from": "T01",
                                     "to": ["T04"]})


def test_a_staged_edge_op_rejects_an_unknown_kind(tg):
    with pytest.raises(pending.ApplyError):
        task_pending._apply_one(tg, {"op": "add_dep", "from": "T01",
                                     "to": ["T04"], "kind": "causes"})


def test_a_new_task_can_record_what_turned_it_up(run_cli, task_store):
    res = run_cli("task", "add", "--id", "T07", "--title", "Fix the fixture",
                  "--area", "Alpha", "--discovered-during", "T04")
    assert res.exit_code == 0
    assert run_cli("apply").exit_code == 0
    tg = TaskGraph.load(task_store / "tasks.json")
    assert tg.discovered_during("T07") == ["T04"]
    # T04 is DOING and unresolved; provenance still leaves the work startable.
    assert tg.ready("T07") and tg.waiting_on("T07") == []


def test_provenance_can_be_recorded_after_the_fact(run_cli, task_store):
    assert run_cli("task", "dep", "T04", "--discovered-during",
                   "T01").exit_code == 0
    assert run_cli("apply").exit_code == 0
    tg = TaskGraph.load(task_store / "tasks.json")
    assert tg.discovered_during("T04") == ["T01"] and tg.prerequisites("T04") == []


def test_removing_provenance_names_the_kind(run_cli, task_store):
    """Both kinds can hold between one pair, so `undep` cannot infer which was
    meant: removing the ordering when the correction was to the provenance is
    exactly the silent wrong answer the kinds exist to prevent."""
    run_cli("task", "dep", "T04", "--discovered-during", "T01")
    run_cli("task", "dep", "T04", "--after", "T01")
    assert run_cli("apply").exit_code == 0
    assert run_cli("task", "undep", "T04", "--discovered-during",
                   "T01").exit_code == 0
    assert run_cli("apply").exit_code == 0
    tg = TaskGraph.load(task_store / "tasks.json")
    assert tg.discovered_during("T04") == [] and tg.prerequisites("T04") == ["T01"]


def test_undep_refuses_a_relation_that_is_not_there(run_cli):
    res = run_cli("task", "undep", "T03", "--discovered-during", "T01")
    assert res.exit_code == 1 and "was not discovered during" in res.output


def test_dep_with_no_relation_is_refused(run_cli):
    res = run_cli("task", "dep", "T04")
    assert res.exit_code == 2 and "--discovered-during" in res.output


def test_a_bad_second_spec_leaves_nothing_staged(run_cli):
    """Both specs are checked before either is written, so a typo in the second
    does not leave the new task sitting in the tray on its own."""
    res = run_cli("task", "add", "--id", "T07", "--title", "x", "--area",
                  "Alpha", "--after", "T01", "--discovered-during", "T99")
    assert res.exit_code == 1
    assert "T07" not in run_cli("task", "pending").output


def test_the_view_names_both_relations(tg):
    tg.edges.append(TaskEdge(src="T01", to=["T04"], kind="prompted"))
    out = task_render.render(tg)
    assert "**Discovered during:** T01" in out and "**Turned up:** T04" in out


def test_the_review_table_names_the_kind_of_each_staged_edge(run_cli):
    """A tray is read by a person before it is applied, so an op that does not
    say which relation it edits cannot be reviewed — which is half the reason
    the kind is stored at all."""
    run_cli("task", "dep", "T04", "--discovered-during", "T01")
    run_cli("task", "dep", "T04", "--after", "T02")
    out = run_cli("task", "pending").output
    assert "prompted" in out and "precedes" in out


# ---- what abandoning work leaves behind ----------------------------------
#
# `RESOLVED` covers DONE and DROPPED alike, so a release is indistinguishable
# from a completion in every derived value. That is the right default and it is
# also a silence; these pin the two places it is now broken.


def _drop(tg, tid):
    tg.tasks[tid].status = "DROPPED"
    tg.tasks[tid].why = "abandoned"


def test_work_released_by_a_drop_is_flagged(tg):
    _drop(tg, "T01")                              # T02 waited only on T01
    hits = [v for v in tg.validate() if v.check == "released_by_drop"]
    assert len(hits) == 1 and "T02" in hits[0].message
    assert not hits[0].blocking                   # never denies a commit


def test_work_released_by_a_completion_is_not_flagged(tg):
    """T01 is DONE in the fixture, so T02 is startable for the ordinary reason
    and nothing should say otherwise."""
    assert not [v for v in tg.validate() if v.check == "released_by_drop"]


def test_starting_the_work_clears_the_release_warning(tg):
    """The acknowledgement path, and the reason there is no `dg task confirm`:
    a task has no stored status to flip the way `dg confirm` flips a decision's,
    so the check asks only of work nobody has looked at yet."""
    _drop(tg, "T01")
    tg.tasks["T02"].status = "DOING"
    assert not [v for v in tg.validate() if v.check == "released_by_drop"]


def test_parked_work_released_by_a_drop_is_flagged(tg):
    """The hole SA03's filter left. `fallout`'s released branch skips a parked
    dependant on purpose — a park has no startability to release — so this
    check is the only thing that can say it afterwards, and while it gated on
    TODO alone nothing said it at all: nothing in the CLI returns a task to
    TODO, so T02 could be picked up months later with its only prerequisite
    abandoned and every surface silent about it."""
    _drop(tg, "T01")                              # T02 waited only on T01
    tg.tasks["T02"].status = "PARKED"
    tg.tasks["T02"].stops = [Stop(why="no design yet", date="2026-06-01")]
    hits = [v for v in tg.validate() if v.check == "released_by_drop"]
    assert len(hits) == 1 and "T02" in hits[0].message
    # Not the TODO wording: `ready` requires TODO, so a parked task was never
    # made startable by the drop — it was undermined by it.
    assert "became startable" not in hits[0].message
    assert not hits[0].blocking


@pytest.mark.parametrize("status", ["TODO", "PARKED", "DOING"])
def test_no_unfinished_task_takes_up_abandoned_work_in_silence(tg, status):
    """The whole claim, over every status work can be picked up from: either
    the store says it while the task sits there, or the door says it at the
    moment somebody starts it. DOING has only the second — starting is what
    *clears* the check, deliberately — which is why widening the check alone
    would not have closed this."""
    _drop(tg, "T01")
    tg.tasks["T02"].status = status
    said = [v.message for v in tg.validate()
            if v.check == "released_by_drop" and "T02" in v.message]
    said += [n for n in [starting_on_abandoned_work(tg, "T02")] if n]
    assert said and all("T01" in m for m in said)


def test_the_start_warning_is_silent_where_the_prerequisite_finished(tg):
    """T01 is DONE in the fixture, so T02 is startable for the ordinary reason
    and there is nothing to say about it."""
    assert starting_on_abandoned_work(tg, "T02") is None
    assert starting_on_abandoned_work(tg, "T04") is None    # no prerequisites


def test_starting_work_says_what_was_abandoned(run_cli):
    """The door. Starting is what clears `released_by_drop`, so a prerequisite
    that was given up on has to be named here or the person doing the work
    never hears it."""
    assert run_cli("task", "drop", "T02", "--why", "obsolete",
                   "--keep", "T03").exit_code == 0
    # Read through `_teff`, so the staged-but-unapplied drop counts — the note
    # and the tray are the same reading of the same store.
    res = run_cli("task", "start", "T03")
    assert res.exit_code == 0
    assert "T02" in res.output and "abandoned" in res.output


def test_starting_ordinary_work_says_nothing_about_abandonment(run_cli):
    res = run_cli("task", "start", "T02")         # T01 is DONE
    assert res.exit_code == 0 and "abandoned" not in res.output


def test_a_reading_survives_a_round_trip(tg, task_store):
    """Archived like a stop, and absent rather than empty where there is none —
    the store stays readable, and work nobody has read against an answer says
    so by silence."""
    tg.tasks["T02"].evidence_for = "D01"
    tg.tasks["T02"].readings = [Reading(date="2026-07-01", note="it holds",
                                        against="D01")]
    tg.save(task_store / "tasks.json")
    raw = json.loads((task_store / "tasks.json").read_text())
    rows = {t["id"]: t for t in raw["tasks"]}
    assert rows["T02"]["readings"] == [
        {"date": "2026-07-01", "note": "it holds", "against": "D01"}]
    assert "readings" not in rows["T01"]
    back = TaskGraph.load(task_store / "tasks.json")
    assert back.tasks["T02"].read_against("D01") == "2026-07-01"
    assert back.tasks["T02"].read_against("D09") is None


def test_a_reading_with_no_note_is_the_box_tick_the_record_refuses(tg):
    tg.tasks["T02"].evidence_for = "D01"
    tg.tasks["T02"].readings = [Reading(date="2026-07-01", note="",
                                        against="D01")]
    hits = [v for v in tg.validate() if v.check == "task_reading_complete"]
    assert len(hits) == 1 and "note" in hits[0].message


def test_a_reading_left_behind_by_a_moved_link_says_so(tg):
    """Kept rather than deleted — it is an archived record — but flagged,
    because a reading against a question this work no longer informs is inert
    and a reader could take it for cover it does not give."""
    tg.tasks["T02"].evidence_for = "D09"
    tg.tasks["T02"].readings = [Reading(date="2026-07-01", note="it holds",
                                        against="D01")]
    hits = [v for v in tg.validate() if v.check == "task_reading_stale"]
    assert len(hits) == 1 and "D01" in hits[0].message
    assert not hits[0].blocking


def test_reading_the_same_evidence_twice_in_a_day_is_refused(tg):
    """The refusal a second park gets, for the same reason: two entries would
    claim two readings where there was one, and this record is kept forever.
    A reading on a later date is a genuine second reading."""
    from dgraph.pending import ApplyError
    tg.tasks["T02"].evidence_for = "D01"
    finished(tg.tasks["T02"], "2026-06-01", "a number")
    op = {"op": "read_evidence", "task": "T02", "against": "D01",
          "note": "it holds", "date": "2026-07-01"}
    task_pending._apply_one(tg, op)
    with pytest.raises(ApplyError, match="already read"):
        task_pending._apply_one(tg, op)
    task_pending._apply_one(tg, {**op, "date": "2026-08-01"})
    assert [r.date for r in tg.tasks["T02"].readings] == ["2026-07-01",
                                                          "2026-08-01"]


def test_unfinished_work_has_no_result_to_read(tg):
    """The op's own floor: a reading is a reading *of a result*, and work that
    has not reported has not produced one."""
    from dgraph.pending import ApplyError
    tg.tasks["T02"].evidence_for = "D01"
    with pytest.raises(ApplyError, match="no result to read"):
        task_pending._apply_one(tg, {"op": "read_evidence", "task": "T02",
                                     "against": "D01", "note": "x",
                                     "date": "2026-07-01"})


def test_work_orphaned_by_a_drop_is_flagged(tg):
    tg.edges.append(TaskEdge(src="T04", to=["T02"], kind="prompted"))
    _drop(tg, "T04")
    hits = [v for v in tg.validate() if v.check == "orphaned_by_drop"]
    assert len(hits) == 1 and "T02" in hits[0].message
    assert not hits[0].blocking


def test_a_surviving_origin_is_not_a_silence(tg):
    """One origin left still says why the work exists, so there is nothing to
    break — the check fires only when *every* origin was abandoned."""
    tg.edges.append(TaskEdge(src="T04", to=["T02"], kind="prompted"))
    tg.edges.append(TaskEdge(src="T03", to=["T02"], kind="prompted"))
    _drop(tg, "T04")
    assert not [v for v in tg.validate() if v.check == "orphaned_by_drop"]


def test_drop_can_abandon_the_work_it_orphans(run_cli, task_store):
    run_cli("task", "dep", "T04", "--discovered-during", "T02")
    assert run_cli("apply").exit_code == 0
    res = run_cli("task", "drop", "T02", "-w", "not needed",
                  "--keep", "T03", "--drop-too", "T04")
    assert res.exit_code == 0
    assert run_cli("apply").exit_code == 0
    tg = TaskGraph.load(task_store / "tasks.json")
    assert tg.tasks["T04"].status == "DROPPED"
    assert "T02" in tg.tasks["T04"].stopped_because
    assert tg.tasks["T03"].status == "TODO"


def test_drop_refuses_a_verdict_on_unaffected_work(run_cli):
    res = run_cli("task", "drop", "T02", "--keep", "T01")
    assert res.exit_code == 1 and "not affected" in res.output


def test_drop_refuses_a_task_named_both_ways(run_cli):
    res = run_cli("task", "drop", "T02", "--keep", "T03", "--drop-too", "T03")
    assert res.exit_code == 1 and "both" in res.output


def test_drop_asks_when_there_is_someone_to_ask(run_cli, tty):
    res = run_cli("task", "drop", "T02", "-w", "no", input="y\n")
    assert res.exit_code == 0 and "still worth doing" in res.output
    assert "kept" in res.output


def test_a_drop_with_no_fallout_needs_no_verdict(run_cli):
    """T04 is unconnected, which is ordinary for tasks. Nothing to ask."""
    assert run_cli("task", "drop", "T04", "-w", "no").exit_code == 0


def test_an_independently_justified_orphan_is_quiet(tg):
    """`--keep` at drop time says the work is still wanted; this is where that
    verdict is recorded durably. A task carrying its own `because` has
    something other than its origin explaining why it exists, so its origin
    being abandoned is no more a silence than one surviving origin is."""
    tg.edges.append(TaskEdge(src="T04", to=["T02"], kind="prompted"))
    _drop(tg, "T04")
    assert [v for v in tg.validate() if v.check == "orphaned_by_drop"]
    tg.tasks["T02"].because = "D01"
    assert not [v for v in tg.validate() if v.check == "orphaned_by_drop"]


# ---- removing a task -----------------------------------------------------
#
# `dg task drop` keeps the task, the reason, and a verdict on its fallout.
# Removal keeps none of that, so it is for a record made in error — a task
# filed twice, or one that turned out to restate another.


@pytest.fixture
def archived(monkeypatch):
    from dgraph import cli
    monkeypatch.setattr(cli, "_archived", lambda store: None)


def test_task_removal_refuses_where_git_would_not_record_it(run_cli):
    res = run_cli("task", "rm", "T04", "--yes")
    assert res.exit_code == 1 and "not a git repository" in res.output


def test_severing_removes_the_task_and_its_edges(run_cli, task_store, archived):
    assert run_cli("task", "rm", "T02", "--yes").exit_code == 0
    assert run_cli("apply").exit_code == 0
    tg = TaskGraph.load(task_store / "tasks.json")
    assert "T02" not in tg.tasks
    assert not [e for e in tg.edges if "T02" in e.to or e.src == "T02"]
    assert tg.prerequisites("T03") == []          # severed, not spliced


def test_splicing_a_task_joins_what_it_sat_between(run_cli, task_store, archived):
    """T01 → T02 → T03. Fold T02 away and the ordering it carried survives."""
    assert run_cli("task", "rm", "T02", "--splice", "--yes").exit_code == 0
    assert run_cli("apply").exit_code == 0
    assert TaskGraph.load(task_store / "tasks.json").prerequisites("T03") == ["T01"]


def test_a_task_splice_never_crosses_the_kinds(run_cli, task_store, archived):
    """T01 *precedes* T02, and T02 *prompted* T04. Splicing T02 must join
    neither to the other: T01 did not turn T04 up, and it does not have to come
    before it. A chain exists only within a kind, so there is nothing to join."""
    run_cli("task", "dep", "T04", "--discovered-during", "T02")
    assert run_cli("apply").exit_code == 0
    assert run_cli("task", "rm", "T02", "--splice", "--yes").exit_code == 0
    assert run_cli("apply").exit_code == 0
    tg = TaskGraph.load(task_store / "tasks.json")
    assert tg.prerequisites("T03") == ["T01"]      # precedes joined to precedes
    assert tg.prerequisites("T04") == []           # not ordered behind T01
    assert tg.discovered_during("T04") == []       # and not attributed to it


def test_a_prompted_chain_joins_within_its_kind(run_cli, task_store, archived):
    """The mirror: T01 prompted T02 prompted T04, so folding T02 away leaves
    T01 as what turned T04 up. Provenance is spliced like any other relation —
    just never into a different one."""
    run_cli("task", "dep", "T02", "--discovered-during", "T01")
    run_cli("task", "dep", "T04", "--discovered-during", "T02")
    assert run_cli("apply").exit_code == 0
    assert run_cli("task", "rm", "T02", "--splice", "--yes").exit_code == 0
    assert run_cli("apply").exit_code == 0
    tg = TaskGraph.load(task_store / "tasks.json")
    assert tg.discovered_during("T04") == ["T01"]
    assert tg.prerequisites("T04") == []


def test_merging_a_task_moves_its_edges(run_cli, task_store, archived):
    assert run_cli("task", "rm", "T02", "--into", "T04", "--yes").exit_code == 0
    assert run_cli("apply").exit_code == 0
    tg = TaskGraph.load(task_store / "tasks.json")
    assert "T02" not in tg.tasks
    assert tg.prerequisites("T04") == ["T01"] and tg.unblocks("T04") == ["T03"]


def test_task_removal_says_what_it_would_lose(run_cli, task_store, archived):
    """T01 is DONE with an outcome. Nothing keeps that once it is gone."""
    res = run_cli("task", "rm", "T01", "--yes")
    assert "loses" in res.output and "PR #1" in res.output


def test_task_removal_refuses_without_a_human_or_a_flag(run_cli):
    from dgraph import pending, task_pending
    res = run_cli("task", "rm", "T04")
    assert res.exit_code in (1, 2)
    assert pending.load(task_pending.path()) == []


def test_a_task_collision_reads_as_another_writer(tg):
    """The same split as the decision store, through the same helper — a rule
    applied in one store and not its twin is the shape most of this tool's
    audit findings took."""
    op = {"op": "add_task", "id": "T09", "title": "New", "area": "Alpha"}
    landed = task_pending.apply_all(tg, [op])
    with pytest.raises(pending.Collision) as exc:
        task_pending.apply_all(landed, [op])
    assert "another writer applied it" in str(exc.value)


def test_a_task_that_differs_only_in_its_premise_is_a_clash(tg):
    """The direction that matters. Two writers creating the same title under
    different premises have *not* created the same task, so a comparison that
    skipped the link fields would tell the loser its work landed when something
    else did — and `tasks.matches` exists where it does so those fields can be
    read at all."""
    landed = task_pending.apply_all(tg, [{"op": "add_task", "id": "T09",
                                          "title": "New", "area": "Alpha",
                                          "because": "D01"}])
    with pytest.raises(pending.ApplyError) as exc:
        task_pending.apply_all(landed, [{"op": "add_task", "id": "T09",
                                         "title": "New", "area": "Alpha",
                                         "because": "D02"}])
    assert not isinstance(exc.value, pending.Collision)
    assert "pick another id" in str(exc.value)


def test_task_drop_op_names_the_op_it_removed(run_cli):
    """The task store's half of audit F29. Same hazard, same shared tray, and
    the kind is named because that is the whole reason an edge stores one."""
    run_cli("task", "add", "--id", "T09", "--title", "Ninth", "--area", "Alpha")
    run_cli("task", "dep", "T09", "--after", "T02")
    ops = pending.load(task_pending.path())
    assert [o["op"] for o in ops] == ["add_task", "add_dep"]

    res = run_cli("task", "drop-op", "1")
    assert res.exit_code == 0
    assert "add_dep" in res.output and "precedes" in res.output
    assert [o["op"] for o in pending.load(task_pending.path())] == ["add_task"]

    res = run_cli("task", "drop-op", "0")
    assert "add_task T09" in res.output and "Ninth" in res.output
    assert pending.load(task_pending.path()) == []


# ---- a group is one write (audit F28) ------------------------------------
#
# `tests/test_staging_atomicity.py` counts the writes; these say what the tray
# is allowed to *contain* while a group is being staged, which is the property
# the count is a proxy for. Both fail against the one-op-at-a-time code: the
# spy sees a tray holding half a group.


def _tray_states(monkeypatch):
    """Every list `pending.save` is asked to write, in order."""
    seen: list[list[dict]] = []
    real = pending.save

    def save(ops, path=None):
        seen.append(list(ops))
        return real(ops, path)

    monkeypatch.setattr(pending, "save", save)
    return seen


def test_the_drop_cascade_never_sits_in_the_tray_half_staged(run_cli,
                                                             monkeypatch):
    """The operator was asked about T03 and answered. A tray holding only the
    first half says the opposite of what they answered — and the task store has
    no invariant that would refuse an apply landing on it."""
    seen = _tray_states(monkeypatch)
    assert run_cli("task", "drop", "T02", "-w", "gone",
                   "--drop-too", "T03").exit_code == 0
    assert [len(s) for s in seen] == [2], seen
    assert [(o["task"], o["status"]) for o in seen[-1]] == [
        ("T02", "DROPPED"), ("T03", "DROPPED")]


def test_a_new_task_and_its_edges_never_sit_in_the_tray_half_staged(run_cli,
                                                                    monkeypatch):
    """A task landing without its prerequisites is not a partial batch that
    something refuses — it is a task that reads as startable."""
    seen = _tray_states(monkeypatch)
    assert run_cli("task", "add", "--id", "T09", "--title", "Ninth",
                   "--area", "Alpha", "--after", "T01,T02").exit_code == 0
    assert [len(s) for s in seen] == [3], seen


def test_both_relation_kinds_are_staged_in_one_step(run_cli, monkeypatch):
    """`--after X --discovered-during Y` is one statement about how this task
    relates to the others, and half of it is a different statement."""
    seen = _tray_states(monkeypatch)
    assert run_cli("task", "dep", "T04", "--after", "T01",
                   "--discovered-during", "T02").exit_code == 0
    assert [len(s) for s in seen] == [2], seen
    assert [(o["from"], o["kind"]) for o in seen[-1]] == [
        ("T01", "precedes"), ("T02", "prompted")]


def test_a_group_is_vetted_against_what_the_ops_before_it_produce(tg):
    """`add_task T09` then `add_dep … → T09` is legal only in that order.
    Vetting each op against the unchanged graph would refuse the second half of
    a group the first half makes legal."""
    ops = [{"op": "add_task", "id": "T09", "title": "Ninth", "area": "Alpha"},
           {"op": "add_dep", "from": "T01", "to": ["T09"], "kind": "precedes"}]
    task_pending.vet_all(tg, ops)                     # does not raise
    with pytest.raises(pending.ApplyError):
        task_pending.vet(tg, ops[1])                  # ...but alone it would


def test_vet_all_checks_without_staging_or_mutating(tg):
    task_pending.vet_all(tg, [{"op": "add_task", "id": "T09", "title": "x",
                               "area": "Alpha"}])
    assert "T09" not in tg.tasks
    assert pending.load(task_pending.path()) == []


def test_a_group_that_will_not_vet_stages_none_of_itself(task_store,
                                                         monkeypatch):
    """`_tstage_all` vets the whole group before writing any of it, so a
    refusal leaves the tray as it was — rather than holding the first half of
    something the command then gave up on."""
    import typer
    from dgraph import cli

    with pytest.raises(typer.Exit):
        cli._tstage_all([
            {"op": "add_task", "id": "T09", "title": "Ninth", "area": "Alpha"},
            {"op": "add_dep", "from": "T99", "to": ["T09"], "kind": "precedes"},
        ])
    assert pending.load(task_pending.path()) == []


def test_a_task_op_is_addressable_by_id(run_cli):
    """The task store's half of audit F29 route 1. Same tray, same hazard, same
    two vocabularies."""
    run_cli("task", "add", "--id", "T09", "--title", "Ninth", "--area", "Alpha")
    run_cli("task", "add", "--id", "T10", "--title", "Tenth", "--area", "Alpha")
    ops = pending.load(task_pending.path())
    res = run_cli("task", "drop-op", ops[1]["ref"])
    assert res.exit_code == 0 and "Tenth" in res.output
    assert [o["id"] for o in pending.load(task_pending.path())] == ["T09"]
    res = run_cli("task", "drop-op", "zzzz")
    assert res.exit_code == 1 and "zzzz" in res.output


# ---- the shape of the work ------------------------------------------------
#
# `dg task tree` and `dg areas`, the two readings the task store did not have.
# Both are the decision side's commands asked of the other store, so what is
# pinned here is mostly that they answer the *task* question rather than a
# translated decision one: the spine is `precedes`, and the two stores' status
# vocabularies never share a table.


def test_the_tree_is_the_ordering_from_what_can_start_first(run_cli):
    """T01 → T02 → T03 in the fixture, and T04 waits on nothing."""
    out = run_cli("task", "tree").output
    at = lambda t: out.index(t)
    assert at("T01") < at("T02") < at("T03")
    for tid in ("T01", "T02", "T03", "T04"):
        assert tid in out
    # Indented under its prerequisite, not beside it.
    t01 = next(ln for ln in out.splitlines() if "T01" in ln)
    t02 = next(ln for ln in out.splitlines() if "T02" in ln)
    assert t02.index("T02") > t01.index("T01")


def test_prompted_work_hangs_off_what_turned_it_up_and_says_so(run_cli):
    """The two edge kinds are different claims. `prompted` records where work
    came from and asserts no ordering, so it is drawn under its origin and
    marked rather than shown as a prerequisite chain."""
    run_cli("task", "dep", "T04", "--discovered-during", "T01")
    assert run_cli("apply").exit_code == 0
    out = run_cli("task", "tree").output
    lines = out.splitlines()
    t01 = next(ln for ln in lines if "T01" in ln)
    t04 = next(ln for ln in lines if "T04" in ln)
    assert "turned up doing it" in t04
    # And it is no longer a root: it is indented under T01, below it.
    assert lines.index(t04) > lines.index(t01)
    assert t04.index("T04") > t01.index("T01")


def test_a_task_reached_twice_is_drawn_once(run_cli):
    """`dg tree`'s rule, because a DAG is not a tree and the second drawing is
    the one that would be believed."""
    run_cli("task", "dep", "T03", "--after", "T04")
    assert run_cli("apply").exit_code == 0
    out = run_cli("task", "tree").output
    assert out.count("(above)") == 1


def test_the_tree_can_be_rooted_at_one_task(run_cli):
    out = run_cli("task", "tree", "T02").output
    assert "T02" in out and "T03" in out and "T01" not in out


def test_the_tree_names_a_task_it_cannot_find(run_cli):
    res = run_cli("task", "tree", "T99")
    assert res.exit_code == 1 and "T99" in res.output


def _cycle_beside_the_work(task_store):
    """Two tasks that precede each other, and nothing else touching them.

    Hand-written, because `task_acyclic` refuses to apply a cycle — which is
    the point: the store this draws is one that got here by being edited, and
    the tree is where somebody would go to find out what is wrong with it.
    """
    raw = json.loads((task_store / "tasks.json").read_text())
    raw["tasks"] += [{"id": "T08", "title": "Ring one", "area": "Alpha",
                      "status": "TODO"},
                     {"id": "T09", "title": "Ring two", "area": "Alpha",
                      "status": "TODO"}]
    raw["edges"] += [{"from": "T08", "to": ["T09"], "kind": "precedes"},
                     {"from": "T09", "to": ["T08"], "kind": "precedes"}]
    (task_store / "tasks.json").write_text(json.dumps(raw))


def test_a_cycle_beside_the_work_is_named_not_dropped(run_cli, task_store):
    """The roots still lead somewhere, so the tree draws — and everything
    inside the ring is exactly what no root reaches. Drawing the rest and
    saying nothing reports a whole region of the graph as absent."""
    _cycle_beside_the_work(task_store)
    out = run_cli("task", "tree").output
    assert "T08" in out and "T09" in out
    assert "no root reaches T08, T09" in out
    assert "T01" in out                      # the reachable work still drawn
    assert out.count("(above)") == 1         # the ring drawn once, not twice


def test_a_store_that_is_all_cycle_still_says_so(run_cli, task_store):
    """The case that already worked, kept: no roots at all is the other half
    of the same failure, and closing one must not open the other."""
    raw = {"areas": ["Alpha"],
           "tasks": [{"id": "T08", "title": "Ring one", "area": "Alpha",
                      "status": "TODO"},
                     {"id": "T09", "title": "Ring two", "area": "Alpha",
                      "status": "TODO"}],
           "edges": [{"from": "T08", "to": ["T09"], "kind": "precedes"},
                     {"from": "T09", "to": ["T08"], "kind": "precedes"}]}
    (task_store / "tasks.json").write_text(json.dumps(raw))
    out = run_cli("task", "tree").output
    assert "has a cycle" in out and "T08" in out and "T09" in out


def test_a_tree_rooted_by_hand_does_not_complain_about_the_rest(run_cli,
                                                                task_store):
    """`--root` is a request for one branch. Everything outside it is meant to
    be absent, so the reachability check belongs to the rootless walk only."""
    _cycle_beside_the_work(task_store)
    out = run_cli("task", "tree", "T02").output
    assert "no root reaches" not in out


def test_areas_counts_the_work_where_there_is_only_work(run_cli):
    """`dg areas` used to exit 2 in a project that tracks only work — a
    decision command failing rather than degrading, which is the thing every
    other cross-store reading avoids."""
    res = run_cli("areas")
    assert res.exit_code == 0
    assert "Tasks" in res.output and "Decisions" not in res.output
    assert "TODO" in res.output


def test_areas_says_a_table_is_missing_rather_than_stopping_at_zero(
        run_cli, task_store, store):
    """A store that exists and cannot be read is neither of the cases the two
    tables cover. The counts *are* the answer here, so half of them is not one
    — and an exit code of 0 is what a script would believe."""
    (store / "decisions.json").write_text("{ not json")
    res = run_cli("areas")
    assert res.exit_code == 1
    assert "Tasks" in res.output              # what could be counted, counted
    assert "no decision counts" in res.output
    assert "premise" not in res.output        # that is another command's word


def test_a_cross_graph_view_still_degrades_where_areas_exits(run_cli,
                                                             task_store, store):
    """The same unreadable store, read by something that only wants premises:
    it says so and carries on, because a backlog is still worth printing
    without one. Two answers to one condition, on purpose."""
    (store / "decisions.json").write_text("{ not json")
    res = run_cli("task")
    assert res.exit_code == 0
    assert "premise information is missing" in res.output


def test_areas_keeps_the_two_vocabularies_in_two_tables(run_cli, store):
    """`OPEN` and `TODO` are not columns of the same table: a row summing
    across them would be counting questions and work as one thing. The areas
    are what the two stores share."""
    out = run_cli("areas").output
    assert "Decisions" in out and "Tasks" in out
    assert out.index("Decisions") < out.index("Tasks")
    for column in ("OPEN", "DECIDED", "TODO", "DONE"):
        assert column in out


# ---- parking: the one archived record this store keeps -------------------


def test_parked_work_still_holds_up_what_waits_on_it(tg):
    """The whole difference from DROPPED. Abandoning work releases its
    dependants, because abandoning it *is* the judgement that it was not
    needed; parking asserts the opposite, so T03 goes on waiting."""
    tg.tasks["T02"].status = "PARKED"
    tg.tasks["T02"].stops = [Stop(why="stuck upstream", date="2026-02-01")]
    assert tg.waiting_on("T03") == ["T02"]
    assert not tg.ready("T03")
    assert "T02" in tg.frontier()          # outstanding, not resolved
    assert not tg.tasks["T02"].resolved


def test_parked_work_is_never_ready(tg):
    """`ready` is TODO-and-nothing-outstanding, so this comes free — but it is
    the property somebody would break by widening that test."""
    tg.tasks["T01"].status = "PARKED"
    tg.tasks["T01"].stops = [Stop(why="no hardware", date="2026-02-01")]
    assert not tg.ready("T01")


def test_the_park_record_survives_being_picked_up(run_cli, task_store):
    """The point of the field. Every other prose field here describes the
    current state and is cleared when that state stops holding; a park is a
    spell that ended, so it outlives the status."""
    assert run_cli("task", "park", "T02", "-w", "stuck on the upstream bug").exit_code == 0
    assert run_cli("apply").exit_code == 0
    assert run_cli("task", "start", "T02").exit_code == 0
    assert run_cli("apply").exit_code == 0
    t = TaskGraph.load(task_store / "tasks.json").tasks["T02"]
    assert t.status == "DOING"
    assert [k.why for k in t.stops] == ["stuck on the upstream bug"]


def test_a_second_park_is_a_second_record(run_cli, task_store):
    """Put down twice, and the list says so. What kept stopping this work is
    the reading the store did not have."""
    for why in ("stuck upstream", "still stuck, different reason"):
        assert run_cli("task", "park", "T02", "-w", why).exit_code == 0
        assert run_cli("apply").exit_code == 0
        assert run_cli("task", "start", "T02").exit_code == 0
        assert run_cli("apply").exit_code == 0
    t = TaskGraph.load(task_store / "tasks.json").tasks["T02"]
    assert [k.why for k in t.stops] == ["stuck upstream",
                                        "still stuck, different reason"]


def test_a_park_and_a_drop_write_the_same_record(run_cli, task_store):
    """One record, not two. Which of them it was is already in the status, and
    a second live field for the current reason was the same fact stored twice
    in the arrangement where the copies can disagree."""
    assert run_cli("task", "park", "T02", "-w", "stuck").exit_code == 0
    assert run_cli("apply").exit_code == 0
    assert run_cli("task", "start", "T02").exit_code == 0
    assert run_cli("apply").exit_code == 0
    assert run_cli("task", "drop", "T02", "-w", "not needed after all",
                   "--keep", "T03").exit_code == 0
    assert run_cli("apply").exit_code == 0
    t = TaskGraph.load(task_store / "tasks.json").tasks["T02"]
    assert [k.why for k in t.stops] == ["stuck", "not needed after all"]
    assert t.stopped_because == "not needed after all"
    # `released_by_drop` warns about T03, correctly — the drop released it.
    # Nothing *blocking*, which is the claim: one record, and it is well formed.
    assert not [v for v in TaskGraph.load(task_store / "tasks.json").validate()
                if v.blocking]


def test_parked_without_a_record_is_a_finding(tg):
    """Only reachable by hand-editing, like the completion rules. A status
    claiming a park is in progress with no reason stored leaves the live reason
    nowhere."""
    tg.tasks["T01"].status = "PARKED"
    assert [v for v in tg.validate() if v.check == "task_park_complete"]


def test_parks_under_another_status_are_not_a_finding(tg):
    """The mirror of `task_drop_complete`, deliberately absent. Work picked up
    again keeps its parks — that is the ordinary case, not drift."""
    tg.tasks["T01"].status = "DOING"
    tg.tasks["T01"].stops = [Stop(why="was stuck", date="2026-02-01")]
    assert not [v for v in tg.validate() if v.check == "task_park_complete"]


def test_a_park_round_trips_through_the_store(tg):
    tg.tasks["T01"].status = "PARKED"
    tg.tasks["T01"].stops = [Stop(why="stuck", date="2026-02-01")]
    parked = TaskGraph.from_dict(tg.to_dict())
    assert parked.tasks["T01"].stops == [Stop(why="stuck", date="2026-02-01")]
    # absent rather than [] where there is none, like every other field
    assert "stops" not in [t for t in tg.to_dict()["tasks"] if t["id"] == "T02"][0]


def test_a_malformed_park_is_refused_at_load(tg):
    """An archived record silently dropped is the one thing it must never be."""
    raw = tg.to_dict()
    raw["tasks"][0]["stops"] = [{"why": "stuck"}]      # no date
    with pytest.raises(ValueError, match="malformed stops entry"):
        TaskGraph.from_dict(raw)


def test_a_park_holding_work_up_is_chased(tg):
    """The counterweight to parking being the cheapest thing this store offers.
    A drop is interrogated about its dependants when it happens and chased by
    `released_by_drop` afterwards; without this, a park settled nothing and was
    never brought up again — so somebody stuck reaches for the cheaper one
    every time and the backlog fills with parked work holding its dependants."""
    tg.tasks["T02"].status = "PARKED"          # T03 waits on T02
    tg.tasks["T02"].stops = [Stop(why="stuck upstream", date="2026-02-01")]
    hits = _check(tg, "parked_holding_work")
    assert hits and not hits[0].blocking       # a warning, like the drop rules
    assert "T02" in str(hits[0]) and "T03" in str(hits[0])
    assert "2026-02-01" in str(hits[0])        # judge staleness yourself
    # all three exits named: pick it up, drop it (which releases), or undep
    assert "dg task drop T02" in str(hits[0])
    assert "undep" in str(hits[0])


def test_a_park_holding_nothing_up_is_not_chased(tg):
    """Silent where it costs nobody anything. A warning on every park would
    train the eye past the ones that matter."""
    tg.tasks["T03"].status = "PARKED"          # nothing waits on T03
    tg.tasks["T03"].stops = [Stop(why="no hardware", date="2026-02-01")]
    assert _check(tg, "parked_holding_work") == []


def test_a_park_behind_finished_work_is_not_chased(tg):
    """Held work means *unfinished* work. A dependant that is already done is
    not being held up by anything."""
    tg.tasks["T01"].status = "PARKED"          # T02 waits on T01, and is TODO
    tg.tasks["T01"].stops = [Stop(why="stuck", date="2026-02-01")]
    assert _check(tg, "parked_holding_work")
    tg.tasks["T02"].status = "DROPPED"
    tg.tasks["T02"].stops = [Stop(why="not needed", date="2026-02-02")]
    assert _check(tg, "parked_holding_work") == []


@pytest.mark.parametrize("verb, status", [("park", "PARKED"),
                                         ("drop", "DROPPED")])
def test_stopping_already_stopped_work_is_refused(run_cli, verb, status):
    """Appending would claim two stoppages where there was one; merging would
    edit a record that is kept forever. The caller wanted to amend a reason,
    and the op cannot tell that from a genuine second stoppage.

    Both stoppages, because the rule is one rule: `task_pending` refuses on
    `was == t.status` and neither verb is special. T04 for the drop -- it is
    unconnected, so nothing stands to be released and the refusal is the only
    thing under test."""
    tid = "T02" if verb == "park" else "T04"
    assert run_cli("task", verb, tid, "-w", "stuck").exit_code == 0
    assert run_cli("apply").exit_code == 0
    res = run_cli("task", verb, tid, "-w", "still stuck")
    assert res.exit_code == 1 and f"already {status}" in res.output


def test_a_store_written_before_stops_is_refused_with_the_repair(tg):
    """`why` was a field and is not one now. Folded in silently it would need a
    date this function does not have, and inventing one puts a fabricated fact
    into the one record here that is kept forever."""
    raw = tg.to_dict()
    raw["tasks"][0]["why"] = "abandoned, once"
    with pytest.raises(ValueError, match="no longer a field"):
        TaskGraph.from_dict(raw)


def test_dropping_needs_a_reason_at_both_doors(run_cli):
    """The web form always required one and the CLI did not. A drop with no
    reason now writes an empty entry into the archived record, so the two doors
    onto the same act agree about what it takes to walk through them."""
    res = run_cli("task", "drop", "T04", input="\n")
    assert res.exit_code != 0 or "Why is this not being done" in res.output


# ---- a drop does not interrogate work that is already stopped ------------


def _stopped_dependants(run_cli):
    """T02 with two dependants that have already stopped: T03 parked, T05
    dropped. Both waited only on T02, so both were fallout before the filter."""
    assert run_cli("task", "add", "--id", "T05", "-t", "Also after T02",
                   "--area", "Beta", "--after", "T02").exit_code == 0
    assert run_cli("apply").exit_code == 0
    assert run_cli("task", "park", "T03", "-w", "waiting on a person"
                   ).exit_code == 0
    assert run_cli("task", "drop", "T05", "-w", "overtaken").exit_code == 0
    assert run_cli("apply").exit_code == 0


def test_a_drop_asks_no_verdict_about_work_that_already_stopped(run_cli):
    """The F3 shape. The released branch had no status filter, so a DROPPED or
    PARKED dependant was demanded a verdict -- and for the dropped one neither
    answer worked: `--drop-too` is refused by `task_pending` as a second drop,
    and `--keep` prints "still worth doing" about abandoned work."""
    _stopped_dependants(run_cli)
    res = run_cli("task", "drop", "T02", "-w", "not needed after all")
    assert res.exit_code == 0
    assert "need a verdict" not in res.output
    assert "T03" not in res.output and "T05" not in res.output


def test_every_task_a_drop_asks_about_can_take_either_verdict(run_cli,
                                                              task_store):
    """The wider claim: the command must not offer a choice one branch of which
    the store refuses. Every task `_fallout` reports is staged with
    `--drop-too` here, and each must apply cleanly."""
    from dgraph.cli import _fallout

    _stopped_dependants(run_cli)
    assert run_cli("task", "dep", "T04", "--discovered-during", "T02"
                   ).exit_code == 0
    assert run_cli("apply").exit_code == 0
    store = task_store / "tasks.json"
    snapshot = store.read_text(encoding="utf-8")
    tg = TaskGraph.load(store)
    affected = sorted(_fallout(tg, "T02"))
    assert affected, "the fixture must actually produce fallout"

    for t in affected:
        others = [o for o in affected if o != t]
        res = run_cli("task", "drop", "T02", "-w", "no", "--drop-too", t,
                      *(["--keep", ",".join(others)] if others else []))
        assert res.exit_code == 0, f"--drop-too {t}: {res.output}"
        applied = run_cli("apply")
        assert applied.exit_code == 0, f"--drop-too {t}: {applied.output}"
        assert TaskGraph.load(store).tasks[t].status == "DROPPED"
        store.write_text(snapshot, encoding="utf-8")   # back, for the next one


# ---- audit F-F5: every completion, and no silent second one --------------
#
# `outcome` and `done` were scalars, and `_apply_one` assigned them. A second
# `dg task done` overwrote both with `dg check` reporting clean — the one
# archival record in either store kept somewhere a later write could erase it,
# on the commonest task command, with no route to recover it. The fix is the
# shape `stops` already had: append to a list, derive the live reading from the
# status, and refuse the repeat that cannot be told from an amendment.


def test_a_second_completion_is_refused_and_names_the_way_forward(run_cli,
                                                                  task_store):
    """The finding. Two `dg task done` used to leave one outcome and no report.

    Refused rather than appended, for the reason a second park is: this op
    cannot tell a genuine second completion from a caller amending the outcome
    of the one there was. Unlike a park, the way forward is a real one, so the
    refusal names it instead of only saying no."""
    assert run_cli("task", "done", "T04", "-o", "A: shipped as PR #7").exit_code == 0
    assert run_cli("apply").exit_code == 0
    res = run_cli("task", "done", "T04", "-o", "B: shipped as PR #9")
    assert res.exit_code == 1
    assert "already DONE" in res.output and "dg task start T04" in res.output
    t = TaskGraph.load(task_store / "tasks.json").tasks["T04"]
    assert [c.outcome for c in t.completions] == ["A: shipped as PR #7"]


def test_the_second_completion_is_refused_inside_one_batch_too(run_cli):
    """Staged twice without applying in between. `vet_all` walks the batch on a
    probe, so the second op meets the first rather than the store — which is
    the only reason a tray cannot hold the loss the store now refuses."""
    assert run_cli("task", "done", "T04", "-o", "first").exit_code == 0
    res = run_cli("task", "done", "T04", "-o", "second")
    assert res.exit_code == 1 and "already DONE" in res.output


def test_restarting_and_finishing_again_keeps_both_results(run_cli, task_store):
    """The fork the refusal offers, taken. Both results are in the record and
    the status says which is live — which is what the old shape could not do
    at all, and why the refusal alone would have been a dead end."""
    assert run_cli("task", "done", "T04", "-o", "HNSW 12ms p50").exit_code == 0
    assert run_cli("apply").exit_code == 0
    assert run_cli("task", "start", "T04").exit_code == 0
    assert run_cli("apply").exit_code == 0
    assert run_cli("task", "done", "T04", "-o", "IVF-PQ 40ms").exit_code == 0
    assert run_cli("apply").exit_code == 0

    t = TaskGraph.load(task_store / "tasks.json").tasks["T04"]
    assert [c.outcome for c in t.completions] == ["HNSW 12ms p50", "IVF-PQ 40ms"]
    assert t.outcome == "IVF-PQ 40ms"           # the live one, and only it
    section = task_render._section(TaskGraph.load(task_store / "tasks.json"),
                                   "T04")
    assert section.index("HNSW 12ms p50") < section.index("IVF-PQ 40ms")
    assert section.count("(the result)") == 1        # on the last, and only it
    assert "IVF-PQ 40ms **(the result)**" in section


def test_a_result_is_kept_through_a_park_and_a_drop(tg):
    """No status clears a completion, because none of them can make it untrue.

    The mirror of `test_a_restarted_task_keeps_the_stop_and_loses_the_marker`:
    the claim is derived and goes, the record is stored and stays."""
    finished(tg.tasks["T04"], "2026-01-09", "PR #2")
    for status in ("PARKED", "DROPPED", "TODO", "DOING"):
        tg.tasks["T04"].status = status
        assert tg.tasks["T04"].done is None and tg.tasks["T04"].outcome is None
        assert tg.tasks["T04"].completions[-1].outcome == "PR #2"


def test_a_second_completion_survives_the_store(task_store, tg):
    """Written and read back. The list is the stored shape now, so a round trip
    is what says the record outlives the process that made it."""
    finished(tg.tasks["T04"], "2026-01-09", "first")
    finished(tg.tasks["T04"], "2026-03-01", "second")
    tg.save(task_store / "tasks.json")
    back = TaskGraph.load(task_store / "tasks.json").tasks["T04"]
    assert [(c.date, c.outcome) for c in back.completions] == [
        ("2026-01-09", "first"), ("2026-03-01", "second")]
    assert back.done == "2026-03-01" and back.outcome == "second"


def test_a_store_written_before_the_list_loads_as_one_completion(task_store):
    """The migration, and it is the whole of it: no store needs converting.

    Folded rather than refused — unlike the pre-`stops` `why`, which needed a
    date nothing could supply. Both halves of a completion are already in the
    old record, and one of them *is* the date."""
    path = task_store / "tasks.json"
    raw = json.loads(path.read_text())
    for t in raw["tasks"]:
        if t["id"] == "T04":
            t.update(status="DONE", done="2026-01-09", outcome="PR #2")
    path.write_text(json.dumps(raw))

    t = TaskGraph.load(path).tasks["T04"]
    assert [(c.date, c.outcome) for c in t.completions] == [("2026-01-09", "PR #2")]
    assert t.done == "2026-01-09" and t.outcome == "PR #2"
    # And the old keys do not come back on the way out.
    written = t and TaskGraph.load(path).to_dict()["tasks"]
    row = next(r for r in written if r["id"] == "T04")
    assert "done" not in row and "outcome" not in row


def test_finishing_without_an_outcome_is_refused_at_the_op(tg):
    """An op-shape rule, beside `--why` on a stop, not a completeness one.

    It has to be: a second `set_status DONE` is refused, so no later op in the
    batch can supply the outcome a first one left out. The transitional state
    `vet` used to allow is not reachable any more."""
    from dgraph.pending import ApplyError
    with pytest.raises(ApplyError, match="needs what it produced"):
        task_pending._apply_one(tg, {"op": "set_status", "task": "T04",
                                     "status": "DONE", "done": "2026-01-09"})


# ---- one stop list, one live marker --------------------------------------


def _with_stops(tg, status, stops):
    tg.tasks["T04"].status = status
    tg.tasks["T04"].stops = [Stop(why=w, date=d) for w, d in stops]
    return tg


def test_a_reason_stopped_once_is_rendered_once(tg):
    """F6. `_section` printed the live reason and then the whole `stops` list
    containing it, so a task stopped a single time had its reason in `tasks.md`
    twice -- and nothing in the markdown said which entry was current."""
    _with_stops(tg, "DROPPED", [("superseded by the rewrite", "2026-02-01")])
    section = task_render._section(tg, "T04")
    assert section.count("superseded by the rewrite") == 1
    assert "not being done" in section
    assert "\n\n\n" not in section          # the stray double blank line


def test_three_stops_are_all_rendered_in_order_with_only_the_last_live(tg):
    """The record is what kept stopping this work; the live marker is a claim
    about now, and only the last entry can carry it."""
    _with_stops(tg, "PARKED", [("first", "2026-01-01"),
                               ("second", "2026-02-01"),
                               ("third", "2026-03-01")])
    section = task_render._section(tg, "T04")
    assert section.index("first") < section.index("second") < section.index("third")
    assert section.count("(put down)") == 1
    assert "2026-03-01 — third **(put down)**" in section
    assert "2026-01-01 — first ·" in section     # unmarked, and still first


def test_a_restarted_task_keeps_the_stop_and_loses_the_marker(tg):
    """The record survives, the claim does not. `stopped_because` is derived
    from the status, so nothing has to be cleared for this to hold."""
    _with_stops(tg, "DOING", [("was stuck", "2026-01-01")])
    section = task_render._section(tg, "T04")
    assert "was stuck" in section
    assert "put down" not in section and "not being done" not in section


def test_the_fallout_reading_lives_beside_the_other_two(run_cli):
    """SA02's first move. `_fallout` lived in `cli.py`, so the web `Drop it`
    button could not ask the question the CLI refuses on -- a door can only
    refuse what it can see. It now sits in `tasks.py` beside
    `dropped_prerequisites` and `abandoned_origins`, which are the same reading
    asked after the fact, and both doors call it."""
    from dgraph import cli, server, tasks

    assert tasks.fallout.__module__ == "dgraph.tasks"
    assert cli._fallout is tasks.fallout
    tg = TaskGraph.load(project.find().tasks)
    assert set(tasks.fallout(tg, "T02")) == {"T03"}
    assert [r["id"] for r in server.fallout_payload("T02")["fallout"]] == ["T03"]


# ---- audit F-F6: the same correction, in the other store ------------------
#
# `dg amend`'s twin, and the point of the finding is that neither store had it:
# `Task.title` and `Task.area` were mutable fields with no mutator, so an agent
# correcting a typo had to edit `tasks.json` by hand. The rules live in
# `pending.vet_fields` and are shared, because a rule applied in one store and
# not its twin is the shape most of this tool's audit findings took.


def test_a_task_title_can_be_corrected_through_the_tool(run_cli, task_store):
    assert run_cli("task", "amend", "T02", "--title", "Reworded").exit_code == 0
    assert run_cli("apply").exit_code == 0
    assert TaskGraph.load(task_store / "tasks.json").tasks["T02"].title \
        == "Reworded"


@pytest.mark.parametrize("args,says", [
    (("task", "amend", "T02"), "nothing to change"),
    (("task", "amend", "T02", "--title", " "), "needs a title"),
    (("task", "amend", "T02", "--area", "Nope"), "unknown area"),
    (("task", "amend", "T99", "--title", "x"), "unknown task"),
])
def test_the_task_correction_is_refused_where_it_would_not_hold(run_cli, args,
                                                                says):
    res = run_cli(*args)
    assert res.exit_code == 1 and says in res.output


def test_an_outcome_is_not_amendable_and_the_refusal_says_what_to_do(tg):
    """The line the op is drawn along, asserted rather than described.

    A title is how the work is referred to; an outcome is a dated record of
    what it produced. Naming one here used to be impossible only because the op
    did not exist — now that it does, it has to refuse, or an op reporting
    success while silently writing nothing of the sort is worse than the
    hand-edit it replaces."""
    from dgraph.pending import ApplyError
    with pytest.raises(ApplyError, match="not amended in outcome"):
        task_pending.vet(tg, {"op": "set_fields", "task": "T02",
                              "title": "fine", "outcome": "sneaked in"})


def test_a_task_note_may_be_emptied_and_the_dialect_is_left_alone(tg):
    """The one place the two stores differ, and it is the records differing
    rather than the rule: a vertex's `format` describes its note and goes with
    it, while a task's covers the note *and* every outcome, which `task_render`
    converts through the one field."""
    tg.tasks["T02"].note, tg.tasks["T02"].format = "*org*", "org"
    task_pending._apply_one(tg, {"op": "set_fields", "task": "T02",
                                 "note": None})
    assert tg.tasks["T02"].note is None and tg.tasks["T02"].format == "org"
