"""The relation between the two graphs.

The link is stored on the task and derived in the other direction, so most of
what is pinned here is that `decisions.json` never learns what a task is, and
that the one thing joining them cannot hold a decision hostage to a backlog.
"""

import json
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from dgraph import cross, gate, pending, project
from dgraph.check import run
from dgraph.cli import app
from dgraph.model import Graph
from dgraph.render import write
from dgraph.tasks import Stop, Task, TaskGraph

runner = CliRunner()


@pytest.fixture
def both(store, task_store, g):
    """A project with both stores, the tasks pointing at real decisions.

    `store` and `task_store` share one tmp_path, so requesting both gives a
    directory holding a decision graph and a task graph.
    """
    write(g)
    tg = TaskGraph.load(task_store / "tasks.json")
    tg.tasks["T01"].because = "D01"     # DONE
    tg.tasks["T02"].because = "D01"     # TODO
    tg.tasks["T03"].because = "D05"     # TODO, premise never settled (OPEN)
    tg.save(task_store / "tasks.json")
    from dgraph import task_render
    task_render.write(tg, task_store / "tasks.md")
    return task_store


def _reopen(root, vid="D01"):
    """Put a decision under review, the way `dg reopen` would."""
    g = Graph.load(root / "decisions.json")
    g.vertices[vid] = replace(g.vertices[vid], status="REOPENED")
    for d in g.descendants(vid):
        if g.vertices[d].base_status == "DECIDED":
            g.vertices[d] = replace(g.vertices[d], status="PROVISIONAL")
    e = g.active_edge(vid)
    if e:
        e.answer = e.falsifier = e.source = e.date = None
    g.save(root / "decisions.json")
    write(g, root / "decision-graph.md")
    return g


# ---- derived accessors ---------------------------------------------------


def test_the_reverse_link_is_derived_not_stored(both):
    """`decisions.json` never names a task; the decision->task direction is
    computed by scanning the task store."""
    raw = json.loads((both / "decisions.json").read_text())
    assert "T01" not in json.dumps(raw)
    tg = TaskGraph.load(both / "tasks.json")
    assert cross.rests_on(tg, "D01") == ["T01", "T02"]


def test_gated_by_is_none_once_the_premise_is_settled(both):
    tg, g = TaskGraph.load(both / "tasks.json"), Graph.load(both / "decisions.json")
    assert cross.gated_by(tg, g, "T02") is None     # D01 is DECIDED
    assert cross.gated_by(tg, g, "T03") == "D05"    # D05 is OPEN


def test_gated_by_skips_an_unknown_decision(both):
    """A dangling reference must not crash the traversal that was about to
    report it — the class of bug audit item A1 fixed."""
    tg, g = TaskGraph.load(both / "tasks.json"), Graph.load(both / "decisions.json")
    tg.tasks["T02"].because = "D99"
    assert cross.gated_by(tg, g, "T02") is None


def test_ready_needs_prerequisites_and_a_settled_premise(both):
    tg, g = TaskGraph.load(both / "tasks.json"), Graph.load(both / "decisions.json")
    assert cross.ready(tg, g, "T02")            # T01 done, D01 decided
    tg.tasks["T02"].because = "D05"             # an unsettled premise
    assert not cross.ready(tg, g, "T02")
    assert tg.ready("T02")                      # ...but the task graph alone says yes


def test_blast_radius_is_unfinished_work_only(both):
    tg = TaskGraph.load(both / "tasks.json")
    assert cross.blast_radius(tg, ["D01"]) == ["T02"]      # T01 is DONE


# ---- the invariants ------------------------------------------------------


def test_a_dangling_because_is_a_blocking_error(both):
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T02"].because = "D99"
    tg.save(both / "tasks.json")
    hits = [v for v in run() if v.check == "link_resolves"]
    assert hits and hits[0].blocking and "D99" in str(hits[0])


def test_a_reopened_premise_warns_about_unfinished_work(both):
    _reopen(both)
    hits = [v for v in run() if v.check == "link_premise_under_review"]
    assert len(hits) == 1
    assert "T02" in str(hits[0]) and not hits[0].blocking


def test_completed_work_is_never_flagged(both):
    """The user's explicit choice: reversing a decision must not raise warnings
    about history. T01 is DONE and rests on the reopened D01."""
    _reopen(both)
    assert not [v for v in run()
                if v.check == "link_premise_under_review" and "T01" in str(v)]


def test_a_never_settled_premise_does_not_warn(both):
    """T03 rests on D05, which is OPEN. Planning work ahead of a decision is
    ordinary; warning about it would emit one warning per task per open
    decision — noise proportional to the backlog."""
    assert not [v for v in run() if v.check == "link_premise_under_review"]


def test_a_provisional_premise_warns_too(both):
    """The propagated case: D02 becomes PROVISIONAL when D01 is reopened, so
    work resting on D02 is equally on shaky ground."""
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T03"].because = "D02"
    tg.save(both / "tasks.json")
    _reopen(both)
    hits = [v for v in run() if v.check == "link_premise_under_review"]
    assert any("T03" in str(h) for h in hits)


def test_a_reopened_premise_never_blocks_a_commit(both, tmp_path):
    """The governing severity rule, and the whole of what it covers: *anything
    a reopen can cause* is a warning. If resting on a reopened decision were an
    error, `dg reopen` would deny every commit in the repo until the backlog
    was triaged — and the check would be switched off the same day.

    The two link *errors* are a different thing and do deny — see below. The
    old name for this test claimed the general property and was wrong."""
    _reopen(both)
    problems = run()
    assert any(p.check == "link_premise_under_review" for p in problems)
    assert not [p for p in problems if p.blocking]
    assert gate.verdict("git commit -m x",
                        project.Project(both))["verdict"] == "allow"


