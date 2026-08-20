"""The agent adapters, and the promises the skill makes.

Two kinds of test here. The first kind runs the hook scripts as the hosts run
them — a JSON payload on stdin, a verdict or a brief on stdout. The second kind
is anti-drift: prose about this tool has already drifted from the tool once, and
these make the drift a failure rather than a misleading instruction.
"""

import ast
import json
import os
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
SKILL = ROOT / "skills" / "development-graph" / "SKILL.md"
HOOKS = ROOT / "hooks"
ADAPTERS = [HOOKS / "brief.py", HOOKS / "precommit.py"]
MANIFEST = ROOT / ".claude-plugin" / "plugin.json"
MARKET = ROOT / ".claude-plugin" / "marketplace.json"
COMMANDS = sorted((ROOT / "commands").glob("*.md"))


def _names(typer_app) -> set[str]:
    return {
        c.name or c.callback.__name__.removesuffix("_cmd").rstrip("_")
        for c in typer_app.registered_commands
    }


def commands() -> set[str]:
    """Every invocation `dg` actually accepts.

    Includes grouped commands as their full path (`task add`), so the skill
    cannot advertise `dg task frobnicate` and pass — the group name alone would
    have made every subcommand under it unverifiable.
    """
    out = _names(app)
    for group in app.registered_groups:
        out.add(group.name)
        out |= {f"{group.name} {sub}" for sub in _names(group.typer_instance)}
    return out


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
    text = SKILL.read_text()
    known = commands()
    # Two words where the first names a group, one otherwise — so a bogus
    # subcommand under `dg task` is caught, not waved through by the group.
    used = set()
    for first, second in re.findall(r"\bdg ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?",
                                    text):
        used.add(f"{first} {second}" if second and f"{first} {second}" in known
                 or (second and first in {g.name for g in app.registered_groups})
                 else first)
    assert used <= known, sorted(used - known)


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


# ---- the slash commands -------------------------------------------------
#
# One directory, both hosts: Claude Code discovers `commands/` in the plugin
# root, opencode symlinks the same files in. That only holds while the files
# stay inside the intersection of what the two accept, so it is asserted rather
# than trusted — the same reason the skill's frontmatter is.


def test_there_are_commands_at_all():
    """The directory both hosts' install instructions name."""
    assert COMMANDS, "commands/ is empty; both READMEs promise these files"


@pytest.mark.parametrize("path", COMMANDS, ids=lambda p: p.stem)
def test_command_mentions_only_subcommands_that_exist(path):
    """`dg brief` in a command file is executed, not described. A command file
    naming a subcommand the tool has moved past is a broken slash command, and
    the skill has already drifted this way once."""
    known = commands()
    used = set()
    for first, second in re.findall(r"\bdg ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?",
                                    path.read_text()):
        used.add(f"{first} {second}" if second and f"{first} {second}" in known
                 or (second and first in {g.name for g in app.registered_groups})
                 else first)
    assert used <= known, sorted(used - known)


@pytest.mark.parametrize("path", COMMANDS, ids=lambda p: p.stem)
def test_command_frontmatter_is_portable(path):
    """Only keys both hosts accept, and `description` always — it is what the
    command list shows and what a host without one would leave blank."""
    fm = frontmatter(path.read_text())
    assert set(fm) <= {"description", "argument-hint", "allowed-tools"}, path.name
    assert fm.get("description"), path.name


@pytest.mark.parametrize("path", COMMANDS, ids=lambda p: p.stem)
def test_command_allows_every_tool_it_actually_runs(path):
    """Claude Code runs a `!` block only if `allowed-tools` covers it. A file
    that executes `dg task` under a permission for `dg brief` fails silently —
    the block comes back empty and the command looks like an empty graph."""
    text = path.read_text()
    fm = frontmatter(text)
    ran = {m.strip().split()[0:2] and " ".join(m.strip().split()[:2])
           for m in re.findall(r"!`([^`]+)`", text)}
    allowed = fm.get("allowed-tools", "")
    for cmd in ran:
        # `dg serve --detach` is allowed by `Bash(dg serve:*)`.
        head = " ".join(cmd.split()[:2])
        assert f"Bash({head}:*)" in allowed or f"Bash({cmd}:*)" in allowed, \
            (path.name, cmd, allowed)


@pytest.mark.parametrize("path", COMMANDS, ids=lambda p: p.stem)
def test_command_never_runs_a_blocking_command(path):
    """A `!` block is waited on. `dg serve` without `--detach` never returns,
    and `--edit` opens an editor with nobody to type in it: either one hangs
    the session that ran the command."""
    for m in re.findall(r"!`([^`]+)`", path.read_text()):
        assert "--edit" not in m, (path.name, m)
        if m.split()[1:2] == ["serve"]:
            assert "--detach" in m or "--stop" in m or "--status" in m, m


