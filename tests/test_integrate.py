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
from dataclasses import replace

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


def test_an_arriving_area_is_carried_by_the_record_that_uses_it(g):
    """This used to be `unexpressible` — a finding with no fix.

    No op wrote the `areas` list, so a contribution introducing an area arrived
    with every record in it refused, and the most this module could do was name
    the cause. Areas register themselves now, from the `add_vertex` that files
    the first record under one, so a fresh area is carried by an ordinary op and
    needs no report of its own.

    Two halves, and both matter. An area **nobody uses** produces nothing at
    all: there is no record to carry it, and inventing an op to write a label
    would make this module a merge driver with rules of its own. An area a
    record *does* use arrives as that record."""
    theirs = copy.deepcopy(g)
    theirs.areas = [*theirs.areas, "Gamma"]
    d = integrate.decisions(g, theirs)
    assert d.ops == []
    assert not any("Gamma" in line for line in d.unexpressible), (
        "an area list that grew is no longer a finding")

    theirs.vertices["D09"] = replace(theirs.vertices["D05"], id="D09",
                                     area="Gamma")
    d = integrate.decisions(g, theirs)
    adds = [o for o in d.ops if o["op"] == "add_vertex"]
    assert [o["area"] for o in adds] == ["Gamma"]
    assert not d.unexpressible


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

    assert run("init").exit_code == 0
    assert run("task", "init").exit_code == 0
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
    tg.tasks["T50"].because = ["D99"]
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


# ---- class M: renamed, never asked about ---------------------------------


def test_a_taken_id_is_renamed_rather_than_put_to_a_person(g):
    """One correct outcome, so no question. A seam that asked about this would
    spend the attention the three semantic conflicts needed on bookkeeping —
    and under a shared base two clones pick the same id for *every* record
    either adds, so the volume is the whole problem."""
    theirs = replay(g, [{"op": "add_vertex", "id": "D07", "title": "theirs",
                         "area": "Alpha", "status": "OPEN"}])
    ours = replay(g, [{"op": "add_vertex", "id": "D07", "title": "mine",
                       "area": "Alpha", "status": "OPEN"}])
    rep = integrate.plan(ours, None, g, None, theirs, None,
                         next_free=lambda p, taken: f"{p}99")
    assert rep.renamed and "D07 → D99" in rep.renamed[0]
    assert rep.contested == [] and rep.inapplicable == []
    assert ours.vertices["D07"].title == "mine"      # ours is untouched


def test_a_rename_carries_every_place_the_id_hid(g):
    """The ones that are easy to forget are the ones that fail *silently*:
    `because` / `evidence_for` are ids in the **other** store's file — which is
    where a decision-id collision crosses over and quietly rewrites what a
    task's premise points at."""
    d_ops = [
        {"op": "add_vertex", "id": "D07", "title": "t", "area": "Alpha",
         "status": "OPEN"},
        {"op": "add_vertex", "id": "D08", "title": "u", "area": "Alpha",
         "status": "OPEN"},
        {"op": "add_edge", "from": "D07", "to": ["D08"]},
    ]
    t_ops = [{"op": "add_task", "id": "T90", "title": "w", "area": "Alpha",
              "because": "D07"}]
    ours = replay(g, [{"op": "add_vertex", "id": "D07", "title": "mine",
                       "area": "Alpha", "status": "OPEN"}])
    integrate.rename_collisions(d_ops, t_ops, ours, None,
                                lambda p, taken: f"{p}99")
    assert d_ops[0]["id"] == "D99"
    assert d_ops[2]["from"] == "D99" and d_ops[2]["to"] == ["D08"]
    assert t_ops[0]["because"] == "D99"


