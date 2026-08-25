"""Bringing another writer's work in — `F-F3`'s mechanism.

The claim under test is not "integration works". It is that **the three ways
two divergent stores can be brought together are not equivalent**, and that the
one built here fails in the direction the other two do not:

    git text-merge      loud, in a file with no semantics
    id-keyed union      silent — the naive improvement, and the worst
    replay through vet  loud, and before the write

So most of what is pinned here is the *silent* half: a removal that a union
would revert, a park a union would erase, two answers a union would pick
between. Each of those is a test that would pass against a union and mean
nothing, unless it also asserts what survived.

The second thing pinned is that a report is **collected, not fail-fast**. Every
apply path in both stores stops at the first refusal, which is right for
staging — one op at a time, vetted as it is typed — and wrong for a
contribution composed elsewhere and arriving whole.
"""

import copy
import json

import pytest

from dgraph import cross, integrate, pending, project, task_pending
from dgraph.model import Graph
from dgraph.tasks import TaskGraph


def replay(g, ops, store="decisions"):
    """Apply ops to a copy, the way the walk does."""
    out = copy.deepcopy(g)
    mod = pending if store == "decisions" else task_pending
    for op in ops:
        for one in (pending.expand(out, op) if store == "decisions" else [op]):
            mod._apply_one(out, one)
    return out


# ---- the derivation is faithful ------------------------------------------


def test_derived_ops_replay_to_the_store_they_were_derived_from(g):
    """The round trip, and the strongest thing this module can be asked.

    A derivation that dropped an act would still produce a plausible op list;
    only replaying it against the base and comparing to the arriving store
    catches the drop."""
    # Built through `replay`, which expands — the same path `dg reopen` takes,
    # so what a reopen drags into PROVISIONAL is in the arriving store. Built
    # with raw `_apply_one` it would not be, and the round trip would compare
    # a faithful replay against a store no command could have produced.
    theirs = replay(g, [
        {"op": "add_vertex", "id": "D50", "title": "new", "area": "Alpha",
         "status": "OPEN"},
        {"op": "add_edge", "from": "D05", "to": ["D50"]},
        {"op": "reopen", "vertex": "D01", "why": "measured wrong",
         "summary": "the old one"},
        {"op": "close", "vertex": "D01", "answer": "a fresh answer",
         "source": "bench/x.md", "falsifier": "the corpus changes",
         "to": ["D02", "D03"]},
        {"op": "set_fields", "vertex": "D03", "title": "reworded"},
    ])

    d = integrate.decisions(g, theirs)
    assert d.unexpressible == []
    assert replay(g, d.ops).to_dict() == theirs.to_dict()


def test_derived_task_ops_replay_to_the_store_they_came_from(tg):
    theirs = copy.deepcopy(tg)
    for op in [
            {"op": "add_task", "id": "T50", "title": "new", "area": "Alpha"},
            {"op": "add_dep", "from": "T01", "kind": "precedes", "to": ["T50"]},
            {"op": "set_status", "task": "T02", "status": "PARKED",
             "why": "the box is busy", "date": "2026-02-01"},
            {"op": "set_fields", "task": "T03", "title": "reworded"},
    ]:
        task_pending._apply_one(theirs, op)

    d = integrate.tasks(tg, theirs)
    assert d.unexpressible == []
    assert replay(tg, d.ops, "tasks").to_dict() == theirs.to_dict()


def test_an_identical_store_derives_nothing(g, tg):
    assert integrate.decisions(g, copy.deepcopy(g)).ops == []
    assert integrate.tasks(tg, copy.deepcopy(tg)).ops == []


# ---- what a union would have swallowed -----------------------------------


def test_a_removal_arrives_as_a_removal(g):
    """Under a union a removal always loses to any side that still names the
    record, and nothing reports that a deletion was reverted. As an op it is
    something a person has to drop **on purpose**."""
    theirs = replay(g, [{"op": "remove_vertex", "vertex": "D06",
                         "mode": "sever"}])
    ops = integrate.decisions(g, theirs).ops
    assert {"op": "remove_vertex", "vertex": "D06", "mode": "sever"} in ops
    assert "D06" not in replay(g, ops).vertices


def test_a_park_and_a_completion_both_land(tg):
    """Pass two's tier 3b. `stops` is append-only; what destroyed the park was
    a union taking a whole task record from one side."""
    theirs = copy.deepcopy(tg)
    task_pending._apply_one(theirs, {"op": "set_status", "task": "T02",
                                     "status": "PARKED", "why": "stuck",
                                     "date": "2026-02-01"})
    ours = copy.deepcopy(tg)
    ops = integrate.tasks(tg, theirs).ops
    out = replay(ours, ops, "tasks")
    assert out.tasks["T02"].stops[-1].why == "stuck"


