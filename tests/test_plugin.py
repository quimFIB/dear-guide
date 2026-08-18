"""The agent adapters, and the promises the skill makes.

Two kinds of test here. The first kind runs the hook scripts as the hosts run
them — a JSON payload on stdin, a verdict or a brief on stdout. The second kind
is anti-drift: prose about this tool has already drifted from the tool once, and
these make the drift a failure rather than a misleading instruction.
"""

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from dgraph import gate
from dgraph.check import CHECKS
from dgraph.cli import app
from dgraph.model import Graph
from dgraph.render import write

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "decisions" / "SKILL.md"
HOOKS = ROOT / "hooks"
ADAPTERS = [HOOKS / "brief.py", HOOKS / "precommit.py"]
MANIFEST = ROOT / ".claude-plugin" / "plugin.json"
MARKET = ROOT / ".claude-plugin" / "marketplace.json"


def commands() -> set[str]:
    """Every subcommand `dg` actually has."""
    return {
        c.name or c.callback.__name__.removesuffix("_cmd").rstrip("_")
        for c in app.registered_commands
    }


def frontmatter(text: str) -> dict:
    """The skill's frontmatter, without taking a YAML dependency for it."""
    _, fm, _ = text.split("---", 2)
    out, key = {}, None
    for line in fm.splitlines():
        if re.match(r"^\w[\w-]*:", line):
            key, _, rest = line.partition(":")
            out[key] = rest.strip().lstrip(">-").strip()
        elif key and line.strip():
            out[key] += " " + line.strip()
    return out


def run_hook(script: Path, payload: dict, argv=(), cwd=None, env=None):
    return subprocess.run(
        [sys.executable, str(script), *argv],
        input=json.dumps(payload), capture_output=True, text=True,
        cwd=str(cwd or ROOT), env=env, timeout=60,
    )


@pytest.fixture
def project_dir(tmp_path):
    """A real project on disk, since the adapters reach it through `dg`."""
    shutil.copy(ROOT / "demo" / "decisions.json", tmp_path / "decisions.json")
    write(Graph.load(tmp_path / "decisions.json"), tmp_path / "decision-graph.md")
    return tmp_path


# ---- the skill -----------------------------------------------------------


def test_skill_mentions_only_subcommands_that_exist():
    """The drift that already happened: hand-written instructions advertising a
    command table the tool had moved past."""
    used = set(re.findall(r"\bdg ([a-z][a-z-]*)", SKILL.read_text()))
    assert used <= commands(), sorted(used - commands())


def test_skill_does_not_restate_the_check_list():
    """`check.CHECKS` is consumed by `dg check` and by `dgraph.testing`, so a new
    check reaches every project with no list to keep in sync. A copy in the skill
    would put one back."""
    text = SKILL.read_text()
    assert not [c for c in CHECKS if f"[{c}]" in text or f"`{c}`" in text]


def test_skill_never_tells_the_agent_to_open_an_editor():
    """`--edit` and `$DG_EDIT` open a blocking editor with nobody to type in it."""
    text = SKILL.read_text()
    assert "--edit" not in text and "DG_EDIT" not in text


def test_skill_frontmatter_is_portable():
    """`name` and `description` and nothing else — the intersection every host
    accepts, so one file can serve more than one of them."""
    fm = frontmatter(SKILL.read_text())
    assert set(fm) == {"name", "description"}
    assert fm["name"] == SKILL.parent.name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", fm["name"])
    assert 0 < len(fm["description"]) <= 1024


def test_skill_description_names_the_situations_that_should_load_it():
    """Automatic invocation is decided from the description alone."""
    d = frontmatter(SKILL.read_text())["description"].lower()
    assert "decisions.json" in d
    assert "reversing" in d or "reopen" in d
    assert "use when" in d


# ---- the adapters decide nothing ----------------------------------------


def test_no_adapter_decides_anything():
    """The whole reason two hosts are cheap: the policy is in `dgraph/gate.py`
    and the brief is in `dgraph/brief.py`, and an adapter only translates. An
    adapter that parsed a command or named a check would be a second
    implementation of the rule in a language this repo does not test."""
    for path in ADAPTERS:
        tree = ast.parse(path.read_text())
        literals = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert literals & commands() <= {"brief", "gate"}, path.name
        # `ast.unparse` drops comments, so prose explaining the policy is fine
        # and only code that acts on it fails.
        code = ast.unparse(tree)
        for token in ("shlex", "--dry-run", "no-verify", "stale_view",
                      "rev-parse", "--cached", "decisions.json", "git"):
            assert token not in code, (path.name, token)


def test_hook_scripts_import_only_the_stdlib():
    """`dg` may live in a virtualenv that is not the interpreter running the
    hook, so importing the package would work here and nowhere else."""
    stdlib = set(sys.stdlib_module_names)
    for path in ADAPTERS:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name.split(".")[0] in stdlib, (path.name, name)


def test_the_fast_path_cannot_hide_a_commit():
    """`precommit.py` skips a command with no "commit" substring. That is only
    safe because the gate requires the literal token, so walk the gate's own
    positive table and prove it."""
    positives = [
        "git commit -m x", "cd sub && git commit -m x",
        "git -C sub -c user.name=x commit", "GIT_AUTHOR_NAME=x git commit -m x",
        "git add -A\ngit commit -m x", "git commit --amend --no-edit",
    ]
    for command in positives:
        assert gate.is_commit(command)
        assert "commit" in command


# ---- the SessionStart adapter -------------------------------------------


