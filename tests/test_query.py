"""`dg find` — getting from a word to an id.

Three things are guarded here.

First, that **the vocabulary cannot drift from the schema**: field names are
read off the dataclasses, so renaming a stored field must break a test rather
than quietly leaving a term that matches nothing.

Second, that **an empty result and an unanswerable query stay apart**. Exit 1
means "you asked, and the answer is nothing"; exit 2 means "you did not ask
what you think you asked". Collapsing those two is how an empty result becomes
a false fact, and it is the failure this whole command is shaped to avoid.

Third, that **`query.py` never learns what the cross-graph link means**. The
terms that reach across arrive injected from `cli`, and the module stays
answerable about one store at a time.
"""

import copy
import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dgraph import query
from dgraph.cli import app
from dgraph.model import Graph
from dgraph.render import write
from dgraph.tasks import TaskGraph

runner = CliRunner()


def dg(root, *args):
    """Invoke `dg` against one project. `--project` is not optional; see
    `tests/test_context.py` for why."""
    return runner.invoke(app, ["--project", str(root), *args])


@pytest.fixture
def both(store, task_store, g):
    """Both stores in one directory, linked the way a real project links them."""
    write(g)
    tg = TaskGraph.load(task_store / "tasks.json")
    tg.tasks["T02"].because = ["D05"]     # premise still OPEN: gated
    tg.tasks["T03"].evidence_for = "D05"   # a spike feeding that question
    tg.save(task_store / "tasks.json")
    return task_store


@pytest.fixture
def lens(g):
    return query.decision_lens(g)


# ---- the grammar ---------------------------------------------------------


def test_terms_and_by_default():
    q = query.parse("alpha beta")
    assert len(q.groups) == 2 and all(len(grp) == 1 for grp in q.groups)


def test_or_merges_into_the_group_beside_it():
    """`a b or c` is `a AND (b OR c)` — the only nesting the grammar has, and
    it is positional rather than parenthesised on purpose."""
    q = query.parse("a b or c")
    assert [len(grp) for grp in q.groups] == [1, 2]


def test_a_leading_dash_negates():
    assert query.parse("-status:DECIDED").terms[0].negated


def test_a_plain_query_renders_back_as_itself():
    """Textual stability, where it holds. It is the *weaker* property — see
    `test_a_query_round_trips_by_meaning_and_not_by_text`, which is the one
    that matters and the one this used to stand in for."""
    for source in ("embedding -status:DECIDED", "status:OPEN or status:REOPENED",
                   'falsifier:"the corpus changes"', "/embed(ding)?s?/",
                   "is:ready under:D04", "date:>2026-01-01"):
        assert str(query.parse(source)) == source


def test_a_slash_inside_a_value_is_not_a_regex():
    """`date:2026/01` is a date. A `/` only opens a pattern where a value may
    start, or every path and date in the store becomes unquotable."""
    t = query.parse("date:2026/01").terms[0]
    assert not t.value.regex and t.value.raw == "2026/01"


def test_a_malformed_query_names_the_column():
    with pytest.raises(query.Fault) as exc:
        query.parse("title:ok :broken")
    assert exc.value.column == len("title:ok ")


@pytest.mark.parametrize("bad", ["", ":x", "is:", "x:", "/[/", "or x", "x or"])
def test_every_malformed_shape_is_a_fault(bad):
    with pytest.raises(query.Fault):
        query.parse(bad)


# ---- the field vocabulary ------------------------------------------------


def test_the_fields_are_the_stored_fields(lens):
    """Read off `Vertex` and `Edge` rather than retyped, so the query
    vocabulary cannot drift from the schema."""
    for name in ("title", "area", "status", "note", "answer", "falsifier",
                 "source", "date", "summary", "why"):
        assert name in lens.fields


def test_a_decision_and_its_edge_are_one_record(lens, g):
    """`falsifier:` is a field of a decision even though it is stored on the
    edge: the split is how dependency stays the graph structure, and not
    something somebody searching has to know."""
    q = query.parse("falsifier:evidence")
    assert "D01" in query.select(q, lens)


def test_a_bare_word_searches_prose_and_not_status(lens):
    """If `decided` matched every DECIDED vertex, the most natural query
    anybody could type would return most of the store.

    The fixture makes the point cleanly: only D05 says "decided" in its prose,
    and D05 is the one vertex that is `OPEN`. So a bare word returns exactly the
    opposite set from the status of the same name."""
    assert query.select(query.parse("decided"), lens) == ["D05"]
    assert query.select(query.parse("status:DECIDED"), lens) == [
        "D01", "D02", "D03", "D04"]


def test_an_unknown_field_is_a_fault_not_an_empty_result(lens):
    with pytest.raises(query.Fault) as exc:
        query.vet(query.parse("falsifer:x"), [lens])
    assert "falsifer" in exc.value.reason


def test_a_field_only_one_store_has_scopes_the_query(g, tg):
    """`falsifier:` needs no `--decisions`: tasks have no such field, so the
    query is already about decisions and saying so twice is noise."""
    lenses = [query.decision_lens(g), query.task_lens(tg)]
    scoped = query.scope(query.parse("falsifier:corpus"), lenses)
    assert [l.kind for l in scoped] == ["decisions"]


def test_one_question_in_two_vocabularies_keeps_both_stores(g, tg):
    """`is:unsettled or is:outstanding` asks "what is still live?" — each half
    in the vocabulary of one store. Narrowing term by term would take the
    decision store away on the second half and the task store away on the
    first, leaving nothing and reporting a contradiction that is not there."""
    lenses = [query.decision_lens(g), query.task_lens(tg)]
    q = query.parse("is:unsettled or is:outstanding")
    query.vet(q, lenses)
    scoped = query.scope(q, lenses)
    assert {l.kind for l in scoped} == {"decisions", "tasks"}
    assert query.select(q, scoped[0]) == g.frontier()
    assert query.select(q, scoped[1]) == tg.frontier()


def test_a_field_both_stores_have_keeps_both(g, tg):
    lenses = [query.decision_lens(g), query.task_lens(tg)]
    scoped = query.scope(query.parse("status:DONE"), lenses)
    assert {l.kind for l in scoped} == {"decisions", "tasks"}


# ---- predicates ----------------------------------------------------------


def test_every_predicate_delegates_to_something_that_exists(g, tg):
    """A predicate that raises here has lost the method it was standing in for.

    This covers the *code*, and only the code — it walks the lens's own dict.
    What keeps the documented table honest is
    `test_the_is_table_documents_every_predicate` below; the two were once
    confused for each other, and four predicates shipped undocumented while
    this one passed.
    """
    for lens, ids in ((query.decision_lens(g), g.vertices),
                      (query.task_lens(tg), tg.tasks)):
        for name, pred in lens.predicates.items():
            for rid in ids:
                assert isinstance(pred(rid), bool), name