def test_the_two_link_errors_do_deny_a_commit(both):
    """The counterpart to the rule above: a dangling premise and a cross-graph
    deadlock are contradictions, not states of play, so they block — which is
    exactly why `dg apply` refuses to write one in the first place."""
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T02"].because = "D99"
    tg.save(both / "tasks.json")
    from dgraph import task_render
    task_render.write(tg, both / "tasks.md")     # the link is the only problem
    v = gate.verdict("git commit -m x", project.Project(both))
    assert v["verdict"] == "deny" and "link_resolves" in v["reason"]
    assert "link between the two graphs" in v["reason"]


def test_a_tasks_only_project_still_resolves_its_links(task_store):
    """Deleting `decisions.json` must not be a way to silence a dangling link:
    with no decision store every link names something that cannot exist."""
    tg = TaskGraph.load(task_store / "tasks.json")
    tg.tasks["T02"].because = "D01"
    tg.save(task_store / "tasks.json")
    from dgraph import task_render
    task_render.write(tg, task_store / "tasks.md")
    hits = [v for v in run() if v.check == "link_resolves"]
    assert hits and hits[0].blocking and "D01" in str(hits[0])


# ---- evidence: work whose outcome bears on a decision --------------------


def test_evidence_is_derived_in_reverse(both):
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T04"].evidence_for = "D05"
    assert cross.evidence(tg, "D05") == ["T04"]
    assert cross.pending_evidence(tg, "D05") == ["T04"]   # DOING
    tg.tasks["T04"].status, tg.tasks["T04"].done = "DONE", "2026-01-09"
    tg.tasks["T04"].outcome = "notes/bench.md"
    assert cross.pending_evidence(tg, "D05") == []


def test_unharvested_evidence_warns(both):
    """The spike ran and nobody wrote down the conclusion. Invisible in either
    graph alone: the task looks done, the decision merely undecided."""
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T01"].evidence_for = "D05"        # T01 is DONE, D05 is OPEN
    tg.save(both / "tasks.json")
    hits = [v for v in run() if v.check == "evidence_unharvested"]
    assert len(hits) == 1
    assert not hits[0].blocking
    assert "dg decide D05" in str(hits[0])


def test_dropped_evidence_warns(both):
    """The sharper sibling of `unharvested`, and a silence neither store can
    break alone. `pending_evidence` filters on `unfinished`, which DROPPED is
    not, so the decision reports waiting on nothing the moment the spike is
    abandoned — while `unharvested` stays quiet too, firing only on DONE."""
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T04"].evidence_for = "D05"        # D05 is OPEN
    tg.tasks["T04"].status = "DROPPED"
    tg.tasks["T04"].stops = [Stop(why="the vendor tool does it", date="2026-02-01")]
    tg.save(both / "tasks.json")
    g = Graph.load(both / "decisions.json")
    # the two readings that used to be the whole story, both silent
    assert cross.pending_evidence(tg, "D05") == []
    assert not [v for v in run() if v.check == "evidence_unharvested"]
    hits = [v for v in run() if v.check == "evidence_dropped"]
    assert len(hits) == 1 and not hits[0].blocking
    assert "dg decide D05" in str(hits[0]) and "T04" in str(hits[0])
    assert cross.dropped_evidence(tg, g)[0]["id"] == "D05"


def test_one_surviving_spike_is_not_a_dropped_silence(both):
    """A decision still visibly waiting on something is not a silence."""
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T04"].evidence_for = "D05"
    tg.tasks["T04"].status = "DROPPED"
    tg.tasks["T04"].stops = [Stop(why="no", date="2026-02-01")]
    tg.tasks["T02"].evidence_for = "D05"        # still unfinished
    tg.save(both / "tasks.json")
    assert not [v for v in run() if v.check == "evidence_dropped"]


def test_dropped_evidence_for_a_settled_decision_warns_on_the_other_check(both):
    """The louder half. `evidence_dropped` stays quiet — it is about a question
    that reads as waiting on nothing — but an *answer* whose evidence was all
    abandoned is standing on work that never produced anything, and that was
    silent in both stores until `evidence_dropped_after_deciding`."""
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T04"].evidence_for = "D01"        # D01 is DECIDED
    tg.tasks["T04"].status = "DROPPED"
    tg.tasks["T04"].stops = [Stop(why="no", date="2026-02-01")]
    tg.save(both / "tasks.json")
    g = Graph.load(both / "decisions.json")
    assert not [v for v in run() if v.check == "evidence_dropped"]
    hits = [v for v in run() if v.check == "evidence_dropped_after_deciding"]
    assert len(hits) == 1 and not hits[0].blocking
    # both readings named, because the store cannot tell them apart
    assert "dg task unlink T04" in str(hits[0])
    assert "dg reopen D01" in str(hits[0])
    assert cross.settled_on_dropped_evidence(tg, g)[0]["id"] == "D01"


