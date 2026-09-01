"""Who staged an op, and who may apply it.

`C-F16` — one tray, no owner, `dg apply` applies everything in it — was decided
*not supported* and reopened once a two-agent workflow existed. It is answered
narrower than it was priced: the tray stays **one file** and gains an owner per
op. What was silent is *who may apply what*, and that is all this changes.

Three configurations, and the whole design is that the first one is not a code
path at all:

    no $DG_AGENT, nothing owned   → applies everything. Today, byte for byte.
    $DG_AGENT=a,  mixed tray      → applies a's; says what it left and whose.
    no $DG_AGENT, some owned      → refuses, names the owners, offers the flags.

The third ships **with** the stamp rather than as a default to flip later.
Landing the stamp with "apply everything" and changing the default afterwards
would be a silent behaviour change — somebody's `dg apply` quietly leaving work
behind — which is the failure the writers pass exists to close. It is
unreachable unless somebody deliberately set an identity, so no single-writer
project can meet it.

**Why not per-agent trays.** They relocate conflicts from stage time to apply
time: `pending.preview` is what every stage-time guard consults, and own-tray
visibility blinds it. Today the second agent to answer a settled question is
refused with nothing typed; with its own tray it writes an answer, a source and
a falsifier first and is refused after, where the only move left is a `reopen`
that files a reversal nobody made. `test_a_second_answer_is_still_refused_at_
stage_time` pins the refusal this design keeps.
"""

from __future__ import annotations

import json
import subprocess
import threading

import pytest
from typer.testing import CliRunner

from dgraph import brief, gate, pending, project, server, task_pending
from dgraph.cli import app
from dgraph.model import Graph
from dgraph.tasks import TaskGraph
from tests.conftest import FIXTURE, TASK_FIXTURE

runner = CliRunner()

D07 = {"op": "add_vertex", "id": "D07", "title": "seven", "area": "Alpha",
       "status": "OPEN"}
D08 = {"op": "add_vertex", "id": "D08", "title": "eight", "area": "Alpha",
       "status": "OPEN"}
T07 = {"op": "add_task", "id": "T07", "title": "seven", "area": "Alpha",
       "status": "TODO"}
T08 = {"op": "add_task", "id": "T08", "title": "eight", "area": "Alpha",
       "status": "TODO"}


@pytest.fixture
def proj(tmp_path, monkeypatch):
    """A project with both stores, committed, and **no identity set**.

    `delenv` rather than trusting the ambient environment: every test here says
    what identity it runs under, and one inherited from the developer's shell
    would make the single-writer cases pass or fail by accident.
    """
    (tmp_path / "decisions.json").write_text(json.dumps(FIXTURE, indent=2),
                                             encoding="utf-8")
    (tmp_path / "tasks.json").write_text(json.dumps(TASK_FIXTURE, indent=2),
                                         encoding="utf-8")
    monkeypatch.setattr(project, "_override", tmp_path)
    monkeypatch.delenv("DG_AGENT", raising=False)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERM", "dumb")
    for argv in (["git", "init", "-q", "."], ["git", "add", "-A"],
                 ["git", "-c", "user.email=a@b", "-c", "user.name=a",
                  "commit", "-qm", "fixture"]):
        subprocess.run(argv, cwd=tmp_path, capture_output=True)
    subprocess.run(["dg", "--project", str(tmp_path), "render"],
                   capture_output=True)
    subprocess.run(["dg", "--project", str(tmp_path), "task", "render"],
                   capture_output=True)
    return project.Project(tmp_path)


@pytest.fixture
def srv(proj):
    """The real server on an ephemeral port, sharing this process's project.

    In-process deliberately: the environment is therefore *the same one* the
    terminal tests run under, which is exactly the condition
    `test_the_browser_stages_as_nobody` has to hold under. A subprocess with a
    scrubbed environment would prove nothing about inheritance.
    """
    from http.server import ThreadingHTTPServer

    s = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=s.serve_forever, daemon=True,
                     kwargs={"poll_interval": 0.01}).start()
    yield f"http://127.0.0.1:{s.server_port}"
    s.shutdown()
    s.server_close()


def stage_via_browser(base, op, agent=None):
    """Stage one op the way the page does: `POST /api/pending`, with this run's
    token and — like the page — **no** agent header unless a caller adds one.

    Its own request rather than `test_server.jreq` because the header under test
    is the one that helper cannot send, and the point of these two tests is what
    happens when nobody sends it.
    """
    import urllib.request

    body = json.dumps(op).encode()
    r = urllib.request.Request(base + "/api/pending", data=body, method="POST")
    r.add_header(server.TOKEN_HEADER, server.TOKEN)
    r.add_header("Content-Type", "application/json")
    if agent:
        r.add_header(server.AGENT_HEADER, agent)
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read() or b"{}")


@pytest.fixture
def run(proj):
    def go(*args):
        return runner.invoke(app, ["--project", str(proj.root), *args])
    return go


def as_agent(monkeypatch, name: str | None):
    """Run the rest of this test as `name`, or as nobody."""
    if name is None:
        monkeypatch.delenv("DG_AGENT", raising=False)
    else:
        monkeypatch.setenv("DG_AGENT", name)


