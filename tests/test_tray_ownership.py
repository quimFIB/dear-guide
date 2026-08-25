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
