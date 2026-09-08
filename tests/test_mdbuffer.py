"""The compose buffer as markdown: what an editor that is not emacs is handed.

`dgraph/mdbuffer.py` converts the org rendering and reads the result back.
The tests here are the round trips — a field typed under `## Answer` arrives
as the same op `** Answer` would have produced, minus the org tag — and the
rule that picks the dialect from the editor.
"""

import os
import re

import pytest

from dgraph import editor, mdbuffer, project, task_editor
from dgraph.editor import EditorError
from dgraph.tasks import TaskGraph


def fill_md(text: str, **fields: str) -> str:
    """Type `body` under `## Field`, the way a person would."""
    for name, body in fields.items():
        head = f"## {name.capitalize()}\n"
        assert head in text, f"no {head.strip()!r} in the markdown buffer"
        text = text.replace(head, head + body.rstrip("\n") + "\n", 1)
    return text


# ---- the rendering ---------------------------------------------------------


def test_the_buffer_is_markdown_shaped(g):
    md = mdbuffer.render(editor.render_close(g, "D05"))
    assert md.startswith("---\nop: close\nvertex: D05\n")
    assert "# Input\n" in md and "## Answer\n" in md and "# Context\n" in md
    # Nothing of org's: no mode line, no keyword, no drawer, no key bindings.
    for org_only in ("-*- mode", "#+TODO", ":PROPERTIES:", "C-c ", "Full org",
                     "[[dg:", "single asterisks"):
        assert org_only not in md, org_only
    # The guidance is there, as blockquote lines an editor shows and never
    # conceals — the value slot sits above them, under the heading (D100).
    assert "## Answer\n\n> What was decided, and on what evidence. Markdown is fine." in md
    assert "<!--" not in md and "-->" not in md
    # the guidance names both markdown markers a value must escape (D101)
    assert 'Escape as "\\#" / "\\>"' in md
    # The checklist is markdown's own syntax and survives untouched.
    assert re.search(r"^- \[ \] D03 — A terminal one$", md, re.M)


def test_the_context_is_readable_as_markdown(g):
    md = mdbuffer.render(editor.render_close(g, "D06"))
    ctx = md[md.index("# Context"):]
    assert "## DECIDED D01 — Root question" not in ctx  # D06 rests on D05, not D01
    assert re.search(r"^## \w+ D05 — Still open$", ctx, re.M)
    assert ":PROPERTIES:" not in ctx and ":END:" not in ctx


# ---- the round trips -------------------------------------------------------


def test_a_close_typed_in_markdown_parses_to_the_same_op_untagged(g, store):
    md = mdbuffer.render(editor.render_close(g, "D05"))
    md = fill_md(md, answer="Chose it.\n\\# a literal hash, not a heading",
                 source="discussion", falsifier="it stops working")
    md = md.replace("- [ ] D03 — A terminal one", "- [X] D03 — A terminal one")
    (op,) = editor.parse(md, g=g, expect_kind="close", expect_vertex="D05",
                         dialect="markdown")
    assert op["answer"] == "Chose it.\n# a literal hash, not a heading"
    assert op["source"] == "discussion" and op["falsifier"] == "it stops working"
    assert op["to"] == ["D03", "D06"]          # picked, plus the linked child
    assert "format" not in op                   # markdown is the store's untagged


def test_the_comments_are_not_prose(g, store):
    md = mdbuffer.render(editor.render_close(g, "D05"))
    md = fill_md(md, answer="typed", source="s", falsifier="f")
    (op,) = editor.parse(md, g=g, dialect="markdown")
    assert op["answer"] == "typed"              # the <!-- hint --> is gone


# A value line that begins like the buffer's own syntax — `*` a heading, `#` a
# comment, `>` a markdown hint — must survive render→parse unchanged (D101,
# audit AB-F1/AB-F2). Each marker, in each dialect, both seeded into a fresh
# compose and through `dg edit`'s render_op→parse, which is where an existing
# op was silently truncated before.
_MARKERS = [
    "#1 no space", "# with space", "#only",
    "> a quote", "*bold* start", "* leading star",
    ",#already commaed", "plain\n#mid-line\nmore", "ordinary prose",
    # `AC-F6`: markdown's own escaped forms are stored values too, and the
    # buffer's escape has to be injective over them.
    "\\#literal hash", "\\> literal gt", "\\\\# two slashes",
    # `AC-F7`(a): two blank lines inside a value are the value's.
    "para one\n\n\npara two",
]


@pytest.mark.parametrize("value", _MARKERS)
@pytest.mark.parametrize("dialect", ["org", "markdown"])
def test_a_value_beginning_like_the_syntax_round_trips_seeded(g, store, value, dialect):
    seed = {"answer": value, "source": "s", "falsifier": "f"}
    org = editor.render_close(g, "D05", seed)
    buf = mdbuffer.render(org) if dialect == "markdown" else org
    (op,) = editor.parse(buf, g=g, dialect=dialect)
    assert op["answer"] == value, f"{dialect}: seeded {value!r} did not round-trip"


@pytest.mark.parametrize("value", _MARKERS)
@pytest.mark.parametrize("dialect", ["org", "markdown"])
def test_a_value_beginning_like_the_syntax_round_trips_through_edit(g, store, value, dialect):
    """`dg edit` renders an existing op and parses it back — the path that
    truncated a `#`-line before AB-F1 was fixed."""
    op = {"op": "close", "vertex": "D05", "answer": value,
          "source": "s", "falsifier": "f", "to": []}
    org = editor.render_op(g, 0, op)
    buf = mdbuffer.render(org) if dialect == "markdown" else org
    back = editor.parse(buf, g=g, expect_index=0, dialect=dialect)[0]
    assert back["answer"] == value, f"{dialect}: dg edit changed {value!r} to {back.get('answer')!r}"