def owners(path):
    return [o.get("by") for o in pending.load(path)]


def ids(path):
    return [v for v in Graph.load(path).vertices]


# ---- the stamp -----------------------------------------------------------


@pytest.mark.parametrize("door", ["cli", "stage_all", "adopt"])
def test_every_staging_door_records_who_staged_it(proj, run, monkeypatch, door):
    """Parametrised over the doors so a new one joins by being added here.

    `adopt` is the interesting one and it needs no code of its own: adoption
    stages through `pending.stage_all` like everything else, so an arriving op
    is stamped with the **adopter** — the person whose clone is taking it on —
    rather than with whoever wrote it elsewhere. That is the right attribution
    and it falls out of putting the stamp at staging.
    """
    as_agent(monkeypatch, "a")
    if door == "cli":
        run("add", "--id", "D07", "--title", "seven", "--area", "Alpha")
    elif door == "stage_all":
        pending.stage_all([D07], proj.pending)
    else:
        pending.stage_all([{**D07, "iref": "d0"}], proj.pending)

    assert owners(proj.pending) == ["a"]


def test_no_identity_leaves_the_op_exactly_as_it_was(proj, monkeypatch):
    """Unowned means **no key**, not `"by": null`.

    The single-writer tray has to stay byte-identical, and a null field in every
    op of every existing project would be a schema change dressed as a default.
    """
    as_agent(monkeypatch, None)
    pending.stage_all([D07], proj.pending)

    op, = pending.load(proj.pending)
    assert "by" not in op, op


def test_the_stamp_survives_an_edit_and_an_apply(proj, monkeypatch):
    """**The one that fails silently.**

    `pending.discard` takes applied ops out of the tray *by value*
    (`remaining.remove(op)`). A stamp rewritten between staging and applying
    makes that match nothing, so the applied op stays staged and the next
    `dg apply` re-applies it — refused loudly for a `close`, silent for
    anything idempotent. Set once in `_with_refs` and never touched again.
    """
    as_agent(monkeypatch, "a")
    pending.stage_all([D07], proj.pending)
    ref = pending.load(proj.pending)[0]["ref"]

    pending.replace(ref, {**D07, "title": "seven, revised"}, proj.pending)
    assert owners(proj.pending) == ["a"], "the edit dropped or changed the stamp"

    from dgraph import applying
    with applying.trays(proj), applying.writing(proj):
        applying.apply_decisions(pending.load(proj.pending))
    assert pending.load(proj.pending) == [], (
        "discard could not find the op it had just applied — the stamp moved")


def test_a_stamped_op_passes_the_stage_time_guard(proj, monkeypatch):
    """`pending.vet_fields` refuses any key it does not know, so `by` has to be
    named in the allowlist beside `ref` and `saw` or every stamped op is
    rejected at the door it was staged through."""
    as_agent(monkeypatch, "a")
    g = Graph.load(proj.store)
    pending.vet_all(pending.preview(g), pending.stage_all([D07], proj.pending))


# ---- the three configurations --------------------------------------------


def test_an_unowned_tray_applies_whole_for_an_unowned_caller(proj, run,
                                                             monkeypatch):
    """Configuration one: today's path, and the assertion is on the store and
    the tray rather than on the exit code, because the claim is *identical*."""
    as_agent(monkeypatch, None)
    pending.stage_all([D07, D08], proj.pending)
    pending.stage_all([T07, T08], task_pending.path())

    res = run("apply")

    assert res.exit_code == 0, res.output
    assert {"D07", "D08"} <= set(ids(proj.store))
    assert pending.load(proj.pending) == []
    assert pending.load(task_pending.path()) == []


@pytest.mark.parametrize("tray,first,second,store,left",
                         [("decisions", D07, D08, "store", "D08"),
                          ("tasks", T07, T08, "tasks", "T08")])
def test_an_owned_caller_applies_its_own_and_leaves_the_rest(
        proj, run, monkeypatch, tray, first, second, store, left):
    """Configuration two, on **both** trays — two files, and `C-F28` is the
    precedent for a rule carried to one store and not the other.

    What is left must still be **staged**, not dropped: the other agent's work
    is not this caller's to discard.
    """
    path = proj.pending if tray == "decisions" else task_pending.path()
    as_agent(monkeypatch, "a")
    pending.stage_all([first], path)
    as_agent(monkeypatch, "b")
    pending.stage_all([second], path)

    as_agent(monkeypatch, "a")
    res = run("apply")

    assert res.exit_code == 0, res.output
    assert owners(path) == ["b"], "a's apply did not leave b's op staged"
    assert "b" in res.output, f"the report did not say whose work it left:\n{res.output}"
    holds = (ids(proj.store) if store == "store"
             else list(TaskGraph.load(proj.tasks).tasks))
    assert left not in holds


