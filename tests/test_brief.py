"""The session brief: what a host injects, and what an adapter parses.

Every rule here exists because the reader is a program on a pipe rather than a
person at a terminal — no colour, no reflow, no boxes, and never an exception
that would take a session down with it.
"""

import json
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from dgraph import brief, pending, project
from dgraph.cli import app
from dgraph.model import Graph
from dgraph.render import write

runner = CliRunner()


@pytest.fixture
def run(store, monkeypatch):
    def go(*args, cols="200"):
        monkeypatch.setenv("COLUMNS", cols)
        return runner.invoke(app, ["--project", str(store), *args])
    return go


def _provisional(store, vid, premise=None):
    g = Graph.load(store / "decisions.json")
    g.vertices[vid] = replace(g.vertices[vid], status="PROVISIONAL")
    if premise:
        g.vertices[premise] = replace(g.vertices[premise], status="REOPENED")
    g.save(store / "decisions.json")
    write(g, store / "decision-graph.md")
    return g


# ---- the text form -------------------------------------------------------


def test_brief_is_plain_text_at_any_width(run, store, g):
    """The `dg export` regression: rich soft-wraps at $COLUMNS, which would move
    an id onto a line of its own and quietly break every line format below."""
    write(g)
    narrow = run("brief", cols="40").output
    wide = run("brief", cols="200").output
    assert narrow == wide
    assert "─" not in narrow and "│" not in narrow


def test_brief_lists_provisional_which_the_frontier_omits(run, store, g):
    """PROVISIONAL counts as settled, so `frontier()` does not return it — and it
    is the one status an agent must not build on unexamined."""
    write(g)
    _provisional(store, "D02", premise="D01")
    out = run("brief").output
    assert "D02" not in out.split("FRONTIER")[1].split("RESTING")[0]
    assert "D02" in out.split("RESTING")[1]


def test_brief_names_the_reopened_premise_it_rests_on(run, store, g):
    write(g)
    _provisional(store, "D02", premise="D01")
    assert "rests on D01" in run("brief").output


def test_brief_flags_a_provisional_vertex_whose_premises_are_settled_again(
        run, store, g):
    """The state with no exit until `dg confirm` — so the brief names the verb."""
    write(g)
    _provisional(store, "D02")
    assert "`dg confirm D02`" in run("brief").output


def test_brief_prints_an_empty_section_with_a_zero(run, store, g):
    """Silence would read identically to "this brief does not cover that"."""
    write(g)
    assert "RESTING ON A PREMISE UNDER REVIEW (0)" in run("brief").output


def test_brief_marks_a_decidable_frontier_vertex(run, store, g):
    """`dg show` prints "—" in Waiting on for exactly the item that can be
    picked up now, which says nothing about whether to pick it up."""
    write(g)
    out = run("brief").output
    assert "decidable now" in out
    assert "unblocks D06" in out          # deciding D05 releases D06
    assert "waiting on D05" in out        # D06 is BLOCKED:D05


def test_brief_survives_a_note_containing_newlines(run, store, g):
    write(g)
    gg = Graph.load(store / "decisions.json")
    gg.vertices["D05"] = replace(
        gg.vertices["D05"], note="First line.\nSecond line.\n\nThird.")
    gg.save(store / "decisions.json")
    out = run("brief").output
    assert "note: First line." in out
    assert "Second line." not in out


def test_brief_clips_a_long_note(run, store, g):
    write(g)
    gg = Graph.load(store / "decisions.json")
    gg.vertices["D05"] = replace(gg.vertices["D05"], note="x" * 500)
    gg.save(store / "decisions.json")
    line = [ln for ln in run("brief").output.splitlines() if "note:" in ln][0]
    assert len(line) < 100


def test_brief_counts_staged_ops(run, store, g):
    """Staged work is invisible in every read command, and `.dgraph-pending.json`
    is gitignored, so it is invisible in a diff too."""
    write(g)
    assert "STAGED BUT NOT APPLIED: 0" in run("brief").output
    pending.stage({"op": "set_status", "vertex": "D05", "status": "OPEN"},
                  store / ".dgraph-pending.json")
    out = run("brief").output
    assert "STAGED BUT NOT APPLIED: 1" in out
    assert "dg apply" in out


def test_brief_truncates_a_long_frontier_and_says_how_many_more(run, store, g):
    write(g)
    gg = Graph.load(store / "decisions.json")
    for i in range(10, 20):
        vid = f"D{i}"
        gg.vertices[vid] = replace(gg.vertices["D05"], id=vid, title=f"Open {i}")
    gg.save(store / "decisions.json")
    out = run("brief", "--limit", "3").output
    assert "+9 more" in out and "`dg show`" in out