def test_one_surviving_spike_is_not_a_settled_silence(both):
    """Matches the unsettled half: one live spike and the answer may yet be
    backed, so there is nothing to break."""
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T04"].evidence_for = "D01"        # D01 is DECIDED
    tg.tasks["T04"].status = "DROPPED"
    tg.tasks["T04"].stops = [Stop(why="no", date="2026-02-01")]
    tg.tasks["T02"].evidence_for = "D01"        # still unfinished
    tg.save(both / "tasks.json")
    assert not [v for v in run()
                if v.check == "evidence_dropped_after_deciding"]


def test_an_unsettled_decision_does_not_warn_on_the_settled_check(both):
    """The two halves partition on `settled`; neither should double-report."""
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T04"].evidence_for = "D05"        # D05 is OPEN
    tg.tasks["T04"].status = "DROPPED"
    tg.tasks["T04"].stops = [Stop(why="no", date="2026-02-01")]
    tg.save(both / "tasks.json")
    assert not [v for v in run()
                if v.check == "evidence_dropped_after_deciding"]
    assert [v for v in run() if v.check == "evidence_dropped"]


def test_parked_evidence_holds_up_an_open_question(both):
    """`parked_holding_work` across the seam. Inside the task store, parked
    work that holds something up is chased; a decision waiting on a spike is
    held the same way, and `unblocks` cannot see it because the thing waiting
    is in the other store — so this was silent in both directions."""
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T04"].evidence_for = "D05"        # D05 is OPEN
    tg.tasks["T04"].status = "PARKED"
    tg.tasks["T04"].stops = [Stop(why="stuck upstream", date="2026-02-01")]
    tg.save(both / "tasks.json")
    hits = [v for v in run() if v.check == "evidence_stalled"]
    assert len(hits) == 1 and not hits[0].blocking
    assert "T04" in str(hits[0]) and "D05" in str(hits[0])
    # not the dropped pair: parked evidence is stopped, not gone
    assert not [v for v in run() if v.check.startswith("evidence_dropped")]


def test_parked_evidence_under_a_settled_answer_warns(both):
    """The other half, mirroring the dropped pair's split on `settled`. Here an
    answer stands on work that stopped before it produced anything — and unlike
    an abandoned spike, this one can still be picked up, which is what the
    remedy says."""
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T04"].evidence_for = "D01"        # D01 is DECIDED
    tg.tasks["T04"].status = "PARKED"
    tg.tasks["T04"].stops = [Stop(why="stuck upstream", date="2026-02-01")]
    tg.save(both / "tasks.json")
    hits = [v for v in run() if v.check == "evidence_stalled_after_deciding"]
    assert len(hits) == 1 and not hits[0].blocking
    assert "dg task unlink T04" in str(hits[0])
    assert not [v for v in run() if v.check == "evidence_stalled"]


def test_one_dropped_and_one_parked_spike_used_to_satisfy_neither(both):
    """The sharpest version of the silence, and the reason this pair is not a
    widening of the dropped one. `evidence_dropped` needs *every* task
    abandoned, so a mix escaped it; the decision then read as waiting on work
    that exists, is unfinished, and is not being done."""
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T04"].evidence_for = "D05"        # D05 is OPEN
    tg.tasks["T04"].status = "DROPPED"
    tg.tasks["T04"].stops = [Stop(why="not needed", date="2026-02-01")]
    tg.tasks["T02"].evidence_for = "D05"
    tg.tasks["T02"].status = "PARKED"
    tg.tasks["T02"].stops = [Stop(why="stuck", date="2026-02-02")]
    tg.save(both / "tasks.json")
    assert not [v for v in run() if v.check == "evidence_dropped"]
    hits = [v for v in run() if v.check == "evidence_stalled"]
    assert len(hits) == 1
    assert "T02" in str(hits[0])            # the recoverable half is the remedy


def test_the_two_pairs_never_both_fire(both):
    """An exact partition, not an overlap: the dropped pair wants every task
    abandoned, this one wants every task stopped with at least one parked.
    Nothing satisfies both, so no decision is reported twice."""
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T04"].evidence_for = "D05"
    tg.tasks["T04"].status = "DROPPED"
    tg.tasks["T04"].stops = [Stop(why="no", date="2026-02-01")]
    tg.save(both / "tasks.json")
    checks = {v.check for v in run()}
    assert "evidence_dropped" in checks
    assert "evidence_stalled" not in checks


def test_one_live_spike_is_not_a_stalled_silence(both):
    """Matching both dropped halves: a decision visibly waiting on work
    somebody is doing is not a silence."""
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T04"].evidence_for = "D05"
    tg.tasks["T04"].status = "PARKED"
    tg.tasks["T04"].stops = [Stop(why="stuck", date="2026-02-01")]
    tg.tasks["T02"].evidence_for = "D05"        # TODO, still going
    tg.save(both / "tasks.json")
    assert not [v for v in run() if v.check == "evidence_stalled"]


def test_evidence_for_a_settled_decision_is_quiet(both):
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T01"].evidence_for = "D01"        # D01 is DECIDED
    tg.save(both / "tasks.json")
    assert not [v for v in run() if v.check == "evidence_unharvested"]


def test_unfinished_evidence_does_not_warn(both):
    """Nothing to harvest until the work is done."""
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T02"].evidence_for = "D05"        # TODO
    tg.save(both / "tasks.json")
    assert not [v for v in run() if v.check == "evidence_unharvested"]


def test_a_decision_waiting_on_a_spike_is_not_decidable_now(both):
    """The correction to the most-read line in the tool."""
    from dgraph import brief
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T02"].evidence_for = "D05"
    tg.save(both / "tasks.json")
    out = brief.text(project.Project(both))
    assert "waiting on evidence from T02" in out
    assert "D05" in out


