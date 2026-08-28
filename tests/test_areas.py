"""Areas as an accumulating vocabulary: registered by use, guarded by similarity.

The whitelist could not be added to. No op wrote the `areas` list — it was the
one field of either store that only `init` and `import` set — so a contribution
that introduced an area arrived with every record in it refused, and
`integrate.py` had to file that as `unexpressible`: reported, never fixable.
Meanwhile `dg areas` and `/api/areas` both said in as many words that the two
stores *share* their areas while the lists were independent fields, so three
commands reached a pair that disagreed:

    dg init --areas corpus,harness    # decisions.json gets three
    dg task init                      # tasks.json defaulted to General
    dg task add --area corpus         # unknown area. one of: General

So `areas` is a registry now, appended to by the op that first files a record
under a name, and every reader takes the union of both stores. This file is
about what that costs and what pays for it: membership was catching **typos**,
and what replaces it is a similarity check on a genuinely new area, with
`--new-area` as the override and `$DG_AREA=strict` as the launcher's rule.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from dgraph import areas, pending, project, render, task_render
from dgraph.agent_cli import app as agent_app
from dgraph.cli import app
from dgraph.model import Graph
from dgraph.tasks import TaskGraph
from tests.conftest import FIXTURE, TASK_FIXTURE

runner = CliRunner()


@pytest.fixture
def both(tmp_path, monkeypatch):
    (tmp_path / "decisions.json").write_text(json.dumps(FIXTURE, indent=2),
                                             encoding="utf-8")
    (tmp_path / "tasks.json").write_text(json.dumps(TASK_FIXTURE, indent=2),
                                         encoding="utf-8")
    monkeypatch.setattr(project, "_override", tmp_path)
    monkeypatch.setenv("COLUMNS", "300")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("DG_AGENT", raising=False)
    monkeypatch.delenv("DG_AREA", raising=False)
    return tmp_path


@pytest.fixture
def run(both):
    def go(*args):
        return runner.invoke(app, ["--project", str(both), *args])
    return go


# ---- the vocabulary declared at `init`, and why it is gone ----------------


def test_neither_init_declares_a_vocabulary(run, both):
    """The flag was the direct cause of the divergence it looked like it was
    preventing, and `init` is the moment a project knows least about itself —
    in a graph elaborated backwards from a sink, the areas are a *finding*."""
    fresh = both / "fresh"
    fresh.mkdir()
    r = runner.invoke(app, ["--project", str(fresh), "init", "--areas", "x"])
    assert r.exit_code == 2, "`--areas` still exists"

    assert runner.invoke(app, ["--project", str(fresh), "init"]).exit_code == 0
    assert runner.invoke(app, ["--project", str(fresh),
                               "task", "init"]).exit_code == 0
    assert Graph.load(fresh / "decisions.json").areas == []
    assert TaskGraph.load(fresh / "tasks.json").areas == []


def test_the_three_commands_that_used_to_diverge_now_agree(both):
    """The reproduction from the top of this file, run forward."""
    fresh = both / "fresh"
    fresh.mkdir()

    def go(*args):
        return runner.invoke(app, ["--project", str(fresh), *args])

    assert go("init").exit_code == 0
    assert go("task", "init").exit_code == 0
    assert go("task", "add", "--id", "T01", "-t", "seed",
              "--area", "corpus").exit_code == 0
    assert go("apply").exit_code == 0
    assert TaskGraph.load(fresh / "tasks.json").areas == ["corpus"]


# ---- registration ---------------------------------------------------------


def test_an_area_is_registered_by_the_op_that_first_uses_it(run, both):
    """Not a new op kind. `OPS` is unchanged, because registering an area is a
    side effect of filing a record under one rather than an act of its own —
    and an `add_area` op would be an act somebody could stage without ever
    filing anything, which is the declared-vocabulary problem again."""
    assert "add_area" not in pending.OPS

    assert run("add", "--id", "D07", "-t", "x", "--area", "Provenance").exit_code == 0
    assert run("apply").exit_code == 0

    g = Graph.load(both / "decisions.json")
    assert g.areas == ["Alpha", "Beta", "Provenance"]
    assert g.vertices["D07"].area == "Provenance"


def test_an_amend_registers_the_area_it_writes(run, both):
    """`set_fields` writes `area`, so it registers one too — otherwise the one
    verb that fixes a misfiled record would leave the store's own list wrong."""
    assert run("amend", "D05", "--area", "Provenance").exit_code == 0
    assert run("apply").exit_code == 0
    assert Graph.load(both / "decisions.json").areas[-1] == "Provenance"