@pytest.mark.parametrize("target", ["commands", "skills"])
def test_the_host_accepts_what_we_ship(target):
    """Claude Code's own validator, run against the components.

    The frontmatter keys a command may carry are the host's business, not ours,
    and guessing them wrong fails silently — an unknown key is ignored and the
    `!` block comes back empty. `--strict` fails on exactly that, so the
    question is asked of the thing that answers it.

    Skipped where the CLI is not installed: this repo's tests must pass without
    either host present.
    """
    claude = shutil.which("claude")
    if not claude:
        pytest.skip("the claude CLI is not on PATH")
    r = subprocess.run([claude, "plugin", "validate", target, "--strict"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert "Validation passed" in r.stdout, r.stdout + r.stderr


def test_the_manifests_validate():
    """The marketplace and plugin manifests, by the same authority."""
    claude = shutil.which("claude")
    if not claude:
        pytest.skip("the claude CLI is not on PATH")
    r = subprocess.run([claude, "plugin", "validate", ".", "--strict"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert "Validation passed" in r.stdout, r.stdout + r.stderr


def test_both_hosts_are_told_where_the_commands_live():
    """The directory moved out of `opencode/` so one copy serves both.

    Only the *source* path matters: `~/.config/opencode/commands/` is the
    install target and stays. So this looks for the repo-relative form the
    instructions would use, not the substring, which the target also contains.
    """
    assert not (ROOT / "opencode" / "commands").exists(), \
        "commands/ lives at the repo root now — one copy for both hosts"
    for readme in (ROOT / "opencode" / "README.md",
                   ROOT / "docs" / "quickstart-agents.md"):
        text = readme.read_text()
        assert "$repo/opencode/commands" not in text, readme.name
        assert "/commands/*.md" in text, readme.name


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
        # `gate.TRIGGERS` is the one piece of policy an adapter is allowed to
        # hold, and only because a hook may not import the package to ask for
        # it. That copy is pinned to the real tuple below; nothing else about
        # the graph may be named here.
        assert (literals & commands()) - set(gate.TRIGGERS) <= {"brief", "gate"}, \
            path.name
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


#: Commands the gate recognises, one per shape its two detection tables cover.
#: The fast paths are judged against these: everything here has to survive
#: `gate.may_trigger`, or the adapter never asks and the verdict is unreachable.
GATE_POSITIVES = {
    "commit": [
        "git commit -m x", "cd sub && git commit -m x",
        "git -C sub -c user.name=x commit", "GIT_AUTHOR_NAME=x git commit -m x",
        "git add -A\ngit commit -m x", "git commit --amend --no-edit",
    ],
    "removal": [
        "dg rm D02", "dg rm D02 --yes", "dg task rm T01",
        "dg --project /x rm D02", "DG_PROJECT=/x dg rm D02",
        "sudo dg task rm T01", "cd sub && dg rm D02",
    ],
}


def test_the_fast_path_cannot_hide_a_verdict():
    """Both adapters skip `dg gate` entirely for a command matching none of
    `gate.TRIGGERS`. That is safe only if everything the gate can answer
    non-`allow` on contains one of those words, so walk its own detection
    tables and prove it.

    Not "cannot hide a commit" any more, which is the point. That was the whole
    question while commits were all the gate judged; `REMOVALS` was added
    afterwards, both fast paths went on testing for `"commit"` alone, and
    `dg rm D02` — which the gate answers `ask` on — was waved through by both
    hosts. The property is about verdicts, not about commits.
    """
    for command in GATE_POSITIVES["commit"]:
        assert gate.is_commit(command), command
        assert gate.may_trigger(command), command
    for command in GATE_POSITIVES["removal"]:
        assert gate.verdict(command)["verdict"] == "ask", command
        assert gate.may_trigger(command), command


def test_both_fast_paths_carry_the_gates_triggers():
    """The copy in each adapter equals `gate.TRIGGERS`.

    An adapter cannot import the package — `dg` may live in a virtualenv that
    is not the interpreter running the hook — so the list is duplicated in two
    languages, which is exactly the shape that goes stale. `dg gate --triggers`
    prints the real one; this asserts both copies against it.
    """
    want = tuple(gate.TRIGGERS)

    tree = ast.parse((HOOKS / "precommit.py").read_text())
    found = {
        tuple(e.value for e in node.elts)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Tuple, ast.List)) and node.elts
        and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                for e in node.elts)
    }
    assert want in found, f"hooks/precommit.py does not carry {want}"

    ts = (ROOT / "opencode" / "development-graph.ts").read_text()
    m = re.search(r"const TRIGGERS = \[([^\]]*)\]", ts)
    assert m, "opencode adapter has no TRIGGERS list"
    assert tuple(re.findall(r'"([^"]*)"', m.group(1))) == want


def test_dg_gate_prints_its_triggers():
    """The seam the two copies above are checked against, and the way a third
    host reads the list from the tool rather than from a comment."""
    r = subprocess.run([sys.executable, "-m", "dgraph.cli", "gate",
                        "--triggers"], capture_output=True, text=True,
                       cwd=str(ROOT), timeout=60)
    assert r.returncode == 0, r.stderr
    assert tuple(r.stdout.split()) == tuple(gate.TRIGGERS)


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


def test_precommit_advises_about_a_stale_view_without_deciding(project_dir):
    """The `warn` verdict, end to end through the hook.

    A `systemMessage` with no `hookSpecificOutput`: the text reaches the user
    and the command proceeds through their own permission rules. An explicit
    `permissionDecision: "allow"` would override those rules for every git
    command in the session, which this file refuses to do — and a `deny` is what
    a lagging generated file no longer earns.
    """
    subprocess.run(["git", "-C", str(project_dir), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(project_dir), "add", "decisions.json"],
                   check=True, capture_output=True)
    (project_dir / "decision-graph.md").write_text("hand-edited\n")
    out = json.loads(_pre(project_dir, "git commit -m x").stdout)
    assert "hookSpecificOutput" not in out
    assert "dg render" in out["systemMessage"]


def test_precommit_asks_when_ops_are_staged(project_dir):
    (project_dir / ".dgraph-pending.json").write_text(json.dumps(
        [{"op": "set_status", "vertex": "D04", "status": "OPEN"}]))
    out = json.loads(_pre(project_dir, "git commit -m x").stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_precommit_ignores_commands_that_are_not_commits(project_dir):
    (project_dir / "decision-graph.md").write_text("hand-edited\n")
    for command in ("git log --oneline", 'echo "hello"', "ls"):
        assert _pre(project_dir, command).stdout == ""


@pytest.mark.parametrize("command", ["dg rm D02", "dg task rm T01",
                                     "dg rm D02 --yes"])
def test_precommit_asks_before_a_removal(project_dir, command):
    """The gate's other verdict, reaching the host.

    It did not, for as long as the fast path tested for `"commit"` alone: a
    removal contains no such word, so the hook returned before `dg gate` was
    ever run and the one mechanism that puts a removal to a person was
    unreachable. The graph here is clean, so nothing but the removal can
    produce this verdict.
    """
    out = json.loads(_pre(project_dir, command).stdout)
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"
    assert "removes a node" in decision["permissionDecisionReason"]


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


# ---- audit F15: a gate that did not finish is not a gate that allowed ------


def test_precommit_says_so_when_the_gate_times_out(project_dir, tmp_path):
    """Audit F15. `dg gate` is written never to fail open — it catches
    everything and denies — and the adapter's 10-second budget undid that from
    outside: `except Exception: verdict = None` allowed the commit having
    judged it by nothing, with no output at all, indistinguishable from a
    project that never had a graph.

    Still exit 0 and still no `permissionDecision`, because a slow disk must
    not block somebody's commit. The difference is that it says so.

    Through `systemMessage`, not stderr. Stderr was where this went until the
    channel was actually checked: a hook that exits 0 has its stderr sent to the
    debug log and never to the transcript, so the fix this test was written for
    was invisible on the host it was written for.
    """
    slow = tmp_path / "bin"
    slow.mkdir()
    (slow / "dg").write_text("#!/bin/sh\nsleep 30\n")
    (slow / "dg").chmod(0o755)
    env = {**os.environ, "PATH": f"{slow}:{os.environ['PATH']}"}

    src = (HOOKS / "precommit.py").read_text().replace("TIMEOUT = 20",
                                                       "TIMEOUT = 1")
    quick = tmp_path / "precommit.py"
    quick.write_text(src)

    r = run_hook(quick, {"tool_name": "Bash", "cwd": str(project_dir),
                         "tool_input": {"command": "git commit -m x"}}, env=env)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert "not checked" in out["systemMessage"]
    assert "hookSpecificOutput" not in out      # no decision either way


def test_the_hook_budget_outlasts_the_gate_it_calls(project_dir):
    """The other half: the timeout must be long enough that it is reached only
    when something is genuinely wrong. The hook's own budget has to exceed what
    `dg gate` can spend, and `hooks.json` has to allow the hook longer again, or
    the host kills the hook before the hook gives up on the gate."""
    src = (HOOKS / "precommit.py").read_text()
    budget = int(re.search(r"^TIMEOUT = (\d+)", src, re.M).group(1))
    assert budget > gate.GIT_TIMEOUT

    hooks = json.loads((HOOKS / "hooks.json").read_text())
    pre = [h for entry in hooks["hooks"]["PreToolUse"] for h in entry["hooks"]
           if "precommit" in h["command"]]
    assert pre and all(h["timeout"] > budget for h in pre)
