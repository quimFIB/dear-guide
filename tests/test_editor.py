"""The editor round trip, driven without an editor installed.

`fake_emacs` replaces the launcher, so every rule below is exercised in CI on a
machine with no emacs. One test drives the real `launch` through `$DG_EDIT_CMD`
to cover the subprocess path itself.
"""

import os
import shutil
import subprocess

import pytest

from dgraph import editor, pending, project
from dgraph.editor import EditorAbort, EditorError
from dgraph.model import Graph


@pytest.fixture
def fake_emacs(monkeypatch):
    """Install a stand-in editor. `edit` is str -> str: what the user did."""
    def install(edit):
        def launch(path):
            path.write_text(edit(path.read_text(encoding="utf-8")), encoding="utf-8")
            return 0
        monkeypatch.setattr(editor, "launch", launch)
        return launch
    return install


def fill(text: str, **fields: str) -> str:
    """Type `body` under `** Field`, the way a person would."""
    for name, body in fields.items():
        head = f"** {name.capitalize()}\n"
        assert head in text, f"no {head.strip()!r} in template"
        text = text.replace(head, head + body.rstrip("\n") + "\n", 1)
    return text


def complete(text: str) -> str:
    return fill(text, answer="Settle on 32k.", source="discussion",
                falsifier="the corpus changes")


# ---- rendering -----------------------------------------------------------


def test_template_carries_the_metadata_the_parser_needs(g, store):
    t = editor.render_close(g, "D05")
    assert ":DGRAPH_OP: close" in t and ":DGRAPH_VERTEX: D05" in t
    assert t.index("* Input") < t.index("* Context")   # fields before reference


def test_context_shows_the_edge_that_led_here(g, store):
    t = editor.render_close(g, "D05")
    ctx = t[t.index("* Context"):]
    assert "[[dg:D04][D04 — Downstream]]" in ctx
    assert "Third answer." in ctx          # the premise's own answer
    assert ":FALSIFIER: the corpus changes" in ctx


def test_context_carries_the_ancestor_chain(g, store):
    ctx = editor.render_close(g, "D05")
    for vid in ("D01", "D02", "D04"):
        assert f"[[dg:{vid}]" in ctx
    assert "| depth | node | status | date |" in ctx


def test_already_linked_children_are_pre_ticked(g, store):
    t = editor.render_close(g, "D05")
    assert "- [X] D06 — Waiting on D05   (linked)" in t
    assert "- [ ] D01 — Root question" in t


def test_reopen_template_leads_with_the_propagation_set(g, store):
    t = editor.render_reopen(g, "D01")
    assert "Becomes PROVISIONAL if you do this" in t
    body = t[t.index("Becomes PROVISIONAL"):]
    assert "D02, D03, D04" in body


def test_add_template_offers_the_next_free_id_and_the_areas(g, store):
    t = editor.render_add(g)
    assert "** Id" in t and "D07" in t          # D01..D06 exist
    assert "One of: Alpha, Beta" in t


# ---- the round trip ------------------------------------------------------


def test_close_round_trips_into_an_appliable_op(g, store, fake_emacs):
    fake_emacs(complete)
    ops = editor.compose(g, "close", vertex="D05")
    assert ops == [{
        "op": "close", "vertex": "D05", "answer": "Settle on 32k.",
        "source": "discussion", "falsifier": "the corpus changes",
        "to": ["D06"], "date": ops[0]["date"],
    }]
    out = pending.apply_all(g, pending.expand(g, ops[0]))
    assert out.vertices["D05"].status == "DECIDED"
    assert out.validate() == []


def test_org_prose_is_stored_verbatim(g, store, fake_emacs):
    """Full org is allowed and is not rewritten on the way in. The views convert."""
    answer = "Per [[file:report/x.md][the sweep]].\n\n| opt | ppl |\n|-----+-----|\n| 32k | 8.1 |"
    fake_emacs(lambda t: fill(t, answer=answer, source="discussion",
                              falsifier="a corpus shift"))
    ops = editor.compose(g, "close", vertex="D05")
    assert ops[0]["answer"] == answer


