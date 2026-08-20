"""`dg import` — adopting a store somebody prepared elsewhere.

The format was always accepted; what was missing was the diagnostics. A
document with one wrong key used to report `TypeError: Vertex.__init__() got an
unexpected keyword argument 'owner'`, and one whose `vertices` is an object
reported `string indices must be integers`. Both true, neither actionable — and
this is the format a person types by hand and an agent generates.

So most of what is guarded here is *messages*: that each one names the record,
the field, and what to write instead. The rest is the rule every bootstrap door
shares — a store `dg apply` would refuse is never written.
"""

import json

import pytest
from typer.testing import CliRunner

from dgraph import json_import, project
from dgraph.cli import app
from dgraph.model import Graph
from dgraph.tasks import TaskGraph

runner = CliRunner()

GOOD = {
    "areas": ["Search"],
    "vertices": [
        {"id": "D01", "title": "Exact or approximate?", "area": "Search",
         "status": "DECIDED"},
        {"id": "D02", "title": "Which index?", "area": "Search",
         "status": "OPEN"},
    ],
    "edges": [{"from": "D01", "to": ["D02"], "active": True,
               "answer": "Approximate.", "falsifier": "a scan lands under 50 ms",
               "source": "bench/x.md", "date": "2026-01-01"}],
}

GOOD_TASKS = {
    "areas": ["Search"],
    "tasks": [{"id": "T01", "title": "Build the index", "area": "Search",
               "status": "TODO", "because": "D02"},
              {"id": "T02", "title": "Wire the merge path", "area": "Search",
               "status": "TODO"}],
    "edges": [{"from": "T01", "to": ["T02"], "kind": "precedes"}],
}


@pytest.fixture
def empty(tmp_path, monkeypatch):
    """A project directory with no store in it yet."""
    monkeypatch.setattr(project, "_override", tmp_path)
    return tmp_path


def write(root, doc, name="candidate.json"):
    path = root / name
    path.write_text(doc if isinstance(doc, str)
                    else json.dumps(doc, indent=2), encoding="utf-8")
    return path


def dg(root, *args):
    return runner.invoke(app, ["--project", str(root), *args])


def refusal(root, doc, *, kind="import"):
    """The message from a refused import, with the ✗ banner stripped."""
    args = (kind,) if isinstance(kind, str) else kind
    res = dg(root, *args, str(write(root, doc)))
    assert res.exit_code == 1, res.output
    return " ".join(res.output.split())


# ---- it works ------------------------------------------------------------


def test_a_prepared_graph_becomes_the_store(empty):
    res = dg(empty, "import", str(write(empty, GOOD)))
    assert res.exit_code == 0 and "2 vertices, 1 edges" in res.output
    assert Graph.load(empty / "decisions.json").vertices.keys() == {"D01", "D02"}


def test_the_view_is_generated_too(empty):
    """A store with no view beside it is one `dg check` immediately warns about,
    which is a poor first impression for a command that just succeeded."""
    dg(empty, "import", str(write(empty, GOOD)))
    assert (empty / "decision-graph.md").exists()


def test_what_it_accepts_is_what_the_store_accepts(empty):
    """Built through `Graph.from_dict`, the path `load` uses. Two construction
    paths would be two schemas, and the one nobody ran would drift."""
    path = write(empty, GOOD)
    assert json_import.read(path, "decisions").to_dict() == \
        Graph.from_dict(GOOD).to_dict()


def test_a_prepared_backlog_becomes_the_task_store(empty):
    res = dg(empty, "task", "import", str(write(empty, GOOD_TASKS)))
    assert res.exit_code == 0 and "2 tasks, 1 edges" in res.output
    assert TaskGraph.load(empty / "tasks.json").tasks.keys() == {"T01", "T02"}


def test_a_link_to_a_decision_survives_the_import(empty):
    """`because` is carried through as written. Whether it names a decision
    that exists is a cross-store question, and `dg check`'s."""
    dg(empty, "task", "import", str(write(empty, GOOD_TASKS)))
    assert TaskGraph.load(empty / "tasks.json").tasks["T01"].because == "D02"


