"""Terminal output. Rich reads `[...]` as markup, so anything from the store or
from a Violation must be escaped before it is printed — otherwise the most
useful token on the line is the one that silently disappears."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dgraph import cli, editor, pending
from dgraph.check import run as check_run
from dgraph.cli import app
from dgraph.model import Graph
from dgraph.render import write
from tests.conftest import bare

runner = CliRunner()


@pytest.fixture
def run(store, monkeypatch):
    """Invoke `dg` against the fixture project, wide enough not to wrap."""
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERM", "dumb")

    def go(*args, input=None):
        return runner.invoke(app, ["--project", str(store), *args], input=input)

    return go


def test_check_names_the_rule_that_broke(run, store, g):
    """The reported bug: `[no_orphans]` was parsed as a style tag and dropped,
    leaving `!  (warning) D01 is connected to nothing` with no rule name."""
    write(g)
    bad = Graph.load(store / "decisions.json")
    bad.vertices["D99"] = replace(bad.vertices["D01"], id="D99", title="Floating")
    bad.save(store / "decisions.json")
    write(bad, store / "decision-graph.md")

    out = run("check").output
    assert "[no_orphans]" in out
    assert "D99 is connected to nothing" in out


def test_check_names_the_rule_on_a_blocking_error(run, store, g):
    write(g)
    raw = json.loads((store / "decisions.json").read_text())
    raw["vertices"].append(
        {"id": "D07", "title": "Bad", "area": "Alpha", "status": "WOBBLY"}
    )
    (store / "decisions.json").write_text(json.dumps(raw))

    res = run("check")
    assert res.exit_code == 1
    assert "[status_legal]" in res.output
    assert "[stale_view]" in res.output


def test_apply_names_the_rule_when_it_aborts(run, store, g):
    """`dg apply` interpolates the same Violation strings via ApplyError.

    Staged unexpanded, so the batch is the stale-block failure that `expand`
    now prevents — the message still has to name the rule.
    """
    write(g)
    pending.save([{"op": "close", "vertex": "D05", "answer": "yes",
                   "source": "discussion", "falsifier": "f", "to": [],
                   "date": "2026-02-01"}], store / ".dgraph-pending.json")
    res = run("apply")
    assert res.exit_code == 1
    assert "[stale_block]" in res.output
    assert "nothing written" in res.output


def test_decide_reports_what_it_released(run, store, g):
    """The CLI half of the propagation fix: `dg decide D05` stages D06 too."""
    write(g)
    # blank answer to the "opens which decisions?" prompt: D06 is already linked
    res = run("decide", "D05", "-a", "yes", "-s", "discussion", "-f", "f",
              input="\n")
    assert res.exit_code == 0
    assert "released to OPEN: D06" in res.output
    assert run("apply").exit_code == 0
    assert Graph.load(store / "decisions.json").vertices["D06"].status == "OPEN"


def test_bracketed_store_text_survives_the_terminal(run, store, g):
    """Answers routinely carry citations and tags; none of it is markup."""
    write(g)
    graph = Graph.load(store / "decisions.json")
    graph.active_edge("D01").answer = "Chose [option A] per [1], not [dim]B[/]."
    graph.vertices["D01"] = replace(graph.vertices["D01"], title="Title [draft]")
    graph.save(store / "decisions.json")

    out = run("node", "D01").output
    assert "[option A]" in out
    assert "[1]" in out
    assert "[dim]B[/]" in out
    assert "[draft]" in out


def test_node_prints_a_superseded_edge_in_full(run, store, g):
    """A reversal is an edge with a payload, and `dg node` says it is
    "everything known about one decision".

    Until this, it printed three of a superseded edge's fields and left the
    rest reachable only through `dg export`: D01's earlier answer opened D02
    alone, where the answer that replaced it opens D02 and D03, and no CLI
    surface could tell you that.
    """
    write(g)
    out = run("node", "D01").output
    assert "Superseded" in out
    assert "older answer" in out          # the label the reopen clipped
    assert "An older answer." in out      # ...and the prose it stands for
    assert "opened" in out and "D02" in out
    assert "it was measured wrong" in out


def test_node_omits_a_field_the_superseded_edge_never_had(run, store, g):
    """D01's reversal carries no falsifier and no source. An em-dash beside
    those labels would read as *decided on no evidence*, which is a claim
    about the record; the gap is that nobody wrote one down."""
    write(g)
    out = run("node", "D01").output
    sup = out[out.index("Superseded"):]
    assert "falsifier" not in sup
    assert "source" not in sup


def test_node_active_says_what_it_left_out(run, store, g):
    """`--active` narrows the panel to the answer that stands. Silently would
    be worse than not offering it: a reader who did not type the flag sees the
    same panel and concludes the decision was never reversed."""
    write(g)
    out = run("node", "D01", "--active").output
    assert "An older answer." not in out
    assert "1 superseded edge not shown" in out
    assert "--active" in out


# ---- the editor workflow -------------------------------------------------

ELISP = Path(__file__).resolve().parents[1] / "dgraph" / "elisp" / "dgraph.el"


@pytest.fixture
def stub_editor(monkeypatch):
    """Replace the launcher for CLI-level tests. `edit` is str -> str."""
    def install(edit):
        def launch(path):
            path.write_text(edit(path.read_text(encoding="utf-8")), encoding="utf-8")
            return 0
        monkeypatch.setattr(editor, "launch", launch)
    return install


def _fill(text, **fields):
    for name, body in fields.items():
        head = f"** {name.capitalize()}\n"
        text = text.replace(head, head + body + "\n", 1)
    return text


def test_decide_edit_stages_the_close_and_the_release(run, store, g, stub_editor):
    """The editor path goes through `expand` too, via the shared `_stage_close`."""
    write(g)
    stub_editor(lambda t: _fill(t, answer="Settle on 32k.", source="discussion",
                                falsifier="the corpus changes"))
    res = run("decide", "D05", "--edit")
    assert res.exit_code == 0
    assert "released to OPEN: D06" in res.output
    ops = pending.load(store / ".dgraph-pending.json")
    assert [o["op"] for o in ops] == ["close", "set_status"]
    assert ops[0]["answer"] == "Settle on 32k."


def test_decide_edit_seeds_the_template_from_flags(run, store, g, stub_editor):
    write(g)
    seen = {}
    stub_editor(lambda t: (seen.update(text=t),
                           _fill(t, source="discussion", falsifier="f"))[1])
    run("decide", "D05", "--edit", "-a", "Prefilled answer.")
    assert "Prefilled answer." in seen["text"]


def test_decide_edit_aborts_on_an_erased_buffer(run, store, g, stub_editor):
    """What `C-c C-k` produces."""
    write(g)
    stub_editor(lambda t: "")
    res = run("decide", "D05", "--edit")
    assert res.exit_code == 1
    assert "aborted" in res.output
    assert pending.load(store / ".dgraph-pending.json") == []


def test_decide_edit_reports_a_half_filled_buffer(run, store, g, stub_editor):
    write(g)
    stub_editor(lambda t: _fill(t, answer="Only an answer."))
    res = run("decide", "D05", "--edit")
    assert res.exit_code == 1
    assert "Source is empty" in res.output
    assert pending.load(store / ".dgraph-pending.json") == []


def test_dg_edit_env_makes_the_editor_the_default(run, store, g, stub_editor,
                                                  monkeypatch, tty):
    write(g)
    stub_editor(lambda t: _fill(t, answer="a", source="s", falsifier="f"))
    monkeypatch.setenv("DG_EDIT", "1")
    assert run("decide", "D05").exit_code == 0
    assert pending.load(store / ".dgraph-pending.json")[0]["answer"] == "a"


def test_no_edit_beats_the_env_var(run, store, g, monkeypatch):
    write(g)
    monkeypatch.setenv("DG_EDIT", "1")
    res = run("decide", "D05", "--no-edit", "-a", "x", "-s", "y", "-f", "z",
              input="\n")
    assert res.exit_code == 0
    assert "buffer:" not in res.output          # never opened an editor


def test_reopen_edit_still_shows_the_propagation_panel(run, store, g, stub_editor,
                                                       tty):
    """The panel is the point of `reopen`; composing in an editor must not skip it."""
    write(g)
    stub_editor(lambda t: _fill(t, why="the sweep was mis-seeded"))
    res = run("reopen", "D01", "--edit", input="y\n")
    assert res.exit_code == 0
    assert "PROVISIONAL" in res.output and "D02, D03, D04" in res.output


def test_add_edit_stages_vertex_and_edge(run, store, g, stub_editor):
    write(g)
    stub_editor(lambda t: _fill(t, title="Which scheduler", area="Beta",
                                after="D04"))
    res = run("add", "--edit")
    assert res.exit_code == 0
    ops = pending.load(store / ".dgraph-pending.json")
    assert [o["op"] for o in ops] == ["add_vertex", "add_edge"]


def test_add_without_edit_still_requires_its_flags(run, store, g):
    """`--id/--title/--area` went from typer-required to optional so `--edit` can
    supply them; the failure has to stay just as loud."""
    write(g)
    res = run("add", "--no-edit", "-t", "No id given", "--area", "Beta")
    assert res.exit_code == 2
    assert "--id" in res.output


def test_deciding_a_blocked_vertex_warns_about_the_block(run, store, g):
    """Audit C2. Closing a BLOCKED vertex silently discarded the block. It is
    still allowed — sometimes the recorded blocker turns out irrelevant, and
    there is deliberately no other exit — but now it is said out loud."""
    write(g)
    res = run("decide", "D06", "-a", "a", "-s", "s", "-f", "f")
    assert res.exit_code == 0
    assert "BLOCKED:D05" in res.output and "did not matter" in res.output


# ---- import-md refuses to plant a contradiction (audit C8) ----------------

BAD_DOC = """# Decision graph
## Area

