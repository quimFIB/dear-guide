"""`dg-agent`: the launcher split out of `dg`, and the environment made visible.

Two properties carry this file.

**The split is a split.** `dg` is for the graphs and `dg-agent` composes the
environment `dg` reads, so neither `--help` names the other's commands and
`dg agent` is not a command anywhere. That is asserted rather than described,
because a forwarding shim is exactly what a split quietly decays into.

**A typo does not weaken a rule by a notch — it removes it.** `$DG_DECIDE`,
`$DG_WRITE`, `$DG_TERSE` and `$DG_AREA` all answer *the widest setting* to a
value they cannot read, and `DG_DECIDE=nevr` therefore reads as `open` and looks
identical to a policy somebody chose. Failing open is right, for the reason the
parsers give: they are read on the path of every judged write, including the
supervisor's, and a launcher's typo must not take the graph away from the person
reviewing the run. It is only *defensible* because something reports it, and for
a long time nothing did — three docstrings promised "the CLI reports the typo
where it is set" and no such report existed. These are that promise.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from dgraph import agent_cli, agents, env, fanout, pending, project
from dgraph.agent_cli import app as agent_app
from dgraph.cli import app as dg_app
from dgraph.tasks import TaskGraph
from tests.conftest import FIXTURE, TASK_FIXTURE

runner = CliRunner()


@pytest.fixture
def proj(tmp_path, monkeypatch):
    (tmp_path / "decisions.json").write_text(json.dumps(FIXTURE, indent=2),
                                             encoding="utf-8")
    (tmp_path / "tasks.json").write_text(json.dumps(TASK_FIXTURE, indent=2),
                                         encoding="utf-8")
    monkeypatch.setattr(project, "_override", tmp_path)
    monkeypatch.setenv("COLUMNS", "300")
    monkeypatch.setenv("TERM", "dumb")
    for name in env.BY_NAME:
        monkeypatch.delenv(name, raising=False)
    return project.Project(tmp_path)


@pytest.fixture
def run(proj):
    def go(*args, **kw):
        return runner.invoke(agent_app, ["--project", str(proj.root), *args],
                             **kw)
    return go


# ---- the split ------------------------------------------------------------


def test_neither_help_screen_names_the_other_binarys_commands(proj):
    """The split asserted rather than described.

    `dg --help` naming an agent command would mean the launcher had grown back
    into the graph tool, and `dg-agent --help` naming a graph command would mean
    the boundary had been crossed the other way — which is the direction that
    actually costs something, since `dg` is where every rule is enforced.
    """
    dg = runner.invoke(dg_app, ["--help"]).output
    launcher = runner.invoke(agent_app, ["--help"]).output

    assert "agent" not in [line.split()[0] for line in dg.splitlines()
                           if line.strip().startswith("│ ")
                           and len(line.split()) > 1]
    for graph_command in ("apply", "decide", "pending", "gate", "serve"):
        assert f" {graph_command} " not in launcher, (
            f"`dg-agent --help` offers {graph_command}, which is `dg`'s")
    for launcher_command in ("claim", "run", "env", "expire"):
        assert launcher_command in launcher


def test_dg_has_no_agent_command_at_all(proj):
    """Not aliased, and not a stub either.

    A forwarding shim would let every generated `launch.sh` in the wild keep
    working, which sounds kind and means nobody migrates — and the generated
    launcher is precisely the file that goes stale, because the README expects
    people to edit it. A stub would keep a whole subcommand alive in `dg --help`
    to say one sentence. So a stale launcher fails the way any typo'd command
    fails, and everything a person could have typed it into says `dg-agent`.
    """
    res = runner.invoke(dg_app, ["--project", str(proj.root), "agent", "claim"])
    assert res.exit_code != 0


def test_the_slash_command_allows_the_binary_it_actually_runs():
    """A stale allowlist does not error — it *denies*, and the command then
    reports a tool it cannot run rather than a rename it needs."""
    text = (project.Path(__file__).resolve().parent.parent
            / "commands" / "fanout.md").read_text(encoding="utf-8")
    head = text.split("---")[1]
    assert "Bash(dg-agent:*)" in head
    assert "Bash(dg agent:*)" not in head


# ---- `dg-agent env` -------------------------------------------------------


def test_a_value_that_was_chosen_reads_as_chosen(run, monkeypatch):
    """The ordinary case, and the one the report is measured against: every
    variable set legally is reported as set, with nothing marked."""
    for name, value in (("DG_DECIDE", "evidence"), ("DG_WRITE", "launch"),
                        ("DG_AREA", "strict"), ("DG_TERSE", "on"),
                        ("DG_BUDGET", "30m"), ("DG_SILENT_AFTER", "20m")):
        monkeypatch.setenv(name, value)

    res = run("env", "--json")
    got = {v["name"]: v for v in json.loads(res.output)["variables"]}

    assert res.exit_code == 0
    assert all(got[n]["ok"] and got[n]["complaint"] is None for n in got)
    assert got["DG_DECIDE"]["effective"] == "evidence"
    assert got["DG_TERSE"]["effective"] == "400 chars"
    assert got["DG_AREA"]["reads_as"] == "only areas already in use"
    assert run("env", "--check").exit_code == 0


@pytest.mark.parametrize("name,bad,fallback", [
    ("DG_DECIDE", "nevr", "open"),
    ("DG_WRITE", "lanch", "open"),
    ("DG_AREA", "strikt", "open"),
    ("DG_TERSE", "on!", "no limit"),
])
def test_a_fail_open_typo_is_named_as_a_fallback_and_fails_the_check(
        run, monkeypatch, name, bad, fallback):
    """The whole defect, one variable at a time.

    `open` looks identical whether it was chosen or fallen into, so the report
    has to say which — and `--check` has to exit non-zero, because a launcher
    that is about to spawn a wave of agents is the one place this is still
    fixable.
    """
    monkeypatch.setenv(name, bad)

    payload = json.loads(run("env", "--json").output)
    got = {v["name"]: v for v in payload["variables"]}[name]

    assert got["ok"] is False and got["fails_open"] is True
    assert got["effective"] == fallback
    assert bad in got["complaint"] and "Set it where the agent is launched" \
        in got["complaint"]

    checked = run("env", "--check")
    assert checked.exit_code == 1
    assert bad in checked.output


def test_an_unset_variable_is_never_a_finding(run):
    """Unset is a legitimate choice and the documented default for every one of
    them, so flagging it would flag every project that has never heard of
    this."""
    res = run("env", "--check")

    assert res.exit_code == 0
    assert all(v["ok"] for v in json.loads(run("env", "--json").output)["variables"])


def test_the_report_covers_the_whole_family_including_the_area_policy(run):
    """One table, so a variable cannot be added by hand-rolling an eleventh
    `os.environ.get` somewhere and going unreported."""
    names = [v["name"] for v in json.loads(run("env", "--json").output)["variables"]]

    assert names == [v.name for v in env.VARS]
    assert "DG_AREA" in names
    assert set(names) == {"DG_AGENT", "DG_DECIDE", "DG_APPLY", "DG_WRITE",
                          "DG_EXEC_ALLOW", "DG_CONFINE", "DG_FLOOR", "DG_AREA",
                          "DG_BUDGET", "DG_TERSE", "DG_SILENT_AFTER",
                          "DG_PROJECT"}


def test_the_budget_is_shown_against_the_lease_not_the_variable(run, proj,
                                                                monkeypatch):
    """`agents.claim` records the budget on the lease so a supervisor reading
    `dg-agent list` sees the same number the agent was given. That makes the
    lease the authority and `$DG_BUDGET` a copy — so a disagreement between them
    is itself a finding, and this is the one place both are to hand."""
    name = agents.claim(proj.root, budget=1800)
    monkeypatch.setenv("DG_AGENT", name)
    monkeypatch.setenv("DG_BUDGET", "2h")

    reads = {v["name"]: v for v in
             json.loads(run("env", "--json").output)["variables"]}

    assert "left on this lease" in reads["DG_BUDGET"]["reads_as"]
    assert "the lease says 30m" in reads["DG_BUDGET"]["reads_as"]
    # Shown, not exited on: the variable parsed, and the lease is what the
    # hand-back actually reads.
    assert reads["DG_BUDGET"]["ok"] is True
    assert run("env", "--check").exit_code == 0


def test_a_project_variable_pointing_at_no_graph_is_reported(run, tmp_path,
                                                             monkeypatch):
    """The archived-env-script failure. A stale `.dgraph-fanout-env.sh` whose
    `DG_PROJECT` still pointed at a root the graph had since moved out of ran a
    whole fan-out against no store at all, and looked perfectly correct."""
    empty = tmp_path / "nowhere"
    empty.mkdir()
    monkeypatch.setenv("DG_PROJECT", str(empty))

    res = run("env", "--check")

    assert res.exit_code == 1
    assert "decisions.json" in res.output and "no graph" in res.output


def test_the_reserved_name_is_reported_as_the_bad_configuration_it_is(run,
                                                                      monkeypatch):
    """`unowned` is how this tool names ops nobody signed, so a writer cannot
    also be called that — and every `dg` call in such a session is refused. That
    is a bad *configuration*, which is this report's subject."""
    monkeypatch.setenv("DG_AGENT", pending.UNOWNED)

    res = run("env", "--check")

    assert res.exit_code == 2, "the launcher refuses the session too"
    assert pending.UNOWNED in res.output


