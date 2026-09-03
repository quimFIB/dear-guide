"""`PAYLOAD`: one tuple, and every site that copies a decision reads it.

Before the tuple, the fields a `close` writes were named by hand in a dozen
places — the apply path, the reopen archive, the reject, the integration
seam's close and keepsake, the import schema, the serialiser. A copy that
names four fields silently drops a fifth: the store keeps loading, `dg check`
says nothing, and the field is gone from the archive the day it is added.

So this pushes **one distinct value per `PAYLOAD` field** through every site
and asserts each value where it should survive and its absence where it
should not. A thirteenth site written by hand fails the source check at the
end on the day it is written.
"""

import copy
import inspect
import json

from dgraph import integrate, pending
from dgraph.json_import import SCHEMA, read
from dgraph.model import CLAIM, EDGE_FIELDS, PAYLOAD, Graph
from tests.conftest import FIXTURE

#: One recognisable value per field. `date`, `format` and `probe` have to be
#: legal; `probe` is the one that is not a string.
VALUES = {"answer": "A-value", "falsifier": "F-value", "source": "S-value",
          "date": "2026-02-02", "format": "org",
          "probe": {"kind": "prose.rule", "args": {"n": 1}}}


def test_the_tuples_nest():
    assert set(CLAIM) < set(PAYLOAD) < set(EDGE_FIELDS)
    assert set(VALUES) == set(PAYLOAD), "give the new field a value here"


def _close(vid="D05", to=("D06",)):
    return {"op": "close", "vertex": vid, "to": list(to), **VALUES}


def test_a_close_writes_every_field():
    g = Graph.from_dict(FIXTURE)
    out = pending.apply_all(g, [_close()])
    e = out.active_edge("D05")
    assert {k: getattr(e, k) for k in PAYLOAD} == VALUES


def test_a_reopen_archives_every_field_and_clears_every_field():
    g = pending.apply_all(Graph.from_dict(FIXTURE), [_close()])
    out = pending.apply_all(g, pending.expand(
        g, {"op": "reopen", "vertex": "D05", "why": "moved", "format": "md"}))
    live = out.active_edge("D05")
    assert all(getattr(live, k) is None for k in PAYLOAD)
    old = out.history("D05")[-1]
    # `format` is the one field the archive owns for itself: it is the
    # reopen's dialect, covering the `why` composed there.
    assert {k: getattr(old, k) for k in PAYLOAD} == {**VALUES, "format": "md"}


def test_a_reject_files_every_field():
    g = pending.apply_all(Graph.from_dict(FIXTURE), [_close()])
    out = pending.apply_all(g, [{
        "op": "reject", "vertex": "D05", "to": ["D06"],
        **{k: v + "-theirs" if k in CLAIM and isinstance(v, str) else v
           for k, v in VALUES.items()},
        "from_source": "elsewhere"}])
    r = out.rejected("D05")[0]
    assert r.answer == "A-value-theirs" and r.falsifier == "F-value-theirs"
    assert r.source == "S-value-theirs" and r.date == VALUES["date"]
    assert r.format == VALUES["format"] and r.probe == VALUES["probe"]


def test_the_seam_derives_a_close_carrying_every_field():
    base = Graph.from_dict(FIXTURE)
    theirs = pending.apply_all(base, [_close()])
    ops = integrate.decisions(base, theirs).ops
    close = next(o for o in ops if o["op"] == "close")
    assert {k: close[k] for k in PAYLOAD} == VALUES


def test_the_keepsake_carries_every_field():
    kept = integrate._keepsake(_close(), {"source": "them"})
    assert {k: kept[k] for k in PAYLOAD} == VALUES
    assert kept["from_source"] == "them"


def test_a_same_answer_is_judged_on_the_claim():
    """`already` and `_same_answer` compare `CLAIM`; a field outside it —
    the date — does not make two answers different."""
    g = pending.apply_all(Graph.from_dict(FIXTURE), [_close()])
    again = pending.apply_all(g, [])
    e = again.active_edge("D05")
    e.date = "2027-01-01"
    assert integrate._same_answer(g.active_edge("D05"), e)
    e.falsifier = "other"
    assert not integrate._same_answer(g.active_edge("D05"), e)


def test_export_and_import_round_trip_every_field(tmp_path):
    g = pending.apply_all(Graph.from_dict(FIXTURE), [_close()])
    path = tmp_path / "decisions.json"
    path.write_text(json.dumps(g.to_dict()))
    back = read(path, "decisions").graph
    assert {k: getattr(back.active_edge("D05"), k) for k in PAYLOAD} == VALUES
    assert set(PAYLOAD) <= set(SCHEMA["decisions"]["edge_optional"])


def test_no_site_names_a_payload_field_by_hand():
    """The guard against a thirteenth site. Each function below used to spell
    the fields out — `falsifier=e.falsifier`, `op.get("date")` — and now
    reads the tuple, so a copy of a payload field by name must not reappear
    in its source. `answer` is exempt: `_payload` indexes it because it is
    required, and the reversal label reads it. What is *not* a copy is
    allowed to stay: `payload["date"]` defaulting to today, and the
    contribution's own `raw.get("source")`."""
    import re
    copied = re.compile(r'op(\.get\(|\[)"(falsifier|source|date|probe)"'
                        r'|(?<![\w_])(falsifier|source|date|probe)=(?!=)'
                        r'|getattr\([^,]+, "(falsifier|source|date|probe)"')
    sites = [pending._apply_one, integrate._edges, integrate._keepsake,
             integrate._same_answer]
    for fn in sites:
        for line in inspect.getsource(fn).splitlines():
            assert not copied.search(line), (
                f"{fn.__name__} copies a payload field by hand — read "
                f"PAYLOAD instead: {line.strip()}")
