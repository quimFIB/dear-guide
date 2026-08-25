"""The id range a clone allocates from — audit `F-F3`'s companion.

Three things are guarded here.

First, that **a project with no grant is untouched**. The file is absent in
every single-writer project, and every path through `dgraph/ranges.py` treats
that as "behave the way this tool always has" rather than as a fault. A test
suite that only exercised the granted case would let the ungranted one rot,
and the ungranted one is almost every project.

Second, that **a grant is a rule and not a discipline**. `next_id` only
prefills a form and `--id` is an option, so a range that lives in the prompt is
one `dg add --id` away from being ignored. Every door that creates a record is
checked, not only the one that offers the number.

Third, that **the watermark survives a checkout**, which is the whole reason it
exists: two branches in one worktree share a grant and start from the same
store, so a range on its own re-offers the same id to both and nothing can see
it.
"""

import json

import pytest
from typer.testing import CliRunner

from dgraph import pending, project, ranges, task_pending
from dgraph.cli import app
from dgraph.editor import next_id
from dgraph.model import Graph
from dgraph.task_editor import next_id as task_next_id
from dgraph.tasks import TaskGraph

runner = CliRunner()


@pytest.fixture
def granted(store, task_store, monkeypatch):
    """The fixture project, with this clone granted 50-99 in both stores."""
    monkeypatch.setenv("DG_PROJECT", str(store))
    ranges.save({p: ranges.Grant(50, 99) for p in ranges.PREFIXES}, store)
    return store


