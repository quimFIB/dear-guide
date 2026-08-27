"""The schematic renderings the read commands default to.

Two properties, and they pull against each other. The output has to be *short*
— it is read in a terminal and paid for in tokens down a pipe — and it has to
be *lossless about ids*, because an id is what the reader follows up with. So
prose is clipped and ids never are, and every compact view says which flag
brings the prose back.
"""

import json

import pytest
from typer.testing import CliRunner

from dgraph import compact, context, project
from dgraph.cli import app
from dgraph.render import write
from dgraph.tasks import TaskGraph

runner = CliRunner()


def dg(root, *args, cols="100"):
    """Invoke `dg` against one project at a known width.

    The width matters here in a way it does not elsewhere: these renderings
    size their columns from it, so a test that does not pin it is a test whose
    assertions depend on the terminal that ran it.
    """
    import os
    old = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = cols
    try:
        return runner.invoke(app, ["--project", str(root), *args])
    finally:
        os.environ.pop("COLUMNS") if old is None else os.environ.update(COLUMNS=old)


@pytest.fixture
def both(store, task_store, g):
    """One directory holding both stores, the tasks pointing at real decisions."""
    write(g)
    tg = TaskGraph.load(task_store / "tasks.json")
    tg.tasks["T01"].because = ["D01"]
    tg.tasks["T02"].because = ["D04"]
    tg.tasks["T03"].because = ["D05"]
    tg.tasks["T04"].evidence_for = "D05"
    tg.save(task_store / "tasks.json")
    return task_store


# ---- the gist ------------------------------------------------------------


def test_the_gist_is_the_first_paragraph_not_the_first_line():
    """Answers are stored wrapped, so the first *line* is a fragment cut
    mid-sentence — and a listing built out of fragments is one nobody can read
    down."""
    assert compact.gist("Approximate. A brute-force\nscan reads 140 GB.") == (
        "Approximate. A brute-force scan reads 140 GB.")


def test_the_gist_stops_at_the_paragraph_break():
    assert compact.gist("The answer.\n\nThe reasoning behind it.") == "The answer."


def test_emphasis_at_the_start_of_a_line_is_not_a_bullet():
    """`*HNSW*, M=32` opens with an asterisk and is a sentence. Skipping it as
    a list item skipped the one line that said what was decided."""
    assert compact.gist("*HNSW*, M=32.\n\nThe sweep:") == "*HNSW*, M=32."


def test_a_table_is_skipped_in_favour_of_the_prose_around_it():
    """"| index | recall@10 |" summarises nothing."""
    assert compact.gist("| a | b |\n|---|---|\n\nSo HNSW it is.") == "So HNSW it is."


def test_a_body_that_is_only_structure_summarises_as_empty():
    """Better nothing than a row of pipes standing in for an answer."""
    assert compact.gist("| a | b |\n|---|---|\n| 1 | 2 |") == ""


def test_org_prose_is_converted_the_way_the_generated_view_converts_it():
    assert compact.gist("*HNSW* it is.", "org") == "**HNSW** it is."


def test_no_prose_at_all_is_the_empty_string():
    assert compact.gist(None) == "" and compact.gist("") == ""


# ---- the listing ---------------------------------------------------------


ROWS = [("D01", "DECIDED", "A short one", "waits D00"),
        ("D02", "BLOCKED", "A very much longer title than the other", "")]


def test_an_id_is_never_clipped_however_narrow_the_width():
    """A line whose id is truncated is a line nobody can follow up, which is
    worse than a line that runs past the margin."""
    for line, (vid, *_) in zip(compact.listing(ROWS, width=40), ROWS):
        assert line.split()[0] == vid


def test_the_columns_line_up():
    """The whole value of a listing over a paragraph is reading down one
    column, so a title is padded even where the aside beside it is empty."""
    a, b = compact.listing(ROWS, width=100)
    assert a.index("DECIDED") == b.index("BLOCKED")


