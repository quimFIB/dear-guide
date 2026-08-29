"""Behaviour of the tool: model queries, rendering, staging, propagation, apply."""

import json
import os
from dataclasses import replace

import pytest

from dgraph import pending, project
from dgraph.model import Graph
from dgraph.render import render, write
from tests.conftest import bare

# ---- model ---------------------------------------------------------------


def test_fixture_is_valid(g):
    assert g.validate() == []


def test_dependency_is_derived_from_edges(g):
    assert g.depends("D02") == ["D01"]
    assert g.depends("D01") == []
    assert g.children("D01") == ["D02", "D03"]


def test_superseded_edges_do_not_create_dependencies(g):
    """The inactive D01 edge must not contribute to the live structure."""
    assert len(g.history("D01")) == 1
    assert g.active_edge("D01").answer == "The root answer."
    assert g.children("D01") == ["D02", "D03"]


def test_payload_less_edge_is_a_dependency_without_a_decision(g):
    e = g.active_edge("D05")
    assert e is not None and not e.decided
    assert g.depends("D06") == ["D05"]


def test_terminal_decision(g):
    assert g.active_edge("D03").terminal
    assert g.children("D03") == []


def test_descendants_and_depth(g):
    assert g.descendants("D01") == {"D02", "D03", "D04", "D05", "D06"}
    assert g.depth("D01") == 0
    assert g.depth("D06") == 4


def test_frontier(g):
    assert g.frontier() == ["D05", "D06"]


def test_waiting_on_is_the_unsettled_premises(g):
    """One implementation, three callers: `dg show`, the propagation check, and
    the brief. D06 is BLOCKED on D05, which is OPEN; D02 rests on a settled D01.
    """
    assert g.waiting_on("D06") == ["D05"]
    assert g.waiting_on("D02") == []
    assert g.waiting_on("D01") == []


def test_path(g):
    assert g.path("D01", "D05") == ["D01", "D02", "D04", "D05"]
    assert g.path("D03", "D05") is None


# ---- validation ----------------------------------------------------------


def _check(g, name):
    return [v for v in g.validate() if v.check == name]


def test_detects_dangling_edge(g):
    g.active_edge("D02").to.append("D99")
    assert _check(g, "no_dangling_refs")


def test_a_dangling_source_into_a_decided_vertex_is_reported_not_fatal(g):
    """Audit A1. `depends()` used to return the unknown source, and the
    propagation walk then crashed on `vertices[p]` — validation died on
    exactly the hand-edit damage it exists to report, and the commit gate
    read the crash as "no verdict" and failed open."""
    from dgraph.model import Edge
    g.edges.append(Edge(src="D99", to=["D02"], active=True))
    assert _check(g, "no_dangling_refs")          # reported, no KeyError
    assert not _check(g, "propagation")           # D99 is not a premise