def test_brief_points_at_the_skill(run, store, g):
    """Injected text is guaranteed context; the skill loads only if the model
    chooses to. This line is the only thing that makes it discoverable."""
    write(g)
    assert "dear-guide skill" in run("brief").output


def test_brief_reports_a_broken_graph(run, store, g):
    """A blocking finding, counted as an error. The view being unrendered is no
    longer one of those — it is a warning now — so this breaks the store."""
    from dataclasses import replace as _replace
    write(g)
    g.vertices["D02"] = _replace(g.vertices["D02"], status="WOBBLY")
    g.save(store / "decisions.json")
    assert "error(s)" in run("brief").output
    assert "status_legal" in run("brief").output


def test_brief_counts_an_unrendered_view_without_demanding_a_fix(run, store, g):
    """A lagging view is a warning now, so the brief counts it with the other
    advisories rather than printing "fix before committing".

    Deliberately not named here. Naming one warning and not the others would be
    special-casing a check inside a payload that is paid for in tokens on every
    session start; `dg check` names it, and the commit gate says so at the
    moment it would go into history.
    """
    out = run("brief").output
    assert "warning(s)" in out
    assert "error(s)" not in out and "fix before committing" not in out


def test_brief_survives_a_corrupt_store(run, store, g):
    """Audit A2b. A store that no longer parses is the moment the graph most
    needs attention, and it used to be the moment the brief crashed — which
    the session hook reads as "nothing to say"."""
    write(g)
    (store / "decisions.json").write_text("{not json", encoding="utf-8")
    res = run("brief")
    assert res.exit_code == 0
    assert "store_loads" in res.output
    assert "could not be read" in res.output


def test_brief_without_a_store_tells_a_human(run, tmp_path):
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    res = runner.invoke(app, ["--project", str(empty), "brief"])
    assert res.exit_code == 2


# ---- the JSON form -------------------------------------------------------


