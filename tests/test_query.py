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

import json

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
    tg.tasks["T02"].because = "D05"        # premise still OPEN: gated
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


def test_a_query_round_trips_through_its_own_rendering():
    """`str(Query)` is what `--json` reports back as the query it ran, so a
    reader can tell what was actually asked."""
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
    """The `is:` table is the specification, and this is what stops it drifting
    from the code it claims to name. A predicate that raises here has lost the
    method it was standing in for."""
    for lens, ids in ((query.decision_lens(g), g.vertices),
                      (query.task_lens(tg), tg.tasks)):
        for name, pred in lens.predicates.items():
            for rid in ids:
                assert isinstance(pred(rid), bool), name


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
    predicate is absent rather than quietly meaning `TaskGraph.ready`."""
    r = dg(task_store, "find", "is:ready")
    assert r.exit_code == 2 and "is:ready" in r.stdout


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