#: The prose that claims to specify the `is:` vocabulary. Named here so the
#: test below fails loudly if the document is moved rather than silently
#: finding no table and asserting nothing.
QUERY_DOC = Path(__file__).resolve().parents[1] / "docs" / "query-framework.md"

#: A row of the `is:` table: the predicate, what it means on decisions, what it
#: means on tasks. Anchored to a lowercase backticked first cell and three
#: columns, which is that table's shape and no other table's in the file.
_IS_ROW = re.compile(r"^\| `([a-z-]+)` \| (.+?) \| (.+?) \|$", re.M)

#: The structural-terms table, whose first cell carries an argument
#: (`under:D04`) and so cannot match `_IS_ROW`.
_TERM_ROW = re.compile(
    r"^\| `([a-z]+:[A-Za-z0-9]+)` \| (.+?) \| (.+?) \|$", re.M)


def documented_predicates() -> dict[str, set[str]]:
    """The `is:` table, read out of the document rather than restated here.

    A list of expected names kept beside this function would be the third copy
    of the vocabulary and the one nothing checks — which is the defect being
    fixed, reintroduced in the fix.
    """
    rows = _IS_ROW.findall(QUERY_DOC.read_text(encoding="utf-8"))
    assert rows, f"no `is:` table found in {QUERY_DOC.name}"
    out: dict[str, set[str]] = {"decisions": set(), "tasks": set()}
    for name, decisions, tasks in rows:
        # An em dash alone is "not offered on this store". A cell that merely
        # contains one — `Task.resolved` — DONE or DROPPED` — is a definition.
        if decisions.strip() != "—":
            out["decisions"].add(name)
        if tasks.strip() != "—":
            out["tasks"].add(name)
    return out


#: A `Class.attr` or `module.func` in backticks — what the tables cite as the
#: thing a term delegates to. `(?!py`)` keeps the `` `model.py` `` pointers
#: beside them out: those name the file a symbol lives in, not a symbol.
_SYMBOL = re.compile(r"`([A-Za-z_]+\.(?!py`)[A-Za-z_]+)`")

#: Where an unqualified name in those citations lives. The tables write
#: `Vertex.settled`, not `dgraph.model.Vertex.settled`, because the prose reads
#: better; this is the one place that shorthand is expanded.
_HOMES = ("dgraph.model", "dgraph.tasks", "dgraph.cross", "dgraph.query",
          "dgraph.context", "dgraph.brief", "dgraph.compact")


def _resolves(dotted: str) -> bool:
    """Whether `Class.attr` / `module.func` names something that exists."""
    import importlib

    owner, _, attr = dotted.partition(".")
    for home in _HOMES:
        mod = importlib.import_module(home)
        if home.rsplit(".", 1)[-1] == owner:          # `cross.gated_by`
            if hasattr(mod, attr):
                return True
        target = getattr(mod, owner, None)            # `Vertex.settled`
        if target is not None and hasattr(target, attr):
            return True
    return False


def test_the_tables_delegate_to_things_that_exist():
    """The *other* two columns: what each term claims to delegate to.

    `test_the_is_table_documents_every_predicate` pins the term names. This
    pins the rest of the row, which was prose nothing read — and it had already
    rotted once: seventeen `file:line` citations in this document pointed at
    the wrong lines, `tasks.py:350` naming `TaskGraph.blocked` when it had
    moved to 602.

    The line numbers are gone now, because a citation that has to be re-checked
    on every edit is one that will not be. A symbol is the durable half: it
    survives the code moving, and it is the half a reader actually follows.
    """
    doc = QUERY_DOC.read_text(encoding="utf-8")
    rows = _IS_ROW.findall(doc) + _TERM_ROW.findall(doc)
    cited = {s for row in rows for cell in row[1:]
             for s in _SYMBOL.findall(cell)}
    assert cited, "no delegate symbols found — did the tables change shape?"
    missing = sorted(s for s in cited if not _resolves(s))
    assert not missing, (
        f"{QUERY_DOC.name} names symbols that do not exist: {missing}")


def test_the_is_table_documents_every_predicate(g, tg):
    """The table in `docs/query-framework.md` against the predicates that exist.

    The document called that table "the implementation's only specification"
    and claimed a test held it to the code. No test read the file at all, and
    the gap was not theoretical: `resolved`, `parked`, `implemented` and
    `awaiting-evidence` were all shipping and undocumented.

    Neither existing guard could have caught them.
    `test_the_predicate_lists_match_the_lenses` pins `DECISION_PREDICATES` and
    `TASK_PREDICATES` to the *base* lenses, so it never sees a predicate
    `cross.lenses` injects; and the delegation test above asserts a predicate
    works, not that anyone was told it exists.

    So this compares against `cross.lenses`, which is the one call holding the
    base and injected predicates together — the same surface `dg find` and
    `GET /api/find` are built on, so the table is checked against what a reader
    can actually type.

    Equality both ways on purpose. A row for a predicate that does not exist
    sends somebody to write a query that exits 2, which is the same defect
    pointing the other way.
    """
    from dgraph import cross

    documented = documented_predicates()
    for lens in cross.lenses(g, tg):
        assert set(lens.predicates) == documented[lens.kind], (
            f"the `is:` table in {QUERY_DOC.name} disagrees with "
            f"{lens.kind}: undocumented "
            f"{sorted(set(lens.predicates) - documented[lens.kind])}, "
            f"documented but absent "
            f"{sorted(documented[lens.kind] - set(lens.predicates))}"
        )


def test_unsettled_is_the_frontier(lens, g):
    assert query.select(query.parse("is:unsettled"), lens) == g.frontier()


def test_blocked_means_held_up_in_both_stores(g, tg):
    """One word, two derivations. A decision is held up by a premise it names
    in its status; a task by an unresolved prerequisite. That is not a wart —
    the stores are held up by different things."""
    assert query.select(query.parse("is:blocked"), query.decision_lens(g)) == ["D06"]
    assert query.select(query.parse("is:blocked"), query.task_lens(tg)) == ["T03"]


def test_superseded_finds_the_decision_that_was_overturned(lens):
    assert query.select(query.parse("is:superseded"), lens) == ["D01"]


# ---- the answers a decision used to have ---------------------------------


