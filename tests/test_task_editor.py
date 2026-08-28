"""Composing a task in an editor, driven without an editor installed.

The task store's half of the compose buffer. `fake_emacs` replaces the
launcher, exactly as `test_editor.py` does, so every rule here is exercised on
a machine with no emacs.

Two things are pinned beyond the round trip. The **barrier**: neither parser
accepts the other's buffer, so the two records cannot be composed through one
template by accident. And **provenance**: prose composed here is org, a task
carries one `format` for its whole record, and an outcome that claims org has
to render as org — the failure this feature would otherwise have introduced.
"""

import json

import pytest
from typer.testing import CliRunner

from dgraph import editor, pending, task_editor, task_pending, task_render
from dgraph.cli import app
from dgraph.editor import EditorAbort, EditorError
from dgraph.model import Graph
from dgraph.tasks import TaskGraph

runner = CliRunner()


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


@pytest.fixture
def run_cli(task_store, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERM", "dumb")

    def go(*args, input=None):
        return runner.invoke(app, ["--project", str(task_store), *args],
                             input=input)
    return go


def fill(text: str, **fields: str) -> str:
    """Type `body` under `** Field`, the way a person would.

    Underscores in the keyword become spaces, so `discovered_during=` fills
    `** Discovered during`.
    """
    for name, body in fields.items():
        head = f"** {name.replace('_', ' ').capitalize()}\n"
        assert head in text, f"no {head.strip()!r} in template"
        text = text.replace(head, head + body.rstrip("\n") + "\n", 1)
    return text


def tray(store):
    return pending.load(store / ".dgraph-task-pending.json")


def capture(seen: dict, **fields: str):
    """`fill`, keeping the template it was handed under `seen["text"]`.

    The buffer still comes back changed, so the command runs to the end: a test
    that only reads the template and returns it unedited asserts against a
    command that aborted, which is not the same command."""
    def edit(text: str) -> str:
        seen["text"] = text
        return fill(text, **fields)
    return edit


# ---- rendering -----------------------------------------------------------


def test_the_add_template_carries_what_the_parser_needs(tg, task_store):
    t = task_editor.render_add(tg, None)
    assert ":DGRAPH_OP: add_task" in t
    assert t.index("* Input") < t.index("* Context")   # fields before reference
    for field in ("** Id", "** Title", "** Area", "** After",
                  "** Discovered during", "** Because", "** Evidence for",
                  "** Note"):
        assert field in t


def test_the_add_template_offers_the_next_free_id_and_the_areas(tg, task_store):
    t = task_editor.render_add(tg, None)
    assert "T05" in t                       # T01..T04 in the fixture
    assert "Alpha" in t and "Beta" in t


def test_the_done_template_is_one_field_with_the_work_beside_it(tg, task_store):
    """The outcome is the reason this buffer exists; everything else about the
    task is reference material, not something to retype."""
    t = task_editor.render_done(tg, None, "T02")
    assert ":DGRAPH_OP: set_status" in t and ":DGRAPH_TASK: T02" in t
    body = t[t.index("* Input"):t.index("* Context")]
    assert body.count("** ") == 1 and "** Outcome" in body
    assert "T02" in t and "unblocks T03" in t


def test_the_done_template_shows_the_premise_from_the_other_store(
        tg, task_store, store, g):
    """An outcome written without the question in view says what was done
    rather than what it showed."""
    tg.tasks["T02"].evidence_for = "D05"
    t = task_editor.render_done(tg, g, "T02")
    assert "D05" in t and g.vertices["D05"].title in t
    assert "dg decide" in t


def test_a_task_keyword_line_is_the_task_store_s(tg, task_store):
    """Org colours the keywords it is told about; the decision list would
    colour the wrong words in a task buffer."""
    t = task_editor.render_add(tg, None)
    assert "#+TODO: TODO DOING PARKED | DONE DROPPED" in t


# ---- the round trip ------------------------------------------------------


def test_add_stages_the_task_and_its_edges_as_one_group(run_cli, fake_emacs,
                                                        task_store):
    fake_emacs(lambda t: fill(t, title="Move the sweep off the batch box",
                              area="Beta", after="T04",
                              discovered_during="T01"))
    res = run_cli("task", "add", "--edit")
    assert res.exit_code == 0, res.output
    ops = tray(task_store)
    assert [o["op"] for o in ops] == ["add_task", "add_dep", "add_dep"]
    assert ops[0]["id"] == "T05" and ops[0]["area"] == "Beta"
    assert {(o["from"], o["kind"]) for o in ops[1:]} == {
        ("T04", "precedes"), ("T01", "prompted")}


def test_flags_seed_the_buffer_rather_than_being_ignored(run_cli, fake_emacs,
                                                         task_store):
    """`dg task add -t … --edit` must not throw the title away — the buffer is
    where it is finished, not where it starts again."""
    fake_emacs(lambda t: t.replace("** Note\n", "** Note\nwritten in the buffer\n"))
    res = run_cli("task", "add", "--id", "T09", "-t", "Seeded title",
                  "--area", "Alpha", "--edit")
    assert res.exit_code == 0, res.output
    op = tray(task_store)[0]
    assert op["id"] == "T09" and op["title"] == "Seeded title"
    assert op["note"] == "written in the buffer"


def test_done_stages_the_outcome_it_was_given(run_cli, fake_emacs, task_store):
    fake_emacs(lambda t: fill(t, outcome="PR #241, and the numbers in bench/x.md"))
    res = run_cli("task", "done", "T02", "--edit")
    assert res.exit_code == 0, res.output
    op = tray(task_store)[0]
    assert op == {**op, "op": "set_status", "task": "T02", "status": "DONE",
                  "outcome": "PR #241, and the numbers in bench/x.md"}
    assert op["done"]


def test_prose_composed_here_is_org_and_renders_as_org(run_cli, fake_emacs,
                                                       task_store):
    """The trap this feature had to avoid. A task carries one `format` for its
    whole record and `task_render` converts through it, so an outcome typed as
    org and stored without saying so renders `*HNSW*` as italic — markdown's
    meaning for org's bold, silently."""
    fake_emacs(lambda t: fill(t, outcome="*0.94 recall* at 11ms p99"))
    assert run_cli("task", "done", "T02", "--edit").exit_code == 0
    assert run_cli("apply").exit_code == 0
    stored = json.loads((task_store / "tasks.json").read_text())
    t02 = next(t for t in stored["tasks"] if t["id"] == "T02")
    assert t02["format"] == "org"
    tg = TaskGraph.load(task_store / "tasks.json")
    task_render.write(tg, task_store / "tasks.md")
    assert "**0.94 recall**" in (task_store / "tasks.md").read_text()


def test_the_store_honours_format_for_an_outcome_not_only_a_note(tg):
    """The enabling fix, at the layer it lives in: `format` used to be applied
    only beside a `note`, so an outcome-only op could claim org and be stored
    as markdown."""
    task_pending._apply_one(tg, {"op": "set_status", "task": "T02",
                                 "status": "DONE", "outcome": "*bold*",
                                 "done": "2026-01-09", "format": "org"})
    assert tg.tasks["T02"].format == "org"


def test_the_command_says_when_the_record_s_dialect_changes(run_cli, fake_emacs,
                                                            task_store):
    """T04 carries a note typed as a flag — markdown. Composing an outcome for
    it makes the whole record org, which is the writer's call to make and
    theirs to be told about."""
    fake_emacs(lambda t: fill(t, outcome="done, see PR #12"))
    res = run_cli("task", "done", "T04", "--edit")
    assert res.exit_code == 0
    assert "markdown" in res.output and "org" in res.output


# ---- refusals ------------------------------------------------------------


def test_an_untouched_template_stages_nothing(run_cli, fake_emacs, task_store):
    fake_emacs(lambda t: t + "\n")          # changed, but no field filled
    res = run_cli("task", "add", "--edit")
    assert res.exit_code == 1 and "untouched" in res.output
    assert tray(task_store) == []


def test_a_missing_title_names_the_field(run_cli, fake_emacs, task_store):
    fake_emacs(lambda t: fill(t, area="Alpha"))
    res = run_cli("task", "add", "--edit")
    assert res.exit_code == 1 and "Title" in res.output
    assert tray(task_store) == []


def test_an_empty_outcome_stages_nothing(run_cli, fake_emacs, task_store):
    fake_emacs(lambda t: t.replace("* Input", "* Input\n"))
    res = run_cli("task", "done", "T02", "--edit")
    assert res.exit_code == 1
    assert tray(task_store) == []


def test_a_new_area_is_taken_and_a_near_miss_is_refused(run_cli, fake_emacs,
                                                        task_store):
    """`editor`'s twin. Areas accumulate, so `Gamma` is a legitimate thing to
    type into the buffer; `alpha` is a misspelling of an area in use and is
    refused in the buffer's own vocabulary, naming what it resembles."""
    fake_emacs(lambda t: fill(t, title="Something", area="Gamma"))
    assert run_cli("task", "add", "--edit").exit_code == 0
    assert tray(task_store)[0]["area"] == "Gamma"

    fake_emacs(lambda t: fill(t, title="Another", area="alpha"))
    res = run_cli("task", "add", "--edit")
    assert res.exit_code == 1 and "Alpha" in res.output and "Area" in res.output


def test_an_unknown_prerequisite_names_the_field_it_was_typed_in(
        run_cli, fake_emacs, task_store):
    fake_emacs(lambda t: fill(t, title="Something", area="Alpha", after="T99"))
    res = run_cli("task", "add", "--edit")
    assert res.exit_code == 1 and "After" in res.output and "T99" in res.output
    assert tray(task_store) == []


def test_a_premise_needs_a_decision_store_to_be_a_premise(run_cli, fake_emacs,
                                                          task_store):
    """`task_store` is a project that tracks only work, which is ordinary. What
    it cannot do is name a decision."""
    fake_emacs(lambda t: fill(t, title="Something", area="Alpha", because="D01"))
    res = run_cli("task", "add", "--edit")
    assert res.exit_code == 1 and "Because" in res.output
    assert "decisions.json" in res.output


def test_a_misspelled_field_is_rejected_not_dropped(run_cli, fake_emacs,
                                                    task_store):
    fake_emacs(lambda t: fill(t, title="Something", area="Alpha").replace(
        "** Note", "** Notes"))
    res = run_cli("task", "add", "--edit")
    assert res.exit_code == 1 and "Notes" in res.output


def test_an_id_that_is_not_a_task_id_is_refused(run_cli, fake_emacs, task_store):
    fake_emacs(lambda t: fill(t, title="Something", area="Alpha").replace(
        "\nT05\n", "\nD05\n"))
    res = run_cli("task", "add", "--edit")
    assert res.exit_code == 1 and "D05" in res.output


# ---- the barrier ---------------------------------------------------------


def test_a_decision_buffer_is_not_a_task_buffer(tg, task_store, store, g):
    """The two templates are not interchangeable, and neither parser will
    pretend otherwise — the barrier between the stores held as code rather than
    as a convention."""
    decision = editor.render_add(g)
    with pytest.raises(EditorError):
        task_editor.parse(decision, tg=tg, g=g, expect_kind="add_task")

    task = task_editor.render_add(tg, g)
    with pytest.raises(EditorError):
        editor.parse(task, g=g, expect_kind="add_vertex")


def test_a_done_buffer_cannot_be_retargeted_at_another_task(tg, task_store):
    """The rule `editor.parse` states for vertices, kept for tasks: retargeting
    by editing the drawer would stage a change to work nobody reviewed."""
    text = task_editor.render_done(tg, None, "T02")
    with pytest.raises(EditorError):
        task_editor.parse(text, tg=tg, g=None, expect_kind="set_status",
                          expect_task="T03")


def test_an_empty_buffer_aborts_rather_than_erroring(tg, task_store):
    with pytest.raises(EditorAbort):
        task_editor.parse("", tg=tg, g=None)


def test_an_op_claims_org_exactly_where_the_store_will_honour_it(tg, task_store):
    """One list, imported rather than restated — and read, which is the half
    that makes it hold. `format` is one field for a task's whole record and the
    store applies it beside whichever of `PROSE` the op writes, so an op
    claiming the dialect while writing none of them claims nothing, and one
    writing prose without claiming it renders org as markdown. Asserted over
    what the parsers actually emit, so a new field has to satisfy it."""
    assert task_editor.PROSE is task_pending.PROSE

    def ops(text, **fields):
        return task_editor.parse(fill(text, **fields), tg=tg, g=None)

    emitted = [
        *ops(task_editor.render_add(tg, None), title="Prose", area="Alpha",
             note="*HNSW* beat IVF"),          # writes a note
        *ops(task_editor.render_add(tg, None), title="Bare", area="Alpha",
             after="T04"),                     # writes none, and an edge op
        *ops(task_editor.render_done(tg, None, "T02"), outcome="*0.94* at 10"),
    ]
    assert len(emitted) == 4
    for op in emitted:
        wrote_prose = any(op.get(f) for f in task_pending.PROSE)
        assert bool(op.get("format")) == wrote_prose, op


def test_the_keyword_line_is_built_from_the_store_s_own_statuses(tg, task_store):
    """Restating them here is how the buffer comes to offer a status the store
    does not have, in the one place a writer types one by hand."""
    from dgraph import tasks
    line = next(ln for ln in task_editor.render_add(tg, None).splitlines()
                if ln.startswith("#+TODO:"))
    assert line == "#+TODO: TODO DOING PARKED | DONE DROPPED"
    for s in tasks.STATUSES:
        assert s in line


# ---- the buffer reads the effective decision graph ------------------------
#
# `--because` resolves against the store *plus* what is staged on it, so that a
# decision and the work it implies can be recorded in one batch. The buffer is
# the second door onto the same field and has to agree, or the two refuse
# different things and the frontier it lists is one nobody is standing in.


def test_a_premise_staged_but_not_applied_is_still_a_premise(
        run_cli, fake_emacs, task_store, store):
    """What `dg task add --because` accepts, the buffer must accept."""
    assert run_cli("add", "--id", "D07", "--title", "Just asked",
                   "--area", "Beta").exit_code == 0
    fake_emacs(lambda t: fill(t, title="Answer it by measuring", area="Beta",
                              because="D07"))
    res = run_cli("task", "add", "--edit")
    assert res.exit_code == 0, res.output
    assert [o for o in tray(task_store) if o["op"] == "add_task"][0][
        "because"] == ["D07"]


def test_the_buffer_lists_a_question_that_is_only_staged(
        run_cli, fake_emacs, task_store, store):
    """A frontier without the question just staged onto it is a buffer telling
    the writer their own last command did not happen."""
    assert run_cli("add", "--id", "D07", "--title", "Just asked",
                   "--area", "Beta").exit_code == 0
    seen = {}
    fake_emacs(capture(seen, title="Answer it", area="Beta"))
    assert run_cli("task", "add", "--edit").exit_code == 0
    assert "D07 OPEN — Just asked" in seen["text"]


def test_the_done_template_shows_a_premise_as_it_has_been_staged(
        run_cli, fake_emacs, task_store, store):
    """D05 is OPEN in the store and DECIDED once the staged close applies. An
    outcome is written against the second one."""
    assert run_cli("task", "link", "T02", "--because", "D05").exit_code == 0
    assert run_cli("decide", "D05", "--answer", "Settled.", "--source",
                   "discussion", "--falsifier", "the corpus changes",
                   "--opens", "").exit_code == 0
    seen = {}
    fake_emacs(capture(seen, outcome="PR #241"))
    assert run_cli("task", "done", "T02", "--edit").exit_code == 0
    assert "D05 — Still open · DECIDED" in seen["text"], seen["text"]


def test_a_decision_tray_that_will_not_apply_does_not_block_an_outcome(
        run_cli, fake_emacs, task_store, store, monkeypatch):
    """The two stores stage independently. Where `_eff` stops the command, the
    buffer degrades to the store and says which graph it is showing."""
    from dgraph import cli, pending as _pending

    def boom(g, **kw):
        raise _pending.ApplyError("D09 is not in the graph")
    monkeypatch.setattr(cli.pending, "preview", boom)
    fake_emacs(lambda t: fill(t, outcome="PR #241"))
    res = run_cli("task", "done", "T02", "--edit")
    assert res.exit_code == 0, res.output
    assert "no longer apply cleanly" in res.output
    assert [o["op"] for o in tray(task_store)] == ["set_status"]
