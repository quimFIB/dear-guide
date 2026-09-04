"""How much an agent may settle on its own — `$DG_DECIDE`.

The tool has always let an owned caller close anything, and the agentic demo is
written that way. `$DG_DECIDE` narrows it for a fan-out that wants it narrowed,
and the default is unchanged, which is why the demo still passes.

The distinction it mechanises is not "agents judge badly". It is that the graph
has two exits from a decision and both are wrong for a premature one: `dg reopen`
files a reversal, and a reversal means *we changed our mind* rather than *that
should not have been written*; `dg rm` erases, is for things that should never
have been written, and `dg gate` answers `ask` on it, so a person decides anyway.

So the restraint that is right depends on the DECISION, not on who is asking. A
decision whose falsifier is a measurement that was actually made is a fact being
recorded. A judgement between defensible alternatives is where a falsifier
written by something that never had to live with the consequence comes out as
rationalisation. Only the first is mechanically recognisable, and `evidence` is
the check for it.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from typer.testing import CliRunner

from dgraph import cross, pending, project
from dgraph.cli import app
from dgraph.model import Graph
from tests.conftest import FIXTURE, TASK_FIXTURE

runner = CliRunner()


@pytest.fixture
def proj(tmp_path, monkeypatch):
    (tmp_path / "decisions.json").write_text(json.dumps(FIXTURE, indent=2),
                                             encoding="utf-8")
    (tmp_path / "tasks.json").write_text(json.dumps(TASK_FIXTURE, indent=2),
                                         encoding="utf-8")
    monkeypatch.setattr(project, "_override", tmp_path)
    monkeypatch.delenv("DG_AGENT", raising=False)
    monkeypatch.delenv(cross.POLICY_ENV, raising=False)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERM", "dumb")
    subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, capture_output=True)
    return project.Project(tmp_path)


@pytest.fixture
def run(proj):
    def go(*args):
        return runner.invoke(app, ["--project", str(proj.root), *args])
    return go


def decide(run, vid):
    return run("decide", vid, "-a", "an answer long enough to pass",
               "-s", "discussion", "-f", "some measurement moves", "--no-edit")


# ---- the default is what it always was --------------------------------------


def test_an_unset_policy_changes_nothing(proj, run, monkeypatch):
    """The agentic demo has agents A and B closing decisions throughout. A
    default that refused them would have been a behaviour change dressed as a
    feature, and would have broken a suite that is checked into this repo."""
    monkeypatch.setenv("DG_AGENT", "a")

    assert decide(run, "D05").exit_code == 0
    assert any(o["op"] == "close" for o in pending.load(proj.pending))


def test_an_unrecognised_value_is_open_rather_than_an_error(monkeypatch):
    """Read on the path of every `decide`. A typo in a launcher's environment
    must not make the tool unusable for the supervisor too."""
    monkeypatch.setenv(cross.POLICY_ENV, "evidance")
    assert cross.policy() == "open"


# ---- never -------------------------------------------------------------------


def test_never_refuses_an_agent_and_says_who_may(proj, run, monkeypatch):
    monkeypatch.setenv("DG_AGENT", "a")
    monkeypatch.setenv(cross.POLICY_ENV, "never")

    res = decide(run, "D05")

    assert res.exit_code != 0
    assert pending.load(proj.pending) == [], "the refusal staged it anyway"
    assert "$DG_AGENT" in res.output, res.output


# ---- evidence ----------------------------------------------------------------


def _finished_evidence_for(proj, run, monkeypatch, did, tid="T77"):
    monkeypatch.delenv(cross.POLICY_ENV, raising=False)
    run("task", "add", "--id", tid, "--title", "measure it", "--area", "Alpha",
        "--evidence-for", did)
    run("apply")
    run("task", "start", tid)
    run("task", "done", tid, "--outcome", "recall@10 = 0.62")
    run("apply")


def test_evidence_refuses_a_decision_nothing_measures(proj, run, monkeypatch):
    """The judgement call. Nothing is `--evidence-for` it, so there is no
    measurement for the agent to be recording — and the refusal names the
    command that would link one, because a refusal a reader cannot act on is
    the shape `C-F17` is about."""
    monkeypatch.setenv("DG_AGENT", "a")
    monkeypatch.setenv(cross.POLICY_ENV, "evidence")

    res = decide(run, "D05")

    assert res.exit_code != 0
    assert pending.load(proj.pending) == []
    assert "--evidence-for D05" in res.output, res.output


def test_evidence_refuses_while_the_measurement_is_still_running(
        proj, run, monkeypatch):
    """Linked but unfinished. The answer would be recording a result that does
    not exist yet, which is `evidence_after_deciding` arriving from the other
    direction."""
    run("task", "add", "--id", "T78", "--title", "measure it", "--area", "Alpha",
        "--evidence-for", "D05")
    run("apply")
    monkeypatch.setenv("DG_AGENT", "a")
    monkeypatch.setenv(cross.POLICY_ENV, "evidence")

    res = decide(run, "D05")

    assert res.exit_code != 0
    assert "T78" in res.output, res.output


def test_evidence_lets_the_agent_record_what_it_measured(
        proj, run, monkeypatch):
    """**The case the whole policy exists to permit.**

    The task finished, so the falsifier is a measurement rather than a guess,
    and the agent that made it is the right thing to be writing it down.
    """
    _finished_evidence_for(proj, run, monkeypatch, "D05")
    monkeypatch.setenv("DG_AGENT", "a")
    monkeypatch.setenv(cross.POLICY_ENV, "evidence")

    assert decide(run, "D05").exit_code == 0
    assert run("apply").exit_code == 0
    assert Graph.load(proj.store).vertices["D05"].status == "DECIDED"


def test_evidence_says_so_when_the_project_has_no_task_store(
        tmp_path, monkeypatch):
    """`evidence` can never be satisfied without one, so it says that rather
    than refusing with a reason nobody can act on."""
    (tmp_path / "decisions.json").write_text(json.dumps(FIXTURE, indent=2),
                                             encoding="utf-8")
    monkeypatch.setattr(project, "_override", tmp_path)
    monkeypatch.setenv("DG_AGENT", "a")
    monkeypatch.setenv(cross.POLICY_ENV, "evidence")
    subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, capture_output=True)

    res = runner.invoke(app, ["--project", str(tmp_path), "decide", "D05",
                              "-a", "an answer long enough", "-s", "discussion",
                              "-f", "a falsifier", "--no-edit"])

    assert res.exit_code != 0
    assert "dg task init" in res.output, res.output


# ---- what a refusal may promise ----------------------------------------------


@pytest.mark.parametrize("mode,did", [("never", "D05"), ("evidence", "D05")])
def test_no_refusal_offers_a_route_through_the_tray(proj, run, monkeypatch,
                                                    mode, did):
    """**A refusal may not describe a route the same call forecloses.**

    Both of these messages once did. `never` said the agent *may stage this
    decision, and a caller with no $DG_AGENT applies it*, and the unbacked
    `evidence` refusal ended *or leave it staged for a person* — while the check
    that printed them runs before `pending.stage_all` and leaves the tray empty.
    An agent following either would look for an op that is not there.

    The staging route is coherent and is what the tray does for every other op;
    what makes it a lie here is that nothing implements it. Keeping the two
    apart is this assertion: whatever these messages say, they may not send the
    reader to a tray this call did not write to.
    """
    monkeypatch.setenv("DG_AGENT", "a")
    monkeypatch.setenv(cross.POLICY_ENV, mode)

    res = decide(run, did)

    assert res.exit_code != 0
    assert pending.load(proj.pending) == [], "the refusal staged it anyway"
    assert "stage" not in res.output.lower().replace("nothing staged", ""), \
        f"the refusal offers a staging route that does not exist: {res.output}"


# ---- who it applies to -------------------------------------------------------


@pytest.mark.parametrize("mode", ["evidence", "never"])
def test_a_supervisor_is_never_refused(proj, run, monkeypatch, mode):
    """Every value is about an AGENT. An unowned caller is the supervisor, the
    same way it is everywhere else in the ownership model — and if the policy
    reached them, a fan-out's launcher could not settle anything in its own
    project while the variable was set."""
    monkeypatch.delenv("DG_AGENT", raising=False)
    monkeypatch.setenv(cross.POLICY_ENV, mode)

    assert decide(run, "D05").exit_code == 0


def test_it_is_cooperative_and_the_docs_do_not_pretend_otherwise(proj):
    """`$DG_AGENT` is self-declared, so `$DG_DECIDE` is self-declarable, and an
    agent that unset the first would be a supervisor. Nothing here is a security
    boundary. The module says so, because a rule that reads as enforcement and
    is not would be worse than no rule."""
    import pathlib
    import re
    # Whitespace-normalised: the claim is wrapped across comment lines, and an
    # anti-drift test that a reflow could break is one that gets deleted rather
    # than fixed.
    src = re.sub(r"[\s#:]+", " ",
                 pathlib.Path(cross.__file__).read_text(encoding="utf-8"))
    assert "Nothing here is a security boundary" in src
    assert "cooperative" in src.lower()


# ---- who is holding live work, and where that fact lives --------------------


def _holdings(proj):
    from dgraph import agents
    return agents.holdings(proj.root)


def test_who_holds_live_work_is_recorded_outside_the_store(proj, run,
                                                           monkeypatch):
    """`DOING` said a task was claimed and not by whom, so a stalled agent could
    not be told from a slow one.

    The fact is kept in `.dgraph-agents.json`, beside the names themselves --
    scratch, gitignored, gone when the run is.
    """
    monkeypatch.setenv("DG_AGENT", "beta")
    run("task", "add", "--id", "T80", "--title", "claimed", "--area", "Alpha")
    run("apply")
    run("task", "start", "T80")
    run("apply")

    assert _holdings(proj) == {"T80": "beta"}


def test_the_task_store_never_learns_who(proj, run, monkeypatch):
    """**The property this whole arrangement is for.**

    `tasks.json` is committed and kept forever, and agent names are recycled the
    moment they are released -- so a name written there identifies nothing in
    six months and is noise a reader scrolls past. `Stop` and `Completion`
    carried `by` for exactly one commit before that argument won.
    """
    monkeypatch.setenv("DG_AGENT", "beta")
    run("task", "add", "--id", "T81", "--title", "claimed", "--area", "Alpha")
    run("apply")
    run("task", "start", "T81")
    run("task", "done", "T81", "--outcome", "a result")
    run("apply")

    raw = proj.tasks.read_text(encoding="utf-8")
    assert "beta" not in raw, raw
    stored = json.loads(raw)["tasks"][0]
    assert "held_by" not in stored
    assert stored["done"] and stored["outcome"]


def test_parking_records_no_name_either(proj, run, monkeypatch):
    """The other exit, and the one that tempted me: an agent parking work is
    exactly when somebody wants to know which agent it was. That is a question
    about a RUN, so `holdings` answers it while the run is on and the permanent
    record does not answer it at all."""
    monkeypatch.setenv("DG_AGENT", "alpha")
    run("task", "add", "--id", "T82", "--title", "abandoned", "--area", "Alpha")
    run("apply")
    run("task", "start", "T82")
    run("task", "park", "T82", "--why", "the agent died mid-run")
    run("apply")

    raw = proj.tasks.read_text(encoding="utf-8")
    assert "alpha" not in raw, raw
    # The fixture has other tasks; find the one this test parked.
    parked = [t for t in json.loads(raw)["tasks"] if t["id"] == "T82"][0]
    assert parked["status"] == "PARKED"
    assert set(parked["stops"][0]) == {"why", "date"}


def test_the_claim_is_released_when_the_work_leaves_doing(proj, run,
                                                          monkeypatch):
    """A claim on live work. A finished task held by nobody is the truth, and
    leaving a stale name behind would be the store's problem arriving in the
    scratch file instead."""
    monkeypatch.setenv("DG_AGENT", "beta")
    run("task", "add", "--id", "T83", "--title", "claimed", "--area", "Alpha")
    run("apply")
    run("task", "start", "T83")
    run("apply")
    assert _holdings(proj) == {"T83": "beta"}

    run("task", "done", "T83", "--outcome", "it produced a thing")
    run("apply")

    assert _holdings(proj) == {}


def test_a_person_holding_work_names_nobody(proj, run, monkeypatch):
    """Unowned means the supervisor, the same as in the tray -- so a project
    with no agents reads exactly as it did before any of this existed."""
    monkeypatch.delenv("DG_AGENT", raising=False)
    run("task", "add", "--id", "T84", "--title", "mine", "--area", "Alpha")
    run("apply")
    run("task", "start", "T84")
    run("apply")

    assert _holdings(proj) == {}
    assert "held by" not in run("task").output


def test_the_listing_names_the_holder(proj, run, monkeypatch):
    monkeypatch.setenv("DG_AGENT", "beta")
    run("task", "add", "--id", "T85", "--title", "claimed", "--area", "Alpha")
    run("apply")
    run("task", "start", "T85")
    run("apply")

    assert "held by beta" in run("task").output


def test_a_second_agent_cannot_take_claimed_work(proj, run, monkeypatch):
    """The property that makes autonomous pickup safe at all, and it predates
    all of this -- `holdings` only reads a claim `dg task start` already made."""
    monkeypatch.setenv("DG_AGENT", "beta")
    run("task", "add", "--id", "T86", "--title", "claimed", "--area", "Alpha")
    run("apply")
    run("task", "start", "T86")
    run("apply")

    monkeypatch.setenv("DG_AGENT", "gamma")
    res = run("task", "start", "T86")

    assert res.exit_code != 0 or "already DOING" in res.output, res.output


def test_the_holdings_file_is_scratch_git_already_ignores(proj, run,
                                                          monkeypatch):
    """It has to be, or "who held this" ends up in the history of a committed
    file by another route -- which is the thing being avoided."""
    from dgraph import agents, project as pj

    monkeypatch.setenv("DG_AGENT", "beta")
    pj.ensure_ignored(proj.root)
    run("task", "add", "--id", "T87", "--title", "claimed", "--area", "Alpha")
    run("apply")
    run("task", "start", "T87")
    run("apply")
    assert agents.path(proj.root).exists()

    subprocess.run(["git", "add", "-A"], cwd=proj.root, capture_output=True)
    tracked = subprocess.run(["git", "ls-files"], cwd=proj.root,
                             capture_output=True, text=True).stdout
    assert agents.AGENTS_NAME not in tracked, tracked
