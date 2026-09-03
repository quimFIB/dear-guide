"""The edge probe: `{kind, args}` in `PAYLOAD`, shape-checked, bounded (T50).

The falsifier's mechanical twin (`D71`). What this file proves is the part
`tests/test_payload.py` does not: the *shape* — one implementation of it,
`model.probe_fault`, asked at every door — and that each door refuses a
malformed probe with the same sentence before anything is staged. The
round-trip (close, reopen, reject, seam, export/import) is `test_payload`'s,
which pushes a probe through with every other field.
"""

import copy
import json

import pytest
from typer.testing import CliRunner

from dgraph import editor, pending
from dgraph.cli import app
from dgraph.model import Graph, probe_args_limit, probe_fault
from tests.conftest import FIXTURE

GOOD = {"kind": "prose.rule", "args": {"sha": "abc"}}


@pytest.fixture
def run(store, monkeypatch):
    """As `tests/test_cli.py`'s: `dg` against the fixture project."""
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERM", "dumb")
    runner = CliRunner()
    return lambda *args, input=None: runner.invoke(
        app, ["--project", str(store), *args], input=input)


def _tray(store) -> list[dict]:
    return pending.load(store / ".dgraph-pending.json")


def _codes(g: Graph) -> list[str]:
    return [v.check for v in g.validate()]


# ---- the shape, in one place ---------------------------------------------


def test_a_well_formed_probe_has_no_fault():
    assert probe_fault(GOOD) is None
    assert probe_fault({"kind": "a.b", "args": {}}) is None


@pytest.mark.parametrize("probe, names", [
    ("prose.rule", "not str"),
    (["prose.rule"], "not list"),
    ({"kind": "prose.rule"}, "args is an object"),
    ({"args": {}}, "kind is a string"),
    ({"kind": 3, "args": {}}, "kind is a string"),
    ({"kind": "prose", "args": {}}, "does not name a domain"),
    ({"kind": ".rule", "args": {}}, "does not name a domain"),
    ({"kind": "prose.", "args": {}}, "does not name a domain"),
    ({"kind": "prose .rule", "args": {}}, "no whitespace"),
    ({"kind": "prose.rule", "args": []}, "args is an object"),
    ({"kind": "prose.rule", "args": "x"}, "args is an object"),
    ({"kind": "prose.rule", "args": {}, "note": "n"}, "note is not read"),
])
def test_each_shape_fault_is_named(probe, names):
    assert names in (probe_fault(probe) or "")


def test_args_are_bounded_at_the_synopsis_limit():
    cap = probe_args_limit()
    just = {"kind": "p.r", "args": {"s": "x" * (cap - len('{"s":""}'))}}
    assert len(json.dumps(just["args"], separators=(",", ":"))) == cap
    assert probe_fault(just) is None
    over = {"kind": "p.r", "args": {"s": "x" * cap}}
    fault = probe_fault(over)
    assert fault and str(cap) in fault and "fingerprint" in fault


# ---- the store ------------------------------------------------------------


def _with_probe(probe, active=True) -> Graph:
    raw = copy.deepcopy(FIXTURE)
    e = next(e for e in raw["edges"] if e["from"] == "D01" and e["active"] is active)
    e["probe"] = probe
    return Graph.from_dict(raw)


def test_a_probe_is_a_known_field_not_an_unknown_one():
    g = _with_probe(GOOD)
    e = g.active_edge("D01")
    assert e.probe == GOOD and e.extra == {}
    back = Graph.from_dict(g.to_dict())
    assert back.active_edge("D01").probe == GOOD


def test_validate_refuses_a_malformed_probe_and_it_blocks():
    g = _with_probe({"kind": "nodot", "args": {}})
    found = [v for v in g.validate() if v.check == "probe_wellformed"]
    assert len(found) == 1 and found[0].blocking
    assert found[0].message.startswith("D01: ")


def test_validate_reads_an_archived_probe_too():
    """A hand-edit can break either edge, and an archived probe is still the
    record of what a past answer pre-committed to."""
    g = _with_probe("not-a-probe", active=False)
    assert "probe_wellformed" in _codes(g)


def test_a_store_without_probes_validates_as_before():
    assert "probe_wellformed" not in _codes(Graph.from_dict(FIXTURE))


# ---- the doors -------------------------------------------------------------


def _close(**extra):
    return {"op": "close", "vertex": "D05", "to": ["D06"], "answer": "a",
            "source": "s", "falsifier": "f", **extra}