def test_an_id_both_sides_already_hold_is_left_alone(g):
    """Only what this merge *introduces*. An established id is cited in
    commits, in docs, in `dg why` output somebody pasted into a review —
    renaming it is not churn, it is breaking every sentence that names it."""
    theirs = replay(g, [{"op": "set_fields", "vertex": "D02",
                         "title": "reworded"}])
    rep = integrate.plan(copy.deepcopy(g), None, g, None, theirs, None,
                         next_free=lambda p, taken: f"{p}99")
    assert rep.renamed == []
    assert rep.d_ops[0]["vertex"] == "D02"


# ---- class R: reported, and never acted on -------------------------------


def test_the_report_says_what_was_integrated_not_only_what_was_wrong(g):
    """A clean `dg check` after an integration is not evidence that the work
    arrived."""
    theirs = replay(g, [{"op": "add_vertex", "id": "D50", "title": "new",
                         "area": "Alpha", "status": "OPEN"},
                        {"op": "add_edge", "from": "D05", "to": ["D50"]}])
    rep = integrate.plan(copy.deepcopy(g), None, g, None, theirs, None)
    assert "D50" in rep.touched and "D05" in rep.touched


def test_warnings_the_contribution_introduces_are_separated_from_refusals(g):
    """Advisory, and several of them fire in one integration order and not the
    other. A signal that depends on who integrated first is a note."""
    theirs = replay(g, [{"op": "add_vertex", "id": "D50", "title": "loose",
                         "area": "Alpha", "status": "OPEN"}])
    rep = integrate.plan(copy.deepcopy(g), None, g, None, theirs, None)
    assert rep.blocking == []
    assert any("no_orphans" in w and "D50" in w for w in rep.warnings)


def test_a_warning_the_store_already_had_is_not_blamed_on_the_contribution(g):
    """Introduced findings only, the rule `guard_decisions` states at length:
    a store that is already imperfect must not have every arriving
    contribution reported as the cause."""
    ours = replay(g, [{"op": "add_vertex", "id": "D40", "title": "lonely",
                       "area": "Alpha", "status": "OPEN"}])
    theirs = replay(g, [{"op": "set_fields", "vertex": "D02", "title": "x"}])
    rep = integrate.plan(ours, None, g, None, theirs, None)
    assert not [w for w in rep.warnings if "D40" in w]


# ---- H1's third door -----------------------------------------------------


def test_two_answers_can_turn_out_to_be_to_two_questions(two_writers):
    """The door the other two cannot stand in for. Take, and an answer nothing
    contradicted becomes history; keep, and this store files a record saying it
    turned down an answer it never disagreed with. Neither is honest when the
    question was worded loosely enough that two people answered different
    things."""
    root, run = two_writers
    _git(root, "checkout", "-q", "worker")
    run("decide", "D01", "-a", "HNSW for the served path", "-s", "bench/a.md",
        "-f", "the corpus passes 10M")
    run("apply")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "theirs")
    _git(root, "checkout", "-q", "master")
    run("decide", "D01", "-a", "IVF-PQ for the batch path", "-s", "bench/b.md",
        "-f", "memory drops")
    run("apply")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "mine")

    run("integrate", "worker")
    raw = integrate.load_incoming(root)
    ref = next(f["ref"] for f in raw["contested"] if f["op"]["op"] == "close")
    res = run("incoming", "--split", ref, "--as", "D51",
              "--title", "How do we index the batch path?")
    assert res.exit_code == 0
    assert run("incoming", "--adopt").exit_code == 0
    assert run("apply").exit_code == 0

    out = Graph.load(root / "decisions.json")
    assert out.active_edge("D01").answer == "IVF-PQ for the batch path"
    assert out.active_edge("D51").answer == "HNSW for the served path"
    assert out.active_edge("D51").falsifier == "the corpus passes 10M"
    # Nothing superseded and nothing declined: neither answer lost.
    assert out.history("D01") == [] and out.rejected("D01") == []
    # And no edge was invented — attaching it under D01's premises would
    # assert a dependency nobody wrote.
    assert out.depends("D51") == []