# ---- cycles across the two graphs ----------------------------------------


def test_because_and_evidence_for_the_same_decision_is_refused(both):
    """A task cannot rest on the answer it exists to produce."""
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T02"].evidence_for = "D01"        # already because D01
    tg.save(both / "tasks.json")
    hits = [v for v in run() if v.check == "link_acyclic"]
    assert hits and hits[0].blocking
    assert "Split the decision" in str(hits[0])


def test_a_cycle_across_the_graphs_is_caught(both):
    """The deadlock neither validator can see on its own: T09 informs D, a task
    that exists because of D must precede it, so nothing in the loop can start."""
    from dgraph.tasks import TaskEdge
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T04"].evidence_for = "D01"        # T04 -> D01
    tg.edges.append(TaskEdge(src="T02", to=["T04"], kind="precedes"))   # T02 -> T04, T02 because D01
    tg.save(both / "tasks.json")
    assert tg.validate() == []                  # the task graph alone is clean
    g = Graph.load(both / "decisions.json")
    assert not [v for v in g.validate() if v.check == "acyclic"]
    hits = [v for v in cross.validate(tg, g) if v.check == "link_acyclic"]
    assert hits and hits[0].blocking


def test_a_prompted_edge_cannot_make_a_cross_cycle(both):
    """The union is "the head waits on the tail", and provenance is not that.

    Feeding `prompted` edges in would manufacture a deadlock out of the
    ordinary case: T04 informs D01, T02 exists because of D01, and doing T02
    turned T04 up. Nothing in that waits on anything in a loop — but as one
    untyped relation it reads exactly like the deadlock the test above pins.
    """
    from dgraph.tasks import TaskEdge
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T04"].evidence_for = "D01"
    tg.edges.append(TaskEdge(src="T02", to=["T04"], kind="prompted"))
    g = Graph.load(both / "decisions.json")
    assert tg.validate() == []
    assert not [v for v in cross.validate(tg, g) if v.check == "link_acyclic"]


def test_because_alone_cannot_make_a_cross_cycle(both):
    """Without a task->decision edge every cycle lies wholly inside one graph,
    which is why the cycle check ships with `evidence_for` and not before."""
    tg = TaskGraph.load(both / "tasks.json")
    for t in tg.tasks.values():
        t.evidence_for = None
    assert not [v for v in cross.validate(tg, Graph.load(both / "decisions.json"))
                if v.check == "link_acyclic"]


# ---- the emergent case ---------------------------------------------------


def test_work_can_be_linked_to_a_decision_it_turned_up(run_cli, both):
    """The case that prompted the feature: doing the work reveals a question
    nobody had written down, so the decision is recorded and the finished task
    pointed at it afterwards."""
    assert run_cli("task", "done", "T02", "-o", "PR #9").exit_code == 0
    assert run_cli("add", "--id", "D30", "-t", "What about backups?",
                   "--area", "Alpha").exit_code == 0
    assert run_cli("apply").exit_code == 0
    res = run_cli("task", "link", "T02", "--evidence-for", "D30")
    assert res.exit_code == 0, res.output
    assert run_cli("apply").exit_code == 0

    tg = TaskGraph.load(both / "tasks.json")
    assert tg.tasks["T02"].evidence_for == "D30"
    assert "evidence from" in run_cli("node", "D30").output
    # ...and it nags until the conclusion is recorded
    assert [v for v in run() if v.check == "evidence_unharvested"]


def test_link_needs_something_to_link(run_cli):
    assert run_cli("task", "link", "T02").exit_code == 2


# ---- the barrier ---------------------------------------------------------


def _imports(module) -> set[str]:
    """Every `dgraph.*` module this one imports, parsed rather than grepped —
    the modules discuss each other in prose, so text matching false-positives."""
    import ast
    import inspect

    names = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            if node.module == "dgraph":
                names |= {f"dgraph.{a.name}" for a in node.names}
    return {n for n in names if n.startswith("dgraph")}


def test_the_two_models_are_mutually_ignorant():
    """The structural half of the barrier: neither model can see the other, so
    neither can grow a dependency on the other's vocabulary. Everything needing
    both lives in `cross.py`, which is visible in a diff."""
    from dgraph import model, tasks
    assert "dgraph.tasks" not in _imports(model)
    assert "dgraph.model" not in _imports(tasks)


def test_only_cross_reasons_about_the_link():
    """Aggregators (`cli`, `check`, `brief`) may load both stores — that is
    what composing a report means. What they must not do is decide *what the
    link means*, or the rule ends up in two places and drifts. `tasks` declares
    and serialises the field, `cli` and `task_render` print it; every other use is
    reasoning and belongs in `cross`.

    This is not hypothetical: `brief` first shipped with its own copy of the
    "is this premise shaky?" rule, and this test is what found it.
    """
    import inspect
    import pkgutil

    import dgraph
    from dgraph import task_render

    # `task_render` prints the stored id and joins nothing — which is only safe
    # while it cannot reach the decision store at all, so that is asserted
    # rather than assumed.
    assert not {"dgraph.cross", "dgraph.model"} & _imports(task_render)

    allowed = {"cross", "tasks", "cli", "task_render"}
    offenders = []
    for info in pkgutil.iter_modules(dgraph.__path__):
        if info.name in allowed:
            continue
        src = inspect.getsource(__import__(f"dgraph.{info.name}",
                                           fromlist=["_"]))
        if ".because" in src or ".evidence_for" in src:
            offenders.append(info.name)
    assert offenders == [], (
        f"these reason about the cross-graph link outside cross.py: {offenders}"
    )