def test_registration_touches_only_the_store_the_op_writes(run, both):
    """Read the union, write your own store.

    A composite op spanning both trays could land in one and fail in the other,
    leaving the two lists divergent *because of* the feature meant to unify
    them — and `dg apply`'s independence of the two batches predates this
    problem and is load-bearing.
    """
    assert run("add", "--id", "D07", "-t", "x", "--area", "Provenance").exit_code == 0
    assert run("apply").exit_code == 0

    assert "Provenance" in Graph.load(both / "decisions.json").areas
    assert "Provenance" not in TaskGraph.load(both / "tasks.json").areas


# ---- the guard that replaced membership -----------------------------------


def test_a_case_only_variant_is_refused_and_the_canonical_form_is_named(run):
    """An exact match after normalising is a typo with near-certainty, so the
    refusal names the form that already exists rather than describing a
    resemblance."""
    res = run("add", "--id", "D07", "-t", "x", "--area", "alpha")

    assert res.exit_code == 1
    assert "Alpha" in res.output and "new" in res.output
    assert "--new-area" in res.output


def test_separators_are_not_a_meaningful_difference(run):
    """`corpus-design`, `corpus_design` and `corpus design` are one area
    written three ways."""
    assert run("add", "--id", "D07", "-t", "x",
               "--area", "corpus design").exit_code == 0
    assert run("apply").exit_code == 0

    res = run("add", "--id", "D08", "-t", "x", "--area", "corpus_design")
    assert res.exit_code == 1 and "corpus design" in res.output


def test_new_area_says_the_resemblance_is_a_coincidence(run, both):
    res = run("add", "--id", "D07", "-t", "x", "--area", "alpha", "--new-area")

    assert res.exit_code == 0
    assert run("apply").exit_code == 0
    assert "alpha" in Graph.load(both / "decisions.json").areas


def test_an_area_that_resembles_nothing_in_use_is_silent(run):
    """The whole point of dropping the whitelist: a scout that discovers a new
    corner of a project can file under it."""
    assert run("add", "--id", "D07", "-t", "x",
               "--area", "Provenance").exit_code == 0


def test_an_amend_toward_an_existing_area_is_never_refused(run, both):
    """The guard fires only on an area new to the union, and this is why.

    An `amend` *toward* an existing area is the fix for a typo, not the
    mistake — a guard that refused `dg amend D05 --area Alpha` because it
    resembles the `alpha` somebody filed by accident would be backwards, and
    would make fragmentation permanent.
    """
    assert run("add", "--id", "D07", "-t", "x", "--area", "alpha",
               "--new-area").exit_code == 0
    assert run("apply").exit_code == 0

    assert run("amend", "D07", "--area", "Alpha").exit_code == 0


def test_the_union_is_what_is_read_not_one_stores_list(run, both):
    """An area known only to `tasks.json` suppresses the guard on a decision.
    Sharing the areas is the point, and divergence between the two files stops
    mattering the moment nothing validates membership."""
    assert run("task", "add", "--id", "T09", "-t", "x",
               "--area", "Provenance").exit_code == 0
    assert run("apply").exit_code == 0
    assert "Provenance" not in Graph.load(both / "decisions.json").areas

    # New to the decision store, known to its twin: silent, and not even a
    # resemblance to argue about.
    assert run("add", "--id", "D07", "-t", "x",
               "--area", "Provenance").exit_code == 0


def test_the_guard_is_the_same_rule_in_both_stores(run):
    """A rule applied in one store and not its twin is the shape most of this
    tool's audit findings took, which is why the guard is one function."""
    d = run("add", "--id", "D07", "-t", "x", "--area", "alpha")
    t = run("task", "add", "--id", "T09", "-t", "x", "--area", "alpha")

    assert d.exit_code == 1 and t.exit_code == 1
    assert "Alpha" in d.output and "Alpha" in t.output


# ---- `$DG_AREA` -----------------------------------------------------------


def test_strict_refuses_a_new_area_for_an_agent_and_not_for_a_person(run,
                                                                     monkeypatch):
    """Read exactly as `$DG_DECIDE` is: only for a caller with `$DG_AGENT` set,
    so a supervisor is never refused. Cooperative rather than a boundary — an
    agent could unset it, at which point it *is* the supervisor."""
    monkeypatch.setenv("DG_AREA", "strict")

    monkeypatch.setenv("DG_AGENT", "brisk-beacon")
    refused = run("add", "--id", "D07", "-t", "x", "--area", "Provenance")
    assert refused.exit_code == 1
    assert "DG_AREA=strict" in refused.output and "dg pending" in refused.output

    monkeypatch.delenv("DG_AGENT")
    assert run("add", "--id", "D07", "-t", "x",
               "--area", "Provenance").exit_code == 0