def test_reopen_round_trips(g, store, fake_emacs):
    fake_emacs(lambda t: fill(t, why="the sweep was mis-seeded",
                              summary="the old answer"))
    ops = editor.compose(g, "reopen", vertex="D01")
    assert ops[0] == {"op": "reopen", "vertex": "D01",
                      "why": "the sweep was mis-seeded", "summary": "the old answer"}


def test_add_round_trips_into_two_ops(g, store, fake_emacs):
    fake_emacs(lambda t: fill(t, title="A new question", area="Beta",
                              after="D05"))
    ops = editor.compose(g, "add_vertex")
    assert ops[0]["op"] == "add_vertex" and ops[0]["area"] == "Beta"
    assert ops[1] == {"op": "add_edge", "from": "D05", "to": [ops[0]["id"]]}


def test_comments_and_org_escapes_are_stripped(g, store, fake_emacs):
    fake_emacs(lambda t: fill(t, answer="Real prose.\n# not this\n,* a literal star",
                              source="discussion", falsifier="f"))
    ops = editor.compose(g, "close", vertex="D05")
    assert ops[0]["answer"] == "Real prose.\n* a literal star"


@pytest.mark.parametrize("answer", [
    "First line.\n* reads as a heading unescaped\nmore prose",
    ",* a stored literal comma-star line",
    "* leading star\n,* comma-star\n,,* two commas",
])
def test_stored_star_lines_survive_a_re_render(g, store, fake_emacs, answer):
    """Audit A4. The parser unescapes `,*`, but the renderer never applied the
    inverse to seeded bodies — so `dg edit N` on a close whose answer carried a
    `* ` line handed the parser a heading: Answer truncated at that line, every
    later field swallowed ("Source is empty"), the op uneditable. One comma is
    added per render and removed per parse, so any stored text round-trips."""
    op = {"op": "close", "vertex": "D05", "answer": answer,
          "source": "discussion", "falsifier": "f", "to": [],
          "date": "2026-08-18"}
    fake_emacs(lambda t: t + "# touched\n")       # revise nothing, just save
    ops = editor.compose(g, "close", vertex="D05", index=0, op=op)
    assert ops[0]["answer"] == answer
    assert ops[0]["source"] == "discussion"       # the field after Answer survives


def test_a_deeper_heading_inside_a_field_stays_in_the_body(g, store, fake_emacs):
    fake_emacs(lambda t: fill(t, answer="Lead.\n*** Detail\nmore", source="s",
                              falsifier="f"))
    ops = editor.compose(g, "close", vertex="D05")
    assert "*** Detail" in ops[0]["answer"]


# ---- only Input is read --------------------------------------------------


def test_mangling_context_cannot_change_the_op(g, store, fake_emacs):
    """The actual guarantee. The emacs read-only guard is a convenience."""
    def wreck(t):
        t = complete(t)
        head, ctx = t.split("* Context", 1)
        return head + "* Context\n** Answer\nLIES\n** Source\nLIES\n" + ctx
    fake_emacs(wreck)
    ops = editor.compose(g, "close", vertex="D05")
    assert ops[0]["answer"] == "Settle on 32k." and ops[0]["source"] == "discussion"


def test_unchecking_a_linked_child_does_not_drop_the_edge(g, store, fake_emacs):
    """`_apply_one` unions to with the edge's targets, so the buffer must not
    imply a dependency can be removed by editing a checkbox."""
    fake_emacs(lambda t: complete(t).replace("- [X] D06", "- [ ] D06"))
    ops = editor.compose(g, "close", vertex="D05")
    assert ops[0]["to"] == ["D06"]


# ---- refusals ------------------------------------------------------------