### D01 — A question
- **Status:** WOBBLY
- **Depends on:** —
- **Falsifier:** —
"""


def test_import_md_refuses_a_result_that_breaks_invariants(tmp_path, monkeypatch):
    """A bootstrap that writes a store `dg apply` would refuse plants the
    contradiction this tool exists to prevent, on day one."""
    monkeypatch.setenv("COLUMNS", "200")
    doc = tmp_path / "doc.md"
    doc.write_text(BAD_DOC, encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    res = runner.invoke(app, ["--project", str(proj), "import-md", str(doc)])
    assert res.exit_code == 1
    assert "status_legal" in res.output
    assert not (proj / "decisions.json").exists()

    forced = runner.invoke(app, ["--project", str(proj), "import-md",
                                 str(doc), "--force"])
    assert forced.exit_code == 0
    assert (proj / "decisions.json").exists()


def test_import_md_names_the_section_missing_its_status(tmp_path, monkeypatch):
    """A hand-written document; the error names the line to fix, not a bare
    AttributeError from the parser's insides."""
    monkeypatch.setenv("COLUMNS", "200")
    doc = tmp_path / "doc.md"
    doc.write_text("## Area\n\n### D01 — A question\n- **Depends on:** —\n",
                   encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    res = runner.invoke(app, ["--project", str(proj), "import-md", str(doc)])
    assert res.exit_code == 1
    assert "D01" in res.output and "Status" in res.output
    assert not (proj / "decisions.json").exists()


# ---- apply's write order (audit B1) --------------------------------------


def test_apply_renders_before_writing_anything(run, store, g, monkeypatch):
    """The view text is computed first, so a rendering bug aborts with the
    store untouched and the staged ops intact — not with a half-applied
    project."""
    import dgraph.render
    write(g)
    run("decide", "D05", "-a", "yes", "-s", "s", "-f", "f")
    staged_before = pending.load(store / ".dgraph-pending.json")
    store_before = (store / "decisions.json").read_text(encoding="utf-8")

    def boom(g):
        raise RuntimeError("render bug")

    monkeypatch.setattr(dgraph.render, "render", boom)
    res = run("apply")
    assert res.exit_code == 1
    assert (store / "decisions.json").read_text(encoding="utf-8") == store_before
    assert pending.load(store / ".dgraph-pending.json") == staged_before


def test_apply_recovers_when_the_view_cannot_be_written(run, store, g):
    """Store first, pending cleared, view last. The applied ops must not stay
    staged — they are in the store now, and re-applying them is the dead end
    A3 removed — and the failure names the recovery: `dg render`."""
    write(g)
    run("decide", "D05", "-a", "yes", "-s", "s", "-f", "f")
    (store / "decision-graph.md").unlink()
    (store / "decision-graph.md").mkdir()          # write_text now fails
    res = run("apply")
    assert res.exit_code == 1
    assert "dg render" in res.output
    assert Graph.load(store / "decisions.json").vertices["D05"].status == "DECIDED"
    assert pending.load(store / ".dgraph-pending.json") == []


# ---- staging judges the store PLUS the staged ops (audit A3) -------------


def test_add_can_chain_onto_a_staged_vertex(run, store, g):
    """Audit A3(1): `--after` a vertex whose add is staged but not applied used
    to fail with "unknown parent(s)" — a chain needed an apply between adds."""
    write(g)
    assert run("add", "--id", "D07", "-t", "First of a chain",
               "--area", "Alpha").exit_code == 0
    res = run("add", "--id", "D08", "-t", "Rests on the first",
              "--area", "Alpha", "--after", "D07")
    assert res.exit_code == 0
    assert run("apply").exit_code == 0
    assert Graph.load(store / "decisions.json").depends("D08") == ["D07"]


def test_a_second_decide_is_refused_with_a_staging_remedy(run, store, g):
    """Audit A3(2): both closes used to stage, then `apply` refused the whole
    batch quoting "reopen it first" — the wrong remedy for a staged duplicate."""
    write(g)
    assert run("decide", "D05", "-a", "one", "-s", "s", "-f", "f").exit_code == 0
    res = run("decide", "D05", "-a", "two", "-s", "s", "-f", "f")
    assert res.exit_code == 1
    assert "already staged" in res.output
    assert "dg edit" in res.output and "reopen" not in res.output
    assert run("apply").exit_code == 0            # the batch stayed appliable


def test_a_staged_add_is_a_taken_id(run, store, g):
    """The flag path never checked the id at all; apply refused it later."""
    write(g)
    run("add", "--id", "D07", "-t", "First", "--area", "Alpha")
    res = run("add", "--id", "D07", "-t", "Again", "--area", "Alpha")
    assert res.exit_code == 1
    assert "staging area" in res.output


def test_reopen_then_redecide_without_an_apply_between(run, store, g):
    """Audit A3(3): the documented reopen -> re-decide workflow in one batch.
    `decide` used to read the store alone and answer "reopen it first" — the
    reopen it demanded was already staged."""
    write(g)
    assert run("reopen", "D01", "-w", "it was mismeasured", "--yes").exit_code == 0
    res = run("decide", "D01", "-a", "the corrected answer", "-s", "discussion",
              "-f", "newer evidence")
    assert res.exit_code == 0
    assert run("apply").exit_code == 0
    gg = Graph.load(store / "decisions.json")
    assert gg.vertices["D01"].status == "DECIDED"
    assert gg.active_edge("D01").answer == "the corrected answer"
    assert len(gg.history("D01")) == 2            # the reversal is on file


def test_reopen_marks_a_descendant_whose_close_is_only_staged(run, store, g):
    """Audit A3(4): expand used to derive propagation from the store, so a
    descendant decided in the staging area was missed and `apply` refused the
    batch wholesale, telling the user to stage an op no command stages."""
    write(g)
    run("decide", "D05", "-a", "yes", "-s", "s", "-f", "f")
    res = run("reopen", "D01", "-w", "shaken", "--yes")
    assert res.exit_code == 0
    marked = {o["vertex"] for o in pending.load(store / ".dgraph-pending.json")
              if o["op"] == "set_status" and o["status"] == "PROVISIONAL"}
    assert "D05" in marked
    assert run("apply").exit_code == 0


def test_staging_warns_when_the_batch_would_not_apply_yet(run, store, g):
    """Deciding a child before its premise is transitional, not refused — but
    the user hears at stage time which rule the batch currently breaks, not at
    apply time several commands later."""
    write(g)
    res = run("decide", "D06", "-a", "early", "-s", "s", "-f", "f")
    assert res.exit_code == 0                     # staged, with a warning
    assert "would currently refuse" in res.output
    assert "propagation" in res.output
    # staging the missing premise clears it
    res = run("decide", "D05", "-a", "the premise", "-s", "s", "-f", "f")
    assert "would currently refuse" not in res.output
    assert run("apply").exit_code == 0


def test_edit_replaces_a_staged_op_in_place(run, store, g, stub_editor):
    write(g)
    stub_editor(lambda t: _fill(t, answer="First.", source="s", falsifier="f"))
    run("decide", "D05", "--edit")
    before = pending.load(store / ".dgraph-pending.json")

    stub_editor(lambda t: t.replace("First.", "Revised."))
    assert run("edit", "0").exit_code == 0
    after = pending.load(store / ".dgraph-pending.json")
    assert len(after) == len(before)                 # replaced, not appended
    assert after[0]["answer"] == "Revised."
    assert after[1] == before[1]                     # derived op untouched


def test_edit_refuses_a_derived_op(run, store, g, stub_editor):
    write(g)
    stub_editor(lambda t: _fill(t, answer="a", source="s", falsifier="f"))
    run("decide", "D05", "--edit")
    res = run("edit", "1")                           # the derived set_status
    assert res.exit_code == 1
    assert "derived" in res.output
    # The remedy names the op's id, not the position it happens to sit at: the
    # message outlives another writer's apply, and the position does not.
    assert f"dg drop {pending.load()[1]['ref']}" in res.output


def test_edit_reports_an_out_of_range_index(run, store, g):
    write(g)
    res = run("edit", "7")
    assert res.exit_code == 1 and "no staged op 7" in res.output


def test_edit_refuses_to_retarget(run, store, g, stub_editor):
    write(g)
    stub_editor(lambda t: _fill(t, answer="a", source="s", falsifier="f"))
    run("decide", "D05", "--edit")
    stub_editor(lambda t: t.replace(":DGRAPH_VERTEX: D05", ":DGRAPH_VERTEX: D02"))
    res = run("edit", "0")
    assert res.exit_code == 1 and "retargeted" in res.output


# ---- export --------------------------------------------------------------


def test_export_is_parseable_json(run, store, g, monkeypatch):
    """Regression guard: `con.print` would soft-wrap at $COLUMNS and corrupt it."""
    from dgraph.server import graph_payload
    write(g)
    monkeypatch.setenv("COLUMNS", "40")
    out = run("export").output
    assert json.loads(out) == graph_payload(g)


def test_export_scoped_to_one_node(run, store, g):
    write(g)
    d = json.loads(run("export", "D05").output)
    assert [v["id"] for v in d["vertices"]] == ["D05"]
    assert d["ancestors"] == ["D01", "D02", "D04"]
    assert "D05" in d["derived"]


def test_export_rejects_an_unknown_node(run, store, g):
    write(g)
    assert run("export", "D99").exit_code == 1


# ---- the elisp write ban -------------------------------------------------


def test_elisp_only_ever_runs_read_only_subcommands():
    """Makes "elisp never mutates the graph" a test rather than a promise.

    Every subprocess call must funnel through `dgraph--dg`, which checks its
    subcommand against `dgraph-readonly-commands`.
    """
    src = ELISP.read_text(encoding="utf-8")
    assert '(defconst dgraph-readonly-commands \'("export")' in src
    body = src.split("(defun dgraph--dg", 1)[1]
    rest = src.replace(body, "")
    for spawn in ("call-process", "start-process", "shell-command",
                  "process-file", "async-shell-command"):
        assert spawn not in rest, (
            f"{spawn} is used outside dgraph--dg — the read-only guard is bypassed"
        )


def test_elisp_does_not_use_a_bare_readonly_overlay():
    """`overlay-put ... 'read-only t` is silently ignored by Emacs; a guard built
    on it would look right and protect nothing."""
    src = ELISP.read_text(encoding="utf-8")
    assert "'read-only t)" not in src.replace("(put-text-property start (point-max) 'read-only t)", "")


def test_packaging_ships_the_elisp():
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert "elisp/*.el" in pyproject


# ---- driving `dg` with no terminal ---------------------------------------
#
# Everything below exists because the caller is an agent: no tty, no way to
# answer a prompt, and no way to see an editor. A command that hangs or fails
# with `Aborted.` is unusable from there, and the failure looks identical to a
# bug in the tool.


def test_dg_edit_env_is_ignored_without_a_tty(run, store, g, monkeypatch):
    """`$DG_EDIT=1` in the user's shell must not launch an editor for an agent.

    The editor blocks with nothing to draw on and nobody to type; the call would
    hang until it timed out. `--edit` still works, so the human keeps their
    setting — it just stops leaking into a pipe.
    """
    write(g)
    monkeypatch.setenv("DG_EDIT", "1")
    monkeypatch.setattr(editor, "launch", lambda *a, **k: pytest.fail(
        "an editor was launched with no terminal"))
    res = run("decide", "D05", "-a", "a", "-s", "s", "-f", "f")
    assert res.exit_code == 0
    assert pending.load(store / ".dgraph-pending.json")[0]["answer"] == "a"


def test_decide_without_a_tty_names_the_missing_flags(run, store, g):
    write(g)
    res = run("decide", "D05", "-a", "an answer")
    assert res.exit_code == 2
    assert "--source/-s" in res.output
    assert not pending.load(store / ".dgraph-pending.json")


def test_an_optional_prompt_falls_back_to_its_default(run, store, g):
    """`--opens` is genuinely optional, so its absence is not an error."""
    write(g)
    res = run("decide", "D05", "-a", "a", "-s", "s", "-f", "f")
    assert res.exit_code == 0
    op = pending.load(store / ".dgraph-pending.json")[0]
    assert op["to"] == []


def test_reopen_without_yes_and_without_a_tty_says_so(run, store, g):
    write(g)
    res = run("reopen", "D01", "-w", "the sweep was mis-seeded")
    assert res.exit_code == 2
    assert "--yes" in res.output
    assert not pending.load(store / ".dgraph-pending.json")


def test_reopen_without_yes_still_reports_the_propagation(run, store, g):
    """Refusing to stage is not a reason to withhold the one computation that
    is hard to do by hand."""
    write(g)
    out = run("reopen", "D01", "-w", "x").output
    assert "PROVISIONAL" in out and "D02, D03, D04" in out


def test_reopen_yes_stages_without_confirming(run, store, g):
    write(g)
    res = run("reopen", "D01", "--yes", "-w", "the sweep was mis-seeded")
    assert res.exit_code == 0
    ops = pending.load(store / ".dgraph-pending.json")
    assert ops[0]["op"] == "reopen"
    assert {o["vertex"] for o in ops[1:]} == {"D02", "D03", "D04"}


def test_reopen_yes_still_prints_the_propagation(run, store, g):
    write(g)
    out = run("reopen", "D01", "--yes", "-w", "x").output
    assert "PROVISIONAL" in out and "D02, D03, D04" in out


# ---- errors arriving at the command that caused them --------------------


def test_decide_refuses_a_vertex_that_already_has_an_answer(run, store, g):
    """`apply` would refuse this too, but only after it was staged — leaving a
    staging area holding an op that can never be applied."""
    write(g)
    res = run("decide", "D01", "-a", "a", "-s", "s", "-f", "f")
    assert res.exit_code == 1
    assert "reopen" in res.output
    assert not pending.load(store / ".dgraph-pending.json")


def test_decide_refuses_a_provisional_vertex(run, store, g):
    """PROVISIONAL keeps the answer it was given, so this is the same case —
    and the one an agent hits after a reopen."""
    write(g)
    bad = Graph.load(store / "decisions.json")
    bad.vertices["D02"] = replace(bad.vertices["D02"], status="PROVISIONAL")
    bad.save(store / "decisions.json")
    res = run("decide", "D02", "-a", "a", "-s", "s", "-f", "f")
    assert res.exit_code == 1
    assert "PROVISIONAL" in res.output


def test_add_rejects_an_unknown_parent(run, store, g):
    write(g)
    res = run("add", "--id", "D07", "-t", "New", "--area", "Alpha",
              "--after", "D99")
    assert res.exit_code == 1
    assert "D99" in res.output
    assert not pending.load(store / ".dgraph-pending.json")


def test_add_rejects_an_illegal_status(run, store, g):
    write(g)
    res = run("add", "--id", "D07", "-t", "New", "--area", "Alpha",
              "--status", "BLOCKED:D99")
    assert res.exit_code == 1
    assert not pending.load(store / ".dgraph-pending.json")


def test_add_note_reaches_the_store(run, store, g):
    """`note` is where "what is undecided and why" lives, and the flag path had
    no way to write it."""
    write(g)
    assert run("add", "--id", "D07", "-t", "New", "--area", "Alpha",
               "-n", "the sweep is unread").exit_code == 0
    assert run("apply").exit_code == 0
    assert Graph.load(store / "decisions.json").vertices["D07"].note == \
        "the sweep is unread"


def test_drop_reports_an_unstaged_index(run, store, g):
    """`dg edit N` already handled this; `dg drop N` tracebacked."""
    write(g)
    res = run("drop", "99")
    assert res.exit_code == 1
    assert "no staged op 99" in res.output


# ---- what an adapter needs to interrogate -------------------------------


def test_check_exits_2_without_a_store(tmp_path):
    """2, not 1. "No graph here" and "this graph is broken" are different
    answers, and an adapter keys on the difference."""
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    res = runner.invoke(app, ["--project", str(empty), "check"])
    assert res.exit_code == 2
    assert "decisions.json" in res.output


def test_version_matches_the_installed_package():
    """Without this an old `dg` is indistinguishable, to an adapter, from a
    project that has no graph."""
    import dgraph
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0
    assert dgraph.version() != "unknown", "the package is not installed"
    assert res.output.strip() == dgraph.version()


# ---- `dg confirm`: the way out of PROVISIONAL ---------------------------


def _make_provisional(store, vid="D02"):
    g = Graph.load(store / "decisions.json")
    g.vertices[vid] = replace(g.vertices[vid], status="PROVISIONAL")
    g.save(store / "decisions.json")
    write(g, store / "decision-graph.md")
    return g


def test_confirm_only_applies_to_provisional(run, store, g):
    write(g)
    res = run("confirm", "D01")
    assert res.exit_code == 1
    assert "not PROVISIONAL" in res.output


def test_confirm_refuses_while_a_premise_is_unsettled(run, store, g):
    """Until the premise is settled again, PROVISIONAL is the accurate status."""
    write(g)
    gg = Graph.load(store / "decisions.json")
    gg.vertices["D01"] = replace(gg.vertices["D01"], status="REOPENED")
    gg.vertices["D02"] = replace(gg.vertices["D02"], status="PROVISIONAL")
    gg.save(store / "decisions.json")
    res = run("confirm", "D02")
    assert res.exit_code == 1
    assert "D01" in res.output
    assert not pending.load(store / ".dgraph-pending.json")


def test_confirm_returns_it_to_decided(run, store, g):
    write(g)
    _make_provisional(store)
    assert run("confirm", "D02").exit_code == 0
    assert run("apply").exit_code == 0
    assert Graph.load(store / "decisions.json").vertices["D02"].status == "DECIDED"


def test_confirm_releases_what_was_blocked_on_it(run, store, g):
    """The release comes from `pending.expand`, so it cannot differ between the
    commands that settle a vertex."""
    write(g)
    gg = Graph.load(store / "decisions.json")
    gg.vertices["D05"] = replace(gg.vertices["D05"], status="PROVISIONAL")
    gg.save(store / "decisions.json")
    res = run("confirm", "D05")
    assert res.exit_code == 0
    assert "D06" in res.output
    ops = pending.load(store / ".dgraph-pending.json")
    assert {(o["vertex"], o["status"]) for o in ops} == {
        ("D05", "DECIDED"), ("D06", "OPEN")}


def test_reopen_then_confirm_round_trips(run, store, g):
    """The whole point: a reversal that turns out not to change the conclusion
    must not have to be recorded as a second reversal."""
    write(g)
    assert run("reopen", "D01", "--yes", "-w", "the corpus changed").exit_code == 0
    assert run("apply").exit_code == 0
    assert Graph.load(store / "decisions.json").vertices["D02"].status == "PROVISIONAL"

    assert run("decide", "D01", "-a", "same answer", "-s", "discussion",
               "-f", "the corpus changes again", "-o", "D02,D03").exit_code == 0
    assert run("apply").exit_code == 0
    assert run("confirm", "D02").exit_code == 0
    assert run("apply").exit_code == 0

    after = Graph.load(store / "decisions.json")
    assert after.vertices["D02"].status == "DECIDED"
    assert len(after.history("D02")) == len(g.history("D02"))


@pytest.mark.parametrize("cmd,tray", [
    (["pending"], ".dgraph-pending.json"),
    (["task", "pending"], ".dgraph-task-pending.json"),
])
def test_pending_lists_an_op_kind_it_does_not_recognise(run, store, cmd, tray):
    """Audit F5. `dg pending` indexed a dict of formatters directly, so an
    unrecognised op kind was a traceback — and this is the command every
    recovery message sends the reader to:

        the staged ops no longer apply cleanly … `dg pending` to review

    A tray nobody can list is a tray nobody can triage; `dg drop N` still
    worked, but only blind, leaving `dg clear` as the practical exit. Both
    trays are covered because the task side already degraded correctly and the
    two must not drift apart again.
    """
    if tray.startswith(".dgraph-task"):
        from dgraph import task_render
        from dgraph.tasks import TaskGraph
        (store / "tasks.json").write_text(
            json.dumps({"areas": ["Alpha"], "tasks": [], "edges": []}))
        task_render.write(TaskGraph.load(store / "tasks.json"),
                          store / "tasks.md")
    (store / tray).write_text(json.dumps(
        [{"op": "frobnicate", "vertex": "D01", "note": "a hand-edit"},
         {"op": "set_status"}]))          # and one missing its required field

    res = run(*cmd)
    assert res.exit_code == 0
    assert "frobnicate" in res.output
    assert "drop" in res.output          # the row is actionable


@pytest.mark.parametrize("cmd,tray", [
    (["pending"], ".dgraph-pending.json"),
    (["task", "pending"], ".dgraph-task-pending.json"),
])
@pytest.mark.parametrize("full", [[], ["--full"]])
def test_pending_lists_a_subject_that_is_not_a_string(run, store, cmd, tray,
                                                      full):
    """The same promise, one field further in. `_op_subject` handed the tray's
    value straight through, and JSON has numbers: `{"vertex": 42}` reached rich,
    which refuses to render an `int`, and took the listing down for the whole
    tray — including the ops beside it that were perfectly readable."""
    if tray.startswith(".dgraph-task"):
        from dgraph import task_render
        from dgraph.tasks import TaskGraph
        (store / "tasks.json").write_text(
            json.dumps({"areas": ["Alpha"], "tasks": [], "edges": []}))
        task_render.write(TaskGraph.load(store / "tasks.json"),
                          store / "tasks.md")
    # Each tray's own subject key, or the op would fall through to the shared
    # `from`/`id` lookup and never reach the value this is about.
    key = "task" if tray.startswith(".dgraph-task") else "vertex"
    (store / tray).write_text(json.dumps(
        [{"op": "add_vertex", key: 42, "title": "typed by hand"},
         {"op": "frobnicate", key: "D01"}]))

    res = run(*cmd, *full)
    assert res.exit_code == 0, res.output
    assert "42" in res.output
    assert "frobnicate" in res.output    # the readable op beside it survives


# ---- audit F13: the ignore lines the tool's own docstrings assume ----------

IGNORABLE = [
    ".dgraph-pending.json", ".dgraph-task-pending.json", ".dgraph-edit.org",
    ".dgraph-pending.json.lock", ".dgraph-task-pending.json.lock",
    ".dgraph-edit.org.lock", "decisions.json.lock", "tasks.json.lock",
    ".decisions.json.ab12cd.dg-tmp", ".dgraph-serve.json", ".dgraph-serve.log",
]
KEEPABLE = ["decisions.json", "decision-graph.md", "tasks.json", "tasks.md",
            "uv.lock", "Cargo.lock", "README.md"]


def _ignored(root, name):
    return subprocess.run(["git", "-C", str(root), "check-ignore", "-q", name],
                          capture_output=True).returncode == 0


@pytest.fixture
def fresh_repo(tmp_path):
    """An empty git repository with no store in it yet — what `dg init` meets."""
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    return tmp_path


def _init(root, *args):
    return runner.invoke(app, ["--project", str(root), *args])


def test_init_ignores_everything_it_can_write(fresh_repo):
    """Audit F13. Three modules argue their own correctness from these lines
    existing. `project.write_atomic` says the `.dg-tmp` suffix "is what the
    `.gitignore` line matches, so [the file a `kill -9` leaves] is ignored
    instead of turning up untracked and being swept into a commit by
    `git add -A`"; the commit gate tells the user `.dgraph-task-pending.json`
    is gitignored and refuses commits on that basis. The quickstart listed two
    of these eleven paths, so in a project set up from the docs none of it was
    true.

    Asserted over every path the tool can write rather than over the block it
    writes, so a new scratch file that nothing ignores fails here.
    """
    _init(fresh_repo, "init", "--areas", "Core")
    _init(fresh_repo, "task", "init")

    assert [n for n in IGNORABLE if not _ignored(fresh_repo, n)] == []
    assert [n for n in KEEPABLE if _ignored(fresh_repo, n)] == []


def test_init_says_what_it_added_and_does_not_repeat_itself(fresh_repo):
    """`dg task init` right after `dg init` has nothing left to add, and a
    second block of the same lines would be noise in somebody's diff."""
    assert "added to .gitignore" in _init(fresh_repo, "init").output
    before = (fresh_repo / ".gitignore").read_text()
    assert "added to .gitignore" not in _init(fresh_repo, "task", "init").output
    assert (fresh_repo / ".gitignore").read_text() == before


def test_init_leaves_a_directory_that_is_not_a_repository_alone(tmp_path):
    """A `.gitignore` where there is no repository is litter, and `dg init` is
    run in plenty of directories that are not repositories yet."""
    _init(tmp_path, "init")
    assert not (tmp_path / ".gitignore").exists()


def test_init_keeps_what_is_already_in_the_gitignore(fresh_repo):
    """Appended to, never rewritten: the file belongs to the project."""
    (fresh_repo / ".gitignore").write_text("*.pyc\n.venv/\n")
    _init(fresh_repo, "init")
    text = (fresh_repo / ".gitignore").read_text()
    assert text.startswith("*.pyc\n.venv/\n")
    assert ".dgraph-*" in text


def test_add_puts_a_blocks_implied_edge_in_the_tray(run, store):
    """A block is a dependency, and `dg pending` is read by a person before it
    is applied — structure that only materialises at apply time cannot be
    reviewed. `_apply_one` adds it regardless; this keeps it visible."""
    from dgraph import pending
    res = run("add", "--id", "D07", "--title", "x", "--area", "Alpha",
              "--status", "BLOCKED:D05", "--no-edit")
    assert res.exit_code == 0
    assert {"op": "add_edge", "from": "D05", "to": ["D07"]} in bare(pending.load())


# ---- removing a dependency (TODO: the undo add_edge never had) -----------
#
# The decision store had the add half and no remove half, so a dependency
# recorded against the wrong parent could only be repaired by hand-editing
# decisions.json — the exit every other guard here works to make unnecessary.


def test_a_dependency_can_be_removed(run, store):
    from dgraph.model import Graph
    assert run("undep", "D06", "--after", "D05").exit_code == 0
    assert run("apply").exit_code == 0
    g = Graph.load(store / "decisions.json")
    assert "D06" not in (g.active_edge("D05").to if g.active_edge("D05") else [])
    assert g.depends("D06") == []


def test_removing_a_block_releases_the_status_in_the_same_batch(run, store):
    """`BLOCKED:D05` asserts the dependency being removed, so the status is
    false the moment this applies. There is no judgement in the repair, and
    `block_is_a_premise` is an error — leaving it would make `apply` refuse the
    batch with a message about an invariant the user did not break."""
    from dgraph.model import Graph
    res = run("undep", "D06", "--after", "D05")
    assert res.exit_code == 0 and "released to OPEN" in res.output
    assert run("apply").exit_code == 0
    g = Graph.load(store / "decisions.json")
    assert g.vertices["D06"].status == "OPEN"
    assert not [v for v in g.validate() if v.check == "block_is_a_premise"]


def test_removing_a_dependency_reports_what_it_makes_odd(run):
    """Orphaning is a judgement the graph cannot make, so it is reported and
    left standing rather than repaired or refused."""
    res = run("undep", "D06", "--after", "D05")
    assert "no_orphans" in res.output and "D06" in res.output


def test_a_decided_edge_refuses_removal_and_names_the_way_through(run):
    res = run("undep", "D02", "--after", "D01")
    assert res.exit_code == 1
    assert "part of that answer" in res.output and "dg reopen D01" in res.output


def test_reopen_then_undep_then_decide_drops_a_target(run, store):
    """The composition the refusal points at, and the reason a decided edge
    needs no removal path of its own: reopen strips the payload and leaves a
    bare edge, which this command may then edit. The superseded record keeps
    every target the answer ever opened."""
    from dgraph.model import Graph
    assert run("reopen", "D01", "--why", "premise moved", "-y").exit_code == 0
    assert run("apply").exit_code == 0
    assert run("undep", "D02", "--after", "D01").exit_code == 0
    assert run("apply").exit_code == 0
    g = Graph.load(store / "decisions.json")
    assert g.active_edge("D01").to == ["D03"]
    assert g.history("D01")[-1].to == ["D02", "D03"]      # the record survives


def test_removing_a_dependency_that_is_not_there_is_refused(run):
    res = run("undep", "D06", "--after", "D02")
    assert res.exit_code == 1 and "does not rest on" in res.output


# ---- removing a vertex ---------------------------------------------------
#
# Tier three: the only commands that erase a record rather than superseding or
# dropping it, and `--splice`/`--into` the only ones that assert an edge nobody
# wrote. `dg gate` answers `ask` on them (see tests/test_gate.py); this is the
# second belt.


@pytest.fixture
def archived(monkeypatch):
    """Pretend the store is committed. The git rule has its own test below."""
    from dgraph import cli
    monkeypatch.setattr(cli, "_archived", lambda store: None)


def test_removal_refuses_where_git_would_not_record_it(run, store):
    """Removal is the one act this tool keeps no record of — `dg drop` keeps
    the task and the reason, a superseded edge keeps the answer. Git is the
    archive, so this makes the archive real rather than assumed."""
    res = run("rm", "D03", "--yes")
    assert res.exit_code == 1 and "not a git repository" in res.output


def test_severing_removes_the_vertex_and_its_edges(run, store, archived):
    from dgraph.model import Graph
    assert run("rm", "D06", "--yes").exit_code == 0
    assert run("apply").exit_code == 0
    g = Graph.load(store / "decisions.json")
    assert "D06" not in g.vertices
    assert not [e for e in g.edges if "D06" in e.to or e.src == "D06"]


def test_removing_a_blocker_releases_what_it_blocked(run, store, archived):
    """No judgement is available once the blocker is gone, so the status is
    repaired rather than left for `status_legal` to refuse the batch over.

    D04 is reopened first because its decided answer opens D05, and removing a
    vertex a decided answer names is refused — see below. Reopening is the way
    through that refusal names, and it is what a person would actually do.
    """
    from dgraph.model import Graph
    assert run("reopen", "D04", "--why", "premise moved", "-y").exit_code == 0
    assert run("apply").exit_code == 0
    res = run("rm", "D05", "--yes")
    assert "releases" in res.output and "D06" in res.output
    assert run("apply").exit_code == 0
    assert Graph.load(store / "decisions.json").vertices["D06"].status == "OPEN"


def test_removing_a_vertex_a_decided_answer_opens_is_refused(run, store, archived):
    """Audit F21. `dg undep` refuses to drop a target from a decided edge, in as
    many words — "its targets are part of that answer". `dg rm` made the
    identical edit silently: D04 is DECIDED and its answer opens D05, and
    removing D05 rewrote that answer to say it never did, with no reversal filed
    and nothing in the confirmation to say so.

    The refusal comes from `pending.vet`, which `cli.rm` runs *before* the
    sanction — so nobody is asked to approve a removal that cannot happen.
    """
    res = run("rm", "D05", "--yes")
    assert res.exit_code == 1
    assert "D04 is decided" in res.output
    assert "dg reopen D04" in res.output
    assert pending.load(store / ".dgraph-pending.json") == []


def test_a_removal_never_edits_a_superseded_edge(run, store, archived):
    """The record is append-only. D01's superseded answer opened D02, and that
    stays true after D02 is deleted — "how a project changed its mind" is the
    most valuable thing the graph holds, and a removal used to quietly edit it.

    D01 is reopened first so its *active* answer no longer opens D02; the
    inactive one still does, which is the whole point.
    """
    from dgraph.model import Graph
    assert run("reopen", "D01", "--why", "premise moved", "-y").exit_code == 0
    assert run("apply").exit_code == 0
    before = [e.to for e in Graph.load(store / "decisions.json").edges
              if e.src == "D01" and not e.active]
    assert before and any("D02" in to for to in before)

    assert run("rm", "D02", "--yes").exit_code == 0
    assert run("apply").exit_code == 0
    g = Graph.load(store / "decisions.json")
    assert "D02" not in g.vertices
    after = [e.to for e in g.edges if e.src == "D01" and not e.active]
    assert after == before, "the superseded record was edited"
    # ...and the historical dangling target is not reported forever
    assert not [v for v in g.validate() if v.check == "no_dangling_refs"]


def test_splice_asserts_the_edge_it_reconnects(run, store, archived):
    """D05's premise D04 is decided, so a splice would rewrite that answer —
    which the test below pins as a refusal. Reopening D04 first is the way
    through the refusal names, and it leaves a bare edge a splice may write."""
    from dgraph.model import Graph
    assert run("reopen", "D04", "--why", "premise moved", "-y").exit_code == 0
    assert run("apply").exit_code == 0
    res = run("rm", "D05", "--splice", "--yes")
    assert res.exit_code == 0
    assert "asserts" in res.output and "D04 → D06" in res.output
    assert run("apply").exit_code == 0
    assert Graph.load(store / "decisions.json").depends("D06") == ["D04"]


def test_splice_refuses_to_write_into_an_answer(run, store, archived):
    """Attaching an answer to a question it never opened is the one claim this
    model must not manufacture. Refused before the confirmation, not after."""
    res = run("rm", "D04", "--splice", "--yes")
    assert res.exit_code == 1
    assert "never did" in res.output and "dg reopen D02" in res.output
    assert pending.load() == []


def test_merge_moves_the_edges_onto_the_target(run, store, archived):
    from dgraph.model import Graph
    assert run("reopen", "D04", "--why", "premise moved", "-y").exit_code == 0
    assert run("apply").exit_code == 0
    assert run("rm", "D05", "--into", "D04", "--yes").exit_code == 0
    assert run("apply").exit_code == 0
    g = Graph.load(store / "decisions.json")
    assert "D05" not in g.vertices and g.depends("D06") == ["D04"]


def test_a_decision_tasks_point_at_is_refused(run, store, archived, tmp_path):
    """The repair is in the other store and the two trays cannot apply as one
    batch, so this is refused up front rather than by `apply_all` complaining
    about a file the command never touched."""
    import json
    (store / "tasks.json").write_text(json.dumps({
        "areas": ["Alpha"], "edges": [],
        "tasks": [{"id": "T01", "title": "x", "area": "Alpha",
                   "status": "TODO", "because": "D05"}]}))
    res = run("rm", "D05", "--yes")
    assert res.exit_code == 1 and "T01" in res.output and "name D05" in res.output


def test_removal_refuses_without_a_human_or_a_flag(run, store, archived):
    res = run("rm", "D06")
    assert res.exit_code == 2 and "not reversible" in res.output
    assert pending.load() == []


def test_splice_and_into_together_are_refused(run, store, archived):
    res = run("rm", "D05", "--splice", "--into", "D04")
    assert res.exit_code == 2 and "different things" in res.output


# ---- recording a dependency between existing vertices --------------------
#
# `dg add --after` could only say this at creation, which left `dg import-md`
# — a whole graph of pre-existing vertices, with the edges it could not infer
# reported for a human to add — with nowhere to add them.


def test_a_dependency_can_be_added_between_existing_vertices(run, store):
    """Both already in the store, neither being created nor answered — the
    shape `dg import-md` leaves behind, and the case that had no path at all."""
    from dgraph.model import Graph
    for vid in ("D07", "D08"):
        assert run("add", "--id", vid, "--title", vid, "--area", "Alpha",
                   "--no-edit").exit_code == 0
    assert run("apply").exit_code == 0
    assert run("dep", "D08", "--after", "D07").exit_code == 0
    assert run("apply").exit_code == 0
    assert Graph.load(store / "decisions.json").depends("D08") == ["D07"]


def test_dep_is_the_remedy_block_is_a_premise_names(run, store):
    """The check shipped pointing only at `dg decide`, which works solely if
    the blocker is being answered now. Where the dependency is real and only
    the edge was forgotten, that left clearing a true status as the only move."""
    from dgraph.model import Graph
    g = Graph.load(store / "decisions.json")
    g.active_edge("D05").to.remove("D06")          # D06 is BLOCKED:D05
    g.save(store / "decisions.json")
    hit = [v for v in Graph.load(store / "decisions.json").validate()
           if v.check == "block_is_a_premise"]
    assert hit and "dg dep D06 --after D05" in str(hit[0])
    assert run("dep", "D06", "--after", "D05").exit_code == 0
    assert run("apply").exit_code == 0
    assert not [v for v in Graph.load(store / "decisions.json").validate()
                if v.check == "block_is_a_premise"]


def test_dep_onto_an_answered_premise_is_allowed(run, store):
    """Not the mirror of `undep` refusing to remove one. Recording that an
    answer *also* opened something is additive — the answer, its source and its
    falsifier all still stand. D01 is DECIDED and carries a falsifier."""
    from dgraph.model import Graph
    assert run("dep", "D05", "--after", "D01").exit_code == 0
    assert run("apply").exit_code == 0
    assert "D05" in Graph.load(store / "decisions.json").active_edge("D01").to


def test_dep_onto_a_terminal_answer_still_needs_a_falsifier(run, store):
    """D03 is decided and opens nothing, so it needed no falsifier. Giving it a
    target makes that requirement real, and `apply` is where that is judged."""
    assert run("dep", "D05", "--after", "D03").exit_code == 0
    res = run("apply")
    assert res.exit_code == 1 and "falsifier" in res.output


def test_dep_refuses_a_self_edge(run):
    res = run("dep", "D05", "--after", "D05")
    assert res.exit_code == 1 and "cannot rest on itself" in res.output


def test_dep_refuses_an_unknown_premise(run):
    res = run("dep", "D05", "--after", "D99")
    assert res.exit_code == 1 and "unknown premise" in res.output


def test_dep_stages_nothing_when_the_edge_is_already_there(run):
    assert run("dep", "D06", "--after", "D05").exit_code == 0
    assert pending.load() == []


def test_dep_says_at_once_when_it_would_close_a_cycle(run):
    """Staged, then warned — the tray is the safety net and `apply` refuses.
    What matters is that the warning arrives now, not two commands later."""
    res = run("dep", "D01", "--after", "D04")
    assert "would currently refuse" in res.output and "cycle" in res.output


# ---- audit F23: `dg repair` ------------------------------------------------


def _merge_damage(store):
    """A store as a merge can leave it: D01 reopened, its propagation absent."""
    g = Graph.load(store / "decisions.json")
    g.vertices["D01"] = replace(g.vertices["D01"], status="REOPENED")
    e = g.active_edge("D01")
    e.answer = e.falsifier = e.source = e.date = None
    g.save(store / "decisions.json")
    write(g, store / "decision-graph.md")


def test_repair_clears_a_merge_that_lost_its_propagation(run, store):
    """The state no `dg` command could truthfully leave. `dg decide D01` cleared
    it before this existed, by recording an answer nobody reached."""
    _merge_damage(store)
    assert {v.check for v in check_run() if v.blocking} == {"propagation"}

    res = run("repair")
    assert res.exit_code == 0
    assert "D02" in res.output and "PROVISIONAL" in res.output
    assert run("apply").exit_code == 0

    g = Graph.load(store / "decisions.json")
    assert g.vertices["D02"].status == "PROVISIONAL"
    assert not [v for v in check_run() if v.blocking]


def test_repair_unblocks_the_commit_gate(run, store):
    """What being wedged actually costs: `propagation` is blocking, so every
    commit in the repository is denied until it clears."""
    from dgraph import gate, project as _project
    _merge_damage(store)
    proj = _project.Project(store)
    assert gate.verdict("git commit -m x", proj)["verdict"] == "deny"
    assert run("repair").exit_code == 0
    assert run("apply").exit_code == 0
    assert gate.verdict("git commit -m x", proj)["verdict"] != "deny"


def test_repair_says_so_when_there_is_nothing_to_repair(run, store):
    """Precisely, because "nothing to repair" reads as "the graph is fine" and
    this command judges exactly one rule."""
    res = run("repair")
    assert res.exit_code == 0
    assert "nothing to repair" in res.output
    assert "dg check" in res.output


def test_the_propagation_message_names_the_command(store):
    """The message named a remedy the tool did not have for as long as the
    remedy did not exist; it must not go back to doing that."""
    _merge_damage(store)
    hits = [v for v in check_run() if v.check == "propagation"]
    assert hits and "dg repair" in str(hits[0])
    assert "dg decide D01" in str(hits[0])       # the other exit, still named


def test_a_collision_is_not_headed_as_lost_work(run, store):
    """Audit F17's last mile. The body has said "nothing of yours was lost"
    since the message was split; the headline still said "✗ aborted, nothing
    written", and the headline is what a model acts on.

    Simulated rather than raced: another writer's apply is exactly a store that
    already holds the op, which is what this sets up.
    """
    assert run("add", "--id", "D09", "-t", "New", "--area", "Alpha").exit_code == 0
    staged = pending.load(store / ".dgraph-pending.json")
    assert run("apply").exit_code == 0                    # the other writer wins
    pending.save(staged, store / ".dgraph-pending.json")  # our tray, still holding it

    res = run("apply")
    assert res.exit_code == 1                             # the batch was refused
    assert "another writer got there first" in res.output
    assert "aborted, nothing written" not in res.output
    assert "nothing" in res.output.lower() and "lost" in res.output.lower()


def test_a_real_refusal_still_reads_as_one(run, store):
    """The other side: a batch that genuinely cannot apply must keep its red ✗,
    or the distinction buys nothing."""
    pending.save([{"op": "add_vertex", "id": "D09", "title": "x",
                   "area": "nowhere"}], store / ".dgraph-pending.json")
    res = run("apply")
    assert res.exit_code == 1
    assert "aborted, nothing written" in res.output
    assert "another writer" not in res.output


def test_apply_says_what_moved_under_the_batch(run, store):
    """The case the invariants let through, and the one the whole stamp exists
    for: a batch that is still perfectly legal after somebody reopened the
    premise it attaches to. It used to land in silence.
    """
    assert run("add", "--id", "D09", "-t", "A new question", "--area", "Alpha",
               "--after", "D01").exit_code == 0
    mine = pending.load(store / ".dgraph-pending.json")

    # ...another writer reopens D01 and applies, while this batch waits
    pending.save([], store / ".dgraph-pending.json")
    assert run("reopen", "D01", "--why", "premise moved", "-y").exit_code == 0
    assert run("apply").exit_code == 0
    pending.save(mine, store / ".dgraph-pending.json")

    res = run("apply")
    assert res.exit_code == 0, res.output          # still legal, still applied
    assert "D01 moved since this batch was staged" in res.output
    assert "DECIDED" in res.output and "REOPENED" in res.output
    assert Graph.load(store / "decisions.json").vertices["D09"].status == "OPEN"


def test_apply_is_quiet_when_nothing_moved(run, store):
    """The other half, and the one that decides whether this gets read: an
    advisory on every apply is an advisory nobody sees."""
    assert run("add", "--id", "D09", "-t", "x", "--area", "Alpha",
               "--after", "D01").exit_code == 0
    res = run("apply")
    assert res.exit_code == 0
    assert "moved since" not in res.output


def test_the_dangerous_case_is_still_a_refusal_not_a_note(run, store):
    """The stamp reports; it never replaces an invariant. A decided answer on a
    reopened premise stays a blocking `propagation` finding, so that batch
    aborts rather than landing with a warning attached."""
    assert run("add", "--id", "D09", "-t", "x", "--area", "Alpha",
               "--after", "D01").exit_code == 0
    assert run("decide", "D09", "-a", "yes", "-s", "s", "-f", "f").exit_code == 0
    mine = pending.load(store / ".dgraph-pending.json")

    pending.save([], store / ".dgraph-pending.json")
    assert run("reopen", "D01", "--why", "moved", "-y").exit_code == 0
    assert run("apply").exit_code == 0
    pending.save(mine, store / ".dgraph-pending.json")

    res = run("apply")
    assert res.exit_code == 1
    assert "propagation" in res.output
    assert "D09" not in Graph.load(store / "decisions.json").vertices


# ---- unstaging says what went (audit F29) --------------------------------
#
# `dg drop N` used to print only the number it was given. The tray is shared and
# `pending.discard` removes applied ops by value from wherever they sit, so an
# index read off an earlier `dg pending` can name a different op by the time it
# is used — and `dropped op 2` reads identically either way. These pin that the
# confirmation names the op, which is what makes a wrong index visible.


def test_drop_names_the_op_it_removed(run):
    run("add", "--id", "D07", "--title", "Seventh", "--area", "Alpha")
    run("add", "--id", "D08", "--title", "Eighth", "--area", "Alpha")
    res = run("drop", "0")
    assert res.exit_code == 0
    assert "add_vertex D07" in res.output and "Seventh" in res.output
    assert [o["id"] for o in pending.load()] == ["D08"]


def test_drop_names_what_actually_went_when_the_index_has_shifted(run, store):
    """The finding itself, made visible rather than fixed.

    An agent lists the tray, sees `add_vertex D09` at index 2, and drops it —
    but another writer applied the first two ops meanwhile, so index 2 now
    addresses D11. The removal is still wrong; what changed is that the
    confirmation says so instead of echoing the number.
    """
    from dgraph import applying

    for n in (7, 8, 9, 10, 11):
        run("add", "--id", f"D{n:02d}", "--title", f"Number {n}", "--area", "Alpha")
    listed = [o["id"] for o in pending.load()]
    assert listed == ["D07", "D08", "D09", "D10", "D11"]

    applying.apply_decisions(pending.load()[:2])       # another writer, D07/D08

    res = run("drop", "2")                            # the agent means D09
    assert res.exit_code == 0
    assert "D11" in res.output, "the confirmation must name the op that went"
    assert [o["id"] for o in pending.load()] == ["D09", "D10"]


def test_the_drop_confirmation_describes_an_op_as_the_listing_does(run):
    """One description, two commands. A confirmation that worded an op
    differently from the row a person reviewed would be no confirmation."""
    run("add", "--id", "D07", "--title", "Seventh", "--area", "Alpha",
        "--after", "D05")
    rows = run("pending").output
    for i, op in enumerate(pending.load()):
        summary = run("drop", "0").output          # always the head, in order
        subject = op.get("vertex") or op.get("from") or op.get("id")
        assert subject in summary and subject in rows
        assert (op.get("op") or "?") in summary


def test_dropping_an_unrecognised_op_still_confirms(run, store):
    """Reached with whatever is in the tray. `dg drop` is where a hand-edited
    or newer-`dg` tray is unstuck, so it must not traceback on one."""
    pending.save([{"op": "from_the_future", "widget": 3}])
    res = run("drop", "0")
    assert res.exit_code == 0
    assert "from_the_future" in res.output
    assert pending.load() == []


# ---- editing an add_vertex keeps its edges (audit F26) -------------------
#
# `editor._parse_add` returns a *group*: the vertex, plus one `add_edge` per
# parent named in the buffer. `dg edit` used to write back `new[0]` and drop the
# rest, so retargeting a staged decision changed nothing and said `updated op 0`.
# The buffer compounded it by rendering `** After` empty on a vertex that was
# attached, which is the reading a person would then act on.


def _after(text: str, value: str) -> str:
    """Type into `** After`, over whatever the template pre-filled."""
    head = "** After\n# Optional. Comma-separated parents that open this.\n"
    assert head in text, "the After field is not where this test thought"
    body, _, rest = text.partition(head)[2].partition("\n** ")
    return body, text.replace(head + body, head + value + "\n", 1)


def test_the_edit_buffer_shows_the_parents_the_batch_attaches(run, store, g,
                                                              stub_editor):
    """A blank field on an attached vertex is the buffer lying about the thing
    it is editing: the obvious reading is 'no parents', and saving it meant so."""
    write(g)
    run("add", "--id", "D07", "--title", "Seventh", "--area", "Alpha",
        "--after", "D05")
    seen = {}
    stub_editor(lambda t: (seen.update(after=_after(t, "D05")[0]), t + "\n")[1])
    run("edit", "0")
    assert "D05" in seen["after"]


def test_edit_keeps_the_edges_the_revision_names(run, store, g, stub_editor):
    write(g)
    run("add", "--id", "D07", "--title", "Seventh", "--area", "Alpha",
        "--after", "D05")
    assert [(o["op"], o.get("from")) for o in pending.load()] == [
        ("add_vertex", None), ("add_edge", "D05")]

    stub_editor(lambda t: _after(t, "D02")[1])       # retarget D05 -> D02
    res = run("edit", "0")
    assert res.exit_code == 0

    ops = pending.load()
    assert [o["op"] for o in ops] == ["add_vertex", "add_edge"]
    assert ops[1]["from"] == "D02", "the edit's parent must be staged"
    assert not any(o.get("from") == "D05" for o in ops), \
        "the superseded parent must not survive alongside it"


def test_edit_can_drop_every_parent(run, store, g, stub_editor):
    """Clearing the field means 'this rests on nothing', and a union with the
    old edges would make it unsayable."""
    write(g)
    run("add", "--id", "D07", "--title", "Seventh", "--area", "Alpha",
        "--after", "D05")
    stub_editor(lambda t: _after(t, "")[1])
    assert run("edit", "0").exit_code == 0
    assert [o["op"] for o in pending.load()] == ["add_vertex"]


def test_edit_keeps_a_shared_edge_ops_other_targets(run, store, g):
    """Nothing in the tool stages a multi-target `add_edge`, but `_apply_one`
    unions targets and the web API takes an op as data. Superseding the whole op
    would lose an attachment the edit never mentioned."""
    from dgraph.cli import _supersedes

    supersede = _supersedes("add_vertex", {"op": "add_vertex", "id": "D07"})
    shared = {"op": "add_edge", "from": "D05", "to": ["D07", "D09"]}
    assert supersede(shared) == {"op": "add_edge", "from": "D05", "to": ["D09"]}
    assert supersede({"op": "add_edge", "from": "D05", "to": ["D07"]}) is None
    assert supersede({"op": "add_edge", "from": "D05", "to": ["D09"]}) == \
        {"op": "add_edge", "from": "D05", "to": ["D09"]}


def test_editing_a_close_supersedes_nothing(run, store, g, stub_editor):
    """The narrowness is the point: `close` and `reopen` each parse back to one
    op, so a revision of either is a swap and must not touch the batch."""
    from dgraph.cli import _supersedes

    assert _supersedes("close", {"op": "close", "vertex": "D05"}) is None
    write(g)
    stub_editor(lambda t: _fill(t, answer="First.", source="s", falsifier="f"))
    run("decide", "D05", "--edit")
    before = pending.load()
    stub_editor(lambda t: t.replace("First.", "Revised."))
    assert run("edit", "0").exit_code == 0
    after = pending.load()
    assert len(after) == len(before) and after[1:] == before[1:]


def test_undep_stages_its_release_in_the_same_write(run, store, g, monkeypatch):
    """Audit F31. The comment above the release already claimed 'the same
    batch'; it was two writes, safe only because the intermediate happens to
    trip `block_is_a_premise` — and that refusal lands on whichever *other*
    writer applied in the window, over an invariant they had not broken.

    Asserted on what the tray is asked to hold at each write, not on where it
    ends up: both versions end up the same, and only one of them ever puts a
    `remove_edge` in the tray without the release that makes it legal.
    """
    write(g)
    seen = []
    real = pending.save
    monkeypatch.setattr(pending, "save",
                        lambda ops, path=None: (seen.append(list(ops)),
                                                real(ops, path))[1])
    res = run("undep", "D06", "--after", "D05")
    assert res.exit_code == 0
    assert [len(s) for s in seen] == [2], seen
    assert [o["op"] for o in seen[-1]] == ["remove_edge", "set_status"]
    assert bare(seen[-1][1]) == {"op": "set_status", "vertex": "D06",
                                 "status": "OPEN"}
    monkeypatch.setattr(pending, "save", real)
    assert run("apply").exit_code == 0


# ---- addressing a staged op by id (audit F29, route 1) -------------------
#
# The confirmation line made a wrong index visible; these make it wrong far less
# often. An id is resolved against the tray the command is about to write, so it
# lands on the op it names however much the positions have moved.


def test_an_id_survives_another_writers_apply(run, store):
    """The finding, now coming out right. An agent lists the tray, notes the id
    beside `D09`, and drops it — after another writer has applied the first two
    ops out from under it."""
    from dgraph import applying

    for n in (7, 8, 9, 10, 11):
        run("add", "--id", f"D{n:02d}", "--title", f"Number {n}", "--area", "Alpha")
    listed = pending.load()
    assert [o["id"] for o in listed] == ["D07", "D08", "D09", "D10", "D11"]
    wanted = listed[2]["ref"]                     # what `dg pending` showed

    applying.apply_decisions(pending.load()[:2])  # another writer

    res = run("drop", wanted)
    assert res.exit_code == 0
    assert "D09" in res.output
    assert [o["id"] for o in pending.load()] == ["D10", "D11"]


def test_an_index_still_works_for_a_single_writer(run, store):
    run("add", "--id", "D07", "--title", "Seventh", "--area", "Alpha")
    run("add", "--id", "D08", "--title", "Eighth", "--area", "Alpha")
    res = run("drop", "0")
    assert res.exit_code == 0 and "D07" in res.output
    assert [o["id"] for o in pending.load()] == ["D08"]


def test_an_id_can_never_be_read_as_an_index(run, store):
    """The two vocabularies have to stay disjoint, or `dg drop 12` is ambiguous
    and the ambiguity is the bug again."""
    from dgraph.pending import REF_ALPHABET

    assert not set(REF_ALPHABET) & set("0123456789")
    for n in (7, 8, 9):
        run("add", "--id", f"D{n:02d}", "--title", "x", "--area", "Alpha")
    for op in pending.load():
        with pytest.raises(ValueError):
            int(op["ref"])


def test_ids_are_unique_across_a_tray(run, store):
    for n in range(7, 30):
        run("add", "--id", f"D{n:02d}", "--title", "x", "--area", "Alpha")
    refs = [o["ref"] for o in pending.load()]
    assert len(refs) == len(set(refs)) == 23


def test_an_unknown_id_is_refused_by_name(run, store):
    run("add", "--id", "D07", "--title", "x", "--area", "Alpha")
    res = run("drop", "zzzz")
    assert res.exit_code == 1
    assert "zzzz" in res.output and "dg pending" in res.output
    assert len(pending.load()) == 1, "nothing may be dropped on a miss"


def test_an_out_of_range_index_still_reads_as_one(run, store):
    res = run("drop", "7")
    assert res.exit_code == 1 and "no staged op 7" in res.output


def test_a_tray_without_ids_is_still_addressable(run, store):
    """A tray written by an older `dg` has no ids. The index is what it has, and
    it must keep working rather than becoming unreachable."""
    pending.save([{"op": "set_status", "vertex": "D05", "status": "OPEN"}])
    res = run("drop", "0")
    assert res.exit_code == 0 and "set_status D05" in res.output
    assert pending.load() == []


def test_edit_writes_back_by_id_not_by_the_index_it_read(run, store, g,
                                                         stub_editor):
    """An editor session lasts minutes. Another writer applying in that window
    moves every position past what it removed, and the revision must still land
    on the op it was composed from."""
    from dgraph import applying

    write(g)
    run("add", "--id", "D07", "--title", "Seventh", "--area", "Alpha")
    run("add", "--id", "D08", "--title", "Eighth", "--area", "Alpha")
    run("add", "--id", "D09", "--title", "Ninth", "--area", "Alpha")

    def apply_two_then_edit(text):
        applying.apply_decisions(pending.load()[:2])      # the other writer
        return text.replace("Ninth", "Revised")

    stub_editor(apply_two_then_edit)
    assert run("edit", "2").exit_code == 0
    ops = pending.load()
    assert [o["id"] for o in ops] == ["D09"]
    assert ops[0]["title"] == "Revised"


def test_a_composed_add_never_reaches_the_tray_unvetted(run, store, g,
                                                        stub_editor):
    """Audit F30, from the command rather than the parser. The parser's message
    is better; `pending.vet_all` before staging is the guarantee. Either way the
    shared tray stays empty — an op that can never apply refuses every other
    writer's batch too, until somebody drops it."""
    write(g)
    stub_editor(lambda t: _fill(t, title="Seventh", area="Alpha")
                .replace("\nOPEN\n", "\nDONE\n", 1))
    res = run("add", "--edit")
    assert res.exit_code == 1
    assert "DONE" in res.output
    assert pending.load() == []


# ---- `--help` is grouped, and the grouping is complete --------------------


def _panels(layout):
    """`{command name: panel}` from a LAYOUT declaration.

    An entry naming aliases (`"why/context"`) is one help line but several
    commands, and each of them still has to exist and declare the panel.
    """
    return {alias: panel
            for panel, names in layout for name in names
            for alias in name.split("/")}


@pytest.mark.parametrize(
    "typer_app, layout",
    [(cli.app, cli.LAYOUT), (cli.task_app, cli.TASK_LAYOUT)],
    ids=["dg", "dg task"],
)
def test_every_command_is_in_a_help_panel(typer_app, layout):
    """`_ordered` lists an unnamed command at the end rather than dropping it,
    so a forgotten one is untidy instead of invisible. This is what stops it
    being lived with."""
    declared = set(_panels(layout))
    registered = {c.name or c.callback.__name__.replace("_", "-")
                  for c in typer_app.registered_commands}
    registered |= {g.name for g in typer_app.registered_groups}
    assert not registered - declared, (
        f"command(s) missing from the help layout: "
        f"{sorted(registered - declared)}")
    assert not declared - registered, (
        f"layout names a command that does not exist: "
        f"{sorted(declared - registered)}")


@pytest.mark.parametrize(
    "typer_app, layout",
    [(cli.app, cli.LAYOUT), (cli.task_app, cli.TASK_LAYOUT)],
    ids=["dg", "dg task"],
)
def test_each_command_declares_the_panel_the_layout_puts_it_in(typer_app, layout):
    """The layout says where a command reads; `rich_help_panel` says where it
    renders. Two places, so they are checked against each other — a command in
    the right sequence under the wrong heading looks deliberate and is not."""
    want = _panels(layout)
    for c in typer_app.registered_commands:
        name = c.name or c.callback.__name__.replace("_", "-")
        assert c.rich_help_panel == want[name], name
    for g in typer_app.registered_groups:
        assert g.rich_help_panel == want[g.name], g.name


def test_the_two_help_screens_agree_on_what_a_heading_means():
    """`dg task --help` is meant to be readable by someone who has read
    `dg --help`, which only works if a command that exists on both sides sits
    under the heading that means the same thing. It drifted once already:
    `render` was honesty about a generated view on one screen and part of
    starting a store on the other, and `export` was half of moving a store on
    one and a reading command on the other.

    Checked through `ROLES` rather than by comparing the headings themselves,
    because the wording differs on purpose — "Reading the graph" and "Reading
    the work" are the same heading about different stores.
    """
    dec = {n: cli.ROLES[p] for n, p in _panels(cli.LAYOUT).items()}
    tsk = {n: cli.ROLES[p] for n, p in _panels(cli.TASK_LAYOUT).items()}
    for name in sorted((set(dec) & set(tsk)) - set(DISAGREE)):
        assert dec[name] == tsk[name], (
            f"`dg {name}` and `dg task {name}` read under different headings: "
            f"{dec[name]} vs {tsk[name]}")


#: The one name the two screens disagree on, and why it is not a defect.
DISAGREE = {
    "drop": "`dg drop` unstages a staged op; `dg task drop` abandons a task. "
            "The task tray's unstage is `drop-op` precisely because this name "
            "was already taken by the more useful meaning.",
}


def test_a_heading_meaning_two_things_is_an_error_where_it_is_written():
    """Two of the headings are one string used by both screens, so the pairing
    cannot be a dict literal: it would keep the last and lose the count. The
    mechanism is exercised rather than trusted, because the case it catches is
    one nobody writes on purpose."""
    with pytest.raises(ValueError, match="two meanings"):
        cli._roles(("Keeping it honest", "honest"),
                   ("Keeping it honest", "store"))
    # And a heading shared on purpose is still one entry, not a refusal.
    assert cli._roles((cli.HONEST, "honest"), (cli.T_HONEST, "honest")) == {
        cli.HONEST: "honest"}


def test_every_panel_on_both_screens_has_a_role():
    """`ROLES` is what the agreement test reads; a panel missing from it makes
    that test raise rather than fail, which reads as a broken test."""
    for layout in (cli.LAYOUT, cli.TASK_LAYOUT):
        for panel, _ in layout:
            assert panel in cli.ROLES, panel


def test_the_one_heading_disagreement_is_the_deliberate_one():
    """So that closing it reads as a decision rather than as tidying up."""
    dec = {n: cli.ROLES[p] for n, p in _panels(cli.LAYOUT).items()}
    tsk = {n: cli.ROLES[p] for n, p in _panels(cli.TASK_LAYOUT).items()}
    for name in DISAGREE:
        assert dec[name] != tsk[name], name


def test_the_help_renders_the_panels_in_the_declared_order(tmp_path):
    """Rich builds a panel per heading in the order `list_commands` yields
    them, which is the property `_ordered` exists for."""
    out = runner.invoke(app, ["--project", str(tmp_path), "--help"]).output
    seen = [p for p, _ in cli.LAYOUT if p.split("—")[0].strip() in out]
    positions = [out.index(p.split("—")[0].strip()) for p in seen]
    assert positions == sorted(positions)
    assert len(seen) == len(cli.LAYOUT)


def test_the_rendered_order_follows_the_layout_not_the_file(tmp_path):
    """Rich takes command order from the file by default. `_ordered` is what
    makes the declaration win — so this compares the help against `LAYOUT`
    (which moves with the declaration, and is only evidence the override runs)
    *and* against the registration order (which does not).
    """
    out = runner.invoke(app, ["--project", str(tmp_path), "--help"]).output
    at = lambda n: out.index(f"│ {n} ")
    for _, names in cli.LAYOUT:
        seen = [at(n) for n in names if f"│ {n} " in out]
        assert seen == sorted(seen), names

    # The override is doing something: `decide` is registered before `add` and
    # `export` before `init`, and both read the other way round. Written out
    # rather than derived from LAYOUT, so reordering LAYOUT cannot quietly
    # bring this assertion along with it.
    registered = [c.name or c.callback.__name__.replace("_", "-")
                  for c in cli.app.registered_commands]
    assert registered.index("decide") < registered.index("add")
    assert registered.index("export") < registered.index("init")
    assert at("add") < at("decide")
    assert at("init") < at("export")


def test_aliases_read_as_one_line(tmp_path):
    """`why` and `context` are one command, and two lines carrying the same
    description would read as two — a reader would go looking for the
    difference. Both still run."""
    out = runner.invoke(app, ["--project", str(tmp_path), "--help"]).output
    assert "│ why/context " in out
    assert "│ why " not in out and "│ context " not in out
    for name in ("why", "context"):
        assert runner.invoke(app, [name, "--help"]).exit_code == 0


def _grouped():
    """The click group typer builds, and a context to ask it questions with.

    Through `context_class` rather than `click.Context`: typer vendors click
    now, so importing it by name is importing a package this project does not
    depend on and may not have.
    """
    import typer.main
    cmd = typer.main.get_command(app)
    return cmd, cmd.context_class(cmd, info_name="dg")


def _completions(incomplete: str) -> list[str]:
    cmd, ctx = _grouped()
    return [c.value for c in cmd.shell_complete(ctx, incomplete)]


def test_completion_offers_the_names_a_shell_can_type(tmp_path):
    """The one-line label is for the help screen. Click reads the same list to
    build completions, where `why/context` would be a name nobody typed —
    inserted by TAB — while `context`, which somebody might type, went
    missing from it."""
    assert "context" in _completions("c")
    assert _completions("w") == ["why"]
    assert not [c for c in _completions("") if "/" in c]


def test_the_help_and_the_shell_are_asked_for_different_lists(tmp_path):
    """Both audiences read `list_commands`, and they want different answers:
    one row for the pair, both names to type. Pinned together so neither can
    be fixed by giving the other the wrong one."""
    cmd, ctx = _grouped()
    assert "why/context" in cmd.list_commands(ctx)
    assert "why" in _completions("") and "context" in _completions("")
    assert ctx.meta == {}, "completion left its flag on the context"


def test_the_order_inside_a_panel_is_the_order_you_meet_them(tmp_path):
    """The sequences that motivated ordering the help at all, pinned literally:
    create before settle, settle before reverse, and removal last."""
    out = runner.invoke(app, ["--project", str(tmp_path), "--help"]).output
    at = lambda n: out.index(f"│ {n} ")
    for earlier, later in (("add", "decide"), ("decide", "reopen"),
                           ("reopen", "rm"), ("init", "import"),
                           ("import", "export"), ("pending", "apply"),
                           ("show", "areas"), ("check", "render")):
        assert at(earlier) < at(later), f"{earlier} should read before {later}"