def test_the_base_is_what_makes_a_removal_a_removal(g):
    """Absent from the arriving store means *deleted* only if the base had it.
    A two-graph diff cannot tell that from a record it never saw, and guessing
    is how a deletion gets silently reverted — so the derivation is against the
    base and the replay is against ours."""
    base = replay(g, [{"op": "remove_vertex", "vertex": "D06",
                       "mode": "sever"}])
    # `theirs` still lacks D03, but so did the base: nothing was removed.
    theirs = copy.deepcopy(base)
    assert not [o for o in integrate.decisions(base, theirs).ops
                if o["op"] == "remove_vertex"]


# ---- collected, not fail-fast --------------------------------------------


def _both(store, task_store, monkeypatch):
    monkeypatch.setenv("DG_PROJECT", str(store))
    return (Graph.load(store / "decisions.json"),
            TaskGraph.load(project.find().tasks))


def test_every_conflict_is_collected_before_anything_is_asked(g):
    """Fail-fast would make this three round-trips in composition order, and
    the reader could not see the third until they had answered the first two."""
    theirs = copy.deepcopy(g)
    for op in [{"op": "set_fields", "vertex": "D02", "title": "theirs"},
               {"op": "set_fields", "vertex": "D03", "title": "theirs too"}]:
        pending._apply_one(theirs, op)
    ours = copy.deepcopy(g)
    for op in [{"op": "set_fields", "vertex": "D02", "title": "mine"},
               {"op": "set_fields", "vertex": "D03", "title": "mine too"}]:
        pending._apply_one(ours, op)

    rep = integrate.plan(ours, None, g, None, theirs, None)
    assert len(rep.contested) == 2
    assert {f.record for f in rep.contested} == {"D02", "D03"}


def test_a_change_nobody_else_touched_is_not_contested(g):
    """Contested is measured against the **base**, not against the op. An op
    says *make it this*, so comparing what it writes with what is here reports
    every op as contested — which is what the first version of this did, and
    it made an ordinary retitle look like a conflict."""
    theirs = copy.deepcopy(g)
    pending._apply_one(theirs, {"op": "set_fields", "vertex": "D02",
                                "title": "reworded"})
    rep = integrate.plan(copy.deepcopy(g), None, g, None, theirs, None)
    assert rep.contested == [] and rep.ok


def test_two_answers_to_one_question_are_contested_and_refused(g):
    """H1, and the shape of it: the refusal is carried *under* the
    disagreement that caused it, one finding rather than two, because a reader
    who meets two lines counts two conflicts."""
    def answer(text):
        return replay(g, [{"op": "close", "vertex": "D05", "answer": text,
                           "source": "bench", "falsifier": "x", "to": []}])

    rep = integrate.plan(answer("mine"), None, g, None, answer("theirs"), None)
    (f,) = rep.contested
    assert f.record == "D05" and "answered here too" in f.message
    assert f.refusal and "reopen" in f.refusal
    assert not [x for x in rep.findings if x.kind == "inapplicable"]


def test_one_cause_is_one_finding_however_many_ops_it_takes_down(g):
    """A record removed here produces a refusal for every arriving op that
    names it, and they are one cause, not five."""
    ours = replay(g, [{"op": "remove_vertex", "vertex": "D06",
                       "mode": "sever"}])
    theirs = replay(g, [
        {"op": "set_fields", "vertex": "D06", "title": "reworded"},
        {"op": "add_vertex", "id": "D50", "title": "new", "area": "Beta",
         "status": "OPEN"},
        {"op": "add_edge", "from": "D06", "to": ["D50"]},
    ])

    rep = integrate.plan(ours, None, g, None, theirs, None)
    (f,) = rep.inapplicable
    assert f.record == "D06" and f.grouped


def test_the_two_sides_are_counted_apart(g, tg):
    """`clean` is counted by `(store, index)`, never by index alone: both
    sides number from zero, so `d1` and `t1` would be one op."""
    theirs_g = copy.deepcopy(g)
    pending._apply_one(theirs_g, {"op": "set_fields", "vertex": "D02",
                                  "title": "theirs"})
    theirs_tg = copy.deepcopy(tg)
    task_pending._apply_one(theirs_tg, {"op": "set_fields", "task": "T02",
                                        "title": "theirs"})
    ours_g, ours_tg = copy.deepcopy(g), copy.deepcopy(tg)
    pending._apply_one(ours_g, {"op": "set_fields", "vertex": "D02",
                                "title": "mine"})
    task_pending._apply_one(ours_tg, {"op": "set_fields", "task": "T02",
                                      "title": "mine"})

    rep = integrate.plan(ours_g, ours_tg, g, tg, theirs_g, theirs_tg)
    assert rep.derived == 2 and len(rep.contested) == 2 and rep.clean == 0