def test_a_long_title_is_clipped_with_an_ellipsis():
    line = compact.listing(ROWS, width=52)[1]
    assert "…" in line and "A very much" in line


def test_markup_does_not_skew_the_columns():
    """Padding a coloured field by `len()` counts the style tags as characters
    and knocks the column out of line by however much styling it carried."""
    plain = compact.listing(ROWS, width=100)
    styled = compact.listing(
        [(v, f"[red]{s}[/]", t, a) for v, s, t, a in ROWS],
        width=100, markup=True)
    assert [compact.visible(x) for x in styled] == plain


def test_an_escaped_bracket_is_not_read_as_markup():
    """`cli._x` writes a literal bracket as `\\[`. Counting that as a style tag
    pads every title holding a bracket four characters short."""
    assert compact.visible("a \\[literal] one") == "a \\[literal] one"


def test_clip_never_cuts_a_style_tag_in_half():
    """Widths are visible characters, so a tag costs nothing and is never cut.
    Slicing raw can end a line on `[di`, which Rich reads as the start of a tag
    that never closes and swallows the rest of the row."""
    cut = compact.clip("[dim]a detail long enough to be clipped[/]", 12)
    assert compact.visible(cut) == "a detail lo…"
    assert "[di" not in compact.visible(cut)
    assert cut.startswith("[dim]")


def test_clipping_measures_what_the_reader_sees():
    """A styled string and its plain twin clip to the same visible text —
    otherwise a coloured row says less than an uncoloured one at the same
    width, for no reason the reader can see."""
    plain = "→ PROVISIONAL (from D01)"
    styled = "→ PROVISIONAL [dim](from D01)[/]"
    assert compact.visible(compact.clip(styled, 18)) == compact.clip(plain, 18)


def test_tails_are_shown_when_they_fit():
    lines = compact.listing(ROWS, width=100, tails=["Alpha", "Beta"])
    assert "Alpha" in lines[0] and "Beta" in lines[1]


def test_tails_are_dropped_from_every_row_when_any_line_does_not_fit():
    """The column either reads down cleanly or is not there at all — a tail
    present on some rows and missing from others is worse than no tail."""
    lines = compact.listing(ROWS, width=46, tails=["Alpha", "Beta"])
    assert not any("Alpha" in ln or "Beta" in ln for ln in lines)


def test_an_empty_listing_is_no_lines_not_a_blank_one():
    assert compact.listing([]) == []


# ---- `dg context` --------------------------------------------------------


def test_context_is_compact_by_default(both):
    out = dg(both, "context", "T02").output
    assert "CHAIN" in out and "RESTS ON" not in out


def test_the_compact_form_names_every_id_the_full_one_does(both):
    """Prose is what gets clipped. An id is never dropped, because dropping one
    silently removes a premise from the reasoning."""
    short = dg(both, "context", "T02").output
    for p in context.data(project.find(), "T02")["chain"]:
        assert p["id"] in short


def test_the_chain_line_marks_what_is_not_settled(both):
    """The one line worth taking if you only take one: the shape of the
    reasoning, with a mark on every link that does not hold."""
    out = dg(both, "context", "T03").output
    assert "D05!" in out and "not settled" in out


def test_the_compact_form_says_which_flag_expands_it(both):
    """A reader who cannot tell whether the tool is summarising or simply does
    not know has to go and check, which costs more than the line saves."""
    assert "--full" in dg(both, "context", "T02").output


def test_full_restores_the_answers_and_the_falsifiers(both):
    out = dg(both, "context", "T02", "--full").output
    assert "RESTS ON" in out and "falsifier" in out
    assert "The root answer." in out


def test_the_verdict_is_the_same_either_way(both):
    """`--full` changes the length, never the reading."""
    verdict = context.data(project.find(), "T03")["verdict"]
    for args in (("context", "T03"), ("context", "T03", "--full")):
        assert verdict.split(",")[0] in dg(both, *args).output


def test_a_decision_closes_with_a_reading_too(both):
    """`text()` printed a closing line only when a premise was shaky, and
    silence there reads as "solid" — the one reading that must not be guessed."""
    assert "every premise under this is settled" in dg(both, "context", "D02").output


