"""`dg context` — the reasoning behind one node.

Two things are being guarded. First, that the chain is *complete and ordered*:
this output is pasted into a subagent's prompt, and a premise left out is a
constraint the agent will not know it is violating. Second, that `data()` and
`text()` come from the same walk — the `brief.py` property, for the same reason.
"""

import json
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from dgraph import context, project
from dgraph.cli import app
from dgraph.model import Graph
from dgraph.render import write
from dgraph.tasks import TaskGraph

runner = CliRunner()


def dg(root, *args):
    """Invoke `dg` against one project.

    `--project` is not optional here: the root callback calls `project.use()`
    on every invocation, which clears the override the fixtures set — so a
    CliRunner test that omits it runs against the real cwd.
    """
    return runner.invoke(app, ["--project", str(root), *args])


@pytest.fixture
def both(store, task_store, g):
    """One directory holding both stores, the tasks pointing at real decisions."""
    write(g)
    tg = TaskGraph.load(task_store / "tasks.json")
    tg.tasks["T01"].because = "D01"      # DONE, premise settled
    tg.tasks["T02"].because = "D04"      # TODO, premise settled, deep chain
    tg.tasks["T03"].because = "D05"      # TODO, premise OPEN
    tg.tasks["T04"].evidence_for = "D05"  # DOING, will settle D05
    tg.save(task_store / "tasks.json")
    return task_store


# ---- the walk ------------------------------------------------------------


def test_the_chain_is_every_ancestor_nearest_last(g):
    """D04 rests on D02 rests on D01. Ordered by depth, so the roots come
    first and the decision's own premises come last — the order the project
    actually settled them in."""
    assert [p.id for p in context.chain(g, "D04")] == ["D01", "D02"]
    assert [p.depth for p in context.chain(g, "D04")] == [0, 1]


def test_a_root_rests_on_nothing(g):
    assert context.chain(g, "D01") == []


def test_each_premise_carries_what_makes_it_one(g):
    """An id and a title are not a premise. The answer, the evidence behind it
    and the falsifier that would overturn it are the whole point."""
    d01 = context.chain(g, "D04")[0]
    assert d01.answer == "The root answer."
    assert d01.falsifier == "new evidence appears"
    assert d01.source == "discussion"
    assert d01.date == "2026-01-01"


def test_a_premise_reports_what_else_it_opened(g):
    """D01 opened D02 and D03. Explaining D02 must say D03 is also resting on
    it — a premise holding up three things is a different risk from one."""
    d01 = context.chain(g, "D02")[0]
    assert d01.also_opened == ["D03"]


def test_an_undecided_premise_is_marked_shaky_and_carries_no_answer(g):
    """D06 is blocked on D05, which is OPEN. The chain must say so rather than
    presenting an empty answer as an answer."""
    d05 = [p for p in context.chain(g, "D06") if p.id == "D05"][0]
    assert d05.shaky and d05.answer is None


def test_a_reopened_premise_makes_everything_under_it_shaky(store, g):
    g.vertices["D01"] = replace(g.vertices["D01"], status="REOPENED")
    g.save()
    assert context.decision(Graph.load(), "D04")["shaky_premises"] == ["D01"]


def test_a_deep_chain_does_not_recurse(store):
    """Audit C7's guard, on the new walk: a graph deep enough to blow the
    stack must still produce a chain."""
    n = 1200
    Graph.load()  # the fixture is replaced wholesale
    (store / "decisions.json").write_text(json.dumps({
        "areas": ["Alpha"],
        "vertices": [{"id": f"D{i:04d}", "title": f"step {i}", "area": "Alpha",
                      "status": "DECIDED"} for i in range(n)],
        "edges": [{"from": f"D{i:04d}", "to": [f"D{i+1:04d}"], "active": True,
                   "answer": "a", "falsifier": "f", "source": "s",
                   "date": "2026-01-01"} for i in range(n - 1)],
    }), encoding="utf-8")
    assert len(context.chain(Graph.load(), f"D{n-1:04d}")) == n - 1


# ---- the two renderings agree --------------------------------------------


def test_text_names_every_id_the_data_does(g):
    d = context.decision(g, "D04")
    out = context.text(d)
    for p in d["chain"]:
        assert p["id"] in out
        assert p["answer"].split(".")[0] in out


def test_text_keeps_a_table_in_the_answer_intact(store):
    """An answer may hold an org or markdown table. Wrapping the whole string
    as one paragraph turns it into an unreadable run — this is what that bug
    looked like."""
    g = Graph.load()
    g.active_edge("D01").answer = (
        "The sweep:\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nSo the first one.")
    g.save()
    out = context.text(context.decision(Graph.load(), "D02"))
    assert "| a | b |" in out and "| 1 | 2 |" in out