def test_a_second_compose_is_refused_while_one_is_open(g, store):
    """Audit C4. The web app has always refused a second session; the CLI
    overwrote the buffer being typed in. One pid-stamped lock now covers both,
    across processes."""
    def nested(path):
        with pytest.raises(EditorError, match="already open"):
            editor.compose(g, "reopen", vertex="D01", launcher=lambda p: 0)
        path.write_text(complete(path.read_text(encoding="utf-8")),
                        encoding="utf-8")
        return 0

    ops = editor.compose(g, "close", vertex="D05", launcher=nested)
    assert ops[0]["vertex"] == "D05"                 # the outer session finished
    assert not (store / ".dgraph-edit.org.lock").exists()   # and released


def test_a_crashed_sessions_lock_is_reclaimed(g, store, fake_emacs):
    """A lock whose pid is gone is debris, not a session."""
    dead = subprocess.Popen(["true"])
    dead.wait()
    (store / ".dgraph-edit.org.lock").write_text(str(dead.pid), encoding="utf-8")
    fake_emacs(complete)
    assert editor.compose(g, "close", vertex="D05")[0]["op"] == "close"
    assert not (store / ".dgraph-edit.org.lock").exists()


def test_a_live_sessions_lock_refuses_and_names_the_pid(g, store, fake_emacs):
    (store / ".dgraph-edit.org.lock").write_text(str(os.getpid()), encoding="utf-8")
    fake_emacs(complete)
    with pytest.raises(EditorError, match=str(os.getpid())):
        editor.compose(g, "close", vertex="D05")
    (store / ".dgraph-edit.org.lock").unlink()


def test_an_unreadable_lock_is_never_stolen(g, store, fake_emacs):
    """Stealing on a parse failure could race a live session; the safe answer
    is to make a human look."""
    (store / ".dgraph-edit.org.lock").write_text("not a pid", encoding="utf-8")
    fake_emacs(complete)
    with pytest.raises(EditorError, match="unreadable"):
        editor.compose(g, "close", vertex="D05")
    (store / ".dgraph-edit.org.lock").unlink()


def test_untouched_template_aborts(g, store, fake_emacs):
    fake_emacs(lambda t: t + "\n")          # changed, but no field filled
    with pytest.raises(EditorAbort, match="untouched"):
        editor.compose(g, "close", vertex="D05")


def test_unchanged_buffer_aborts(g, store, fake_emacs):
    fake_emacs(lambda t: t)
    with pytest.raises(EditorAbort, match="not changed"):
        editor.compose(g, "close", vertex="D05")


def test_erased_buffer_aborts(g, store, fake_emacs):
    """What `C-c C-k` produces."""
    fake_emacs(lambda t: "")
    with pytest.raises(EditorAbort, match="empty buffer"):
        editor.compose(g, "close", vertex="D05")


def test_nonzero_editor_exit_aborts(g, store, monkeypatch):
    monkeypatch.setattr(editor, "launch", lambda p: 1)
    with pytest.raises(EditorAbort, match="status 1"):
        editor.compose(g, "close", vertex="D05")


def test_missing_source_names_the_field(g, store, fake_emacs):
    fake_emacs(lambda t: fill(t, answer="Settled.", falsifier="f"))
    with pytest.raises(EditorError, match="Source is empty"):
        editor.compose(g, "close", vertex="D05")


def test_falsifier_required_when_the_decision_opens_something(g, store, fake_emacs):
    fake_emacs(lambda t: fill(t, answer="Settled.", source="discussion"))
    with pytest.raises(EditorError, match="Falsifier is empty"):
        editor.compose(g, "close", vertex="D05")


def test_falsifier_optional_for_a_terminal_decision(g, store, fake_emacs):
    """Mirrors `decided_complete`, which exempts a decision that opens nothing."""
    fake_emacs(lambda t: fill(t.replace("- [X] D06", "- [ ] D06"),
                              answer="Settled.", source="discussion"))
    monkey = Graph.load()
    monkey.active_edge("D05").to = []       # nothing linked, so nothing to open
    monkey.save()
    g2 = Graph.load()
    ops = editor.compose(g2, "close", vertex="D05")
    assert ops[0]["to"] == [] and ops[0]["falsifier"] is None