def test_the_premise_of_a_task_gets_its_own_line(both):
    """Clipped into the listing's aside column, it stopped being visible — and
    it is the reason the work exists."""
    out = dg(both, "context", "T02").output
    assert "BECAUSE  D04" in out


def test_json_is_untouched_by_the_new_default(both):
    """`--json` is a contract with host adapters; the default rendering is not
    allowed to reach it."""
    d = json.loads(dg(both, "context", "D04", "--json").output)
    assert d == context.data(project.find(), "D04")


# ---- `dg show` and `dg task` ---------------------------------------------


def test_show_is_a_listing_by_default_and_a_table_under_full(store, g):
    write(g)
    assert "┏" not in dg(store, "show").output
    assert "┏" in dg(store, "show", "--full").output


def test_the_show_listing_keeps_every_id_and_everything_waited_on(store, g):
    """The property `dg show`'s table had and a summary could quietly lose."""
    from dgraph import brief
    write(g)
    out = dg(store, "show").output
    for r in brief.rows(g):
        assert r.id in out
        for w in r.waiting_on:
            assert w in out


def test_the_task_listing_keeps_the_premise_gate(both):
    out = dg(both, "task").output
    assert "D05 (undecided)" in out


def test_both_listings_say_which_flag_expands_them(both):
    assert "--full" in dg(both, "show").output
    assert "--full" in dg(both, "task").output


def test_a_wider_terminal_spends_the_room_on_titles(store, g):
    """The width goes where the information is, rather than on more padding."""
    from dataclasses import replace
    g.vertices["D05"] = replace(
        g.vertices["D05"],
        title="A question long enough that a narrow terminal has to clip it")
    g.save()
    write(g)
    narrow = dg(store, "show", cols="70").output
    wide = dg(store, "show", cols="116").output
    assert narrow.count("…") == 1 and wide.count("…") == 0


def test_a_short_title_is_not_padded_out_to_the_floor(store, g):
    """`MIN_TITLE` stops a crowded line clipping a title to nothing; it is not
    a width to pad every short title out to."""
    write(g)
    line = [ln for ln in dg(store, "show").output.splitlines()
            if ln.startswith("  D06")][0]
    assert "Waiting on D05  ·" in line


def test_the_head_keeps_its_column_gaps(both):
    """`clip` normalises whitespace, so running the assembled head through it
    closed up the gaps that separate the id from the status from the title."""
    assert dg(both, "context", "T02").output.startswith("T02  TODO  ")


def test_the_compact_context_fits_a_narrow_terminal(both):
    """Printed plain, so nothing reflows it: it has to fit as written, whatever
    `$COLUMNS` says. A long title must be clipped, not allowed to run."""
    from dataclasses import replace
    from dgraph.model import Graph
    graph = Graph.load()
    graph.vertices["D04"] = replace(
        graph.vertices["D04"],
        title="A title far longer than any terminal is going to be, going on "
              "well past the point where anybody is still reading it")
    graph.save()
    for line in dg(both, "context", "T02", cols="200").output.splitlines():
        assert len(line) <= context.COMPACT_WIDTH, line


def test_a_long_relation_line_folds_rather_than_running(both):
    """The width is fixed and an id is never clipped, so a node with many
    premises has to fold — the one place the two rules meet."""
    from dgraph.model import Edge, Graph, Vertex
    graph = Graph.load()
    for n in range(7, 20):
        vid = f"D{n:02d}"
        graph.vertices[vid] = Vertex(id=vid, title=f"Premise {n}", area="Alpha",
                                     status="DECIDED")
        graph.edges.append(Edge(src=vid, to=["D05"], active=True,
                                answer="Settled.", source="discussion",
                                date="2026-01-01"))
    graph.save()
    out = dg(both, "context", "D05").output
    assert all(len(line) <= context.COMPACT_WIDTH for line in out.splitlines())
    for n in range(7, 20):
        assert f"D{n:02d}" in out


