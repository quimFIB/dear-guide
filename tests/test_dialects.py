"""Two buffers, one store, and no prose mislabelled between them.

The store tags prose with the dialect it was typed in (`format: "org"`, else
markdown) so the views can convert it. Two compose buffers — org for emacs,
markdown for everything else — are two ways for prose in one dialect to meet
a record, a seed or a tag in the other. Every meeting has a rule, and every
rule is here:

- **A seed is shown in the buffer's dialect** (`editor.seed_in`): an op
  composed in emacs and revised in vim is converted to markdown on the way
  in, and what comes back is markdown, untagged.
- **The Context is uniformly org** in the org buffer, so `mdbuffer` can
  convert all of it (`editor._context`).
- **The tag follows the last writer, and the rest follows the tag**
  (`pending._retag`, `task_pending._retag`): a record's one tag covers
  several fields, so a field written in the other dialect converts the
  fields beside it rather than falsifying the tag for them.

`orgmd.to_org` is the conversion the last two need in the direction the
module did not have.
"""

import pytest

from dgraph import editor, mdbuffer, orgmd, pending, task_editor, task_pending
from dgraph.tasks import Stop


# ---- the reverse conversion ------------------------------------------------


@pytest.mark.parametrize("md, org", [
    ("**bold** and *italic* and _under_", "*bold* and /italic/ and /under/"),
    ("`code` span", "=code= span"),
    ("[D04](#d04) and [the report](bench/x.md) and [site](https://x.y/)",
     "[[dg:D04][D04]] and [[file:bench/x.md][the report]] and [[https://x.y/][site]]"),
    ("| a | b |\n|---|:--|\n| 1 | 2 |", "| a | b |\n|---+---|\n| 1 | 2 |"),
    ("2*3*4 and a/b and lr=0.001", "2*3*4 and a/b and lr=0.001"),   # not emphasis
    ("plain prose, nothing to do", "plain prose, nothing to do"),
])
def test_to_org_is_to_markdowns_inverse(md, org):
    assert orgmd.to_org(md) == org
    assert orgmd.to_markdown(org, fmt="org") == md.replace("_under_", "_under_") \
        .replace("*italic*", "_italic_").replace("|:--|", "|---|")


def test_convert_is_the_identity_within_a_dialect():
    assert orgmd.convert("*x*", "org", "org") == "*x*"
    assert orgmd.convert("*x*", None, None) == "*x*"
    assert orgmd.convert("*x*", "org", None) == "**x**"
    assert orgmd.convert("**x**", None, "org") == "*x*"
    assert orgmd.convert(None, "org", None) is None


# ---- seeds -----------------------------------------------------------------


def test_an_org_op_revised_in_markdown_is_shown_and_read_as_markdown(g, store, monkeypatch):
    """`dg edit N` in vim on an op composed in emacs."""
    monkeypatch.setenv("DG_EDIT_FORMAT", "markdown")
    op = {"op": "close", "vertex": "D05", "answer": "*HNSW* it is, see [[dg:D02][D02]]",
          "source": "s", "falsifier": "=recall= < 0.95", "to": ["D06"], "format": "org"}
    shown = {}

    def launch(path):
        shown["text"] = path.read_text(encoding="utf-8")
        path.write_text(shown["text"].replace("HNSW", "HNSW, revised"), encoding="utf-8")
        return 0
    monkeypatch.setattr(editor, "launch", launch)
    (new,) = editor.compose(g, "close", vertex="D05", index=0, op=op)
    assert "**HNSW** it is, see [D02](#d02)" in shown["text"]
    assert new["answer"] == "**HNSW, revised** it is, see [D02](#d02)"
    assert new["falsifier"] == "`recall` < 0.95"
    assert "format" not in new


def test_a_markdown_seed_is_shown_and_read_as_org_in_emacs(g, store, monkeypatch):
    """The web form's draft, carried into emacs; or `--answer` beside `--edit`."""
    monkeypatch.setenv("DG_EDIT_FORMAT", "org")
    shown = {}

    def launch(path):
        shown["text"] = path.read_text(encoding="utf-8")
        path.write_text(shown["text"] + "\n", encoding="utf-8")   # saved as shown
        return 0
    monkeypatch.setattr(editor, "launch", launch)
    seed = {"answer": "**bold** and [the sweep](bench/x.md)", "source": "s",
            "falsifier": "f"}
    (op,) = editor.compose(g, "close", vertex="D05", seed=seed)
    assert "*bold* and [[file:bench/x.md][the sweep]]" in shown["text"]
    assert op["answer"] == "*bold* and [[file:bench/x.md][the sweep]]"
    assert op["format"] == "org"


