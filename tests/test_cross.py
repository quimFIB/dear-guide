"""The relation between the two graphs.

The link is stored on the task and derived in the other direction, so most of
what is pinned here is that `decisions.json` never learns what a task is, and
that the one thing joining them cannot hold a decision hostage to a backlog.
"""

import json
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from dgraph import cross, gate, project
from dgraph.check import run
from dgraph.cli import app
from dgraph.model import Graph
from dgraph.render import write
from dgraph.tasks import TaskGraph

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
    tg.edges.append(TaskEdge(src="T02", to=["T04"]))   # T02 -> T04, T02 because D01
    tg.save(both / "tasks.json")
    assert tg.validate() == []                  # the task graph alone is clean
    g = Graph.load(both / "decisions.json")
    assert not [v for v in g.validate() if v.check == "acyclic"]
    hits = [v for v in cross.validate(tg, g) if v.check == "link_acyclic"]
    assert hits and hits[0].blocking


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
    tg.edges.append(TaskEdge(src="T02", to=["T04"]))    # T02 -> T04, because D01
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
    tg.edges.append(TaskEdge(src="T02", to=["T04"]))
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
