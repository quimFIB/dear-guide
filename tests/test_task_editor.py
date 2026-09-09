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


def test_the_stop_template_is_one_field_with_the_earlier_stops_in_view(
        tg, task_store):
    """T143, on D121: `render_done`'s shape for a park or a drop — the reason
    is the field, the record is beside it, and because a stop is appended and
    never cleared, the earlier ones are shown so the third says something the
    first two did not."""
    from dgraph.tasks import Stop
    tg.tasks["T04"].stops.append(Stop(why="waiting on the dataset",
                                      date="2026-01-03"))
    t = task_editor.render_stop(tg, None, "T04", "PARKED")
    assert ":DGRAPH_OP: set_status" in t and ":DGRAPH_TASK: T04" in t
    assert ":DGRAPH_STATUS: PARKED" in t
    body = t[t.index("* Input"):t.index("* Context")]
    assert body.count("** ") == 1 and "** Why" in body
    assert "waiting on the dataset" in body
    d = task_editor.render_stop(tg, None, "T04", "DROPPED")
    assert ":DGRAPH_STATUS: DROPPED" in d and "dg task drop T04" in d


def test_the_resolve_template_is_the_three_closing_fields_side_by_side(
        tg, task_store):
    """D122: the browser's one buffer for the task panel's three boxes — the
    field of `render_done` and the fields of the two `render_stop`s, each
    seeded from its box, with the earlier stops in view as the park buffer
    shows them. It parses to fields, not to an op."""
    from dgraph.tasks import Stop
    tg.tasks["T04"].stops.append(Stop(why="waiting on the dataset",
                                      date="2026-01-03"))
    t = task_editor.render_resolve(tg, None, "T04",
                                   {"outcome": "seeded", "why_park": "stuck"})
    assert ":DGRAPH_OP: resolve" in t and ":DGRAPH_TASK: T04" in t
    assert ":DGRAPH_STATUS:" not in t
    body = t[t.index("* Input"):t.index("* Context")]
    assert body.count("** ") == 3
    assert body.index("** Outcome") < body.index("** Why parked") \
        < body.index("** Why dropped")
    assert "seeded" in body and "stuck" in body
    assert "waiting on the dataset" in body
    got = task_editor.parse(fill(t, why_dropped="the licence fell through"),
                            tg=tg, g=None, expect_kind="resolve",
                            expect_task="T04")
    assert got == [{"op": "resolve", "task": "T04", "outcome": "seeded",
                    "why_park": "stuck",
                    "why_drop": "the licence fell through", "format": "org"}]


def test_the_fields_a_status_buffer_takes_follow_its_status():
    """A finishing buffer takes the outcome, a stopping one the reason; a
    `** Why` under a done buffer is an unknown field, not a second door."""
    assert task_editor._allowed("set_status", {"status": "DONE"}) == {"outcome"}
    assert task_editor._allowed("set_status", {"status": "PARKED"}) == {"why"}
    assert task_editor._allowed("set_status", {"status": "DROPPED"}) == {"why"}


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


def test_park_edit_stages_the_reason_it_was_given(run_cli, fake_emacs,
                                                  task_store):
    """T143: `dg task park --edit` is `dg task done --edit` for the reason —
    the same op `--why` stages, composed in the buffer instead."""
    fake_emacs(lambda t: fill(t, why="blocked on the dataset licence"))
    res = run_cli("task", "park", "T04", "--edit")
    assert res.exit_code == 0, res.output
    op = tray(task_store)[0]
    assert op == {**op, "op": "set_status", "task": "T04", "status": "PARKED",
                  "why": "blocked on the dataset licence"}
    assert op["date"]
    assert "outcome" not in op


def test_drop_edit_composes_the_reason_and_keeps_the_fallout_as_flags(
        run_cli, fake_emacs, task_store):
    """The reason is composed; the verdicts on other work are not in the
    buffer. T02 precedes T03, so dropping it needs a verdict on T03, given
    here as `--drop-too`, and the cascade op is staged beside the composed
    one as one write, exactly as the flag path stages it."""
    fake_emacs(lambda t: fill(t, why="the index it fed is gone"))
    res = run_cli("task", "drop", "T02", "--edit", "--drop-too", "T03")
    assert res.exit_code == 0, res.output
    ops = tray(task_store)
    assert [o["task"] for o in ops] == ["T02", "T03"]
    assert ops[0] == {**ops[0], "status": "DROPPED",
                      "why": "the index it fed is gone"}
    assert ops[1]["why"] == "abandoned along with T02"
    assert ops[0]["date"] == ops[1]["date"]


