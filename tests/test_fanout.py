"""Setting up a fan-out, in the three places it has to work.

The property under everything here is that **the TUI and the flags produce the
same files**. A wizard that only a human could drive would be useless inside
Claude Code or opencode, and one whose two paths diverged would be worse than
useless — the person would set up a run one way and describe it the other.
"""

import json
import pathlib
import stat

import pytest
from typer.testing import CliRunner

from dataclasses import replace

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
    # Not pinned as one string: the line also carries `--floor-applied` where
    # the spawn line carried a floor, and that flag is `P-F2`'s business rather
    # than this test's.
    assert "dg-agent run" in out and "--plan fanout/env.json" in out


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

    # Derived from `ENV_FIELDS`, not listed. The literal here held the same
    # eight `env_plan` did, so the two agreed with each other and both
    # disagreed with the table — which is what let `apply` be missing from the
    # remit for as long as it was. Audit `G-F1`.
    assert set(spec) == set(fanout.ENV_FIELDS)
    # The default plan proposes an allowlist from the project's marker files,
    # so `DG_EXEC_ALLOW` is here. It is a *proposal* — `env.json` is where a
    # person cuts it down — which is why it lands in the file rather than only
    # in the launcher. `test_agent_env` pins the empty case, where the
    # variable is absent rather than assigned to nothing.
    composed = fanout.plan_env(spec)
    assert composed.pop("DG_EXEC_ALLOW").split()[:2] == ["dg", "dg-agent"]
    composed.pop("DG_CONFINE"), composed.pop("DG_FLOOR")
    assert composed == {
        "DG_DECIDE": "evidence", "DG_APPLY": "own", "DG_WRITE": "launch",
        "DG_AREA": "open", "DG_TERSE": "on", "DG_BUDGET": "30m"}


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
    # `customise` rather than a preset, so the answers below are the only thing
    # setting the policy block. A preset here would make this script assert two
    # things at once — that the collector asks in this order, and that a preset
    # fills these fields — and the second has its own tests.
    "customise",                      # remit
    "settle the search area",          # brief
    "T01",                            # focus
    "spec.md: the criteria", "",      # reads, then blank to finish
    "findings/<id>.md",               # findings
    "3", "claude",                    # agents, host
    "never", "own", "launch", "open", # decide, apply, write, area
    "45m",                            # budget
    "on",                             # terse
    "cargo pytest",                   # exec allowlist
    "require", "bwrap",               # confine, and which backend
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
    # ANSWERS is remit, brief, focus, reads, blank, findings, agents, host,
    # decide, apply, write, area, budget — so the budget is index 12, and a
    # rejected one goes there.
    answers[12:12] = ["half an hour"]
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
            app_.query_one("#exec_allow", Input).value = "cargo pytest"
            for rb in app_.query_one("#decide", RadioSet).query("RadioButton"):
                rb.value = str(rb.label) == "never"
            # The floor, set on all three surfaces rather than left at the
            # default on each — `P-F6` added these last and a test that only
            # ever saw their defaults would agree about a value nobody chose.
            for rb in app_.query_one("#confine", RadioSet).query("RadioButton"):
                rb.value = str(rb.label) == "require"
            for rb in app_.query_one("#floor", RadioSet).query("RadioButton"):
                rb.value = str(rb.label) == "bwrap"
            await pilot.pause()
            app_.action_save()
            await pilot.pause()

    asyncio.run(drive())
    # The collectors collect; assignment is the step `setup` runs after
    # every one of them, so the TUI's plan gets it here before the render.
    from_tui = fanout.render_scout(fanout.assign(app_.result, proj)[0], proj)

    res = run(proj, "setup", "--focus", "T01", "--agents", "3",
              "--brief", "settle the search area", "--budget", "45m",
              "--decide", "never", "--terse", "on",
              "--exec-allow", "cargo pytest",
              "--confine", "require", "--floor", "bwrap",
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


def test_the_launcher_carries_the_settings_dg_agent_run_cannot(proj):
    """The half of the floor that is the runner's own vocabulary. `dg-agent run`
    is host-neutral and can only prepend argv, so a backend that configures the
    runner is carried by the one line that knows which runner it is calling."""
    plan = replace(fanout.defaults(proj), confine="require", floor="host")
    line = [l for l in fanout.render_launch(plan, proj).splitlines()
            if "claude" in l][0]
    assert "--settings" in line and '"sandbox"' in line
    assert line.index("--settings") < line.index("-p")

    bare = replace(plan, floor="bwrap")
    assert "--settings" not in fanout.render_launch(bare, proj)

    off = replace(plan, confine="off")
    assert "--settings" not in fanout.render_launch(off, proj)


# ---- backend x launcher, the other three cells (`P-F2`) --------------------

def test_the_generated_line_says_it_carried_the_settings(proj):
    """`dg-agent run` refuses a floor it cannot apply, so the launcher that
    *did* apply it has to say so. The token and the injection are one act."""
    plan = replace(fanout.defaults(proj), confine="require", floor="host",
                   host="claude")
    line = [l for l in fanout.render_launch(plan, proj).splitlines()
            if "dg-agent run" in l][0]
    assert "--floor-applied" in line

    prefixed = replace(plan, floor="bwrap")
    assert "--floor-applied" not in fanout.render_launch(prefixed, proj), \
        "bwrap is prepended by dg-agent run; asserting it would excuse a floor"
    off = replace(plan, confine="off")
    assert "--floor-applied" not in fanout.render_launch(off, proj)


def test_a_runner_that_cannot_carry_a_backend_is_refused_not_generated(proj):
    """The cell that can never work: opencode reads no `--settings`, so
    `require` + `host` there is a plan with no launch that confines anything.

    Refused where it is written rather than at the run, because there is no
    answer at run time — the pair is wrong, not the invocation."""
    plan = replace(fanout.defaults(proj), confine="require", floor="host",
                   host="opencode")
    fault = fanout.plan_fault(plan)
    assert fault and "opencode" in fault and "bwrap" in fault

    assert fanout.plan_fault(replace(plan, floor="bwrap")) is None
    assert fanout.plan_fault(replace(plan, confine="off")) is None
    assert fanout.plan_fault(replace(plan, host="claude")) is None


def test_setup_refuses_to_write_a_plan_that_cannot_be_confined(proj, monkeypatch):
    from typer.testing import CliRunner
    from dgraph.agent_cli import app
    from dgraph import confine
    monkeypatch.chdir(proj.root)
    monkeypatch.setattr(confine, "available", lambda b: (True, ""))
    res = CliRunner().invoke(app, ["setup", "--host", "opencode",
                                   "--brief", "x", "--findings", "f/x.md"])
    assert res.exit_code == 2, res.output
    assert not (proj.root / fanout.OUT_DIR / "launch.sh").exists()


def test_every_host_declares_what_it_can_carry(proj):
    """Derived, not written down twice: a backend whose floor is the runner's
    own settings must be claimed by a host, and one that wraps the command must
    be claimed by none — `dg-agent run` applies those and needs no host."""
    from dgraph import confine
    for name, host in fanout.HOSTS.items():
        for backend in host.carries:
            assert confine.configures_runner(backend, proj.root), \
                f"{name} claims to carry {backend}, which needs no carrying"


# ---- the prompt covers the remit it was generated from (`P-F5`) -----------

def test_every_environment_field_reaches_the_agent(proj):
    """The completeness half, and why it is a test rather than three
    paragraphs: `exec_allow`, `confine`, `floor` and `area` were all missing at
    once, and the reason all four were missing is that nothing ever checked. A
    ninth field would have gone the same way.

    `env.json` says what a run enforces and `scout.md` is the only account of
    it the agent gets. A field in one and not the other is a rule the agent
    meets as a refusal it was told nothing about."""
    plan = replace(fanout.defaults(proj), exec_allow=["cargo", "pytest"],
                   confine="require", floor="bwrap", area="strict",
                   terse="on", budget=1800, brief="x", findings="f/x.md")
    body = fanout.render_scout(plan, proj).lower()

    #: What in the rendered prompt shows each field reached it. A field is
    #: covered when its *value* is visible, not merely its name — a prompt that
    #: said "an allowlist applies" without saying what is on it would pass a
    #: name check and tell the agent nothing.
    evidence = {
        "decide": "$dg_decide",
        "apply": "$dg_apply",
        "write": "$dg_write",
        "area": "$dg_area",
        "terse": "$dg_terse",
        "budget": "30m",
        "exec_allow": "cargo",
        "confine": "confinement floor",
        "floor": "bwrap",
    }
    assert set(evidence) == set(fanout.ENV_FIELDS), \
        "a field was added to the remit and not to what the prompt must say"
    for field_, shown in evidence.items():
        assert shown in body, \
            f"{field_} is enforced on this run and the prompt never says so"


def test_every_environment_field_is_written_to_the_plan(proj):
    """The other axis of the same table, and the one nothing read.

    `test_every_environment_field_reaches_the_agent` maps `ENV_FIELDS` to what
    must be visible in the rendered prompt. Nothing mapped it to the *file* the
    prompt claims to have been rendered from — so `env_plan` held eight of the
    nine, `apply` was the missing one, and `--preset scout` wrote a prompt
    saying `dg apply` is refused beside an `env.json` that set nothing.

    A guard on one axis is why nobody looks at the other. Audit `G-F1`."""
    got = fanout.env_plan(fanout.defaults(proj))
    assert set(got) == set(fanout.ENV_FIELDS), (
        "a field is in the remit and not in env.json: "
        f"{sorted(set(fanout.ENV_FIELDS) - set(got))}")


def test_the_remit_a_preset_writes_is_the_remit_the_child_runs_under(proj):
    """The whole path, because either guard above could pass while the path
    between them dropped a field: plan → `env.json` → `plan_env` → the child's
    environment. `scout` is the case that mattered — it is the only preset that
    moves `$DG_APPLY`, and the one somebody reaches for when they do not yet
    trust a fan-out."""
    res = run(proj, "setup", "--preset", "scout", "--brief", "x")
    assert res.exit_code == 0, res.output
    spec = json.loads((proj.root / fanout.OUT_DIR / fanout.ENV_NAME)
                      .read_text(encoding="utf-8"))
    assert spec["apply"] == "never", "the preset's apply never reached the file"
    assert fanout.plan_env(spec)["DG_APPLY"] == "never", \
        "the file's apply never reached the child's environment"
    # …and the prompt says the same thing, which is the claim the pair keeps.
    assert "$DG_APPLY=never" in (proj.root / fanout.OUT_DIR
                                 / "scout.md").read_text(encoding="utf-8")


def test_the_prompt_does_not_promise_a_scope_the_gate_refuses(proj):
    """The correctness half. `_write_prose` promised writing "freely" under
    roots holding two files that are refused by a different rule with a
    different fix."""
    from dgraph import limits
    plan = replace(fanout.defaults(proj), write="launch", brief="x")
    body = fanout.render_scout(plan, proj)
    for store in limits.protected_paths(proj.root):
        assert pathlib.Path(store).name in body, \
            "the prompt promises a scope containing a file it may not write"
    assert "goes through `dg`" in body

    # …and under `open`, where scope has nothing to say, the record still does.
    wide = fanout.render_scout(replace(plan, write="open"), proj)
    assert pathlib.Path(limits.protected_paths(proj.root)[0]).name in wide


def test_an_empty_allowlist_is_stated_as_the_blocker_it_is(proj):
    """An unconfigured allowlist means the agent cannot run `dg` itself, which
    is the whole loop. Saying so is the difference between a scout that reports
    it in the first minute and one that spends its budget guessing."""
    plan = replace(fanout.defaults(proj), exec_allow=[], brief="x")
    body = fanout.render_scout(plan, proj)
    assert "$DG_EXEC_ALLOW" in body and "cannot even run" in body


# ---- every plan field reaches every setup surface (`P-F6`) ----------------

#: What a `Plan` field is *not* asked about anywhere, and why. Both are read
#: from somewhere other than an answer — `out` is a flag about where files go,
#: and `capture` has its own checkbox rather than a value. Anything else absent
#: from a surface is a value the person setting up a fan-out cannot see.
NOT_ASKED = {"out"}


def test_the_flags_reach_every_field_of_the_plan(proj):
    """`confine` and `floor` were settable from nothing at all: no flag, no
    wizard question, no TUI field — so the only way to change the floor of a
    fan-out was to hand-edit `env.json` after the wizard had chosen for you.

    Derived from `Plan` rather than listed, so a field added tomorrow fails
    this instead of quietly repeating the same absence."""
    from dataclasses import fields
    import inspect
    from dgraph import agent_cli
    src = inspect.getsource(agent_cli.agent_setup)
    for f in fields(fanout.Plan):
        if f.name in NOT_ASKED:
            continue
        flag = "--" + f.name.replace("_", "-")
        assert flag in src or f'"{f.name}"' in src, \
            f"{f.name} is part of the remit and no flag sets it"


def test_the_json_defaults_report_every_field_the_flags_take(proj):
    """An agent inside a host cannot drive a wizard: `--json` is its only view
    of what it is about to set. It listed eight of eleven, and the three it
    left out were the three that had no flags either."""
    res = run(proj, "setup", "--json")
    assert res.exit_code == 0, res.output
    got = json.loads(res.stdout)["defaults"]
    from dataclasses import fields
    for f in fields(fanout.Plan):
        if f.name in ("brief", "reads", "capture"):
            continue
        assert f.name in got, f"--json never mentions {f.name}"


def test_both_wizards_ask_about_the_whole_remit(proj):
    """The three surfaces have to agree, because a person who uses the form and
    a person who uses the questions are setting up the same run."""
    import inspect
    from dgraph import wizard
    plain = inspect.getsource(wizard.ask)
    for name in ("exec_allow", "confine", "floor"):
        assert name in plain, f"the plain wizard never asks about {name}"

    tui = pathlib.Path(wizard.fanout.__file__).with_name("wizard_tui.py").read_text()
    for name in ("exec_allow", "confine", "floor"):
        assert f'id="{name}"' in tui, f"the TUI form has no field for {name}"


def test_a_mistyped_floor_is_refused_rather_than_defaulted(proj):
    """Like every other word flag here, and more so: a silent fallback on this
    one is an unconfined run that reports itself confined."""
    res = run(proj, "setup", "--floor", "seatbelt", "--brief", "x")
    assert res.exit_code == 2 and "seatbelt" in res.output
    res = run(proj, "setup", "--confine", "required", "--brief", "x")
    assert res.exit_code == 2 and "required" in res.output


# ---- the curated remits --------------------------------------------------


def test_the_middle_preset_is_what_the_tool_already_defaulted_to(proj):
    """`contributor` names the dial's existing position rather than moving it.

    The claim the presets rest on: they curate a dial the tool already had, so
    naming them changed nobody's run. If this fails, either a `Plan` default
    moved or a preset does — and the two have to move together or the guide's
    *(default)* is a lie.
    """
    plan = fanout.defaults(proj)
    assert fanout.preset_of(plan, proj) == fanout.DEFAULT_PRESET
    assert fanout.apply_preset(plan, "contributor", proj) == plan


def test_a_preset_answers_the_whole_policy_block(proj):
    """Every field the remit covers, set by the preset rather than left over.

    The point of a preset is that one answer settles all of them. A field that
    quietly kept whatever the plan had would be the one a person still has to
    know about, which is the state the presets exist to end.
    """
    edited = fanout.Plan(decide="open", write="open", area="strict",
                         terse="off", exec_allow=["rm"], confine="off",
                         floor="bwrap", budget=99)
    out = fanout.apply_preset(edited, "scout", proj)
    # Derived from `Preset`'s own fields rather than listed: this test's
    # docstring claims *every* field the remit covers, and it asserted five of
    # six — `apply` was added to `Preset` and to no assertion here. Audit
    # `G-F1`.
    import dataclasses
    for field_ in dataclasses.fields(fanout.Preset):
        if field_.name in ("name", "row", "exec_scope"):
            continue
        assert getattr(out, field_.name) == \
            getattr(fanout.PRESETS["scout"], field_.name), \
            f"{field_.name} varies by preset and the preset did not set it"
    assert (out.write, out.area, out.terse) == ("launch", "open", "on")
    assert out.exec_allow == [*fanout.ALWAYS, *fanout.READERS]
    # The budget is deliberately untouched: it follows the size of the work
    # rather than the remit, and the wizard asks it either way.
    assert out.budget == 99


def test_the_strictest_preset_still_lets_an_agent_read(proj):
    """`scout` is `ALWAYS + READERS`, never the empty list.

    An empty allowlist reads as the strictest option and is in fact unusable:
    every command escalates, so a person approves `cat`, and a supervisor
    approving `cat` is a supervisor who stops looking. What `scout` withholds
    is the build tools, which is where running a command becomes running the
    project's own code.
    """
    out = fanout.apply_preset(fanout.defaults(proj), "scout", proj)
    for name in ("dg", "dg-agent", "cat", "grep"):
        assert name in out.exec_allow
    for name in fanout.MARKERS["pyproject.toml"] + fanout.MARKERS[".git"]:
        assert name not in out.exec_allow


def test_a_preset_is_expanded_and_never_stored(proj):
    """`env.json` holds the values, and no preset name.

    The same argument that keeps the focus ids out of it: a name recorded
    beside the values it produced is free to disagree with them the moment
    somebody edits one, and that file exists to close exactly that gap.
    """
    res = run(proj, "setup", "--preset", "scout", "--brief", "x")
    assert res.exit_code == 0, res.output
    written = json.loads((proj.root / fanout.OUT_DIR
                          / fanout.ENV_NAME).read_text(encoding="utf-8"))
    assert "preset" not in written and "scout" not in json.dumps(written)
    assert written["decide"] == "never"


def test_a_flag_beside_a_preset_overrides_one_field(proj):
    """`--preset scout --decide open` is "that remit, except for this".

    Which requires the preset to be applied first. Applied after, it would
    silently undo the flag the launcher was more specific about.
    """
    res = run(proj, "setup", "--preset", "scout", "--decide", "open",
              "--brief", "x")
    assert res.exit_code == 0, res.output
    written = json.loads((proj.root / fanout.OUT_DIR
                          / fanout.ENV_NAME).read_text(encoding="utf-8"))
    assert written["decide"] == "open"
    assert written["exec_allow"] == [*fanout.ALWAYS, *fanout.READERS]


def test_a_mistyped_preset_is_refused_and_names_the_three(proj):
    """Refused rather than defaulted, like every other word flag here — and it
    says what the choices are, since a preset is the one flag whose values a
    newcomer has not read about yet."""
    res = run(proj, "setup", "--preset", "scot", "--brief", "x")
    assert res.exit_code == 2
    for name in fanout.PRESETS:
        assert name in res.output


def test_open_writes_under_a_floor_are_refused_where_they_are_written(proj):
    """The pair found while writing the presets, and why `--write launch` is a
    constant rather than a preference.

    A floor seals `limits.writable_roots`, which never reads `$DG_WRITE`. So
    `open` under a floor stops the gate asking and the kernel refuses anyway —
    an unexplained permission error in place of the question the refusal exists
    to ask. Nothing at run time could put it right, so it is refused where it is
    written, like the floor-under-the-wrong-runner pair beside it.
    """
    res = run(proj, "setup", "--write", "open", "--brief", "x")
    assert res.exit_code == 2, res.output
    assert "nothing was written" in res.output
    assert not (proj.root / fanout.OUT_DIR).exists()
    # …and the pair is what is wrong, not either half of it.
    assert run(proj, "setup", "--write", "open", "--confine", "off",
               "--brief", "x").exit_code == 0


def test_the_guide_prints_the_presets_the_code_defines(proj):
    """The table in `agentic/README.md` against `fanout.PRESETS`.

    A card that named a policy it did not set would be the `DG_DECIDE=nevr`
    failure wearing a friendlier name — a rule moved toward more permission by
    something nobody could read. So the wizard's cards, `dg-agent presets` and
    the guide all render from one dict, and this is what stops the prose half
    drifting out of it.
    """
    guide = (pathlib.Path(__file__).resolve().parent.parent / "agentic"
             / "README.md").read_text(encoding="utf-8")
    for preset in fanout.PRESETS.values():
        assert f"`{preset.name}`" in guide, f"the guide never names {preset.name}"
        assert preset.row in guide, f"the guide's row for {preset.name} has drifted"
        assert f"`{preset.decide}`" in guide
    for field, value in fanout.PRESET_CONSTANTS.items():
        assert f"$DG_{field.upper()}={value}" in guide, (
            f"the guide no longer says {field} is constant at {value}")


def test_presets_reports_the_same_rows(proj):
    """`dg-agent presets`, which is where a person reads them without the guide."""
    res = run(proj, "presets", "--json")
    assert res.exit_code == 0, res.output
    got = json.loads(res.stdout)
    assert got["default"] == fanout.DEFAULT_PRESET
    assert [p["name"] for p in got["presets"]] == list(fanout.PRESETS)
    assert got["constant"]["write"] == "launch"
    res = run(proj, "presets")
    assert res.exit_code == 0
    for name in fanout.PRESETS:
        assert name in res.output


def test_setup_json_offers_the_presets_to_an_agent(proj):
    """The path an agent inside a host actually takes: it cannot drive a form,
    so the curation has to reach it as data or not at all."""
    got = json.loads(run(proj, "setup", "--json").stdout)
    assert [p["name"] for p in got["presets"]] == list(fanout.PRESETS)
    assert got["default_preset"] == fanout.DEFAULT_PRESET


def test_both_wizards_offer_the_remit_first(proj):
    """Both collectors, like every other field. A preset available only in the
    form would leave the person without `textual` looking at the twelve
    questions this exists to spare them."""
    import inspect
    from dgraph import wizard
    assert "preset" in inspect.getsource(wizard._remit)
    assert "_remit" in inspect.getsource(wizard.ask)
    tui = (pathlib.Path(wizard.fanout.__file__).with_name("wizard_tui.py")
           .read_text(encoding="utf-8"))
    assert 'id="preset"' in tui, "the TUI form has no remit cards"


# ---- the tray as an approval queue ---------------------------------------


def test_an_agent_applies_its_own_ops_unless_told_otherwise(proj, monkeypatch):
    """The catch this policy exists for, pinned so it cannot drift silently.

    `$DG_DECIDE` guards what an agent may *answer*; an `add` is ungated at both
    ends, and `dg apply` writes an owned caller's own ops with nothing
    consulted. That is the documented default and a good one for a fan-out
    aimed at a frontier somebody chose — it is just not approval, which is what
    it is easily mistaken for.
    """
    from dgraph import pending
    monkeypatch.setenv("DG_AGENT", "brisk-beacon")
    monkeypatch.delenv("DG_APPLY", raising=False)
    assert pending.refuse_apply(3) is None


def test_never_refuses_the_agent_and_leaves_the_ops_where_they_are(proj,
                                                                   monkeypatch):
    from dgraph import pending
    monkeypatch.setenv("DG_AGENT", "brisk-beacon")
    monkeypatch.setenv("DG_APPLY", "never")
    why = pending.refuse_apply(4)
    assert why is not None
    assert "brisk-beacon" in why and "4 op(s) stay staged" in why


def test_the_supervisor_is_never_refused_by_the_apply_policy(proj, monkeypatch):
    """The reading every other remit rule takes: a caller with no `$DG_AGENT`
    *is* the supervisor these ops are being held for, so refusing them would
    refuse the only person who can clear the queue."""
    from dgraph import pending
    monkeypatch.delenv("DG_AGENT", raising=False)
    monkeypatch.setenv("DG_APPLY", "never")
    assert pending.refuse_apply(4) is None


def test_a_mistyped_apply_policy_widens_and_is_reported(proj, monkeypatch):
    """Fails open like its neighbours — it is read on the path of every apply,
    and a launcher's typo must not take the store from the supervisor sharing
    the tray. What makes that defensible is that `dg-agent env` reports it."""
    from dgraph import env as _env, pending
    monkeypatch.setenv("DG_AGENT", "brisk-beacon")
    monkeypatch.setenv("DG_APPLY", "nevr")
    assert _env.apply_policy() == "own"
    assert pending.refuse_apply(1) is None
    assert any(v.name == _env.APPLY_ENV and v.fails_open for v in _env.VARS)


def test_scout_is_the_preset_where_nothing_lands_unapproved(proj):
    """The name always implied it; until `$DG_APPLY` existed it did not deliver
    it, because `--decide never` leaves `dg add` entirely ungated."""
    plans = {n: fanout.apply_preset(fanout.defaults(proj), n, proj)
             for n in fanout.PRESETS}
    assert plans["scout"].apply == "never"
    assert plans["contributor"].apply == "own"
    assert plans["maintainer"].apply == "own"


def test_the_prompt_tells_the_agent_which_of_the_two_trays_it_is_in(proj):
    """The prompt used to assert *"nothing you stage is written until somebody
    applies it"* unconditionally, which was simply untrue for an agent that
    could apply its own. Both halves are now stated from the plan."""
    from dataclasses import replace as _replace
    base = _replace(fanout.defaults(proj), brief="x", findings="f/x.md")
    own = fanout.render_scout(_replace(base, apply="own"), proj)
    never = fanout.render_scout(_replace(base, apply="never"), proj)
    assert "$DG_APPLY=own" in own and "your own" in own
    assert "$DG_APPLY=never" in never and "stage only" in never.lower()
    assert "approval queue" in never


# ---- the step people skip (audit `G-F7`) ---------------------------------


def test_readiness_says_when_nothing_will_answer_an_escalation(proj):
    """Four green ticks and a launch command, for a run where every escalation
    is a refusal nobody chose.

    `dg gate` answers `ask` where it cannot decide; with no broker listening
    that `ask` reaches the host as a permission prompt, and a headless
    `claude -p` has nobody to answer it. Every check beside this one is about
    the *graph* — which is why this was missing rather than argued away."""
    checks = fanout.readiness(proj)
    broker_check = [c for c in checks if "broker" in c.label]
    assert broker_check, "readiness says nothing about the broker"
    assert not broker_check[0].ok
    assert "refused, not asked" in broker_check[0].label
    assert "dg-agent broker" in broker_check[0].fix


def test_the_broker_check_clears_when_one_is_listening(proj, monkeypatch):
    """A warning that never clears is one people learn to read past."""
    from dgraph import broker as _broker
    monkeypatch.setattr(_broker, "listening", lambda root=None: True)
    ok = [c for c in fanout.readiness(proj) if "broker" in c.label]
    assert ok and ok[0].ok and "is listening" in ok[0].label


def test_readiness_never_raises_over_the_broker(proj, monkeypatch):
    """Read-only and never raises is `readiness`'s own contract — it is the
    first thing both modes show, and a wizard that fell over while reporting
    something missing would be reporting it the hard way."""
    from dgraph import broker as _broker

    def boom(root=None):
        raise OSError("no socket here")

    monkeypatch.setattr(_broker, "listening", boom)
    checks = fanout.readiness(proj)          # must not raise
    assert [c for c in checks if "broker" in c.label][0].ok is False


def test_setup_names_the_broker_before_the_launcher(proj):
    """The last line on the screen, and the one that used to point past the
    step it depends on. `readiness` scrolls off; this does not."""
    res = run(proj, "setup", "--preset", "contributor", "--brief", "x",
              "--confine", "off")
    assert res.exit_code == 0, res.output
    tail = res.output
    assert "start a broker" in tail, \
        "setup names ./launch.sh as the next step with nothing listening"
    # …and the order is the point: the broker is named before the launcher.
    assert tail.index("start a broker") < tail.index("launch.sh`"), \
        "the launcher is offered before the thing it depends on"


def test_an_advisory_check_does_not_bar_the_run(proj):
    """`ready` is computed from the checks that *bar*, and the distinction is a
    field rather than a comment.

    It was a comment: the ready-tasks check said "a warning rather than a bar"
    and `--json` computed `ready` from every check, so the warning barred and
    the sentence saying otherwise was read by nobody. Found because the broker
    check landed beside it and turned `ready` false for a run that was fine.
    `G-F7`.
    """
    checks = fanout.readiness(proj)
    advisory = [c for c in checks if not c.bars]
    assert {"broker", "ready to claim"} <= {
        w for c in advisory for w in ("broker", "ready to claim")
        if w in c.label}, "the two advisory checks are not marked advisory"
    # No broker is listening in this fixture, so an advisory check is failing…
    assert any(not c.ok for c in advisory)
    # …and the run is still ready, which is the whole claim.
    got = json.loads(run(proj, "setup", "--json").stdout)
    assert got["ready"] is True
    assert any(c["bars"] is False for c in got["checks"]), \
        "an agent reading this door cannot tell a warning from a refusal"


def test_a_missing_store_still_bars(proj):
    """The other half — `bars` defaults to True, so a new prerequisite refuses
    until somebody argues it should not."""
    (proj.root / "tasks.json").unlink()
    checks = fanout.readiness(proj)
    tasks = [c for c in checks if "tasks.json" in c.label][0]
    assert tasks.bars and not tasks.ok
    assert json.loads(run(proj, "setup", "--json").stdout)["ready"] is False


# ---- regenerating over an edit (audit `G-F9`) ----------------------------


def test_a_rerun_refuses_to_destroy_what_a_launcher_edited(proj):
    """Both artefacts a person is *told* to edit were regenerated in silence.

    `agentic/README.md` expects `scout.md` to be edited, and `MARKERS` says the
    proposed allowlist goes into `env.json` "where it can be read back, cut
    down, and diffed next time" — while re-running `setup` is what this tool
    recommends when the graph has moved on. A launcher who had removed `cargo`
    got it back and was told nothing.
    """
    assert run(proj, "setup", "--preset", "scout").exit_code == 0
    scout = proj.root / fanout.OUT_DIR / "scout.md"
    spec = proj.root / fanout.OUT_DIR / fanout.ENV_NAME

    scout.write_text(scout.read_text(encoding="utf-8") + "\nMINE\n",
                     encoding="utf-8")
    cut = json.loads(spec.read_text(encoding="utf-8"))
    cut["exec_allow"] = [n for n in cut["exec_allow"] if n != "grep"]
    spec.write_text(json.dumps(cut, indent=2) + "\n", encoding="utf-8")

    res = run(proj, "setup", "--preset", "scout")
    assert res.exit_code == 1, res.output
    assert "nothing written" in res.output
    # Both are named, not just the first one found.
    assert "scout.md" in res.output and fanout.ENV_NAME in res.output
    assert "MINE" in scout.read_text(encoding="utf-8")
    assert "grep" not in json.loads(spec.read_text(encoding="utf-8"))["exec_allow"]
    # …and the refusal says the run they have still works, since a launcher who
    # just wanted to relaunch does not need to regenerate at all.
    assert "launch.sh" in res.output


def test_an_unedited_rerun_still_regenerates(proj):
    """The common case has to stay free. Re-running to refresh the chain after
    the graph has moved is the act this tool recommends, and a refusal there
    would be the guard costing more than it saves."""
    assert run(proj, "setup", "--preset", "scout").exit_code == 0
    res = run(proj, "setup", "--preset", "contributor")
    assert res.exit_code == 0, res.output
    assert "wrote" in res.output
    spec = json.loads((proj.root / fanout.OUT_DIR / fanout.ENV_NAME)
                      .read_text(encoding="utf-8"))
    assert spec["decide"] == "evidence", "the second plan did not land"


def test_force_regenerates_over_an_edit(proj):
    """The way out, which a refusal must always name."""
    assert run(proj, "setup", "--preset", "scout").exit_code == 0
    scout = proj.root / fanout.OUT_DIR / "scout.md"
    scout.write_text(scout.read_text(encoding="utf-8") + "\nMINE\n",
                     encoding="utf-8")
    res = run(proj, "setup", "--preset", "scout", "--force")
    assert res.exit_code == 0, res.output
    assert "MINE" not in scout.read_text(encoding="utf-8")


def test_a_fanout_generated_before_the_digest_existed_is_not_refused(proj):
    """A missing record reads as *not edited*.

    Failing closed would make the first run after upgrading a refusal nobody
    could explain, over files the tool itself wrote. The digest is evidence of
    an edit, and its absence is evidence of nothing."""
    assert run(proj, "setup", "--preset", "scout").exit_code == 0
    (proj.root / fanout.DIGEST_NAME).unlink()
    scout = proj.root / fanout.OUT_DIR / "scout.md"
    scout.write_text(scout.read_text(encoding="utf-8") + "\nMINE\n",
                     encoding="utf-8")
    assert fanout.edited(proj, fanout.defaults(proj)) == []
    assert run(proj, "setup", "--preset", "scout").exit_code == 0


# ---- the roster ----------------------------------------------------------
#
# One agent per task. By default the tasks are chosen from the graph — a
# maximal set of ready tasks no two of which collide (`cross.independent`,
# `D45`, `D46`) — and `--roster` is the exception, for a launcher who wants to
# say which. The property under all of it is that **an empty roster is
# untouched**: a plan with nothing assigned renders the artefacts it always
# did, byte for byte, which is what a run with nothing ready still gets.


def test_an_empty_roster_renders_exactly_what_it_always_did(proj):
    """The regression that would matter most, and the cheapest to miss.

    Assignment happens in `setup`, not in `defaults`, so a plan built here has
    no roster and must render unchanged — not "equivalent", identical. Both
    artefacts, because the prompt and the launcher grew a branch each."""
    plan = fanout.defaults(proj)
    assert plan.roster == [], "defaults does not assign; setup does"
    launch = fanout.render_launch(plan, proj)
    scout = fanout.render_scout(plan, proj)

    assert "for i in $(seq 1 2); do" in launch
    assert "--task" not in launch
    assert "Nothing is assigned to you." in scout
    assert "$DG_TASK" not in scout


def test_a_roster_is_the_loop_rather_than_a_count(proj):
    """`seq` would put the roster's length in the file twice — once as a
    number and once as the list it came from — which is the two-numbers
    failure `dg-agent run` was built to end."""
    plan = fanout.defaults(proj)
    launch = fanout.render_launch(
        replace(plan, roster=["T02", "T06"], agents=2), proj)

    assert "for t in T02 T06; do" in launch
    assert "seq" not in launch
    assert '--task "$t"' in launch


def test_the_assignment_reaches_the_child_and_not_the_launcher(proj):
    """`$DG_TASK` is set the way `$DG_AGENT` is: by `dg-agent run`, for the
    child alone. A bare assignment in the launcher would be a value nothing
    validates and one that outlives the run."""
    launch = fanout.render_launch(
        replace(fanout.defaults(proj), roster=["T02"], agents=1), proj)

    assert "export DG_TASK" not in launch
    assert "DG_TASK=" not in launch


def test_a_rostered_prompt_names_the_task_and_still_ends_open(proj):
    """The roster says where an agent *begins*. An agent that stopped after
    its one task would leave the queue sitting there, which is the failure the
    loop section has always argued against — so the assignment is added to
    that section rather than replacing it."""
    scout = fanout.render_scout(
        replace(fanout.defaults(proj), roster=["T02"], agents=1), proj)

    assert '`$DG_TASK` is yours' in scout
    assert 'dg task start "$DG_TASK"' in scout
    assert "Do not stop after one task." in scout, "the open loop survives"


def _ready(proj, **tasks):
    """Add TODO tasks with the given seam fields, so the fixture has more than
    one ready task. T02 is the fixture's only ready one; everything here is
    unconnected and startable."""
    store = proj.root / "tasks.json"
    data = json.loads(store.read_text())
    for tid, fields in tasks.items():
        data["tasks"].append({"id": tid, "title": tid, "area": "x",
                              "status": "TODO", **fields})
    store.write_text(json.dumps(data, indent=2))


def test_the_default_roster_is_an_independent_set_one_task_per_agent(proj):
    """With no `--roster`, each agent is assigned a ready task no other
    agent's task collides with, and the report says so. T05 writes D02 and
    T06 rests on it, so only one of them is offered; the pair is named."""
    _ready(proj, T05={"evidence_for": "D02"}, T06={"because": ["D02"]},
           T07={"because": ["D01"]})
    res = run(proj, "setup", "--agents", "2", "--brief", "x")
    assert res.exit_code == 0, res.stdout
    assert "3 of those can run side by side: T02, T05, T07" in res.stdout
    assert "assigned T02, T05 — one agent each" in res.stdout
    assert "1 more could run beside these: T07" in res.stdout
    launch = (proj.root / "fanout" / "launch.sh").read_text()
    assert "for t in T02 T05; do" in launch and "seq" not in launch
    scout = (proj.root / "fanout" / "scout.md").read_text()
    assert '`$DG_TASK` is yours' in scout
    # every assigned task's chain is in the shared prompt (D47)
    assert "T05  TODO" in scout


def test_fewer_independent_tasks_than_agents_launches_fewer_and_says_which_pair(proj):
    """K < N: the run is K agents. An unassigned N−K would read the frontier
    and take exactly the held tasks, which is the collision the set was
    computed to avoid — so the shortfall is reported with the pair to break."""
    _ready(proj, T05={"evidence_for": "D02"}, T06={"because": ["D02"]})
    res = run(proj, "setup", "--agents", "4", "--brief", "x")
    assert res.exit_code == 0, res.stdout
    assert "4 agents asked for, 2 independent task(s) ready: launching 2" in res.stdout
    assert "T06 cannot join: shares D02 with T05" in res.stdout
    assert "for t in T02 T05; do" in (proj.root / "fanout" / "launch.sh").read_text()


def test_nothing_ready_keeps_the_self_selecting_run(proj):
    """K = 0 is not a shortfall: N agents reading the frontier is the right
    run against a graph whose work is all questions, and the artefacts are the
    ones this tool has always written."""
    store = proj.root / "tasks.json"
    data = json.loads(store.read_text())
    for t in data["tasks"]:
        if t["id"] in ("T02", "T03"):    # T03 is ready once T02 is done
            t["status"] = "DONE"
    store.write_text(json.dumps(data))
    res = run(proj, "setup", "--agents", "3", "--brief", "x")
    assert res.exit_code == 0, res.stdout
    assert "assigned" not in res.stdout
    assert "for i in $(seq 1 3); do" in (proj.root / "fanout" / "launch.sh").read_text()
    assert "Nothing is assigned to you." in (proj.root / "fanout" / "scout.md").read_text()


def test_a_hand_roster_is_obeyed_and_a_colliding_pair_is_named(proj):
    """Written by hand, so obeyed as written — and the pair that may collide
    is said, with the decision it meets on and which task would move it."""
    _ready(proj, T05={"evidence_for": "D02"}, T06={"because": ["D02"]})
    res = run(proj, "setup", "--roster", "T05,T06", "--brief", "x")
    assert res.exit_code == 0, res.stdout
    assert "T05 and T06 may collide: both name D02, and T05 would move it" in res.stdout
    assert "assigned" not in res.stdout, "a hand roster is not rewritten"
    assert "for t in T05 T06; do" in (proj.root / "fanout" / "launch.sh").read_text()


def test_a_roster_sets_the_agent_count_and_a_second_number_is_refused(proj):
    """One number, not two — `dg-agent run`'s own argument about `--budget`
    and `timeout`, applied to the pair that can disagree here."""
    assert run(proj, "setup", "--roster", "T02,T03", "--brief", "x").exit_code == 0
    plan = fanout.read_env_plan(proj.root / "fanout" / fanout.ENV_NAME)
    assert "roster" not in plan, "an assignment is not part of the shared remit"
    assert "for t in T02 T03; do" in (proj.root / "fanout" / "launch.sh").read_text()

    clash = run(proj, "setup", "--roster", "T02,T03", "--agents", "3",
                "--brief", "x", "--force")
    assert clash.exit_code == 2
    assert "already says how many" in clash.stdout


def test_a_roster_that_names_work_nobody_could_start_is_refused(proj):
    """Checked where the store is open and the file is not written yet. At
    launch it would cost a spawned agent, a claimed name, and a run that
    discovers it has nothing to do."""
    missing = run(proj, "setup", "--roster", "T02,T99", "--brief", "x")
    assert missing.exit_code == 2
    assert "T99" in missing.stdout and "not in the task store" in missing.stdout

    # T01 is DONE in the fixture.
    done = run(proj, "setup", "--roster", "T01", "--brief", "x")
    assert done.exit_code == 2
    assert "nothing to start" in done.stdout

    twice = run(proj, "setup", "--roster", "T02,T02", "--brief", "x")
    assert twice.exit_code == 2
    assert "more than once" in twice.stdout


def test_blocked_and_held_work_is_said_rather_than_refused(proj):
    """`dg task start` does not refuse a blocked task, so neither does this —
    a supervisor who means to run ahead of a prerequisite is doing something
    legitimate, and so is one relaunching after a crash onto work still marked
    DOING. What is not legitimate is finding either out afterwards."""
    blocked = run(proj, "setup", "--roster", "T03", "--brief", "x")
    assert blocked.exit_code == 0, "said, not refused"
    assert "T03 waits on T02" in blocked.stdout

    held = run(proj, "setup", "--roster", "T04", "--brief", "x", "--force")
    assert held.exit_code == 0
    assert "T04 is already DOING" in held.stdout


def test_the_launcher_asks_whether_anybody_can_answer_an_escalation(proj):
    """`D53`. `agentic/QUICKSTART.md` calls broker-before-launch the step people
    skip, and until this line only prose said so.

    A warning and never a refusal — `|| true` says that in the file itself.
    A run with no broker is legal and behaves exactly as it did before one
    existed: the gate returns its own verdict and an `ask` becomes a refusal.
    What is not legal is finding that out from an agent that stopped."""
    out = fanout.render_launch(fanout.defaults(proj), proj)
    assert "dg-agent broker --check || true" in out
    check = out.index("dg-agent broker --check")
    assert out.index("dg-agent env --check") < check < out.index("dg-agent run"), \
        "after the plan check, before the first agent"


# ---- --mode session ------------------------------------------------------
#
# `D52`. `D34` split supervising from spawning: a session may hold the tray and
# broker consent, which gives up nothing, and spawning the agents in-process is
# a separate mode that declares what it makes advisory. The property under all
# of it is that `process` is untouched.


def test_process_mode_renders_exactly_what_it_always_did(proj):
    """A generated prompt in the enforced mode must be byte-identical to the
    one written before the mode existed — which is what makes a diff of two
    prompts show the mode and nothing else."""
    plan = fanout.defaults(proj)
    assert plan.mode == "process", "the enforced mode stays the default"
    scout = fanout.render_scout(plan, proj)
    assert "Where you are running" not in scout
    assert "\n\n\n" not in scout, "the empty token must close its own line up"
    assert "dg-agent run" in fanout.render_launch(plan, proj)


def test_session_mode_refuses_a_floor_its_agents_cannot_have(proj):
    """`D33`: the floor is prepended by `dg-agent run`, and a subagent the
    session spawns has no such parent. This is the one refusal the mode carries
    — everything else it gives up is stated, per `D34`."""
    plan = replace(fanout.defaults(proj), mode="session", confine="require")
    fault = fanout.plan_fault(plan)
    assert fault and "--mode session and --confine require contradict" in fault


def test_choosing_session_mode_drops_the_floor_rather_than_asserting_one(proj):
    """...but only where `--confine` was not given. `defaults` settles the floor
    before it can know the mode, so a plan that becomes `session` later would
    otherwise carry a `require` the launcher never wrote and be refused for it.
    """
    res = run(proj, "setup", "--mode", "session", "--brief", "x", "--dry-run")
    assert res.exit_code == 0, res.stdout
    clash = run(proj, "setup", "--mode", "session", "--confine", "require",
                "--brief", "x", "--dry-run")
    assert clash.exit_code == 2 and "contradict" in clash.stdout


def test_a_roster_under_session_mode_points_at_the_session_not_a_variable(proj):
    """No `dg-agent run`, so nothing sets `$DG_TASK`; the session that passed
    the roster is the only carrier. The prompt must not tell the agent a
    launcher named its task, and the losses list must say where the
    assignment went rather than that it is gone. `D61`, audit `X-F4`."""
    plan = replace(fanout.defaults(proj), mode="session", confine="off",
                   roster=["T02"], agents=1)
    scout = fanout.render_scout(plan, proj)
    assert "the launcher named yours" not in scout
    assert 'dg task start "$DG_TASK"' not in scout
    assert "named by the session that spawned you" in scout
    assert "Do not stop after one task." in scout, "the open loop survives"
    assert any("relocated" in why for what, _, why in fanout.SESSION_LOSSES
               if what == "$DG_TASK")


def test_session_mode_writes_no_launcher_and_says_why(proj):
    """There is nothing for `dg-agent run` to parent. What the file can
    usefully be is the two checks that still apply and a statement of what does
    not — rather than a script that looks like the other mode's and enforces
    none of it."""
    out = fanout.render_launch(
        replace(fanout.defaults(proj), mode="session", confine="off"), proj)
    # No *invocation* — the prose names the command to say it is absent, which
    # is the sentence the file exists for.
    assert "dg-agent run --plan" not in out and "&\n" not in out
    assert "There is no launcher for this mode" in out
    assert "dg-agent env --check" in out and "dg-agent broker --check" in out


def test_the_agent_is_told_what_it_must_carry_itself(proj):
    """The site of the three that changes behaviour rather than informing a
    human: `D32`'s mechanism is the agent prefixing `$DG_AGENT` on every call,
    and a mechanism that needs cooperation works only if it is asked for."""
    scout = fanout.render_scout(
        replace(fanout.defaults(proj), mode="session", confine="off"), proj)
    assert "spawned **inside a session**" in scout
    assert "DG_AGENT=<your name> dg" in scout
    assert "park before you stop" in scout


@pytest.mark.parametrize("host,expected", [("claude", 0), ("opencode", 1)])
def test_the_opencode_loss_is_named_only_where_it_applies(proj, host, expected):
    """Five of the losses are permanent consequences of in-process spawning.
    The sixth is opencode#5894 and may go away, so it is marked rather than
    merged — and an agent running under the other host is not told about a
    defect in a runner it is not using."""
    scout = fanout.render_scout(
        replace(fanout.defaults(proj), mode="session", confine="off",
                host=host), proj)
    assert scout.count("opencode#5894") == expected