def test_export_never_offers_the_agent_name(run, monkeypatch):
    """The one rule `render_launch` used to carry as a comment: an exported
    `$DG_AGENT` makes the launcher an agent, and its own policy then refuses
    it. `--export` is for a host that cannot take a wrapper process, and it is
    the one place that rule can be enforced rather than written down."""
    monkeypatch.setenv("DG_DECIDE", "evidence")
    monkeypatch.setenv("DG_AGENT", "brisk-beacon")

    out = run("env", "--export").output

    assert "export DG_DECIDE=evidence" in out
    assert "export DG_AGENT" not in out
    assert "per command" in out


# ---- `--plan`, and the drift it closes ------------------------------------


def _plan(proj, **over):
    """A plan file, with no floor unless a test asks for one.

    `Plan()` proposes `confine="require"`, and since `P-F2` a `dg-agent run`
    that cannot apply the declared floor refuses rather than spawning an
    unconfined child. These tests are about the other five variables, so they
    say `off` and mean it; the floor has its own tests in `test_confine.py`.
    """
    over.setdefault("confine", "off")
    spec = fanout.env_plan(fanout.Plan(**over))
    path = proj.root / "env.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return str(path)


def test_a_bad_value_in_the_plan_is_refused_before_any_agent_starts(run, proj):
    """The plan is read back by something that then spawns agents under it, so
    `"decide": "nevr"` would compose the widest policy for every child of the
    run. Refused at the launcher, where it can still be fixed."""
    bad = proj.root / "env.json"
    bad.write_text(json.dumps({"decide": "nevr"}), encoding="utf-8")

    res = run("env", "--check", "--plan", str(bad))

    assert res.exit_code == 2 and "nevr" in res.output