def test_a_reason_composed_in_org_claims_org(run_cli, fake_emacs, task_store):
    """A stop's `why` is prose the record's one `format` covers
    (`task_pending._apply_one`, `task_render`), so a reason with org emphasis
    must be tagged as an outcome is, or it renders as markdown's italic."""
    fake_emacs(lambda t: fill(t, why="*stuck* on the licence"))
    assert run_cli("task", "park", "T04", "--edit").exit_code == 0
    op = tray(task_store)[0]
    assert op["format"] == "org"
    assert run_cli("apply").exit_code == 0
    stored = json.loads((task_store / "tasks.json").read_text())
    t04 = next(t for t in stored["tasks"] if t["id"] == "T04")
    assert t04["format"] == "org"
    assert t04["stops"][-1]["why"] == "*stuck* on the licence"


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


# ---- task amend in an editor (D103, T110) -----------------------------------

def test_task_amend_composes_only_the_changed_fields(tg, task_store, fake_emacs):
    t = tg.tasks["T01"]
    seen = {}

    def edit(text):
        seen["t"] = text
        return fill(text.replace(f"** Title\n{t.title}\n", "** Title\nReworded work\n"))

    import dgraph.task_editor as te
    # Through the fixture, never `ed.launch = …`: a bare assignment outlives
    # the test, and every launcher test run after this file in the same
    # process then exercised the lambda (`AC-F8`).
    fake_emacs(edit)
    ops = te.compose_amend(tg, None, "T01")
    assert ops == [{"op": "set_fields", "task": "T01", "title": "Reworded work"}]


def test_task_amend_nothing_changed_aborts(tg, task_store, monkeypatch):
    import dgraph.task_editor as te, dgraph.editor as ed
    monkeypatch.setattr(ed, "launch", lambda p: 0)   # touches nothing
    with pytest.raises(EditorAbort):
        te.compose_amend(tg, None, "T01")


# ---- dg task edit: revise a staged task op (D102, T107) ---------------------

def test_task_edit_revises_a_staged_add_task_in_place(run_cli, task_store, monkeypatch):
    run_cli("task", "add", "--id", "T50", "--area", "Alpha", "--title", "draft")
    def edit(text):
        return fill(text.replace("** Title\ndraft\n", "** Title\nrevised\n"))
    import dgraph.editor as ed
    monkeypatch.setattr(ed, "launch",
                        lambda p: (p.write_text(edit(p.read_text())), 0)[1])
    res = run_cli("task", "edit", "0")
    assert res.exit_code == 0, res.output
    staged = [o for o in tray(task_store) if o["op"] == "add_task"]
    assert len(staged) == 1 and staged[0]["title"] == "revised"


def test_task_edit_refuses_a_derived_op(run_cli, task_store, monkeypatch):
    run_cli("task", "add", "--id", "T51", "--area", "Alpha", "--title", "w")
    run_cli("apply")
    run_cli("task", "start", "T51")                      # a set_status DOING op
    import dgraph.editor as ed
    monkeypatch.setattr(ed, "launch", lambda p: 0)
    res = run_cli("task", "edit", "0")
    assert res.exit_code == 1
    assert "derived or structural" in res.output


# ---- the twin's guards — what `dg edit` had that `dg task edit` lacked ------
# Pass 28 (`AC-F1`–`AC-F3`, `AC-F5`): the task ✎ was written as `dg edit`'s
# twin, and each guard the original had gained one audit at a time was
# missing from it. Each test below is one of those guards, on the twin.


def test_task_edit_renders_a_staged_add_task_that_carries_a_premise(
        run_cli, task_store, store, fake_emacs):
    """`AC-F1`: the staged op holds `because` as a list where the page's seed
    sends a string; the buffer shows it, and the revision keeps it."""
    run_cli("task", "add", "--id", "T50", "--area", "Alpha", "--title", "draft",
            "--because", "D01")
    seen = {}

    def edit(text):
        seen["text"] = text
        return text.replace("** Title\ndraft\n", "** Title\nrevised\n")
    fake_emacs(edit)
    res = run_cli("task", "edit", "0")
    assert res.exit_code == 0, res.output
    assert "** Because\nD01\n" in seen["text"]
    (op,) = [o for o in tray(task_store) if o["op"] == "add_task"]
    assert op["title"] == "revised" and op["because"] == ["D01"]