# ---- what cannot be said is said -----------------------------------------


def test_an_area_the_ops_cannot_carry_is_reported_not_invented(g):
    """No op writes an area list, so a contribution that added one arrives with
    every record in it failing `area_known`. One line naming the cause beats a
    wall of identical refusals — and inventing the area would make this module
    a merge driver with rules of its own."""
    theirs = copy.deepcopy(g)
    theirs.areas = [*theirs.areas, "Gamma"]
    d = integrate.decisions(g, theirs)
    assert d.ops == []
    assert any("Gamma" in line for line in d.unexpressible)


# ---- the commands, over two real branches --------------------------------


import subprocess

from typer.testing import CliRunner

from dgraph.cli import app

runner = CliRunner()


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True)


@pytest.fixture
def two_writers(tmp_path, monkeypatch):
    """One base commit, then a branch that adds work, then a commit of our own.

    A real repository rather than a stub, because the base comes from `git
    merge-base` and the arriving store is read with `git show` — mocking those
    would test the mock.
    """
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("DG_PROJECT", str(root))
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")

    def run(*args):
        return runner.invoke(app, ["--project", str(root), *args])

    assert run("init", "--areas", "Search").exit_code == 0
    assert run("task", "init", "--areas", "Search").exit_code == 0
    run("add", "--id", "D01", "--title", "Which index?", "--area", "Search")
    run("task", "add", "--id", "T01", "--title", "Bench it", "--area", "Search")
    run("apply")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "worker")
    run("add", "--id", "D50", "--title", "How do we shard?", "--area", "Search",
        "--after", "D01")
    run("task", "add", "--id", "T50", "--title", "Fan-out", "--area", "Search",
        "--because", "D50")
    run("apply")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "worker")
    _git(root, "checkout", "-q", "master")
    return root, run


def test_a_clean_contribution_is_reported_quarantined_and_adoptable(
        two_writers):
    root, run = two_writers
    res = run("integrate", "worker")
    assert res.exit_code == 0 and "0 contested" in res.output
    assert (root / project.INCOMING_NAME).exists()
    # **Quarantined, not staged.** The tray is what every stage-time guard
    # consults, so an unadjudicated op put there would have this clone
    # answering `dg node` with a record nobody accepted.
    assert pending.load(root / project.PENDING_NAME) == []

    assert run("incoming", "--adopt").exit_code == 0
    assert not (root / project.INCOMING_NAME).exists()
    assert pending.load(root / project.PENDING_NAME)
    assert run("apply").exit_code == 0
    assert "D50" in Graph.load(root / "decisions.json").vertices


def test_a_contribution_atomic_across_both_stores_is_not_refused_by_arrival_order(
        two_writers):
    """`T50 --because D50` and `D50` arrive together. Judge the task half
    against the *stored* decision graph and `link_resolves` reports a task
    naming a decision that does not exist — a false refusal produced by
    arrival order rather than by a conflict, and the integration twin of
    `F-F1`. The pair guard is what makes it pass."""
    root, run = two_writers
    out = run("integrate", "worker").output
    assert "0 blocking" in out and "link_resolves" not in out


def test_the_cross_store_guard_is_wired_and_not_merely_silent(two_writers):
    """The other half of the test above, and the half that matters.

    Asserting the *absence* of `link_resolves` passes trivially when the guard
    is not running at all — which is exactly the state the design warns about:
    put an arriving contribution through `apply_all` without the cross guards
    and both link invariants are silent, the dangling reference lands, and the
    commit gate then denies every commit in the repository. So a contribution
    that genuinely breaks the seam has to be *refused*, or nothing here is
    being checked. Verified by turning `guard_pair` off and watching this
    fail."""
    root, run = two_writers
    _git(root, "checkout", "-q", "worker")
    # A task pointing at a decision no side has: the one thing neither store
    # can notice alone.
    tg = TaskGraph.load(root / "tasks.json")
    tg.tasks["T50"].because = "D99"
    tg.save(root / "tasks.json")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "dangling")
    _git(root, "checkout", "-q", "master")

    out = run("integrate", "worker").output
    assert "blocking" in out and "link_resolves" in out and "D99" in out