def test_answer_reads_the_edges_a_reversal_replaced(lens):
    """A reversal's prose is often the only place a rejected approach is
    written down, and `answer:` used to resolve against the active edge alone
    — so the reasoning that overturned a decision was searchable only through
    the label a reopen clipped from it."""
    assert query.select(query.parse('answer:"An older answer"'), lens) == ["D01"]


def test_active_narrows_the_search_to_the_answer_that_stands(g):
    """`dg find --active`. The archive is the point often enough to be the
    default, and noise often enough to need a way out."""
    now = query.decision_lens(g, archived=False)
    assert query.select(query.parse('answer:"An older answer"'), now) == []
    assert query.select(query.parse('answer:"The root answer"'), now) == ["D01"]


def test_a_hit_says_which_record_it_landed_in(lens):
    """The whole reason the panel grew edge cards: a row saying plain
    `answer:` about text the current answer does not contain is the same
    conflation, one surface along."""
    (m,) = query.explain(query.parse('answer:"An older answer"'), lens, "D01")
    assert m.field == "superseded answer"
    (m,) = query.explain(query.parse('answer:"The root answer"'), lens, "D01")
    assert m.field == "answer"


def test_date_is_when_this_was_settled_not_when_it_was_overturned(lens):
    """An editorial line, so it is guarded. `date:>=` asks when a decision was
    settled; answering it from a record that was overturned would quietly
    change what every date query already in use means. D01's superseded edge
    is dated 2025-12-01 and stays out of it."""
    assert query.select(query.parse("date:2025-12-01"), lens) == []
    assert query.select(query.parse("date:2026-01-01"), lens) == ["D01"]


def test_parked_is_not_the_same_question_as_blocked(tg):
    """A parked task may have every prerequisite resolved and still be put
    down. Blocked is what the graph says; parked is what somebody decided."""
    from dgraph.tasks import Stop
    tg.tasks["T02"].status = "PARKED"          # T02's prerequisite is DONE
    tg.tasks["T02"].stops = [Stop(why="stuck", date="2026-02-01")]
    lens = query.task_lens(tg)
    assert query.select(query.parse("is:parked"), lens) == ["T02"]
    assert "T02" not in query.select(query.parse("is:blocked"), lens)


def test_why_reads_the_stop_record(tg):
    """Both answer *why is this work not being done*, and the archived one is
    the half that survives being picked up again."""
    from dgraph.tasks import Stop
    tg.tasks["T02"].stops = [Stop(why="stuck on the kern bug", date="2026-02-01")]
    lens = query.task_lens(tg)
    assert query.select(query.parse('why:"kern bug"'), lens) == ["T02"]
    (m,) = query.explain(query.parse('why:"kern bug"'), lens, "T02")
    assert m.field == "stopped earlier because"


def test_outcome_reads_the_one_result_there_is(tg):
    """`why:` still reads a list; `outcome:` no longer has one to read.

    The two used to be twins — a result an earlier round produced was still a
    result, and was labelled *produced earlier* so it could not be read as
    current. `D81` removed the second round: a task has one outcome, and work
    done again is a child task with a result of its own. `why:` keeps its list
    because a task really can be put down more than once."""
    from conftest import finished
    finished(tg.tasks["T02"], "2026-03-01", "IVF-PQ 40ms")
    lens = query.task_lens(tg)
    assert query.select(query.parse("outcome:IVF"), lens) == ["T02"]
    (m,) = query.explain(query.parse("outcome:IVF"), lens, "T02")
    assert m.field == "outcome"


def test_done_asks_when_this_work_was_finished(tg):
    """`done:>=` asks when this work was finished, and since `D81` there is
    exactly one answer: `DONE` is terminal, so the date cannot be superseded
    and there is no earlier one for the query to reach past."""
    from conftest import finished
    finished(tg.tasks["T02"], "2026-01-09", "a number")
    lens = query.task_lens(tg)
    assert "T02" in query.select(query.parse("done:>=2026-01-01"), lens)
    assert "T02" not in query.select(query.parse("done:>=2026-06-01"), lens)


# ---- structure -----------------------------------------------------------


def test_under_selects_the_subgraph_a_decision_opened(lens, g):
    assert set(query.select(query.parse("under:D01"), lens)) == g.descendants("D01")


def test_under_composes_with_a_predicate(lens, g):
    """The query the tool could not previously ask: what is still open in the
    part of the graph this decision opened."""
    got = query.select(query.parse("under:D01 is:unsettled"), lens)
    assert set(got) == g.descendants("D01") & set(g.frontier())


def test_a_structural_term_explains_nothing(lens):
    """`under:D04` matched no text and must not pretend it did, or the aside
    would claim a hit in a field nobody searched."""
    q = query.parse("under:D01")
    assert query.explain(q, lens, "D02") == []


# ---- the cross-graph barrier ---------------------------------------------


def test_query_never_names_the_link():
    """The rule `tests/test_cross.py` enforces generally, asserted here where
    the reason lives. `cross.rests_on` looks like a plain string comparison and
    is really the derived reverse of the relation, so a generic field match on
    `because` would be a second implementation of it."""
    import inspect
    src = inspect.getsource(query)
    assert ".because" not in src and ".evidence_for" not in src


def test_query_does_not_import_cross():
    import inspect
    src = inspect.getsource(query)
    assert "import cross" not in src


def test_the_link_fields_are_hidden_from_the_generic_table(tg):
    """Built from the dataclass, the table would offer `because` without this
    module ever naming it — which is why hiding is the caller's job and is
    asserted rather than assumed."""
    from dgraph.cross import LINK_FIELDS
    lens = query.task_lens(tg, hide=LINK_FIELDS)
    assert "because" not in lens.fields and "evidence_for" not in lens.fields


def test_because_is_injected_and_finds_the_work_a_decision_justifies(both):
    r = dg(both, "find", "because:D05", "--ids")
    assert r.exit_code == 0 and r.stdout.split() == ["T02"]


def test_evidence_is_injected_and_finds_the_spike(both):
    r = dg(both, "find", "evidence:D05", "--ids")
    assert r.exit_code == 0 and r.stdout.split() == ["T03"]


def test_ready_needs_a_decision_store(task_store):
    """`cli._gated_by` returns None with no decision store, which is right for
    "can I start this?" and wrong for a predicate asserting a property — so the
    predicate is absent rather than quietly meaning `TaskGraph.ready`.

    The **sentence** is asserted, not only the code. This test passed for a
    while against `no predicate `is:ready` — try blocked, orphaned,
    outstanding, resolved`, which denies a predicate that exists and sends the
    reader to fix a spelling that was right. Checking the exit code alone is
    what let that stand."""
    r = dg(task_store, "find", "is:ready")
    assert r.exit_code == 2
    assert "is:ready" in r.stdout and "decision store" in r.stdout


