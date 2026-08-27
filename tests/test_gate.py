"""The commit gate. Half of this file is the detection table, because the gate
sees an arbitrary shell command and a miss is silent in both directions: a false
negative lets a contradiction into the history, a false positive blocks work."""

import itertools
import json
import subprocess
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from dgraph import check, gate, pending, project
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


def _break(store):
    """A blocking violation in the decision store.

    Used by every test whose subject is *something else* — which repository the
    commit targets, whether `dg gate` exits 0 — and which only needs the gate to
    be denying at all. Those all used to hand-edit `decision-graph.md`, which
    stopped denying when the view checks became warnings: a stale view is a
    lagging generated file, not a broken graph. Making the store itself invalid
    says what those tests actually mean.
    """
    g = Graph.load(store / "decisions.json")
    g.vertices["D02"] = replace(g.vertices["D02"], status="WOBBLY")
    g.save(store / "decisions.json")


def test_a_clean_graph_is_allowed_with_no_reason(repo):
    assert _v(repo) == {"verdict": "allow", "reason": ""}


def test_anything_that_is_not_a_commit_is_allowed(repo):
    assert _v(repo, "git log")["verdict"] == "allow"


def test_a_directory_with_no_graph_is_allowed(tmp_path):
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    assert gate.verdict("git commit -m x",
                        project.Project(empty))["verdict"] == "allow"


def test_a_stale_view_warns_and_names_the_remedy(repo):
    """A generated file that lags its store is worth one sentence at the moment
    a commit records it, and is not worth refusing over: the view is generated
    in full from the store and `dg render` rebuilds it. It denied every commit
    in the repository until this pass — including commits touching only `src/`,
    since `check.run` is repo-global."""
    # The realistic shape: the store moved, nobody rendered, and the store is
    # what the commit carries. The view is left behind *in history*, which is
    # the one thing `dg check` cannot see and this is the last cheap moment to
    # fix.
    g = Graph.load(repo / "decisions.json")
    g.vertices["D05"] = replace(g.vertices["D05"], title="Renamed")
    g.save(repo / "decisions.json")
    subprocess.run(["git", "-C", str(repo), "add", "decisions.json"],
                   check=True, capture_output=True)
    v = _v(repo)
    assert v["verdict"] == "warn"
    assert "decision-graph.md no longer matches decisions.json" in v["reason"]
    assert "dg render" in v["reason"]


def test_a_stale_view_is_silent_for_a_commit_that_does_not_record_it(repo):
    """The scoping that makes the warning bearable. A commit touching only
    `src/` is not improved by being told about a generated file it has nothing
    to do with, and that noise is most of what made the denial intolerable."""
    (repo / "decision-graph.md").write_text("hand-edited\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("noise\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "unrelated.txt"], check=True,
                   capture_output=True)
    assert _v(repo) == {"verdict": "allow", "reason": ""}


def test_a_stale_view_is_not_a_blocking_finding(repo):
    """Where the severity actually lives. `dg check` still reports it, the
    pytest plugin still raises it in the warning summary, and nothing refuses."""
    from dgraph.check import run as check_run
    (repo / "decision-graph.md").write_text("hand-edited\n", encoding="utf-8")
    hits = [v for v in check_run(project.Project(repo)) if v.check == "stale_view"]
    assert hits and not any(v.blocking for v in hits)


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