# ---- it refuses to overwrite --------------------------------------------


def test_it_will_not_land_on_an_existing_store(empty):
    dg(empty, "import", str(write(empty, GOOD)))
    res = dg(empty, "import", str(write(empty, GOOD)))
    assert res.exit_code == 1 and "already exists" in res.output


def test_force_does_not_mean_overwrite(empty):
    """`--force` means "I accept a graph that breaks invariants". Letting it
    also mean "discard the store I have" puts an irreversible act behind a flag
    reached for routinely."""
    dg(empty, "import", str(write(empty, GOOD)))
    res = dg(empty, "import", str(write(empty, GOOD)), "--force")
    assert res.exit_code == 1 and "already exists" in res.output


# ---- it refuses a graph `dg apply` would refuse --------------------------


BROKEN = {
    "areas": ["Search"],
    "vertices": [{"id": "D01", "title": "Root", "area": "Search",
                  "status": "DECIDED"},
                 {"id": "D02", "title": "Child", "area": "Search",
                  "status": "BLOCKED:D99"}],
    "edges": [{"from": "D01", "to": ["D02"], "active": True,
               "answer": "Yes.", "source": "x"}],
}


def test_an_invalid_graph_is_not_written(empty):
    """A bootstrap that writes a store `dg apply` would refuse plants, on day
    one, the contradiction the tool exists to prevent."""
    res = dg(empty, "import", str(write(empty, BROKEN)))
    assert res.exit_code == 1
    assert "blocked by unknown vertex D99" in res.output
    assert not (empty / "decisions.json").exists()


def test_force_writes_it_anyway_and_still_says_what_is_wrong(empty):
    res = dg(empty, "import", str(write(empty, BROKEN)), "--force")
    assert res.exit_code == 0
    assert "blocked by unknown vertex D99" in res.output
    assert (empty / "decisions.json").exists()


# ---- the diagnostics -----------------------------------------------------


def test_malformed_json_is_located(empty):
    msg = refusal(empty, '{"areas": ["A"], "vertices": [')
    assert "not valid JSON" in msg and "line 1" in msg


def test_a_document_that_is_not_an_object_says_what_a_store_looks_like(empty):
    assert "not an object" in refusal(empty, '[{"id": "D01"}]')


def test_a_missing_collection_names_the_key(empty):
    msg = refusal(empty, {"areas": ["A"], "edges": []})
    assert 'no "vertices" key' in msg and "not a decision store" in msg


def test_the_other_store_is_named_as_such(empty):
    """`dg import` on a tasks.json is the likeliest way to reach this message,
    and "no vertices key" is a poor way to be told you typed the wrong verb."""
    msg = refusal(empty, GOOD_TASKS)
    assert "the other store" in msg and "dg task import" in msg
    msg = refusal(empty, GOOD, kind=("task", "import"))
    assert "the other store" in msg and "dg import" in msg


def test_a_collection_that_is_not_a_list_says_so(empty):
    msg = refusal(empty, {"areas": ["A"], "vertices": {"D01": {}}, "edges": []})
    assert '"vertices" is an object' in msg and "must be a list" in msg


def test_an_unknown_field_is_refused_and_the_real_ones_listed(empty):
    """Refused, not dropped: dropping it silently loses whatever the writer
    meant by it."""
    doc = {"areas": ["A"], "vertices": [
        {"id": "D01", "title": "t", "area": "A", "status": "OPEN",
         "owner": "me"}], "edges": []}
    msg = refusal(empty, doc)
    assert 'decision D01 has "owner"' in msg
    assert "id, title, area, status (required)" in msg


def test_a_missing_field_names_the_record_by_its_id(empty):
    doc = {"areas": ["A"], "vertices": [{"id": "D01", "area": "A",
                                         "status": "OPEN"}], "edges": []}
    assert 'decision D01 has no "title"' in refusal(empty, doc)