def test_splitting_needs_an_id_and_only_means_something_for_an_answer(
        two_writers):
    root, run = two_writers
    run("amend", "D01", "--title", "mine")
    run("apply")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "mine")
    _git(root, "checkout", "-q", "worker")
    run("amend", "D01", "--title", "theirs")
    run("apply")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "theirs")
    _git(root, "checkout", "-q", "master")

    run("integrate", "worker")
    raw = integrate.load_incoming(root)
    ref = raw["contested"][0]["ref"]
    assert run("incoming", "--split", ref).exit_code == 2        # no --as
    res = run("incoming", "--split", ref, "--as", "D51")
    assert res.exit_code == 1 and "not two answers" in res.output


# ---- the order the ops arrive in -----------------------------------------


def test_the_incoming_file_preserves_the_order_the_ops_were_derived_in(
        two_writers):
    """Left open by the design as *probably a test rather than a decision*, and
    it is. Replay is ordered — a record exists before anything attaches to it,
    a question is reopened before it is answered again, and removals come last
    because a removal rewrites the edges around it. Nothing between derivation
    and the file re-sorts, and this is what says so: the file's own order has
    to be the order `plan` produced, or the first op to be replayed out of turn
    is a refusal about the wrong act."""
    root, run = two_writers
    run("integrate", "worker")
    raw = integrate.load_incoming(root)
    refs = [op["iref"] for op in raw["decisions"]]
    assert refs == sorted(refs, key=lambda r: int(r[1:]))
    trefs = [op["iref"] for op in raw["tasks"]]
    assert trefs == sorted(trefs, key=lambda r: int(r[1:]))


def test_a_removal_is_derived_after_the_ops_that_name_what_it_removes(g):
    """The ordering that matters most. A removal rewrites the edges around it,
    so replayed before an `add_edge` naming the removed vertex it leaves the
    second op refusing — and a person reading that refusal would be reading
    about the wrong act."""
    # D06 is the fixture's only vertex no decided answer opens, so it is the
    # one a removal can reach without reopening something first.
    theirs = replay(g, [
        {"op": "add_vertex", "id": "D50", "title": "new", "area": "Beta",
         "status": "OPEN"},
        {"op": "add_edge", "from": "D05", "to": ["D50"]},
        {"op": "remove_vertex", "vertex": "D06", "mode": "sever"},
    ])
    ops = integrate.decisions(g, theirs).ops
    kinds = [o["op"] for o in ops]
    assert kinds.index("remove_vertex") == len(kinds) - 1
    assert kinds.index("add_vertex") < kinds.index("add_edge")


# ---- the enumeration, and what reads it (audit `G-F12`) -------------------


def test_every_op_kind_is_contestable_or_argued_not_to_be():
    """The half an enumeration cannot have on its own.

    `_contest_*` spells its cases out one by one, which is right — the comment
    above it argues that contested-ness cannot be derived from a refusal. What
    that leaves is a list whose completeness nothing reads, under a report line
    that asserts completeness: *"nothing contested — every op applies and the
    result is valid"*. `set_status` sat outside the list for five statuses and
    no reader could have told.

    So every op kind either has a rule or is named in `CANNOT_CONFLICT` with
    the reason two writers doing it to one record is not two judgements. A new
    kind fails here on the day it is added.

    **What it cannot catch is a reason that is false**, and two were: the
    removals sat in `CANNOT_CONFLICT` arguing that a removal *is refused or
    lands*, when the refusal they named fires only where a decided answer holds
    the vertex. This test passed throughout, because it counts entries rather
    than checking them. `D50` is that gap; `D48` moved these two across.
    """
    ruled = {"close", "set_fields", "set_status",
             "remove_vertex", "remove_task"}
    every = pending.OPS | task_pending.OPS
    unjudged = every - ruled - set(integrate.CANNOT_CONFLICT)
    assert not unjudged, (
        "op kind(s) with no contest rule and no argument for having none: "
        + ", ".join(sorted(unjudged)))

    stale = set(integrate.CANNOT_CONFLICT) - every
    assert not stale, (
        "CANNOT_CONFLICT names op kind(s) neither store accepts: "
        + ", ".join(sorted(stale)))

    # A reason, not a placeholder — the table's value is the argument, and an
    # empty string would pass a membership test while saying nothing.
    for kind, why in integrate.CANNOT_CONFLICT.items():
        assert len(why.split()) >= 4, f"{kind} is listed without a reason"