def test_ready_is_available_once_both_stores_are(both):
    """The contrast with the case above: here the predicate *can* be answered,
    and the answer is that nothing is ready — T02's premise D05 is still open,
    and everything else is finished, running, or waiting on T02.

    Exit 1, not 2. "You asked, and the answer is nothing" is a different thing
    from "that question cannot be answered here", and keeping the two apart is
    what the whole command is shaped around."""
    r = dg(both, "find", "is:ready", "--ids")
    assert r.exit_code == 1 and r.stdout.split() == []


# ---- the command ---------------------------------------------------------


def test_no_matches_is_exit_one_not_two(store):
    r = dg(store, "find", "nothinglikethisanywhere")
    assert r.exit_code == 1


def test_an_unanswerable_query_is_exit_two(store):
    r = dg(store, "find", "falsifer:x")
    assert r.exit_code == 2


def test_the_two_empty_answers_are_distinguishable(store):
    """The property the whole design turns on: a script must be able to tell
    "already settled, nothing found" from "you asked wrong"."""
    assert dg(store, "find", "zzznotfound").exit_code == 1
    assert dg(store, "find", "zzz:notfound").exit_code == 2


def test_a_flag_that_contradicts_the_query_is_refused(both):
    """Honouring the flag would print an empty decisions section for a query
    with a perfectly good answer, and that failure is invisible."""
    r = dg(both, "find", "--decisions", "because:D05")
    assert r.exit_code == 2 and "contradicts" in r.stdout


def test_opposite_flags_are_refused(store):
    assert dg(store, "find", "--decisions", "--tasks", "x").exit_code == 2


def test_ids_prints_nothing_but_ids(store):
    r = dg(store, "find", "is:unsettled", "--ids")
    assert r.exit_code == 0
    assert all(line.startswith("D") for line in r.stdout.split())


def test_json_and_the_listing_come_from_one_walk(store):
    """The `brief.py` property: a program parsing JSON and a person reading the
    terminal cannot be told different things."""
    data = json.loads(dg(store, "find", "is:unsettled", "--json").stdout)
    ids = [row["id"] for row in data["decisions"]]
    assert ids == dg(store, "find", "is:unsettled", "--ids").stdout.split()


def test_json_says_which_field_matched(store):
    data = json.loads(dg(store, "find", "falsifier:evidence", "--json").stdout)
    assert data["decisions"][0]["matched"][0]["field"] == "falsifier"


def test_a_typo_is_offered_a_spelling_and_not_a_result(store):
    """The concession that makes exactness liveable. The suggestion is a re-run
    to accept or ignore — never rows folded into an answer, which would put the
    empty result's factuality back at risk."""
    r = dg(store, "find", "consequense")
    assert r.exit_code == 1 and "did you mean" in r.stdout
    assert "D02" not in r.stdout


def test_the_staging_tray_is_counted_and_not_searched(store, g):
    """A staged op has no id to follow up with, so a result set holding one
    would be a set where some rows answer `dg context` and some do not."""
    dg(store, "add", "--id", "D09", "--title", "A staged question",
       "--area", "Alpha")
    r = dg(store, "find", "staged")
    assert "D09" not in r.stdout
    assert "staged and not applied" in r.stdout


# ---- the boundary between "no" and "wrong" -------------------------------
#
# Everything below guards the one property this command is shaped around, in
# the four places it was found leaking: a contradiction between stores, a
# mistyped id, a date operator that could never match, and a predicate whose
# store is missing. Each is paired with the honest empty result it must stay
# distinguishable from, because a fix that collapsed the pair the other way
# would pass a one-sided test.


def test_a_query_no_store_can_answer_is_a_fault(both):
    """`falsifier:corpus because:D05` asks for a record that is both a decision
    and a task. Each half is fine; together they scope to nothing.

    This used to print the hint line and *nothing else* and exit 1, which reads
    as "there is no such work" — a false fact produced by the one path through
    `scope` that had no guard on it."""
    r = dg(both, "find", "falsifier:corpus because:D05")
    assert r.exit_code == 2
    assert "falsifier:corpus" in r.stdout and "because:D05" in r.stdout


def test_the_contradiction_names_both_halves(both):
    """Naming only the term that tripped it would send the reader to delete the
    wrong one."""
    r = dg(both, "find", "is:settled is:outstanding")
    assert "decisions" in r.stdout and "tasks" in r.stdout


def test_a_genuinely_empty_answer_over_both_stores_is_still_exit_one(both):
    """The other side of the pair. Fixing the contradiction by treating an
    empty scope as an error would be easy to do by treating an empty *result*
    as one too, and that would destroy the property instead of restoring it."""
    r = dg(both, "find", "zzznotanywhereinthisstore")
    assert r.exit_code == 1


@pytest.mark.parametrize("q", ["under:D0", "under:NOSUCH", "above:NOSUCH",
                               "because:NOSUCH", "waits:ZZZ", "after:NOSUCH"])
def test_a_structural_term_naming_no_record_is_a_fault(both, q):
    """`model.children`/`depends` skip ids that name no vertex — right for
    traversal, because `validate()` is what reports a dangling edge, and wrong
    here, where `under:D0` came back empty and read as *nothing is under D04*.

    A dropped digit is the likeliest typo in this grammar and was the only one
    with no rescue at all: the *did you mean* pass covers bare words only."""
    assert dg(both, "find", q).exit_code == 2


def test_a_subgraph_that_is_really_empty_is_still_exit_one(store):
    """D03 exists and opens nothing, so `under:D03` is a fair question with
    nothing in it. The id check must not swallow this case."""
    assert dg(store, "find", "under:D03").exit_code == 1


def test_a_mistyped_id_is_offered_a_near_miss(store):
    """The rescue the prose terms already had, extended to the term class where
    a typo was both likeliest and completely silent. A suggestion, never a
    row: the empty result stays a fact only while nothing has been guessed."""
    r = dg(store, "find", "under:D04x")
    assert r.exit_code == 2 and "did you mean" in r.stdout and "D04" in r.stdout


def test_the_link_terms_resolve_their_argument_against_the_decision_ids(both):
    """`because:` lives on the task lens and its argument is a *decision* id, so
    `arg_kind` resolves it against the decision store rather than against the
    ids of the lens it sits on. Resolving it the other way would refuse every
    correct use of the term.

    The claim needs both halves: that a decision id is accepted -- which
    `test_because_is_injected_...` above covers -- and that a *task* id in the
    same position faults rather than silently matching nothing, which is the
    reading a bare "no results" would hide."""
    bad = dg(both, "find", "because:T02", "--ids")
    assert bad.exit_code != 0
    assert "T02" in bad.output


