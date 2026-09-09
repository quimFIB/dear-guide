"""What a person met driving a 400-record project through the editor doors
(pass 29, `AD-`). Each test is one claim a surface made that was false, kept
here rather than beside the module because the finding is about the surface
a *user* reads, and four of the five cross a module boundary.
"""

import json
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from dgraph import context, editor, pending, task_pending
from dgraph.cli import app
from dgraph.model import Graph
from dgraph.render import write
from dgraph.tasks import TaskGraph

runner = CliRunner()


def dg(root, *args, cols="200"):
    import os
    os.environ["COLUMNS"] = cols
    return runner.invoke(app, ["--project", str(root), *args])


def flat(out: str) -> str:
    """One line: the console may wrap at whatever width it was created at."""
    return " ".join(out.split())


# ---- AD-F1 · the task tray's resolver names the decision tray -------------

def test_a_task_tray_refusal_names_the_task_listing(task_store):
    """`dg task edit zzzz` and `dg task drop-op zzzz` used to send the person
    to `dg pending`, which lists the *other* tray. The resolver is shared; the
    hint must follow the tray it resolved against."""
    for cmd in (("task", "edit", "zzzz"), ("task", "drop-op", "zzzz")):
        r = dg(task_store, *cmd)
        assert r.exit_code == 1
        assert "`dg task pending`" in flat(r.output), r.output
        assert "`dg pending`" not in flat(r.output)


def test_a_task_tray_that_will_not_apply_names_the_task_edit_door(task_store):
    """`dg check --staged` on a malformed task op said `dg edit <id>` to fix —
    the decision tray's door."""
    pending.stage({"op": "add_task", "title": "no id", "area": "Alpha"},
                  task_pending.path())
    out = flat(dg(task_store, "check", "--staged").output)
    assert "`dg task edit <id>`" in out, out
    assert "`dg edit <id>`" not in out


def test_a_decision_tray_refusal_still_names_the_decision_listing(store):
    for cmd in (("edit", "zzzz"), ("drop", "zzzz")):
        r = dg(store, *cmd)
        assert r.exit_code == 1
        assert "`dg pending`" in r.output


# ---- AD-F3 · the amend buffers carry Tags -------------------------------

def _fake(monkeypatch, edit):
    def launch(path):
        path.write_text(edit(path.read_text(encoding="utf-8")), encoding="utf-8")
        return 0
    monkeypatch.setattr(editor, "launch", launch)


def test_amend_buffer_seeds_and_reads_back_tags(g, store, monkeypatch):
    """`dg amend --tag/--untag` and the add buffer's `** Tags` exist; the amend
    buffer had no Tags slot, so a tag could be filed by flag and never by the
    door built to correct how a record is filed (`D103`)."""
    gg = Graph.load()
    gg.vertices["D05"] = replace(gg.vertices["D05"], tags=["perf", "draft"])
    gg.save()
    g = Graph.load()
    seen = {}

    def edit(text):
        seen["t"] = text
        return text.replace("** Tags\nperf, draft\n", "** Tags\nperf, final\n")
    _fake(monkeypatch, edit)
    ops = editor.compose(g, "amend", vertex="D05")
    assert "** Tags\nperf, draft\n" in seen["t"]
    assert ops == [{"op": "set_fields", "vertex": "D05", "tags": ["perf", "final"]}]


def test_amend_buffer_leaves_tags_alone_when_untouched(g, store, monkeypatch):
    v = g.vertices["D05"]
    _fake(monkeypatch, lambda t: t.replace(f"** Title\n{v.title}\n",
                                           "** Title\nReworded\n"))
    ops = editor.compose(g, "amend", vertex="D05")
    assert ops == [{"op": "set_fields", "vertex": "D05", "title": "Reworded"}]


def test_amend_buffer_can_clear_every_tag(g, store, monkeypatch):
    gg = Graph.load()
    gg.vertices["D05"] = replace(gg.vertices["D05"], tags=["perf"])
    gg.save()
    g = Graph.load()
    _fake(monkeypatch, lambda t: t.replace("** Tags\nperf\n", "** Tags\n\n"))
    ops = editor.compose(g, "amend", vertex="D05")
    assert ops == [{"op": "set_fields", "vertex": "D05", "tags": []}]


def test_task_amend_buffer_seeds_and_reads_back_tags(tg, task_store, monkeypatch):
    import dgraph.task_editor as te
    tg.tasks["T02"].tags = ["perf"]
    tg.save(task_store / "tasks.json")
    tg = TaskGraph.load(task_store / "tasks.json")
    seen = {}

    def edit(text):
        seen["t"] = text
        return text.replace("** Tags\nperf\n", "** Tags\nperf, urgent\n")
    _fake(monkeypatch, edit)
    ops = te.compose_amend(tg, None, "T02")
    assert "** Tags\nperf\n" in seen["t"]
    assert ops == [{"op": "set_fields", "task": "T02", "tags": ["perf", "urgent"]}]