def test_the_check_fails_when_the_shell_contradicts_the_plan(run, proj,
                                                             monkeypatch):
    """`fanout/scout.md` asserts each policy to the agent in the second person
    and the launcher sets it separately; edit one afterwards — which the README
    expects — and the prompt goes on asserting a policy nobody is enforcing, to
    an agent with no way to check. One file feeds both artefacts, and this is
    what asserts they still agree."""
    plan = _plan(proj, decide="evidence")
    assert run("env", "--check", "--plan", plan).exit_code == 0

    monkeypatch.setenv("DG_DECIDE", "open")
    res = run("env", "--check", "--plan", plan)

    assert res.exit_code == 1
    assert "DG_DECIDE" in res.output and "open" in res.output


# ---- `dg-agent run` -------------------------------------------------------


def _echo(var: str) -> list[str]:
    return [sys.executable, "-c",
            f"import os,sys; sys.stdout.write(os.environ.get({var!r}, '<unset>'))"]


def test_the_child_gets_the_name_and_the_parent_never_does(run, proj, capfd):
    """The exported-`$DG_AGENT` failure the launcher used to warn about in a
    comment, asserted instead. A behaviour beats a comment somebody has to
    obey."""
    res = run("run", "--decide", "never", "--", *_echo("DG_AGENT"))
    out = capfd.readouterr().out

    assert res.exit_code == 0
    assert out.strip() in agents.load(proj.root), (
        f"the child was not given a claimed name: {out!r}")
    assert os.environ.get("DG_AGENT") is None, "the launcher became an agent"