def test_the_date_operators_include_their_boundary(lens):
    """`date:>=` used to parse as `>` with a stray `=` on the operand, and
    `"2026-01-03" > "=2026-01-03"` is false for every date there will ever be.
    So the most natural thing to type after learning `>` matched nothing, said
    exit 1, and never explained itself."""
    after = query.select(query.parse("date:>2026-01-03"), lens)
    incl = query.select(query.parse("date:>=2026-01-03"), lens)
    assert incl == ["D03", *after]


def test_the_other_date_operators_still_mean_what_they_did(lens):
    assert query.select(query.parse("date:<=2026-01-02"), lens) == ["D01", "D02"]
    assert query.select(query.parse("date:<2026-01-02"), lens) == ["D01"]


@pytest.mark.parametrize("bad", ["date:>", "date:<", "date:>=", "date:<="])
def test_an_operator_with_no_date_is_a_fault(bad):
    """`date:>` compared every date against the empty string and so matched
    them all — the same failure pointing the other way."""
    with pytest.raises(query.Fault):
        query.parse(bad)


def test_a_predicate_whose_store_is_absent_says_which_store(task_store):
    """"no predicate `is:ready` — try blocked, orphaned, outstanding, resolved"
    sends a reader to fix their spelling. Their spelling was right; the project
    is missing the decision store that answers the question."""
    r = dg(task_store, "find", "is:ready")
    assert r.exit_code == 2 and "decision store" in r.stdout


def test_an_unknown_predicate_is_still_reported_as_a_typo(task_store):
    """The contrast: `is:notathing` really is a misspelling, and the offer list
    is the right help for it."""
    r = dg(task_store, "find", "is:notathing")
    assert r.exit_code == 2 and "no predicate" in r.stdout


# ---- the scanner keeps a value's parts apart -----------------------------


def test_a_colon_inside_a_phrase_is_not_a_field_separator():
    """The silent one. `parse` used to re-split the joined token on the first
    `:`, so a quoted phrase beginning with a field name became a search of that
    field — different rows, no fault, and no way to tell."""
    t = query.parse('"note: nobody has"').terms[0]
    assert t.kind == "prose" and t.value.raw == "note: nobody has"


def test_a_regex_may_contain_a_colon():
    """Same cause, louder symptom: this became a field named `\\x00foo`, and
    the control character used to smuggle regex-ness through the string leaked
    into the error message."""
    t = query.parse("/foo:bar/").terms[0]
    assert t.kind == "prose" and t.value.regex and t.value.raw == "foo:bar"


def test_a_regex_may_contain_an_escaped_delimiter():
    r"""`/https?:\/\//` could not be written at all: the backslash was copied
    and the `/` after it closed the pattern."""
    assert query.parse(r"/https?:\/\//").terms[0].value.raw == "https?://"


def test_only_the_delimiter_is_escapable_inside_a_pattern():
    r"""`\w` must survive as `\w`, and `\\` as `\\`. Collapsing every escape
    would hand `re` a dangling backslash and stop `/a\\/` compiling."""
    assert query.parse(r"/\w+/").terms[0].value.raw == r"\w+"
    assert query.parse(r"/a\\/").terms[0].value.raw == "a\\\\"


def test_quoting_makes_or_a_word_rather_than_the_operator():
    """Quoting is how a reader says *literally this*. Testing the joined text
    made `x "or" y` mean `x OR y` and left the word unsearchable."""
    q = query.parse('x "or" y')
    assert len(q.groups) == 3
    assert query.parse('"or"').terms[0].value.raw == "or"


@pytest.mark.parametrize("bad", ["/foo/i", '"ab"cd'])
def test_text_after_a_closing_delimiter_is_a_fault(bad):
    """`/foo/i` is somebody reaching for a regex flag. It used to be silently
    joined up and searched for as `fooi`."""
    with pytest.raises(query.Fault):
        query.parse(bad)


def test_the_regex_sentinel_is_gone():
    """A control character carrying structure through a string is what made
    the colon bug possible and what leaked into a user-facing message. `Tok`
    carries it in a field instead."""
    assert not hasattr(query, "REGEX_MARK")


def test_a_query_round_trips_by_meaning_and_not_by_text():
    """`str(Query)` is what `--json` reports as the query it ran and what the
    browser writes back into its box, so it has to *parse back to the same
    thing*. Asserting the string alone was the weaker property and is what hid
    `title:/a b/` rendering as `title:"/a b/"` and returning a literal."""
    def shape(q):
        return [[(t.kind, t.name, t.negated,
                  None if t.value is None else
                  (t.value.raw, t.value.regex, t.value.op)) for t in grp]
                for grp in q.groups]

    for source in ("embedding -status:DECIDED", "status:OPEN or status:REOPENED",
                   'falsifier:"the corpus changes"', "/embed(ding)?s?/",
                   "is:ready under:D04", "date:>2026-01-01", "date:>=2026-01",
                   "title:/a b/", "/a b/", "/foo:bar/", 'x "or" y',
                   r"/https?:\/\//"):
        once = query.parse(source)
        assert shape(query.parse(str(once))) == shape(once), source


# ---- cost -----------------------------------------------------------------


def test_a_structural_term_is_walked_once_per_query(g):
    """`fn(arg)` returns the same whole set for every candidate row, and it was
    called once per row — building the set, testing one membership, throwing it
    away. Six hundred vertices made `above:` take fifteen seconds against a
    walk that costs twenty milliseconds.

    Pinned as an invocation count rather than a duration: the timing would be
    flaky and the invariant is the thing that actually matters."""
    lens = query.decision_lens(g)
    calls, real = [], lens.structural["under"]
    lens.structural["under"] = lambda arg: (calls.append(arg), real(arg))[1]
    got = query.select(query.parse("under:D01"), lens)
    assert set(got) == g.descendants("D01")
    assert len(calls) == 1, f"walked {len(calls)} times for {len(lens.ids)} rows"