@pytest.mark.parametrize("staged,missing", [
    ("decisions.json", "decision-graph.md"),
    ("decision-graph.md", "decisions.json"),
])
def test_staging_one_half_of_the_pair_warns(repo, staged, missing):
    """Invisible to `dg check`, which compares the worktree — and the exact way
    a store and its view come to disagree inside one commit.

    Both directions in one parametrised test, because they are one rule: the
    pair is committed together or not at all. Only the first half was checked
    for a long time, and the second is the likelier one — the generated file is
    the one people `git add` without thinking about what produced it — and it
    makes the worse commit, since the readable artifact then names decisions
    the committed source of truth has never heard of.
    """
    g = Graph.load(repo / "decisions.json")
    g.vertices["D05"] = replace(g.vertices["D05"], title="Renamed")
    g.save(repo / "decisions.json")
    write(g, repo / "decision-graph.md")
    subprocess.run(["git", "-C", str(repo), "add", staged],
                   check=True, capture_output=True)
    v = _v(repo)
    assert v["verdict"] == "warn"
    assert f"{staged} is staged but {missing} is not" in v["reason"]
    # each half names its own remedy: rebuild the view, or add the store
    assert ("dg render" if staged.endswith(".json")
            else f"git add {missing}") in v["reason"]


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
    _break(repo)
    monkeypatch.setenv("DG_HOOK_OFF", "1")
    assert _v(repo)["verdict"] == "allow"


def test_no_reason_ever_advertises_the_bypass(repo):
    """The reason is read by the model, and a model told about a bypass will use
    it. `DG_HOOK_OFF` is documented for humans, in the README."""
    _break(repo)
    pending.stage({"op": "set_status", "vertex": "D05", "status": "OPEN"},
                  repo / ".dgraph-pending.json")
    for command in ("git commit -m x",):
        assert "DG_HOOK_OFF" not in _v(repo, command)["reason"]


def test_a_missing_git_never_makes_it_guess(repo, monkeypatch):
    """Not every directory with a graph is a repository."""
    monkeypatch.setenv("PATH", "")
    assert _v(repo)["verdict"] == "allow"


def test_a_commit_into_another_repository_is_allowed(repo, tmp_path):
    """Audit C9. `git -C /elsewhere commit` records nothing about this graph;
    gating it against this project's state was a false positive."""
    other = tmp_path / "elsewhere"
    other.mkdir()
    subprocess.run(["git", "-C", str(other), "init", "-q"], check=True,
                   capture_output=True)
    _break(repo)
    assert _v(repo)["verdict"] == "deny"                        # ours: gated
    assert _v(repo, f"git -C {other} commit -m x")["verdict"] == "allow"


def test_a_dash_c_into_the_same_repository_is_still_gated(repo):
    _break(repo)
    assert _v(repo, f"git -C {repo} commit -m x")["verdict"] == "deny"


def test_every_doubt_about_the_target_keeps_gating(repo):
    """An unresolvable -C path, or a --git-dir/--work-tree override the gate
    does not model: the conservative direction is to keep gating."""
    _break(repo)
    assert _v(repo, "git -C /no/such/dir commit -m x")["verdict"] == "deny"
    assert _v(repo, "git --git-dir=/x/.git commit -m x")["verdict"] == "deny"
    assert _v(repo, "git --work-tree=/x commit -m x")["verdict"] == "deny"


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
    _break(repo)
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


# ---- the task store, and its staging tray (audit D5, F1) -----------------


@pytest.fixture
def repo_with_tasks(repo, task_store):
    """The fixture graphs, both rendered, in a repository with both committed."""
    from dgraph import task_render
    from dgraph.tasks import TaskGraph
    task_render.write(TaskGraph.load(repo / "tasks.json"), repo / "tasks.md")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-qm", "tasks"], check=True,
                   capture_output=True)
    return repo


def test_staged_task_ops_ask_rather_than_pass(repo_with_tasks):
    """`.dgraph-task-pending.json` is gitignored like its sibling, so a commit
    drops staged work with no trace in the diff — whichever store it is for."""
    pending.stage({"op": "set_status", "task": "T02", "status": "DOING"},
                  repo_with_tasks / ".dgraph-task-pending.json")
    v = _v(repo_with_tasks)
    assert v["verdict"] == "ask"
    assert "1 task op(s)" in v["reason"] and "dg task clear" in v["reason"]


def test_an_unreadable_task_tray_denies(repo_with_tasks):
    (repo_with_tasks / ".dgraph-task-pending.json").write_text("{oh no")
    v = _v(repo_with_tasks)
    assert v["verdict"] == "deny"
    assert ".dgraph-task-pending.json" in v["reason"]
    assert "dg task clear" in v["reason"]