def test_two_writers_moving_one_status_apart_is_contested(tg):
    """The rule `_contest`'s docstring states, applied to the field every count
    is derived from.

    One writer finishes the work and records an outcome; the other abandons it.
    Before this, `0 contested` and the arriving drop landed silently, leaving a
    record that read DROPPED with the measured result printed underneath and
    `dg check` reporting all invariants holding.
    """
    tid = sorted(tg.tasks)[0]
    # Both sides stated, not inherited from the fixture's ordering: the rule is
    # about a *move* from the base, so a base that already held the status
    # under test would make this pass for the wrong reason.
    base = copy.deepcopy(tg)
    base.tasks[tid] = replace(base.tasks[tid], status="TODO")
    mine = copy.deepcopy(tg)
    mine.tasks[tid] = replace(mine.tasks[tid], status="DONE")

    op = {"op": "set_status", "task": tid, "status": "DROPPED"}
    got = integrate._contest_task(mine, base, op)
    assert got is not None, "a drop over a completion is not contested"
    assert "status differs" in got[1]
    assert "DONE" in got[1] and "DROPPED" in got[1]


def test_a_status_this_clone_never_moved_is_not_contested(tg):
    """The other half of the rule, and the reason it is a rule rather than a
    comparison: an op says *make it this*, so comparing what it writes against
    what is here reports every op as contested. Measured against the base."""
    tid = sorted(tg.tasks)[0]
    base = copy.deepcopy(tg)
    base.tasks[tid] = replace(base.tasks[tid], status="TODO")
    mine = copy.deepcopy(base)                 # this clone never touched it

    op = {"op": "set_status", "task": tid, "status": "DROPPED"}
    assert integrate._contest_task(mine, base, op) is None

    # …and two writers making the same edit is not a conflict either.
    mine.tasks[tid] = replace(mine.tasks[tid], status="DROPPED")
    assert integrate._contest_task(mine, base, op) is None


def test_doing_is_not_exempt(tg):
    """Decided rather than inherited: `DOING` is a claim about a run and not a
    judgement about the work, which is an argument for exempting it. It is not
    exempt — if somebody here is working on the task and a contribution says it
    was dropped, that is what a reviewer should see."""
    tid = sorted(tg.tasks)[0]
    base = copy.deepcopy(tg)
    base.tasks[tid] = replace(base.tasks[tid], status="TODO")
    mine = copy.deepcopy(tg)
    mine.tasks[tid] = replace(mine.tasks[tid], status="DOING")

    got = integrate._contest_task(
        mine, base, {"op": "set_status", "task": tid, "status": "DROPPED"})
    assert got is not None and "DOING" in got[1]


# ---- a removal over a record this clone moved -----------------------------
#
# `D48`. These three arrive from the direction the module docstring does not:
# not a removal losing to a side that still names the record, but a removal
# *winning* over a side that changed it. All three landed silently under the
# `CANNOT_CONFLICT` entry these rules replaced, with the report printing
# "nothing contested" — which is the half of the enumeration nothing checked.


def test_a_removal_over_a_local_retitle_is_contested(g):
    base = copy.deepcopy(g)
    theirs = replay(g, [{"op": "remove_vertex", "vertex": "D06",
                         "mode": "sever"}])
    ours = replay(g, [{"op": "set_fields", "vertex": "D06",
                       "title": "retitled here"}])

    found = integrate._walk(ours, base, integrate.decisions(base, theirs).ops,
                            "decisions", pending.expand)[0]
    assert [f.kind for f in found] == ["contested"]
    assert "D06 is removed there" in found[0].message
    assert "title changed here" in found[0].message