def test_misspelled_field_is_rejected_not_dropped(g, store, fake_emacs):
    fake_emacs(lambda t: complete(t).replace("** Summary", "** Anwser"))
    with pytest.raises(EditorError, match=r"unknown field\(s\).*Anwser"):
        editor.compose(g, "close", vertex="D05")


def test_duplicate_field_is_rejected(g, store, fake_emacs):
    fake_emacs(lambda t: complete(t).replace("** Summary", "** Answer"))
    with pytest.raises(EditorError, match="duplicate field"):
        editor.compose(g, "close", vertex="D05")


def test_unknown_checked_target_is_rejected(g, store, fake_emacs):
    fake_emacs(lambda t: complete(t) + "\n")
    with pytest.raises(EditorError, match="unknown target"):
        editor.parse(fill(editor.render_close(g, "D05"),
                          answer="a", source="s", falsifier="f")
                     .replace("- [ ] D01", "- [X] D99"), g=g)


def test_buffer_without_metadata_is_rejected(g, store):
    with pytest.raises(EditorError, match="DGRAPH_OP"):
        editor.parse("* Input\n** Answer\nhello\n", g=g)


def test_buffer_without_an_input_section_is_rejected(g, store):
    with pytest.raises(EditorError, match="not a dg template"):
        editor.parse(":PROPERTIES:\n:DGRAPH_OP: close\n:END:\n* Context\n", g=g)


def test_retargeting_is_refused(g, store, fake_emacs):
    fake_emacs(lambda t: complete(t).replace(":DGRAPH_VERTEX: D05",
                                             ":DGRAPH_VERTEX: D02"))
    with pytest.raises(EditorError, match="cannot be retargeted"):
        editor.compose(g, "close", vertex="D05")


def test_add_rejects_an_unknown_area(g, store, fake_emacs):
    fake_emacs(lambda t: fill(t, title="X", area="Nonexistent"))
    with pytest.raises(EditorError, match="unknown area"):
        editor.compose(g, "add_vertex")


def test_add_rejects_an_existing_id(g, store, fake_emacs):
    fake_emacs(lambda t: fill(t.replace("** Id\n# Like D07. Next unused: D07\nD07",
                                        "** Id\nD01"),
                              title="X", area="Alpha"))
    with pytest.raises(EditorError, match="D01 already exists"):
        editor.compose(g, "add_vertex")


def test_editing_a_derived_op_is_refused(g, store):
    with pytest.raises(EditorError, match="derived ops are not edited"):
        editor.render_op(g, 1, {"op": "set_status", "vertex": "D06",
                                "status": "OPEN"})


# ---- editor resolution ---------------------------------------------------


def test_resolution_order(monkeypatch):
    for var in ("DG_EDITOR", "VISUAL", "EDITOR", "DG_EDIT_CMD"):
        monkeypatch.delenv(var, raising=False)
    assert editor.resolve_editor() == "emacs"
    monkeypatch.setenv("EDITOR", "nano")
    assert editor.resolve_editor() == "nano"
    monkeypatch.setenv("VISUAL", "vim")
    assert editor.resolve_editor() == "vim"
    monkeypatch.setenv("DG_EDITOR", "emacs -nw")
    assert editor.resolve_editor() == "emacs -nw"


def test_the_gui_editor_ignores_EDITOR_and_VISUAL(monkeypatch):
    """`$EDITOR` names a terminal editor by convention, and the web path has no
    terminal to give it — honouring it there would hang the browser's request."""
    for var in ("DG_GUI_EDITOR", "VISUAL", "EDITOR"):
        monkeypatch.delenv(var, raising=False)
    assert editor.resolve_gui_editor() == "emacs"
    monkeypatch.setenv("EDITOR", "vim")
    monkeypatch.setenv("VISUAL", "nano")
    assert editor.resolve_gui_editor() == "emacs"
    monkeypatch.setenv("DG_GUI_EDITOR", "gvim -f")
    assert editor.resolve_gui_editor() == "gvim -f"