@pytest.mark.parametrize("staged,missing", [
    ("tasks.json", "tasks.md"),
    ("tasks.md", "tasks.json"),
])
def test_staging_one_half_of_the_task_pair_warns(repo_with_tasks, staged, missing):
    """The same drift the decision pair is guarded against, in both directions:
    a commit recording a store and a view that disagree."""
    from dgraph import task_render
    from dgraph.tasks import TaskGraph
    tg = TaskGraph.load(repo_with_tasks / "tasks.json")
    tg.tasks["T02"].title = "Renamed"
    tg.save(repo_with_tasks / "tasks.json")
    task_render.write(tg, repo_with_tasks / "tasks.md")
    subprocess.run(["git", "-C", str(repo_with_tasks), "add", staged],
                   check=True, capture_output=True)
    v = _v(repo_with_tasks)
    assert v["verdict"] == "warn"
    assert f"{staged} is staged but {missing} is not" in v["reason"]


def test_the_deny_names_the_store_that_broke(repo_with_tasks):
    """A task-store violation framed as "the decision graph is not valid" sends
    the reader — often a model that will act on the sentence — to the wrong
    file."""
    from dgraph.tasks import TaskGraph
    tg = TaskGraph.load(repo_with_tasks / "tasks.json")
    tg.tasks["T02"].status = "WOBBLY"
    tg.save(repo_with_tasks / "tasks.json")
    v = _v(repo_with_tasks)
    assert v["verdict"] == "deny"
    assert "The task graph is not valid" in v["reason"]
    assert "task_status_legal" in v["reason"]


# ---- audit F11: what the commit records, not what the worktree holds -------


def _touch_pair(repo, monkeypatch=None):
    """Move the store and its view together, so both have something to record.

    `monkeypatch` moves into the repository, because a pathspec is resolved
    against the shell's working directory and a host hands the gate the command
    it is about to run *there*.
    """
    if monkeypatch is not None:
        monkeypatch.chdir(repo)
    g = Graph.load(repo / "decisions.json")
    g.vertices["D05"] = replace(g.vertices["D05"], title="Renamed")
    g.save(repo / "decisions.json")
    write(g, repo / "decision-graph.md")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                   capture_output=True)


@pytest.mark.parametrize("command,named,missing", [
    ("git commit -m x -- decisions.json", "decisions.json", "decision-graph.md"),
    ("git commit decisions.json -m x", "decisions.json", "decision-graph.md"),
    ("git commit -m x -- decision-graph.md", "decision-graph.md", "decisions.json"),
])
def test_a_pathspec_commit_of_half_the_pair_warns(repo, monkeypatch, command,
                                                  named, missing):
    """Audit F11. `git commit -- <path>` ignores the index entirely and commits
    the worktree version of the named paths, so a gate that read only the index
    said `allow` to the very commit the pair check exists to refuse: both files
    staged and consistent, one of them committed.

    The bare-path form (no `--`) is the same command and was equally allowed.
    """
    _touch_pair(repo, monkeypatch)
    v = _v(repo, command)
    assert v["verdict"] == "warn"
    assert f"{named} is named in this commit but {missing} is not" in v["reason"]


@pytest.mark.parametrize("command", [
    "git commit -m x",                                    # the index, consistent
    "git commit -am x",                                   # -a commits the pair
    "git commit -m x -- decisions.json decision-graph.md",   # both named
    "git commit -m x -- .",                               # a directory covering both
    "git commit -m x -- README.md",                       # names neither
    'git commit -m x -- "*.json"',                        # opaque: falls back
    "git commit --message=x -- decisions.json decision-graph.md",
])
def test_a_pathspec_that_keeps_the_pair_together_is_allowed(repo, monkeypatch,
                                                            command):
    """The other half of the fix, and the one that matters more day to day: a
    parser that guessed would turn `-m x` into a pathspec named `x` and deny
    every commit with a message."""
    _touch_pair(repo, monkeypatch)
    assert _v(repo, command)["verdict"] == "allow"