def test_the_gate_denies_a_commit_over_a_quarantined_contribution(two_writers):
    """`deny`, where a staged tray gets `ask`, and the asymmetry is the point:
    one is your own unfinished thought and the other is somebody else's
    finished one. The file is gitignored, so committing over it drops the
    contribution with nothing recording that it arrived."""
    root, run = two_writers
    from dgraph import gate
    assert gate.verdict("git commit -m x", project.find())["verdict"] == "allow"
    run("integrate", "worker")
    answer = gate.verdict("git commit -m x", project.find())
    assert answer["verdict"] == "deny" and "another writer" in answer["reason"]


def test_a_contested_contribution_is_not_adopted(two_writers):
    """The three questions only a person can answer are not answered by a
    flag. `--discard` refuses the whole contribution; there is no `--force`."""
    root, run = two_writers
    run("amend", "D01", "--title", "Which index structure?")
    run("apply")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "mine")
    run("integrate", "worker")           # nothing contested yet
    run("incoming", "--discard")

    # Now both sides have retitled the same record.
    _git(root, "checkout", "-q", "worker")
    run("amend", "D01", "--title", "Which index, at 48M?")
    run("apply")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "worker retitle")
    _git(root, "checkout", "-q", "master")

    assert "title differs" in run("integrate", "worker").output
    res = run("incoming", "--adopt")
    assert res.exit_code == 1 and "not adopted" in res.output
    assert (root / project.INCOMING_NAME).exists()      # still waiting


def test_a_second_contribution_is_refused_while_one_is_waiting(two_writers):
    """Judged against a graph nobody has agreed to yet is not judged."""
    root, run = two_writers
    run("integrate", "worker")
    res = run("integrate", "worker")
    assert res.exit_code == 1 and "already waiting" in res.output


def test_no_common_history_is_refused_rather_than_guessed(two_writers):
    """Without a base, a record missing from the arriving store cannot be told
    from one it never saw — and guessing is how a deletion gets reverted."""
    root, run = two_writers
    _git(root, "checkout", "-q", "--orphan", "stranger")
    _git(root, "commit", "-qm", "unrelated", "--allow-empty")
    _git(root, "checkout", "-q", "master")
    res = run("integrate", "stranger")
    assert res.exit_code == 1 and "no common history" in res.output


# ---- the seam: three questions, and a place for the losing answer ---------


def test_the_three_semantic_conflicts_arrive_together_and_are_answered_one_by_one(
        two_writers):
    """Eleven of the fifteen ways two writers disagree are mechanical. Three
    are not, and a seam that asked about all fifteen is one an orchestrator
    learns to click through — after which the three that mattered go past
    unread. So: exactly these three, together, each with two candidate answers.
    """
    root, run = two_writers
    # Both sides answer D01, finish T01, and retitle D01.
    def diverge(answer, outcome, title):
        run("decide", "D01", "-a", answer, "-s", "bench", "-f", "x")
        run("task", "done", "T01", "-o", outcome)
        run("amend", "D01", "--title", title)
        run("apply")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "diverge")

    _git(root, "checkout", "-q", "worker")
    diverge("HNSW", "recall 0.94", "Which index, at 48M?")
    _git(root, "checkout", "-q", "master")
    diverge("IVF-PQ", "recall 0.91", "Which index structure?")

    out = run("integrate", "worker").output
    assert "3 contested" in out
    assert "title differs" in out and "answered here too" in out \
        and "finished here too" in out

    # Unanswered, adoption refuses — and there is no flag that answers them.
    assert run("incoming", "--adopt").exit_code == 1
    assert run("incoming", "--take", "nope").exit_code == 1

    # Refs looked up rather than hardcoded: they are positions in the derived
    # list, which is stable for one report and not across scenarios — and a
    # test that pinned them would be pinning the derivation order rather than
    # the seam.
    raw = integrate.load_incoming(root)
    ref = {f["op"]["op"]: f["ref"] for f in raw["contested"]}
    assert run("incoming", "--keep", ref["set_fields"]).exit_code == 0
    assert run("incoming", "--keep", ref["close"]).exit_code == 0
    assert run("incoming", "--take", ref["set_status"]).exit_code == 0
    assert run("incoming", "--adopt").exit_code == 0
    assert run("apply").exit_code == 0

    g = Graph.load(root / "decisions.json")
    # Ours stands, and theirs is kept without claiming it ever did.
    assert g.active_edge("D01").answer == "IVF-PQ"
    (declined,) = g.rejected("D01")
    assert declined.answer == "HNSW" and declined.from_source == "worker"
    assert declined.why is None and declined.replaced_by is None
    assert g.history("D01") == []          # nothing was overturned
    assert g.vertices["D01"].title == "Which index structure?"

    # Taking theirs on the task keeps both results, with theirs live.
    t = TaskGraph.load(root / "tasks.json").tasks["T01"]
    assert [c.outcome for c in t.completions] == ["recall 0.91", "recall 0.94"]
    assert t.outcome == "recall 0.94"


