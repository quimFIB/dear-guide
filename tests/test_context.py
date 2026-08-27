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
    tg.tasks["T01"].because = ["D01"]      # DONE, premise settled
    tg.tasks["T02"].because = ["D04"]      # TODO, premise settled, deep chain
    tg.tasks["T03"].because = ["D05"]      # TODO, premise OPEN
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
    assert d["kind"] == "task" and d["because"] == ["D04"]
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
    assert d["because"] == []
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
    tg.tasks["T01"].because = ["D99"]
    tg.save()
    d = context.data(project.find(), "T01")
    assert d["premise"] is None and d["because"] == ["D99"]
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


# ---- why work stopped, whatever stopped it -------------------------------


def _stopped(root, status, stops):
    """T04 -- unconnected, so nothing else is in the way -- stopped `stops`."""
    from dgraph.tasks import Stop
    tg = TaskGraph.load(root / "tasks.json")
    tg.tasks["T04"].status = status
    tg.tasks["T04"].stops = [Stop(why=w, date=d) for w, d in stops]
    tg.save(root / "tasks.json")
    return tg


@pytest.mark.parametrize("status, label", [("PARKED", "put down"),
                                           ("DROPPED", "not being done")])
@pytest.mark.parametrize("flag", [[], ["--full"]])
def test_the_context_surface_shows_why_work_stopped(task_store, status, label,
                                                    flag):
    """F5. `text` tested `status == "DROPPED"` on top of `stopped_because`,
    which has already gated on the status -- so the PARKED reason, the one
    status whose entire point is a written reason, was silently dropped. And
    `compact`, the default, rendered neither.

    Parametrised over both statuses and both forms because the claim is "the
    context surface shows why work stopped", not "it shows why work was
    dropped"."""
    _stopped(task_store, status, [("the upstream fix never landed", "2026-02-01")])
    res = dg(task_store, "context", "T04", *flag)
    assert res.exit_code == 0
    assert "the upstream fix never landed" in res.output
    assert label in res.output.lower()


def test_context_says_which_of_three_stops_is_live(task_store):
    """Three stoppages, three reasons, and which one is current is the thing a
    reader cannot work out from a bare list."""
    _stopped(task_store, "PARKED", [("first", "2026-01-01"),
                                    ("second", "2026-02-01"),
                                    ("third", "2026-03-01")])
    out = dg(task_store, "context", "T04", "--full").output
    body = out.split("STOPPED")[1]
    assert body.index("first") < body.index("second") < body.index("third")
    assert "third  ← put down" in body
    assert "first  ← " not in body and "second  ← " not in body


def test_context_marks_no_stop_live_once_the_work_restarted(task_store):
    """The record survives the restart; the claim that it is current does not."""
    _stopped(task_store, "DOING", [("was stuck", "2026-01-01")])
    out = dg(task_store, "context", "T04", "--full").output
    assert "was stuck" in out
    assert "put down" not in out and "not being done" not in out


def test_one_table_names_the_stop_for_every_renderer(task_store):
    """The label is chosen in one place. Three renderers picked it
    independently -- `task_render`, `app.html` and `context` -- and the fourth
    opinion is how F5 happened."""
    from dgraph import server, task_render, tasks

    assert sorted(tasks.STOP_LABEL) == ["DROPPED", "PARKED"]
    assert tasks.stop_label("DOING") is None
    tg = _stopped(task_store, "PARKED", [("stuck", "2026-02-01")])
    label = tasks.STOP_LABEL["PARKED"]

    assert label.lower() in task_render.render(tg)
    assert context.task(tg, "T04")["stop_label"] == label
    # The panel is served the word rather than choosing one.
    assert server.task_payload(tg, None)["derived"]["T04"]["stop_label"] == label


# ---- several premises ----------------------------------------------------
#
# `because` became a list on 2026-08-27 (`5643301`) and this reading did not
# follow: `premise`, `chain` and `shaky_premises` were all built from the first
# resolving premise while `_verdict` joined the whole list into sentences
# written for one decision. `V-F4`. Every test here fails on that code.


@pytest.fixture
def many(both, g):
    """T02 rests on three premises that disagree: settled, OPEN, and absent."""
    tg = TaskGraph.load()
    tg.tasks["T02"].because = ["D04", "D05", "D99"]
    tg.save()
    return both


def test_every_resolving_premise_is_reported_not_just_the_first(many):
    """The BECAUSE block is the reading an agent acts on before starting work.

    Wider than "the list round-trips": `because` already held all three before
    this change, and reporting the ids while resolving one of them is exactly
    the state the finding describes.
    """
    d = context.data(project.find(), "T02")
    assert d["because"] == ["D04", "D05", "D99"]
    assert [p["id"] for p in d["premises"]] == ["D04", "D05"]


def test_a_dangling_verdict_names_only_the_premise_that_is_missing(many):
    """The false sentence in the finding: D04 and D05 are both in the store."""
    d = context.data(project.find(), "T02")
    assert "D99" in d["verdict"]
    assert "D04" not in d["verdict"] and "D05" not in d["verdict"]


def test_a_gated_verdict_names_the_unsettled_premises_and_no_others(both):
    """D04 is settled and D05 is OPEN — only D05 holds the work back."""
    tg = TaskGraph.load()
    tg.tasks["T02"].because = ["D04", "D05"]
    tg.save()
    d = context.data(project.find(), "T02")
    assert "D05" in d["verdict"] and "D04" not in d["verdict"]


def test_the_chain_unions_every_premises_ancestors(many):
    """One chain, not the first premise's — an agent reads it as the whole
    set of constraints, so a premise's ancestors left out is a constraint it
    will not know it is violating."""
    d = context.data(project.find(), "T02")
    ids = [p["id"] for p in d["chain"]]
    assert len(ids) == len(set(ids)), "an ancestor shared by two premises is one row"
    for p in d["premises"]:
        for q in p["chain"]:
            assert q["id"] in ids


@pytest.mark.parametrize("form", ["", "--full"])
def test_both_renderings_show_every_premise(many, form):
    """`data()` and the two texts come from one walk — the `brief.py` property.
    A premise present in the dict and absent from the page is the drift the
    pair exists to catch."""
    res = dg(many, "context", "T02", *([form] if form else []))
    assert res.exit_code == 0
    assert "D04" in res.output and "D05" in res.output