def test_vet_refuses_a_malformed_probe_at_the_door():
    g = Graph.from_dict(FIXTURE)
    with pytest.raises(pending.ApplyError, match=r"probe: .*does not name a domain"):
        pending.vet(g, _close(probe={"kind": "nodot", "args": {}}))
    pending.vet(g, _close(probe=GOOD))           # and a good one passes


def test_vet_refuses_it_on_a_reject_too():
    g = pending.apply_all(Graph.from_dict(FIXTURE), [_close()])
    op = {**_close(answer="theirs"), "op": "reject", "from_source": "them",
          "probe": ["nope"]}
    with pytest.raises(pending.ApplyError, match="probe: "):
        pending.vet(g, op)


def test_apply_refuses_a_probe_that_slipped_past_the_door():
    g = Graph.from_dict(FIXTURE)
    with pytest.raises(pending.ApplyError, match="probe_wellformed"):
        pending.apply_all(g, [_close(probe={"kind": "nodot", "args": {}})])


def test_decide_probe_stages_it(run, store, g):
    res = run("decide", "D05", "-a", "yes", "-s", "discussion", "-f", "f",
              "--opens", "D06", "--probe", json.dumps(GOOD))
    assert res.exit_code == 0, res.output
    close = next(o for o in _tray(store) if o["op"] == "close")
    assert close["probe"] == GOOD


def test_decide_refuses_a_malformed_probe_before_asking_anything(run, store, g):
    res = run("decide", "D05", "--probe", '{"kind": "nodot", "args": {}}')
    assert res.exit_code == 1
    assert "does not name a domain" in res.output
    assert _tray(store) == []
    res = run("decide", "D05", "--probe", "{not json")
    assert res.exit_code == 1 and "not JSON" in res.output


def test_decide_without_probe_stages_no_probe_key(run, store, g):
    run("decide", "D05", "-a", "yes", "-s", "discussion", "-f", "f",
        "--opens", "D06")
    close = next(o for o in _tray(store) if o["op"] == "close")
    assert "probe" not in close


# ---- the editor buffer -----------------------------------------------------


def test_the_buffer_offers_a_probe_field_and_carries_a_seed(g, store):
    text = editor.render_close(g, "D05", seed={"probe": GOOD})
    assert "** Probe" in text
    assert '"kind": "prose.rule"' in text


def test_the_buffer_round_trips_a_probe(g, store):
    from tests.test_editor import fill
    text = fill(editor.render_close(g, "D05", seed={"probe": GOOD}),
                answer="a", source="s", falsifier="f")
    ops = editor.parse(text, g=g)
    assert ops[0]["probe"] == GOOD


def test_an_empty_probe_field_stages_no_probe(g, store):
    from tests.test_editor import fill
    text = fill(editor.render_close(g, "D05"), answer="a", source="s",
                falsifier="f")
    assert "probe" not in editor.parse(text, g=g)[0]


def test_the_buffer_refuses_a_malformed_probe_by_the_field_name(g, store):
    from tests.test_editor import fill
    base = fill(editor.render_close(g, "D05"), answer="a", source="s",
                falsifier="f")
    with pytest.raises(editor.EditorError, match="Probe is not JSON"):
        editor.parse(fill(base, probe="{nope"), g=g)
    with pytest.raises(editor.EditorError, match="Probe: .*does not name"):
        editor.parse(fill(base, probe='{"kind": "nodot", "args": {}}'), g=g)


def test_dg_edit_re_renders_a_staged_probe(g, store):
    op = _close(probe=GOOD, date="2026-01-01")
    text = editor.render_op(g, 0, op)
    assert '"sha": "abc"' in text


# ---- reading it back -------------------------------------------------------


def test_node_shows_the_probe_beside_the_falsifier(run, store, g):
    g2 = _with_probe(GOOD)
    g2.save()
    out = run("node", "D01").output
    assert "probe" in out and "prose.rule" in out and '"sha": "abc"' in out
    # and a decision without one says nothing about it
    assert "probe" not in run("node", "D02").output


def test_the_markdown_view_shows_a_probe_only_where_there_is_one(store):
    from dgraph import render
    plain = render.render(Graph.from_dict(FIXTURE))
    assert "Probe" not in plain
    withp = render.render(_with_probe(GOOD))
    assert "- **Probe:** `prose.rule`" in withp
