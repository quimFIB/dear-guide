"""`rule` on an open vertex and `done_when` on a task: optional prose
pre-commitments, amendable like a note, shown back at decide and done (T55,
`D75`).

Independent of the probe slots: these are the writer's own wording of what
would count, not a criterion a domain reads. D75's falsifier is the shape of
this file — a store carrying neither field stays valid, and neither is ever
required at add.
"""

import copy
import json

import pytest
from typer.testing import CliRunner

from dgraph import editor, integrate, limits, pending, render, task_editor
from dgraph import task_pending, task_render
from dgraph.cli import app
from dgraph.json_import import SCHEMA
from dgraph.model import Graph
from dgraph.tasks import TaskGraph
from tests.conftest import FIXTURE, TASK_FIXTURE

RULE = "the sweep shows recall@10 above 0.95"
DONE = "the benchmark file exists and names the winner"


@pytest.fixture
def run(store, task_store, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERM", "dumb")
    runner = CliRunner()
    return lambda *args, input=None: runner.invoke(
        app, ["--project", str(store), *args], input=input)


def _unfinished(tg):
    return next(t for t in tg.tasks if tg.tasks[t].unfinished)


# ---- the store: optional, never required -------------------------------


def test_a_store_carrying_neither_field_is_valid_and_round_trips():
    g = Graph.from_dict(FIXTURE)
    assert all(v.rule is None for v in g.vertices.values())
    assert not [v for v in g.validate() if v.blocking]
    assert "rule" not in json.dumps(g.to_dict())
    tg = TaskGraph.from_dict(TASK_FIXTURE)
    assert all(t.done_when is None for t in tg.tasks.values())
    assert "done_when" not in json.dumps(tg.to_dict())


def test_the_fields_load_round_trip_and_import():
    raw = copy.deepcopy(FIXTURE)
    raw["vertices"][4]["rule"] = RULE
    g = Graph.from_dict(raw)
    assert g.vertices["D05"].rule == RULE and g.vertices["D05"].extra == {}
    assert Graph.from_dict(g.to_dict()).vertices["D05"].rule == RULE
    traw = copy.deepcopy(TASK_FIXTURE)
    traw["tasks"][0]["done_when"] = DONE
    tg = TaskGraph.from_dict(traw)
    t = tg.tasks[traw["tasks"][0]["id"]]
    assert t.done_when == DONE and t.extra == {}
    assert "rule" in SCHEMA["decisions"]["optional"]
    assert "done_when" in SCHEMA["tasks"]["optional"]


def test_both_fields_are_under_the_synopsis_rule():
    assert {"rule", "done_when"} <= set(limits.TERSE_FIELDS)
    assert limits.overlong({"rule": "x" * 500}) == [("rule", 500)]


# ---- written at add, amended like a note ------------------------------------


def test_add_writes_it_and_amend_changes_it_on_the_decision_side(run, store):
    res = run("add", "--id", "D07", "--title", "q", "--area", "Alpha",
              "--after", "D05", "--rule", RULE)
    assert res.exit_code == 0, res.output
    run("apply")
    assert Graph.load(store / "decisions.json").vertices["D07"].rule == RULE
    res = run("amend", "D07", "--rule", "a tighter rule")
    assert res.exit_code == 0 and "rule" in res.output, res.output
    run("apply")
    assert Graph.load(store / "decisions.json").vertices["D07"].rule == "a tighter rule"


def test_add_writes_it_and_amend_changes_it_on_the_task_side(run, store, task_store):
    res = run("task", "add", "--id", "T99", "--title", "w", "--area", "Alpha",
              "--done-when", DONE)
    assert res.exit_code == 0, res.output
    run("apply")
    assert TaskGraph.load(task_store / "tasks.json").tasks["T99"].done_when == DONE
    res = run("task", "amend", "T99", "--done-when", "sharper")
    assert res.exit_code == 0, res.output
    run("apply")
    assert TaskGraph.load(task_store / "tasks.json").tasks["T99"].done_when == "sharper"


def test_each_store_refuses_the_other_stores_field(store, tg):
    g = Graph.from_dict(FIXTURE)
    with pytest.raises(pending.ApplyError, match="not amended in done_when"):
        pending.vet(g, {"op": "set_fields", "vertex": "D05", "done_when": "x"})
    with pytest.raises(pending.ApplyError, match="not amended in rule"):
        task_pending.vet(tg, {"op": "set_fields", "task": _unfinished(tg),
                              "rule": "x"})


def test_amending_to_the_same_value_is_refused_like_a_note(store):
    g = pending.apply_all(Graph.from_dict(FIXTURE), [
        {"op": "set_fields", "vertex": "D05", "rule": RULE}])
    with pytest.raises(pending.ApplyError, match="already has that rule"):
        pending.vet(g, {"op": "set_fields", "vertex": "D05", "rule": RULE})


# ---- shown back at decide and done -----------------------------------------


def test_decide_shows_the_rule_before_asking(run, store):
    run("add", "--id", "D07", "--title", "q", "--area", "Alpha",
        "--after", "D05", "--rule", RULE)
    run("apply")
    res = run("decide", "D07", "-a", "yes", "-s", "discussion")
    assert res.exit_code == 0, res.output
    assert "rule for settling" in res.output and RULE in res.output
    assert "rule for settling" not in run("decide", "D05", "-a", "y", "-s", "s",
                                          "-f", "f").output


def test_the_close_buffer_carries_the_rule_as_context(g, store):
    g.vertices["D05"].rule = RULE
    g.save()
    g2 = Graph.load()
    assert RULE in editor.render_close(g2, "D05")
    assert "rule for settling" in editor.render_close(g2, "D05")


def test_task_done_shows_done_when_before_asking(run, store, task_store):
    tg = TaskGraph.load(task_store / "tasks.json")
    tid = _unfinished(tg)
    run("task", "amend", tid, "--done-when", DONE); run("apply")
    res = run("task", "done", tid, "-o", "did it")
    assert res.exit_code == 0, res.output
    assert "done when" in res.output and DONE in res.output


def test_the_done_buffer_carries_done_when(tg, task_store):
    tid = _unfinished(tg)
    tg.tasks[tid].done_when = DONE
    assert f"Done when: {DONE}" in task_editor.render_done(tg, None, tid)


# ---- the add buffers ---------------------------------------------------------


def test_the_add_buffers_round_trip_the_prose(g, store, tg, task_store):
    from tests.test_editor import fill
    text = editor.render_add(g, seed={"rule": RULE})
    assert "** Rule" in text and RULE in text
    ops = editor.parse(fill(text, title="q", area="Alpha"), g=g)
    assert ops[0]["rule"] == RULE
    ttext = task_editor.render_add(tg, None, seed={"done_when": DONE})
    assert "** Done when" in ttext and DONE in ttext
    tops = task_editor.parse(fill(ttext, title="w", area="Alpha"), tg=tg, g=None)
    assert tops[0]["done_when"] == DONE


# ---- the seam and the views ------------------------------------------------


def test_the_seam_carries_both_fields(store):
    base = Graph.from_dict(FIXTURE)
    theirs = pending.apply_all(base, [
        {"op": "set_fields", "vertex": "D05", "rule": RULE},
        {"op": "add_vertex", "id": "D07", "title": "q", "area": "Alpha",
         "rule": "born with one"},
        {"op": "add_edge", "from": "D05", "to": ["D07"]}])
    ops = integrate.decisions(base, theirs).ops
    assert {"op": "set_fields", "vertex": "D05", "rule": RULE} in ops
    assert next(o for o in ops if o["op"] == "add_vertex")["rule"] == "born with one"
    tbase = TaskGraph.from_dict(TASK_FIXTURE)
    tid = _unfinished(tbase)
    ttheirs = task_pending.apply_all(tbase, [
        {"op": "set_fields", "task": tid, "done_when": DONE}])
    assert {"op": "set_fields", "task": tid, "done_when": DONE} in \
        integrate.tasks(tbase, ttheirs).ops
    # two clones wording it differently is contested, like a title
    mine = pending.apply_all(base, [
        {"op": "set_fields", "vertex": "D05", "rule": "mine"}])
    rep = integrate.plan(mine, None, base, None, theirs, None)
    assert any("rule differs" in f.message for f in rep.contested)


def test_node_find_and_the_views_show_the_prose(run, store, task_store):
    assert "Rule for settling" not in run("node", "D05").output
    run("amend", "D05", "--rule", RULE); run("apply")
    out = run("node", "D05").output
    assert "Rule for settling" in out and RULE in out
    assert "D05" in run("find", "rule:recall").output
    assert f"- **Rule:** {RULE}" in render.render(Graph.load(store / "decisions.json"))
    tg = TaskGraph.load(task_store / "tasks.json")
    tid = _unfinished(tg)
    run("task", "amend", tid, "--done-when", DONE); run("apply")
    tout = run("task", "node", tid).output
    assert "Done when" in tout and DONE in tout
    assert f"- **Done when:** {DONE}" in task_render.render(
        TaskGraph.load(task_store / "tasks.json"))
