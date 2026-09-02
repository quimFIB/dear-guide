"""The demo store must keep holding one of every record, and stay loadable.

The demo is the only graph most people will ever see, and it went stale in the
quiet way: the work after it added `PARKED`, superseded edges reachable from
the CLI, provenance edges, readings and the cross-store findings, and none of
it reached `demo/`. Every command still worked, `dg check` still passed, and the
walkthrough went on describing a graph with no reversal in it. Nothing could
fail, which is why nobody noticed.

So the tests here are written as *what the demo is for*, not as a schema check.
`test_demo_holds_one_of_every_record` names the states a reader is promised, so
adding a status without putting it in the demo fails here rather than in a
reader's understanding; `test_demo_findings_are_the_three_the_walkthrough_names`
pins the findings *by name*, because those three are the whole reason the store
is not clean, and a fourth appearing silently would make the walkthrough wrong
about what the soundness chip says.

Deliberately **not** asserted: prose. A demo whose sentences are pinned is a
demo nobody edits.
"""

import json
import shutil
from pathlib import Path

import pytest

from dgraph import check, cross, project, render, task_render
from dgraph.model import Graph
from dgraph.tasks import TaskGraph

DEMO = Path(__file__).resolve().parent.parent / "demo"


@pytest.fixture
def demo(tmp_path, monkeypatch):
    """The demo served the way `demo.sh` serves it — copied, then rendered.

    The render is not a detail: `stale_view` and `stale_task_view` fire against
    a store whose generated views are absent, so a fixture that skipped it
    would report two findings the demo never shows anyone.
    """
    for name in ("decisions.json", "tasks.json"):
        shutil.copy(DEMO / name, tmp_path / name)
    monkeypatch.setenv("DG_PROJECT", str(tmp_path))
    render.write(Graph.load(tmp_path / "decisions.json"),
                 tmp_path / "decision-graph.md")
    task_render.write(TaskGraph.load(tmp_path / "tasks.json"),
                      tmp_path / "tasks.md")
    return tmp_path


def test_demo_stores_load_and_hold_together(demo):
    """Whatever else drifts, the two stores must be a valid project.

    `demo.sh` runs `dg check` under `set -e`, so an error here is the demo
    failing to start rather than a test failing quietly.
    """
    g = Graph.load(demo / "decisions.json")
    tg = TaskGraph.load(demo / "tasks.json")
    blocking = [v for v in g.validate() + tg.validate() + cross.validate(tg, g)
                if v.severity == "error"]
    assert not blocking, [v.message for v in blocking]


def test_demo_holds_one_of_every_record(demo):
    """Every status a reader is promised is in the store to be clicked on.

    The list is spelled out rather than read off `SIMPLE_STATUSES` and
    `tasks.STATUSES`: a new status should fail this test and make somebody
    decide where in the demo it belongs, which is exactly what reading it off
    the source would skip.
    """
    g = Graph.load(demo / "decisions.json")
    tg = TaskGraph.load(demo / "tasks.json")

    assert {v.base_status for v in g.vertices.values()} == {
        "DECIDED", "OPEN", "BLOCKED", "REOPENED", "PROVISIONAL"}
    assert {t.status for t in tg.tasks.values()} == {
        "TODO", "DOING", "PARKED", "DONE", "DROPPED"}


def test_demo_holds_a_reading(demo):
    """Late evidence that was read against its answer, and held.

    `dg confirm --against` is the third exit from `evidence_after_deciding`,
    and the common one; the store has to hold a reading so the panel and
    `dg node` have one to show, and so the finding on D02 reads as the case
    where nobody has taken an exit yet rather than the only shape late
    evidence can have.
    """
    tg = TaskGraph.load(demo / "tasks.json")
    read = [t for t in tg.tasks.values() if getattr(t, "readings", None)]
    assert read, "no task in the demo carries a reading"
    t = read[0]
    assert t.status == "DONE" and t.evidence_for == t.readings[-1].against
    assert t.readings[-1].note


def test_demo_holds_work_finished_more_than_once(demo):
    """The record `F-F5` exists for, in the store a reader actually opens.

    A completion is archived exactly as a stop is, and a demo showing only
    once-finished work shows the list as a formality — the reader has no reason
    to notice that the first result was kept, because nothing was superseded.
    T01 was built, T02 changed what it was built over, and it was built again."""
    tg = TaskGraph.load(demo / "tasks.json")
    twice = [t for t in tg.tasks.values() if len(t.completions) > 1]
    assert twice, "no task in the demo was finished more than once"
    t = twice[0]
    assert t.outcome == t.completions[-1].outcome     # the live one is the last
    assert t.completions[0].outcome != t.outcome      # and the first was kept


def test_demo_holds_both_kinds_of_reversal(demo):
    """A finished reversal and one still open — the record this model exists for.

    Both, because they read differently and only one of them is a status: a
    re-decided question carries `replaced_by` and is DECIDED again, while a
    reopened one carries the same archive with nothing yet in its place.
    """
    g = Graph.load(demo / "decisions.json")
    superseded = [e for e in g.edges if not e.active]
    assert [e for e in superseded if e.replaced_by], "no completed reversal"
    assert [e for e in superseded
            if e.replaced_by is None
            and g.vertices[e.src].base_status == "REOPENED"], "no live reopen"
    assert all(e.why for e in superseded), "a reversal with no reason kept"


def test_demo_holds_both_task_edge_kinds(demo):
    """`prompted` blocks nothing, so a demo without one cannot show the difference."""
    kinds = {e.kind for e in TaskGraph.load(demo / "tasks.json").edges}
    assert kinds == {"precedes", "prompted"}


def test_demo_exercises_both_directions_of_the_seam(demo):
    """`because` and `evidence_for` have opposite polarity, and both are shown."""
    tg = TaskGraph.load(demo / "tasks.json")
    assert [t for t in tg.tasks.values() if t.because]
    assert [t for t in tg.tasks.values() if t.evidence_for]


def test_demo_findings_are_the_four_the_walkthrough_names(demo):
    """Exactly four findings stand, and the walkthrough gives each one an exit.

    Pinned by name and by count. A fifth appearing — from a new rule, or from
    an edit to the store — makes the soundness chip say something the
    walkthrough does not explain, which is the failure this catches. All four
    are warnings by construction: an error would stop `demo.sh`.

    `verbose_field` was the fourth, and it arrived exactly the way this test is
    written to catch: a new rule fired on a store nobody had reread. It was
    kept rather than tidied away, because D02's answer cites `bench/ann-sweep.md`
    and then pastes that file's table into the store — which is the duplication
    the rule is about rather than a false positive, and the demo's only long
    field is also the only thing that shows the panel's fold doing its job.
    """
    found = check.run(project.find())
    assert {v.check for v in found} == {
        "parked_holding_work",           # T10 is parked and T04 waits on it
        "evidence_after_deciding",       # T09 measured D02 after D02 was settled
        "link_premise_under_review",     # T08 rests on D07, which D03 reopened
        "verbose_field",                 # D02's answer pastes the sweep it cites
    }, [(v.check, v.message, str(project.find().root), str(demo)) for v in found]
    assert all(v.severity == "warning" for v in found), [v.message for v in found]


def test_demo_json_is_hand_editable(demo):
    """It is read as an example of the format, so it has to look like one."""
    for name in ("decisions.json", "tasks.json"):
        raw = (DEMO / name).read_text(encoding="utf-8")
        json.loads(raw)                      # parses
        assert "\t" not in raw               # spaces, like everything else here
        assert raw.endswith("\n")