def test_the_child_gets_every_composed_policy(run, capfd):
    res = run("run", "--decide", "never", "--write", "launch",
              "--area-policy", "strict", "--terse", "250",
              "--", *_echo("DG_DECIDE"))
    assert res.exit_code == 0 and capfd.readouterr().out.strip() == "never"


@pytest.mark.parametrize("flag,value,says", [
    ("--decide", "evidenced", "must be one of"),
    ("--write", "lanch", "must be one of"),
    ("--area-policy", "strikt", "must be one of"),
    ("--terse", "loose", "character count"),
    ("--budget", "half an hour", "not a budget"),
])
def test_a_bad_value_is_refused_before_anything_is_spawned(run, proj, flag,
                                                           value, says):
    """**Failing closed here does not contradict failing open there**, and the
    split is what makes that structural rather than a judgement about which code
    path you are on. The fail-open argument is about a supervisor sharing a tray
    on the path of every call; this is a launcher composing one child, once,
    standing at the terminal.

    Nothing is spawned *and* no name is claimed, so a refused launch costs
    nothing from the pool.
    """
    before = dict(agents.load(proj.root))
    res = run("run", flag, value, "--", sys.executable, "-c", "pass")

    assert res.exit_code == 2 and says in res.output
    assert agents.load(proj.root) == before, "a refused launch claimed a name"


def test_a_child_that_exits_clean_has_nothing_parked(run, proj):
    """A park filed over a finished session records a stop that never
    happened."""
    assert run("run", "--", sys.executable, "-c", "pass").exit_code == 0
    assert pending.load(proj.task_pending) == []


def _holding(proj, tid="T02"):
    """A claimed name that has actually taken a task, the way a run does:
    staged under its own name, then applied — which is what reaches
    `applying._record_holdings` and so the lease."""
    from dgraph import applying
    name = agents.claim(proj.root)
    with pending.as_owner(name):
        pending.stage_all([{"op": "set_status", "task": tid,
                            "status": "DOING"}], proj.task_pending)
    applying.apply_tasks(pending.load(proj.task_pending))
    return name


def test_a_child_stopped_at_its_budget_hands_its_work_back(run, proj):
    """The substantive gain from `dg-agent run` being the child's **parent**.

    `dg` is not in the agent's process tree and never was, so this used to be
    `timeout 1800 …` in a generated line and a `dg-agent expire` somebody
    remembered to run afterwards — a rate limit killed three scouts mid-wave in
    one real run and every one of their tasks had to be parked by hand.

    Under `run` the budget is the timeout, and the hand-back happens when the
    information is freshest: staged under the agent's own name, so `dg pending
    --agent <name>` shows it beside whatever that agent had already proposed.

    **What this does not cover, and the reason `dg-agent expire` stays.** This
    is the child dying. `run` itself being killed — a `kill -9` on the group,
    the machine going down — leaves exactly what it always did, and the backstop
    is still the procedure's last step.
    """
    name = _holding(proj)

    res = run("run", "--agent", name, "--budget", "1", "--",
              sys.executable, "-c", "import time; time.sleep(30)")

    assert res.exit_code != 0
    assert "stopped at its budget" in res.output
    parked = [op for op in pending.load(proj.task_pending)
              if op.get("status") == "PARKED"]
    assert [op["task"] for op in parked] == ["T02"]
    assert parked[0]["by"] == name
    assert "budget spent" in parked[0]["why"]


