"""Tags: the free labels a record carries beside its area (`D95`, `T93`).

A tag is a word for a person, not a claim and not an address. What these
tests pin is the definition of done: an untouched store loads and saves
unchanged; `--tag` on add and amend in both stores supersedes nothing;
`tags:` matches exactly in both lenses; a new tag is judged by the area
similarity guard at the staging door with `--new-tag` as the override; and
`dg find 'area:X tags:y' --subgraph --hops 0` prints the induced slice.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from dgraph import json_import, pending, project, tags, task_pending
from dgraph.cli import app
from dgraph.model import Graph
from dgraph.tasks import TaskGraph
from tests.conftest import FIXTURE, TASK_FIXTURE

runner = CliRunner()


@pytest.fixture
def both(tmp_path, monkeypatch):
    (tmp_path / "decisions.json").write_text(json.dumps(FIXTURE, indent=2),
                                             encoding="utf-8")
    (tmp_path / "tasks.json").write_text(json.dumps(TASK_FIXTURE, indent=2),
                                         encoding="utf-8")
    monkeypatch.setattr(project, "_override", tmp_path)
    monkeypatch.setenv("COLUMNS", "300")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("DG_AGENT", raising=False)
    monkeypatch.delenv("DG_AREA", raising=False)
    return tmp_path


@pytest.fixture
def run(both):
    def go(*args):
        return runner.invoke(app, ["--project", str(both), *args])
    return go


def _apply(run):
    r = run("apply")
    assert r.exit_code == 0, r.output
    return r


def _g(both) -> Graph:
    return Graph.load(both / "decisions.json")


def _tg(both) -> TaskGraph:
    return TaskGraph.load(both / "tasks.json")


# ---- the field ------------------------------------------------------------


def test_an_untouched_store_loads_and_saves_unchanged():
    """The first line of `T93`: a store written before tags existed is not
    rewritten by a version that knows them. Absent, never `[]`."""
    for cls, raw in ((Graph, FIXTURE), (TaskGraph, TASK_FIXTURE)):
        out = cls.from_dict(json.loads(json.dumps(raw))).to_dict()
        assert out == raw
        assert not any("tags" in rec for rec in
                       out.get("vertices") or out.get("tasks"))


def test_a_stored_tag_list_round_trips():
    raw = json.loads(json.dumps(FIXTURE))
    raw["vertices"][0]["tags"] = ["perf", "recall"]
    g = Graph.from_dict(raw)
    assert g.vertices["D01"].tags == ["perf", "recall"]
    assert g.to_dict()["vertices"][0]["tags"] == ["perf", "recall"]
    traw = json.loads(json.dumps(TASK_FIXTURE))
    traw["tasks"][0]["tags"] = ["perf"]
    assert TaskGraph.from_dict(traw).tasks["T01"].tags == ["perf"]


def test_clean_and_fault():
    """A flag's spelling is tidied; a body's shape is judged."""
    assert tags.clean(["a, b", "b", "c"]) == ["a", "b", "c"]
    assert tags.clean("x") == ["x"]
    assert tags.clean(None) == []
    assert tags.fault(["a"]) is None
    assert tags.fault("a") == "tags is a list of words"
    assert tags.fault(["a", "a"]) == "a tag is listed once"
    assert tags.fault([""]) == "a tag is a non-empty word"
    assert "comma" in tags.fault(["a,b"])


def test_import_accepts_the_field(tmp_path):
    raw = json.loads(json.dumps(FIXTURE))
    raw["vertices"][0]["tags"] = ["perf"]
    path = tmp_path / "prepared.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert json_import.read(path, "decisions").graph.vertices["D01"].tags == ["perf"]


# ---- filing, in both stores -------------------------------------------------


def test_add_files_under_tags_in_both_stores(run, both):
    r = run("add", "--id", "D07", "-t", "Shape", "--area", "Alpha",
            "--tag", "perf,recall", "--no-edit")
    assert r.exit_code == 0, r.output
    r = run("task", "add", "--id", "T05", "-t", "Sweep", "--area", "Alpha",
            "--tag", "perf", "--tag", "latency", "--no-edit")
    assert r.exit_code == 0, r.output
    _apply(run)
    assert _g(both).vertices["D07"].tags == ["perf", "recall"]
    assert _tg(both).tasks["T05"].tags == ["perf", "latency"]
    # Stored as the list, and nowhere else: a tag registers no vocabulary.
    stored = json.loads((both / "decisions.json").read_text())
    assert [v for v in stored["vertices"] if v["id"] == "D07"][0]["tags"] == ["perf", "recall"]
    assert "tags" not in stored


def test_amend_is_a_set_and_supersedes_nothing(run, both):
    """`--tag` adds, `--untag` removes, `--clear-tags` empties — and the op
    is the same `set_fields` a retitle is, so no edge is archived."""
    run("add", "--id", "D07", "-t", "Shape", "--area", "Alpha",
        "--tag", "perf,recall", "--no-edit")
    _apply(run)
    edges_before = [e for e in _g(both).edges if not e.active]

    r = run("amend", "D07", "--untag", "recall", "--tag", "latency")
    assert r.exit_code == 0, r.output
    assert "perf, recall → perf, latency" in r.output
    _apply(run)
    assert _g(both).vertices["D07"].tags == ["perf", "latency"]

    r = run("amend", "D07", "--untag", "recall")
    assert r.exit_code == 1
    assert "not filed under recall" in r.output

    r = run("amend", "D07", "--tag", "perf")
    assert r.exit_code == 1, "adding a tag already held is a no-op, refused"
    assert "already has those tags" in r.output

    r = run("amend", "D07", "--clear-tags")
    assert r.exit_code == 0, r.output
    _apply(run)
    assert _g(both).vertices["D07"].tags == []
    assert "tags" not in [v for v in json.loads(
        (both / "decisions.json").read_text())["vertices"] if v["id"] == "D07"][0]
    assert [e for e in _g(both).edges if not e.active] == edges_before

    r = run("amend", "D07", "--clear-tags", "--untag", "x")
    assert r.exit_code == 2


def test_task_amend_is_the_same_set(run, both):
    r = run("task", "amend", "T02", "--tag", "perf")
    assert r.exit_code == 0, r.output
    _apply(run)
    assert _tg(both).tasks["T02"].tags == ["perf"]
    r = run("task", "amend", "T02", "--untag", "perf", "--tag", "recall")
    assert r.exit_code == 0, r.output
    _apply(run)
    assert _tg(both).tasks["T02"].tags == ["recall"]


def test_amend_does_not_touch_tags_it_was_not_asked_about(run, both):
    run("add", "--id", "D07", "-t", "Shape", "--area", "Alpha",
        "--tag", "perf", "--no-edit")
    _apply(run)
    r = run("amend", "D07", "--title", "Index shape")
    assert r.exit_code == 0, r.output
    ops = pending.load(both / project.PENDING_NAME)
    assert "tags" not in ops[-1]
    _apply(run)
    assert _g(both).vertices["D07"].tags == ["perf"]


# ---- the staging door -------------------------------------------------------


def test_a_lookalike_tag_is_refused_naming_what_it_resembles(run, both):
    run("add", "--id", "D07", "-t", "Shape", "--area", "Alpha",
        "--tag", "perf", "--no-edit")
    _apply(run)
    r = run("task", "add", "--id", "T05", "-t", "Sweep", "--area", "Alpha",
            "--tag", "Perf", "--no-edit")
    assert r.exit_code == 1, r.output
    assert "'Perf' is new, and close to tags already in use" in r.output
    assert "perf" in r.output and "--new-tag" in r.output
    r = run("task", "add", "--id", "T05", "-t", "Sweep", "--area", "Alpha",
            "--tag", "Perf", "--new-tag", "--no-edit")
    assert r.exit_code == 0, r.output


def test_the_guard_reads_both_trays(run, both):
    """A decision staged under `perf` and its work under `perff` a minute
    later land in different trays; the typo is caught all the same."""
    run("add", "--id", "D07", "-t", "Shape", "--area", "Alpha",
        "--tag", "perf", "--no-edit")
    r = run("task", "add", "--id", "T05", "-t", "Sweep", "--area", "Alpha",
            "--tag", "perff", "--no-edit")
    assert r.exit_code == 1, r.output
    assert "perff" in r.output and "perf" in r.output


def test_a_genuinely_new_tag_is_silent_and_no_policy_refuses_it(run, both, monkeypatch):
    """`D95`: no `$DG_TAG`. An agent under `$DG_AREA=strict` may still coin a
    tag, because the area policy is about areas."""
    monkeypatch.setenv("DG_AGENT", "scout")
    monkeypatch.setenv("DG_AREA", "strict")
    r = run("add", "--id", "D07", "-t", "Shape", "--area", "Alpha",
            "--tag", "latency", "--no-edit")
    assert r.exit_code == 0, r.output


def test_amend_to_a_lookalike_is_judged_too(run, both):
    run("add", "--id", "D07", "-t", "Shape", "--area", "Alpha",
        "--tag", "perf", "--no-edit")
    _apply(run)
    r = run("task", "amend", "T02", "--tag", "perff")
    assert r.exit_code == 1, r.output
    assert "--new-tag" in r.output
    r = run("task", "amend", "T02", "--tag", "perff", "--new-tag")
    assert r.exit_code == 0, r.output


def test_an_op_arriving_as_data_is_held_to_the_shape(both):
    g = Graph.load(both / "decisions.json")
    with pytest.raises(pending.ApplyError, match="tags: tags is a list"):
        pending.vet(g, {"op": "add_vertex", "id": "D07", "title": "x",
                        "area": "Alpha", "status": "OPEN", "tags": "perf"})
    with pytest.raises(pending.ApplyError, match="listed once"):
        pending.vet(g, {"op": "set_fields", "vertex": "D01",
                        "tags": ["a", "a"]})
    tg = TaskGraph.load(both / "tasks.json")
    with pytest.raises(pending.ApplyError, match="non-empty word"):
        task_pending.vet(tg, {"op": "add_task", "id": "T05", "title": "x",
                              "area": "Alpha", "tags": [""]})


def test_a_landed_add_reads_as_landed_only_with_its_tags(both):
    """`_same_vertex` compares what the op wrote, tags included."""
    g = Graph.load(both / "decisions.json")
    v = g.vertices["D01"]
    op = {"op": "add_vertex", "id": "D01", "title": v.title, "area": v.area,
          "status": v.status, "note": v.note}
    assert pending._same_vertex(g, op)
    assert not pending._same_vertex(g, {**op, "tags": ["perf"]})


# ---- the query term ---------------------------------------------------------


def test_tags_match_exactly_in_both_lenses(run, both):
    run("add", "--id", "D07", "-t", "Shape", "--area", "Alpha",
        "--tag", "perf,recall", "--no-edit")
    run("task", "add", "--id", "T05", "-t", "Sweep", "--area", "Beta",
        "--tag", "perf", "--no-edit")
    _apply(run)
    r = run("find", "tags:perf", "--ids")
    assert r.exit_code == 0, r.output
    assert r.output.split() == ["D07", "T05"]
    r = run("find", "tags:per", "--ids")
    assert r.exit_code == 1, "a tag is a word, not a prefix"
    r = run("find", "tags:recall -tags:perf", "--ids")
    assert r.exit_code == 1


def test_area_and_tag_induce_the_slice(run, both):
    """The question that opened `D95`: the subgraph one area and one tag
    induce, at zero hops, from both stores."""
    run("add", "--id", "D07", "-t", "Shape", "--area", "Alpha",
        "--tag", "perf", "--no-edit")
    run("add", "--id", "D08", "-t", "Other", "--area", "Beta",
        "--tag", "perf", "--no-edit")
    run("task", "add", "--id", "T05", "-t", "Sweep", "--area", "Alpha",
        "--tag", "perf", "--because", "D07", "--no-edit")
    run("task", "add", "--id", "T06", "-t", "Untagged", "--area", "Alpha",
        "--because", "D07", "--no-edit")
    _apply(run)
    r = run("find", "area:Alpha tags:perf", "--subgraph", "--hops", "0")
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert [v["id"] for v in out["decisions"]["vertices"]] == ["D07"]
    assert [t["id"] for t in out["tasks"]["tasks"]] == ["T05"]
    assert out["tasks"]["tasks"][0]["tags"] == ["perf"]


# ---- the readings (`T94`) ---------------------------------------------------


def _tagged(run):
    run("add", "--id", "D07", "-t", "Shape", "--area", "Alpha",
        "--tag", "perf,recall", "--no-edit")
    run("task", "add", "--id", "T05", "-t", "Sweep", "--area", "Alpha",
        "--tag", "perf", "--no-edit")
    _apply(run)


def test_listings_print_the_tags_after_the_area(run, both):
    _tagged(run)
    r = run("show")
    assert r.exit_code == 0, r.output
    line = [ln for ln in r.output.splitlines() if ln.strip().startswith("D07")][0]
    assert "Alpha · perf · recall" in line
    r = run("task")
    line = [ln for ln in r.output.splitlines() if ln.strip().startswith("T05")][0]
    assert "Alpha · perf" in line
    # A record with none reads as it always did.
    line = [ln for ln in r.output.splitlines() if ln.strip().startswith("T02")][0]
    assert line.rstrip().endswith("Alpha")


def test_node_panels_give_tags_a_line_only_when_held(run, both):
    _tagged(run)
    r = run("node", "D07")
    assert "tags        perf, recall" in r.output
    assert "tags" not in run("node", "D01").output.split("area")[1].split("depends")[0]
    r = run("task", "node", "T05")
    assert "tags        perf" in r.output


def test_dg_tags_counts_both_stores_in_one_table(run, both):
    r = run("tags")
    assert r.exit_code == 0, r.output
    assert "no tags yet" in r.output
    _tagged(run)
    r = run("tags")
    assert r.exit_code == 0, r.output
    rows = {ln.split("│")[1].strip(): [c.strip() for c in ln.split("│")[2:-1]]
            for ln in r.output.splitlines() if ln.startswith("│")}
    assert rows["perf"] == ["1", "1", "2"]
    assert rows["recall"] == ["1", "0", "1"]


def test_dg_tags_rename_refiles_across_both_stores(run, both):
    _tagged(run)
    r = run("tags", "rename", "perf", "performance")
    assert r.exit_code == 0, r.output
    assert "staged 2 op(s): perf → performance" in r.output
    _apply(run)
    assert _g(both).vertices["D07"].tags == ["performance", "recall"]
    assert _tg(both).tasks["T05"].tags == ["performance"]
    r = run("tags", "rename", "perf", "x")
    assert "nothing carries perf" in r.output
    # Into a tag the record already holds: the old one simply goes.
    r = run("tags", "rename", "recall", "performance")
    assert r.exit_code == 0, r.output
    _apply(run)
    assert _g(both).vertices["D07"].tags == ["performance"]
    assert run("tags", "prune").exit_code == 2, "no registry, so nothing to prune"


def test_the_markdown_view_carries_tags_and_import_md_reads_them_back(run, both, tmp_path):
    _tagged(run)
    r = run("render")
    assert r.exit_code == 0, r.output
    view = (both / "decision-graph.md").read_text(encoding="utf-8")
    assert "- **Tags:** perf, recall" in view
    r = run("task", "render")
    assert "- **Tags:** perf" in (both / "tasks.md").read_text(encoding="utf-8")
    # A store with no tags renders as it did before the field existed.
    assert "Tags" not in view.split("### D07")[0]

    rebuilt = tmp_path / "rebuilt"
    rebuilt.mkdir()
    r = runner.invoke(app, ["--project", str(rebuilt), "import-md",
                            str(both / "decision-graph.md")])
    assert r.exit_code == 0, r.output
    got = Graph.load(rebuilt / "decisions.json")
    assert {v.id: v.tags for v in got.vertices.values()} == \
        {v.id: v.tags for v in _g(both).vertices.values()}


def test_the_editor_buffers_take_a_tags_field(both):
    from dgraph import editor, task_editor
    g = Graph.load(both / "decisions.json")
    ops = editor._parse_add(g, {"id": "D07", "title": "Shape", "area": "Alpha",
                                "tags": "perf, recall"})
    assert ops[0]["tags"] == ["perf", "recall"]
    assert "** Tags" in editor.render_add(g, {"tags": ["perf"]})
    tg = TaskGraph.load(both / "tasks.json")
    ops = task_editor._parse_add(tg, g, {"id": "T05", "title": "Sweep",
                                         "area": "Alpha", "tags": "perf"})
    assert ops[0]["tags"] == ["perf"]