def test_an_unowned_caller_refuses_a_tray_holding_somebody_s_work(proj, run,
                                                                  monkeypatch):
    """Configuration three, and the one that must not be deferred.

    Nothing written, nonzero, and the message has to name the owners and both
    flags — a refusal a reader cannot act on is the shape `C-F17` is about.
    """
    as_agent(monkeypatch, "a")
    pending.stage_all([D07], proj.pending)
    as_agent(monkeypatch, None)

    res = run("apply")

    assert res.exit_code != 0
    assert "D07" not in ids(proj.store), "the refusal wrote anyway"
    assert owners(proj.pending) == ["a"]
    for expected in ("a", "--all", "--mine"):
        assert expected in res.output, f"{expected!r} missing:\n{res.output}"


@pytest.mark.parametrize("who", ["a", None])
@pytest.mark.parametrize("flag,lands", [("--all", True), ("--mine", False)])
def test_the_flags_do_what_they_say_from_either_caller(proj, run, monkeypatch,
                                                       who, flag, lands):
    """`--all` applies the whole tray whoever asks; `--mine` applies only the
    caller's, and for an unowned caller that is the unowned ops — here, none."""
    as_agent(monkeypatch, "b")
    pending.stage_all([D08], proj.pending)
    as_agent(monkeypatch, who)

    run("apply", flag)

    assert ("D08" in ids(proj.store)) is lands


# ---- single-writer equivalence -------------------------------------------


def test_a_single_writer_never_meets_the_refusal(proj, run, monkeypatch):
    """The whole design rests on this: with no identity anywhere, the tray can
    never hold an owned op, so configuration three is unreachable."""
    as_agent(monkeypatch, None)
    for op in (D07, D08):
        pending.stage_all([op], proj.pending)

    assert owners(proj.pending) == [None, None]
    assert run("apply").exit_code == 0
    assert pending.load(proj.pending) == []


def test_the_staged_warning_still_counts_everyone_s(proj, monkeypatch):
    """The property per-agent trays would have cost.

    `dg brief`'s "work is staged and about to be lost" is the safety net for an
    agent that died mid-batch, and `C-F16` noted that per-agent trays would make
    it "quieter exactly as more work is at risk". One file keeps the count
    whole.
    """
    as_agent(monkeypatch, "a")
    pending.stage_all([D07], proj.pending)
    as_agent(monkeypatch, "b")
    pending.stage_all([D08], proj.pending)
    as_agent(monkeypatch, None)

    assert brief.data(proj)["staged"] == 2


@pytest.mark.parametrize("stamped", [True, False])
def test_the_commit_gate_is_blind_to_ownership(proj, monkeypatch, stamped):
    """Ownership is about who may apply, not about what is committable. The
    gate reads one tray and counts everything, before and after."""
    as_agent(monkeypatch, "a" if stamped else None)
    pending.stage_all([D07], proj.pending)
    as_agent(monkeypatch, None)

    assert gate.verdict("git commit -m x", proj)["verdict"] in ("ask", "warn")


# ---- the identity hazards ------------------------------------------------


def test_the_browser_stages_as_nobody(proj, srv, monkeypatch):
    """`dg serve --detach` is a `subprocess.Popen` and inherits the environment,
    so an agent that set `$DG_AGENT` and launched the server would hand its
    identity to every person who later clicks in that browser — and their
    terminal `dg apply` would then silently skip what they staged.

    The browser is a person's door and a person is the supervisor, so it stages
    unowned. Driven through the HTTP route rather than by reading a variable:
    `B-F1` is three controls that were drawn, bound to nothing, and green.
    """
    as_agent(monkeypatch, "a")
    stage_via_browser(srv, D07)

    assert owners(proj.pending) == [None], (
        "the browser inherited the identity of whoever started the server")


def test_one_person_two_doors_is_one_writer(proj, srv, run, monkeypatch):
    """The regression this must never cause.

    `commands/serve.md` tells a user to work in the browser and a terminal at
    once. Both are unowned, so what is staged in one applies from the other.
    """
    as_agent(monkeypatch, None)
    stage_via_browser(srv, D07)
    run("add", "--id", "D08", "--title", "eight", "--area", "Alpha")

    assert run("apply").exit_code == 0
    assert {"D07", "D08"} <= set(ids(proj.store))


def test_a_second_answer_is_still_refused_at_stage_time(proj, run, monkeypatch):
    """What per-agent trays would have cost, kept.

    `preview` sees the whole tray, so the second agent to answer a settled
    question is refused **before writing an answer, a source and a falsifier**.
    Split the tray and this refusal moves to apply time, where the only move
    left is a `reopen` that files a reversal nobody made.
    """
    as_agent(monkeypatch, "a")
    run("decide", "D05", "--answer", "HNSW", "--source", "a.md",
        "--falsifier", "x", "--no-edit")
    as_agent(monkeypatch, "b")

    res = run("decide", "D05", "--answer", "IVF-PQ", "--source", "b.md",
              "--falsifier", "y", "--no-edit")

    assert res.exit_code != 0
    assert "already staged" in res.output, res.output
    closes = [o for o in pending.load(proj.pending) if o["op"] == "close"]
    assert [o["answer"] for o in closes] == ["HNSW"], (
        "the second answer reached the tray, so the refusal did not hold")