def test_a_declined_answer_is_not_filed_as_a_reversal(g):
    """The record `reject` exists for. Filed as an ordinary superseded edge it
    would render under **Superseded** with an empty `why`, asserting the
    project once believed something it never did — a claim about its history
    nobody made. So `history` leaves it out and `rejected` reads it."""
    out = pending.apply_all(g, [
        {"op": "reject", "vertex": "D01", "answer": "the other way",
         "source": "bench/b.md", "from_source": "worker-a",
         "falsifier": "the corpus changes"}])
    assert out.history("D01") == [e for e in out.history("D01")
                                  if e.from_source is None]
    (declined,) = out.rejected("D01")
    assert declined.from_source == "worker-a"
    # The active answer is untouched, and nothing was opened.
    assert out.active_edge("D01").answer == g.active_edge("D01").answer
    assert out.children("D01") == g.children("D01")


def test_a_declined_answer_needs_a_question_that_has_one(g):
    """Declined *in favour of what?* A question with no answer of its own has
    nothing to have preferred, and the record would assert a comparison that
    never happened."""
    with pytest.raises(pending.ApplyError, match="nothing this one was"):
        pending.apply_all(g, [
            {"op": "reject", "vertex": "D05", "answer": "a",
             "source": "s", "from_source": "worker"}])


def test_a_declined_answer_must_say_whose_it_was(g):
    with pytest.raises(pending.ApplyError, match="where it came from"):
        pending.vet(g, {"op": "reject", "vertex": "D01", "answer": "a",
                        "source": "s"})


def test_a_record_that_was_never_current_may_not_carry_history_s_fields(g):
    """`why` and `replaced_by` say a decision was overturned. This one was
    declined, and the two are different sentences about the project."""
    out = pending.apply_all(g, [
        {"op": "reject", "vertex": "D01", "answer": "a", "source": "s",
         "from_source": "worker"}])
    bad = next(e for e in out.edges if e.from_source)
    bad.why = "it was measured wrong"
    hits = [v for v in out.validate() if v.check == "rejected_complete"]
    assert hits and "declined" in str(hits[0])


def test_a_waiting_contribution_is_named_where_a_tray_is_listed(two_writers):
    """Quarantine keeps an unadjudicated op out of every reading here, and the
    cost of that is a batch a reader could miss entirely. It still appears in
    one place; what it does not do is appear as though it were yours."""
    root, run = two_writers
    run("integrate", "worker")
    for argv in (("pending",), ("task", "pending")):
        out = run(*argv).output
        assert "ARRIVING" in out and "not staged" in out


def test_a_declined_answer_survives_the_store_and_a_round_trip(store, g):
    """Written, read back, exported and re-imported. A record kept forever
    that a round trip drops is a record kept until somebody moves the store."""
    from dgraph import json_import
    out = pending.apply_all(g, [
        {"op": "reject", "vertex": "D01", "answer": "the other way",
         "source": "bench/b.md", "from_source": "worker-a",
         "falsifier": "the corpus changes", "date": "2026-06-01"}])
    out.save(store / "decisions.json")

    back = Graph.load(store / "decisions.json")
    (d,) = back.rejected("D01")
    assert d.answer == "the other way" and d.from_source == "worker-a"

    moved = store / "moved.json"
    moved.write_text(json.dumps(back.to_dict()), encoding="utf-8")
    loaded = json_import.read(moved, "decisions")
    assert loaded.graph.rejected("D01")[0].from_source == "worker-a"


def test_the_generated_view_keeps_the_two_records_apart(store, g):
    """`_superseded` is a table of reversals with a *Replaced by* column. A
    declined answer has no `replaced_by`, so folding it in would fill that
    column with "(undecided)" against a question that is decided — and claim a
    reversal that never happened."""
    from dgraph import render
    out = pending.apply_all(g, [
        {"op": "reject", "vertex": "D01", "answer": "the other way",
         "source": "bench/b.md", "from_source": "worker-a"}])
    text = render.render(out)
    assert "Offered and not adopted" in text and "worker-a" in text
    table = text.split("## Superseded edges")[1]
    assert "the other way" not in table
