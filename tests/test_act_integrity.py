"""A batch that holds part of an act is refused at the one apply door.

`G11` made the tray hold judgements: `stage_all` stamps a group, `pending.drop`
refuses to take a member out, and `dg apply --group` takes an act whole. Every
one of those guards is on a *door*. `applying.apply_tasks` and
`apply_decisions` take a list of ops as data and write whatever they are
handed -- so a caller that selects ops by any predicate other than the act
can still land half of one, and the broker's mechanical apply did, one commit
after `G11` closed. Audit `X-F1`, and a shape that recurs: a guard placed on
every door instead of on the one function every door reaches.

These pin the guard where it belongs: on the function every door reaches.
"""

from __future__ import annotations

import json

import pytest

from dgraph import applying, pending, project, task_pending


def _tray(path, ops):
    path.write_text(json.dumps(ops), encoding="utf-8")


def test_apply_tasks_refuses_part_of_an_act(task_store):
    """`add_task` handed in alone, its `add_dep` still staged under the same
    group: refused, nothing written, the tray untouched, and the message names
    the member left behind."""
    ops = [
        {"op": "add_task", "id": "T09", "title": "second", "area": "Alpha",
         "by": "a", "ref": "aaaa", "group": "gggg"},
        {"op": "add_dep", "from": "T02", "to": ["T09"], "kind": "precedes",
         "by": "a", "ref": "bbbb", "group": "gggg"},
    ]
    _tray(task_store / ".dgraph-task-pending.json", ops)
    before = (task_store / "tasks.json").read_text()
    with pytest.raises(pending.ApplyError) as exc:
        applying.apply_tasks(ops[:1])
    assert "bbbb" in str(exc.value) and "add_dep" in str(exc.value)
    assert (task_store / "tasks.json").read_text() == before
    assert json.loads((task_store / ".dgraph-task-pending.json").read_text()) == ops


def test_apply_tasks_takes_a_whole_act(task_store):
    """The same act handed in whole lands, so the guard is about the
    partition and not about groups as such."""
    ops = [
        {"op": "add_task", "id": "T09", "title": "second", "area": "Alpha",
         "by": "a", "ref": "aaaa", "group": "gggg"},
        {"op": "add_dep", "from": "T02", "to": ["T09"], "kind": "precedes",
         "by": "a", "ref": "bbbb", "group": "gggg"},
    ]
    _tray(task_store / ".dgraph-task-pending.json", ops)
    res = applying.apply_tasks(ops)
    assert res.applied == 2
    assert "T09" in res.graph.tasks
    tray = task_store / ".dgraph-task-pending.json"
    assert not tray.exists() or json.loads(tray.read_text()) == []


def test_apply_tasks_ignores_groups_that_have_already_left(task_store):
    """A member whose siblings are no longer in the tray is a group of one
    -- applied or dropped by a route that judged the act -- and is not
    refused for company it has not got."""
    lone = [{"op": "set_status", "task": "T02", "status": "DOING", "by": "a",
             "ref": "cccc", "group": "gone"}]
    _tray(task_store / ".dgraph-task-pending.json", lone)
    assert applying.apply_tasks(lone).applied == 1


def test_apply_decisions_refuses_part_of_an_act(store):
    """The twin, on the store whose invariants happened to cover this for a
    reopen and do not for `dg add --after`."""
    ops = [
        {"op": "add_vertex", "id": "D90", "title": "a new question",
         "area": "Alpha", "by": "a", "ref": "aaaa", "group": "gggg"},
        {"op": "add_edge", "from": "D01", "to": "D90",
         "by": "a", "ref": "bbbb", "group": "gggg"},
    ]
    _tray(store / ".dgraph-pending.json", ops)
    before = (store / "decisions.json").read_text()
    with pytest.raises(pending.ApplyError) as exc:
        applying.apply_decisions(ops[:1])
    assert "bbbb" in str(exc.value)
    assert (store / "decisions.json").read_text() == before
    assert json.loads((store / ".dgraph-pending.json").read_text()) == ops


