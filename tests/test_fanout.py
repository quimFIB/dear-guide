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
from dgraph.cli import app

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


def test_the_launcher_never_exports_the_agent_name(proj):
    """The one rule that makes an orchestrator work: a per-command assignment.
    An exported $DG_AGENT makes the launcher an agent, and its own policy then
    refuses it."""
    out = fanout.render_launch(fanout.defaults(proj), proj)
    assert "export DG_AGENT" not in out
    assert "DG_AGENT=$(dg agent claim" in out


def test_the_launcher_pairs_the_budget_with_a_timeout(proj):
    """`dg` is not in the agent's process tree. A budget with no `timeout` is
    a number nothing acts on until the run is already over."""
    from dataclasses import replace
    out = fanout.render_launch(replace(fanout.defaults(proj), budget=1800), proj)
    assert "timeout 1800" in out and "--budget 30m" in out

    none = fanout.render_launch(replace(fanout.defaults(proj), budget=None), proj)
    assert "timeout" not in none and "--budget" not in none


def test_the_capture_path_is_absolute(proj):
    """It lives in the dear-guide checkout, not in the project being fanned
    out — `$PWD/agentic/bin` found nothing and recorded nothing while looking
    like it had worked."""
    from dataclasses import replace
    out = fanout.render_launch(replace(fanout.defaults(proj), capture=True), proj)
    if fanout.capture_bin() is not None:
        assert str(fanout.capture_bin()) in out
        assert '$PWD/agentic' not in out


def test_write_produces_both_files_and_marks_the_launcher_runnable(proj):
    written = fanout.write(fanout.defaults(proj), proj)
    assert [p.name for p in written] == ["scout.md", "launch.sh"]
    assert all(p.exists() for p in written)
    assert written[1].stat().st_mode & stat.S_IXUSR


# ---- the three doors -----------------------------------------------------


def test_json_reports_readiness_and_what_it_must_still_ask(proj):
    """The door an agent inside Claude Code uses: it cannot drive a TUI, so it
    reads what the graph already answers and asks the person the rest."""
    res = run(proj, "agent", "setup", "--json")
    assert res.exit_code == 0
    payload = json.loads(res.stdout)
    assert payload["ready"] is True
    assert payload["defaults"]["decide"] in ("open", "evidence", "never")
    assert "--brief" in payload["asks"]


def test_flags_alone_write_the_files(proj):
    res = run(proj, "agent", "setup", "--focus", "T01", "--agents", "3",
              "--brief", "settle the search area", "--budget", "45m")
    assert res.exit_code == 0, res.output
    scout = (proj.root / fanout.OUT_DIR / "scout.md").read_text()
    assert "settle the search area" in scout and "45m" in scout


def test_a_bad_policy_is_refused_rather_than_defaulted(proj):
    """A launcher that typed `--decide evidenced` means to constrain its
    agents; running them unconstrained is the failure the flag prevents."""
    res = run(proj, "agent", "setup", "--decide", "evidenced")
    assert res.exit_code == 2 and "must be one of" in res.output
    assert not (proj.root / fanout.OUT_DIR).exists()


def test_dry_run_writes_nothing(proj):
    res = run(proj, "agent", "setup", "--focus", "T01", "--dry-run")
    assert res.exit_code == 0
    assert "scout.md" in res.output
    assert not (proj.root / fanout.OUT_DIR).exists()


def test_a_project_with_no_graph_is_refused_with_the_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(project, "_override", tmp_path)
    res = runner.invoke(app, ["--project", str(tmp_path), "agent", "setup"])
    assert res.exit_code == 2
    assert "dg init" in res.output


# ---- the two paths must not diverge --------------------------------------


ANSWERS = [
    "settle the search area",          # brief
    "T01",                            # focus
    "spec.md: the criteria", "",      # reads, then blank to finish
    "findings/<id>.md",               # findings
    "3", "claude",                    # agents, host
    "never", "launch", "45m",         # decide, write, budget
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
    res = run(proj, "agent", "setup", "--plain", input="\n".join(ANSWERS) + "\n")
    assert res.exit_code == 0, res.output
    scout = (proj.root / fanout.OUT_DIR / "scout.md").read_text()
    assert "settle the search area" in scout and "45m" in scout


def test_declining_the_summary_writes_nothing(proj, monkeypatch):
    """The last chance to back out, and the reason the summary is there rather
    than the answers simply being applied."""
    from dgraph import cli
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    res = run(proj, "agent", "setup", "--plain",
              input="\n".join(ANSWERS[:-1] + ["n"]) + "\n")
    assert res.exit_code == 1 and "cancelled" in res.output
    assert not (proj.root / fanout.OUT_DIR).exists()


def test_no_terminal_names_the_flags_rather_than_aborting(proj, monkeypatch):
    """With nothing on the other end, prompting gets EOF and click prints a
    bare `Aborted.` — true, and useless to an agent that needed to be told
    flags exist. This is the case inside Claude Code."""
    from dgraph import cli
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    res = run(proj, "agent", "setup")
    assert res.exit_code == 2
    assert "--json" in res.output and "flags" in res.output


def test_a_bad_budget_is_re_asked_rather_than_swallowed(proj, monkeypatch):
    """Free text, so it is the one answer that can be wrong. Silently keeping
    the default would run the fan-out on a number nobody chose."""
    from dgraph import cli
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers = list(ANSWERS)
    # ANSWERS is brief, focus, reads, blank, findings, agents, host, decide,
    # write, budget — so the budget is index 9, and a rejected one goes there.
    answers[9:9] = ["half an hour"]
    res = run(proj, "agent", "setup", "--plain", input="\n".join(answers) + "\n")
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

    res = run(proj, "agent", "setup", "--focus", "T01", "--agents", "3",
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
    res = run(proj, "agent", "setup", "--plain",
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

    from dgraph import cli

    out = io.StringIO()
    Console(file=out, width=200).print(cli.TUI_HINT)
    assert "dear-guide[tui]" in out.getvalue()


# ---- $DG_TERSE -----------------------------------------------------------

from dataclasses import replace          # noqa: E402  (section-local, as above)


def test_the_launcher_sets_the_field_limit(proj):
    """The whole contract with the host is environment variables, and a policy
    the launcher does not set is a policy nothing enforces."""
    plan = replace(fanout.defaults(proj), terse="250")
    assert "DG_TERSE=250" in fanout.render_launch(plan, proj)


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
    res = run(proj, "agent", "setup", "--terse", "loose", "--brief", "x")
    assert res.exit_code == 2 and "--terse" in res.output