def test_load_refuses_duplicate_vertex_ids(store):
    """Audit A5. The vertex dict collapsed duplicates silently (last one wins),
    so a hand-edit or a bad merge lost a decision and `dg check` called the
    graph clean — the declared "unique ids" invariant could never fire."""
    raw = json.loads((store / "decisions.json").read_text(encoding="utf-8"))
    raw["vertices"].append(dict(raw["vertices"][0], title="a second D01"))
    (store / "decisions.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="D01"):
        Graph.load(store / "decisions.json")


def test_a_dangling_source_above_a_provisional_vertex_is_reported_not_fatal(g):
    """Same crash, other path: `provisional_because` walks ancestors."""
    from dataclasses import replace

    from dgraph.model import Edge
    g.vertices["D02"] = replace(g.vertices["D02"], status="PROVISIONAL")
    g.edges.append(Edge(src="D99", to=["D02"], active=True))
    assert _check(g, "no_dangling_refs")


def test_detects_two_active_edges(g):
    from dgraph.model import Edge
    g.edges.append(Edge(src="D01", to=["D04"], active=True))
    assert _check(g, "one_active_edge")


def test_detects_missing_falsifier(g):
    g.active_edge("D01").falsifier = None
    assert _check(g, "decided_complete")


def test_detects_unpropagated_reopen(g):
    from dataclasses import replace
    g.vertices["D02"] = replace(g.vertices["D02"], status="REOPENED")
    hits = _check(g, "propagation")
    assert hits and "D04" in str(hits[0])


def test_detects_stale_block(g):
    from dataclasses import replace
    g.vertices["D05"] = replace(g.vertices["D05"], status="DECIDED")
    assert _check(g, "stale_block")


# ---- a block is a dependency ---------------------------------------------
#
# `BLOCKED:D05` asserts that this vertex rests on D05. Dependency is the graph
# structure and never a second copy in a status field, so the two have to
# agree — and before this check they could disagree in silence.


def test_detects_a_block_that_rests_on_nothing(g):
    """D06 is BLOCKED:D05 in the fixture. Sever the edge and the status is a
    claim the graph does not hold."""
    g.active_edge("D05").to.remove("D06")
    hits = _check(g, "block_is_a_premise")
    assert hits and "D06" in str(hits[0]) and "D05" in str(hits[0])
    assert hits[0].blocking


def test_a_backed_block_is_quiet(g):
    assert not _check(g, "block_is_a_premise")


def test_an_unbacked_block_is_reported_once_not_twice(g):
    """A blocker that is both settled and not a premise is one contradiction,
    not two: whether it has settled cannot matter if the block was never real,
    so `stale_block` stays quiet and the more fundamental fault is named."""
    from dataclasses import replace
    g.active_edge("D05").to.remove("D06")
    g.vertices["D05"] = replace(g.vertices["D05"], status="DECIDED")
    assert _check(g, "block_is_a_premise")
    assert not _check(g, "stale_block")


def test_an_unknown_blocker_is_not_reported_as_unbacked(g):
    """`status_legal` already names it, and a second finding about the same id
    would send the reader looking for an edge that could never exist."""
    from dataclasses import replace
    g.vertices["D06"] = replace(g.vertices["D06"], status="BLOCKED:D99")
    assert _check(g, "status_legal")
    assert not _check(g, "block_is_a_premise")


def test_adding_a_blocked_vertex_records_the_dependency(g, store):
    """Every route in goes through this op, so the status and the structure
    agree by construction rather than by each caller remembering."""
    out = pending.apply_all(g, [{"op": "add_vertex", "id": "D07", "title": "x",
                                 "area": "Alpha", "status": "BLOCKED:D05"}])
    assert out.depends("D07") == ["D05"]
    assert not [v for v in out.validate() if v.check == "block_is_a_premise"]


def test_a_blocked_vertex_naming_an_unknown_blocker_invents_no_edge(g):
    """The op must not manufacture an edge to an id that does not resolve —
    that would turn a clear `status_legal` finding into a dangling reference."""
    from dgraph.pending import _apply_one
    import copy
    out = copy.deepcopy(g)
    _apply_one(out, {"op": "add_vertex", "id": "D07", "title": "x",
                     "area": "Alpha", "status": "BLOCKED:D99"})
    assert not [e for e in out.edges if "D07" in e.to]
    assert _check(out, "status_legal")


def test_detects_cycle(g):
    g.active_edge("D04").to.append("D01")
    assert _check(g, "acyclic")


# ---- rendering -----------------------------------------------------------


def test_render_is_deterministic(g):
    assert render(g) == render(Graph.load())


def test_render_round_trips(g, tmp_path):
    p = tmp_path / "copy.json"
    g.save(p)
    assert Graph.load(p).to_dict() == g.to_dict()


def test_save_is_stable(g, tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    g.save(a)
    Graph.load(a).save(b)
    assert a.read_text() == b.read_text()


def test_table_cells_survive_pipes_and_newlines(g):
    """Audit C1. A `|` in a title split the index row; a multi-line `why` —
    routine, since reopens are composed in an editor — broke the superseded
    table for every row after it. Both now render as one row per record."""
    from dataclasses import replace
    g.vertices["D05"] = replace(g.vertices["D05"], title="Tokens | subwords?")
    ops = pending.expand(g, {"op": "reopen", "vertex": "D01",
                             "why": "first line\nsecond line"})
    out = pending.apply_all(g, ops)
    text = render(out)
    assert "| Tokens \\| subwords? |" in text
    assert "first line<br>second line" in text
    # the raw newline survives only as prose (the vertex's note); in the
    # superseded table no cell spills onto a new row
    sup = text[text.index("## Superseded"):]
    assert "\nsecond line" not in sup


def test_untagged_prose_keeps_markdown_meaning(g):
    """The other half of provenance: a close staged without a tag — the web
    form, an agent, `md_import` — is markdown, and its single stars must stay
    italic in the view, exactly as before."""
    out = pending.apply_all(g, pending.expand(g, {
        "op": "close", "vertex": "D05",
        "answer": "This stays *italic* in the view.",
        "source": "s", "falsifier": "f", "to": [], "date": "2026-02-01",
    }))
    assert out.active_edge("D05").format is None
    assert "This stays *italic* in the view." in render(out)


def test_redecide_without_a_summary_still_fills_replaced_by(g):
    """Audit C3. Without `--summary` the reversal record said "(undecided)"
    forever about a question that has an answer; the answer's first line now
    stands in, the same default `reopen` uses for the superseded side."""
    ops = pending.expand(g, {"op": "reopen", "vertex": "D01", "why": "x"})
    out = pending.apply_all(g, ops)
    out = pending.apply_all(out, [
        {"op": "close", "vertex": "D01",
         "answer": "The corrected answer.\nWith supporting detail.",
         "source": "s", "falsifier": "f", "to": ["D02", "D03"],
         "date": "2026-03-01"},
    ] + [{"op": "set_status", "vertex": v, "status": "DECIDED"}
         for v in ("D02", "D03", "D04")])
    hist = out.history("D01")
    assert hist[1].replaced_by == "The corrected answer."
    # a record that already names its replacement is never overwritten
    assert hist[0].replaced_by == "the root answer"


def test_a_deep_chain_does_not_hit_the_recursion_limit():
    """Audit C7. `depth` and the cycle check were recursive; a chain past the
    interpreter's limit (default 1000) crashed layout and — worse — the
    validator, which must never die on a legal graph."""
    from dgraph.model import Edge, Graph, Vertex
    g = Graph(areas=["A"])
    n = 1100
    ids = [f"D{i:04d}" for i in range(1, n + 1)]
    for vid in ids:
        g.vertices[vid] = Vertex(id=vid, title="q", area="A", status="OPEN")
    for a, b in zip(ids, ids[1:]):
        g.edges.append(Edge(src=a, to=[b]))
    assert g.depth(ids[-1]) == n - 1
    assert not [x for x in g.validate() if x.check == "acyclic"]


def test_view_contains_every_vertex_and_the_frontier(g, store):
    out = render(g)
    for vid in g.vertices:
        assert f"### {vid} —" in out
    assert "D05, D06." in out
    assert "older answer" in out  # superseded history survives


def test_write_targets_the_project(g, store):
    write(g)
    assert (store / "decision-graph.md").exists()


def test_org_prose_is_converted_in_the_view(g, store):
    """The store keeps what was typed; the view converts. See dgraph/orgmd.py."""
    g.active_edge("D01").answer = (
        "Per [[file:report/x.md][the sweep]].\n\n| opt | ppl |\n|-----+-----|\n| 32k | 8.1 |"
    )
    out = render(g)
    assert "[the sweep](report/x.md)" in out
    assert "|-----|-----|" in out
    assert "[[file:" not in out


def test_sections_carry_an_anchor_so_dg_links_resolve(g, store):
    out = render(g)
    for vid in g.vertices:
        assert f'<a id="{vid.lower()}"></a>' in out
    g.active_edge("D02").answer = "Rests on [[dg:D01][D01]]."
    assert "[D01](#d01)" in render(g)


def test_render_stays_stable_with_org_prose(g, store):
    """`stale_view` compares the file to a fresh render on every check, so the
    conversion must be deterministic and idempotent."""
    g.active_edge("D01").answer = "A [[dg:D02][link]] and =code= and a\n| t |\n|---+|\n| 1 |"
    once = render(g)
    assert render(Graph.load()) == render(Graph.load())
    assert render(g) == once


# ---- staging -------------------------------------------------------------


def test_staging_round_trips(store):
    assert pending.load() == []
    pending.stage({"op": "set_status", "vertex": "D05", "status": "OPEN"})
    pending.stage({"op": "set_status", "vertex": "D06", "status": "OPEN"})
    assert len(pending.load()) == 2
    assert pending.drop(0)["vertex"] == "D05"      # the op removed, not what is left
    assert [o["vertex"] for o in pending.load()] == ["D06"]
    pending.clear()
    assert pending.load() == [] and not project.find().pending.exists()


def test_drop_returns_the_op_it_removed_not_the_remainder(store):
    """The caller's job is to say what went, and it cannot re-read the tray to
    find out — by then the op is gone. So `drop` hands it back from under the
    lock. Audit F29."""
    a = {"op": "set_status", "vertex": "D05", "status": "OPEN"}
    b = {"op": "set_status", "vertex": "D06", "status": "OPEN"}
    pending.stage(a)
    pending.stage(b)
    assert bare(pending.drop(1)) == b
    assert bare(pending.load()) == [a]


# ---- propagation ---------------------------------------------------------


def test_reopen_propagates_to_exactly_the_decided_descendants(g):
    ops = pending.expand(g, {"op": "reopen", "vertex": "D01", "why": "x"})
    marked = {o["vertex"] for o in ops if o["op"] == "set_status"}
    assert marked == {"D02", "D03", "D04"}  # D05 is OPEN, D06 BLOCKED
    assert all(o["status"] == "PROVISIONAL"
               for o in ops if o["op"] == "set_status")


def test_reopen_keeps_dependencies_but_drops_the_answer(g):
    ops = pending.expand(g, {"op": "reopen", "vertex": "D01", "why": "x"})
    out = pending.apply_all(g, ops)
    assert out.vertices["D01"].status == "REOPENED"
    assert out.children("D01") == g.children("D01")
    assert out.active_edge("D01").answer is None
    assert len(out.history("D01")) == 2


def test_unpropagated_reopen_is_rejected(g):
    with pytest.raises(pending.ApplyError, match="propagation"):
        pending.apply_all(g, [{"op": "reopen", "vertex": "D01", "why": "x"}])


def test_settling_a_vertex_releases_what_was_blocked_on_it(g):
    """The mirror of reopen propagation: D06 is BLOCKED:D05, so closing D05
    must stage D06 -> OPEN rather than leaving a stale block for apply to
    reject."""
    ops = pending.expand(g, {"op": "close", "vertex": "D05", "answer": "a",
                             "source": "s", "falsifier": "f", "to": []})
    assert [(o["vertex"], o["status"]) for o in ops if o["op"] == "set_status"] \
        == [("D06", "OPEN")]
    assert all(o.get("derived_from") == "D05"
               for o in ops if o["op"] == "set_status")


def test_expanded_close_applies_without_manual_help(g):
    """Before the fix this batch needed a hand-written set_status to survive."""
    ops = pending.expand(g, {"op": "close", "vertex": "D05", "answer": "a",
                             "source": "s", "falsifier": "f", "to": [],
                             "date": "2026-02-01"})
    out = pending.apply_all(g, ops)
    assert out.vertices["D05"].status == "DECIDED"
    assert out.vertices["D06"].status == "OPEN"
    assert out.validate() == []


def test_unexpanded_close_still_fails_the_stale_block(g):
    """The invariant is what makes the propagation load-bearing; keep it sharp."""
    with pytest.raises(pending.ApplyError, match="stale_block"):
        pending.apply_all(g, [{"op": "close", "vertex": "D05", "answer": "a",
                               "source": "s", "falsifier": "f", "to": []}])


def test_set_status_to_a_settled_status_also_releases(g):
    ops = pending.expand(g, {"op": "set_status", "vertex": "D05",
                             "status": "PROVISIONAL"})
    assert [o["vertex"] for o in ops if o["op"] == "set_status"][1:] == ["D06"]


def test_set_status_to_an_unsettled_status_releases_nothing(g):
    for status in ("OPEN", "REOPENED", "BLOCKED:D01"):
        ops = pending.expand(g, {"op": "set_status", "vertex": "D05",
                                 "status": status})
        assert ops == [{"op": "set_status", "vertex": "D05", "status": status}]


def test_release_only_fires_for_the_vertex_named_in_the_block(g):
    """D06 is BLOCKED:D05; settling anything else must not touch it."""
    ops = pending.expand(g, {"op": "close", "vertex": "D02", "answer": "a",
                             "source": "s", "falsifier": "f", "to": []})
    assert not [o for o in ops if o["op"] == "set_status"]


# ---- apply ---------------------------------------------------------------


def test_close_an_open_vertex(g):
    out = pending.apply_all(g, [
        {"op": "close", "vertex": "D05", "answer": "yes", "source": "discussion",
         "falsifier": "ANALYTIC — test", "to": [], "date": "2026-02-01"},
        {"op": "set_status", "vertex": "D06", "status": "OPEN"},
    ])
    assert out.vertices["D05"].status == "DECIDED"
    assert "D05" not in out.frontier()


def test_close_preserves_existing_dependants(g):
    out = pending.apply_all(g, [
        {"op": "close", "vertex": "D05", "answer": "a", "source": "s",
         "falsifier": "f", "to": [], "date": "2026-02-01"},
        {"op": "set_status", "vertex": "D06", "status": "OPEN"},
    ])
    assert "D06" in out.children("D05")


def test_apply_rejects_unknown_target(g):
    with pytest.raises(pending.ApplyError, match="unknown vertex D99"):
        pending.apply_all(g, [{"op": "close", "vertex": "D05", "answer": "a",
                               "source": "s", "falsifier": "f", "to": ["D99"]}])


def test_apply_rejects_deciding_twice(g):
    with pytest.raises(pending.ApplyError, match="reopen it first"):
        pending.apply_all(g, [{"op": "close", "vertex": "D01", "answer": "a",
                               "source": "s", "falsifier": "f", "to": []}])


def test_apply_leaves_the_input_graph_untouched(g):
    before = json.dumps(g.to_dict())
    with pytest.raises(pending.ApplyError):
        pending.apply_all(g, [{"op": "reopen", "vertex": "D01", "why": "x"}])
    assert json.dumps(g.to_dict()) == before


def test_close_fills_in_what_it_replaced(g):
    ops = pending.expand(g, {"op": "reopen", "vertex": "D01", "why": "x",
                             "summary": "the root answer"})
    out = pending.apply_all(g, ops)
    out = pending.apply_all(out, [
        {"op": "close", "vertex": "D01", "answer": "A newer answer.",
         "source": "discussion", "falsifier": "f", "to": ["D02", "D03"],
         "summary": "newer answer", "date": "2026-03-01"},
    ] + [{"op": "set_status", "vertex": v, "status": "DECIDED"}
         for v in ("D02", "D03", "D04")])
    hist = out.history("D01")
    assert len(hist) == 2
    # only the edge left open by the reopen is filled in; the older one keeps
    # the answer that actually replaced it
    assert hist[0].replaced_by == "the root answer"
    assert hist[1].replaced_by == "newer answer"


def test_add_vertex_then_link(g):
    out = pending.apply_all(g, [
        {"op": "add_vertex", "id": "D99", "title": "New", "area": "Beta",
         "status": "OPEN"},
        {"op": "add_edge", "from": "D05", "to": ["D99"]},
    ])
    assert "D05" in out.depends("D99")


def test_add_duplicate_vertex_is_rejected(g):
    with pytest.raises(pending.ApplyError, match="already exists"):
        pending.apply_all(g, [{"op": "add_vertex", "id": "D01", "title": "x",
                               "area": "Alpha"}])


# ---- project resolution --------------------------------------------------


def test_project_is_found_by_walking_up(store, monkeypatch):
    monkeypatch.setattr(project, "_override", None)
    nested = store / "a" / "b"
    nested.mkdir(parents=True)
    assert project.find(nested).root == store


def test_env_var_wins_over_cwd(store, monkeypatch, tmp_path):
    monkeypatch.setattr(project, "_override", None)
    monkeypatch.setenv("DG_PROJECT", str(store))
    assert project.find(tmp_path).root == store


# ---- audit 2026-08: traversals, atomicity, the tray ----------------------


@pytest.fixture
def dangling(store):
    """The fixture graph with one edge target naming no vertex.

    A state `dg check` reports (`no_dangling_refs`) and therefore a state the
    tool has to survive reading: a store nobody can *look at* cannot be
    repaired, and the report is the only thing telling anyone it is broken.
    """
    raw = json.loads((store / "decisions.json").read_text())
    raw["edges"].append({"from": "D05", "to": ["D99"], "active": True})
    # D05 already has an active edge; replace it rather than break one_active_edge
    raw["edges"] = [e for e in raw["edges"]
                    if not (e["from"] == "D05" and e.get("to") == ["D06"])]
    raw["edges"].append({"from": "D05", "to": ["D06", "D99"], "active": True})
    raw["edges"] = [e for e in raw["edges"] if e.get("to") != ["D99"]]
    (store / "decisions.json").write_text(json.dumps(raw, indent=2))
    return store


def test_a_dangling_target_is_reported_not_traversed(dangling):
    """Audit F1. `depends` documented why a traversal helper must not return an
    id naming no vertex — a caller that dereferences it crashes the validator
    that was about to report the dangling edge — and `children` did it anyway.
    """
    g = Graph.load(dangling / "decisions.json")
    assert "D99" not in g.children("D05")
    assert "D99" not in g.descendants("D01")
    # the finding still fires: filtering the walk must not hide the fault
    assert any(v.check == "no_dangling_refs" and "D99" in v.message
               for v in g.validate())
    # and the edge keeps it, so nothing is dropped from the store
    assert "D99" in g.active_edge("D05").to


@pytest.mark.parametrize("read", ["brief", "rows", "tree", "expand", "context"])
def test_every_read_survives_a_dangling_target(dangling, read):
    """Parametrised over the readers rather than testing one, because the bug
    was "one traversal was hardened and its twin was not" — a single-command
    test would have missed it in exactly the same way.

    `dg brief` matters most: `hooks/brief.py` reads a non-zero exit as "no
    graph here" and stays silent, so a crash here makes the session-start
    brief go quiet permanently, at the moment the graph most needs attention.
    """
    from dgraph import brief as _brief
    from dgraph import context as _context
    from dgraph.model import Graph as _G

    g = _G.load(dangling / "decisions.json")
    if read == "brief":
        assert "D05" in _brief.text(project.find())
    elif read == "rows":
        assert _brief.rows(g)
    elif read == "tree":
        assert g.depths("D06")
    elif read == "expand":
        # the write side: reopening walks descendants and dereferences each
        assert pending.expand(g, {"op": "reopen", "vertex": "D01", "why": "x"})
    else:
        assert _context.decision(g, "D05")["id"] == "D05"


def test_a_store_write_is_all_or_nothing(store, monkeypatch):
    """Audit F3. Ordering the four writes covers a failure *between* them and
    says nothing about one *inside* a write. A truncated `decisions.json` has
    no exit from inside the tool: check reports `store_loads`, every command
    that could repair it refuses to load, and the gate denies every commit.

    The failure is injected where a real one lands — after some bytes are down
    and before the file is in place — which under a bare `write_text` is
    exactly the moment the store is half its old contents and half its new.
    """
    g = Graph.load()
    before = (store / "decisions.json").read_text(encoding="utf-8")
    g.vertices["D05"] = replace(g.vertices["D05"], title="A much longer title")

    monkeypatch.setattr("os.fsync",
                        lambda fd: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        g.save()

    assert (store / "decisions.json").read_text(encoding="utf-8") == before
    assert Graph.load().vertices["D05"].title == "Still open"


def test_an_interrupted_write_leaves_no_temp_file(store, monkeypatch):
    """The temp file is a sibling of the target — `os.replace` is only atomic
    within a filesystem — so a leftover would sit untracked in the user's repo
    and be swept up by `git add -A`. It is cleaned on the way out of *any*
    exception, `KeyboardInterrupt` included, that being the case the helper
    most exists for.
    """
    def interrupt(fd):
        raise KeyboardInterrupt

    monkeypatch.setattr("os.fsync", interrupt)
    with pytest.raises(KeyboardInterrupt):
        project.write_atomic(store / "decisions.json", "x")
    assert not list(store.glob(".*dg-tmp*"))


def test_apply_keeps_what_was_staged_while_it_ran(store, g):
    """Audit F4. `apply` reads the tray, validates, renders and writes; an op
    staged during that window belongs to the next batch. Clearing the file
    dropped it with no error and nothing in any diff — the same silent loss the
    gate's `ask` verdict exists to prevent from `git`.

    The race made deterministic: apply a snapshot taken before the second op
    was staged, which is exactly what a threaded `dg serve` does when a
    terminal stages alongside it.
    """
    from dgraph import applying

    write(g)
    first = {"op": "add_vertex", "id": "D10", "title": "First",
             "area": "Alpha", "status": "OPEN"}
    pending.stage(first)
    batch = pending.load()                    # what apply is about to write
    second = {"op": "add_vertex", "id": "D11", "title": "Staged meanwhile",
              "area": "Alpha", "status": "OPEN"}
    pending.stage(second)

    applying.apply_decisions(batch)

    assert bare(pending.load()) == [second]
    assert "D10" in Graph.load().vertices     # the batch landed
    assert "D11" not in Graph.load().vertices  # and the next one did not


def test_discard_tolerates_an_op_dropped_meanwhile(store):
    """Removal is by value, one occurrence each, not a prefix strip: a
    concurrent `dg drop` means the op is already gone and there is nothing to
    do, rather than a mismatch that strands the whole tray."""
    a = {"op": "add_edge", "from": "D01", "to": ["D05"]}
    b = {"op": "add_edge", "from": "D02", "to": ["D05"]}
    pending.save([b])                          # `a` was dropped meanwhile
    assert pending.discard([a, b]) == []


def test_discard_leaves_a_second_identical_op_staged(store):
    """Two equal ops, one applied: the tool cannot tell which, and leaving one
    staged is the safe direction — a re-applied op is refused loudly by
    `_apply_one`, while a dropped one is silent."""
    op = {"op": "add_edge", "from": "D01", "to": ["D05"]}
    pending.save([op, dict(op)])
    assert pending.discard([op]) == [op]


def test_an_unknown_severity_is_refused(store):
    """Audit F6. `blocking` is `severity == "error"` and the field was a free
    string, so a mistyped `"error"` silently demoted a blocking invariant —
    `apply_all` stops refusing, the gate stops denying, the pytest plugin goes
    green, and nothing anywhere says so.

    Every other fail-open in this tool is guarded on purpose; this is the same
    guard for the one that was not. The drift it closes was already in the
    store: `no_orphans` said `"warn"` where everything else said `"warning"`.
    """
    from dgraph.violation import Violation

    with pytest.raises(ValueError, match="unknown severity"):
        Violation("no_orphans", "…", "warn")
    assert not Violation("x", "…", "warning").blocking
    assert Violation("x", "…").blocking


def test_a_cycle_finding_names_only_the_cycle(store):
    """Audit F7. The message used to be `trail + [node]` — the route *into* the
    loop, then the loop — so a vertex merely feeding a cycle appeared in it,
    and breaking the graph there fixes nothing. The message is read by a model
    that will act on it.
    """
    raw = json.loads((store / "decisions.json").read_text())
    raw["vertices"] = [v for v in raw["vertices"] if v["id"] in ("D01", "D02")]
    raw["vertices"] += [{"id": f"D0{n}", "title": f"c{n}", "area": "Alpha",
                         "status": "OPEN"} for n in (7, 8, 9)]
    raw["edges"] = [
        {"from": "D01", "to": ["D07"], "active": True},   # feeds in, not in it
        {"from": "D07", "to": ["D08"], "active": True},
        {"from": "D08", "to": ["D09"], "active": True},
        {"from": "D09", "to": ["D07"], "active": True},
    ]
    (store / "decisions.json").write_text(json.dumps(raw, indent=2))
    g = Graph.load()

    found = [v.message for v in g.validate() if v.check == "acyclic"]
    assert found == ["cycle: D07 -> D08 -> D09 -> D07"]
    assert "D01" not in found[0]


def test_a_cycle_reads_the_same_however_the_rows_are_ordered(store):
    """The second reason the loop is rotated to its smallest id, and the reason
    it lives beside `Violation` rather than beside any one walk:
    `cross.guard_decisions` tells an introduced finding from a pre-existing one
    **by its text**. A cycle that read differently on two runs would look new,
    and the guard would refuse a write it should have allowed."""
    raw = json.loads((store / "decisions.json").read_text())
    raw["vertices"] = [{"id": f"D0{n}", "title": f"c{n}", "area": "Alpha",
                        "status": "OPEN"} for n in (7, 8, 9)]
    edges = [{"from": "D07", "to": ["D08"], "active": True},
             {"from": "D08", "to": ["D09"], "active": True},
             {"from": "D09", "to": ["D07"], "active": True}]

    seen = set()
    for order in ([0, 1, 2], [2, 0, 1], [1, 2, 0]):
        raw["edges"] = [edges[i] for i in order]
        (store / "decisions.json").write_text(json.dumps(raw, indent=2))
        seen.update(v.message for v in Graph.load().validate()
                    if v.check == "acyclic")
    assert seen == {"cycle: D07 -> D08 -> D09 -> D07"}


def test_concurrent_staging_loses_nothing(store):
    """Audit F4's other half. Every tray mutation is load-then-save, so two
    interleaving means the second writes a list built before the first one's op
    existed. Threads rather than a contrived interleave because that is the
    real shape: `dg serve` is a `ThreadingHTTPServer` and `commands/serve.md`
    tells the user to work in the browser and a terminal at once.

    Without `pending.held` this loses roughly three quarters of the batch.
    """
    import concurrent.futures as cf

    ops = [{"op": "add_edge", "from": "D01", "to": [f"D{i:02d}"]}
           for i in range(10, 50)]
    with cf.ThreadPoolExecutor(8) as ex:
        list(ex.map(pending.stage, ops))
    assert len(pending.load()) == len(ops)


def test_concurrent_writers_to_one_file_do_not_collide(store, g):
    """The temp name comes from `mkstemp`, not the pid: two threads share a
    pid, so a pid-named temp gives them one path — the first `os.replace`
    consumes it and the second raises `FileNotFoundError` on a file that is no
    longer there. Concurrent writers should merely race to be last."""
    import concurrent.futures as cf

    with cf.ThreadPoolExecutor(8) as ex:
        for r in [ex.submit(g.save) for _ in range(40)]:
            r.result()                       # re-raises whatever a thread hit
    assert Graph.load().to_dict() == g.to_dict()
    assert not list(store.glob("*dg-tmp*"))


def test_an_atomic_write_keeps_the_file_s_mode(store):
    """`mkstemp` opens 0600. A store that silently became unreadable to the
    group on its first atomic write would be a surprise nobody asked for."""
    target = store / "decisions.json"
    target.chmod(0o664)
    project.write_atomic(target, "{}\n")
    assert target.stat().st_mode & 0o777 == 0o664


# ---- audit F10 and F12: the store's lock, and whose lock it is -------------


def test_an_apply_does_not_overwrite_a_batch_applied_meanwhile(store):
    """Audit F10. The tray got a lock; the store it feeds did not, and `apply`
    is a read-modify-write of the store: load, apply a batch to a copy, save.

    Two hosts sharing a project — which `commands/serve.md` tells the user to
    do — could each load the store and each write their own result, and the
    later write erased the earlier one's decisions while `discard` took its ops
    out of the tray. An *applied* batch, reported as applied, gone with no error
    and nothing in any diff.

    No threads needed: a caller that hands over a graph it loaded earlier is the
    same stale read, made deterministic.
    """
    from dgraph import applying

    stale = Graph.load()                     # host A reads the store...
    applying.apply_decisions(               # ...host B applies and writes
        [{"op": "add_vertex", "id": "D20", "title": "B's decision",
          "area": "Alpha"}])
    assert "D20" in Graph.load().vertices

    with pytest.raises(pending.ApplyError):
        # Host A resumes against the graph it read before B wrote. The op it
        # carries no longer applies to the store as it now stands, and being
        # told so is the whole point: silence here meant losing D20.
        applying.apply_decisions(
            [{"op": "add_vertex", "id": "D21", "title": "A's decision",
              "area": "Alpha"}, {"op": "add_vertex", "id": "D20",
                                 "title": "clash", "area": "Alpha"}],
            g=stale)
    assert "D20" in Graph.load().vertices    # B's work is still there


def test_a_lock_is_taken_from_a_dead_holder_and_not_from_a_live_one(store, monkeypatch):
    """Audit F12. The tray lock stole from any holder that outlasted a timeout,
    on the reasoning that it must have crashed. A holder that is merely slow has
    not — and stealing from it left two writers inside the block at once, since
    the victim's release then deleted the thief's lock file and admitted a
    third. Liveness is the question the lock was already recording the pid to
    answer.
    """
    lock = store / "decisions.json.lock"

    lock.write_text("999999999")             # a pid nothing can be running as
    with project.held(store / "decisions.json", wait=0.05):
        assert int(lock.read_text()) == os.getpid()      # taken over
    assert not lock.exists()

    lock.write_text(str(os.getpid()))        # a holder that is demonstrably alive
    with project.held(store / "decisions.json", wait=0.05):
        assert lock.read_text() == str(os.getpid())
    # not ours to remove: we degraded rather than stealing, so the file the
    # other holder is relying on is still there
    assert lock.exists()
    lock.unlink()


def test_releasing_never_removes_another_holder_s_lock(store):
    """The second half of F12, and the one that let a third writer in. A process
    that has been stolen from must not delete the file its thief now holds."""
    path = store / "decisions.json"
    lock = path.with_name(path.name + ".lock")
    with project.held(path, wait=0.05):
        lock.write_text("999999999")         # somebody else's lock, in our place
    assert lock.read_text() == "999999999"   # left alone
    lock.unlink()


def test_two_threads_never_hold_one_store_at_once(store):
    """Not an old bug — a regression the F12 fix would otherwise have caused.

    Deciding whether to steal a lock by asking whether its holder is alive is
    the right question between processes and a useless one between threads:
    they share a pid, so each reads the other's lock as "held by something
    alive", waits out the timeout and proceeds unlocked. `dg serve` is a
    `ThreadingHTTPServer` and two Apply clicks are two threads, so that is the
    common case rather than the exotic one, and the in-process lock is what
    separates them.
    """
    import concurrent.futures as cf
    import time

    path = store / "decisions.json"
    inside, overlaps = [], []

    def body(_):
        with project.held(path, wait=5):
            inside.append(1)
            if len(inside) > 1:
                overlaps.append(1)
            time.sleep(0.005)
            inside.pop()

    with cf.ThreadPoolExecutor(8) as ex:
        list(ex.map(body, range(24)))
    assert overlaps == []


# ---- audit F23: repairing a propagation the tool never derived -------------
#
# `pending.expand` marks decided descendants PROVISIONAL from a **reopen op**,
# and is the only producer of that status. A merge, a rebase or a second clone
# can land the reopened premise without the ops it implies, and then the rule is
# broken with nothing to derive the remedy from.


def _merged(g):
    """The damage a merge does: the reopen landed, its propagation did not."""
    g.vertices["D01"] = replace(g.vertices["D01"], status="REOPENED")
    e = g.active_edge("D01")
    e.answer = e.falsifier = e.source = e.date = None
    return g


def test_repairs_is_what_the_reopen_would_have_staged(g):
    """The property the whole command rests on: a repair produces the batch the
    reopen would have produced had it gone through the tool.

    Compared against `expand` itself rather than against a hand-written list, so
    the two cannot drift — if `expand` learns to mark something else, this fails
    until `repairs` learns it too.
    """
    expected = [o for o in pending.expand(g, {"op": "reopen", "vertex": "D01",
                                              "why": "x"})
                if o["op"] == "set_status"]
    assert expected, "the fixture must have decided descendants to propagate to"
    assert pending.repairs(_merged(g)) == sorted(expected,
                                                 key=lambda o: o["vertex"])


def test_repairs_reaches_past_what_propagation_reports(g):
    """`propagation` fires only on a *direct* parent, so a decided vertex two
    levels under the reopened one is invisible to it — the vertex between them
    is DECIDED and therefore counts as settled. The reopen would have marked it
    all the same, and leaving it DECIDED is the same untruth one level down."""
    broken = _merged(g)
    reported = {vid for vid, _ in broken.unpropagated()}
    repaired = {o["vertex"] for o in pending.repairs(broken)}
    assert reported < repaired, "a repair that only fixed what is reported"
    assert "D04" in repaired and "D04" not in reported


def test_repairs_clears_the_finding_and_nothing_else(g):
    """Applying the batch leaves a valid graph, and touches only what it must."""
    broken = _merged(g)
    out = pending.apply_all(broken, pending.repairs(broken))
    assert not [v for v in out.validate() if v.check == "propagation"]
    assert not [v for v in out.validate() if v.blocking]
    assert out.vertices["D05"].status == "OPEN"      # untouched: never DECIDED
    assert out.vertices["D06"].status == "BLOCKED:D05"


def test_repairs_is_empty_on_a_graph_the_checker_is_happy_with(g):
    """Derived from the finding, so it cannot invent a status where the
    validator is not complaining — the property that keeps `set_status` an op
    the tool derives rather than one a caller may write."""
    assert pending.repairs(g) == []
    g.vertices["D02"] = replace(g.vertices["D02"], status="PROVISIONAL")
    assert pending.repairs(g) == []                  # already provisional


# ---- audit F17: what the loser of a race is told ---------------------------


def test_a_collision_with_an_identical_op_names_the_other_writer(g):
    """The F16 sequence, asserting on the message rather than the store: two
    writers load one tray, both apply, and the loser's op is refused.

    "D01 already exists" is true and its plain reading is false — the op is in
    the store, applied by somebody else. An agent reading it as "my work failed"
    re-stages under a fresh id and puts two vertices behind one question.
    """
    op = {"op": "add_vertex", "id": "D09", "title": "New", "area": "Alpha"}
    landed = pending.apply_all(g, [op])              # the other writer wins
    with pytest.raises(pending.ApplyError) as exc:
        pending.apply_all(landed, [op])              # ...and we apply second
    assert "another writer applied it" in str(exc.value)
    assert "Nothing of yours was lost" in str(exc.value)


def test_a_genuine_id_clash_keeps_its_own_words(g):
    """The other reading, and it must not be softened: D09 is taken by something
    else entirely, nothing of this op landed, and re-staging under a fresh id is
    exactly the right response."""
    landed = pending.apply_all(g, [{"op": "add_vertex", "id": "D09",
                                    "title": "Something else", "area": "Alpha"}])
    with pytest.raises(pending.ApplyError) as exc:
        pending.apply_all(landed, [{"op": "add_vertex", "id": "D09",
                                    "title": "New", "area": "Alpha"}])
    assert "another writer" not in str(exc.value)
    assert "pick another id" in str(exc.value)


def test_a_collision_with_an_identical_close_names_the_other_writer(g):
    """The same for a decision: `dg decide D05` twice, once from each of two
    terminals, used to read as "reopen it first" — advice that would file a
    reversal of an answer that had just been recorded correctly."""
    # Through `expand`, as both hosts stage it: closing D05 releases D06, which
    # is BLOCKED on it, and a close without that is refused for `stale_block`
    # rather than for the collision this is about.
    ops = pending.expand(g, {"op": "close", "vertex": "D05",
                             "answer": "Because.", "source": "discussion",
                             "falsifier": "new evidence", "to": []})
    landed = pending.apply_all(g, ops)
    with pytest.raises(pending.ApplyError) as exc:
        pending.apply_all(landed, ops)
    assert "another writer applied it" in str(exc.value)


def test_a_different_answer_to_a_decided_question_still_says_reopen(g):
    """Not a concurrency message: somebody is answering a settled question with
    something new, and reopening really is the way through."""
    pending.apply_all(g, [])                          # D01 is already decided
    with pytest.raises(pending.ApplyError) as exc:
        pending.apply_all(g, [{"op": "close", "vertex": "D01",
                               "answer": "A different answer.",
                               "source": "discussion", "to": []}])
    assert "reopen it first" in str(exc.value)
    assert "another writer" not in str(exc.value)


def test_a_collision_is_its_own_exception_type(g):
    """Audit F17, the part the message alone did not fix.

    Both hosts head an `ApplyError` with "aborted, nothing written" — true of
    the apply, false of the work, and the headline is what a model acts on. A
    subclass lets the caller say something else without every existing
    `except ApplyError` having to learn about it.
    """
    op = {"op": "add_vertex", "id": "D09", "title": "New", "area": "Alpha"}
    landed = pending.apply_all(g, [op])
    with pytest.raises(pending.Collision):
        pending.apply_all(landed, [op])
    # ...and still an ApplyError, so nothing that catches the base class breaks
    assert issubclass(pending.Collision, pending.ApplyError)


def test_a_genuine_clash_is_not_a_collision(g):
    """The distinction has to hold in the type as well as the words, or a caller
    keying off the type re-reads a real clash as somebody else's write."""
    landed = pending.apply_all(g, [{"op": "add_vertex", "id": "D09",
                                    "title": "Something else", "area": "Alpha"}])
    with pytest.raises(pending.ApplyError) as exc:
        pending.apply_all(landed, [{"op": "add_vertex", "id": "D09",
                                    "title": "New", "area": "Alpha"}])
    assert not isinstance(exc.value, pending.Collision)


# ---- what moved underneath a staged batch ----------------------------------
#
# The invariants already refuse the dangerous case — a decided answer on a
# reopened premise is a blocking `propagation` finding, so that batch aborts and
# names the premise. What these cover is the *quiet* case: a batch that applies
# cleanly while resting on something that changed while it sat in the tray.


def test_a_stamped_op_records_what_its_premises_looked_like(g):
    op = pending.stamp(g, {"op": "close", "vertex": "D05", "answer": "a",
                           "source": "s", "to": []})
    assert set(op["saw"]) == {"D05", "D04"}          # itself, and its premise
    assert op["saw"]["D04"].startswith("DECIDED|")


def test_an_op_that_leans_on_nothing_is_not_stamped(g):
    """`add_vertex` invents a vertex and rests on nothing; `set_status` is
    derived from an op in the same batch, which carries the stamp already. A
    `saw` on either would be noise in a tray a person reads."""
    for op in ({"op": "add_vertex", "id": "D09", "title": "x", "area": "Alpha"},
               {"op": "set_status", "vertex": "D05", "status": "OPEN"}):
        assert "saw" not in pending.stamp(g, op)


def test_drift_is_silent_when_nothing_moved(g):
    ops = [pending.stamp(g, {"op": "add_edge", "from": "D01", "to": ["D05"]})]
    assert pending.drift(g, ops) == []


def test_drift_names_the_premise_that_was_reopened(g):
    ops = [pending.stamp(g, {"op": "add_edge", "from": "D01", "to": ["D05"]})]
    moved = pending.apply_all(g, pending.expand(g, {"op": "reopen",
                                                    "vertex": "D01",
                                                    "why": "x"}))
    d = pending.drift(moved, ops)
    assert len(d) == 1 and d[0]["premise"] == "D01"
    assert d[0]["was"] == "DECIDED" and d[0]["now"] == "REOPENED"
    assert "DECIDED → REOPENED" in pending.describe(d[0])


def test_drift_catches_a_re_decision_that_left_the_status_alone(g):
    """The case a status-only stamp misses entirely, and the reason the
    fingerprint digests the answer: DECIDED → REOPENED → DECIDED reads as no
    change at all, while the answer underneath is a different one."""
    ops = [pending.stamp(g, {"op": "add_edge", "from": "D01", "to": ["D05"]})]
    moved = pending.apply_all(g, pending.expand(g, {"op": "reopen",
                                                    "vertex": "D01",
                                                    "why": "x"}))
    moved = pending.apply_all(moved, [{"op": "close", "vertex": "D01",
                                       "answer": "A different answer.",
                                       "falsifier": "f", "source": "s",
                                       "to": ["D02", "D03"]}])
    assert moved.vertices["D01"].status == "DECIDED"     # ...same as before
    d = pending.drift(moved, ops)
    assert len(d) == 1 and d[0]["answer_changed"]
    assert "its answer changed" in pending.describe(d[0])


def test_drift_skips_an_id_the_batch_is_about_to_create(g):
    """Stamped against the *effective* graph, so `saw` can name a vertex that
    exists only in the tray. Reporting it as moved would be a warning about a
    premise nobody has touched."""
    ops = [{"op": "add_edge", "from": "D99", "to": ["D05"],
            "saw": {"D99": "OPEN|-"}}]
    assert pending.drift(g, ops) == []


# ---- one status rule, four callers (audit F30) ---------------------------
#
# `status_fault` replaced three copies — `Graph.validate`, `cli._status_legal`
# and an inline one in `pending.vet` — plus a fourth route that had none.


@pytest.mark.parametrize("status,fault", [
    ("OPEN", None), ("DECIDED", None), ("REOPENED", None), ("PROVISIONAL", None),
    ("BLOCKED:D02", None),
    ("DONE", "illegal status 'DONE'"),
    ("", "illegal status ''"),
    ("open", "illegal status 'open'"),
    ("BLOCKED", "BLOCKED must name a blocker"),
    ("BLOCKED:", "BLOCKED must name a blocker"),
    ("BLOCKED:D01", "blocked by itself"),
    ("BLOCKED:D99", "blocked by unknown vertex D99"),
    # The drift the extraction found: `validate` read only `base_status`, so a
    # blocker smuggled onto a non-BLOCKED status went unread while
    # `Vertex.blocker` went on reporting it.
    ("OPEN:D02", "illegal status 'OPEN:D02'"),
    ("DECIDED:D02", "illegal status 'DECIDED:D02'"),
])
def test_status_fault_is_the_whole_rule(status, fault):
    from dgraph.model import status_fault
    assert status_fault(status, {"D01", "D02"}, of="D01") == fault


def test_a_blocker_on_a_non_blocked_status_is_refused_by_the_validator(g, store):
    """It used to pass, and `Vertex.blocker` reported `D99` regardless — a
    dependency asserted in a field nothing reads as one."""
    from dataclasses import replace as _r
    g.vertices["D05"] = _r(g.vertices["D05"], status="OPEN:D99")
    assert g.vertices["D05"].blocker == "D99"
    hits = [str(v) for v in g.validate() if v.check == "status_legal"]
    assert hits == ["[status_legal] D05: illegal status 'OPEN:D99'"]


def test_every_route_refuses_the_same_status(g, store):
    """The point of one implementation. `vet` is the staging floor the web app
    and the editor both go through; `validate` is the store's authority."""
    from dataclasses import replace as _r
    for bad in ("DONE", "BLOCKED", "OPEN:D02"):
        with pytest.raises(pending.ApplyError):
            pending.vet(g, {"op": "set_status", "vertex": "D05", "status": bad})
        broken = Graph.load()
        broken.vertices["D05"] = _r(broken.vertices["D05"], status=bad)
        assert [v for v in broken.validate() if v.check == "status_legal"], bad


def test_vet_all_lets_a_group_build_on_itself(g, store):
    """`add_vertex D07` then an edge *to* D07: legal only in that order, which
    is why the editor path needs the plural rather than `vet` per op."""
    ops = [{"op": "add_vertex", "id": "D07", "title": "x", "area": "Alpha",
            "status": "OPEN"},
           {"op": "add_edge", "from": "D01", "to": ["D07"]}]
    pending.vet_all(g, ops)                       # does not raise
    with pytest.raises(pending.ApplyError):
        pending.vet(g, ops[1])                    # ...but alone it would
    assert pending.load() == [], "vet_all stages nothing"


# ---- drift is about other writers, not about your own batch (F-F1) -------


def test_drift_is_silent_within_one_writers_own_batch(g):
    """A batch that reopens a premise and then attaches to it must report
    nothing. Nobody else has written.

    This is the ordinary shape of decomposing a reversal into work — `dg reopen`
    then `dg add --after` on the vertex just reopened — and both stage against
    the *effective* graph, where the reopen has already taken effect. Comparing
    those stamps against the bare store reports the batch's own first op as a
    stranger's, with the direction backwards: `REOPENED → DECIDED`.

    It matters more than a cosmetic wrong line. The drift report is the only
    signal that a premise moved under a staged answer, and `demo-agentic/`
    rests a whole scene on somebody reading one. A line that also fires on the
    commonest single-writer batch in the tool is one an agent learns to skip.
    """
    reopen = pending.expand(g, {"op": "reopen", "vertex": "D01", "why": "x"})
    reopen = [pending.stamp(g, op) for op in reopen]
    eff = pending.apply_all(g, reopen)                 # what the tray now reads
    attach = pending.stamp(eff, {"op": "add_edge", "from": "D01",
                                 "to": ["D05"]})
    assert pending.drift(g, [*reopen, attach]) == []


def test_drift_still_names_another_writer_behind_your_own_ops(g):
    """The guard on the fix above: silencing a batch's own effect must not
    silence the case the report exists for.

    Same batch, except the store has *also* moved — somebody else re-decided
    `D01` with a different answer while this sat in the tray. Op 0 rests on the
    store as it stands and its answer is not the one it was composed against,
    so that op must still be named.

    What must *not* happen is the second entry the unfixed code adds: the
    attach reporting the batch's own reopen a second time, which is what turns
    one true signal into two lines a reader cannot tell apart.
    """
    reopen = pending.expand(g, {"op": "reopen", "vertex": "D01", "why": "x"})
    reopen = [pending.stamp(g, op) for op in reopen]
    eff = pending.apply_all(g, reopen)
    attach = pending.stamp(eff, {"op": "add_edge", "from": "D01",
                                 "to": ["D05"]})

    # Meanwhile, in the store: reopened and settled again, differently.
    moved = pending.apply_all(g, pending.expand(g, {"op": "reopen",
                                                    "vertex": "D01",
                                                    "why": "someone else"}))
    moved = pending.apply_all(moved, [{"op": "close", "vertex": "D01",
                                       "answer": "A different answer.",
                                       "falsifier": "f", "source": "s",
                                       "to": ["D02", "D03"]}])

    d = pending.drift(moved, [*reopen, attach])
    assert [x["premise"] for x in d] == ["D01"]     # once, not twice
    assert d[0]["op"] == 0 and d[0]["answer_changed"]


# ---- an ordinary edit has a route (F-F6) ---------------------------------


def test_a_vertex_note_and_its_dialect_go_together(g):
    """`add_vertex` already applies this rule — the tag describes the note, so
    without one it describes nothing — and `set_fields` is the only other way a
    note reaches a vertex. The task store does *not* do this, and that is the
    records differing rather than the rule: a task's `format` covers its
    outcomes too."""
    out = pending.apply_all(g, [
        {"op": "set_fields", "vertex": "D05", "note": "*org*", "format": "org"}])
    assert out.vertices["D05"].format == "org"
    gone = pending.apply_all(out, [
        {"op": "set_fields", "vertex": "D05", "note": None}])
    assert gone.vertices["D05"].note is None
    assert gone.vertices["D05"].format is None


def test_a_correction_is_not_stamped_as_leaning_on_the_record(g):
    """`premises` leaves `set_fields` out, and the reason is `fingerprint`
    rather than the op: a fingerprint is a status and a digest of an answer, so
    listing the vertex would make `drift` report a status change under an op
    that does not care about the status, and go on missing the wording it does
    care about. Two writers giving one record different titles is the seam's
    problem, not this function's."""
    assert pending.premises(g, {"op": "set_fields", "vertex": "D01",
                                "title": "x"}) == []
    assert "saw" not in pending.stamp(g, {"op": "set_fields", "vertex": "D01",
                                          "title": "x"})


# ---- a vertex with two answers (F-F4) ------------------------------------
#
# The store *loads*, which is what makes this worse than the duplicate-id case
# beside it. Union two clones that each settled the same inherited vertex and
# the result holds two active edges: `dg check` refuses it, blocking — and
# every reader that asks `active_edge` gets first-wins and shows one answer
# with no sign the other exists. The reader is told something false and cannot
# tell.


def _two_answers(g):
    """The store a git text-merge of two clones leaves behind."""
    from dgraph.model import Edge
    out = replace(g)
    out.edges = [e for e in g.edges if e.src != "D01"] + [
        Edge(src="D01", to=[], answer="OURS: two reviewers, no formal gate.",
             source="a", falsifier="x", date="2026-06-01"),
        Edge(src="D01", to=[], answer="THEIRS: one reviewer plus CI.",
             source="b", falsifier="y", date="2026-06-02"),
    ]
    return out


def test_two_active_edges_are_readable_as_two(g):
    """`active_edge` stays first-wins, because that is right for a traversal —
    `children` needs an answer to follow and any will do. What changes is that
    there is now a way to ask, so a reader is not stuck with the traversal's
    answer."""
    out = _two_answers(g)
    assert out.active_edge("D01").answer.startswith("OURS")
    (other,) = out.rival_answers("D01")
    assert other.answer.startswith("THEIRS")
    assert g.rival_answers("D01") == []          # the sound store says nothing


def test_the_store_still_refuses_it(g):
    """The finding is about the *readers*, not the invariant: `one_active_edge`
    already refused this, blocking, and still does."""
    hits = [v for v in _two_answers(g).validate()
            if v.check == "one_active_edge"]
    assert hits and hits[0].blocking


def test_every_surface_that_shows_an_answer_says_there_are_two(g, store):
    """Four renderers show an answer, and one phrasing serves all of them —
    the rule `stop_label` and `done_label` already follow. Asserted together
    rather than one test each, because the failure this guards is exactly one
    of them being left out."""
    from dgraph import context, render, server
    from dgraph.model import rival_note
    out = _two_answers(g)
    out.save(store / "decisions.json")
    said = rival_note(1)

    assert said in render.render(out)
    node = context.decision(out, "D01")
    assert node["rival_answers"] == said
    assert said.split(" — ")[0] in context.text(node)
    assert said.split(" — ")[0] in context.compact(node)
    payload = server.graph_payload(out)
    assert payload["derived"]["D01"]["rival_answers"] == said


def test_a_rival_answer_is_searchable(g):
    """`dg find` answering "nothing" about a sentence sitting in the store is
    the failure that whole command is shaped to avoid, and `active_edge` being
    first-wins made the second answer unfindable."""
    from dgraph import query
    lens = query.decision_lens(_two_answers(g))
    assert query.select(query.parse("answer:CI"), lens) == ["D01"]


def test_the_wording_is_written_down_once(g):
    """Four surfaces, one sentence. Three renderers picking their own label is
    how the PARKED reason came to be printed by one of them and dropped by the
    other two."""
    import inspect
    from dgraph import cli, context, render, server
    for mod in (cli, context, render, server):
        assert "active edges —" not in inspect.getsource(mod), mod.__name__


# ---- the derivations that were rewritten for cost -------------------------
#
# `stale_provisional`, `roots` and `unpropagated` used to ask `depends` — and
# so rescan the whole edge list — once per vertex; `stale_provisional` asked
# `provisional_because`, a full upward walk, once per PROVISIONAL vertex, which
# made `dg check` cubic. They now build the reverse adjacency, or walk down
# from the unsettled set, once per call.
#
# These tests pin the **answer**, not the speed. Each states the definition the
# rewrite replaced and asserts the two agree, so the definitions stay the
# specification and a future optimisation has something to be wrong against.
# A speed assertion would be the wrong test: it fails on a loaded machine and
# passes on a rewrite that returns nonsense quickly.


def _slow_depends(g, vid):
    """`depends` as it read before the reverse index: one scan per call."""
    return sorted({e.src for e in g.edges
                   if e.active and vid in e.to and e.src in g.vertices})


def _slow_stale_provisional(g):
    """One full `ancestors` walk per PROVISIONAL vertex — the cubic term."""
    return [vid for vid, v in g.vertices.items()
            if v.base_status == "PROVISIONAL" and not g.provisional_because(vid)]


def _slow_roots(g):
    return sorted(v for v in g.vertices if not _slow_depends(g, v))


def _slow_unpropagated(g):
    return [(vid, p) for vid, v in sorted(g.vertices.items())
            if v.base_status == "DECIDED"
            for p in _slow_depends(g, vid) if not g.vertices[p].settled]


def test_depends_is_the_same_with_and_without_the_reverse_index(g):
    """The index is an optimisation and nothing else, so the two paths through
    `depends` have to answer identically — including for a vertex nothing points
    at, where one returns `[]` and the other misses the key."""
    into = g._reverse()
    for vid in g.vertices:
        assert g.depends(vid, into) == g.depends(vid) == _slow_depends(g, vid)


def test_the_rewritten_derivations_agree_with_the_definitions(g):
    assert g.stale_provisional() == _slow_stale_provisional(g)
    assert g.roots() == _slow_roots(g)
    assert g.unpropagated() == _slow_unpropagated(g)


def _slow_provisional_causes(g):
    return {vid: g.provisional_because(vid) for vid, v in g.vertices.items()
            if v.base_status == "PROVISIONAL"}


def test_provisional_causes_is_the_per_vertex_answer(g):
    """`dg brief` reaches the per-vertex walk without going through `validate`,
    so it stayed cubic after the rule was fixed. The shared index has to give
    back exactly what asking one vertex at a time gave."""
    g.vertices["D02"] = replace(g.vertices["D02"], status="PROVISIONAL")
    g.vertices["D04"] = replace(g.vertices["D04"], status="PROVISIONAL")
    g.vertices["D01"] = replace(g.vertices["D01"], status="REOPENED")
    causes = g.provisional_causes()
    assert causes == _slow_provisional_causes(g)
    # ...and it is not vacuously empty: D01 under review is why both are.
    assert causes == {"D02": ["D01"], "D04": ["D01"]}


def test_ancestors_is_the_same_with_and_without_the_shared_index(g):
    into = g._reverse()
    for vid in g.vertices:
        assert g.ancestors(vid, into) == g.ancestors(vid)


def test_stale_provisional_agrees_when_the_rule_actually_fires(g):
    """The fixture is clean, so the check above compares two empty lists. Make
    D02 PROVISIONAL under settled premises and the rule has something to find —
    otherwise a rewrite returning `[]` unconditionally would pass."""
    g.vertices["D02"] = replace(g.vertices["D02"], status="PROVISIONAL")
    assert g.stale_provisional() == ["D02"] == _slow_stale_provisional(g)


def test_stale_provisional_is_silent_under_an_unsettled_premise(g):
    """The other half: D01 under review is exactly what PROVISIONAL is for, and
    the downward walk has to reach D02 from it."""
    g.vertices["D01"] = replace(g.vertices["D01"], status="REOPENED")
    g.vertices["D02"] = replace(g.vertices["D02"], status="PROVISIONAL")
    assert g.stale_provisional() == [] == _slow_stale_provisional(g)


def test_stale_provisional_follows_rival_active_edges(g):
    """`children` takes the first active edge; `depends` sees them all. The walk
    has to use the second, or a store with two answers — which `validate` must
    survive in order to report it — gets a warning that contradicts
    `provisional_because`.

    D07 is new and nothing else points at it, so D01's second active edge is the
    only way down to it. Reusing a fixture vertex would not test this: the chain
    D01 → D02 → D04 → D05 already reaches every one of them by the first edge,
    and the assertion would hold whichever edge the walk followed.
    """
    from dgraph.model import Edge, Vertex
    g.vertices["D01"] = replace(g.vertices["D01"], status="REOPENED")
    g.vertices["D07"] = Vertex("D07", "Reachable only by the rival edge",
                               "Beta", "PROVISIONAL")
    g.edges.append(Edge(src="D01", to=["D07"], active=True))
    assert g.stale_provisional() == [] == _slow_stale_provisional(g)


def test_the_rewrites_agree_on_awkward_random_graphs():
    """Cycles, rival active edges, dangling targets and edges from ids that name
    no vertex — the shapes a hand-written fixture does not reach, and the ones
    `validate` has to survive rather than crash on, since reporting them is its
    job. Seeded, so a failure is reproducible."""
    import random

    from dgraph.model import Edge, Graph, Vertex
    statuses = ["OPEN", "BLOCKED", "REOPENED", "DECIDED", "PROVISIONAL",
                "TERMINAL"]
    rng = random.Random(4242)
    fired = 0
    for _ in range(200):
        ids = [f"D{i:05d}" for i in range(rng.randint(1, 30))]
        graph = Graph(
            vertices={i: Vertex(i, "t", "a", rng.choice(statuses)) for i in ids},
            edges=[],
        )
        for i in ids:
            for _ in range(rng.randint(0, 2)):
                to = rng.sample(ids, rng.randint(0, min(4, len(ids))))
                if rng.random() < 0.15:
                    to.append("D99999")            # a target naming no vertex
                graph.edges.append(Edge(i, to, active=rng.random() < 0.75))
            if rng.random() < 0.05:                # a source naming no vertex
                graph.edges.append(Edge("D99999", [rng.choice(ids)], active=True))
        slow = _slow_stale_provisional(graph)
        fired += len(slow)
        assert graph.stale_provisional() == slow
        assert graph.roots() == _slow_roots(graph)
        assert graph.unpropagated() == _slow_unpropagated(graph)
        assert graph.provisional_causes() == _slow_provisional_causes(graph)
    assert fired > 50, f"the rule barely fired ({fired}) — the corpus is too easy"


# ---- the short-circuits, which are the point of the two guards -------------
#
# Asserting the *answer* here proves nothing: both return `[]` either way. What
# has to hold is that the edge list is never walked, because the per-vertex form
# these replaced got that for free — it started no walk when there was nothing
# to walk for — and without it the rewrite is slower on an ordinary store that
# happens to have nothing under review. A timing assertion would be the wrong
# test, so the edge list is made to raise instead.


class _Unwalkable(list):
    """A list that refuses to be iterated, so a walk over it is a test failure."""

    def __iter__(self):
        raise AssertionError("the edge list was walked when it need not be")


def test_stale_provisional_does_not_walk_with_nothing_provisional(g):
    """The fixture has no PROVISIONAL vertex, so there is no question to answer
    and no reason to build the adjacency to answer it."""
    g.edges = _Unwalkable(g.edges)
    assert g.stale_provisional() == []


def test_unpropagated_does_not_walk_with_nothing_decided(g):
    """The same guard on the other rule: `propagation` is about DECIDED
    vertices resting on unsettled premises, so with nothing DECIDED there is
    nothing it can report."""
    for vid in list(g.vertices):
        g.vertices[vid] = replace(g.vertices[vid], status="OPEN")
    g.edges = _Unwalkable(g.edges)
    assert g.unpropagated() == []


def test_the_guards_still_walk_when_there_is_something_to_find(g):
    """The other half — a guard that always short-circuits would pass both
    tests above and be silently broken."""
    # D02 rests on D01, which is DECIDED — so PROVISIONAL is no longer true of
    # it and `stale_provisional` has something to say.
    g.vertices["D02"] = replace(g.vertices["D02"], status="PROVISIONAL")
    # D06 rests on D05, which is OPEN — the `propagation` pair. The fixture is
    # clean as it ships, so both states have to be made deliberately.
    g.vertices["D06"] = replace(g.vertices["D06"], status="DECIDED")
    assert g.stale_provisional() == ["D02"]
    assert g.unpropagated() == [("D06", "D05")] == _slow_unpropagated(g)


# ---- every depth in one walk ----------------------------------------------


def test_all_depths_is_the_per_vertex_answer(g):
    assert g.all_depths() == {vid: g.depth(vid) for vid in g.vertices}


def test_all_depths_agrees_with_depth_on_random_acyclic_graphs():
    """A DAG is the only shape this has to agree on — `validate` reports a
    cycle as an error, and on one both readings are arbitrary for the same
    reason (an in-cycle parent counts 0, so the answer depends on where the
    walk entered). Forward-only edges make these acyclic by construction."""
    import random

    from dgraph.model import Edge, Graph, Vertex
    rng = random.Random(99)
    for _ in range(200):
        n = rng.randint(1, 25)
        ids = [f"D{i:03d}" for i in range(n)]
        graph = Graph(vertices={i: Vertex(i, "t", "a", "DECIDED") for i in ids},
                      edges=[])
        for k, i in enumerate(ids):
            to = [ids[j] for j in range(k + 1, n) if rng.random() < 0.25]
            if to or rng.random() < 0.5:
                graph.edges.append(Edge(i, to, active=True))
        assert graph.all_depths() == {v: graph.depth(v) for v in graph.vertices}


# ---- grouping the edge records by source ----------------------------------
#
# `active_edge`, `rival_answers`, `history` and `children` each scanned the
# whole edge list to answer about one vertex, and `dg find` and the web view's
# payload ask all of them for every vertex. `by_src` groups once. As everywhere
# else it is built inside the call and dropped with it — the grouping costs
# less than one of the scans it replaces, so there is nothing to cache and
# nothing to invalidate.


def test_by_src_answers_as_the_scan_does(g):
    by = g.by_src()
    for vid in g.vertices:
        assert g.active_edge(vid, by) == g.active_edge(vid), vid
        assert g.rival_answers(vid, by) == g.rival_answers(vid), vid
        assert g.history(vid, by) == g.history(vid), vid
        assert g.children(vid, by) == g.children(vid), vid


def test_by_src_keeps_store_order_so_first_wins_still_means_first(g):
    """`active_edge` is *first*-wins where a store holds rival answers, and a
    grouping that reordered the records would quietly return the other answer —
    a store the tool cannot write but a text-merge can, and one `validate` has
    to survive in order to report."""
    from dgraph.model import Edge
    rival = Edge(src="D01", to=["D04"], active=True, answer="The rival.")
    g.edges.append(rival)
    by = g.by_src()
    assert g.active_edge("D01", by) is g.active_edge("D01")
    assert g.active_edge("D01", by) is not rival           # the first still wins
    assert g.rival_answers("D01", by) == [rival] == g.rival_answers("D01")


def test_by_src_keeps_history_apart_from_a_rejected_answer(g):
    """`history` is not "every inactive edge": an answer offered at the
    integration seam and not adopted is inactive too and was never believed.
    The grouped path has to make the same distinction."""
    from dgraph.model import Edge
    g.edges.append(Edge(src="D01", to=[], active=False, answer="Offered.",
                        from_source="somebody-else"))
    by = g.by_src()
    assert g.history("D01", by) == g.history("D01")
    assert all(e.from_source is None for e in g.history("D01", by))


def test_by_src_is_empty_for_a_vertex_with_no_edges(g):
    """The scan returns nothing; the grouping must not raise on a missing key."""
    from dgraph.model import Vertex
    g.vertices["D07"] = Vertex("D07", "Nothing points from it", "Beta", "OPEN")
    by = g.by_src()
    assert g.active_edge("D07", by) is None
    assert g.rival_answers("D07", by) == []
    assert g.history("D07", by) == []
    assert g.children("D07", by) == []


def test_provisional_causes_falls_back_on_a_cycle(g):
    """The topological pass cannot order a cycle, and a vertex inside one has
    no well-defined set of ancestors above it. `validate` reports the cycle;
    until it is fixed this has to answer *something* rather than report that
    those vertices rest on nothing — so it falls back to the walk, which has
    its own guard."""
    g.vertices["D01"] = replace(g.vertices["D01"], status="REOPENED")
    g.vertices["D04"] = replace(g.vertices["D04"], status="PROVISIONAL")
    # Extend D04's own active edge rather than adding a second one: `children`
    # is first-wins, so a rival edge would not be on the followed path and the
    # cycle would not exist for the walk that has to survive it.
    g.active_edge("D04").to.append("D02")                      # D02 → D04 → D02
    assert [v for v in g.validate() if v.check == "acyclic"], "expected a cycle"
    assert g.provisional_causes() == _slow_provisional_causes(g)
    assert g.provisional_causes()["D04"] == ["D01"]


def test_provisional_causes_over_random_graphs_including_cycles():
    """The fast path and the fallback have to agree with the definition on both
    shapes, and the corpus must actually contain each."""
    import random

    from dgraph.model import Edge, Graph, Vertex
    statuses = ["OPEN", "BLOCKED", "REOPENED", "DECIDED", "PROVISIONAL",
                "TERMINAL"]
    rng = random.Random(7)
    fired = 0
    for _ in range(200):
        ids = [f"D{i:03d}" for i in range(rng.randint(1, 25))]
        graph = Graph(
            vertices={i: Vertex(i, "t", "a", rng.choice(statuses)) for i in ids},
            edges=[])
        for i in ids:
            if rng.random() < 0.8:                    # cycles allowed on purpose
                graph.edges.append(Edge(
                    i, rng.sample(ids, rng.randint(0, min(4, len(ids)))),
                    active=True))
        ref = {v: graph.provisional_because(v) for v, x in graph.vertices.items()
               if x.base_status == "PROVISIONAL"}
        fired += len(ref)
        assert graph.provisional_causes() == ref
    assert fired > 100, f"the corpus barely exercised it ({fired})"


# ---- the shapes the fixture and the generated stores do not reach ---------


def test_a_deep_chain_does_not_blow_the_stack_or_rescan_per_step():
    """A chain a few thousand deep is a legal graph. Every walk here is
    iterative for that reason — and each must also carry an index, or the walk
    is O(chain x edges) and one `dg context` on a deep vertex hangs. `depth`
    took 12.3 s on this before it was given one."""
    from dgraph.model import Edge, Graph, Vertex
    n = 3000
    ids = [f"D{i:05d}" for i in range(n)]
    graph = Graph(vertices={i: Vertex(i, "t", "a", "DECIDED") for i in ids},
                  edges=[Edge(ids[i], [ids[i + 1]], active=True)
                         for i in range(n - 1)])
    assert graph.depth(ids[-1]) == n - 1
    assert graph.all_depths()[ids[-1]] == n - 1
    assert len(graph.ancestors(ids[-1])) == n - 1
    assert len(graph.descendants(ids[0])) == n - 1


def test_every_derivation_terminates_on_a_cycle(g):
    """`validate` has to survive a cycle in order to report one, so nothing it
    calls may loop forever. A cycle is an error, not a crash."""
    from dgraph.model import Edge, Graph, Vertex
    n = 500
    ids = [f"D{i:05d}" for i in range(n)]
    graph = Graph(vertices={i: Vertex(i, "t", "a", "PROVISIONAL") for i in ids},
                  edges=[Edge(ids[i], [ids[(i + 1) % n]], active=True)
                         for i in range(n)])
    graph.all_depths()
    graph.provisional_causes()
    graph.stale_provisional()
    graph.roots()
    graph.unpropagated()
    graph.ancestors(ids[0])
    graph.descendants(ids[0])
    assert [v for v in graph.validate() if v.check == "acyclic"]


def test_the_derivations_hold_on_a_graph_with_no_edges_and_no_vertices():
    from dgraph.model import Graph
    empty = Graph(vertices={}, edges=[])
    assert empty.all_depths() == {}
    assert empty.roots() == []
    assert empty.provisional_causes() == {}
    assert empty.stale_provisional() == []
    assert empty.unpropagated() == []
    assert empty.by_src() == {}


def test_rendering_reads_the_graph_through_an_index_without_changing_a_byte(g):
    """`dg check` rebuilds both views to see whether they are stale, so the
    renderer's cost is the commit gate's cost — which is why it now takes a
    grouping of the edges rather than scanning per record.

    The view is a *file people read and diff*, so the only acceptable outcome
    is that the bytes are unchanged. This pins that the indexed reads and the
    scanning ones produce the same document, by rendering the same graph with
    the index deliberately withheld."""
    from dgraph import render as r
    by, into = g.by_src(), g._reverse()
    for vid in g.vertices:
        # `None` for either index is the scanning path — the code as it was.
        assert r._section(g, vid, by, into) == r._section(g, vid), vid
        assert r._resolves_cell(g, vid, by) == r._resolves_cell(g, vid), vid
    # ...and set up the records whose reads the index could plausibly reorder:
    # a superseded edge, and a rival answer the archive must not swallow.
    from dgraph.model import Edge
    g.edges.append(Edge(src="D01", to=["D04"], active=True, answer="A rival."))
    # Both indexes rebuilt, not just the one whose store changed. Reusing the
    # `_reverse` from above fails this assertion, which is the rule these are
    # built per call to obey: an index that outlives a write is a stale answer,
    # and here it would render a premise list the store no longer has.
    by2, into2 = g.by_src(), g._reverse()
    for vid in g.vertices:
        assert r._section(g, vid, by2, into2) == r._section(g, vid), vid


def test_the_task_view_renders_the_same_with_and_without_the_index(tg):
    from dgraph import task_render as tr
    adj = tg._adjacency()
    for tid in tg.tasks:
        assert tr._section(tg, tid, adj) == tr._section(tg, tid), tid