def test_the_decision_store_never_gains_a_task_field(both):
    """The "stored twice, in two directions" failure, guarded. A decision store
    that listed its tasks would dirty on every chore."""
    _reopen(both)
    raw = json.loads((both / "decisions.json").read_text())
    blob = json.dumps(raw)
    for key in ("task", "because", "T01", "T02", "T03"):
        assert key not in blob


def test_the_decision_view_never_mentions_tasks(both):
    """`decision-graph.md` is checked in and guarded by `stale_view`, a
    blocking violation and a gate denial — so a task in it would mean filing a
    chore makes the decision view stale and denies the commit."""
    view = (both / "decision-graph.md").read_text()
    assert "T01" not in view and "implemented by" not in view


# ---- the command surface -------------------------------------------------


@pytest.fixture
def run_cli(both, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERM", "dumb")

    def go(*args, input=None):
        return runner.invoke(app, ["--project", str(both), *args], input=input)
    return go


def test_because_resolves_against_the_effective_graph(run_cli, both):
    """The A3 lesson across the barrier: a decision staged but not applied must
    still be nameable as a premise."""
    assert run_cli("add", "--id", "D20", "-t", "New question",
                   "--area", "Alpha").exit_code == 0
    res = run_cli("task", "add", "--id", "T20", "-t", "The work",
                  "--area", "Alpha", "--because", "D20")
    assert res.exit_code == 0, res.output
    assert run_cli("apply").exit_code == 0
    assert TaskGraph.load(both / "tasks.json").tasks["T20"].because == "D20"


def test_because_refuses_an_unknown_decision(run_cli):
    res = run_cli("task", "add", "--id", "T21", "-t", "x", "--area", "Alpha",
                  "--because", "D99")
    assert res.exit_code == 1 and "unknown decision" in res.output


def test_reopen_reports_the_work_resting_on_it(run_cli):
    res = run_cli("reopen", "D01", "--why", "new evidence", "--yes")
    assert res.exit_code == 0
    assert "unfinished task" in res.output
    assert "T02" in res.output.split("unfinished task")[1]


def test_node_shows_what_implements_a_decision(run_cli):
    out = run_cli("node", "D01").output
    assert "implemented by" in out and "T01 (DONE)" in out


def test_the_task_list_shows_the_premise_gate(run_cli):
    out = run_cli("task").output
    assert "D05 (undecided)" in out          # T03's premise is OPEN


def test_brief_carries_a_bounded_task_section(run_cli, both):
    _reopen(both)
    out = run_cli("brief").output
    assert "TASKS" in out
    assert "premise under review" in out and "T02" in out


def test_brief_has_no_task_section_without_a_task_store(store, g, monkeypatch):
    """A project that has never heard of tasks pays nothing for the feature."""
    monkeypatch.setenv("COLUMNS", "200")
    write(g)
    out = runner.invoke(app, ["--project", str(store), "brief"]).output
    assert "TASKS" not in out


# ---- the apply-time guard (audit D2) -------------------------------------
#
# `cross.validate` is what `dg check` reads; these pin what refuses to *write*.
# Before this guard, every scenario below applied cleanly and was discovered
# afterwards by `dg check`, with the commit gate denying every commit in the
# repository and no `dg` command able to undo the link.


def test_apply_refuses_a_task_batch_that_closes_a_cross_cycle(run_cli, both):
    """T20 informs D01 and comes after T02, which exists because of D01:
    nothing in the loop can start, and neither store's own validator sees it."""
    assert run_cli("task", "add", "--id", "T20", "-t", "Benchmark it",
                   "--area", "Alpha", "--evidence-for", "D01",
                   "--after", "T02").exit_code == 0
    res = run_cli("apply")
    assert res.exit_code == 1
    assert "link_acyclic" in res.output
    assert "T20" not in (both / "tasks.json").read_text()
    assert not [v for v in run() if v.blocking]


def test_apply_refuses_a_task_that_rests_on_the_answer_it_produces(run_cli, both):
    assert run_cli("task", "add", "--id", "T20", "-t", "Spike",
                   "--area", "Alpha", "--because", "D01",
                   "--evidence-for", "D01").exit_code == 0
    res = run_cli("apply")
    assert res.exit_code == 1 and "link_acyclic" in res.output
    assert "T20" not in (both / "tasks.json").read_text()


def test_apply_refuses_a_decision_batch_that_closes_a_cross_cycle(run_cli, both):
    """The inversion that matters most: the batch is decisions-only, and what
    makes it invalid is in the task store."""
    assert run_cli("add", "--id", "D20", "-t", "A new question",
                   "--area", "Alpha").exit_code == 0
    assert run_cli("apply").exit_code == 0
    assert run_cli("task", "add", "--id", "T20", "-t", "The spike",
                   "--area", "Alpha", "--because", "D20",
                   "--evidence-for", "D05").exit_code == 0
    assert run_cli("apply").exit_code == 0

    # D05 -> D20 closes it: D05 -> D20 -> T20 -> D05.
    assert run_cli("decide", "D05", "-a", "The answer", "-s", "a meeting",
                   "--falsifier", "new evidence", "--opens", "D20").exit_code == 0
    res = run_cli("apply")
    assert res.exit_code == 1 and "link_acyclic" in res.output
    assert Graph.load(both / "decisions.json").vertices["D05"].status == "OPEN"


def test_apply_refuses_a_premise_that_no_longer_exists(run_cli, both):
    """`--because` vets against the *effective* decision graph, so a premise
    staged and then discarded leaves a task pointing at nothing."""
    assert run_cli("add", "--id", "D20", "-t", "A new question",
                   "--area", "Alpha").exit_code == 0
    assert run_cli("task", "add", "--id", "T20", "-t", "The work",
                   "--area", "Alpha", "--because", "D20").exit_code == 0
    assert run_cli("clear").exit_code == 0          # the premise is discarded
    res = run_cli("apply")
    assert res.exit_code == 1 and "link_resolves" in res.output
    assert "T20" not in (both / "tasks.json").read_text()


def test_a_decision_and_the_work_resting_on_it_apply_in_one_batch(run_cli, both):
    """The guard must not cost the documented workflow: record the decision and
    the work it implies together, apply once."""
    assert run_cli("add", "--id", "D20", "-t", "Use Postgres",
                   "--area", "Alpha").exit_code == 0
    assert run_cli("task", "add", "--id", "T20", "-t", "Provision it",
                   "--area", "Alpha", "--because", "D20").exit_code == 0
    assert run_cli("apply").exit_code == 0
    assert not [v for v in run() if v.blocking]
    assert cross.rests_on(TaskGraph.load(both / "tasks.json"), "D20") == ["T20"]


def test_a_pre_existing_violation_does_not_freeze_either_store(run_cli, both):
    """"No worse than before", not "perfect or nothing". A store that is
    already invalid — hand-edited, or written before this guard existed — must
    stay repairable with `dg`, which is the only way out the tool offers."""
    from dgraph import task_render
    from dgraph.tasks import TaskEdge
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T04"].evidence_for = "D01"                # T04 -> D01
    tg.edges.append(TaskEdge(src="T02", to=["T04"], kind="precedes"))    # T02 -> T04, because D01
    tg.save(both / "tasks.json")
    task_render.write(tg, both / "tasks.md")
    assert [v for v in run() if v.check == "link_acyclic"]

    # Writes still go through, in both stores...
    assert run_cli("add", "--id", "D20", "-t", "Somewhere to re-point",
                   "--area", "Alpha").exit_code == 0
    assert run_cli("apply").exit_code == 0
    # ...and the repair applies, leaving the join valid again.
    assert run_cli("task", "link", "T04", "--evidence-for", "D20").exit_code == 0
    assert run_cli("apply").exit_code == 0
    assert not [v for v in run() if v.blocking]


def test_the_stage_time_warning_covers_the_join_too(run_cli, both):
    """`_warn_stuck` runs the same guard, so the refusal is visible when the op
    is staged rather than several commands later at apply."""
    assert run_cli("task", "add", "--id", "T20", "-t", "The spike",
                   "--area", "Alpha", "--because", "D01",
                   "--evidence-for", "D05").exit_code == 0
    assert run_cli("apply").exit_code == 0
    res = run_cli("decide", "D05", "-a", "The answer", "-s", "a meeting",
                  "--falsifier", "new evidence", "--opens", "D01")
    assert res.exit_code == 0                      # a warning, never a refusal
    assert "would currently refuse this batch" in res.output


def test_a_cycle_reads_the_same_way_twice(both):
    """The guard compares findings by their text, so the walk must not report
    the same deadlock differently depending on where the DFS entered it."""
    from dgraph.tasks import TaskEdge
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T04"].evidence_for = "D01"
    tg.edges.append(TaskEdge(src="T02", to=["T04"], kind="precedes"))
    g = Graph.load(both / "decisions.json")
    first = [str(v) for v in cross.validate(tg, g) if v.check == "link_acyclic"]
    tg.edges.reverse()
    tg.tasks = dict(reversed(list(tg.tasks.items())))
    assert [str(v) for v in cross.validate(tg, g)
            if v.check == "link_acyclic"] == first


# ---- one command, two independent batches (audit D4, F4) -----------------


def test_an_unreadable_task_tray_does_not_stop_the_decision_batch(run_cli, both):
    """`apply`'s own docstring promises this: "a task batch that will not apply
    can never stop a decision batch that would". It loaded the task tray first,
    unguarded, so an unparseable file took the whole command down."""
    (both / ".dgraph-task-pending.json").write_text("{oh no")
    assert run_cli("add", "--id", "D20", "-t", "A question",
                   "--area", "Alpha").exit_code == 0
    res = run_cli("apply")
    assert res.exit_code == 1                       # the tray is still broken
    assert ".dgraph-task-pending.json could not be read" in res.output
    assert "dg task clear" in res.output
    assert "D20" in Graph.load(both / "decisions.json").vertices   # ...and yet


def test_a_malformed_staged_op_is_a_message_not_a_traceback(run_cli, both):
    json.dump([{"op": "set_status", "status": "DONE"}],
              (both / ".dgraph-task-pending.json").open("w"))
    res = run_cli("task", "start", "T02")       # consults the effective graph
    assert res.exit_code == 1
    assert "Traceback" not in res.output
    assert "missing required field" in res.output
    assert "dg task drop-op" in res.output


def test_the_task_list_says_when_premise_information_is_missing(run_cli, both):
    """Degrading silently prints "ready" for work whose premise is undecided —
    a wrong answer, which is worse than an absent one."""
    (both / "decisions.json").write_text("{not json")
    res = run_cli("task")
    assert "premise information is missing" in res.output


def test_a_cross_graph_deadlock_names_only_the_loop(tg, g):
    """Audit F7's third site. `cross._cycles` already rotated to the smallest
    id and said why — the apply guard tells an introduced finding from a
    pre-existing one **by its text** — while the two store validators reported
    the route into the loop as part of it. The rule lives once now, in
    `violation.cycle_from`, and this pins the property the guard rests on.
    """
    from dgraph.violation import cycle_from

    # the same loop, met at each of its three nodes, reads identically
    walks = [(["D02", "T01", "D05"], "D02"),
             (["T01", "D05", "D02"], "T01"),
             (["D05", "D02", "T01"], "D05")]
    assert {tuple(cycle_from(trail, node)) for trail, node in walks} == {
        ("D02", "T01", "D05", "D02")}

    # and a node merely feeding the loop is not part of the finding
    assert cycle_from(["D01", "D02", "T01", "D05"], "D02") \
        == ["D02", "T01", "D05", "D02"]


# ---- audit F9: the guards and the batch that never lands -------------------
#
# Each guard judges its batch against the *other* store's staged state, on the
# grounds that `dg apply` writes both. Either batch can be refused, though, and
# then the one that landed was validated against a state that never existed.
# The store is left holding a blocking `link_resolves` or `link_acyclic` that
# no `dg` command produced: `dg check` reports it and the commit gate denies
# every commit in the repository until somebody hand-edits a store.
#
# Both directions, because the shape of this bug is "one half was reasoned
# about and its mirror was not".


def _stage(root, name, ops):
    (root / name).write_text(json.dumps(ops), encoding="utf-8")


def _render(root):
    """Both views, so `check.run` has nothing stale to say about them."""
    from dgraph import task_render
    write(Graph.load(root / "decisions.json"), root / "decision-graph.md")
    task_render.write(TaskGraph.load(root / "tasks.json"), root / "tasks.md")


def _apply_both(root):
    """`dg apply`'s sequence: decisions, then tasks, each on its own."""
    from dgraph import applying, pending, task_pending
    out = {}
    for label, path, fn in (
        ("decisions", root / ".dgraph-pending.json", applying.apply_decisions),
        ("tasks", root / ".dgraph-task-pending.json", applying.apply_tasks),
    ):
        ops = pending.load(path)
        if not ops:
            continue
        try:
            out[label] = fn(ops).applied
        except Exception as exc:                       # noqa: BLE001
            out[label] = f"refused: {exc}"
    return out


@pytest.mark.parametrize("direction", ["decision", "task"])
def test_a_batch_is_never_judged_against_one_that_will_not_apply(both, direction):
    """Audit F9, both directions. The batch that lands must have been judged
    against a state that actually comes about.

    *decision*: the decision batch closes a loop through the task store, and
    the task batch that would have broken the loop fails its own validation.
    *task*: the task batch links to a decision the decision batch was supposed
    to create, and that batch is refused.

    Before the fix each of these wrote a store whose blocking violation the
    gate then denied every commit over.
    """
    if direction == "decision":
        # Two open decisions and a task between them, so the only loop the
        # batch can close runs *through* the task store — a decision-store
        # cycle would be caught by `acyclic` and prove nothing about the guard.
        raw = json.loads((both / "decisions.json").read_text())
        raw["vertices"] += [
            {"id": "D07", "title": "one end", "area": "Beta", "status": "OPEN"},
            {"id": "D08", "title": "the other", "area": "Beta", "status": "OPEN"},
        ]
        (both / "decisions.json").write_text(json.dumps(raw, indent=2))
        tg = TaskGraph.load(both / "tasks.json")
        tg.tasks["T05"] = Task(id="T05", title="the spike", area="Beta",
                               status="TODO", because="D07",
                               evidence_for="D08")   # D07 -> T05 -> D08
        tg.save(both / "tasks.json")
        _render(both)
        # D08 opens D07 closes the loop. The clear would break it, but the
        # batch carrying it will not apply — DONE with no outcome.
        _stage(both, ".dgraph-pending.json",
               [{"op": "add_edge", "from": "D08", "to": ["D07"]}])
        _stage(both, ".dgraph-task-pending.json",
               [{"op": "set_link", "task": "T05", "clear": ["evidence_for"]},
                {"op": "set_status", "task": "T02", "status": "DONE"}])
    else:
        _stage(both, ".dgraph-pending.json",
               [{"op": "add_vertex", "id": "D09", "title": "new",
                 "area": "nowhere"}])          # unknown area: refused
        _stage(both, ".dgraph-task-pending.json",
               [{"op": "set_link", "task": "T04", "because": "D09"}])

    _apply_both(both)

    from dgraph import check
    blocking = [str(v) for v in check.run() if v.blocking]
    assert blocking == [], "the apply left the project invalid"


def test_a_batch_that_needs_the_other_one_still_applies(both):
    """The property the staged-state judging exists for, and which the fix
    above must not cost: when the other batch *will* land, a batch that is only
    legal alongside it is still allowed through.

    Here the task op links to a decision that does not exist yet and is created
    by the decision batch in the same `dg apply`.
    """
    _stage(both, ".dgraph-pending.json",
           [{"op": "add_vertex", "id": "D09", "title": "new", "area": "Beta"}])
    _stage(both, ".dgraph-task-pending.json",
           [{"op": "set_link", "task": "T04", "because": "D09"}])

    assert _apply_both(both) == {"decisions": 1, "tasks": 1}
    assert TaskGraph.load(both / "tasks.json").tasks["T04"].because == "D09"
    from dgraph import check
    assert [str(v) for v in check.run() if v.blocking] == []


def test_a_refused_decision_batch_does_not_stop_the_task_batch(run_cli, both):
    """`dg apply`'s own docstring: "a task batch that will not apply can never
    stop a decision batch that would". The reverse was not true — a refused
    decision batch exited before the task batch was reached — which is why the
    two hosts needed different reproductions of the bug above. The server
    always continued; the CLI now does too.
    """
    _stage(both, ".dgraph-pending.json",
           [{"op": "add_vertex", "id": "D09", "title": "new",
             "area": "nowhere"}])                      # refused
    _stage(both, ".dgraph-task-pending.json",
           [{"op": "set_status", "task": "T02", "status": "DOING"}])

    res = run_cli("apply")
    assert res.exit_code == 1                          # something was refused
    assert "aborted, nothing written" in res.output    # the decision batch
    assert TaskGraph.load(both / "tasks.json").tasks["T02"].status == "DOING"


@pytest.mark.parametrize("missing", ["decisions.json", "tasks.json"])
def test_a_missing_store_does_not_stop_the_other_batch(run_cli, both, missing):
    """Audit F22. The independence above held for a *refusal* and not for a
    missing store, which is a different way for a batch to be unapplicable and
    reached the same helpers by a different door.

    `_apply_decisions` and `_apply_tasks` are written to return rather than
    exit, so `apply` can give both batches their turn — but they loaded the
    store through `_g()`/`_tg()`, which print and `raise typer.Exit`, and those
    were evaluated as arguments. A project with no `decisions.json` therefore
    exited before the task batch was reached, losing nothing but applying
    nothing either, with exit code 2.

    Not a hand-edited state: both trays are gitignored, so they outlive a
    `git checkout` of a branch on which one of the stores does not exist yet.
    """
    survivor = "tasks.json" if missing == "decisions.json" else "decisions.json"
    _stage(both, ".dgraph-pending.json",
           [{"op": "add_vertex", "id": "D09", "title": "new", "area": "Beta"}])
    _stage(both, ".dgraph-task-pending.json",
           [{"op": "set_status", "task": "T02", "status": "DOING"}])
    (both / missing).unlink()

    res = run_cli("apply")
    assert res.exit_code == 1, res.output          # a batch did not apply...
    assert missing in res.output                   # ...and it says which store

    if survivor == "tasks.json":
        assert TaskGraph.load(both / "tasks.json").tasks["T02"].status == "DOING"
        assert pending.load(both / ".dgraph-task-pending.json") == []
        # The batch that could not run keeps its ops: nothing was applied, so
        # nothing may be discarded.
        assert len(pending.load(both / ".dgraph-pending.json")) == 1
    else:
        assert "D09" in (both / "decisions.json").read_text()
        assert pending.load(both / ".dgraph-pending.json") == []
        assert len(pending.load(both / ".dgraph-task-pending.json")) == 1


def test_an_unreadable_store_does_not_stop_the_other_batch(run_cli, both):
    """The same, for a store that is present and will not parse. `Graph.load`
    raises where `has_decisions` is happy, and an exception out of one helper
    takes the other batch with it exactly as an exit did."""
    _stage(both, ".dgraph-pending.json",
           [{"op": "add_vertex", "id": "D09", "title": "new", "area": "Beta"}])
    _stage(both, ".dgraph-task-pending.json",
           [{"op": "set_status", "task": "T02", "status": "DOING"}])
    (both / "decisions.json").write_text("{ not json", encoding="utf-8")

    res = run_cli("apply")
    assert res.exit_code == 1, res.output
    assert "could not be read" in res.output
    assert TaskGraph.load(both / "tasks.json").tasks["T02"].status == "DOING"
    assert len(pending.load(both / ".dgraph-pending.json")) == 1


def test_the_stage_time_warning_does_not_cry_over_a_decision_being_staged(run_cli, both):
    """The regression the F9 fix could have introduced, and the reason
    `decisions_after` exists.

    The apply-time guard reads the decision *store*, which is right there:
    decisions are written first, so the file already holds a batch that landed.
    The stage-time warning asks the same question before either batch has run,
    and against the file alone it announces that a link is dangling when the
    very next command is about to create the decision it names — a message
    `dg apply` then disproves.
    """
    run_cli("add", "--id", "D09", "--title", "a new question", "--area", "Beta")
    res = run_cli("task", "link", "T04", "--because", "D09")

    assert res.exit_code == 0
    assert "would currently refuse" not in res.output
    assert _apply_both(both) == {"decisions": 1, "tasks": 1}
    assert TaskGraph.load(both / "tasks.json").tasks["T04"].because == "D09"


def test_the_stage_time_warning_still_fires_when_nothing_will_fix_it(run_cli, both):
    """The other side of it: a task batch that no staged decision rescues must
    still be called out at staging time rather than several commands later."""
    _stage(both, ".dgraph-task-pending.json",
           [{"op": "set_link", "task": "T04", "because": "D77"}])
    res = run_cli("task", "start", "T02")     # any command that re-warns

    assert "would currently refuse" in res.output
    assert "D77" in res.output