# ---- the tray says which ops are one act ----------------------------------
#
# Refusing to split an act (above) is only half of it: `dg pending` listed
# `add_vertex D77` and `add_edge D73 → D77` as two rows that happened to be
# adjacent, so a reader had no way to tell that `dg drop 1` would be refused
# or that `dg apply --group ckqz` takes both. The browser tray drew the same
# rows the same way. Both now draw the act as one block — a rail down its
# rows in the CLI, a spanning control cell in the page (`test_doors`).

from typer.testing import CliRunner  # noqa: E402
from dgraph.cli import app  # noqa: E402

ACT = [
    {"op": "add_vertex", "id": "D90", "title": "a new question",
     "area": "Alpha", "ref": "aaaa", "group": "gggg"},
    {"op": "add_edge", "from": "D01", "to": ["D90"],
     "ref": "bbbb", "group": "gggg"},
]
LONE = [{"op": "add_edge", "from": "D02", "to": ["D03"], "ref": "cccc"}]


def _pending(store, *args):
    res = CliRunner().invoke(app, ["--project", str(store), "pending", *args])
    assert res.exit_code == 0, res.output
    return res.output


def test_the_listing_draws_a_rail_down_each_act(store, monkeypatch):
    monkeypatch.setenv("COLUMNS", "120")
    _tray(store / ".dgraph-pending.json", ACT + LONE)
    out = _pending(store)
    lines = out.splitlines()
    assert lines[0].endswith("3 op(s) in 2 act(s)"), lines[0]
    assert lines[1].startswith("  ┌ 0  aaaa"), lines[1]
    assert lines[2].startswith("  └ 1  bbbb"), lines[2]
    assert lines[3].startswith("    2  cccc"), lines[3]
    assert "`dg apply --group <id>` takes one act" in out
    assert "`dg drop <id> --group` drops one" in out


def test_a_rail_runs_through_the_middle_of_a_longer_act(store, monkeypatch):
    monkeypatch.setenv("COLUMNS", "120")
    three = ACT + [{"op": "add_edge", "from": "D02", "to": ["D90"],
                    "ref": "dddd", "group": "gggg"}]
    _tray(store / ".dgraph-pending.json", three)
    lines = _pending(store).splitlines()
    assert [ln[2] for ln in lines[1:4]] == ["┌", "│", "└"], lines[1:4]


def test_a_tray_of_single_ops_reads_exactly_as_it_did(store, monkeypatch):
    """Every tray staged before groups existed is made of these. No rail, no
    act count, no act line — the reading a single writer always had."""
    monkeypatch.setenv("COLUMNS", "120")
    _tray(store / ".dgraph-pending.json", LONE + [
        {"op": "add_edge", "from": "D01", "to": ["D03"], "ref": "eeee"}])
    out = _pending(store)
    lines = out.splitlines()
    assert lines[0].endswith("2 op(s)") and "act" not in lines[0], lines[0]
    assert lines[1].startswith("  0  cccc"), lines[1]
    assert "┌" not in out and "└" not in out
    assert "--group" not in out


def test_the_table_rules_off_each_act_and_not_single_ops(store, monkeypatch):
    """`--full` draws a rule where an act of more than one begins or ends,
    and none between two single ops — a rule after every row would draw a
    grouping the tray does not have."""
    monkeypatch.setenv("COLUMNS", "160")
    _tray(store / ".dgraph-pending.json", ACT + LONE + [
        {"op": "add_edge", "from": "D01", "to": ["D03"], "ref": "eeee"}])
    out = _pending(store, "--full")
    assert "4 op(s) in 3 act(s)" in out
    assert "┌ 0" in out and "└ 1" in out and "  2" in out
    rows = [ln for ln in out.splitlines() if ln.startswith("│")]
    rules = [ln for ln in out.splitlines() if ln.startswith("├")]
    assert len(rows) == 4, out          # the four ops; the header row is ┃
    assert len(rules) == 1, out         # after the act; none between 2 and 3
    assert out.index("└ 1") < out.index("├") < out.index("  2")