def test_task_seeds_follow_the_same_rule(tg, task_store, monkeypatch):
    monkeypatch.setenv("DG_EDIT_FORMAT", "org")
    shown = {}

    def launch(path):
        shown["text"] = path.read_text(encoding="utf-8")
        path.write_text(shown["text"] + "\n", encoding="utf-8")
        return 0
    monkeypatch.setattr(editor, "launch", launch)
    (op,) = task_editor.compose_done(tg, None, "T02", seed={"outcome": "**PR #7**"})
    assert "*PR #7*" in shown["text"] and op["outcome"] == "*PR #7*"
    assert op["format"] == "org"


def test_the_amend_buffer_shows_the_record_in_the_buffers_dialect(g, store, monkeypatch):
    """`AC-F4`: `dg amend --edit` in vim on a record whose note is org. The
    note is shown as markdown; untouched it is not staged; touched it comes
    back markdown, untagged — the rule `seed_in` states for both doors."""
    import re
    pending._apply_one(g, {"op": "set_fields", "vertex": "D05",
                           "note": "this is *bold* in org", "format": "org"})
    monkeypatch.setenv("DG_EDIT_FORMAT", "markdown")
    shown = {}

    def retitle(path):
        shown["text"] = path.read_text(encoding="utf-8")
        path.write_text(re.sub(r"(## Title\n)(.*)", r"\1\2 renamed", shown["text"], count=1),
                        encoding="utf-8")
        return 0
    monkeypatch.setattr(editor, "launch", retitle)
    (op,) = editor.compose(g, "amend", vertex="D05")
    assert "this is **bold** in org" in shown["text"], shown["text"]
    assert "note" not in op and "format" not in op and op["title"].endswith(" renamed")

    def touch(path):
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("this is **bold** in org",
                                     "this is **bold** in org, plus"), encoding="utf-8")
        return 0
    monkeypatch.setattr(editor, "launch", touch)
    (op,) = editor.compose(g, "amend", vertex="D05")
    assert op["note"] == "this is **bold** in org, plus" and "format" not in op

    # and the other way: a markdown record amended in emacs
    pending._apply_one(g, {"op": "set_fields", "vertex": "D05", "note": "a **md** note"})
    monkeypatch.setenv("DG_EDIT_FORMAT", "org")

    def touch_org(path):
        shown["text"] = path.read_text(encoding="utf-8")
        path.write_text(shown["text"].replace("a *md* note", "a *md* note, plus"),
                        encoding="utf-8")
        return 0
    monkeypatch.setattr(editor, "launch", touch_org)
    (op,) = editor.compose(g, "amend", vertex="D05")
    assert "a *md* note" in shown["text"]
    assert op["note"] == "a *md* note, plus" and op["format"] == "org"


def test_the_task_amend_buffer_and_the_task_edit_follow_the_same_rule(
        tg, task_store, monkeypatch):
    """`AC-F4`, task side: `compose_amend` over an org record and
    `compose_edit` over an org op, both in markdown."""
    task_pending._apply_one(tg, {"op": "set_fields", "task": "T04",
                                 "note": "this is *bold* in org", "format": "org"})
    monkeypatch.setenv("DG_EDIT_FORMAT", "markdown")
    shown = {}

    def touch(path):
        shown["text"] = path.read_text(encoding="utf-8")
        path.write_text(shown["text"].replace("this is **bold** in org",
                                              "this is **bold** in org, plus"),
                        encoding="utf-8")
        return 0
    monkeypatch.setattr(editor, "launch", touch)
    (op,) = task_editor.compose_amend(tg, None, "T04")
    assert "this is **bold** in org" in shown["text"], shown["text"]
    assert op["note"] == "this is **bold** in org, plus" and "format" not in op

    staged = {"op": "add_task", "id": "T50", "title": "t", "area": "Alpha",
              "note": "also *bold*", "format": "org"}

    def retitle(path):
        shown["text"] = path.read_text(encoding="utf-8")
        path.write_text(shown["text"].replace("## Title\nt\n", "## Title\nt revised\n"),
                        encoding="utf-8")
        return 0
    monkeypatch.setattr(editor, "launch", retitle)
    (op,) = task_editor.compose_edit(tg, None, 0, staged)
    assert "also **bold**" in shown["text"], shown["text"]
    assert op["note"] == "also **bold**" and "format" not in op


# ---- the context -----------------------------------------------------------


def test_the_context_is_org_in_the_org_buffer_and_markdown_in_the_other(g, store):
    e = g.active_edge("D04")                         # D05 rests on it
    e.answer, e.format = "**Third** answer, see `x`", None
    g.vertices["D05"].note, g.vertices["D05"].format = "a *markdown* note", None
    org = editor.render_close(g, "D05")
    ctx = org[org.index("* Context"):]
    assert "*Third* answer, see =x=" in ctx and "a /markdown/ note" in ctx
    md = mdbuffer.render(org)
    ctx = md[md.index("# Context"):]
    assert "**Third** answer, see `x`" in ctx and "a _markdown_ note" in ctx