def test_an_unreadable_tray_still_does_not_stop_the_other_batch(proj, run,
                                                                monkeypatch):
    """`C-F22`, re-checked against the scoping that came after it.

    `apply` promises that "a task batch that will not apply can never stop a
    decision batch that would", and a tray nobody can parse is `None` rather
    than empty all the way through. The first version of `_scope` treated it as
    a list, and an unparseable task tray took the decision batch down again —
    the same finding, four years of comments later, reintroduced by a feature
    that had nothing to do with it.
    """
    as_agent(monkeypatch, "a")
    pending.stage_all([D07], proj.pending)
    (proj.root / ".dgraph-task-pending.json").write_text("{oh no")

    res = run("apply")

    assert res.exit_code == 1
    assert ".dgraph-task-pending.json could not be read" in res.output
    assert "D07" in ids(proj.store), (
        "an unreadable task tray stopped the decision batch again")


def test_the_skill_tells_the_truth_about_sharing_a_clone(proj, run,
                                                         monkeypatch):
    """`H-F2`: the skill is the only prose here written for a machine to act on,
    and its three anti-drift tests check whether the commands it names *exist*,
    never whether what it says about them is true.

    So each claim is run. The section is the premise and the store is the
    evidence; move the behaviour and this fails where the claim is written.
    """
    import pathlib as _pl

    skill = (_pl.Path(__file__).resolve().parents[1] / "skills" / "dear-guide"
             / "SKILL.md").read_text(encoding="utf-8")
    section = skill.split("## Sharing a clone", 1)[1].split("\n## ", 1)[0]

    assert pending.AGENT_ENV in section, "the skill does not name the variable"

    # "dg apply writes your ops and leaves everyone else's staged"
    as_agent(monkeypatch, "b")
    pending.stage_all([D08], proj.pending)
    as_agent(monkeypatch, "a")
    pending.stage_all([D07], proj.pending)
    assert run("apply").exit_code == 0
    assert owners(proj.pending) == ["b"] and "D08" not in ids(proj.store)

    # "with no $DG_AGENT ... refuses if it holds work somebody else staged"
    as_agent(monkeypatch, None)
    assert run("apply").exit_code != 0

    # ...and both escapes the section offers really are escapes
    for flag, lands in (("--mine", False), ("--all", True)):
        assert f"`dg apply {flag}`" in section, f"{flag} is not offered"
    assert run("apply", "--all").exit_code == 0
    assert "D08" in ids(proj.store)


# ---- the roster ----------------------------------------------------------
#
# `--agent` is `--mine` with the identity supplied instead of inherited, and
# the roster is the list a reader picks the name from. They ship together
# because either alone is a trap: a filter with no roster is a flag whose only
# valid arguments are undiscoverable, and a roster with no filter names writers
# a reader cannot then act on. The workflow both exist for is several agents
# proposing *alternatives* into one tray, where the supervisor means to write
# one and turn the rest down. Complementary proposals want the union, and
# `--all` was always that.


def test_the_roster_spans_both_trays_and_puts_unowned_last(proj, monkeypatch):
    """A writer with no decision ops and five task ops is present in the review.

    The trays are staged apart and applied as a pair, so a per-tray roster is
    the reading that would call that writer absent — and `unowned` sorts last
    because it is the one entry that is not a name.
    """
    as_agent(monkeypatch, "b")
    pending.stage_all([D07], proj.pending)
    as_agent(monkeypatch, "a")
    pending.stage_all([T07, T08], task_pending.path())
    as_agent(monkeypatch, None)
    pending.stage_all([D08], proj.pending)

    r = pending.roster(pending.load(proj.pending),
                       pending.load(task_pending.path()))

    assert list(r.items()) == [("a", 2), ("b", 1), ("unowned", 1)]


def test_unowned_is_a_label_and_never_a_stamp(proj, monkeypatch):
    """The word the refusal prints has to be the word the filter takes back.

    An unowned op has no `by` key at all, so a filter that passed the label
    straight to `mine` would match nothing and then report an empty selection
    as a legitimate one.
    """
    assert pending.addressed(pending.UNOWNED) is None
    assert pending.addressed("a") == "a"

    as_agent(monkeypatch, None)
    pending.stage_all([D07], proj.pending)
    as_agent(monkeypatch, "a")
    pending.stage_all([D08], proj.pending)

    keep, rest = pending.mine(pending.load(proj.pending),
                             pending.addressed(pending.UNOWNED))
    assert [o["id"] for o in keep] == ["D07"]
    assert [o["id"] for o in rest] == ["D08"]


def test_a_single_writer_is_never_shown_a_roster(proj, run, monkeypatch):
    """The equivalence a project that sets no identity is owed.

    Same rule as the owner column in `_tray_listing`: a line reading
    "staged by  unowned 2" is noise dressed as information, and it would appear
    in every project that will never set an identity.
    """
    as_agent(monkeypatch, None)
    pending.stage_all([D07, D08], proj.pending)

    out = run("pending").output

    assert "staged by" not in out, out


def test_the_roster_under_a_listing_counts_the_other_tray_too(proj, run,
                                                              monkeypatch):
    """`dg pending` shows one tray and reports both.

    A reader who saw only the decision counts would conclude that an agent
    holding nothing but task ops had gone home, and then apply without them.
    """
    as_agent(monkeypatch, "a")
    pending.stage_all([D07], proj.pending)
    as_agent(monkeypatch, "b")
    pending.stage_all([T07, T08], task_pending.path())

    out = run("pending").output

    assert "staged by" in out
    assert "b 2" in out, f"the other tray was not counted:\n{out}"