def test_a_pathspec_leaving_out_an_unchanged_partner_is_allowed(repo, monkeypatch):
    """A pathspec commit is the normal way to commit part of a dirty tree.
    Naming the store and not the view is a contradiction only if the view has
    something to record — here it does not, so the commit records a pair that
    still agrees and must not be refused."""
    monkeypatch.chdir(repo)
    (repo / "unrelated.txt").write_text("noise\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                   capture_output=True)
    assert _v(repo, "git commit -m x -- unrelated.txt")["verdict"] == "allow"


@pytest.mark.parametrize("command,paths", [
    ("git commit -m msg", ()),
    ("git commit -am msg", ()),
    ("git commit -mmsg a.py", ("a.py",)),
    ("git commit --message=msg -- a.py", ("a.py",)),
    ("git commit --author x -m y b.py", ("b.py",)),
    ("git commit -F note.txt c.py", ("c.py",)),
    ("git commit -S -m y d.py", ("d.py",)),
])
def test_the_pathspec_parser_knows_which_words_are_values(command, paths):
    """`-m x` swallows its argument and `-am x` swallows it too, one option
    later. Getting this wrong in either direction is silent: a message read as
    a path denies every commit, a path read as a message allows every one."""
    assert gate._commits(command)[0].paths == paths


# ---- removals go to the human --------------------------------------------
#
# The only `ask` the gate emits. Not a question about validity — a removal can
# be perfectly valid — but about whether somebody meant it, because nothing in
# this tool records what a removal takes away.


@pytest.mark.parametrize("command", [
    "dg rm D02",
    "dg rm D02 --splice",
    "dg --project /somewhere rm D02",
    "dg -C /somewhere task rm T01 --into T02",
    "dg task rm T01",
    "echo hi && dg rm D03",
    "sudo dg rm D03",
])
def test_these_removals_are_put_to_the_human(command):
    v = gate.verdict(command)
    assert v["verdict"] == "ask" and "removes a node" in v["reason"]


@pytest.mark.parametrize("command", [
    "dg show",
    "dg task drop T01 --keep T02",     # loses a claim, invents none
    "dg undep D06 --after D05",        # likewise
    "dg drop 0",                       # unstages an op; nothing is removed
    "dg task drop-op 0",
    "rm -rf D02",                      # not this tool at all
])
def test_these_are_not_removals(command):
    assert gate.verdict(command)["verdict"] != "ask"


def test_a_removal_is_asked_about_even_where_there_is_no_project(tmp_path,
                                                                 monkeypatch):
    """The question is about intent, not about the store, so it does not depend
    on finding one — and a removal outside a project would fail anyway."""
    monkeypatch.setattr(project, "_override", tmp_path)
    assert gate.verdict("dg rm D02")["verdict"] == "ask"


def test_the_gate_switch_still_turns_removals_off(monkeypatch):
    """`ask` sits behind the same `_off` escape as everything else — one switch
    for the whole gate, not one per verdict."""
    monkeypatch.setenv("DG_HOOK_OFF", "1")
    assert gate.verdict("dg rm D02")["verdict"] == "allow"


# ---- a finding carries where it came from --------------------------------


#: The three validators, and the store each one's findings are about. The
#: origin is read off the finding at runtime; this is how the test tells what
#: the right answer *is*, without trusting the same table the code trusts.
_EMITTERS = {"model": "decision", "tasks": "task", "cross": "link"}


def _emitter(name):
    """The validator module that raises `name`, by looking for the literal."""
    import pathlib
    import re

    root = pathlib.Path(gate.__file__).parent
    found = [origin for mod, origin in _EMITTERS.items()
             if re.search(rf'["\']{re.escape(name)}["\']',
                          (root / f"{mod}.py").read_text(encoding="utf-8"))]
    assert len(found) < 2, f"{name} is emitted by {found}"
    return found[0] if found else None


@pytest.mark.parametrize("name", check.CHECKS)
def test_every_check_name_resolves_to_the_store_that_emits_it(name):
    """The F8 test, and the point of the item. Its predecessor discharged "the
    deny names the broken store" with `task_status_legal` -- one of the eleven
    names that happens to carry the `task_` prefix the gate classified by. Nine
    others did not, so `released_by_drop`, `orphaned_by_drop`,
    `parked_holding_work` and the five `evidence_*` rules were all reported as
    decision-graph breakage, and nothing noticed."""
    assert name in check.ORIGIN, (
        f"{name} declares no store — add it to `check.ORIGIN`")
    emitter = _emitter(name)
    if emitter is None:
        # `store_loads` and the two view checks are raised by `check` itself,
        # which knows which store it was reading; there is no single right
        # answer to read off a module. `store_loads` declares none at all.
        return
    assert check.ORIGIN[name] == emitter, (
        f"{name} is emitted by {emitter}.py but declared as "
        f"{check.ORIGIN[name]!r}")


def test_no_check_name_is_classified_by_its_prefix_any_more():
    """The convention the gate used to rely on is not merely unenforced, it is
    false: eight rules about the task and link stores carry no prefix. Pinned so
    that nobody restores the prefix rule as a shortcut."""
    unprefixed = [n for n, o in check.ORIGIN.items()
                  if o == "task" and not n.startswith(("task_", "stale_task_"))]
    unprefixed += [n for n, o in check.ORIGIN.items()
                   if o == "link" and not n.startswith("link_")]
    # Not an exact list: a new check must not have to be added here, or the
    # pin becomes a chore and gets deleted. What is asserted is that both
    # halves of the old convention are false, which is enough to make
    # restoring it fail.
    assert {"released_by_drop", "orphaned_by_drop"} <= set(unprefixed)
    assert {"evidence_dropped", "evidence_unharvested"} <= set(unprefixed)


def test_every_finding_names_a_store(repo_with_tasks):
    """Nothing reaches the gate unclassified, whichever store it came from."""
    tg_path = repo_with_tasks / "tasks.json"
    raw = json.loads(tg_path.read_text())
    raw["tasks"][1]["because"] = "D99"          # a link violation
    tg_path.write_text(json.dumps(raw))
    _break(repo_with_tasks)                     # a decision violation
    findings = check.run(project.Project(repo_with_tasks))
    assert findings
    assert all(v.origin in ("decision", "task", "link") for v in findings), (
        [(v.check, v.origin) for v in findings if v.origin is None])
    assert {v.origin for v in findings} >= {"decision", "link"}


def _tasks_only(tmp_path, monkeypatch, tasks_json):
    """A project with a `tasks.json` and no decision store at all."""
    (tmp_path / "tasks.json").write_text(tasks_json, encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t",
                 "-c", "user.name=t", "commit", "-qm", "initial"]):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True)
    monkeypatch.setattr(project, "_override", tmp_path)
    return tmp_path