def test_org_prose_is_converted_the_way_the_view_converts_it(store):
    """The same answer must not read one way in decision-graph.md and another
    here; `orgmd` is the one converter."""
    g = Graph.load()
    e = g.active_edge("D01")
    e.answer, e.format = "*HNSW* it is.", "org"
    g.save()
    assert "**HNSW**" in context.text(context.decision(Graph.load(), "D02"))


# ---- tasks ---------------------------------------------------------------


def test_a_task_pulls_in_its_premise_and_that_premise_s_whole_chain(both):
    d = context.data(project.find(), "T02")
    assert d["kind"] == "task" and d["because"] == "D04"
    assert d["premise"]["id"] == "D04"
    # D04's own chain, not just D04: that is the part a fresh context lacks.
    assert [p["id"] for p in d["chain"]] == ["D01", "D02"]


def test_a_task_waiting_on_an_unsettled_premise_says_so(both):
    d = context.data(project.find(), "T03")
    assert d["gated_by"] == "D05" and not d["ready"]
    assert "D05" in d["verdict"] and "not settled" in d["verdict"]


def test_a_task_with_no_premise_says_nothing_can_go_stale(both):
    tg = TaskGraph.load()
    tg.tasks["T04"].evidence_for = None
    tg.save()
    d = context.data(project.find(), "T04")
    assert d["because"] is None
    assert "no premise" in d["verdict"]


def test_evidence_work_is_reported_from_the_decision_s_side_too(both):
    """T04 is evidence for D05, so D05's context must name it as outstanding —
    the reading `cross.pending_evidence` exists for."""
    d = context.data(project.find(), "D05")
    assert d["work"]["evidence"] == ["T04"]
    assert d["work"]["pending_evidence"] == ["T04"]
    assert "T04" in context.text(d)


def test_a_dangling_premise_is_named_not_hidden(both):
    tg = TaskGraph.load()
    tg.tasks["T01"].because = "D99"
    tg.save()
    d = context.data(project.find(), "T01")
    assert d["premise"] is None and d["because"] == "D99"
    assert "dangling" in d["verdict"]


def test_a_tasks_only_project_says_the_premise_could_not_be_checked(task_store):
    """Not "no premise": a project with no decision store cannot tell the
    difference, and reporting the wrong one of those is a silent lie."""
    d = context.data(project.find(), "T02")
    assert "not available" in d["verdict"]


# ---- the command ---------------------------------------------------------


def test_the_command_prints_the_chain(both):
    res = dg(both, "context", "T02")
    assert res.exit_code == 0
    assert "D01" in res.output and "D02" in res.output


def test_json_and_text_describe_the_same_node(both):
    d = json.loads(dg(both, "context", "D04", "--json").output)
    assert [p["id"] for p in d["chain"]] == ["D01", "D02"]
    assert d == context.data(project.find(), "D04")


def test_the_output_is_pipe_safe(both):
    """Read through a pipe into a subagent's prompt. Rich markup, or the
    soft-wrap `con.print` applies at $COLUMNS, would corrupt it."""
    out = dg(both, "context", "D04").output
    assert "\x1b[" not in out and "[green]" not in out


def test_an_unknown_id_exits_one_and_says_which_store_it_looked_in(both):
    res = dg(both, "context", "D99")
    assert res.exit_code == 1 and "D99" in res.output


def test_a_task_id_in_a_project_with_no_task_store_says_that(store, g):
    """The most confusing failure to get wrong: a well-formed id, and the
    store it belongs to does not exist here."""
    write(g)
    res = dg(store, "context", "T01")
    assert res.exit_code == 1 and "tasks.json" in res.output


# ---- the editor renders the same walk ------------------------------------


def test_the_org_context_names_the_same_ancestors(g):
    """`editor._context` renders `context.chain`; three consumers, one
    traversal. If they ever disagree about what a decision rests on, one of
    them is telling somebody the wrong thing."""
    from dgraph import editor
    org = editor._context(g, "D04")
    for p in context.chain(g, "D04"):
        assert p.id in org


def test_why_is_the_same_command_as_context(store, g):
    """`dg why` is the question the tool is named for. Registered against the
    one callback, so the two cannot drift in options, help or output."""
    write(g)
    a, b = dg(store, "why", "D02"), dg(store, "context", "D02")
    assert a.exit_code == 0 and a.output == b.output
    assert "CHAIN" in a.output
    # ...including the flag, which is where a wrapper would have drifted first.
    x, y = dg(store, "why", "D02", "--full"), dg(store, "context", "D02", "--full")
    assert x.output == y.output and "RESTS ON" in x.output


def test_why_takes_the_same_options(store, g):
    write(g)
    res = dg(store, "why", "D02", "--json")
    assert res.exit_code == 0
    assert json.loads(res.output)["id"] == "D02"


def test_why_reports_an_unknown_id_like_context_does(store, g):
    write(g)
    a, b = dg(store, "why", "D99"), dg(store, "context", "D99")
    assert a.exit_code == 1 and a.output == b.output