# ---- narrowing a reading -------------------------------------------------


@pytest.mark.parametrize("cmd,tray,first,second",
                         [(("pending",), "decisions", D07, D08),
                          (("task", "pending"), "tasks", T07, T08)])
def test_agent_narrows_the_listing_to_one_writer(proj, run, monkeypatch,
                                                 cmd, tray, first, second):
    """Both trays, through the one renderer they share."""
    path = proj.pending if tray == "decisions" else task_pending.path()
    as_agent(monkeypatch, "a")
    pending.stage_all([first], path)
    as_agent(monkeypatch, "b")
    pending.stage_all([second], path)
    as_agent(monkeypatch, None)

    out = run(*cmd, "--agent", "a").output

    assert "seven" in out
    assert "eight" not in out, f"b's op survived the narrowing:\n{out}"
    assert "showing" in out and "a" in out


def test_a_mistyped_name_shows_the_roster_rather_than_an_empty_tray(
        proj, run, monkeypatch):
    """**The one that fails silently.**

    An unknown name matches nothing, and an empty listing reads exactly like an
    empty tray — so the reader concludes there is nothing staged when there are
    two ops they were about to review. The roster prints whether or not the
    name matched, which is the whole reason it prints at all.
    """
    as_agent(monkeypatch, "a")
    pending.stage_all([D07, D08], proj.pending)
    as_agent(monkeypatch, None)

    out = run("pending", "--agent", "agnet").output

    assert "nothing staged by" in out
    assert "a 2" in out, f"the roster did not say who really staged them:\n{out}"


# ---- applying for one writer ---------------------------------------------


def test_agent_writes_one_writer_s_ops_and_leaves_the_rest_staged(
        proj, run, monkeypatch):
    """The accept half of the review, on both trays at once.

    What is left must still be **staged**: taking one proposal is not a verdict
    on the others, and `pending.discard` removes applied ops by value, so the
    rest survive with no extra care taken.
    """
    as_agent(monkeypatch, "a")
    pending.stage_all([D07], proj.pending)
    pending.stage_all([T07], task_pending.path())
    as_agent(monkeypatch, "b")
    pending.stage_all([D08], proj.pending)
    as_agent(monkeypatch, None)

    res = run("apply", "--agent", "a")

    assert res.exit_code == 0, res.output
    assert "D07" in ids(proj.store) and "D08" not in ids(proj.store)
    assert "T07" in TaskGraph.load(proj.tasks).tasks
    assert owners(proj.pending) == ["b"], "b's op was not left staged"


def test_an_agent_may_not_apply_another_agent_s_drafts(proj, run, monkeypatch):
    """`C-F16`, and naming the victim in a flag does not make it a different act.

    A draft `close` applied by somebody else is a DECIDED answer whose only
    exit is a `reopen`, filing a reversal nobody made. The supervisor is
    usually right to write for an agent; one agent reaching into another's
    drafts is the failure the stamp exists to stop.
    """
    as_agent(monkeypatch, "b")
    pending.stage_all([D08], proj.pending)
    as_agent(monkeypatch, "a")

    res = run("apply", "--agent", "b")

    assert res.exit_code != 0
    assert "D08" not in ids(proj.store), "the refusal wrote anyway"
    assert owners(proj.pending) == ["b"]
    assert "not yours" in res.output, res.output


def test_naming_yourself_is_allowed(proj, run, monkeypatch):
    """Refusing `--agent a` to `a` would be refusing `--mine` spelled out."""
    as_agent(monkeypatch, "a")
    pending.stage_all([D07], proj.pending)

    assert run("apply", "--agent", "a").exit_code == 0
    assert "D07" in ids(proj.store)


def test_an_unknown_name_is_refused_with_the_roster(proj, run, monkeypatch):
    """A typo must not read as "there was nothing of theirs".

    The two are indistinguishable in the tray afterwards and mean opposite
    things, and only one of them is a reason to look again.
    """
    as_agent(monkeypatch, "a")
    pending.stage_all([D07], proj.pending)
    as_agent(monkeypatch, None)

    res = run("apply", "--agent", "agnet")

    assert res.exit_code != 0
    assert "D07" not in ids(proj.store)
    assert owners(proj.pending) == ["a"]
    assert "a 1" in res.output, f"the roster was not offered:\n{res.output}"


@pytest.mark.parametrize("flags", [("--all", "--agent", "a"),
                                   ("--mine", "--agent", "a"),
                                   ("--all", "--mine")])
def test_two_scopes_at_once_is_refused_before_anything_is_read(
        proj, run, monkeypatch, flags):
    as_agent(monkeypatch, "a")
    pending.stage_all([D07], proj.pending)
    as_agent(monkeypatch, None)

    res = run("apply", *flags)

    assert res.exit_code == 2, res.output
    assert "D07" not in ids(proj.store)


