"""What `agentic/` ships: the fan-out procedure, and the optional capture.

It is a wrapper named `dg`, put first on `$PATH`, that records every invocation
and then delegates. Two properties carry the whole thing and both fail silently,
which is why they are pinned here rather than left to a smoke test somebody
remembers to run.

**Stdout must stay byte-clean.** `dg-agent claim` prints a bare name precisely
so `DG_AGENT=$(dg-agent claim)` works. A wrapper that prepended a banner, or
that let a stray `echo` reach stdout, would break every launch that used it --
and break it into a name with a newline and a sentence in it, which fails later
and somewhere else.

**There are two wrappers, because there are two binaries.** The launcher split
out of `dg` into `dg-agent`, and most of what a capture of a *fan-out* is for
went with it: the claims, the setup, the parks. A capture that wrapped only
`dg` would look complete and hold none of them.

**A dropped op must survive.** That is the reason the capture exists at all. The
graph keeps what landed and deliberately not what was proposed: `dg drop`,
`dg clear --agent` and an applied tray all erase. The wrapper records both trays
as they stood after each call, so an op that never reached the graph is the
difference between one entry and the next.

Driven through a real subprocess with a real `$PATH`, because the mechanism IS
the `$PATH` interception -- calling the script directly would test everything
except the thing that makes it work.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAPTURE_BIN = os.path.join(ROOT, "agentic", "bin")


@pytest.fixture
def project(tmp_path):
    """An empty git repo with a graph, and `$PATH` led by the capture.

    The real `dg` has to be findable behind the wrapper, so whichever one is
    running these tests is put on `$PATH` after it. That is also the arrangement
    a user ends up with.
    """
    real = shutil.which("dg") or os.path.join(os.path.dirname(sys.executable), "dg")
    if not os.path.exists(real):
        pytest.skip("no `dg` on PATH to wrap")
    if not os.path.exists(os.path.join(os.path.dirname(real), "dg-agent")):
        pytest.skip("no `dg-agent` beside it")
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(
        [CAPTURE_BIN, os.path.dirname(real), env.get("PATH", "")])
    env.pop("DG_AGENT", None)
    subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, capture_output=True)

    def run(*args, **kw):
        return subprocess.run(["dg", *args], cwd=tmp_path, env={**env, **kw.pop("env", {})},
                              capture_output=True, text=True, **kw)

    def arun(*args, **kw):
        return subprocess.run(["dg-agent", *args], cwd=tmp_path,
                              env={**env, **kw.pop("env", {})},
                              capture_output=True, text=True, **kw)

    run("init")
    run("task", "init")
    return tmp_path, run, arun


def entries(root):
    path = os.path.join(root, ".dgraph-capture", "dg.jsonl")
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_the_wrapper_is_what_runs(project):
    """The mechanism, asserted rather than assumed: a directory holding only
    `dg`, first on `$PATH`. If this resolves to the real one the rest of this
    file is testing nothing."""
    root, run, arun = project
    assert os.path.isfile(os.path.join(CAPTURE_BIN, "dg"))
    assert os.path.isfile(os.path.join(CAPTURE_BIN, "dg-agent")), (
        "after the split, a capture with no `dg-agent` wrapper records none of "
        "the claims, the setup or the parks -- which is most of what a capture "
        "of a fan-out is for")
    assert sorted(os.listdir(CAPTURE_BIN)) == ["dg", "dg-agent"], (
        "the capture directory must hold nothing else -- putting it first on "
        "$PATH shadows whatever is in it")
    assert entries(root), "nothing was recorded for `dg init`"


def test_stdout_stays_clean_enough_to_substitute(project):
    """`DG_AGENT=$(dg-agent claim)` is the only sensible caller of `claim`, so
    the whole of stdout has to be the name -- and it goes through the second
    wrapper now, which is the one that could most easily have broken it."""
    root, run, arun = project
    r = arun("claim")

    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() and "\n" not in r.stdout.strip(), repr(r.stdout)
    name = r.stdout.strip()
    assert all(c.isalnum() or c == "-" for c in name), repr(name)


def test_the_exit_code_is_the_real_one(project):
    """A wrapper that swallowed a refusal would turn every failed `dg` into a
    success, and a launcher checking `$?` would carry on into a sweep that had
    not been set up."""
    root, run, arun = project

    assert arun("release", "nobody-holds-this").returncode != 0
    assert run("pending").returncode == 0


def test_an_op_that_never_reached_the_graph_is_still_in_the_log(project):
    """**The reason the capture exists.**

    The store keeps what landed. A proposal somebody staged and thought better
    of leaves nothing behind -- rightly, since a record of every draft is not a
    record of a project's decisions, and wrongly for watching a fan-out, where
    the proposals nobody took are most of what there is to see.
    """
    root, run, arun = project
    name = arun("claim").stdout.strip()
    run("add", "--id", "D02", "--title", "a proposal nobody kept",
        "--area", "General", env={"DG_AGENT": name})
    run("drop", "0")

    log = entries(root)
    staged = [e for e in log if e["tray"]]
    assert staged, "no entry recorded a non-empty tray"
    ops = json.loads(staged[-1]["tray"])
    assert ops[0]["title"] == "a proposal nobody kept"
    assert ops[0]["by"] == name, "the record lost who proposed it"

    # ...and the drop is visible as the difference, not as an absence.
    assert log[-1]["argv"][:1] == ["drop"]
    assert not log[-1]["tray"], "the tray after the drop should be empty"


def test_a_refused_call_is_recorded_as_a_reason_and_not_as_a_crash(project):
    """`agentic/README.md`: *the refusals are the content*. A run where every
    proposal was accepted demonstrates nothing about a tool whose subject is
    what happens when work is proposed and sometimes turned down.

    Which makes the shape of a refusal part of what the capture records. A
    stage-time rule raised from `pending.stage_all` — the one door, where
    `$DG_TERSE` is judged — reaches the log as whatever the CLI printed, so a
    refusal that escaped as a traceback would be recorded as one: an entry
    saying the tool broke, where the run's most interesting moment should be.
    `cli._stage_all` is what keeps it a sentence, and nothing else asserts that.

    Pinned with the tray beside it, because a refusal that also emptied a
    shared tray would take another writer's proposals with it.
    """
    root, run, arun = project
    env = {"DG_AGENT": "brisk-beacon", "DG_TERSE": "on"}
    run("add", "--id", "D01", "--title", "A question", "--area", "General",
        env=env)
    run("apply", env=env)
    run("add", "--id", "D02", "--title", "Another", "--area", "General",
        env=env)

    r = run("decide", "D01", "--answer", "x " * 300, "--source", "discussion",
            "--falsifier", "it moves", "--opens", "D02", env=env)
    assert r.returncode == 1
    said = r.stdout + r.stderr
    assert "Traceback" not in said, said
    assert "DG_TERSE" in said and "--source" in said

    last = entries(root)[-1]
    assert last["argv"][:2] == ["decide", "D01"] and last["exit"] == 1
    assert "DG_TERSE" in (last["stdout"] or "") + (last["stderr"] or "")
    # The proposal staged before it is untouched: nothing was taken out of a
    # tray that may be shared with other writers.
    assert len(json.loads(last["tray"])) == 1


def test_every_entry_says_who_and_what(project):
    root, run, arun = project
    name = arun("claim").stdout.strip()
    run("pending", env={"DG_AGENT": name})

    last = entries(root)[-1]
    assert last["argv"] == ["pending"]
    assert last["agent"] == name
    assert last["exit"] == 0
    assert "at" in last and "cwd" in last
    assert last["stdout"] is not None


def test_the_record_is_scratch_that_git_already_ignores(project):
    """`.dgraph-capture/` is covered by the `.dgraph-*` line `dg init` writes,
    so a capture never has to be remembered about at commit time -- and a run
    that was recorded cannot accidentally be committed as if it were part of
    the graph."""
    root, run, arun = project
    run("pending")
    assert os.path.isdir(os.path.join(root, ".dgraph-capture"))

    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
    tracked = subprocess.run(["git", "ls-files"], cwd=root,
                             capture_output=True, text=True).stdout
    assert ".dgraph-capture" not in tracked, tracked


# ---- the procedure, and the one claim in it that can rot --------------------


def _guide():
    with open(os.path.join(ROOT, "agentic", "README.md"), encoding="utf-8") as f:
        return f.read()


def test_the_workflow_does_not_belong_to_one_host():
    """The claim: a fan-out works under Claude Code, under opencode, or under
    anything else that can run `dg`.

    It is true by construction -- an agent touches the graph only through the
    CLI, and the whole contract with whatever spawned it is one environment
    variable. Prose is where that stops being true, by growing an example that
    reads as a requirement, so the guide has to keep showing both.
    """
    guide = _guide()
    assert "claude" in guide.lower() and "opencode" in guide.lower(), (
        "the launch step names one host only, which reads as a requirement")
    assert "DG_AGENT" in guide
    assert "contract with the host" in guide, (
        "the guide no longer says what the host actually has to do")


def test_the_command_is_shipped_for_both_hosts():
    """One file in `commands/`, which Claude Code namespaces as `/dg:fanout`
    and the opencode install snippet symlinks as `/dg-fanout`. A command that
    lived anywhere else would be a Claude Code feature wearing a shared name."""
    assert os.path.isfile(os.path.join(ROOT, "commands", "fanout.md"))
    with open(os.path.join(ROOT, "opencode", "README.md"), encoding="utf-8") as f:
        assert '"$repo"/commands/*.md' in f.read(), (
            "opencode installs commands by glob; a new one must need no edit")


def test_the_capture_is_offered_as_optional_and_not_as_a_step():
    """It was written so one run could become a demo, and it is not part of the
    workflow. A guide that presented it as setup would have every fan-out
    recording itself for no reason -- and would suggest the graph is not enough
    on its own, which is the opposite of what it is for."""
    guide = _guide()
    body, _, optional = guide.partition("# Optional: recording the run")
    assert optional, "the capture is no longer in an Optional section"
    assert "Nothing above needs this" in optional
    for step in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6."):
        assert step in body, f"{step} is not in the procedure"
    assert "capture" not in body.split("## 1.")[1].lower(), (
        "the numbered procedure mentions the capture; it is meant to stand "
        "without it")