def test_a_corrupt_tasks_json_alone_never_blames_a_decision_graph(tmp_path,
                                                                  monkeypatch):
    """`store_loads` is emitted by all three validators, so no naming scheme can
    say which store it is about. A tasks-only project with an unreadable store
    was refused with "The decision graph is not valid" -- naming a file that
    does not exist anywhere in the project."""
    root = _tasks_only(tmp_path, monkeypatch, "{oh no")
    v = _v(root)
    assert v["verdict"] == "deny"
    assert "The task graph is not valid" in v["reason"]
    assert "decision graph" not in v["reason"]
    assert "decisions.json" not in v["reason"]


@pytest.mark.parametrize("check_name, subject", [
    ("released_by_drop", "The task graph is not valid"),
    ("evidence_dropped", "The link between the two graphs is not valid"),
])
def test_an_unprefixed_rule_names_its_own_store(repo_with_tasks, monkeypatch,
                                               check_name, subject):
    """Two of the nine names that carried no prefix, taken all the way through
    the gate. Both used to answer "The decision graph is not valid"."""
    from dgraph.violation import Violation

    findings = [Violation(check_name, "something broke", "error",
                          check.ORIGIN[check_name])]
    monkeypatch.setattr(gate._check, "run", lambda proj=None: findings)
    v = _v(repo_with_tasks)
    assert v["verdict"] == "deny" and subject in v["reason"]