def test_the_refusal_offers_the_flag_that_answers_it(proj, run, monkeypatch):
    """The refusal names the owners; the flag that acts on one of them belongs
    beside them. A refusal a reader cannot act on is the shape `C-F17` is
    about."""
    as_agent(monkeypatch, "a")
    pending.stage_all([D07], proj.pending)
    as_agent(monkeypatch, None)

    out = run("apply").output

    for expected in ("--all", "--mine", "--agent"):
        assert expected in out, f"{expected!r} missing:\n{out}"


# ---- rejecting one writer ------------------------------------------------


@pytest.mark.parametrize("cmd,tray,first,second",
                         [(("clear",), "decisions", D07, D08),
                          (("task", "clear"), "tasks", T07, T08)])
def test_clear_agent_discards_one_proposal_and_keeps_the_others(
        proj, run, monkeypatch, cmd, tray, first, second):
    """The reject verb. A bare `dg clear` takes the whole file whoever runs it,
    which is right for the single writer it was written for and blunt once four
    agents stage into one tray."""
    path = proj.pending if tray == "decisions" else task_pending.path()
    as_agent(monkeypatch, "a")
    pending.stage_all([first], path)
    as_agent(monkeypatch, "b")
    pending.stage_all([second], path)
    as_agent(monkeypatch, None)

    res = run(*cmd, "--agent", "a")

    assert res.exit_code == 0, res.output
    assert owners(path) == ["b"]


def test_clearing_a_name_nobody_staged_under_is_a_failure(proj, run,
                                                          monkeypatch):
    """Not a clear of zero ops. The two are indistinguishable in the tray
    afterwards, and only one of them means the ops you meant to reject are
    still sitting there."""
    as_agent(monkeypatch, "a")
    pending.stage_all([D07], proj.pending)
    as_agent(monkeypatch, None)

    res = run("clear", "--agent", "agnet")

    assert res.exit_code != 0
    assert owners(proj.pending) == ["a"], "the ops went anyway"
    assert "a 1" in res.output


def test_a_bare_clear_is_untouched(proj, run, monkeypatch):
    """The existing verb keeps its existing meaning. Narrowing it by default
    would be a silent behaviour change, which is the failure this whole feature
    is written around."""
    as_agent(monkeypatch, "a")
    pending.stage_all([D07], proj.pending)
    as_agent(monkeypatch, "b")
    pending.stage_all([D08], proj.pending)
    as_agent(monkeypatch, None)

    assert run("clear").exit_code == 0
    assert pending.load(proj.pending) == []


# ---- the other door ------------------------------------------------------
#
# The browser is where reviewing several proposals actually happens — the CLI
# lists a tray, the page draws the graph beside it — so the two halves have to
# be reachable from both. A screen that can reject one writer's proposal but
# can only accept *every* writer's is one that walks its user to "apply
# everything" and then to a reversal.


def jreq(base, path, method="GET", body=None):
    """One authed request, decoded. `tests/test_server.py`'s helper, inlined
    rather than imported so this module keeps owning its own fixtures."""
    import urllib.error
    import urllib.request

    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data, method=method)
    r.add_header(server.TOKEN_HEADER, server.TOKEN)
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_the_browser_clears_one_writer_s_ops(proj, srv, monkeypatch):
    as_agent(monkeypatch, "a")
    pending.stage_all([D07], proj.pending)
    as_agent(monkeypatch, "b")
    pending.stage_all([D08], proj.pending)
    as_agent(monkeypatch, None)

    code, out = jreq(srv, "/api/pending?agent=a", "DELETE")

    assert code == 200 and out["cleared"] == 1
    assert owners(proj.pending) == ["b"]


def test_an_empty_agent_never_widens_into_a_clear_of_everything(proj, srv,
                                                                monkeypatch):
    """`parse_qs` drops a blank value, so `?agent=` parses to nothing and a
    narrowed clear would silently become a clear of the whole tray — a
    destructive widening produced by a page bug, which is the one direction
    this route must not fail in."""
    as_agent(monkeypatch, "a")
    pending.stage_all([D07, D08], proj.pending)
    as_agent(monkeypatch, None)

    code, out = jreq(srv, "/api/pending?agent=", "DELETE")

    assert code == 400, out
    assert len(pending.load(proj.pending)) == 2, "the tray was cleared anyway"


def test_the_browser_applies_one_writer_s_ops(proj, srv, monkeypatch):
    as_agent(monkeypatch, "a")
    pending.stage_all([D07], proj.pending)
    pending.stage_all([T07], task_pending.path())
    as_agent(monkeypatch, "b")
    pending.stage_all([D08], proj.pending)
    as_agent(monkeypatch, None)

    code, out = jreq(srv, "/api/apply", "POST", {"agent": "a"})

    assert code == 200, out
    assert "D07" in ids(proj.store) and "D08" not in ids(proj.store)
    assert "T07" in TaskGraph.load(proj.tasks).tasks
    assert owners(proj.pending) == ["b"]


