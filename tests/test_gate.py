"""The commit gate. Half of this file is the detection table, because the gate
sees an arbitrary shell command and a miss is silent in both directions: a false
negative lets a contradiction into the history, a false positive blocks work."""

import json
import subprocess
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from dgraph import gate, pending, project
from dgraph.cli import app
from dgraph.model import Graph
from dgraph.render import write

runner = CliRunner()


# ---- recognising a commit ------------------------------------------------


@pytest.mark.parametrize("command", [
    "git commit -m x",
    "git commit",
    "cd sub && git commit -m x",              # the shape agents produce most
    "git add -A && git commit -m 'x y'",
    "git -C sub -c user.name=x commit -m x",
    "/usr/bin/git commit -m x",
    "GIT_AUTHOR_NAME=x git commit -m x",
    "sudo git commit -m x",
    "git add -A\ngit commit -m x",            # one string, two commands
    "git commit -n -m x",                     # -n is --no-verify, not a dry run
    "git commit -F- <<EOF",
    "git commit --amend --no-edit",
])
def test_these_are_commits(command):
    assert gate.is_commit(command)


@pytest.mark.parametrize("command", [
    "git log --oneline",
    "git status",
    'echo "git commit"',
    'git log --format="git commit"',
    "git commit --dry-run",                   # writes nothing
    "git add decisions.json",
    "grep -r 'git commit' .",
    "git commit-tree abc123",                 # a different subcommand
    "gitk commit",
])
def test_these_are_not_commits(command):
    assert not gate.is_commit(command)


def test_a_quoted_subcommand_still_counts():
    """Quoting is a shell detail, not an escape hatch."""
    assert gate.is_commit("git 'commit' -m x")


def test_unbalanced_quotes_fail_open():
    """The shell will not run it either, so there is nothing to gate — and
    guessing would mean denying commands that were never going to happen."""
    assert not gate.is_commit('git commit -m "unterminated')


# ---- the policy ----------------------------------------------------------


@pytest.fixture
def repo(store, g):
    """The fixture graph, rendered, inside a real git repository."""
    write(g)
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t",
                 "-c", "user.name=t", "commit", "-qm", "initial"]):
        subprocess.run(["git", "-C", str(store), *args], check=True,
                       capture_output=True)
    return store


def _v(store, command="git commit -m x"):
    return gate.verdict(command, project.Project(store))


def test_a_clean_graph_is_allowed_with_no_reason(repo):
    assert _v(repo) == {"verdict": "allow", "reason": ""}


def test_anything_that_is_not_a_commit_is_allowed(repo):
    assert _v(repo, "git log")["verdict"] == "allow"


def test_a_directory_with_no_graph_is_allowed(tmp_path):
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    assert gate.verdict("git commit -m x",
                        project.Project(empty))["verdict"] == "allow"


def test_a_stale_view_is_denied_and_names_the_remedy(repo):
    (repo / "decision-graph.md").write_text("hand-edited\n", encoding="utf-8")
    v = _v(repo)
    assert v["verdict"] == "deny"
    assert "stale_view" in v["reason"]
    assert "dg render" in v["reason"]


def test_a_broken_graph_is_denied_quoting_every_violation(repo):
    from dgraph.check import run as check_run
    bad = Graph.load(repo / "decisions.json")
    bad.vertices["D02"] = replace(bad.vertices["D02"], status="WOBBLY")
    bad.save(repo / "decisions.json")
    v = _v(repo)
    assert v["verdict"] == "deny"
    for problem in check_run(project.Project(repo)):
        if problem.blocking:
            assert str(problem) in v["reason"]


def test_staging_the_store_without_the_view_is_denied(repo):
    """Invisible to `dg check`, which compares the worktree — and the exact way
    a store and its view come to disagree inside one commit."""
    g = Graph.load(repo / "decisions.json")
    g.vertices["D05"] = replace(g.vertices["D05"], title="Renamed")
    g.save(repo / "decisions.json")
    write(g, repo / "decision-graph.md")
    subprocess.run(["git", "-C", str(repo), "add", "decisions.json"],
                   check=True, capture_output=True)
    v = _v(repo)
    assert v["verdict"] == "deny"
    assert "decision-graph.md" in v["reason"] and "dg render" in v["reason"]