def test_task_edit_seeds_the_staged_edges_and_retracts_them_on_revision(
        run_cli, task_store, fake_emacs):
    """`AC-F2`: the buffer reads the task's edges back off the act (`F26`),
    and a revision retracts the edges the old version staged rather than
    leaving both readings — and the act stays one act."""
    run_cli("task", "add", "--id", "T50", "--area", "Alpha", "--title", "draft",
            "--after", "T02", "--discovered-during", "T04")
    seen = {}

    def retitle(text):
        seen["text"] = text
        return text.replace("** Title\ndraft\n", "** Title\nrevised\n")
    fake_emacs(retitle)
    res = run_cli("task", "edit", "0")
    assert res.exit_code == 0, res.output
    assert "** After\nT02\n" in seen["text"]
    assert "** Discovered during\nT04\n" in seen["text"]
    ops = tray(task_store)
    deps = [(o["from"], o["kind"]) for o in ops if o["op"] == "add_dep"]
    assert deps == [("T02", "precedes"), ("T04", "prompted")]
    groups = {o.get("group") for o in ops}
    assert len(groups) == 1 and None not in groups, ops     # still one act
    # Correcting After replaces the edge; it does not add a second reading.
    fake_emacs(lambda t: t.replace("** After\nT02\n", "** After\nT03\n"))
    res = run_cli("task", "edit", "0")
    assert res.exit_code == 0, res.output
    deps = [(o["from"], o["kind"]) for o in tray(task_store) if o["op"] == "add_dep"]
    assert deps == [("T03", "precedes"), ("T04", "prompted")]


def test_task_edit_judges_against_the_prefix_and_its_own_act(
        run_cli, task_store, fake_emacs):
    """`AC-F3`(a): a later act resting on the task being revised is not a
    reason to refuse, and never a reason to say the tray does not apply."""
    run_cli("task", "add", "--id", "T50", "--area", "Alpha", "--title", "draft")
    run_cli("task", "add", "--id", "T51", "--area", "Alpha", "--title", "later",
            "--after", "T50")
    fake_emacs(lambda t: t.replace("** Title\ndraft\n", "** Title\nrevised\n"))
    res = run_cli("task", "edit", "0")
    assert res.exit_code == 0, res.output
    assert "no longer apply" not in res.output
    assert [o["id"] for o in tray(task_store) if o["op"] == "add_task"] == ["T50", "T51"]


def test_task_edit_refuses_to_rest_on_an_act_staged_after_it(
        run_cli, task_store, fake_emacs):
    """`AC-F3`(b): `D97` on the task tray — refused by the act's name, the
    tray untouched, where the whole-tray reading accepted it and `dg apply`
    then aborted."""
    run_cli("task", "add", "--id", "T50", "--area", "Alpha", "--title", "draft")
    run_cli("task", "add", "--id", "T51", "--area", "Alpha", "--title", "later")
    before = tray(task_store)
    fake_emacs(lambda t: t.replace("** After\n", "** After\nT51\n", 1))
    res = run_cli("task", "edit", "0")
    assert res.exit_code == 1, res.output
    assert "T51 is added by act" in res.output and "staged after" in res.output
    assert tray(task_store) == before


def test_a_task_amend_of_the_note_in_emacs_is_tagged_org(
        run_cli, task_store, fake_emacs, monkeypatch):
    """`AC-F5`: the note is one of `PROSE`; an org buffer that writes it
    claims org, as the decision amend does and as `_tag` says."""
    monkeypatch.setenv("DG_EDIT_FORMAT", "org")
    fake_emacs(lambda t: t.replace(
        "** Note\nNobody has finished this yet.\n",
        "** Note\nNobody has finished this yet — *not even* the note.\n"))
    res = run_cli("task", "amend", "T04", "--edit")
    assert res.exit_code == 0, res.output
    (op,) = tray(task_store)
    assert op["note"].endswith("*not even* the note.")
    assert op.get("format") == "org", op
