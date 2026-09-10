"""Six rules about what an act may write, and where it lands.

A re-affirmation is a fact about one answer: it lands on the answer it
re-read, wherever that answer now stands, and only the acts that make one —
`dg confirm`, and integration replaying a clone's — write one. A reopen that
arrives from a clone and sets aside an answer given here since the base is a
judgement to report. A reading needs a standing answer, on every door. The view
keeps an archived answer's re-affirmations. And a one-line field is one line,
whichever door the value came through.
"""

import copy
import json
import subprocess

import pytest
from typer.testing import CliRunner

from dgraph import brief, integrate, pending, project, server, task_pending
from dgraph.check import run as check_run
from dgraph.cli import app
from dgraph.md_import import import_markdown
from dgraph.model import CLAIM, Graph
from dgraph.render import write
from dgraph.tasks import TaskGraph
from conftest import FIXTURE, TASK_FIXTURE

runner = CliRunner()

ENTRY = {"date": "2026-09-10", "note": "it still holds"}

#: D02's answer replaced here: reopened, then decided again with another.
REDECIDE = [{"op": "reopen", "vertex": "D02", "why": "measured again"},
            {"op": "close", "vertex": "D02", "answer": "A new answer.",
             "falsifier": "p95 over 300 ms", "source": "bench",
             "date": "2026-09-09"}]

CLOSE_D05 = {"op": "close", "vertex": "D05", "answer": "Now decided.",
             "falsifier": "a counter-example", "source": "bench",
             "date": "2026-09-01"}


def replay(g, ops):
    out = copy.deepcopy(g)
    for op in ops:
        for one in pending.expand(out, op):
            pending._apply_one(out, one)
    return out


def reaffirmed(g, vid="D02", note="theirs re-read it"):
    out = copy.deepcopy(g)
    pending._reaffirm(out, vid, {"note": note, "date": "2026-09-10"})
    return out


def adopted(ours, rep):
    out = copy.deepcopy(ours)
    for op in rep.d_ops:
        pending._apply_one(out, op)
    return out


# ---- T149 · a re-affirmation lands on the answer it re-read (D124) --------

def test_the_seam_carries_the_answer_an_entry_re_read(g):
    theirs = reaffirmed(g)
    (op,) = [o for o in integrate.decisions(g, theirs).ops
             if o["op"] == "reaffirm"]
    e = g.active_edge("D02")
    assert op["claim"] == {k: getattr(e, k) for k in CLAIM
                           if getattr(e, k) is not None}


def test_a_clones_re_affirmation_of_an_answer_replaced_here_goes_on_that_answer(g):
    """`AE-F1`: the clone re-read D02's base answer; here it was replaced.
    The entry is filed with the answer it was about, and said — not a
    contest, and not on the answer standing now."""
    ours = replay(g, REDECIDE)
    rep = integrate.plan(ours, None, g, None, reaffirmed(g), None)
    out = adopted(ours, rep)
    assert not out.active_edge("D02").reaffirmed
    old = out.history("D02")[-1]
    assert old.answer == "Second answer."
    assert [r["note"] for r in old.reaffirmed] == ["theirs re-read it"]
    assert not [f for f in rep.contested if f.record == "D02"
                and "re-affirm" in f.message]
    assert any("D02" in line for line in rep.filed)


def test_a_re_affirmation_of_an_answer_reopened_here_goes_on_the_archive(g):
    ours = replay(g, REDECIDE[:1])
    rep = integrate.plan(ours, None, g, None, reaffirmed(g), None)
    assert rep.inapplicable == []
    out = adopted(ours, rep)
    assert [r["note"] for r in out.history("D02")[-1].reaffirmed] == [
        "theirs re-read it"]
    assert any("D02" in line for line in rep.filed)


def test_a_re_affirmation_on_the_answer_still_standing_is_said_nowhere(g):
    rep = integrate.plan(copy.deepcopy(g), None, g, None, reaffirmed(g), None)
    assert rep.filed == [] and rep.ok
    assert [r["note"] for r in adopted(g, rep).active_edge("D02").reaffirmed] == [
        "theirs re-read it"]


def test_a_re_affirmation_of_an_answer_held_nowhere_here_is_left_out(g):
    op = {"op": "reaffirm", "vertex": "D02", **ENTRY,
          "claim": {"answer": "Never held here.", "source": "elsewhere"}}
    with pytest.raises(pending.ApplyError, match="left out"):
        pending._apply_one(copy.deepcopy(g), op)


def test_a_new_answer_arrives_with_its_entries_as_ops_of_their_own(g):
    theirs = replay(g, [CLOSE_D05])
    pending._reaffirm(theirs, "D05", ENTRY)
    ops = integrate.decisions(g, theirs).ops
    kinds = [o["op"] for o in ops]
    close = ops[kinds.index("close")]
    assert not close.get("reaffirmed")
    assert kinds.index("reaffirm") > kinds.index("close")
    out = adopted(g, integrate.plan(copy.deepcopy(g), None, g, None, theirs, None))
    assert out.active_edge("D05").reaffirmed == [ENTRY]