def test_the_browser_refuses_a_name_nobody_staged_under(proj, srv,
                                                        monkeypatch):
    """The same sentence the terminal prints, because it is the same function.

    A rule about who may write somebody else's draft that held at one door and
    not the other is exactly the drift `applying.py` exists to stop.
    """
    as_agent(monkeypatch, "a")
    pending.stage_all([D07], proj.pending)
    as_agent(monkeypatch, None)

    code, out = jreq(srv, "/api/apply", "POST", {"agent": "agnet"})

    assert code == 400
    assert "a 1" in out["error"], out
    assert "D07" not in ids(proj.store)
    assert owners(proj.pending) == ["a"]


def test_a_bodyless_apply_still_applies_everything(proj, srv, monkeypatch):
    """The page posted no body before this change and the older one may still
    be in somebody's tab. An absent `agent` is every writer, not none."""
    as_agent(monkeypatch, "a")
    pending.stage_all([D07], proj.pending)
    as_agent(monkeypatch, None)
    pending.stage_all([D08], proj.pending)

    code, out = jreq(srv, "/api/apply", "POST")

    assert code == 200, out
    assert {"D07", "D08"} <= set(ids(proj.store))


def test_the_skill_tells_the_truth_about_several_agents(proj, run, monkeypatch):
    """`H-F2` again, for the section that came after it.

    The skill is the only prose here written for a machine to act on, so each
    claim in it is run rather than spell-checked. The section is the premise
    and the store is the evidence: move the behaviour and this fails where the
    claim is written.
    """
    import pathlib as _pl

    skill = (_pl.Path(__file__).resolve().parents[1] / "skills" / "dear-guide"
             / "SKILL.md").read_text(encoding="utf-8")
    section = skill.split("## Sharing a clone", 1)[1].split("\n## ", 1)[0]

    for claim in ("dg pending --agent", "dg apply   --agent",
                  "dg clear   --agent", "--agent unowned"):
        assert claim in section, f"the skill does not offer {claim!r}"

    as_agent(monkeypatch, "b")
    pending.stage_all([D07], proj.pending)
    as_agent(monkeypatch, "c")
    pending.stage_all([D08], proj.pending)

    # "as an agent, do not reach for it ... only a caller with no $DG_AGENT"
    assert run("apply", "--agent", "b").exit_code != 0
    assert "D07" not in ids(proj.store)

    as_agent(monkeypatch, None)

    # "a name nobody staged under is refused, with the roster"
    res = run("apply", "--agent", "d")
    assert res.exit_code != 0 and "b 1" in res.output

    # "dg apply --agent b ... takes it, and leaves everybody else's staged"
    assert run("apply", "--agent", "b").exit_code == 0
    assert "D07" in ids(proj.store) and owners(proj.pending) == ["c"]

    # "dg clear --agent c ... turn one down, without touching the others"
    assert run("clear", "--agent", "c").exit_code == 0
    assert pending.load(proj.pending) == [] and "D08" not in ids(proj.store)


def test_a_narrowed_clear_names_the_half_it_could_not_reach(proj, run,
                                                            monkeypatch):
    """The clears are per store; rejecting a proposal is not.

    `dg apply --agent` spans both trays because applying always did, and these
    two never did — so turning a proposal down through one of them leaves the
    other half staged. The cross-tray roster would catch it eventually; saying
    it here catches it one command earlier, and names the command.
    """
    as_agent(monkeypatch, "b")
    pending.stage_all([D07], proj.pending)
    pending.stage_all([T07], task_pending.path())
    as_agent(monkeypatch, None)

    res = run("clear", "--agent", "b")

    assert res.exit_code == 0, res.output
    assert "dg task clear --agent b" in res.output, res.output
    assert owners(task_pending.path()) == ["b"], "the note was wrong"

    assert run("task", "clear", "--agent", "b").exit_code == 0
    assert pending.load(task_pending.path()) == []


def test_the_note_is_silent_when_there_is_no_other_half(proj, run,
                                                        monkeypatch):
    as_agent(monkeypatch, "b")
    pending.stage_all([D07], proj.pending)
    as_agent(monkeypatch, None)

    out = run("clear", "--agent", "b").output

    assert "other tray" not in out, out


# ---- the name is an address ----------------------------------------------
#
# `--agent` made the name something a reader types and a writer acts on, and
# nothing had ever constrained it: `$DG_AGENT` is stamped verbatim. One value
# breaks it, because `named` falls back to it and `addressed` maps it to
# `None` — so the reading and the write disagreed about who "unowned" meant.
# Reserved at the door rather than reconciled in the filters: one place the
# name is written down, one place it has to be a name.


def test_a_writer_cannot_be_called_unowned(proj, run, monkeypatch):
    """**The bug this closes.** Before the reservation:

        dg pending --agent unowned   → 2 ops (the named writer's, and unsigned)
        dg apply   --agent unowned   → 1 op  (only the unsigned)

    A reader reviewed two and accepted one, with nothing saying so.
    """
    as_agent(monkeypatch, "unowned")

    res = run("add", "--id", "D07", "--title", "seven", "--area", "Alpha")

    assert res.exit_code == 2, res.output
    assert pending.load(proj.pending) == [], "the reserved name reached the tray"
    assert "reserved" in res.output