def test_a_record_with_no_id_is_named_by_its_position(empty):
    doc = {"areas": ["A"], "vertices": [{"title": "t", "area": "A"}],
           "edges": []}
    assert "decision 0 (no id)" in refusal(empty, doc)


def test_an_edge_with_no_source_says_what_from_means(empty):
    doc = {"areas": ["A"],
           "vertices": [{"id": "D01", "title": "t", "area": "A",
                         "status": "OPEN"}],
           "edges": [{"to": ["D02"]}]}
    assert 'edge 0 has no "from"' in refusal(empty, doc)


def test_a_scalar_to_is_refused_rather_than_wrapped(empty):
    doc = {"areas": ["A"],
           "vertices": [{"id": "D01", "title": "t", "area": "A",
                         "status": "OPEN"}],
           "edges": [{"from": "D01", "to": "D02"}]}
    assert "must be a list of ids" in refusal(empty, doc)


def test_the_loaders_own_refusals_come_through_unchanged(empty):
    """A duplicate id and a task edge with no kind are already refused with
    messages written for a person; restating them here would be restating them
    less well."""
    doc = {"areas": ["A"], "vertices": [
        {"id": "D01", "title": "a", "area": "A", "status": "OPEN"},
        {"id": "D01", "title": "b", "area": "A", "status": "OPEN"}], "edges": []}
    assert "duplicate vertex id(s): D01" in refusal(empty, doc)

    doc = {"areas": ["A"], "tasks": [{"id": "T01", "title": "t", "area": "A"}],
           "edges": [{"from": "T01", "to": ["T02"]}]}
    msg = refusal(empty, doc, kind=("task", "import"))
    assert 'has no "kind"' in msg and "before edge kinds existed" in msg


def test_a_missing_file_is_a_clean_refusal_not_a_traceback(empty):
    res = dg(empty, "import", str(empty / "nope.json"))
    assert res.exit_code == 1 and "not imported" in res.output


# ---- `import-md` stays what it is ----------------------------------------


def test_import_md_still_refuses_a_document_in_another_dialect(empty):
    """The reframing was to the help and the docs. What it reads did not
    change, and the refusal must still name the section that failed."""
    doc = empty / "old.md"
    doc.write_text("## Search\n\n### D01 — A question?\n\nStill open.\n",
                   encoding="utf-8")
    res = dg(empty, "import-md", str(doc))
    assert res.exit_code == 1
    assert "not the dialect this importer reads" in " ".join(res.output.split())


def test_import_md_round_trips_a_view_this_tool_wrote(empty):
    """What the command is actually for: a store rebuilt from the view beside
    it. This is the property the name now claims and nothing tested."""
    dg(empty, "import", str(write(empty, GOOD)))
    before = Graph.load(empty / "decisions.json")
    view = (empty / "decision-graph.md").read_text(encoding="utf-8")
    (empty / "decisions.json").unlink()
    (empty / "old-view.md").write_text(view, encoding="utf-8")

    res = dg(empty, "import-md", str(empty / "old-view.md"))
    assert res.exit_code == 0, res.output
    after = Graph.load(empty / "decisions.json")
    assert after.vertices.keys() == before.vertices.keys()
    for vid, v in before.vertices.items():
        assert after.vertices[vid].status == v.status
        assert after.vertices[vid].title == v.title
    assert after.active_edge("D01").answer == before.active_edge("D01").answer


def test_an_import_ignores_the_scratch_files_the_way_init_does(empty, monkeypatch):
    """A store has just appeared, so the trays and locks beside it are scratch.
    The commit gate's advice ("it is gitignored, so committing now drops it")
    is only true if they actually are."""
    import subprocess
    subprocess.run(["git", "init", "-q", str(empty)], check=True)
    dg(empty, "import", str(write(empty, GOOD)))
    assert ".dgraph-*" in (empty / ".gitignore").read_text()