def test_a_reopen_and_an_add_typed_in_markdown(g, store):
    md = fill_md(mdbuffer.render(editor.render_reopen(g, "D01")),
                 why="the premise moved")
    (op,) = editor.parse(md, g=g, expect_kind="reopen", dialect="markdown")
    assert op == {"op": "reopen", "vertex": "D01", "why": "the premise moved"}

    md = mdbuffer.render(editor.render_add(g))
    md = fill_md(md, title="A question", area="Alpha", note="with a *note*")
    ops = editor.parse(md, g=g, expect_kind="add_vertex", dialect="markdown")
    assert ops[0]["note"] == "with a *note*" and "format" not in ops[0]


def test_a_revision_carries_its_index_in_the_front_matter(g, store):
    op = {"op": "close", "vertex": "D05", "answer": "a", "source": "s",
          "falsifier": "f", "to": []}
    md = mdbuffer.render(editor.render_op(g, 3, op))
    assert mdbuffer.meta(md)["index"] == "3"
    assert editor.parse(md.replace("\na\n", "\nrevised\n", 1), g=g,
                        expect_index=3, dialect="markdown")[0]["answer"] == "revised"
    with pytest.raises(EditorError, match="staged op 3, not 4"):
        editor.parse(md, g=g, expect_index=4, dialect="markdown")


def test_refusals_spell_the_headings_the_person_typed(g, store):
    md = mdbuffer.render(editor.render_close(g, "D05"))
    with pytest.raises(EditorError, match=r"## Bogus"):
        editor.parse(md.replace("## Summary", "## Bogus"), g=g, dialect="markdown")
    with pytest.raises(EditorError, match="front matter"):
        editor.parse(md[md.index("# Input"):], g=g, dialect="markdown")
    with pytest.raises(EditorError, match="no `# Input`"):
        editor.parse(md.replace("# Input", "# Inputs"), g=g, dialect="markdown")


def test_task_buffers_have_the_same_twin(tg, task_store):
    md = fill_md(mdbuffer.render(task_editor.render_done(tg, None, "T02")),
                 outcome="PR #7, merged")
    (op,) = task_editor.parse(md, tg=tg, g=None, expect_kind="set_status",
                              expect_task="T02", dialect="markdown")
    assert op["outcome"] == "PR #7, merged" and "format" not in op

    md = mdbuffer.render(task_editor.render_add(tg, None))
    md = fill_md(md, title="A piece of work", area="Alpha", note="why")
    ops = task_editor.parse(md, tg=tg, g=None, expect_kind="add_task",
                            dialect="markdown")
    assert ops[0]["note"] == "why" and "format" not in ops[0]


# ---- the rule --------------------------------------------------------------


def test_the_dialect_follows_the_editor(monkeypatch):
    for var in ("DG_EDIT_FORMAT", "DG_EDIT_CMD", "DG_EDITOR", "VISUAL", "EDITOR"):
        monkeypatch.delenv(var, raising=False)
    assert editor.cli_dialect() == "org"                 # the default is emacs
    monkeypatch.setenv("EDITOR", "vim")
    assert editor.cli_dialect() == "markdown"
    monkeypatch.setenv("DG_EDITOR", "emacs -nw")
    assert editor.cli_dialect() == "org"
    monkeypatch.setenv("DG_EDIT_CMD", "code --wait {file}")
    assert editor.cli_dialect() == "markdown"            # the command, not $EDITOR
    monkeypatch.setenv("DG_EDIT_FORMAT", "org")
    assert editor.cli_dialect() == "org"                 # the override wins
    monkeypatch.setenv("DG_EDIT_FORMAT", "asciidoc")
    with pytest.raises(EditorError, match="org or markdown"):
        editor.cli_dialect()


def test_the_browser_door_follows_its_own_editor(monkeypatch):
    for var in ("DG_EDIT_FORMAT", "DG_EDIT_CMD", "DG_GUI_EDITOR", "DG_EDITOR",
                "VISUAL", "EDITOR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(editor.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setenv("EDITOR", "vim")
    assert editor.gui_dialect() == "markdown"
    monkeypatch.setenv("DG_GUI_EDITOR", "emacs")
    assert editor.gui_dialect() == "org"


def test_compose_writes_the_markdown_buffer_for_a_markdown_editor(g, store, monkeypatch):
    monkeypatch.setenv("DG_EDIT_FORMAT", "markdown")
    seen = {}

    def launch(path):
        seen["path"] = path
        path.write_text(fill_md(path.read_text(encoding="utf-8"),
                                answer="typed in vim", source="s",
                                falsifier="f"),
                        encoding="utf-8")
        return 0
    monkeypatch.setattr(editor, "launch", launch)
    ops = editor.compose(g, "close", vertex="D05")
    assert seen["path"] == store / ".dgraph-edit.md"
    assert ops[0]["answer"] == "typed in vim" and "format" not in ops[0]
    assert project.find().buffer("markdown") == store / ".dgraph-edit.md"
    assert project.find().buffer("org") == store / ".dgraph-edit.org"


def test_one_lock_guards_both_buffers(g, store, monkeypatch):
    """One buffer per project however it is spelled: an org session's lock
    refuses a markdown compose, since they are the same edit."""
    monkeypatch.setenv("DG_EDIT_FORMAT", "markdown")
    monkeypatch.setattr(editor, "launch", lambda p: 0)
    (store / ".dgraph-edit.lock").write_text(str(os.getpid()), encoding="utf-8")
    with pytest.raises(EditorError, match="already open"):
        editor.compose(g, "close", vertex="D05")
    (store / ".dgraph-edit.lock").unlink()