# ---- `dg pending` --------------------------------------------------------
#
# The tray is read in the same two places the other listings are — a terminal,
# and an agent's context immediately before `dg apply` — and it is the longest
# of them: one `dg reopen` stages the reopen plus a `set_status` per decided
# descendant, so a tray of nine ops is an ordinary afternoon rather than a
# pathological case.

TRAY = [
    {"op": "reopen", "vertex": "D01", "ref": "kfnq",
     "why": "the crawl finished at 48M vectors, five times the ~10M threshold "
            "named in the falsifier"},
    {"op": "set_status", "vertex": "D02", "ref": "bwsp",
     "status": "PROVISIONAL", "derived_from": "D01"},
    {"op": "set_status", "vertex": "D03", "ref": "hjza",
     "status": "PROVISIONAL", "derived_from": "D01"},
]

TASK_TRAY = [
    {"op": "add_task", "task": "T09", "ref": "wqmb",
     "title": "Re-run the recall sweep against the corpus as it now stands"},
]

#: The two trays, and the command that reads each.
TRAYS = [(["pending"], ".dgraph-pending.json", TRAY),
         (["task", "pending"], ".dgraph-task-pending.json", TASK_TRAY)]


@pytest.fixture
def staged(store, g):
    """Both trays holding work, so either command has something to render."""
    write(g)
    for _, name, ops in TRAYS:
        (store / name).write_text(json.dumps(ops), encoding="utf-8")
    return store


@pytest.mark.parametrize("cmd,_name,ops", TRAYS, ids=["dg", "dg task"])
def test_the_tray_is_a_listing_by_default_and_a_table_under_full(
        staged, cmd, _name, ops):
    """Both trays or neither: one of them compact while the other is
    box-drawn is the asymmetry the compact pass exists to remove."""
    assert "┏" not in dg(staged, *cmd).output
    assert "┏" in dg(staged, *cmd, "--full").output


@pytest.mark.parametrize("cmd,_name,ops", TRAYS, ids=["dg", "dg task"])
def test_a_staged_op_keeps_both_ways_of_addressing_it(staged, cmd, _name, ops):
    """The id `dg drop` takes and the position every message names. A clipped
    id is a row the review cannot act on, so neither is ever clipped — however
    narrow the terminal."""
    out = dg(staged, *cmd, cols="60").output
    for i, o in enumerate(ops):
        line = next(ln for ln in out.splitlines() if o["ref"] in ln)
        assert line.split()[:2] == [str(i), o["ref"]]


@pytest.mark.parametrize("cmd,_name,ops", TRAYS, ids=["dg", "dg task"])
def test_the_tray_says_which_flag_expands_it(staged, cmd, _name, ops):
    assert "--full" in dg(staged, *cmd).output


@pytest.mark.parametrize("cmd,_name,ops", TRAYS, ids=["dg", "dg task"])
def test_the_tray_keeps_saying_how_to_unstage_an_op(staged, cmd, _name, ops):
    """The compact form is a shorter view, not a view with less to act on."""
    out = dg(staged, *cmd).output
    assert "dg apply" in out and "drop" in out


def test_a_long_detail_is_clipped_rather_than_wrapped(staged):
    """One op, one line. A detail wrapped across four rows is what made the
    tray hard to read down in the first place."""
    out = dg(staged, "pending", cols="76").output
    body = [ln for ln in out.splitlines() if "kfnq" in ln]
    assert len(body) == 1 and "…" in body[0]


def test_the_derived_ops_still_name_what_derived_them(staged):
    """`(from D01)` is the difference between an op somebody wrote and one the
    tool propagated, which is the thing being reviewed."""
    out = dg(staged, "pending").output
    assert out.count("(from D01)") == 2


def test_the_tray_listing_is_pipe_safe(staged):
    """Read through a pipe into an agent's context before `dg apply`."""
    out = dg(staged, "pending").output
    assert "\x1b[" not in out and "[dim]" not in out
