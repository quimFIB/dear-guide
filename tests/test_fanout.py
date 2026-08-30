"""Setting up a fan-out, in the three places it has to work.

The property under everything here is that **the TUI and the flags produce the
same files**. A wizard that only a human could drive would be useless inside
Claude Code or opencode, and one whose two paths diverged would be worse than
useless — the person would set up a run one way and describe it the other.
"""

import json
import stat

import pytest
from typer.testing import CliRunner

from dgraph import fanout, project
from dgraph.agent_cli import app

runner = CliRunner()


@pytest.fixture
def both_stores(tmp_path, monkeypatch):
    """A project with both stores. Local, like the one in
    `test_staging_atomicity.py` — neither is general enough for conftest, and
    this one wants T01 startable rather than a committed tree."""
    from tests.conftest import FIXTURE, TASK_FIXTURE
    (tmp_path / "decisions.json").write_text(json.dumps(FIXTURE, indent=2),
                                             encoding="utf-8")
    (tmp_path / "tasks.json").write_text(json.dumps(TASK_FIXTURE, indent=2),
                                         encoding="utf-8")
    monkeypatch.setattr(project, "_override", tmp_path)
    return tmp_path


@pytest.fixture
def proj(both_stores, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("DG_AGENT", raising=False)
    return project.Project(both_stores)


def run(proj, *args, input=None):
    """`dg-agent`, not `dg`. The wizard moved with the launcher: `dg` is for the
    graphs, and everything that composes an agent's environment or spawns
    something to run under it is the other binary's."""
    return runner.invoke(app, ["--project", str(proj.root), *args],
                         input=input)


# ---- the artefacts -------------------------------------------------------


def test_no_blank_survives_the_render(proj):
    """The template ships around twenty ⟨…⟩ and the whole point is that a
    generated prompt has none: a blank left in is an instruction to the agent
    to go looking for something that was never filled."""
    plan = fanout.defaults(proj)
    out = fanout.render_scout(plan, proj)
    assert "⟨" not in out and "⟩" not in out


def test_the_fill_in_instructions_are_stripped(proj):
    """The template opens with an HTML comment telling a person to fill in
    every blank. It has been filled in."""
    assert not fanout.render_scout(fanout.defaults(proj), proj).startswith("<!--")


def test_the_chain_is_pasted_verbatim(proj):
    """The single most valuable thing in the prompt, and the one a fresh
    context cannot reconstruct. A paraphrase of a premise is a premise nobody
    can check."""
    from dataclasses import replace
    from dgraph import context as _context

    plan = replace(fanout.defaults(proj), focus=["T01"])
    want = _context.text(_context.data(proj, "T01")).rstrip()
    assert want in fanout.render_scout(plan, proj)


def test_an_unreadable_focus_id_says_so_rather_than_failing(proj):
    """Setup is the first thing anyone runs. It must not be the thing that
    crashes on a typo."""
    from dataclasses import replace
    out = fanout.render_scout(replace(fanout.defaults(proj), focus=["T99"]),
                              proj)
    assert "T99" in out


def test_the_policies_reach_the_prompt_as_prose(proj):
    """An agent told `$DG_DECIDE=never` and nothing else does not know a
    refusal at stage time is the policy rather than a broken tool."""
    from dataclasses import replace
    plan = replace(fanout.defaults(proj), decide="never", write="launch")
    out = fanout.render_scout(plan, proj)
    assert "back to a person" in out
    assert "asks the person" in out and "not a broken tool" in out


def test_the_launcher_spells_no_environment_and_no_timeout(proj):
    """The two things that used to be in this file, and both had to go.

    A bare `DG_DECIDE=evidence` in a shell line is a value nothing validates,
    and the policy variables **fail open** — mistype one and it is read as the
    widest setting, silently. And `--budget 30m` beside `timeout 1800` was two
    independent numbers saying one thing, agreeing in generated output and
    only until somebody edited the file, which the README expects.

    `dg-agent run` replaces both: it validates before it spawns, it is the
    child's parent so the budget *is* the timeout, and it sets `$DG_AGENT` for
    that child alone — the rule this file used to carry as a comment.
    """
    out = fanout.render_launch(fanout.defaults(proj), proj)

    assert "export DG_AGENT" not in out
    assert "DG_AGENT=" not in out, "a bare assignment is a value nothing checks"
    assert "timeout " not in out, "the budget is the timeout now"
    assert "dg-agent run --plan fanout/env.json" in out


def test_the_launcher_checks_the_plan_before_any_agent_starts(proj):
    """`dg-agent env --check --plan` first, so a typo in the remit is caught
    before a wave of agents has filed proposals under the widest policy there
    is — and so the prompt's claims and the launcher's settings are asserted to
    still agree."""
    out = fanout.render_launch(fanout.defaults(proj), proj)
    check = out.index("dg-agent env --check --plan fanout/env.json")
    assert check < out.index("dg-agent run"), (
        "the check has to run before the loop, not after it")


def test_the_capture_path_is_absolute(proj):
    """It lives in the dear-guide checkout, not in the project being fanned
    out — `$PWD/agentic/bin` found nothing and recorded nothing while looking
    like it had worked."""
    from dataclasses import replace
    out = fanout.render_launch(replace(fanout.defaults(proj), capture=True), proj)
    if fanout.capture_bin() is not None:
        assert str(fanout.capture_bin()) in out
        assert '$PWD/agentic' not in out


def test_write_produces_three_files_and_marks_the_launcher_runnable(proj):
    written = fanout.write(fanout.defaults(proj), proj)
    assert [p.name for p in written] == ["scout.md", "launch.sh", "env.json"]
    assert all(p.exists() for p in written)
    assert written[1].stat().st_mode & stat.S_IXUSR


def test_the_plan_is_the_remit_and_nothing_else(proj):
    """`env.json` holds the environment and stops there. A plan file that also
    carried the focus ids, the host and the brief would be a second copy of
    `scout.md`, free to disagree with it — which is the failure it is being
    written to close, arrived at from the other direction."""
    written = fanout.write(fanout.defaults(proj), proj)
    spec = json.loads(written[2].read_text(encoding="utf-8"))

    assert set(spec) == {"decide", "write", "area", "terse", "budget",
                         "exec_allow", "confine", "floor"}
    # The default plan proposes an allowlist from the project's marker files,
    # so `DG_EXEC_ALLOW` is here. It is a *proposal* — `env.json` is where a
    # person cuts it down — which is why it lands in the file rather than only
    # in the launcher. `test_agent_env` pins the empty case, where the
    # variable is absent rather than assigned to nothing.
    composed = fanout.plan_env(spec)
    assert composed.pop("DG_EXEC_ALLOW").split()[:2] == ["dg", "dg-agent"]
    composed.pop("DG_CONFINE"), composed.pop("DG_FLOOR")
    assert composed == {
        "DG_DECIDE": "evidence", "DG_WRITE": "launch", "DG_AREA": "open",
        "DG_TERSE": "on", "DG_BUDGET": "30m"}


def test_the_plan_is_read_back_and_a_bad_value_in_it_is_refused(proj, tmp_path):
    """Checked rather than trusted, because this file's whole purpose is to be
    read back by something that then spawns agents under it: `"decide":
    "nevr"` would compose the widest policy for every child of the run."""
    good = tmp_path / "env.json"
    good.write_text(json.dumps({"decide": "evidence", "budget": 1800}))
    assert fanout.read_env_plan(good)["decide"] == "evidence"

    for bad, says in (({"decide": "nevr"}, "not one of"),
                      ({"terse": "on!"}, "not `on`"),
                      ({"budget": "30m"}, "seconds"),
                      ({"focus": ["T01"]}, "unknown key")):
        (tmp_path / "bad.json").write_text(json.dumps(bad))
        with pytest.raises(ValueError, match=says):
            fanout.read_env_plan(tmp_path / "bad.json")


def test_a_null_budget_is_absent_rather_than_the_word_infinite(proj):
    """Unset is what the tool documents as no limit. Writing the word would
    make an unset variable and a deliberate `infinite` render differently in
    `dg-agent env` while meaning the same thing."""
    assert "DG_BUDGET" not in fanout.plan_env({"budget": None, "decide": "open"})


# ---- the three doors -----------------------------------------------------


def test_json_reports_readiness_and_what_it_must_still_ask(proj):
    """The door an agent inside Claude Code uses: it cannot drive a TUI, so it
    reads what the graph already answers and asks the person the rest."""
    res = run(proj, "setup", "--json")
    assert res.exit_code == 0
    payload = json.loads(res.stdout)
    assert payload["ready"] is True
    assert payload["defaults"]["decide"] in ("open", "evidence", "never")
    assert "--brief" in payload["asks"]


def test_flags_alone_write_the_files(proj):
    res = run(proj, "setup", "--focus", "T01", "--agents", "3",
              "--brief", "settle the search area", "--budget", "45m")
    assert res.exit_code == 0, res.output
    scout = (proj.root / fanout.OUT_DIR / "scout.md").read_text()
    assert "settle the search area" in scout and "45m" in scout


def test_a_bad_policy_is_refused_rather_than_defaulted(proj):
    """A launcher that typed `--decide evidenced` means to constrain its
    agents; running them unconstrained is the failure the flag prevents."""
    res = run(proj, "setup", "--decide", "evidenced")
    assert res.exit_code == 2 and "must be one of" in res.output
    assert not (proj.root / fanout.OUT_DIR).exists()


def test_dry_run_writes_nothing(proj):
    res = run(proj, "setup", "--focus", "T01", "--dry-run")
    assert res.exit_code == 0
    assert "scout.md" in res.output
    assert not (proj.root / fanout.OUT_DIR).exists()


def test_a_project_with_no_graph_is_refused_with_the_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(project, "_override", tmp_path)
    res = runner.invoke(app, ["--project", str(tmp_path), "setup"])
    assert res.exit_code == 2
    assert "dg init" in res.output


# ---- the two paths must not diverge --------------------------------------


ANSWERS = [
    "settle the search area",          # brief
    "T01",                            # focus
    "spec.md: the criteria", "",      # reads, then blank to finish
    "findings/<id>.md",               # findings
    "3", "claude",                    # agents, host
    "never", "launch", "open",        # decide, write, area
    "45m",                            # budget
    "on",                             # terse
    "n",                              # capture
    "y",                              # write the files
]


def test_the_plain_collector_asks_and_writes(proj, monkeypatch):
    """Interactive setup with no optional dependency at all. `rich` is already
    a hard requirement, so this path always exists — which is the whole point
    of it, since a wizard available only to people who installed an extra is
    not the easy way in it claims to be."""
    from dgraph import cli
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    res = run(proj, "setup", "--plain", input="\n".join(ANSWERS) + "\n")
    assert res.exit_code == 0, res.output
    scout = (proj.root / fanout.OUT_DIR / "scout.md").read_text()
    assert "settle the search area" in scout and "45m" in scout


def test_declining_the_summary_writes_nothing(proj, monkeypatch):
    """The last chance to back out, and the reason the summary is there rather
    than the answers simply being applied."""
    from dgraph import cli
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    res = run(proj, "setup", "--plain",
              input="\n".join(ANSWERS[:-1] + ["n"]) + "\n")
    assert res.exit_code == 1 and "cancelled" in res.output
    assert not (proj.root / fanout.OUT_DIR).exists()


def test_no_terminal_names_the_flags_rather_than_aborting(proj, monkeypatch):
    """With nothing on the other end, prompting gets EOF and click prints a
    bare `Aborted.` — true, and useless to an agent that needed to be told
    flags exist. This is the case inside Claude Code."""
    from dgraph import cli
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    res = run(proj, "setup")
    assert res.exit_code == 2
    assert "--json" in res.output and "flags" in res.output


def test_a_bad_budget_is_re_asked_rather_than_swallowed(proj, monkeypatch):
    """Free text, so it is the one answer that can be wrong. Silently keeping
    the default would run the fan-out on a number nobody chose."""
    from dgraph import cli
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers = list(ANSWERS)
    # ANSWERS is brief, focus, reads, blank, findings, agents, host, decide,
    # write, area, budget — so the budget is index 10, and a rejected one goes
    # there.
    answers[10:10] = ["half an hour"]
    res = run(proj, "setup", "--plain", input="\n".join(answers) + "\n")
    assert res.exit_code == 0, res.output
    assert "is not a budget" in res.output


def test_all_three_collectors_produce_identical_files(proj, monkeypatch):
    """The invariant the whole split rests on.

    Each collector fills a `fanout.Plan` and does nothing else; `fanout.py`
    turns a plan into files. So the same answers given any of the three ways
    must produce the same bytes — otherwise a run set up in the terminal, one
    set up at the prompt, and one set up from inside an agent are three
    different runs, and the docs can only describe one of them.
    """
    import asyncio

    pytest.importorskip("textual", reason="the tui extra is not installed")
    from textual.widgets import Input, RadioSet, TextArea

    from dgraph import wizard_tui

    app_ = wizard_tui.Wizard(fanout.defaults(proj), proj)

    async def drive():
        async with app_.run_test() as pilot:
            app_.query_one("#brief", TextArea).text = "settle the search area"
            app_.query_one("#focus", Input).value = "T01"
            app_.query_one("#findings", Input).value = "findings/<id>.md"
            app_.query_one("#reads", TextArea).text = "spec.md: the criteria"
            app_.query_one("#budget", Input).value = "45m"
            app_.query_one("#terse", Input).value = "on"
            app_.query_one("#agents", Input).value = "3"
            for rb in app_.query_one("#decide", RadioSet).query("RadioButton"):
                rb.value = str(rb.label) == "never"
            await pilot.pause()
            app_.action_save()
            await pilot.pause()

    asyncio.run(drive())
    from_tui = fanout.render_scout(app_.result, proj)

    res = run(proj, "setup", "--focus", "T01", "--agents", "3",
              "--brief", "settle the search area", "--budget", "45m",
              "--decide", "never", "--terse", "on",
              "--read", "spec.md:the criteria",
              "--findings", "findings/<id>.md")
    assert res.exit_code == 0, res.output
    from_flags = (proj.root / fanout.OUT_DIR / "scout.md").read_text()
    assert from_tui == from_flags

    from dgraph import cli
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    (proj.root / fanout.OUT_DIR / "scout.md").unlink()
    res = run(proj, "setup", "--plain",
              input="\n".join(ANSWERS) + "\n")
    assert res.exit_code == 0, res.output
    from_plain = (proj.root / fanout.OUT_DIR / "scout.md").read_text()
    assert from_plain == from_flags


def test_the_missing_textual_hint_keeps_the_extra_it_names():
    """`[tui]` is rich markup unless escaped, and the first version of this
    message rendered as `pip install 'dear-guide'` — an install hint that
    silently drops the extra it is telling you to install.

    Rendered through a real console rather than reached by blocking the import:
    `sys.modules["textual"] = None` does not stop `from textual.app import App`
    once another test has cached the submodule, and the test then launches a
    TUI that blocks forever waiting on a terminal.
    """
    import io

    from rich.console import Console

    from dgraph import agent_cli

    out = io.StringIO()
    Console(file=out, width=200).print(agent_cli.TUI_HINT)
    assert "dear-guide[tui]" in out.getvalue()


# ---- $DG_TERSE -----------------------------------------------------------

from dataclasses import replace          # noqa: E402  (section-local, as above)


def test_the_launcher_sets_the_field_limit(proj):
    """The whole contract with the host is environment variables, and a policy
    the launcher does not set is a policy nothing enforces."""
    plan = replace(fanout.defaults(proj), terse="250")
    # In the plan the launcher names, which is where the remit lives now — the
    # launcher spells no assignment at all.
    assert fanout.env_plan(plan)["terse"] == "250"
    assert "fanout/env.json" in fanout.render_launch(plan, proj)


def test_the_prompt_says_which_limit_is_in_force(proj):
    """An agent told the rule but not the policy reads a stage-time refusal as
    a broken tool — the argument `$DG_DECIDE` already makes in this template."""
    scout = fanout.render_scout(replace(fanout.defaults(proj), terse="250"), proj)
    assert "$DG_TERSE=250" in scout and "250 characters" in scout
    assert "⟨" not in scout


def test_off_is_stated_as_a_request_rather_than_hidden(proj):
    """A fan-out with no limit still gets the convention, because the rule is
    about duplicating the graph rather than about the count."""
    scout = fanout.render_scout(replace(fanout.defaults(proj), terse="off"), proj)
    assert "$DG_TERSE=off" in scout and "request rather than a refusal" in scout


def test_a_bad_field_limit_is_refused_rather_than_defaulted(proj):
    """`--decide evidenced` reasoning: a launcher that typed this meant to
    constrain its agents, and running them unconstrained is the failure the
    flag exists to prevent."""
    res = run(proj, "setup", "--terse", "loose", "--brief", "x")
    assert res.exit_code == 2 and "--terse" in res.output


# ---- what a project looks like it needs -----------------------------------
#
# The first setup field answered by the filesystem rather than by the graph,
# and the reason a per-run list beats a committed one: a list written once goes
# stale in a repository nobody re-reads it in, and a derived one is recomputed
# every run and cannot.

def test_the_loop_is_always_proposed(proj):
    """An agent that had to ask before `dg show` would be asking about the
    commands a fan-out is made of."""
    names = fanout.propose_exec_allow(proj)
    assert names[:2] == ["dg", "dg-agent"]


def test_readers_are_proposed_because_reads_are_never_judged(proj):
    """A `cat` that had to be approved would obey the rule in letter while
    contradicting it in spirit. Redirection does not widen them — `cat a > b`
    is composition, and the recogniser sends it to a person anyway."""
    names = fanout.propose_exec_allow(proj)
    assert {"ls", "cat", "grep"} <= set(names)


def test_find_and_sed_are_not_proposed(proj):
    """Their absence is not an oversight for whoever notices it to fix.
    `find -delete` deletes and `sed -i` rewrites in place — and `sed -i` on the
    decision store is the exact move `limits.protected_paths` exists to stop."""
    names = fanout.propose_exec_allow(proj)
    assert "find" not in names and "sed" not in names


@pytest.mark.parametrize("marker, expected", [
    ("Cargo.toml", "cargo"),
    ("pyproject.toml", "pytest"),
    ("package.json", "npm"),
    ("Makefile", "make"),
    ("go.mod", "go"),
])
def test_a_marker_file_proposes_its_toolchain(proj, marker, expected):
    (proj.root / marker).write_text("")
    assert expected in fanout.propose_exec_allow(proj)


def test_nothing_is_proposed_twice(proj):
    """`pyproject.toml` and `requirements.txt` both mean python3, and a list
    naming it twice would read as two decisions."""
    (proj.root / "pyproject.toml").write_text("")
    (proj.root / "requirements.txt").write_text("")
    names = fanout.propose_exec_allow(proj)
    assert len(names) == len(set(names))
    assert names.count("python3") == 1


def test_the_default_plan_carries_the_proposal(proj):
    (proj.root / "Cargo.toml").write_text("")
    assert "cargo" in fanout.defaults(proj).exec_allow