# ---- T150 · a reopen that sets aside an answer given here is contested ----

def test_an_arriving_reopen_that_sets_aside_an_answer_given_here_is_contested(g):
    """`AE-F2`: D02 was decided again here since the base; the clone reopened
    D01, which D02 rests on. The status row reads DECIDED at both ends, and
    that is not what *decided here since the base* means."""
    theirs = replay(g, [{"op": "reopen", "vertex": "D01", "why": "licence"}])
    rep = integrate.plan(replay(g, REDECIDE), None, g, None, theirs, None)
    covered = {r for f in rep.contested for r in (f.record, *f.also)}
    assert "D02" in covered
    assert not rep.ok


def test_an_arriving_reopen_over_answers_nobody_moved_here_is_not_contested(g):
    theirs = replay(g, [{"op": "reopen", "vertex": "D01", "why": "licence"}])
    rep = integrate.plan(copy.deepcopy(g), None, g, None, theirs, None)
    assert rep.contested == []


# ---- T151 · a re-affirmation is written only by the acts that make one ----

@pytest.mark.parametrize("op", [
    {**CLOSE_D05, "reaffirmed": [{"date": "1999-01-01",
                                  "note": "before it was decided"}]},
    {"op": "reject", "vertex": "D02", "answer": "Declined.", "source": "s",
     "from_source": "them",
     "reaffirmed": [{"date": "2026-09-10", "note": "never ours"}]},
], ids=["close", "reject"])
def test_an_answer_carrying_re_affirmations_is_refused_as_data_and_at_apply(g, op):
    """`AE-F3`: a new answer starts with none (`PAYLOAD`)."""
    with pytest.raises(pending.ApplyError, match="re-affirm"):
        pending.vet(g, copy.deepcopy(op))
    with pytest.raises(pending.ApplyError, match="re-affirm"):
        pending.apply_all(g, [copy.deepcopy(op)])


def test_a_reaffirm_arriving_as_data_is_refused_unless_integration_derived_it(g):
    """`AE-F4`: D02 was never under review."""
    op = {"op": "reaffirm", "vertex": "D02", "note": "never under review",
          "date": "2026-09-10"}
    with pytest.raises(pending.ApplyError, match="dg confirm"):
        pending.vet(g, dict(op))
    with pytest.raises(pending.ApplyError, match="dg confirm"):
        server.stage(g, dict(op))
    assert pending.load() == []
    pending.vet(g, dict(op), integrated=True)
    pending.vet_all(g, [dict(op)], integrated=True)


def test_a_re_affirmation_dated_with_something_that_is_not_a_date_is_refused(g):
    op = {"op": "reaffirm", "vertex": "D02", "note": "fine",
          "date": "last tuesday"}
    with pytest.raises(pending.ApplyError, match="YYYY-MM-DD"):
        pending._apply_one(copy.deepcopy(g), op)


def test_a_clones_malformed_entry_on_a_new_answer_is_inapplicable_not_blocking(g):
    theirs = replay(g, [CLOSE_D05])
    theirs.active_edge("D05").reaffirmed = [
        {"date": "2026-09-10", "note": "one\ntwo"}]
    rep = integrate.plan(copy.deepcopy(g), None, g, None, theirs, None)
    assert rep.blocking == []
    (f,) = [x for x in rep.inapplicable if x.record == "D05"]
    assert "one line" in f.message
    assert any(o["op"] == "close" and o["vertex"] == "D05" for o in rep.d_ops)


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_adopting_a_contribution_takes_its_re_affirmation(tmp_path, monkeypatch):
    """The one door `vet` lets a `reaffirm` through: `dg incoming --adopt`."""
    monkeypatch.setenv("COLUMNS", "200")
    root = tmp_path
    _git(root, "init", "-q", "-b", "main", ".")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")

    def run(*args):
        return runner.invoke(app, ["--project", str(root), *args])

    assert run("init").exit_code == 0
    run("add", "--id", "D01", "--title", "Which store?", "--area", "Search")
    run("add", "--id", "D02", "--title", "Which index?", "--area", "Search",
        "--after", "D01")
    run("apply")
    run("decide", "D01", "--no-edit", "-a", "sqlite", "-s", "s", "-f", "f")
    run("apply")
    run("decide", "D02", "--no-edit", "-a", "fts5", "-s", "s", "-f", "f")
    run("apply")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "worker")
    run("reopen", "D01", "--why", "licence", "--yes")
    run("apply")
    run("decide", "D01", "--no-edit", "-a", "sqlite, read", "-s", "s", "-f", "f")
    run("apply")
    assert run("confirm", "D02", "--note", "the index does not care").exit_code == 0
    run("apply")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "worker")
    _git(root, "checkout", "-q", "main")
    assert run("integrate", "worker").exit_code == 0
    res = run("incoming", "--adopt")
    assert res.exit_code == 0, res.output
    assert run("apply").exit_code == 0
    e = Graph.load(root / "decisions.json").active_edge("D02")
    assert [r["note"] for r in e.reaffirmed] == ["the index does not care"]