def test_staging_both_is_allowed(repo):
    g = Graph.load(repo / "decisions.json")
    g.vertices["D05"] = replace(g.vertices["D05"], title="Renamed")
    g.save(repo / "decisions.json")
    write(g, repo / "decision-graph.md")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                   capture_output=True)
    assert _v(repo)["verdict"] == "allow"


def test_unapplied_ops_ask_rather_than_deny(repo):
    """A judgement call about work in progress belongs to the human, not to the
    agent — and `.dgraph-pending.json` is gitignored, so nothing else would
    mention it."""
    pending.stage({"op": "set_status", "vertex": "D05", "status": "OPEN"},
                  repo / ".dgraph-pending.json")
    v = _v(repo)
    assert v["verdict"] == "ask"
    assert "dg apply" in v["reason"]


def test_the_switch_off_allows_everything(repo, monkeypatch):
    (repo / "decision-graph.md").write_text("hand-edited\n", encoding="utf-8")
    monkeypatch.setenv("DG_HOOK_OFF", "1")
    assert _v(repo)["verdict"] == "allow"


def test_no_reason_ever_advertises_the_bypass(repo):
    """The reason is read by the model, and a model told about a bypass will use
    it. `DG_HOOK_OFF` is documented for humans, in the README."""
    (repo / "decision-graph.md").write_text("hand-edited\n", encoding="utf-8")
    pending.stage({"op": "set_status", "vertex": "D05", "status": "OPEN"},
                  repo / ".dgraph-pending.json")
    for command in ("git commit -m x",):
        assert "DG_HOOK_OFF" not in _v(repo, command)["reason"]


def test_a_missing_git_never_makes_it_guess(repo, monkeypatch):
    """Not every directory with a graph is a repository."""
    monkeypatch.setenv("PATH", "")
    assert _v(repo)["verdict"] == "allow"


def test_a_corrupt_pending_file_is_denied_naming_the_file(repo):
    """Audit A2a. An unreadable staging file may hold staged work this commit
    would silently drop — the situation `ask` exists for, unreadable. It used
    to crash the gate instead, and a crash is an allow to both adapters."""
    (repo / ".dgraph-pending.json").write_text("{not json", encoding="utf-8")
    v = _v(repo)
    assert v["verdict"] == "deny"
    assert ".dgraph-pending.json" in v["reason"]
    assert "dg clear" in v["reason"]


def test_verdict_never_raises_even_when_state_reading_does(repo, monkeypatch):
    """The contract in the docstring, pinned. Anything unexpected must come
    back as a deny, because the adapters fail open on a crash."""
    def boom(proj):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(gate._check, "run", boom)
    v = _v(repo)
    assert v["verdict"] == "deny"
    assert "disk on fire" in v["reason"]
    assert "DG_HOOK_OFF" not in v["reason"]      # never advertise the bypass


# ---- the command surface ------------------------------------------------


def test_gate_always_exits_zero(repo):
    """An adapter must be able to tell a refusal from a crash."""
    (repo / "decision-graph.md").write_text("hand-edited\n", encoding="utf-8")
    res = runner.invoke(app, ["--project", str(repo), "gate",
                              "--command", "git commit -m x", "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output)["verdict"] == "deny"


def test_gate_json_is_one_object_of_two_keys(repo):
    res = runner.invoke(app, ["--project", str(repo), "gate",
                              "--command", "git log", "--json"])
    assert json.loads(res.output) == {"verdict": "allow", "reason": ""}


def test_gate_exits_zero_on_a_corrupt_pending_file(repo):
    """The A2a crash end-to-end: exit 0 with a deny, parseable, no traceback."""
    (repo / ".dgraph-pending.json").write_text("{not json", encoding="utf-8")
    res = runner.invoke(app, ["--project", str(repo), "gate",
                              "--command", "git commit -m x", "--json"])
    assert res.exit_code == 0
    assert json.loads(res.output)["verdict"] == "deny"