def test_the_whole_session_is_refused_not_just_the_staging(proj, run,
                                                           monkeypatch):
    """Deliberately wider than the op, and pinned so it stays a decision.

    A bad `$DG_AGENT` is a bad *configuration*: refusing only the stage would
    leave twelve call sites to wrap, a traceback wherever one was missed, and
    readings that succeed under an identity every later stage refuses.
    """
    as_agent(monkeypatch, "unowned")

    assert run("show").exit_code == 2
    assert run("pending").exit_code == 2


def test_the_library_keeps_its_own_refusal_for_other_callers(proj,
                                                             monkeypatch):
    """The CLI door is not the only door. `_with_refs` is where the name is
    actually written down, so the raise stays there for everything that does
    not come through `dg`."""
    as_agent(monkeypatch, "unowned")

    with pytest.raises(pending.ApplyError, match="reserved"):
        pending.stage_all([D07], proj.pending)
    assert pending.load(proj.pending) == []


def test_the_refusal_names_the_door_it_arrived_through(proj, srv, monkeypatch):
    """An agent is told to change `$DG_AGENT`; a caller driving the API is told
    to change its header. Telling either one the other's answer is a refusal it
    cannot act on."""
    as_agent(monkeypatch, None)

    code, out = jreq(srv, "/api/pending", "POST", D07)          # sanity: fine
    assert code == 200, out
    pending.clear(proj.pending)

    import urllib.error
    import urllib.request
    r = urllib.request.Request(srv + "/api/pending",
                               data=json.dumps(D07).encode(), method="POST")
    r.add_header(server.TOKEN_HEADER, server.TOKEN)
    r.add_header("Content-Type", "application/json")
    r.add_header(server.AGENT_HEADER, pending.UNOWNED)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            raise AssertionError(f"staged anyway: {resp.status}")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        why = json.loads(exc.read())["error"]

    assert server.AGENT_HEADER in why, why
    assert pending.AGENT_ENV not in why, "the browser was sent to fix a shell"
    assert pending.load(proj.pending) == []


def test_the_header_has_one_definition(proj):
    """`pending` names the header in that refusal and `server` reads it. Two
    literals would be free to drift, and the drift is invisible: a refusal
    naming a header nobody sends."""
    assert server.AGENT_HEADER is pending.server_header()


def test_every_other_name_is_untouched(proj, run, monkeypatch):
    """The reservation is one word. It is not a charset, and a name that worked
    before this went in still works."""
    for name in ("scout-a", "agent 3", "b/2", "UNOWNED", "unowned-1"):
        as_agent(monkeypatch, name)
        pending.clear(proj.pending)
        pending.stage_all([D07], proj.pending)
        assert owners(proj.pending) == [name], name


# ---- narrowing by writer cannot cut an act (audit `G-F11`) ----------------


def test_narrowing_by_writer_never_splits_an_act(proj, monkeypatch):
    """A group is one writer's **by construction**, so `mine` cannot cut one.

    `_with_refs` stamps `by` once per `stage_all` call and the group ref in the
    same pass, so every member of an act carries the same name. That made
    `apply --agent` safe without a line of code — which is exactly why it wants
    a test: it was incidental before groups existed and is load-bearing after,
    and nothing else would notice if a later change stamped `by` per op.
    """
    path = task_pending.path()
    as_agent(monkeypatch, "a")
    pending.stage_all([
        {"op": "add_task", "id": "T80", "title": "second", "area": "Alpha"},
        {"op": "add_dep", "from": "T01", "to": ["T80"], "kind": "precedes"},
    ], path)
    as_agent(monkeypatch, "b")
    pending.stage_all([{"op": "add_task", "id": "T81", "title": "theirs",
                        "area": "Alpha"}], path)

    tray = pending.load(path)
    acts = {}
    for op in tray:
        acts.setdefault(op.get("group") or op["ref"], []).append(op)
    assert any(len(v) > 1 for v in acts.values()), "no multi-op act was staged"

    # Every act is one writer's — the property the safety rests on.
    for members in acts.values():
        assert len({o.get("by") for o in members}) == 1

    # …so every narrowing takes whole acts, whoever is asked for.
    for who in ("a", "b"):
        got, _ = pending.mine(tray, who)
        for op in got:
            whole = pending.group_of(tray, op)
            assert all(o in got for o in whole), (
                f"narrowing to {who} took part of an act")


def test_clearing_one_writer_never_splits_an_act(proj, monkeypatch):
    """`clear_agent`'s half of the same argument, and the reason it needs no
    group logic of its own: it removes everything one writer staged, and an act
    is one writer's."""
    path = task_pending.path()
    as_agent(monkeypatch, "a")
    pending.stage_all([
        {"op": "add_task", "id": "T80", "title": "second", "area": "Alpha"},
        {"op": "add_dep", "from": "T01", "to": ["T80"], "kind": "precedes"},
    ], path)
    as_agent(monkeypatch, "b")
    pending.stage_all([{"op": "add_task", "id": "T81", "title": "theirs",
                        "area": "Alpha"}], path)

    pending.clear_agent("a", path)
    left = pending.load(path)
    assert [o["id"] for o in left] == ["T81"], "a clear left half an act"
    assert not any(o.get("group") for o in left), \
        "the surviving op is a lone one and should carry no group"
