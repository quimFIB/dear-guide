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