def test_a_term_whose_argument_is_not_in_this_store_skips_the_walk(both):
    """`waits:` exists on both lenses, so a decision id used to be walked over
    the task store too — ten of the twenty-four seconds that query took, spent
    proving an empty set empty."""
    from dgraph import cross
    from dgraph.model import Graph
    from dgraph.tasks import TaskGraph

    lenses = cross.lenses(Graph.load(), TaskGraph.load(both / "tasks.json"))
    tasks = next(l for l in lenses if l.kind == "tasks")
    calls, real = [], tasks.structural["waits"]
    tasks.structural["waits"] = lambda arg: (calls.append(arg), real(arg))[1]
    query.select(query.parse("waits:D05"), tasks)
    assert calls == []


@pytest.mark.parametrize("pattern", [r"/(a+)+$/", r"/^(\w+\s+)+\w+!/",
                                     r"/(x+x+)+y/"])
def test_a_pattern_that_can_backtrack_exponentially_is_refused(pattern):
    r"""`dg find '/^(\w+\s+)+\w+!/'` over six hundred records did not return
    within two minutes, and the same query reaches `GET /api/find` from any
    page in the browser. `re` has no timeout and a running match cannot be
    interrupted, so the only place to stop it is before it starts."""
    with pytest.raises(query.Fault):
        query.parse(pattern)


@pytest.mark.parametrize("pattern", [r"/embed(ding)?s?/", r"/\w+/",
                                     r"/https?:\/\//", r"/D0[1-9]/",
                                     r"/(foo|bar)/", r"/[A-Z]{2,4}-\d+/"])
def test_an_ordinary_pattern_is_not_refused(pattern):
    """The guard refuses a *shape*, so it has to leave the shapes people
    actually write alone — `/embed(ding)?s?/` is the design's own example of
    the escape hatch that makes exactness liveable."""
    assert query.parse(pattern).terms[0].value.regex


def test_a_query_past_the_cap_is_refused():
    """`GET /api/find?q=` accepts whatever is sent, and it is a GET, so no
    token stands in front of it."""
    with pytest.raises(query.Fault):
        query.parse("a" * (query.MAX_QUERY + 1))


# ---- what the command reports --------------------------------------------


def test_json_names_every_store_including_the_ones_out_of_scope(both):
    """`null` for a store the query was not about, `[]` for one that was and
    matched nothing. Omitting the key left a consumer unable to tell those
    apart without re-parsing the query, and `data["decisions"]` raising on a
    perfectly good answer. `find_payload` drew this distinction from the
    start; the two surfaces now agree."""
    data = json.loads(dg(both, "find", "falsifier:corpus", "--json").stdout)
    assert data["tasks"] is None and isinstance(data["decisions"], list)
    assert data["scope"] == ["decisions"]


def test_a_fault_under_json_is_reported_as_json(store):
    """Exit 2 already tells a script it asked wrong; it should not have to
    parse a caret diagram to find out why."""
    r = dg(store, "find", "zzz:x", "--json")
    assert r.exit_code == 2
    assert json.loads(r.stdout)["fault"].startswith("unknown field")


@pytest.mark.parametrize("n", ["0", "-1"])
def test_limit_counts_rows_and_so_starts_at_one(store, n):
    """`--limit 0` printed a count and a "… 2 more" line with no rows above
    it; `--limit -1` dropped the *last* row and reported it as withheld —
    Python's slice semantics leaking through a flag."""
    assert dg(store, "find", "is:unsettled", "--limit", n).exit_code == 2


def test_the_withheld_field_list_is_checked_against_the_dataclasses():
    """`decision_lens` cannot derive which stored fields are unsearchable — it
    is an editorial judgement — but it can verify the list names real fields.
    A rename that left the list behind would quietly make the old field
    searchable, which is the drift `_fields_of` exists to prevent."""
    with pytest.raises(AssertionError):
        query._excluded("tasks", query.Value)


def test_the_predicate_lists_match_the_lenses(g, tg):
    """`DECISION_PREDICATES` and `TASK_PREDICATES` exist so that `cross.lenses`
    can name what a *missing* store would have answered — a lens that was never
    built cannot list its own vocabulary. They are checked here rather than
    derived, so a predicate added without updating them fails a test instead of
    becoming one that a project lacking the store denies rather than explains."""
    assert set(query.DECISION_PREDICATES) == set(query.decision_lens(g).predicates)
    assert set(query.TASK_PREDICATES) == set(query.task_lens(tg).predicates)


def test_a_predicate_is_explained_when_its_store_will_not_parse(tmp_path, monkeypatch):
    """The warning about an unreadable `decisions.json` is right and should be
    the whole answer, not a preamble to a line denying the predicate."""
    from dgraph import project
    from conftest import TASK_FIXTURE

    (tmp_path / "tasks.json").write_text(json.dumps(TASK_FIXTURE), encoding="utf-8")
    (tmp_path / "decisions.json").write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(project, "_override", tmp_path)
    r = dg(tmp_path, "find", "is:unsettled")
    assert r.exit_code == 2
    assert "could not be read" in r.stdout and "decision store" in r.stdout
    assert "no predicate" not in r.stdout


# ---- exact where the vocabulary is closed, substring where it is prose ----


def test_an_id_matches_exactly_and_not_as_a_substring(both):
    """`id:0` used to return every record in this store, and in a larger one a
    scattered eleven sharing nothing but a digit — the worse case, because it
    can be mistaken for an answer rather than noticed as nonsense.

    Ids are zero-padded, so every id below ten carries a `0`; the substring
    rule was inherited from the generic field path rather than chosen."""
    assert dg(both, "find", "id:D04", "--ids").stdout.split() == ["D04"]
    assert dg(both, "find", "id:0").exit_code == 1
    assert dg(both, "find", "id:D").exit_code == 1


def test_an_id_still_ignores_case():
    """Case-insensitive throughout, exact fields included."""
    from dgraph.query import Value
    assert Value("d04").same("D04")


def test_a_pattern_overrides_exactness(both):
    """The bargain that makes a strict default affordable: `/…/` is one
    keystroke away and says out loud that an approximation is wanted, so the
    closed-vocabulary fields can afford to be exact without losing anything."""
    got = dg(both, "find", "id:/^D0/", "--ids").stdout.split()
    assert got == ["D01", "D02", "D03", "D04", "D05", "D06"]


def test_a_status_matches_the_base_and_the_stored_form(store):
    """There is no stored blocked status (`D68`): a waiting vertex is `OPEN`,
    and *held up* is the derived `is:blocked`. `status:BLOCKED` matches
    nothing, honestly — exit 1, not a fault, because the field is real."""
    assert dg(store, "find", "status:BLOCKED", "--ids").exit_code == 1
    assert set(dg(store, "find", "status:OPEN", "--ids").stdout.split()) == {"D05", "D06"}
    assert dg(store, "find", "is:blocked", "--ids").stdout.split() == ["D06"]