@pytest.fixture
def run(store, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERM", "dumb")

    def go(*args, input=None):
        return runner.invoke(app, ["--project", str(store), *args], input=input)

    return go


# ---- a project with no grant is the project this tool always had ----------


def test_no_grant_is_not_a_fault_and_changes_nothing(store, g):
    assert ranges.load(store) == {}
    assert ranges.grant("D", store) is None
    assert ranges.next_number("D", [1, 2, 12], root=store) == 13
    assert next_id(g) == "D07"          # the fixture holds D01-D06


def test_nothing_is_refused_where_nothing_was_granted(store, g):
    """The check is silent rather than permissive-by-accident: `fault` returns
    None because there is no grant, not because the id happened to pass."""
    assert ranges.fault("D", "D99", store) is None
    assert ranges.fault("T", "T01", store) is None


def test_a_grant_removed_puts_allocation_back_where_it_was(granted, g):
    ranges.save({}, granted)
    assert not (granted / project.RANGE_NAME).exists()
    assert next_id(g) == "D07"


# ---- allocation inside a grant --------------------------------------------


def test_the_first_id_of_a_grant_is_its_low_end(granted, g):
    """Not `max(store) + 1`. The fixture's highest is D06 and the grant starts
    at 50, so a clone that ignored its range would hand out D07 — an id the
    writer who holds 1-49 is about to use."""
    assert next_id(g) == "D50"
    assert task_next_id(TaskGraph.load(project.find().tasks)) == "T50"


def test_an_id_already_in_the_range_is_stepped_over(granted, g, store):
    """A grant does not mean an empty range: an integration can land ids from
    this clone's own earlier contribution before it allocates again."""
    from dataclasses import replace
    g.vertices["D57"] = replace(g.vertices["D01"], id="D57")
    g.save(store / "decisions.json")
    assert next_id(Graph.load(store / "decisions.json")) == "D58"


def test_an_exhausted_grant_is_an_error_that_names_the_range(granted, g):
    """Not a spill into the next grant, which is the one outcome a range
    exists to prevent and the one a silent `+ 1` would produce."""
    ranges.save({"D": ranges.Grant(50, 51, 51)}, granted)
    with pytest.raises(ranges.RangeError, match="50-51 is used up"):
        next_id(g)


def test_a_grant_present_and_unreadable_raises_rather_than_falls_back(granted):
    """Falling back to the whole sequence here would hand out ids inside
    somebody else's grant at the moment the operator most believes they are
    protected."""
    (granted / project.RANGE_NAME).write_text("{ not json", encoding="utf-8")
    with pytest.raises(ranges.RangeError, match="could not be read"):
        ranges.load(granted)


# ---- the watermark, and the checkout it has to survive --------------------


def test_staging_raises_the_watermark_and_offering_does_not(granted, g):
    """The distinction the whole design turns on. All four `next_id` callers
    only *offer* an id — one of them answers `/api/graph` on every page load —
    so a watermark bumped there would burn an id per refresh. Staging is the
    first moment a writer has said which id they mean."""
    assert next_id(g) == "D50" and next_id(g) == "D50"       # offered twice
    assert ranges.grant("D", granted).issued is None

    pending.stage_all([{"op": "add_vertex", "id": "D50", "title": "x",
                        "area": "Alpha", "status": "OPEN"}])
    assert ranges.grant("D", granted).issued == 50


def test_the_watermark_stops_a_second_branch_re_offering_an_id(granted, g,
                                                               store):
    """The case a range alone cannot see. `vet` asks *is this id inside my
    range*, and it is; nothing asks *has this range already issued it*, because
    the only memory of what was issued is the store — and a checkout rewinds
    the store while leaving this file alone, being gitignored."""
    ranges.issue("D", 50, granted)
    # The store as the other branch has it: D50 was never applied here.
    assert "D50" not in g.vertices
    assert next_id(g) == "D51"


def test_the_watermark_never_leaves_the_range_it_belongs_to(granted):
    """An id outside the grant is `vet`'s refusal to make, and a mark that
    followed one would point somewhere the grant does not reach."""
    ranges.issue("D", 12, granted)
    assert ranges.grant("D", granted).issued is None
    ranges.issue("D", 60, granted)
    ranges.issue("D", 55, granted)          # backwards: the mark is a high water
    assert ranges.grant("D", granted).issued == 60


# ---- a grant is a rule, at every door -------------------------------------


def test_an_out_of_range_id_is_refused_by_the_composer(granted, g):
    """`dg add`'s flag path and `POST /api/add`. Checked here *and* in `vet`,
    unlike the area rule, because a grant has no invariant behind it: a route
    that skips the check writes the id rather than having a batch refused."""
    with pytest.raises(pending.ApplyError, match="outside this clone's D range"):
        pending.compose_add(g, vid="D07", title="x", area="Alpha")


def test_an_out_of_range_id_is_refused_by_vet(granted, g):
    """The raw-op path — the browser tray and the editor buffer."""
    with pytest.raises(pending.ApplyError, match="outside this clone's D range"):
        pending.vet(g, {"op": "add_vertex", "id": "D07", "title": "x",
                        "area": "Alpha", "status": "OPEN"})


def test_the_task_store_is_refused_by_the_same_rule(granted):
    """One grant, both stores. A worker with a `D` range and no `T` range
    collides on every task it adds while its decisions are safe."""
    tg = TaskGraph.load(project.find().tasks)
    with pytest.raises(pending.ApplyError, match="outside this clone's T range"):
        task_pending.compose_add(tg, None, tid="T07", title="x", area="Alpha")
    with pytest.raises(pending.ApplyError, match="outside this clone's T range"):
        task_pending.vet(tg, {"op": "add_task", "id": "T07", "title": "x",
                              "area": "Alpha"})


def test_an_id_inside_the_range_passes_every_door(granted, g):
    ops = pending.compose_add(g, vid="D60", title="x", area="Alpha")
    assert ops[0]["id"] == "D60"
    pending.vet(g, ops[0])                    # raises nothing


# ---- the command ----------------------------------------------------------


def test_the_command_grants_both_stores_from_one_flag(run, store):
    res = run("range", "--set", "50-99")
    assert res.exit_code == 0 and "D50-D99 and T50-T99" in res.output
    raw = json.loads((store / project.RANGE_NAME).read_text())
    assert raw == {"D": {"range": [50, 99]}, "T": {"range": [50, 99]}}


def test_the_command_says_plainly_when_there_is_no_grant(run):
    out = run("range").output
    assert "no grant" in out and "whole sequence" in out


def test_the_command_reports_the_mark_and_the_next_id(run, store):
    run("range", "--set", "50-99")
    ranges.issue("D", 57, store)
    out = run("range").output
    assert "50-99" in out and "D57" in out and "D58" in out


def test_clearing_removes_the_file(run, store):
    run("range", "--set", "50-99")
    assert run("range", "--clear").exit_code == 0
    assert not (store / project.RANGE_NAME).exists()


@pytest.mark.parametrize("bad", ["50", "99-50", "0-9", "fifty-99", "50-"])
def test_a_range_that_is_not_one_is_refused_by_the_act_that_set_it(run, bad):
    """Rather than by the first `dg add` that meets it, several commands
    later, with the reason no longer to hand."""
    res = run("range", "--set", bad)
    assert res.exit_code == 2
    assert not (project.find().range.exists())


def test_set_and_clear_together_are_refused(run):
    assert run("range", "--set", "50-99", "--clear").exit_code == 2


def test_the_missing_id_refusal_offers_the_granted_one(run, store):
    """`dg add` prefills an id only in the editor, so without this a writer in
    a granted clone would have to look the range up and count — and the id it
    then guesses is refused by a rule it cannot see from where it stands."""
    run("range", "--set", "50-99")
    out = run("add", "--title", "x", "--area", "Alpha").output
    assert "next unused: D50" in out


def test_an_exhausted_grant_refuses_the_command_rather_than_tracebacking(run,
                                                                        store):
    run("range", "--set", "50-51")
    ranges.issue("D", 51, store)
    res = run("add", "--title", "x", "--area", "Alpha", "--edit")
    assert res.exit_code == 1 and "used up" in res.output


def test_a_used_up_grant_says_so_where_the_id_would_have_been(run, store):
    """A hint that simply vanished would leave the writer with `missing --id`
    and no id, and no way to tell a clone that is out of ids from a message
    that never offered one."""
    run("range", "--set", "50-51")
    ranges.issue("D", 51, store)
    out = run("add", "--title", "x", "--area", "Alpha").output
    assert "missing option(s)" in out and "used up" in out


def test_the_generated_ignore_block_already_covers_the_grant(tmp_path):
    """`.dgraph-*` covers it, and the watermark *has* to be ignored — it is
    what survives the checkout that rewinds the store. Asserted rather than
    assumed, because `C-F13` was exactly this list being short of what the
    tool writes."""
    from fnmatch import fnmatch
    assert any(fnmatch(project.RANGE_NAME, pat) for pat in project.IGNORE)


def test_the_watermark_follows_the_tray_it_was_staged_into(granted, tmp_path):
    """`stage_all` takes a tray path, and a caller that names one outside the
    current project must not raise *this* clone's watermark — that is precisely
    the write the file exists to stop, made by the file's own bookkeeping."""
    other = tmp_path / "elsewhere"
    other.mkdir()
    pending.stage_all([{"op": "add_vertex", "id": "D60", "title": "x",
                        "area": "Alpha", "status": "OPEN"}],
                      other / project.PENDING_NAME)
    assert ranges.grant("D", granted).issued is None