def test_new_area_does_not_override_the_launchers_rule(run, monkeypatch):
    """The flag answers "is this new area intentional?", which is the author's
    question. `$DG_AREA` answers "may an agent invent areas at all?", which is
    the launcher's — and a rule the thing it constrains can switch off is not a
    rule."""
    monkeypatch.setenv("DG_AREA", "strict")
    monkeypatch.setenv("DG_AGENT", "brisk-beacon")

    res = run("add", "--id", "D07", "-t", "x", "--area", "Provenance",
              "--new-area")
    assert res.exit_code == 1 and "DG_AREA=strict" in res.output


def test_strict_never_stops_an_area_already_in_use(run, monkeypatch):
    monkeypatch.setenv("DG_AREA", "strict")
    monkeypatch.setenv("DG_AGENT", "brisk-beacon")

    assert run("add", "--id", "D07", "-t", "x", "--area", "Alpha").exit_code == 0


def test_an_unreadable_area_policy_falls_open_and_is_reported(run, both,
                                                              monkeypatch):
    """It fails open like `$DG_DECIDE` and `$DG_WRITE`, because it is read on
    the path of every stage and a typo in a launcher must not make the tool
    unusable for the supervisor sharing the tray.

    That is **only** defensible because `dg-agent env --check` reports it, which
    is the whole argument of the environment page — and the reason this variable
    should not have shipped before it. Both halves are pinned here.
    """
    monkeypatch.setenv("DG_AREA", "strikt")
    monkeypatch.setenv("DG_AGENT", "brisk-beacon")

    assert run("add", "--id", "D07", "-t", "x",
               "--area", "Provenance").exit_code == 0, "it did not fail open"

    reported = runner.invoke(agent_app, ["--project", str(both),
                                         "env", "--check"])
    assert reported.exit_code == 1 and "strikt" in reported.output


# ---- rendering ------------------------------------------------------------


def test_a_registered_area_gets_a_section_in_the_view(run, both):
    assert run("add", "--id", "D07", "-t", "x", "--area", "Provenance").exit_code == 0
    assert run("apply").exit_code == 0

    out = render.render(Graph.load(both / "decisions.json"))
    assert "## Provenance" in out and "D07" in out


def test_a_record_whose_area_is_unlisted_still_renders(both):
    """The latent bug dropping the invariant opens, closed in the same pass.

    Both renderers iterated `for area in g.areas`, so a record filed under an
    area the list does not mention appeared in **no section at all** — silently,
    with nothing above the index to say a section was missing. `validate` used
    to make that unreachable; a hand-edited or imported store can reach it now.
    """
    raw = json.loads((both / "decisions.json").read_text(encoding="utf-8"))
    raw["areas"] = ["Alpha"]                       # Beta declared by nobody
    (both / "decisions.json").write_text(json.dumps(raw), encoding="utf-8")

    out = render.render(Graph.load(both / "decisions.json"))

    assert "## Beta" in out
    for vid in ("D04", "D05", "D06"):
        assert vid in out, f"{vid} renders in no section at all"


def test_the_task_view_closes_the_same_hole(both):
    raw = json.loads((both / "tasks.json").read_text(encoding="utf-8"))
    raw["areas"] = ["Alpha"]
    (both / "tasks.json").write_text(json.dumps(raw), encoding="utf-8")

    out = task_render.render(TaskGraph.load(both / "tasks.json"))

    assert "## Beta" in out and "T03" in out and "T04" in out


def test_an_unlisted_area_sorts_after_every_declared_one(both):
    """`(rank, name)` rather than a bare sentinel, so unlisted areas group
    instead of interleaving at whatever index they all shared."""
    order = areas.order(["Alpha", "Beta"])

    assert order("Alpha") < order("Beta") < order("Gamma") < order("Zeta")


def test_a_store_with_no_declared_areas_still_renders_every_record(both):
    raw = json.loads((both / "decisions.json").read_text(encoding="utf-8"))
    raw["areas"] = []
    (both / "decisions.json").write_text(json.dumps(raw), encoding="utf-8")

    out = render.render(Graph.load(both / "decisions.json"))
    assert "## Alpha" in out and "## Beta" in out


# ---- `dg check` no longer judges a label ----------------------------------


def test_no_graph_becomes_uncommittable_over_a_label(run, both):
    """Both removals, so nothing that used to pass now fails.

    The same reasoning `verbose_field` was given: a graph must not become
    uncommittable over a *label*, and membership was the one rule no op could
    satisfy.
    """
    for store in ("decisions.json", "tasks.json"):
        raw = json.loads((both / store).read_text(encoding="utf-8"))
        raw["areas"] = []
        (both / store).write_text(json.dumps(raw), encoding="utf-8")

    res = run("check")
    assert "area" not in res.output.lower()