def test_a_child_that_dies_holding_work_parks_it_under_its_own_name(run, proj,
                                                                    monkeypatch):
    """The other half: a crash, not a timeout. Both are "stopped without saying
    so", which is the state a task left `DOING` cannot be told apart from work
    in progress."""
    name = _holding(proj)

    res = run("run", "--agent", name, "--",
              sys.executable, "-c", "raise SystemExit(3)")

    assert res.exit_code == 3
    parked = [op for op in pending.load(proj.task_pending)
              if op.get("status") == "PARKED"]
    assert [op["task"] for op in parked] == ["T02"]
    assert parked[0]["by"] == name, "the park was filed under the wrong writer"
    assert name in parked[0]["why"] and "exited 3" in parked[0]["why"]


def test_the_name_is_not_released_when_the_child_stops(run, proj):
    """An agent that staged a proposal is holding something a person has to
    read, so auto-release would recycle a name whose tray still matters."""
    res = run("run", "--", sys.executable, "-c", "pass")
    assert res.exit_code == 0

    held = agents.load(proj.root)
    assert len(held) == 1
    assert "stays claimed" in res.output


def test_run_composes_from_a_plan_and_flags_override_it(run, proj, capfd):
    """One file feeds the prompt and the launcher, so the launch reads the same
    remit the agent was told about. A flag still wins, because a person at a
    terminal overriding one value for one child is the case `--plan` is not."""
    plan = _plan(proj, decide="evidence", terse="on")

    run("run", "--plan", plan, "--", *_echo("DG_TERSE"))
    assert capfd.readouterr().out.strip() == "on"

    run("run", "--plan", plan, "--terse", "off", "--", *_echo("DG_TERSE"))
    assert capfd.readouterr().out.strip() == "off"


def test_nothing_to_run_is_a_refusal_and_not_an_empty_launch(run):
    res = run("run", "--decide", "never")
    assert res.exit_code == 2 and "after `--`" in res.output


# ---- what the cloud review found -----------------------------------------


@pytest.mark.parametrize("shell,plan", [
    ({"DG_BUDGET": "1800"}, {"budget": 1800}),   # `plan_env` renders `30m`
    ({"DG_BUDGET": "30m"}, {"budget": 1800}),
    ({"DG_TERSE": "400"}, {"terse": "on"}),      # both are `TERSE_DEFAULT`
    ({"DG_TERSE": "on"}, {"terse": 400}),
])
def test_two_spellings_of_one_remit_are_not_a_conflict(shell, plan):
    """`plan_env` renders a budget through `show_span`, so a plan holding
    `1800` arrives as `30m` — and `agentic/README.md` lists both as spellings
    of one value. Compared as strings they read as drift, and `launch.sh` runs
    `dg-agent env --check` under `set -euo pipefail` before the first agent, so
    a launcher that had followed the documentation had its whole fan-out
    refused over two ways of writing one number.
    """
    assert agent_cli._plan_conflicts(plan, env.readings(shell)) == []


@pytest.mark.parametrize("shell,plan", [
    ({"DG_BUDGET": "3600"}, {"budget": 1800}),
    ({"DG_DECIDE": "open"}, {"decide": "evidence"}),
    ({"DG_TERSE": "off"}, {"terse": "on"}),
])
def test_real_drift_between_shell_and_plan_still_reports(shell, plan):
    """The falsifier for the pair above: normalising must not make the check
    silent. This is the drift the command exists for — a prompt asserting a
    policy in the second person while the launcher enforces another."""
    assert agent_cli._plan_conflicts(plan, env.readings(shell))