def test_the_gui_launcher_refuses_without_a_display(tmp_path, monkeypatch):
    """Refused up front, because the alternative is a request that hangs with no
    window to type in and no way to explain itself."""
    monkeypatch.delenv("DG_EDIT_CMD", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert editor.gui_available() is False
    with pytest.raises(EditorError, match="windowed editor"):
        editor.launch_gui(tmp_path / "b.org")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert editor.gui_available() is True


def test_launch_takes_an_explicit_editor(tmp_path, monkeypatch):
    """The seam `launch_gui` uses: resolution and running are separable."""
    monkeypatch.delenv("DG_EDIT_CMD", raising=False)
    monkeypatch.setenv("DG_EDITOR", "should-not-be-used")
    seen = {}
    monkeypatch.setattr(editor.subprocess, "call",
                        lambda argv, **kw: seen.setdefault("argv", argv) and 0 or 0)
    editor.launch(tmp_path / "b.org", editor="nano")
    assert seen["argv"][0] == "nano"


def test_elisp_is_passed_only_to_emacs(monkeypatch, tmp_path):
    monkeypatch.delenv("DG_EDIT_CMD", raising=False)
    f = tmp_path / "b.org"
    assert editor.command("emacs", f) == ["emacs", "-l", str(editor.ELISP), str(f)]
    assert editor.command("vim", f) == ["vim", str(f)]
    assert editor.command("nano", f) == ["nano", str(f)]
    assert "-l" in editor.command("/usr/bin/emacs-30.2", f)


def test_edit_cmd_override_substitutes_the_file(monkeypatch, tmp_path):
    f = tmp_path / "b.org"
    monkeypatch.setenv("DG_EDIT_CMD", "myed --file {file} --wait")
    assert editor.command("emacs", f) == ["myed", "--file", str(f), "--wait"]
    monkeypatch.setenv("DG_EDIT_CMD", "myed")
    assert editor.command("emacs", f) == ["myed", str(f)]


def test_a_missing_editor_is_reported_not_crashed(g, store, monkeypatch):
    monkeypatch.delenv("DG_EDIT_CMD", raising=False)
    monkeypatch.setenv("DG_EDITOR", "definitely-not-an-editor-9f3a")
    with pytest.raises(EditorError, match="not found"):
        editor.compose(g, "close", vertex="D05")


def test_the_real_launcher_runs_a_subprocess(g, store, monkeypatch):
    """Covers `launch` itself — no emacs needed, but a real child process."""
    monkeypatch.setenv(
        "DG_EDIT_CMD",
        'python -c "import sys,pathlib;p=pathlib.Path(sys.argv[1]);'
        "p.write_text(p.read_text().replace('** Source\\n', "
        "'** Source\\ndiscussion\\n',1).replace('** Answer\\n', "
        "'** Answer\\nFrom a real subprocess.\\n',1).replace('** Falsifier\\n', "
        "'** Falsifier\\nf\\n',1))\" {file}",
    )
    ops = editor.compose(g, "close", vertex="D05")
    assert ops[0]["answer"] == "From a real subprocess."


def test_the_buffer_lands_in_the_project(g, store, fake_emacs):
    fake_emacs(complete)
    editor.compose(g, "close", vertex="D05")
    assert (store / ".dgraph-edit.org").exists()
    assert project.find().edit == store / ".dgraph-edit.org"


def test_elisp_ships_with_the_package():
    assert editor.ELISP.exists(), "dgraph/elisp/dgraph.el is missing"


# ---- the elisp itself ----------------------------------------------------


@pytest.mark.skipif(shutil.which("emacs") is None, reason="emacs not installed")
def test_elisp_byte_compiles_without_warnings():
    r = subprocess.run(
        ["emacs", "-Q", "--batch", "-f", "batch-byte-compile", str(editor.ELISP)],
        capture_output=True, text=True,
    )
    try:
        assert r.returncode == 0, r.stderr
        assert "Warning" not in r.stderr, r.stderr
    finally:
        editor.ELISP.with_suffix(".elc").unlink(missing_ok=True)


@pytest.mark.skipif(shutil.which("emacs") is None, reason="emacs not installed")
def test_elisp_loads_and_registers_the_dg_link_type():
    """Byte-compiling catches syntax; this catches the file not wiring itself up."""
    r = subprocess.run(
        ["emacs", "-Q", "--batch", "-l", str(editor.ELISP), "-f", "dgraph--selftest"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "selftest ok" in r.stdout


def _emacs_batch(store, body: str):
    """Run BODY in batch emacs against a real buffer in STORE."""
    return subprocess.run(
        ["emacs", "-Q", "--batch", "-l", str(editor.ELISP),
         "--eval", f'(progn (find-file ".dgraph-edit.org") {body})'],
        capture_output=True, text=True, cwd=store,
        env={**os.environ, "DG_PROJECT": str(store)},
    )


@pytest.fixture
def buffer_in(store, g):
    """A real editor buffer on disk, as `dg decide --edit` would write it."""
    (store / ".dgraph-edit.org").write_text(editor.render_close(g, "D05"),
                                            encoding="utf-8")
    return store


@pytest.mark.skipif(shutil.which("emacs") is None, reason="emacs not installed")
def test_the_mode_activates_and_finds_its_target(buffer_in):
    """Behaviour, not just syntax: `-l` runs before the file is visited, so the
    auto-mode hook has to be registered in time."""
    r = _emacs_batch(buffer_in, '(princ (format "%s %s" dgraph-edit-mode (dgraph--target)))')
    assert r.returncode == 0, r.stderr
    assert "t D05" in r.stdout


@pytest.mark.skipif(shutil.which("emacs") is None, reason="emacs not installed")
def test_the_context_subtree_is_actually_protected(buffer_in):
    """A bare `read-only` overlay would silently protect nothing; this is the
    test that would catch a regression to one."""
    r = _emacs_batch(buffer_in, '''
      (goto-char (point-max))
      (princ (format "ctx=%s " (condition-case e (progn (insert "X") "UNPROTECTED")
                                 (error (car e)))))
      (goto-char (point-min))
      (re-search-forward "^\\\\*\\\\* Answer$")
      (forward-line 2)
      (princ (format "field=%s" (condition-case e (progn (insert "typed") "editable")
                                  (error (car e)))))''')
    assert r.returncode == 0, r.stderr
    assert "ctx=text-read-only" in r.stdout
    assert "field=editable" in r.stdout


@pytest.mark.skipif(shutil.which("emacs") is None, reason="emacs not installed")
def test_preflight_names_the_fields_still_empty(buffer_in):
    """`C-c C-c` refuses early rather than letting the CLI fail after the buffer
    is gone. `editor.parse` checks the same things for everyone else."""
    r = _emacs_batch(buffer_in, '(princ (dgraph--check))')
    assert r.returncode == 0, r.stderr
    assert "Answer, Source still empty" in r.stdout


@pytest.mark.skipif(shutil.which("emacs") is None, reason="emacs not installed")
def test_a_dg_link_resolves_through_dg_export(buffer_in):
    """End to end: org follows the link, elisp shells out to `dg export`, and the
    premise's own answer comes back."""
    if shutil.which("dg") is None:
        pytest.skip("dg not on PATH")
    r = _emacs_batch(buffer_in, '''
      (goto-char (point-min))
      (re-search-forward "\\\\[\\\\[dg:D04\\\\]")
      (goto-char (match-beginning 0))
      (org-open-at-point)
      (with-current-buffer "*dgraph: D04*" (princ (buffer-string)))''')
    assert r.returncode == 0, r.stderr
    assert "D04 — Downstream" in r.stdout
    assert "Third answer." in r.stdout          # the premise's answer, fetched live


@pytest.mark.skipif(shutil.which("emacs") is None, reason="emacs not installed")
def test_the_write_ban_refuses_at_runtime(buffer_in):
    """The grep test in test_cli.py pins the structure; this pins the behaviour."""
    r = _emacs_batch(buffer_in, '''
      (princ (condition-case e (dgraph--dg "apply") (error (cadr e))))''')
    assert r.returncode == 0, r.stderr
    assert "refuses to run" in r.stdout and "read-only" in r.stdout
