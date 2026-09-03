"""`bind` / `unbind` on both stores: an address, written like an edge (T52).

`binds` is a set of `{kind, ref}` held as a list on a vertex and a task —
what the record is *about* in a domain's terms. It is written by two ops
that take the union and the difference, as `add_edge` and `remove_edge` do,
and never by `set_fields` (proposal §Reconciliation C2): a field a
`set_fields` assigns has the semantics of a scalar, so two clones binding
different refs to one record would be a keep-or-take whose only right
answer is the union. Both ops sit in `CANNOT_CONFLICT` with the edge's
reason, and the seam derives them from the set difference per record.
"""

import copy
import json

import pytest
from typer.testing import CliRunner

from dgraph import integrate, pending, render, task_pending, task_render
from dgraph.cli import app
from dgraph.json_import import SCHEMA, read
from dgraph.model import Bind, Graph, bind_fault, spell_bind
from dgraph.tasks import TaskGraph
from tests.conftest import FIXTURE, TASK_FIXTURE

A = {"kind": "rocq.constant", "ref": "Closure.closed_under_step"}
B = {"kind": "rocq.file", "ref": "theories/Closure.v"}


@pytest.fixture
def run(store, task_store, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERM", "dumb")
    runner = CliRunner()
    return lambda *args, input=None: runner.invoke(
        app, ["--project", str(store), *args], input=input)


def _tray(store, name=".dgraph-pending.json") -> list[dict]:
    return pending.load(store / name)


# ---- the shape --------------------------------------------------------------


@pytest.mark.parametrize("bind, names", [
    ("rocq.constant:X", "not str"),
    ({"kind": "rocq.constant"}, "ref is a non-empty string"),
    ({"kind": "rocq.constant", "ref": "  "}, "ref is a non-empty string"),
    ({"kind": "nodot", "ref": "X"}, "does not name a domain"),
    ({"kind": "rocq.constant", "ref": "X", "note": 1}, "note is not read"),
])
def test_each_shape_fault_is_named(bind, names):
    assert names in (bind_fault(bind) or "")
    assert bind_fault(A) is None


def test_the_spelling_splits_on_the_first_colon_only():
    assert spell_bind("rocq.constant:Closure.closed_under_step") == A
    assert spell_bind("git.rev:a:b") == {"kind": "git.rev", "ref": "a:b"}


# ---- the store ---------------------------------------------------------------


def test_binds_load_and_round_trip_on_both_records():
    raw = copy.deepcopy(FIXTURE)
    raw["vertices"][0]["binds"] = [A, B]
    g = Graph.from_dict(raw)
    assert g.vertices["D01"].binds == [Bind(**A), Bind(**B)]
    assert g.vertices["D01"].extra == {}
    assert Graph.from_dict(g.to_dict()).vertices["D01"].binds == [Bind(**A), Bind(**B)]
    traw = copy.deepcopy(TASK_FIXTURE)
    traw["tasks"][0]["binds"] = [A]
    tg = TaskGraph.from_dict(traw)
    t = tg.tasks[traw["tasks"][0]["id"]]
    assert t.binds == [Bind(**A)] and t.extra == {}
    assert TaskGraph.from_dict(tg.to_dict()).tasks[t.id].binds == [Bind(**A)]


def test_validate_names_a_bad_pair_and_a_duplicate_in_both_stores():
    raw = copy.deepcopy(FIXTURE)
    raw["vertices"][0]["binds"] = [A, A, {"kind": "nodot", "ref": "x"}]
    found = [v for v in Graph.from_dict(raw).validate()
             if v.check == "binding_wellformed"]
    assert [("twice" in v.message, "domain" in v.message) for v in found] == [
        (True, False), (False, True)]
    assert all(v.blocking for v in found)
    traw = copy.deepcopy(TASK_FIXTURE)
    traw["tasks"][0]["binds"] = [{"kind": "rocq.constant", "ref": ""}]
    assert "task_binding_wellformed" in [
        v.check for v in TaskGraph.from_dict(traw).validate()]


def test_the_import_schema_accepts_binds(tmp_path):
    assert "binds" in SCHEMA["decisions"]["optional"]
    assert "binds" in SCHEMA["tasks"]["optional"]
    raw = copy.deepcopy(FIXTURE)
    raw["vertices"][0]["binds"] = [A]
    p = tmp_path / "decisions.json"
    p.write_text(json.dumps(raw))
    assert read(p, "decisions").graph.vertices["D01"].binds == [Bind(**A)]


# ---- the ops: union and difference -----------------------------------------


def _bind(vid, *binds, op="bind"):
    return {"op": op, "vertex": vid, "binds": list(binds)}


def test_bind_is_the_union_and_unbind_the_difference(store):
    g = pending.apply_all(Graph.from_dict(FIXTURE), [_bind("D01", A)])
    g = pending.apply_all(g, [_bind("D01", A, B)])       # A already held
    assert [b.spelled for b in g.vertices["D01"].binds] == [
        "rocq.constant:Closure.closed_under_step", "rocq.file:theories/Closure.v"]
    g = pending.apply_all(g, [_bind("D01", A, op="unbind")])
    assert g.vertices["D01"].binds == [Bind(**B)]


def test_unbind_naming_nothing_held_is_refused_like_remove_edge(store):
    g = Graph.from_dict(FIXTURE)
    with pytest.raises(pending.ApplyError, match="D01 is not bound to"):
        pending.apply_all(g, [_bind("D01", A, op="unbind")])
    with pytest.raises(pending.ApplyError, match="names no"):
        pending.apply_all(g, [_bind("D01")])


def test_vet_refuses_a_malformed_pair_at_the_door_on_both_stores(store, tg):
    g = Graph.from_dict(FIXTURE)
    with pytest.raises(pending.ApplyError, match="bind: .*domain"):
        pending.vet(g, _bind("D01", {"kind": "nodot", "ref": "x"}))
    tid = next(iter(tg.tasks))
    with pytest.raises(pending.ApplyError, match="bind: .*domain"):
        task_pending.vet(tg, {"op": "bind", "task": tid,
                              "binds": [{"kind": "nodot", "ref": "x"}]})
    out = task_pending.apply_all(tg, [{"op": "bind", "task": tid, "binds": [A]}])
    assert out.tasks[tid].binds == [Bind(**A)]
    out = task_pending.apply_all(out, [{"op": "unbind", "task": tid,
                                        "binds": [A]}])
    assert out.tasks[tid].binds == []


def test_amend_cannot_reach_the_binds():
    assert "binds" not in pending.FIELDS


def test_compose_bind_says_what_was_already_held(store):
    g = pending.apply_all(Graph.from_dict(FIXTURE), [_bind("D01", A)])
    op, fresh, already = pending.compose_bind(g, vid="D01", binds=[A, B])
    assert op["binds"] == [B] and fresh == [Bind(**B).spelled]
    assert already == [Bind(**A).spelled]
    op, fresh, already = pending.compose_bind(g, vid="D01", binds=[A])
    assert op is None and already == [Bind(**A).spelled]
    op, fresh, already = pending.compose_bind(g, vid="D01", binds=[B],
                                              remove=True)
    assert op is None and already == [Bind(**B).spelled]


# ---- the doors -------------------------------------------------------------


def test_dg_bind_and_unbind_stage_and_apply(run, store):
    res = run("bind", "D01", "rocq.constant:Closure.closed_under_step",
              "rocq.file:theories/Closure.v")
    assert res.exit_code == 0 and "bound to" in res.output, res.output
    assert _tray(store)[0]["binds"] == [A, B]
    run("apply")
    res = run("bind", "D01", "rocq.constant:Closure.closed_under_step")
    assert res.exit_code == 0 and "already bound to" in res.output
    assert _tray(store) == []            # nothing fresh, nothing staged
    res = run("unbind", "D01", "rocq.file:theories/Closure.v")
    assert res.exit_code == 0 and "unbound from" in res.output
    run("apply")
    assert Graph.load(store / "decisions.json").vertices["D01"].binds == [Bind(**A)]
    res = run("bind", "D01", "nodot:x")
    assert res.exit_code == 1 and "does not name a domain" in res.output


def test_dg_task_bind_and_unbind(run, store, task_store):
    tg = TaskGraph.load(task_store / "tasks.json")
    tid = next(iter(tg.tasks))
    res = run("task", "bind", tid, "rocq.file:theories/Closure.v")
    assert res.exit_code == 0, res.output
    assert _tray(task_store, ".dgraph-task-pending.json")[0]["task"] == tid
    run("apply")
    assert TaskGraph.load(task_store / "tasks.json").tasks[tid].binds == [Bind(**B)]
    res = run("task", "unbind", tid, "rocq.constant:nope")
    assert res.exit_code == 0 and "not bound to" in res.output


def test_the_tray_lists_the_pairs(run, store):
    run("bind", "D01", "rocq.constant:Closure.closed_under_step")
    out = run("pending").output
    assert "bind" in out and "rocq.constant:Closure.closed_under_step" in out


# ---- the seam --------------------------------------------------------------


def test_the_seam_derives_the_set_difference_per_record(store):
    base = pending.apply_all(Graph.from_dict(FIXTURE), [_bind("D01", A)])
    theirs = pending.apply_all(base, [_bind("D01", B), _bind("D01", A, op="unbind"),
                                      _bind("D02", A)])
    ops = integrate.decisions(base, theirs).ops
    assert [o for o in ops if o["op"] in ("bind", "unbind")] == [
        {"op": "bind", "vertex": "D01", "binds": [B]},
        {"op": "unbind", "vertex": "D01", "binds": [A]},
        {"op": "bind", "vertex": "D02", "binds": [A]}]
    replay = pending.apply_all(base, ops)
    assert replay.vertices["D01"].binds == theirs.vertices["D01"].binds
    tbase = TaskGraph.from_dict(TASK_FIXTURE)
    tid = next(iter(tbase.tasks))
    ttheirs = task_pending.apply_all(tbase, [{"op": "bind", "task": tid,
                                              "binds": [A]}])
    assert [o for o in integrate.tasks(tbase, ttheirs).ops if o["op"] == "bind"] == [
        {"op": "bind", "task": tid, "binds": [A]}]


def test_two_clones_binding_one_record_compose_rather_than_contest(store):
    base = Graph.from_dict(FIXTURE)
    mine = pending.apply_all(base, [_bind("D01", A)])
    theirs = pending.apply_all(base, [_bind("D01", B)])
    rep = integrate.plan(mine, None, base, None, theirs, None)
    assert not rep.contested and rep.ok
    assert {"bind", "unbind"} <= set(integrate.CANNOT_CONFLICT)


def test_a_removal_of_a_record_bound_here_is_contested(store):
    base = Graph.from_dict(FIXTURE)
    mine = pending.apply_all(base, [_bind("D06", A)])
    theirs = pending.apply_all(base, [
        {"op": "remove_vertex", "vertex": "D06", "mode": "sever"}])
    rep = integrate.plan(mine, None, base, None, theirs, None)
    assert any("bound to something here" in f.message for f in rep.contested)


# ---- the views ---------------------------------------------------------------


def test_node_and_the_views_show_binds_only_where_there_are_some(run, store,
                                                                 task_store):
    assert "bound to" not in run("node", "D01").output
    assert "Bound to" not in render.render(Graph.from_dict(FIXTURE))
    run("bind", "D01", "rocq.constant:Closure.closed_under_step"); run("apply")
    assert "bound to    rocq.constant:Closure.closed_under_step" in run("node", "D01").output
    g = Graph.load(store / "decisions.json")
    assert "- **Bound to:** `rocq.constant:Closure.closed_under_step`" in render.render(g)
    tg = TaskGraph.from_dict(TASK_FIXTURE)
    assert "Bound to" not in task_render.render(tg)
    tid = next(iter(tg.tasks))
    tg = task_pending.apply_all(tg, [{"op": "bind", "task": tid, "binds": [B]}])
    assert "- **Bound to:** `rocq.file:theories/Closure.v`" in task_render.render(tg)
    run("task", "bind", tid, "rocq.file:theories/Closure.v"); run("apply")
    assert "bound to    rocq.file:theories/Closure.v" in run("task", "node", tid).output


# ---- N02 · a key inside a bind loads and is refused at check ---------------

def test_a_key_inside_a_bind_loads_and_is_refused_at_check_on_both_stores():
    raw = copy.deepcopy(FIXTURE)
    raw["vertices"][4]["binds"] = [{"kind": "rocq.constant", "ref": "X",
                                    "since": "2026"}]
    g = Graph.from_dict(raw)                       # no crash (audit N-F2)
    assert g.vertices["D05"].binds[0].spelled == "rocq.constant:X"
    found = [v for v in g.validate() if v.check == "binding_wellformed"]
    assert len(found) == 1 and found[0].blocking and "since" in found[0].message
    assert g.to_dict()["vertices"][4]["binds"][0]["since"] == "2026"

    traw = copy.deepcopy(TASK_FIXTURE)
    traw["tasks"][0]["binds"] = [{"kind": "rocq.file", "ref": "a.v", "since": "x"}]
    tg = TaskGraph.from_dict(traw)
    tfound = [v for v in tg.validate() if v.check == "task_binding_wellformed"]
    assert len(tfound) == 1 and tfound[0].blocking
    assert tg.to_dict()["tasks"][0]["binds"][0]["since"] == "x"
    # the set reading ignores what it cannot read: still one bind, still equal
    assert tg.tasks[traw["tasks"][0]["id"]].binds[0] == Bind("rocq.file", "a.v")