@pytest.mark.parametrize("bad", [0, -1, -3600, True, False, "1800", 1.5])
def test_a_plan_budget_is_range_checked_and_not_only_typed(bad, tmp_path):
    """`read_env_plan` promises to refuse a bad value *"here, at the launcher,
    where it can still be fixed"* and range-checked every field but this one.

    `0` and negatives passed `isinstance(int)`, `plan_env` rendered `0` as
    `"0s"`, and `dg-agent run` then raised `BadSpan` from `env.span` — the one
    line outside the try/except that promises "nothing was spawned and no name
    was claimed". A traceback, on exactly the hand-edited-plan case this
    file's contract is for.
    """
    path = tmp_path / "env.json"
    path.write_text(json.dumps({"budget": bad}))
    with pytest.raises(ValueError):
        fanout.read_env_plan(str(path))


@pytest.mark.parametrize("good", [1, 1800, 86400, None])
def test_a_plan_budget_that_means_something_is_kept(good, tmp_path):
    """The other side, so the guard above cannot pass by refusing everything.
    `None` is no limit and is what an unbudgeted plan holds."""
    path = tmp_path / "env.json"
    path.write_text(json.dumps({"budget": good}))
    assert fanout.read_env_plan(str(path))["budget"] == good


# ---- $DG_EXEC_ALLOW ------------------------------------------------------
#
# The list the recogniser matches a command against. It is composed at launch
# like the other five and never read from `fanout/env.json` when a command is
# judged: that file sits inside the project an agent may write to, so a list
# read at gate time would be one the agent could append `bash` to between two
# commands.

def test_exec_allow_keeps_order_and_drops_duplicates():
    assert env.exec_allow("cargo git pytest cargo") == ("cargo", "git", "pytest")


def test_exec_allow_unset_is_empty_so_everything_asks():
    """The one variable here whose unreadable value **narrows**.

    An agent that drops it gets an empty list and every command escalates, so
    shedding the variable makes its own run stricter. That is why the table
    marks it `fails_open=False` while `$DG_DECIDE` beside it is True.
    """
    assert env.exec_allow("") == ()
    assert env.BY_NAME[env.EXEC_ENV].fails_open is False


def test_exec_allow_drops_command_lines_and_says_which():
    r = env.reading(env.EXEC_ENV, {env.EXEC_ENV: "cargo git|sh $(id) pytest"})
    assert r.value == ("cargo", "pytest")
    assert r.ok is False
    assert "git|sh" in r.complaint and "$(id)" in r.complaint


def test_exec_allow_round_trips_plan_to_environment():
    spec = fanout.env_plan(fanout.Plan(exec_allow=["cargo", "git"]))
    assert spec["exec_allow"] == ["cargo", "git"]
    assert fanout.plan_env(spec)[env.EXEC_ENV] == "cargo git"


def test_exec_allow_absent_from_the_environment_when_empty():
    """Absent, not `""`. The two mean the same to `env.exec_allow`, and an
    empty assignment would make `dg-agent env` report a variable somebody had
    chosen when nobody had."""
    assert env.EXEC_ENV not in fanout.plan_env(fanout.env_plan(fanout.Plan()))


def test_env_plan_file_refuses_a_command_line(tmp_path):
    spec = tmp_path / "env.json"
    spec.write_text(json.dumps({"exec_allow": ["cargo", "git commit"]}))
    with pytest.raises(ValueError) as exc:
        fanout.read_env_plan(spec)
    assert "program names" in str(exc.value)


def test_env_plan_file_refuses_a_non_list(tmp_path):
    spec = tmp_path / "env.json"
    spec.write_text(json.dumps({"exec_allow": "cargo git"}))
    with pytest.raises(ValueError):
        fanout.read_env_plan(spec)


def test_no_policy_vocabulary_is_spelled_twice(monkeypatch):
    """`P-F12`. Every entry in `VARS` reads its legal values from the module
    that owns them, so a backend added to `confine` is one `dg-agent env`
    offers. Asserted by adding one and watching the table follow, which a
    literal could not."""
    from dgraph import confine, env as _env
    assert _env.BY_NAME[confine.CONFINE_ENV].choices == confine.CONFINE_MODES
    assert _env.BY_NAME[confine.FLOOR_ENV].choices == confine.BACKENDS
    assert _env.BY_NAME[confine.FLOOR_ENV].unset == confine.BACKENDS[0]