# ---- `dg areas` -----------------------------------------------------------


def test_both_tables_list_every_area_either_store_knows(run):
    """What "the stores share their areas" has always claimed and did not used
    to mean. An area used only for work has a row on the decision side holding
    zero, and that zero is a real answer — it is where a question about that
    corner would go if anybody opened one."""
    assert run("task", "add", "--id", "T09", "-t", "x",
               "--area", "Provenance").exit_code == 0
    assert run("apply").exit_code == 0

    out = run("areas").output
    assert out.count("Provenance") == 2, out


def test_rename_stages_across_both_stores_as_two_batches(run, both):
    """One `set_fields` per affected record, staged like everything else.

    The two batches stay independent, which is not a compromise: `dg apply`
    keeps them apart so one that cannot apply can never stop one that can, and
    a rename that tried to be a single atomic act across both would give that up
    for the one command whose whole job is undoing a divergence.
    """
    res = run("areas", "rename", "Alpha", "corpus")

    assert res.exit_code == 0
    d = pending.load(both / ".dgraph-pending.json")
    t = pending.load(both / ".dgraph-task-pending.json")
    assert [op["vertex"] for op in d] == ["D01", "D02", "D03"]
    assert [op["task"] for op in t] == ["T01", "T02"]
    assert all(op["area"] == "corpus" for op in d + t)

    assert run("apply").exit_code == 0
    assert Graph.load(both / "decisions.json").vertices["D01"].area == "corpus"
    assert TaskGraph.load(both / "tasks.json").tasks["T01"].area == "corpus"


def test_rename_leaves_the_old_name_in_the_registry(run, both):
    """Pruning it would silently move render order — a reused area would come
    back at the end rather than in place — and a zero row says more than a row
    that vanished. It is the person's call, so `dg areas prune` is the verb."""
    assert run("areas", "rename", "Alpha", "corpus").exit_code == 0
    assert run("apply").exit_code == 0

    g = Graph.load(both / "decisions.json")
    assert "Alpha" in g.areas and "corpus" in g.areas
    assert not [v for v in g.vertices.values() if v.area == "Alpha"]


def test_rename_into_an_existing_area_is_the_merge_case(run, both):
    assert run("areas", "rename", "Alpha", "Beta").exit_code == 0
    assert run("apply").exit_code == 0

    g = Graph.load(both / "decisions.json")
    assert {v.area for v in g.vertices.values()} == {"Beta"}


def test_rename_says_so_when_nothing_is_filed_under_the_name(run, both):
    res = run("areas", "rename", "Nothing", "Something")

    assert res.exit_code == 0 and "nothing filed under" in res.output
    assert pending.load(both / ".dgraph-pending.json") == []


def test_prune_releases_only_areas_holding_nothing(run, both):
    """Deliberate, never automatic — the same reading `dg-agent prune` has,
    which releases only names holding nothing and tells you what it declined.

    Per store, because that is where an area is registered: a rename empties
    the old name in whichever stores held records under it, and an area still
    holding one anywhere keeps its row there.
    """
    assert run("areas", "rename", "Alpha", "Beta").exit_code == 0
    assert run("apply").exit_code == 0

    res = run("areas", "prune")

    assert res.exit_code == 0
    assert "Alpha (decisions)" in res.output and "Alpha (tasks)" in res.output
    assert Graph.load(both / "decisions.json").areas == ["Beta"]
    assert TaskGraph.load(both / "tasks.json").areas == ["Beta"], (
        "Beta still holds every record and must be kept")


def test_prune_says_nothing_to_do_rather_than_writing(run, both):
    before = (both / "decisions.json").read_text(encoding="utf-8")
    res = run("areas", "prune")

    assert res.exit_code == 0 and "nothing to prune" in res.output
    assert (both / "decisions.json").read_text(encoding="utf-8") == before


# ---- the primitives -------------------------------------------------------


def test_normalisation_takes_out_only_what_is_never_meaningful():
    assert areas.normal(" Corpus Design ") == "corpus-design"
    assert areas.normal("corpus_design") == areas.normal("corpus-design")
    assert areas.normal("corpus") != areas.normal("corpora")


def test_a_project_with_one_store_reads_a_union_of_one(tmp_path, monkeypatch):
    """`server.areas_payload` is careful to allow a project with only one
    store, and so is the guard: the twin it cannot find is an empty registry
    rather than an error."""
    monkeypatch.setattr(project, "_override", tmp_path)
    assert areas.stored_counts(tmp_path / "tasks.json") == {}
    assert pending.refuse_area("Anything", own={}, other={}, owner=None) is None
