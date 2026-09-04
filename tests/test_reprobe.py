"""Appended probes on a task and an open vertex, changed only by `reprobe` (T51).

The slot `D71` puts on the two records that are not an edge: a rule for
settling an open question, a definition of done for a task. Both are lists,
last entry live, each dated, and the only writer is an append — `stops` and
`stops` are the precedent (proposal §Reconciliation C3). The shape of
each entry's criterion is `probe_fault`'s, which `tests/test_probe.py` owns;
what this file proves is the append, the doors onto it, the refusals, the
seam, and the views.
"""

import copy
import json

import pytest
from typer.testing import CliRunner

from dgraph import editor, integrate, pending, render, task_editor
from dgraph import task_pending, task_render
from dgraph.cli import app
from dgraph.json_import import SCHEMA, read
from dgraph.model import Graph, Probe
from dgraph.tasks import TaskGraph
from tests.conftest import FIXTURE, TASK_FIXTURE

ONE = {"kind": "prose.rule", "args": {"n": 1}}
TWO = {"kind": "prose.rule", "args": {"n": 2}}


@pytest.fixture
def run(store, task_store, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERM", "dumb")
    runner = CliRunner()
    return lambda *args, input=None: runner.invoke(
        app, ["--project", str(store), *args], input=input)


def _tray(store, name=".dgraph-pending.json") -> list[dict]:
    return pending.load(store / name)


# ---- the store: both records, one shape ----------------------------------


def test_probes_load_and_round_trip_on_a_vertex_and_a_task():
    raw = copy.deepcopy(FIXTURE)
    raw["vertices"][4]["probes"] = [{**ONE, "date": "2026-01-01"}]
    g = Graph.from_dict(raw)
    v = g.vertices["D05"]
    assert v.probe == Probe("prose.rule", {"n": 1}, "2026-01-01")
    assert v.extra == {} and v.probe.criterion == ONE
    assert Graph.from_dict(g.to_dict()).vertices["D05"].probes == v.probes

    traw = copy.deepcopy(TASK_FIXTURE)
    traw["tasks"][0]["probes"] = [{**ONE, "date": "2026-01-01"}]
    tg = TaskGraph.from_dict(traw)
    t = tg.tasks[traw["tasks"][0]["id"]]
    assert t.probe.criterion == ONE and t.extra == {}
    back = TaskGraph.from_dict(tg.to_dict())
    assert back.tasks[t.id].probes == t.probes


def test_a_malformed_entry_is_refused_at_load_like_a_stop():
    raw = copy.deepcopy(FIXTURE)
    raw["vertices"][4]["probes"] = [{"kind": "prose.rule"}]
    with pytest.raises(ValueError, match="D05: malformed probes entry"):
        Graph.from_dict(raw)


def test_validate_names_a_bad_criterion_or_a_missing_date_in_both_stores():
    raw = copy.deepcopy(FIXTURE)
    raw["vertices"][4]["probes"] = [{"kind": "nodot", "args": {}, "date": "d"},
                                    {**ONE, "date": ""}]
    found = [v for v in Graph.from_dict(raw).validate()
             if v.check == "probe_wellformed"]
    assert len(found) == 2 and all(v.blocking for v in found)
    assert "does not name a domain" in found[0].message
    assert "missing its date" in found[1].message

    traw = copy.deepcopy(TASK_FIXTURE)
    traw["tasks"][0]["probes"] = [{"kind": "nodot", "args": {}, "date": "d"}]
    tfound = [v for v in TaskGraph.from_dict(traw).validate()
              if v.check == "task_probe_wellformed"]
    assert len(tfound) == 1 and tfound[0].blocking


def test_the_import_schema_accepts_the_field_on_both(tmp_path):
    assert "probes" in SCHEMA["decisions"]["optional"]
    assert "probes" in SCHEMA["tasks"]["optional"]
    raw = copy.deepcopy(FIXTURE)
    raw["vertices"][4]["probes"] = [{**ONE, "date": "2026-01-01"}]
    p = tmp_path / "decisions.json"
    p.write_text(json.dumps(raw))
    assert read(p, "decisions").graph.vertices["D05"].probe.criterion == ONE


# ---- the ops: add writes the first entry, reprobe appends ----------------


def test_add_vertex_with_a_probe_writes_the_first_dated_entry(store):
    g = pending.apply_all(Graph.from_dict(FIXTURE), [
        {"op": "add_vertex", "id": "D07", "title": "t", "area": "Alpha",
         "probe": ONE, "date": "2026-03-03"},
        {"op": "add_edge", "from": "D05", "to": ["D07"]}])
    assert g.vertices["D07"].probes == [Probe("prose.rule", {"n": 1}, "2026-03-03")]


def test_reprobe_appends_and_keeps_the_earlier_entry(store):
    g = Graph.from_dict(FIXTURE)
    g = pending.apply_all(g, [
        {"op": "reprobe", "vertex": "D05", "probe": ONE, "date": "2026-01-01"}])
    g = pending.apply_all(g, [
        {"op": "reprobe", "vertex": "D05", "probe": TWO, "date": "2026-02-02"}])
    v = g.vertices["D05"]
    assert [p.criterion for p in v.probes] == [ONE, TWO]
    assert v.probe.criterion == TWO and v.probe.date == "2026-02-02"


def test_reprobe_is_refused_on_a_settled_question(store):
    g = Graph.from_dict(FIXTURE)
    with pytest.raises(pending.ApplyError, match="D01 is DECIDED"):
        pending.apply_all(g, [{"op": "reprobe", "vertex": "D01", "probe": ONE,
                               "date": "2026-01-01"}])
    with pytest.raises(pending.ApplyError, match="D01 is DECIDED"):
        pending.compose_reprobe(g, vid="D01", probe=ONE)


def test_vet_refuses_a_malformed_slot_probe_at_the_door(store):
    g = Graph.from_dict(FIXTURE)
    bad = {"kind": "nodot", "args": {}}
    with pytest.raises(pending.ApplyError, match="probe: "):
        pending.vet(g, {"op": "reprobe", "vertex": "D05", "probe": bad,
                        "date": "2026-01-01"})
    with pytest.raises(pending.ApplyError, match="probe: "):
        pending.vet(g, {"op": "add_vertex", "id": "D07", "title": "t",
                        "area": "Alpha", "probe": bad})
    with pytest.raises(pending.ApplyError, match="--probe: "):
        pending.compose_add(g, vid="D07", title="t", area="Alpha", probe=bad)


def test_the_task_store_has_the_same_ops(tg):
    tid = next(t for t in tg.tasks if tg.tasks[t].unfinished)
    out = task_pending.apply_all(tg, [
        {"op": "reprobe", "task": tid, "probe": ONE, "date": "2026-01-01"}])
    out = task_pending.apply_all(out, [
        {"op": "reprobe", "task": tid, "probe": TWO, "date": "2026-02-02"}])
    assert [p.criterion for p in out.tasks[tid].probes] == [ONE, TWO]
    out = task_pending.apply_all(out, [
        {"op": "add_task", "id": "T99", "title": "t", "area": "Alpha",
         "probe": ONE, "date": "2026-03-03"}])
    assert out.tasks["T99"].probe.date == "2026-03-03"
    with pytest.raises(pending.ApplyError, match="probe: "):
        task_pending.vet(out, {"op": "reprobe", "task": tid,
                               "probe": {"kind": "x", "args": {}},
                               "date": "d"})


def test_reprobe_is_refused_on_finished_or_abandoned_work(tg):
    done = next(t for t in tg.tasks if tg.tasks[t].status == "DONE")
    with pytest.raises(pending.ApplyError, match=f"{done} is DONE"):
        task_pending.apply_all(tg, [{"op": "reprobe", "task": done,
                                     "probe": ONE, "date": "d"}])


def test_amend_cannot_reach_the_probes():
    """`FIELDS` is what `set_fields` writes; a probe is a claim, not a
    wording, and the only way to change one is the append."""
    assert "probes" not in pending.FIELDS and "probe" not in pending.FIELDS


# ---- the doors -----------------------------------------------------------


def test_dg_add_and_dg_reprobe_stage_the_slot(run, store):
    res = run("add", "--id", "D07", "--title", "q", "--area", "Alpha",
              "--after", "D05", "--probe", json.dumps(ONE))
    assert res.exit_code == 0, res.output
    add = next(o for o in _tray(store) if o["op"] == "add_vertex")
    assert add["probe"] == ONE and add["date"]
    res = run("reprobe", "D05", "--probe", json.dumps(TWO))
    assert res.exit_code == 0, res.output
    assert "staged" in res.output
    rp = next(o for o in _tray(store) if o["op"] == "reprobe")
    assert rp == {**rp, "vertex": "D05", "probe": TWO}
    assert run("apply").exit_code == 0
    g = Graph.load(store / "decisions.json")
    assert g.vertices["D05"].probe.criterion == TWO
    assert g.vertices["D07"].probe.criterion == ONE


def test_dg_reprobe_refuses_a_settled_question_and_a_bad_shape(run, store):
    res = run("reprobe", "D01", "--probe", json.dumps(ONE))
    assert res.exit_code == 1 and "D01 is DECIDED" in res.output
    res = run("reprobe", "D05", "--probe", '{"kind": "nodot", "args": {}}')
    assert res.exit_code == 1 and "does not name a domain" in res.output
    assert _tray(store) == []


def test_dg_task_add_and_reprobe_stage_the_slot(run, store, task_store):
    tg = TaskGraph.load(task_store / "tasks.json")
    tid = next(t for t in tg.tasks if tg.tasks[t].unfinished)
    res = run("task", "add", "--id", "T99", "--title", "w", "--area", "Alpha",
              "--probe", json.dumps(ONE))
    assert res.exit_code == 0, res.output
    res = run("task", "reprobe", tid, "--probe", json.dumps(TWO))
    assert res.exit_code == 0, res.output
    ops = _tray(task_store, ".dgraph-task-pending.json")
    assert next(o for o in ops if o["op"] == "add_task")["probe"] == ONE
    assert next(o for o in ops if o["op"] == "reprobe")["task"] == tid
    done = next(t for t in tg.tasks if tg.tasks[t].status == "DONE")
    res = run("task", "reprobe", done, "--probe", json.dumps(ONE))
    assert res.exit_code == 1 and f"{done} is DONE" in res.output


def test_the_tray_names_a_reprobe_by_its_criterion(run, store):
    run("reprobe", "D05", "--probe", json.dumps(ONE))
    out = run("pending").output
    assert "reprobe" in out and "prose.rule" in out


# ---- the add buffers carry it -------------------------------------------


def test_the_add_buffer_round_trips_a_probe(g, store):
    from tests.test_editor import fill
    text = editor.render_add(g, seed={"probe": ONE})
    assert "** Probe" in text and '"n": 1' in text
    ops = editor.parse(fill(text, title="q", area="Alpha"), g=g)
    assert ops[0]["probe"] == ONE and ops[0]["date"]
    plain = editor.parse(fill(editor.render_add(g), title="q", area="Alpha"),
                         g=g)
    assert "probe" not in plain[0]


def test_dg_edit_of_a_staged_add_keeps_its_probe(g, store):
    op = {"op": "add_vertex", "id": "D07", "title": "q", "area": "Alpha",
          "status": "OPEN", "probe": ONE, "date": "2026-01-01"}
    assert '"n": 1' in editor.render_op(g, 0, op)


def test_the_task_add_buffer_round_trips_a_probe(tg, task_store):
    from tests.test_editor import fill
    text = task_editor.render_add(tg, None, seed={"probe": ONE})
    assert "** Probe" in text
    ops = task_editor.parse(fill(text, title="w", area="Alpha"),
                            tg=tg, g=None)
    assert ops[0]["probe"] == ONE


# ---- the seam ------------------------------------------------------------


def test_the_seam_derives_a_reprobe_per_new_entry_on_both_stores(store):
    base = Graph.from_dict(FIXTURE)
    theirs = pending.apply_all(base, [
        {"op": "reprobe", "vertex": "D05", "probe": ONE, "date": "2026-01-01"},
        {"op": "add_vertex", "id": "D07", "title": "q", "area": "Alpha",
         "probe": TWO, "date": "2026-02-02"},
        {"op": "add_edge", "from": "D05", "to": ["D07"]}])
    ops = integrate.decisions(base, theirs).ops
    rps = [o for o in ops if o["op"] == "reprobe"]
    assert rps == [
        {"op": "reprobe", "vertex": "D05", "probe": ONE, "date": "2026-01-01"},
        {"op": "reprobe", "vertex": "D07", "probe": TWO, "date": "2026-02-02"}]
    # and replaying them lands the same store
    replay = pending.apply_all(base, ops)
    assert replay.vertices["D07"].probes == theirs.vertices["D07"].probes

    tbase = TaskGraph.from_dict(TASK_FIXTURE)
    tid = next(t for t in tbase.tasks if tbase.tasks[t].unfinished)
    ttheirs = task_pending.apply_all(tbase, [
        {"op": "reprobe", "task": tid, "probe": ONE, "date": "2026-01-01"}])
    tops = integrate.tasks(tbase, ttheirs).ops
    assert [o for o in tops if o["op"] == "reprobe"] == [
        {"op": "reprobe", "task": tid, "probe": ONE, "date": "2026-01-01"}]


def test_two_clones_writing_different_rules_is_contested(store):
    base = Graph.from_dict(FIXTURE)
    mine = pending.apply_all(base, [
        {"op": "reprobe", "vertex": "D05", "probe": ONE, "date": "2026-01-01"}])
    theirs = pending.apply_all(base, [
        {"op": "reprobe", "vertex": "D05", "probe": TWO, "date": "2026-01-01"}])
    rep = integrate.plan(mine, None, base, None, theirs, None)
    assert [f.record for f in rep.contested] == ["D05"]
    assert "rule for settling here too" in rep.contested[0].message
    # the same rule twice is two writers agreeing, not a conflict
    same = pending.apply_all(base, [
        {"op": "reprobe", "vertex": "D05", "probe": ONE, "date": "2026-01-02"}])
    assert not integrate.plan(mine, None, base, None, same, None).contested


def test_a_removal_of_a_record_reprobed_here_is_contested(store):
    base = Graph.from_dict(FIXTURE)
    mine = pending.apply_all(base, [
        {"op": "reprobe", "vertex": "D06", "probe": ONE, "date": "2026-01-01"}])
    theirs = pending.apply_all(base, [
        {"op": "remove_vertex", "vertex": "D06", "mode": "sever"}])
    rep = integrate.plan(mine, None, base, None, theirs, None)
    assert any("criterion was written for it here" in f.message
               for f in rep.contested)


# ---- the views -------------------------------------------------------------


def test_node_shows_the_rule_and_tags_the_live_one(run, store):
    run("reprobe", "D05", "--probe", json.dumps(ONE)); run("apply")
    run("reprobe", "D05", "--probe", json.dumps(TWO)); run("apply")
    out = run("node", "D05").output
    assert "Rule for settling" in out
    assert out.count("prose.rule") == 2 and "(live)" in out
    assert "Rule for settling" not in run("node", "D01").output


def test_task_node_shows_the_definition_of_done(run, store, task_store):
    tg = TaskGraph.load(task_store / "tasks.json")
    tid = next(t for t in tg.tasks if tg.tasks[t].unfinished)
    run("task", "reprobe", tid, "--probe", json.dumps(ONE)); run("apply")
    out = run("task", "node", tid).output
    assert "Definition of done" in out and "prose.rule" in out and "(live)" in out


def test_the_views_show_a_probe_only_where_there_is_one(store, task_store):
    assert "Rule for settling" not in render.render(Graph.from_dict(FIXTURE))
    g = pending.apply_all(Graph.from_dict(FIXTURE), [
        {"op": "reprobe", "vertex": "D05", "probe": ONE, "date": "2026-01-01"}])
    assert "- **Rule for settling:** `prose.rule`" in render.render(g)
    tg = TaskGraph.from_dict(TASK_FIXTURE)
    assert "Definition of done" not in task_render.render(tg)
    tid = next(t for t in tg.tasks if tg.tasks[t].unfinished)
    tg = task_pending.apply_all(tg, [
        {"op": "reprobe", "task": tid, "probe": ONE, "date": "2026-01-01"}])
    assert "*Definition of done:* 2026-01-01 — `prose.rule`" in task_render.render(tg)



# ---- N01 · the seam replays a reprobe in the order it happened -------------
#
# `_probes` used to run before the edge ops and before the status ops, on the
# argument that a reprobe is refused on a settled question and the close is
# among the edge ops — one direction of two. Reopen a question and then write
# its rule, and the other clone adopted the reopen and quarantined the rule as
# inapplicable, telling the reader to run the reopen it had just adopted.
# Audit `N-F1`. Every legal order that contains a reprobe, on both stores.

def _decided_base():
    return Graph.from_dict(FIXTURE)


CLOSE = {"op": "close", "vertex": "D04", "answer": "again", "source": "s",
         "falsifier": "f", "to": ["D05"]}
REOPEN = {"op": "reopen", "vertex": "D04", "why": "moved"}
RP_D = lambda p, d: {"op": "reprobe", "vertex": "D04", "probe": p, "date": d}


@pytest.mark.parametrize("sequence", [
    [{"op": "reprobe", "vertex": "D05", "probe": ONE, "date": "2026-02-01"},
     {"op": "close", "vertex": "D05", "answer": "a", "source": "s",
      "falsifier": "f", "to": ["D06"]}],                    # reprobe, close
    [REOPEN, RP_D(ONE, "2026-02-02")],                       # reopen, reprobe
    [REOPEN, RP_D(ONE, "2026-02-02"), CLOSE],                # reopen, reprobe, close
    [REOPEN, RP_D(ONE, "2026-02-02"), RP_D(TWO, "2026-02-03"), CLOSE],
    [{"op": "add_vertex", "id": "D07", "title": "q", "area": "Alpha",
      "probe": ONE, "date": "2026-02-02"},
     {"op": "reprobe", "vertex": "D07", "probe": TWO, "date": "2026-02-03"}],
], ids=["reprobe-close", "reopen-reprobe", "reopen-reprobe-close",
        "reopen-two-reprobes-close", "new-vertex-two-entries"])
def test_the_seam_replays_every_legal_decision_order_with_a_reprobe(sequence):
    base = _decided_base()
    theirs = pending.apply_all(base, sequence)
    ops = integrate.decisions(base, theirs).ops
    replay = pending.apply_all(base, ops)
    for vid in theirs.vertices:
        assert replay.vertices[vid].probes == theirs.vertices[vid].probes, vid
        assert replay.vertices[vid].status == theirs.vertices[vid].status
    rep = integrate.plan(base, None, base, None, theirs, None)
    assert not rep.inapplicable and not rep.contested, rep.inapplicable


def _task_base():
    return TaskGraph.from_dict(TASK_FIXTURE)


T_RP = lambda t, p, d: {"op": "reprobe", "task": t, "probe": p, "date": d}
T_DONE = lambda t, d: {"op": "set_status", "task": t, "status": "DONE",
                       "outcome": "out", "done": d}
T_DOING = lambda t: {"op": "set_status", "task": t, "status": "DOING"}


T_PARK = lambda t, d: {"op": "set_status", "task": t, "status": "PARKED",
                       "why": "waiting", "date": d}
T_DROP = lambda t, d: {"op": "set_status", "task": t, "status": "DROPPED",
                       "why": "no", "date": d}

# Every sequence here is legal under `D81`'s matrix, which is what changed
# them: three used to restart a `DONE` task and `DONE` is terminal now. The
# ones that replaced them exercise what the store still allows and the seam
# still has to replay — a park and a pick-up, two stops with a restart between
# them, and a stop *before* a finish, which is where `Y-F1` lived.
@pytest.mark.parametrize("sequence", [
    [T_RP("T02", ONE, "2026-02-01"), T_DONE("T02", "2026-02-05")],   # reprobe, done
    [T_DOING("T02"), T_RP("T02", ONE, "2026-02-02")],               # start, reprobe
    [T_DOING("T02"), T_RP("T02", ONE, "2026-02-02"), T_DONE("T02", "2026-02-03")],
    [T_PARK("T02", "2026-02-01"), T_DOING("T02"), T_RP("T02", ONE, "2026-02-02"),
     T_PARK("T02", "2026-02-03")],                          # two stops, restart between
    [T_PARK("T02", "2026-02-01"), T_DOING("T02"), T_RP("T02", TWO, "2026-02-02"),
     T_DONE("T02", "2026-02-04")],                          # stop, then a finish
    [T_RP("T02", ONE, "2026-02-01"), T_DROP("T02", "2026-02-02")],   # reprobe, drop
    [T_RP("T02", ONE, "2026-02-05"), T_DONE("T02", "2026-02-05")],   # same day
], ids=["reprobe-done", "start-reprobe", "start-reprobe-done",
        "park-restart-park", "park-restart-done", "reprobe-drop", "same-day"])
def test_the_seam_replays_every_legal_task_order_with_a_reprobe(sequence):
    base = _task_base()
    theirs = task_pending.apply_all(base, sequence)
    ops = integrate.tasks(base, theirs).ops
    replay = task_pending.apply_all(base, ops)
    for tid in theirs.tasks:
        assert replay.tasks[tid].probes == theirs.tasks[tid].probes, tid
        assert replay.tasks[tid].status == theirs.tasks[tid].status
    rep = integrate.plan(None, base, None, base, None, theirs)
    assert not rep.inapplicable and not rep.contested, rep.inapplicable


# ---- N02 · a key inside a slot is refused at check, not at load ------------
#
# `Probe(**p)` on an entry carrying a key this version does not name raised
# at load — the pre-`D76` crash, one level below the record `extra` protects:
# nothing answered, and `store_loads` denied every commit in the clone. The
# edge's probe was refused by `validate` instead. One door now: the entry
# loads, the key is carried and written back, and `validate` refuses it as
# blocking `probe_wellformed`, which is what the proposal decided a probe's
# shape is (`D71`) — closed, and checked. Audit `N-F2`.

def test_a_key_inside_a_probes_entry_loads_and_is_refused_at_check_on_both_stores():
    raw = copy.deepcopy(FIXTURE)
    raw["vertices"][4]["probes"] = [{**ONE, "date": "2026-01-01", "who": "me"}]
    g = Graph.from_dict(raw)                       # no crash
    assert g.vertices["D05"].probe.criterion == ONE
    found = [v for v in g.validate() if v.check == "probe_wellformed"]
    assert len(found) == 1 and found[0].blocking and "who" in found[0].message
    assert g.to_dict()["vertices"][4]["probes"][0]["who"] == "me"   # carried

    traw = copy.deepcopy(TASK_FIXTURE)
    traw["tasks"][0]["probes"] = [{**ONE, "date": "2026-01-01", "who": "me"}]
    tg = TaskGraph.from_dict(traw)
    tfound = [v for v in tg.validate() if v.check == "task_probe_wellformed"]
    assert len(tfound) == 1 and tfound[0].blocking and "who" in tfound[0].message
    assert tg.to_dict()["tasks"][0]["probes"][0]["who"] == "me"


def test_a_missing_key_in_a_probes_entry_is_still_refused_at_load():
    raw = copy.deepcopy(FIXTURE)
    raw["vertices"][4]["probes"] = [{"kind": "prose.rule", "date": "d"}]
    with pytest.raises(ValueError, match="D05: malformed probes entry"):
        Graph.from_dict(raw)
    raw["vertices"][4]["probes"] = ["not an object"]
    with pytest.raises(ValueError, match="D05: malformed probes entry"):
        Graph.from_dict(raw)