# ---- the gate answers the whole command ----------------------------------


@pytest.mark.parametrize("a, b", list(itertools.product(gate.RANK, repeat=2)))
def test_the_stronger_verdict_wins_and_no_reason_is_dropped(a, b):
    """The property the fall-through rests on. `combine` is order-independent,
    returns the strongest of the two, and carries both reasons -- the second is
    the half F4 lost, since the removal's `ask` kept its verdict but replaced
    the commit's reason with its own."""
    left = {"verdict": a, "reason": f"because {a}"}
    right = {"verdict": b, "reason": f"because {b}"}
    out = gate.combine([left, right])
    assert out["verdict"] == max((a, b), key=gate.RANK.index)
    assert f"because {a}" in out["reason"]
    assert f"because {b}" in out["reason"]
    assert gate.combine([right, left])["verdict"] == out["verdict"]


def test_combine_of_nothing_is_a_silent_allow():
    assert gate.combine([]) == {"verdict": "allow", "reason": ""}
    assert gate.combine([{"verdict": "allow", "reason": ""}]) == {
        "verdict": "allow", "reason": ""}


@pytest.mark.parametrize("removal", ["dg rm D01", "dg task rm T01"])
def test_a_removal_beside_a_commit_on_an_invalid_store_still_denies(
        repo_with_tasks, removal):
    """F4. `_verdict` returned the removal's `ask` before it looked for a
    commit, so `dg rm D01 && git commit` against a broken store answered `ask`
    where the commit alone answers `deny` -- and the graph the commit would
    have recorded a contradiction into was never mentioned."""
    _break(repo_with_tasks)
    v = _v(repo_with_tasks, f"{removal} && git commit -m x")
    assert v["verdict"] == "deny"
    assert "removes a node" in v["reason"]
    assert "The decision graph is not valid" in v["reason"]
    assert "status_legal" in v["reason"]


@pytest.mark.parametrize("removal", ["dg rm D01", "dg task rm T01"])
def test_a_removal_beside_a_commit_over_a_staged_tray_says_both(
        repo_with_tasks, removal):
    """Same verdict as the removal alone, and that is the point: `ask` is right
    here, but the reason the operator reads has to name the ops about to be
    lost as well as the node about to go."""
    pending.stage({"op": "set_status", "task": "T02", "status": "DOING"},
                  repo_with_tasks / ".dgraph-task-pending.json")
    v = _v(repo_with_tasks, f"{removal} && git commit -m x")
    assert v["verdict"] == "ask"
    assert "removes a node" in v["reason"]
    assert "1 task op(s)" in v["reason"] and "dg task clear" in v["reason"]


def test_a_removal_beside_a_clean_commit_is_still_asked_about(repo_with_tasks):
    """The inversion that moving the check below the commit path would cause:
    a clean commit answers `allow`, and the removal's `ask` -- the reason these
    commands sit behind the gate at all -- would become unreachable."""
    v = _v(repo_with_tasks, "dg rm D01 && git commit -m x")
    assert v["verdict"] == "ask" and "removes a node" in v["reason"]


def test_a_removal_beside_a_commit_carries_a_view_warning_too(repo_with_tasks,
                                                              monkeypatch):
    """A `warn` is the weakest thing the commit path emits, so it is the one a
    combiner that merely took the stronger verdict would silently drop."""
    monkeypatch.chdir(repo_with_tasks)
    (repo_with_tasks / "decision-graph.md").write_text("stale\n")
    subprocess.run(["git", "-C", str(repo_with_tasks), "add", "decisions.json",
                    "decision-graph.md"], check=True, capture_output=True)
    v = _v(repo_with_tasks, "dg rm D01 && git commit -m x")
    assert v["verdict"] == "ask"
    assert "removes a node" in v["reason"]
    assert "decision-graph.md" in v["reason"]