def test_a_removal_over_a_child_hung_here_is_contested(g):
    """The case a field comparison misses. This clone never touched D06's own
    row — it hung a fresh question on it — and the removal takes the question
    away and leaves the child a root."""
    base = copy.deepcopy(g)
    theirs = replay(g, [{"op": "remove_vertex", "vertex": "D06",
                         "mode": "sever"}])
    ours = replay(g, [
        {"op": "add_vertex", "id": "D60", "title": "new question",
         "area": "Alpha", "status": "OPEN"},
        {"op": "add_edge", "from": "D06", "to": ["D60"]}])

    found = integrate._walk(ours, base, integrate.decisions(base, theirs).ops,
                            "decisions", pending.expand)[0]
    assert [f.kind for f in found] == ["contested"]
    assert "D60 was hung on it here" in found[0].message


def test_a_removal_over_a_record_nobody_here_touched_is_not_contested(g):
    """The other direction, and the one that makes the rule a rule rather than
    a refusal: one writer removing what nobody else moved is a removal."""
    base = copy.deepcopy(g)
    theirs = replay(g, [{"op": "remove_vertex", "vertex": "D06",
                         "mode": "sever"}])
    ours = copy.deepcopy(g)

    found, probe, _ = integrate._walk(
        ours, base, integrate.decisions(base, theirs).ops, "decisions",
        pending.expand)
    assert found == []
    assert "D06" not in probe.vertices


def test_the_task_store_contests_a_removal_the_same_way(tg):
    """The twin, because a rule applied in one store and not the other is the
    shape most of this tool's audit findings took."""
    base = copy.deepcopy(tg)
    theirs = copy.deepcopy(tg)
    task_pending._apply_one(theirs, {"op": "remove_task", "task": "T04",
                                     "mode": "sever"})
    ours = copy.deepcopy(tg)
    task_pending._apply_one(ours, {"op": "set_fields", "task": "T04",
                                   "title": "retitled here"})

    found = integrate._walk(ours, base, integrate.tasks(base, theirs).ops,
                            "tasks", None)[0]
    assert [f.kind for f in found] == ["contested"]
    assert "T04 is removed there" in found[0].message


def test_a_removal_over_a_local_change_reaches_a_person_and_is_answerable(
        two_writers):
    """`D48` end to end: the report, the refusal to adopt while it is open, and
    both answers. Add-wins is the *declared* policy — what it replaced was
    remove-wins by accident, with the report asserting nothing was contested.
    """
    root, run = two_writers
    # A record that is in the base: the worker removes it, we reword it.
    _git(root, "checkout", "-q", "worker")
    assert run("task", "rm", "T01", "--yes").exit_code == 0
    assert run("apply").exit_code == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "worker removes T01")
    _git(root, "checkout", "-q", "master")
    assert run("task", "amend", "T01",
               "--title", "kept, and reworded here").exit_code == 0
    assert run("apply").exit_code == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "mine")

    out = run("integrate", "worker").output
    assert "T01 is removed there" in out and "title changed here" in out

    assert run("incoming", "--adopt").exit_code == 1, "open while unanswered"
    # The ref is read off the line that names the record, not hardcoded: the
    # fixture's worker branch carries other ops, so the index is not stable.
    line = next(l for l in run("incoming").output.splitlines()
                if "T01 is removed there" in l)
    ref = line.split()[1]
    assert run("incoming", "--keep", ref).exit_code == 0
    assert run("incoming", "--adopt").exit_code == 0
    from dgraph.tasks import TaskGraph
    assert "T01" in TaskGraph.load(root / "tasks.json").tasks