# ---- AD-F4 · the brief says a tray `dg apply` would refuse ----------------

@pytest.fixture
def both(store, task_store, g):
    write(g)
    return store


def test_brief_says_when_the_staged_batch_would_be_refused(both):
    """`STAGED BUT NOT APPLIED: 0 decision, 1 task` beside `CHECK: clean` sent
    the next session to a `dg apply` that refuses. The store *is* clean; the
    tray is the thing that is not, and the brief is where the tray is
    reported."""
    pending.stage({"op": "add_task", "id": "T09", "title": "bad",
                   "area": "Alpha", "because": ["D04"],
                   "evidence_for": "D04"}, task_pending.path())
    out = flat(dg(both, "brief").output)
    assert "would refuse" in out, out
    assert "`dg check --staged`" in out
    d = json.loads(dg(both, "brief", "--json").output)
    assert d["staged_refused"] >= 1


def test_brief_is_silent_about_a_tray_that_applies(both):
    pending.stage({"op": "add_task", "id": "T09", "title": "fine",
                   "area": "Alpha"}, task_pending.path())
    out = dg(both, "brief").output
    assert "STAGED BUT NOT APPLIED: 0 decision, 1 task" in out
    assert "would refuse" not in out
    assert json.loads(dg(both, "brief", "--json").output)["staged_refused"] == 0


# ---- AD-F5 · ids order by number on every listing -----------------------

def _hundred(store):
    gg = Graph.load()
    for i in (98, 99, 100, 101):
        gg.vertices[f"D{i}"] = replace(gg.vertices["D05"], id=f"D{i}",
                                       title=f"Question {i}")
    gg.save()


def _ids(text, prefix="D"):
    import re
    return re.findall(rf"\b{prefix}\d+\b", text)


def test_show_brief_and_find_list_ids_by_number(store, g):
    """Past 99 the ids sorted as strings: D100 before D71 on every listing,
    including the owner's own graph at `D104`."""
    _hundred(store)
    for args in (("show",), ("brief",), ("find", "Question", "--ids")):
        ids = [i for i in _ids(dg(store, *args).output) if int(i[1:]) >= 98]
        seen = []
        for i in ids:
            if i not in seen:
                seen.append(i)
        assert seen == ["D98", "D99", "D100", "D101"], (args, seen)


def test_task_listings_order_by_number(task_store, tg):
    for i in (99, 100):
        tg.tasks[f"T{i}"] = replace(tg.tasks["T02"], id=f"T{i}", title=f"Work {i}")
    tg.save(task_store / "tasks.json")
    for args in (("task",), ("task", "tree")):
        ids = [i for i in _ids(dg(task_store, *args).output, "T")
               if int(i[1:]) >= 99]
        seen = []
        for i in ids:
            if i not in seen:
                seen.append(i)
        assert seen == ["T99", "T100"], (args, seen)


def test_the_close_buffer_offers_opens_boxes_by_number(store, g, monkeypatch):
    _hundred(store)
    g = Graph.load()
    text = editor.render_close(g, "D05")
    import re
    boxes = [re.search(r"(D\d+)", ln).group(1)
             for ln in text.splitlines() if ln.startswith("- [")]
    tail = [b for b in boxes if int(b[1:]) >= 98]
    assert tail == ["D98", "D99", "D100", "D101"]


# ---- AD-F6 · `dg why` draws arrows only where there is an edge ----------

def test_why_does_not_draw_an_arrow_between_ancestors_that_share_no_edge(store, g):
    """A diamond: D07 rests on D02 and D03, both children of D01. The chain is
    every ancestor by depth — D01, D02, D03 — and `D02 → D03` is not an edge.
    The arrow line used to assert it was."""
    gg = Graph.load()
    gg.vertices["D07"] = replace(gg.vertices["D05"], id="D07", title="Diamond")
    from dgraph.model import Edge
    gg.edges.append(Edge(src="D02", to=["D04", "D07"], active=True,
                         answer="Second answer.", falsifier="x",
                         source="discussion", date="2026-01-02"))
    gg.edges = [e for e in gg.edges if not (e.src == "D02" and e.to == ["D04"])]
    gg.edges = [e if e.src != "D03" else replace(e, to=["D07"]) for e in gg.edges]
    gg.save()
    out = dg(store, "why", "D07").output
    chain = [ln for ln in out.splitlines() if ln.startswith("CHAIN")][0]
    assert "D02 → D03" not in chain, chain
    assert "D01 → D02" in chain and "D03 → D07" in chain, chain
