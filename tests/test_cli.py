"""Terminal output. Rich reads `[...]` as markup, so anything from the store or
from a Violation must be escaped before it is printed — otherwise the most
useful token on the line is the one that silently disappears."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dgraph import editor, pending
from dgraph.cli import app
from dgraph.model import Graph
from dgraph.render import write

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
    assert "derived" in res.output and "dg drop 1" in res.output


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
    from importlib.metadata import version
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0
    assert res.output.strip() == version("decision-graph-assistant")


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