def test_a_status_no_longer_matches_a_prefix_of_itself(store):
    """The point of the change: `status:BLOCK` is not a status."""
    assert dg(store, "find", "status:BLOCK").exit_code == 1


def test_the_status_chips_in_the_page_still_resolve(store, g):
    """`app.html` writes `status:` + `base(v.status)` into the query box, so
    every base status the page can offer has to match something. This is the
    coupling that a naive exactness change would have broken silently — the
    chips would have emptied the canvas with no error anywhere."""
    for status in {v.base_status for v in g.vertices.values()}:
        assert dg(store, "find", f"status:{status}").exit_code == 0, status


def test_an_area_matches_exactly(store):
    assert dg(store, "find", "area:Beta", "--ids").stdout.split() == [
        "D04", "D05", "D06"]
    assert dg(store, "find", "area:Bet").exit_code == 1


def test_the_prose_fields_still_match_substrings(store):
    """Exactness stops at the closed vocabularies. Exact-matching a sentence is
    never the question somebody has, so `note:` would have needed a regex every
    single time — and a default that is never right is not a default."""
    assert dg(store, "find", "note:nobody", "--ids").stdout.split() == ["D05"]
    assert dg(store, "find", "falsifier:corpus", "--ids").stdout.split() == ["D04"]


def test_every_exact_field_is_a_real_field(g, tg):
    """`EXACT` is a retyped list like `_UNSEARCHABLE`, so it gets the same
    bargain: verified against the lenses rather than trusted."""
    for lens in (query.decision_lens(g), query.task_lens(tg)):
        for name in query.EXACT:
            assert name in lens.fields, name


def test_a_negated_term_is_vacuously_true_where_the_field_is_absent(both):
    """Kept deliberately, so pinned deliberately.

    A task has no `falsifier`, so "this task's falsifier does not contain zzz"
    is true of all of them. Making it *false* instead — the tempting patch —
    would leave `x` and `-x` both false for the same record, which is not a
    negation any more.

    The oddity is the scoping rule rather than the logic: alone the term
    narrows to decisions and the vacuity never shows, and it surfaces only in
    an OR-group with a term the task store knows, because the group then keeps
    both lenses."""
    alone = dg(both, "find", "--ids", "--", "-falsifier:zzz").stdout.split()
    assert alone == ["D01", "D02", "D03", "D04", "D05", "D06"]

    grouped = dg(both, "find", "--ids", "--",
                 "is:outstanding or -falsifier:zzz").stdout.split()
    assert grouped == [*alone, "T01", "T02", "T03", "T04"]


def test_the_positive_form_of_an_absent_field_never_matches(both):
    """The asymmetry is only in the negated direction, which is the point: it
    is negation doing its job, not a hole in the field table."""
    got = dg(both, "find", "--ids", "is:outstanding or falsifier:zzz")
    assert got.stdout.split() == ["T02", "T03", "T04"]


# ---- the lens's per-vertex edge cache -------------------------------------
#
# `values` is called once per field per vertex and used to re-ask the graph for
# the active edge, the rival answers and the history every time — about twenty
# scans of the edge list per vertex to answer one search. The lens now fetches
# them once per vertex.
#
# The cache is safe because of *when* it dies, not because of what it holds:
# the lens is built per invocation and dropped with it. So what is pinned here
# is the lifetime — that a lens built after a change sees the change — rather
# than the speed.


def _lens(g):
    from dgraph import query
    return query.decision_lens(g)


def test_the_lens_cache_does_not_change_what_a_search_finds(g):
    from dgraph import query
    for q in ("answer:root", "is:terminal", "is:superseded", "older"):
        parsed = query.parse(q)
        first = query.select(parsed, _lens(g))
        second = query.select(parsed, _lens(g))
        assert first == second, q
    # ...and it still reaches the superseded edge, which is the archive that
    # `values` reads history for.
    assert query.select(query.parse("answer:older"), _lens(g)) == ["D01"]


def test_a_lens_built_after_a_change_sees_it(g):
    """The cache lives on the lens, so a new lens must not inherit a stale one.
    A cache that outlived its lens would make `dg find` answer from a store
    that no longer exists — and the tool's own rule is that nothing derived is
    kept."""
    from dgraph import query
    before = query.select(query.parse("answer:root"), _lens(g))
    assert before == ["D01"]
    g.active_edge("D01").answer = "A completely different answer."
    assert query.select(query.parse("answer:root"), _lens(g)) == []
    assert query.select(query.parse("answer:different"), _lens(g)) == ["D01"]


def test_one_lens_is_consistent_within_itself(g):
    """The other side of the same coin: within a single lens the cached reading
    is the reading, for every field, so two fields of one vertex cannot
    disagree about which edge is active."""
    lens = _lens(g)
    assert lens.values("D01", "answer") == _lens(g).values("D01", "answer")
    assert lens.label("D01", "answer", "The root answer.") == "answer"
    assert lens.label("D01", "answer", "An older answer.") == "superseded answer"


def test_a_predicate_fetches_only_the_lookup_it_needs(g):
    """The cache is three caches, not one tuple, and this is why.

    `active_edge` stops at the first match; `rival_answers` is a full scan with
    no early exit and `history` another pass. Fetching all three together made
    `is:terminal` — which wants the active edge alone — about five times
    *slower* than the uncached version it replaced, and a sweep caught it. A
    cache that fetches more than it is asked for is not a cache.
    """
    from dgraph import query
    calls = []
    g.rival_answers = lambda vid, _f=g.rival_answers: (calls.append(vid), _f(vid))[1]
    g.history = lambda vid, _f=g.history: (calls.append(vid), _f(vid))[1]
    lens = query.decision_lens(g)
    query.select(query.parse("is:terminal"), lens)
    assert not calls, f"is:terminal fetched what it does not read: {calls}"


def test_a_prose_search_still_reads_the_archive(g):
    """The other half — laziness must not become a narrowing. `answer:` reads
    superseded edges, and a lens too lazy to fetch history would quietly stop
    finding the reversals."""
    from dgraph import query
    assert query.select(query.parse("answer:older"), _lens(g)) == ["D01"]


# ---- the per-record predicates and structural terms -----------------------
#
# `is:orphaned` and both `waits:` terms asked a question of *every* record in
# the store, and each ask was a scan of the edge list. They now read an index,
# and `waits:` reads it forwards instead of asking every vertex whether it
# depends on the argument. These pin that the answers did not move.