def test_brief_hook_prints_the_frontier(project_dir):
    out = run_hook(ADAPTERS[0], {"cwd": str(project_dir)}).stdout
    assert "FRONTIER" in out and "D04" in out


def test_brief_hook_is_silent_without_a_project(tmp_path):
    """A plugin is installed for a user, not a project. A directory that has
    never heard of this tool must pay nothing for having it enabled."""
    r = run_hook(ADAPTERS[0], {"cwd": str(tmp_path)})
    assert r.returncode == 0 and r.stdout == ""


def test_brief_hook_is_silent_when_dg_is_absent(project_dir, monkeypatch):
    import os
    env = dict(os.environ, PATH="")
    r = run_hook(ADAPTERS[0], {"cwd": str(project_dir)}, env=env)
    assert r.returncode == 0 and r.stdout == ""


def test_brief_hook_is_silent_when_switched_off(project_dir):
    import os
    env = dict(os.environ, DG_HOOK_OFF="1")
    r = run_hook(ADAPTERS[0], {"cwd": str(project_dir)}, env=env)
    assert r.returncode == 0 and r.stdout == ""


def test_brief_hook_asks_what_was_settled_after_a_compact(project_dir):
    out = run_hook(ADAPTERS[0], {"cwd": str(project_dir)},
                   argv=("--compacted",)).stdout
    assert "compacted" in out.lower()
    assert "FRONTIER" in out


def test_brief_hook_survives_a_payload_it_does_not_understand(project_dir):
    r = subprocess.run([sys.executable, str(ADAPTERS[0])], input="not json",
                       capture_output=True, text=True, cwd=str(project_dir))
    assert r.returncode == 0


# ---- the PreToolUse adapter --------------------------------------------


def _pre(project_dir, command, **kw):
    return run_hook(ADAPTERS[1], {
        "tool_name": "Bash", "cwd": str(project_dir),
        "tool_input": {"command": command},
    }, **kw)


def test_precommit_is_silent_on_a_clean_graph(project_dir):
    """Silence is the allow. An explicit "allow" would override the user's own
    permission rules for every git command they run."""
    r = _pre(project_dir, "git commit -m x")
    assert r.returncode == 0 and r.stdout == ""


def test_precommit_denies_a_stale_view(project_dir):
    (project_dir / "decision-graph.md").write_text("hand-edited\n")
    out = json.loads(_pre(project_dir, "git commit -m x").stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "dg render" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_precommit_asks_when_ops_are_staged(project_dir):
    (project_dir / ".dgraph-pending.json").write_text(json.dumps(
        [{"op": "set_status", "vertex": "D04", "status": "OPEN"}]))
    out = json.loads(_pre(project_dir, "git commit -m x").stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_precommit_ignores_commands_that_are_not_commits(project_dir):
    (project_dir / "decision-graph.md").write_text("hand-edited\n")
    for command in ("git log --oneline", 'echo "hello"', "ls"):
        assert _pre(project_dir, command).stdout == ""


def test_precommit_never_emits_an_allow_decision(project_dir):
    for command in ("git commit -m x", "git commit --dry-run", "git status"):
        assert '"allow"' not in _pre(project_dir, command).stdout


def test_precommit_ignores_other_tools(project_dir):
    (project_dir / "decision-graph.md").write_text("hand-edited\n")
    r = run_hook(ADAPTERS[1], {"tool_name": "Edit", "cwd": str(project_dir),
                               "tool_input": {"command": "git commit -m x"}})
    assert r.stdout == ""


def test_precommit_is_silent_when_switched_off(project_dir):
    import os
    (project_dir / "decision-graph.md").write_text("hand-edited\n")
    env = dict(os.environ, DG_HOOK_OFF="1")
    assert _pre(project_dir, "git commit -m x", env=env).stdout == ""


# ---- the manifests -----------------------------------------------------


def test_the_manifests_parse_and_agree():
    plugin = json.loads(MANIFEST.read_text())
    market = json.loads(MARKET.read_text())
    assert plugin["name"] == market["plugins"][0]["name"]
    assert market["plugins"][0]["source"] in ("./", ".")


def test_the_plugin_version_matches_the_package():
    """One repo, two installs — a `pip install` and a marketplace entry. If the
    versions can disagree silently, the first symptom is a hook going quiet."""
    import tomllib
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert json.loads(MANIFEST.read_text())["version"] == \
        pyproject["project"]["version"]


def test_every_hook_command_points_at_a_file_that_exists():
    spec = json.loads((HOOKS / "hooks.json").read_text())["hooks"]
    seen = 0
    for entries in spec.values():
        for entry in entries:
            for hook in entry["hooks"]:
                m = re.search(r'\$\{CLAUDE_PLUGIN_ROOT\}/(\S+?)"', hook["command"])
                assert m, hook["command"]
                assert (ROOT / m.group(1)).exists(), m.group(1)
                seen += 1
    assert seen == 3


def test_the_hooks_cover_both_mechanisms():
    spec = json.loads((HOOKS / "hooks.json").read_text())["hooks"]
    assert set(spec) == {"SessionStart", "PreToolUse"}
    matchers = [e.get("matcher") for e in spec["SessionStart"]]
    assert "compact" in " ".join(matchers)


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude not installed")
def test_claude_plugin_validate_is_happy():
    """Skipped when the subcommand is unavailable as well as when `claude` is:
    a CLI upgrade must not be able to turn this into a false failure."""
    probe = subprocess.run(["claude", "plugin", "validate", "--help"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("`claude plugin validate` not available")
    r = subprocess.run(["claude", "plugin", "validate", str(ROOT), "--strict"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