def test_brief_json_reports_no_project_instead_of_failing(tmp_path):
    """An adapter runs this in every directory. A session must never break
    because a project has never heard of this tool."""
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    res = runner.invoke(app, ["--project", str(empty), "brief", "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output)["project"] is None


def test_brief_json_survives_a_corrupt_store(run, store):
    """The JSON twin of the crash above: an adapter must always get a payload,
    and the payload must carry the store_loads violation it can quote."""
    (store / "decisions.json").write_text("{not json", encoding="utf-8")
    res = run("brief", "--json")
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["project"] == str(store)
    assert payload["frontier"] == []
    assert any(v["check"] == "store_loads" and v["blocking"]
               for v in payload["violations"])


def test_brief_json_reports_an_unreadable_pending_file(run, store, g):
    """Unreadable is not "none staged" — that reading loses work. The count
    stays honest at 0 and a blocking violation names the file, matching the
    gate's refusal."""
    write(g)
    (store / ".dgraph-pending.json").write_text("{not json", encoding="utf-8")
    res = run("brief", "--json")
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["staged"] == 0
    assert any(".dgraph-pending.json" in v["text"] and v["blocking"]
               for v in payload["violations"])


def test_brief_json_carries_the_violation_text_verbatim(run, store, g):
    """So an adapter quoting a refusal quotes the words `dg check` would have."""
    from dgraph.check import run as check_run
    payload = json.loads(run("brief", "--json").output)
    assert [v["text"] for v in payload["violations"]] == \
        [str(v) for v in check_run(project.Project(store))]


def test_brief_json_and_text_agree_on_the_frontier(run, store, g):
    write(g)
    payload = json.loads(run("brief", "--json").output)
    text = run("brief").output
    for entry in payload["frontier"]:
        assert entry["id"] in text


# ---- one implementation of the frontier ---------------------------------


def test_show_and_brief_agree_on_the_frontier(run, store, g):
    """`dg show`'s table is built from `brief.rows`, so the human view and the
    agent view cannot disagree about what is open or what it waits on."""
    write(g)
    shown = run("show").output
    for r in brief.rows(Graph.load(store / "decisions.json")):
        assert r.id in shown
        for w in r.waiting_on:
            assert w in shown


def test_show_reports_provisional_too(run, store, g):
    """If it is what an agent most needs told, it is what a human most needs
    told; `dg show` omitted it entirely."""
    write(g)
    _provisional(store, "D02", premise="D01")
    assert "premise under review" in run("show").output.lower()
    assert "premise under review" in run("show", "--full").output.lower()


# ---- a project that tracks work and no decisions yet (audit D1, D5) ------


@pytest.fixture
def run_tasks(task_store, monkeypatch):
    def go(*args, cols="200"):
        monkeypatch.setenv("COLUMNS", cols)
        return runner.invoke(app, ["--project", str(task_store), *args])
    return go


def test_brief_works_with_a_task_store_and_no_decisions(run_tasks):
    """`Project.exists` accepts either store — a team may track work before it
    tracks decisions — but `text()` still raised straight through the CLI's
    guard, so the session hook read the nonzero exit as "nothing to say" and
    went silent. The A2 failure, restored through the new store."""
    res = run_tasks("brief")
    assert res.exit_code == 0
    assert "Traceback" not in res.output
    assert "TASK GRAPH" in res.output and "no decisions.json here" in res.output
    assert "TASKS  4" in res.output
    assert "CHECK:" in res.output


def test_brief_json_survives_a_project_with_no_decisions(run_tasks):
    payload = json.loads(run_tasks("brief", "--json").output)
    assert payload["counts"] == {} and payload["tasks"]["counts"]


def test_brief_counts_both_staging_trays(run_tasks, task_store):
    """"STAGED BUT NOT APPLIED: 0" beside a full task tray is the reading that
    loses work."""
    pending.stage({"op": "set_status", "task": "T02", "status": "DOING"},
                  task_store / ".dgraph-task-pending.json")
    out = run_tasks("brief").output
    assert "STAGED BUT NOT APPLIED: 0 decision, 1 task" in out
    assert "dg task pending" in out
    assert json.loads(run_tasks("brief", "--json").output)["staged_tasks"] == 1


def test_an_unreadable_task_tray_is_reported_not_counted_as_none(run_tasks,
                                                                 task_store):
    (task_store / ".dgraph-task-pending.json").write_text("{oh no")
    payload = json.loads(run_tasks("brief", "--json").output)
    assert payload["staged_tasks"] == 0
    assert any(".dgraph-task-pending.json" in v["text"] and v["blocking"]
               for v in payload["violations"])


# ---- one definition of "blocked" -----------------------------------------


def _five_statuses(root, edges):
    """A task store covering every status, wired by `edges`.

    `edges` is a list of (from, to) `precedes` pairs, so the same five tasks
    can be posed with and without prerequisites.
    """
    raw = {"areas": ["Alpha"], "tasks": [
        {"id": "T01", "title": "done", "area": "Alpha", "status": "DONE",
         "done": "2026-01-05", "outcome": "landed"},
        {"id": "T02", "title": "todo", "area": "Alpha", "status": "TODO"},
        {"id": "T03", "title": "doing", "area": "Alpha", "status": "DOING"},
        {"id": "T04", "title": "parked", "area": "Alpha", "status": "PARKED",
         "stops": [{"why": "waiting on a person", "date": "2026-01-06"}]},
        {"id": "T05", "title": "dropped", "area": "Alpha", "status": "DROPPED",
         "stops": [{"why": "overtaken", "date": "2026-01-06"}]},
    ], "edges": [{"from": a, "to": [b], "kind": "precedes"} for a, b in edges]}
    (root / "tasks.json").write_text(json.dumps(raw, indent=2),
                                     encoding="utf-8")
    return root


def test_work_in_hand_and_work_put_down_are_not_blocked(tmp_path, monkeypatch):
    """The F1 shape. `blocked` used to be frontier-minus-ready, so a DOING task
    and a PARKED task -- neither waiting on anything -- were both counted
    blocked, and the brief disagreed with `is:blocked` and the `dg task` table
    about who was stuck."""
    root = _five_statuses(tmp_path, edges=[])
    monkeypatch.setattr(project, "_override", root)
    payload = json.loads(
        runner.invoke(app, ["--project", str(root), "brief", "--json"]).output)
    assert payload["tasks"]["blocked"] == 0


@pytest.mark.parametrize("edges", [
    [],
    [("T01", "T02")],                       # a resolved prerequisite: not blocked
    [("T02", "T03"), ("T03", "T04"), ("T02", "T05")],
    [("T01", "T02"), ("T02", "T03"), ("T02", "T04"), ("T03", "T05")],
])
def test_the_brief_and_the_query_name_the_same_blocked_tasks(tmp_path,
                                                             monkeypatch,
                                                             edges):
    """The claim is not "this store gives the right number" but "the brief and
    `is:blocked` agree about who is blocked" -- over every status, with and
    without prerequisites. A fourth surface inventing a fifth answer is the
    finding, so the test compares two surfaces rather than one to a constant."""
    root = _five_statuses(tmp_path, edges=edges)
    monkeypatch.setattr(project, "_override", root)

    def dg(*args):
        return runner.invoke(app, ["--project", str(root), *args])

    payload = json.loads(dg("brief", "--json").output)
    ids = [x for x in dg("find", "is:blocked", "--tasks",
                         "--ids").output.split() if x]
    assert payload["tasks"]["blocked"] == len(ids)