# ---- the store -------------------------------------------------------------


def _vertex_with_org_rule(g):
    pending._apply_one(g, {"op": "set_fields", "vertex": "D05",
                           "note": "*org* note", "rule": "*org* rule",
                           "format": "org"})
    v = g.vertices["D05"]
    assert (v.note, v.rule, v.format) == ("*org* note", "*org* rule", "org")
    return v


def test_a_note_written_as_markdown_converts_the_rule_beside_it(g):
    _vertex_with_org_rule(g)
    pending._apply_one(g, {"op": "set_fields", "vertex": "D05", "note": "a **md** note"})
    v = g.vertices["D05"]
    assert (v.note, v.rule, v.format) == ("a **md** note", "**org** rule", None)
    # and back: an org op over a markdown record converts the other way
    pending._apply_one(g, {"op": "set_fields", "vertex": "D05",
                           "rule": "/new/ rule", "format": "org"})
    v = g.vertices["D05"]
    assert (v.note, v.rule, v.format) == ("a *md* note", "/new/ rule", "org")


def test_a_title_correction_leaves_the_dialect_alone(g):
    _vertex_with_org_rule(g)
    pending._apply_one(g, {"op": "set_fields", "vertex": "D05", "title": "Renamed"})
    v = g.vertices["D05"]
    assert (v.rule, v.format) == ("*org* rule", "org")


def test_a_rule_alone_is_tagged_and_kept_readable_through_a_close(g):
    pending._apply_one(g, {"op": "add_vertex", "id": "D07", "title": "t",
                           "area": "Alpha", "rule": "*settle* it", "format": "org"})
    assert g.vertices["D07"].format == "org"
    pending._apply_one(g, {"op": "close", "vertex": "D07", "answer": "done",
                           "source": "s", "to": [], "date": "2026-09-08"})
    v = g.vertices["D07"]
    assert (v.rule, v.format, v.note) == ("**settle** it", None, None)


def test_a_reopen_in_markdown_converts_what_it_archives(g):
    e = g.active_edge("D01")
    e.answer, e.falsifier, e.format = "*The* root answer", "=x= appears", "org"
    _vertex_with_org_rule(g)                          # D05; D01 gets its own
    pending._apply_one(g, {"op": "set_fields", "vertex": "D01",
                           "rule": "*root* rule", "format": "org"})
    pending._apply_one(g, {"op": "reopen", "vertex": "D01", "why": "it **moved**"})
    old = g.history("D01")[-1]
    assert (old.answer, old.falsifier, old.format) == ("**The** root answer", "`x` appears", None)
    v = g.vertices["D01"]
    assert (v.note, v.rule, v.format) == ("it **moved**", "**root** rule", None)


def test_a_markdown_outcome_converts_the_org_note_beside_it(tg):
    t = tg.tasks["T04"]
    t.note, t.format = "*org* note", "org"
    t.stops.append(Stop(why="was *parked*", date="2026-01-01"))
    task_pending._apply_one(tg, {"op": "set_status", "task": "T04", "status": "DONE",
                                 "outcome": "a **md** outcome", "done": "2026-09-08"})
    assert (t.note, t.outcome, t.format) == ("**org** note", "a **md** outcome", None)
    assert t.stops[0].why == "was **parked**"


def test_an_org_outcome_converts_the_markdown_note_beside_it(tg):
    t = tg.tasks["T02"]
    t.note, t.done_when = "a **md** note", "`x` passes"
    task_pending._apply_one(tg, {"op": "set_status", "task": "T02", "status": "DONE",
                                 "outcome": "*PR #7*", "done": "2026-09-08",
                                 "format": "org"})
    assert (t.note, t.done_when, t.outcome, t.format) == \
        ("a *md* note", "=x= passes", "*PR #7*", "org")


def test_parking_with_a_reason_is_writing_prose(tg):
    t = tg.tasks["T04"]
    t.note, t.format = "*org* note", "org"
    task_pending._apply_one(tg, {"op": "set_status", "task": "T04", "status": "PARKED",
                                 "why": "waiting on **hardware**", "date": "2026-09-08"})
    assert (t.note, t.format) == ("**org** note", None)
    assert t.stops[-1].why == "waiting on **hardware**"


def test_a_task_correction_of_prose_retags_and_of_a_title_does_not(tg):
    t = tg.tasks["T04"]
    t.note, t.format = "*org* note", "org"
    task_pending._apply_one(tg, {"op": "set_fields", "task": "T04", "title": "Renamed"})
    assert (t.note, t.format) == ("*org* note", "org")
    task_pending._apply_one(tg, {"op": "set_fields", "task": "T04",
                                 "done_when": "**green**"})
    assert (t.note, t.done_when, t.format) == ("**org** note", "**green**", None)