# ---- T152 · a reading needs a standing answer, on every door -----------------

@pytest.fixture
def both(store, task_store, g):
    return task_store


def test_a_reading_against_a_question_with_no_answer_is_refused_at_stage(both):
    tg = TaskGraph.load(both / "tasks.json")
    op = {"op": "read_evidence", "task": "T01", "against": "D05",
          "note": "looked", "date": "2026-09-10"}
    with pytest.raises(pending.ApplyError, match="an answer has to be standing"):
        task_pending.vet(tg, dict(op))
    with pytest.raises(pending.ApplyError, match="an answer has to be standing"):
        server.stage_tasks(tg, [dict(op)])
    assert pending.load(task_pending.path()) == []


def test_a_close_dropped_from_the_tray_leaves_its_reading_refused(both, monkeypatch):
    """`AE-F5`: the close and its reading are two ops in two trays."""
    monkeypatch.setenv("COLUMNS", "200")
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T01"].evidence_for = "D05"
    tg.save(both / "tasks.json")

    def run(*args):
        return runner.invoke(app, ["--project", str(both), *args])

    r = run("decide", "D05", "--no-edit", "-a", "yes", "-s", "discussion",
            "-f", "a counter-example")
    assert r.exit_code == 0 and "read at decide" in r.output, r.output
    (close,) = pending.load()
    assert run("drop", close["ref"]).exit_code == 0
    r = run("check", "--staged")
    assert "tray_applies" in r.output and "an answer has to be standing" in r.output
    assert brief.data()["staged_refused"] >= 1
    r = run("apply")
    assert "an answer has to be standing" in r.output
    assert TaskGraph.load(both / "tasks.json").tasks["T01"].readings == []


# ---- T153 · archived re-affirmations survive the view ----------------------

def test_an_archived_answers_re_affirmations_round_trip_through_the_view(tmp_path):
    g = Graph.from_dict(FIXTURE)
    pending._reaffirm(g, "D02", ENTRY)
    g = replay(g, REDECIDE)
    path = tmp_path / "decision-graph.md"
    write(g, path)
    back = import_markdown(path)
    assert back.history("D02")[-1].reaffirmed == [ENTRY]
    assert not back.active_edge("D02").reaffirmed


# ---- T154 · a one-line field is one line at every door ----------------------

@pytest.mark.parametrize("op", [
    {"op": "add_vertex", "id": "D07", "title": "two\nlines", "area": "Beta",
     "status": "OPEN"},
    {"op": "set_fields", "vertex": "D05", "title": "re\ntitled"},
    {"op": "set_fields", "vertex": "D05", "area": "Be\nta"},
    {**CLOSE_D05, "source": "one\ntwo"},
], ids=["add-title", "amend-title", "amend-area", "close-source"])
def test_a_newline_in_a_one_line_decision_field_is_refused_at_stage(store, op):
    with pytest.raises(pending.ApplyError, match="one line"):
        pending.stage_all([op])
    assert pending.load() == []


@pytest.mark.parametrize("op", [
    {"op": "add_task", "id": "T09", "title": "two\nlines", "area": "Beta",
     "status": "TODO"},
    {"op": "set_fields", "task": "T02", "title": "a\nb"},
    {"op": "set_fields", "task": "T02", "area": "Al\npha"},
], ids=["add-title", "amend-title", "amend-area"])
def test_a_newline_in_a_one_line_task_field_is_refused_at_stage(task_store, op):
    with pytest.raises(pending.ApplyError, match="one line"):
        pending.stage_all([op], task_pending.path())
    assert pending.load(task_pending.path()) == []


def test_the_flag_doors_refuse_a_title_of_two_lines(both):
    for args in (["add", "--id", "D07", "--title", "two\nlines", "--area", "Beta"],
                 ["amend", "D05", "--title", "re\ntitled"],
                 ["task", "add", "--id", "T09", "--title", "two\nlines",
                  "--area", "Beta"],
                 ["task", "amend", "T02", "--title", "a\nb"]):
        r = runner.invoke(app, ["--project", str(both), *args])
        assert r.exit_code != 0 and "one line" in r.output, (args, r.output)
    assert pending.load() == [] and pending.load(task_pending.path()) == []


def test_check_warns_of_a_one_line_field_already_stored(tmp_path, monkeypatch):
    d = copy.deepcopy(FIXTURE)
    d["vertices"][4]["title"] = "Still\nopen"
    t = copy.deepcopy(TASK_FIXTURE)
    t["tasks"][1]["title"] = "Second,\nnow startable"
    (tmp_path / "decisions.json").write_text(json.dumps(d))
    (tmp_path / "tasks.json").write_text(json.dumps(t))
    monkeypatch.setattr(project, "_override", tmp_path)
    hits = [v for v in check_run() if v.check == "one_line_field"]
    assert {v.message.split()[0].rstrip("'s") for v in hits} >= {"D05", "T02"}
    assert all(not v.blocking for v in hits)
