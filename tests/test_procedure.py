"""A project's own decision procedure: `PROCEDURE.md` beside the store.

The skill ships the model and a set of defaults; a project that decides
differently — the model drafts and the owner's verdict is the review, say —
writes that down once, and `dg brief` names the file so every host's session
starts knowing it exists. It governs a session with a person in it: under
`$DG_AGENT` the launcher's policy governs, and the brief does not name it, so
the scope is a property of the channel rather than a sentence to remember.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dgraph import brief, project
from dgraph.cli import app

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def run(store, monkeypatch):
    monkeypatch.delenv("DG_AGENT", raising=False)

    def go(*args):
        monkeypatch.setenv("COLUMNS", "200")
        return runner.invoke(app, ["--project", str(store), *args])
    return go


@pytest.fixture
def written(store):
    path = store / project.PROCEDURE_NAME
    path.write_text("# How this project decides\n\nThe owner closes.\n",
                    encoding="utf-8")
    return path


# ---- the brief names it -----------------------------------------------------


def test_the_brief_names_the_procedure_in_a_session_with_a_person(run, written):
    out = run("brief").output
    line = next(l for l in out.splitlines() if l.startswith("PROCEDURE"))
    assert str(written) in line
    # Near the top, before the frontier: it governs how everything below is
    # recorded, and a brief is read from the top and clipped from the bottom.
    assert out.index("PROCEDURE") < out.index("FRONTIER")


def test_the_brief_says_nothing_of_a_procedure_under_a_launch(run, written,
                                                              monkeypatch):
    """Under `$DG_AGENT` the launcher's policy governs. Naming the file there
    would hand an agent two rule sets that can disagree, one of them written
    for a conversation it is not in."""
    monkeypatch.setenv("DG_AGENT", "a")
    out = run("brief").output
    assert "PROCEDURE" not in out
    assert json.loads(run("brief", "--json").output)["procedure"] is None


def test_a_project_without_one_pays_nothing(run, store):
    out = run("brief").output
    assert "PROCEDURE" not in out
    assert json.loads(run("brief", "--json").output)["procedure"] is None


def test_the_json_brief_carries_the_path(run, written):
    assert json.loads(run("brief", "--json").output)["procedure"] == str(written)


def test_a_task_only_project_is_named_it_too(tmp_path, monkeypatch):
    from tests.conftest import TASK_FIXTURE
    monkeypatch.delenv("DG_AGENT", raising=False)
    (tmp_path / "tasks.json").write_text(json.dumps(TASK_FIXTURE),
                                         encoding="utf-8")
    (tmp_path / project.PROCEDURE_NAME).write_text("# x\n", encoding="utf-8")
    proj = project.Project(tmp_path)
    assert "PROCEDURE" in brief.text(proj)


def test_no_graph_at_all_reports_no_procedure(tmp_path):
    assert brief.data(project.Project(tmp_path))["procedure"] is None


# ---- dg procedure -----------------------------------------------------------


def test_dg_procedure_prints_the_projects_file(run, written):
    res = run("procedure")
    assert res.exit_code == 0
    assert "The owner closes." in res.output


def test_dg_procedure_without_one_says_how_to_start(run, store):
    """A fact, not a failure — the `dg find` rule: nothing written is an
    ordinary state for a project, and a slash command that runs this must not
    come back as an error."""
    res = run("procedure")
    assert res.exit_code == 0
    assert "no PROCEDURE.md" in res.output
    assert "dg procedure --template" in res.output


def test_dg_procedure_under_a_launch_says_it_is_not_in_force(run, written,
                                                            monkeypatch):
    monkeypatch.setenv("DG_AGENT", "a")
    res = run("procedure")
    assert res.exit_code == 0
    assert "not in force" in res.output and "$DG_AGENT" in res.output


def test_the_template_needs_no_project(tmp_path, monkeypatch):
    """It is what a project starts from, so it cannot require one to exist."""
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["procedure", "--template"])
    assert res.exit_code == 0, res.output
    for section in ("## What gets recorded", "## Where the argument lives",
                    "## Who closes", "## How a question is settled",
                    "## Shelves", "## Commits", "## Not to do"):
        assert section in res.output, section
    assert "$DG_AGENT" in res.output


def test_the_template_ships_as_package_data():
    """An installed `dg` has it, the way it has the fan-out prompts."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"templates/*.md"' in pyproject
    assert (ROOT / "dgraph" / "templates" / project.PROCEDURE_NAME).is_file()


# ---- what the agent is told -------------------------------------------------


def test_the_skill_says_the_procedure_wins_and_where_it_does_not():
    skill = (ROOT / "skills" / "dear-guide" / "SKILL.md").read_text(
        encoding="utf-8")
    section = skill.split("## This project's own procedure")[1].split("\n## ")[0]
    assert "PROCEDURE.md" in section
    assert "$DG_AGENT" in section
    # And at the default it overrides, where the conflict actually bites.
    apply_own = skill.split("Apply your own work")[1][:300]
    assert "procedure" in apply_own


def test_the_slash_command_interviews_rather_than_fills_in():
    text = (ROOT / "commands" / "procedure.md").read_text(encoding="utf-8")
    assert "!`dg procedure --template`" in text
    assert "one at a time" in text