def test_is_orphaned_finds_what_the_scanning_form_found(g):
    from dgraph import query
    from dgraph.model import Vertex
    scanning = {v for v in g.vertices
                if not g.depends(v) and not g.children(v)}
    got = set(query.select(query.parse("is:orphaned"), _lens(g)))
    assert got == scanning
    # ...and with something actually orphaned, so this is not two empty sets.
    g.vertices["D07"] = Vertex("D07", "Connected to nothing", "Beta", "OPEN")
    assert set(query.select(query.parse("is:orphaned"), _lens(g))) == {"D07"}


def test_waits_reads_forwards_and_finds_the_same_vertices(g):
    """`waits:D01` is "who rests on D01?". Asked of every vertex that is a scan
    each time; read from D01's own edges it is one lookup. It has to union
    *every* active edge, not follow the first: `depends` sees them all, so a
    rival answer's targets rest on D01 too."""
    from dgraph import query
    from dgraph.model import Edge
    for arg in list(g.vertices):
        scanning = {v for v in g.vertices if arg in g.depends(v)}
        got = set(query.select(query.parse(f"waits:{arg}"), _lens(g)))
        assert got == scanning, arg
    g.edges.append(Edge(src="D01", to=["D05"], active=True, answer="A rival."))
    assert "D05" in set(query.select(query.parse("waits:D01"), _lens(g)))
    assert set(query.select(query.parse("waits:D01"), _lens(g))) == \
        {v for v in g.vertices if "D01" in g.depends(v)}


# ---- D80: every offered field can match, or says what it does not reach ----


DEC80 = {"areas": ["Alpha"], "edges": [
    {"from": "D01", "to": [], "active": True, "answer": "HNSW it is",
     "falsifier": "recall drops", "source": "discussion", "date": "2026-01-01",
     "probe": {"kind": "prose.rule", "args": {"needle": "recall"}}}],
    "vertices": [
        {"id": "D01", "title": "Pick an index", "area": "Alpha",
         "status": "DECIDED", "rule": "settle by measurement",
         "binds": [{"kind": "rocq.constant", "ref": "Closure.closed"}]},
        {"id": "D02", "title": "Still open", "area": "Alpha", "status": "OPEN",
         "note": "nobody has decided this", "probes": [
             {"kind": "prose.rule", "args": {"needle": "hnsw"},
              "date": "2026-01-02"}]}],
    "superseded": None}

TSK80 = {"areas": ["Alpha"], "edges": [], "tasks": [
    {"id": "T01", "title": "Measure it", "area": "Alpha", "status": "DONE",
     "done": "2026-01-01", "outcome": "recall 0.94",
     "done_when": "recall over the sample",
     "binds": [{"kind": "rocq.constant", "ref": "Closure.closed"}],
     "probes": [{"kind": "prose.rule", "args": {"needle": "hnsw"},
                 "date": "2026-01-02"}],
     "note": "the sweep is scripted",
     "because": ["D01"], "evidence_for": "D02",
     "readings": [{"against": "D01", "note": "it holds",
                   "date": "2026-01-03"}]}]}

#: A value genuinely in the fixture above, for every field each lens offers.
NEEDLE = {
    "decisions": {"id": "D01", "title": "index", "area": "Alpha",
                  "status": "DECIDED", "note": "decided", "rule": "measurement",
                  "answer": "HNSW", "falsifier": "recall",
                  "source": "discussion", "date": "2026-01-01",
                  "probe": "prose.rule", "probes": "prose.rule",
                  "binds": "rocq.constant",
                  # No fixture value: a store this tool writes has no rival
                  # answer and no reversal, so `summary`, `why` and
                  # `from_source` have nothing to carry.
                  "summary": None, "why": None, "from_source": None},
    "tasks": {"id": "T01", "title": "Measure", "area": "Alpha",
              "status": "DONE", "outcome": "recall", "done": "2026-01-01",
              "done_when": "recall", "probes": "prose.rule",
              "binds": "rocq.constant", "readings": "D01",
              "note": "scripted", "because": "D01", "evidence_for": "D02",
              # `why` reads `stops`, and this fixture's task never stopped.
              "why": None},
}


@pytest.mark.parametrize("kind", ["decisions", "tasks"])
def test_every_offered_field_matches_or_says_what_it_does_not_reach(kind):
    """The axis nothing read, and the pair of the withheld-list guard above.

    `test_the_withheld_field_list_is_checked_against_the_dataclasses` verifies
    every **withheld** name still exists. Nothing verified that every
    **offered** name can match, and four walked through: `probe`, `probes`,
    `binds` and `readings` were advertised in the *unknown field* help, parsed,
    and were satisfied by nothing — for a record carrying the value. `Q-F12`
    through the front door `_UNSEARCHABLE`'s own comment warns about
    (`Y-F5`, `D80`).

    Three outcomes are allowed and no fourth: the term matches; or the field is
    in `PARTIAL`, which says what it indexes and what it leaves out; or it is
    in `_UNSEARCHABLE` and not offered at all. Silence is not one of them.
    """
    lens = (query.decision_lens(Graph.from_dict(copy.deepcopy(DEC80)))
            if kind == "decisions"
            else query.task_lens(TaskGraph.from_dict(copy.deepcopy(TSK80))))
    dead = []
    for name in lens.fields:
        assert name in NEEDLE[kind] or name in query.PARTIAL, \
            f"`{name}:` is offered and this test says nothing about it — give " \
            f"it a value the fixture carries, or put it in PARTIAL with what " \
            f"it indexes"
        needle = NEEDLE[kind].get(name)
        if needle is None:
            continue        # nothing in a tool-written store to match
        hit = any(needle.lower() in text.lower()
                  for rid in lens.ids for text in lens.values(rid, name))
        if not hit and query.partial_note(name) is None:
            dead.append(f"{name}:{needle}")
    assert not dead, ("offered, and silently matches nothing for a record that "
                      f"carries the value: {', '.join(dead)}")


def test_a_partial_field_says_what_it_leaves_out():
    """The middle outcome, which is the one `D80` added. `_no_predicate` makes
    this distinction for `is:` already — a term that cannot be answered is a
    different sentence from one that does not exist — and a term answered
    about *part* of a field is a third."""
    assert "args are the domain's" in query.partial_note("probes")
    assert "kind:ref" in query.partial_note("binds")
    assert query.partial_note("title") is None
    # Every partial name is a real field on one of the two lenses.
    from dgraph.model import Edge, Vertex
    from dgraph.tasks import Task
    known = set(query._fields_of(Vertex, Edge, Task))
    assert set(query.PARTIAL) <= known, set(query.PARTIAL) - known
