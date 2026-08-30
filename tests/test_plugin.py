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

from dgraph import gate, pending
from dgraph.check import CHECKS, run as check_run
from dgraph.cli import app
from dgraph.model import Graph
from dgraph.render import write

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "dear-guide" / "SKILL.md"
HOOKS = ROOT / "hooks"
ADAPTERS = [HOOKS / "brief.py", HOOKS / "precommit.py", HOOKS / "prewrite.py"]
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


def agent_commands() -> set[str]:
    """Every invocation `dg-agent` accepts. `commands()`'s twin.

    A second binary needs a second answer, and it did not have one: the guard
    below matches `\\bdg ([a-z][a-z-]*)` and resolves the result against
    `cli.app`, and `dg-agent` does not match that pattern — the hyphen is not a
    space. `3807d66` moved six commands across and the guard did not follow, so
    the surfaces went from one checked vocabulary to one checked and one not,
    in the commit that said *"everything a person could type it into says
    `dg-agent`"*. Audit `R-F5`.
    """
    from dgraph.agent_cli import app as agent_app
    return _names(agent_app)


#: Where a `dg-agent` invocation can appear. Wider than `commands/` and the
#: skill, because the split put the launcher in the *procedure* documents and
#: the prompts as well — `agentic/README.md` is the file a supervisor follows
#: line by line, and a command that has moved is a step that fails.
AGENT_SURFACES = [
    *sorted((ROOT / "commands").glob("*.md")),
    *sorted((ROOT / "docs").glob("*.md")),
    *sorted((ROOT / "agentic").glob("*.md")),
    *sorted((ROOT / "opencode").glob("*.md")),
    *sorted((ROOT / "dgraph" / "prompts").glob("*.md")),
    ROOT / "README.md",
    SKILL,
]

#: `dg-agent <word>` where the word is a command. The negative lookahead keeps
#: a prose sentence that happens to follow the binary's name — `agentic/bin/dg`
#: and `dg-agent afterwards`, in a listing of the two wrapper files — out of the
#: vocabulary being checked. It is the only such line and it is a file list, not
#: an invocation.
_AGENT_CALL = re.compile(r"\bdg-agent ([a-z][a-z-]*)")


@pytest.mark.parametrize("path", AGENT_SURFACES,
                         ids=lambda p: str(p.relative_to(ROOT)))
def test_every_surface_mentions_only_dg_agent_commands_that_exist(path):
    """The `dg` guard, pointed at the binary that did not have one.

    132 mentions across seven trees when this was written, every one of them
    valid — so this is a guard gap closed before it became a drift, which is
    the only reason `R-F5` was filed `low`. Delete a command from
    `agent_cli.app` and this goes red.
    """
    if not path.exists():
        return
    known = agent_commands()
    used = set(_AGENT_CALL.findall(path.read_text(encoding="utf-8")))
    # Prose that follows the binary's name rather than invoking it. Kept as an
    # explicit set of one, so a second entry has to be argued for rather than
    # absorbed by a looser pattern.
    used -= {"afterwards"}
    assert used <= known, \
        f"{path.relative_to(ROOT)} names {sorted(used - known)}"


def test_the_dg_agent_guard_would_catch_a_removal():
    """This guard's own falsifier.

    A regex that silently matches nothing is `H-F3`'s shape, and this file is
    where that was found. So: the surfaces must actually name commands, and a
    name that does not resolve must fail the check the test above makes.
    """
    seen = set()
    for path in AGENT_SURFACES:
        if path.exists():
            seen |= set(_AGENT_CALL.findall(path.read_text(encoding="utf-8")))
    assert len(seen & agent_commands()) >= 4, \
        "the pattern matched almost nothing; it is not checking a vocabulary"
    assert not ({"frobnicate"} <= agent_commands())


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


#: The gate's account of itself, as the skill has to tell it. One row per
#: verdict `dg gate` can answer: a live store that earns it, the commands the
#: paragraph describing it must offer, and the thing it must say outright.
#:
#: The point of the table is the **link** between the two halves. Pinning the
#: word `warn` somewhere near `dg render` discharges nothing — it is satisfied
#: by prose that is wrong about everything else, which is shape 6 and is how
#: `H-F2` survived three tests over this file. What is checked here is that the
#: verdict each cause earns is *computed*, by running `gate.verdict` against a
#: store built for it, and that the paragraph about that verdict names the
#: command that can see it.
#:
#: `build` receives a project directory holding a rendered demo graph.


def _stale_view(d):
    """A view that lags its store, in a commit that records one of them.

    The `git init` and the `git add` are not scaffolding: the gate says nothing
    about a generated file a commit has nothing to do with, which is most of
    what made the old blocking version intolerable. Without them this store
    earns `allow` and the row would be testing the wrong thing.
    """
    subprocess.run(["git", "-C", str(d), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(d), "add", "decisions.json"],
                   check=True, capture_output=True)
    (d / "decision-graph.md").write_text("hand-edited\n")


def _staged_tray(d):
    (d / ".dgraph-pending.json").write_text(json.dumps(
        [{"op": "set_status", "vertex": "D04", "status": "OPEN"}]))


def _contradiction(d):
    store = json.loads((d / "decisions.json").read_text())
    store["edges"].append({"from": "D01", "to": ["D99"]})
    (d / "decisions.json").write_text(json.dumps(store))


GATE_STORY = [
    # verdict, builds a store that earns it, commands offered, said outright
    ("deny",  _contradiction,  ("dg check",),   "refus"),
    ("ask",   _staged_tray,    ("dg pending",), "cannot see this"),
    ("warn",  _stale_view,     ("dg render",),  "Not a refusal"),
    ("allow", lambda d: None,  (),              "nothing is said"),
]


def _gate_section() -> str:
    """The skill's account of the gate, whatever it is currently titled.

    Found by the command it is about rather than by its heading, because the
    heading is the half that was wrong: it read *When a commit is refused* over
    a section describing two things that do not refuse.
    """
    sections = re.split(r"^## ", SKILL.read_text(), flags=re.M)
    hit = [s for s in sections
           if "dg gate" in s and "commit" in s.split("\n", 1)[0]]
    assert len(hit) == 1, [s.split("\n", 1)[0] for s in hit]
    return hit[0]


def _paragraph(section: str, verdict: str) -> str:
    hit = [para for para in section.split("\n\n") if f"`{verdict}`" in para]
    assert len(hit) == 1, f"{verdict} is described {len(hit)} times"
    return hit[0]


@pytest.mark.parametrize("want,build,offers,says", GATE_STORY,
                         ids=[r[0] for r in GATE_STORY])
def test_the_skill_tells_the_truth_about_each_gate_verdict(
        project_dir, want, build, offers, says):
    """`H-F2`. The skill is the only prose in this repository written for a
    machine to act on, and its account of the gate had been describing a
    severity the gate stopped using. The section predates `warn`; the sweep that
    fixed the identical error in `dg gate --help` did not open this file; and
    the three tests over it all check vocabulary rather than claims.

    Two assertions per row, and the first is what makes the second worth having.
    The verdict is computed against a live store, so a severity that moves fails
    here — where the message names the row — and takes the prose with it.
    """
    from dgraph import gate, project as _project

    build(project_dir)
    got = gate.verdict("git commit -m x", _project.Project(project_dir))
    assert got["verdict"] == want, \
        f"the premise moved: this store now earns {got['verdict']}"

    para = _paragraph(_gate_section(), want)
    assert says in para, (want, says)
    for command in offers:
        assert f"`{command}`" in para, (want, command)


def test_the_skill_sends_a_stopped_commit_to_a_command_that_can_see_it():
    """The sharp half of `H-F2`, kept as its own claim because it is the one
    that costs work rather than a retry.

    The old section said *"Run `dg check` — it names the rule that broke"* for
    every case. `dg check` cannot see a staging tray at all: it prints every
    invariant holding and exits 0 while the ops sit in a gitignored file, one
    commit from being dropped with nothing in the diff. An agent that believed
    it would conclude the gate was wrong.
    """
    import tempfile
    from dgraph import gate, project as _project
    d = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "demo" / "decisions.json", d / "decisions.json")
    write(Graph.load(d / "decisions.json"), d / "decision-graph.md")
    _staged_tray(d)

    proj = _project.Project(d)
    assert gate.verdict("git commit -m x", proj)["verdict"] == "ask"
    # the premise: the command the old prose named reports nothing wrong
    assert not [v for v in check_run(proj) if v.blocking]

    para = _paragraph(_gate_section(), "ask")
    assert "`dg pending`" in para or "`dg brief`" in para
    assert "gitignored" in para                  # why it costs work, not a retry


def test_the_skill_accounts_for_every_verdict_the_gate_can_give():
    """The half that survives a fifth verdict.

    `warn` was added to a gate that had three and this file went on describing
    the old set for two days. Driven off `gate.RANK`, so a verdict added to the
    gate and not to `GATE_STORY` fails here — the same property
    `dg gate --triggers` gives the two fast paths.
    """
    from dgraph import gate
    assert set(gate.RANK) == {row[0] for row in GATE_STORY}, \
        "GATE_STORY has drifted from gate.RANK"
    section = _gate_section()
    for name in gate.RANK:
        assert f"`{name}`" in section, name


def test_the_skill_does_not_call_a_warn_or_an_ask_a_refusal():
    """The framing, which is where `H-F2` actually lived: a heading reading
    *When a commit is refused* over two causes that do not refuse.

    Prose is deliberately not asserted anywhere else in this suite. The word
    *refused* over a `warn` is not prose, it is a claim about behaviour, and it
    is the claim that was false.
    """
    section = _gate_section()
    heading, _, body = section.partition("\n")
    assert "refus" not in heading.lower(), heading
    assert "refus" in _paragraph(body, "deny")          # the one that is


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
        # `dg serve --detach` is allowed by `Bash(dg serve:*)`, and
        # `dg-agent list` by `Bash(dg-agent:*)` — the launcher is its own
        # binary, so what a prefix has to cover is the tool rather than a
        # subcommand of `dg`.
        words = cmd.split()
        heads = [" ".join(words[:2]), words[0]] if words else []
        assert (any(f"Bash({h}:*)" in allowed for h in heads)
                or f"Bash({cmd}:*)" in allowed), (path.name, cmd, allowed)


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


def test_a_command_file_carries_no_prefix_of_its_own():
    """Each host supplies its own namespace, so the file must not.

    Claude Code prefixes a plugin's commands with the plugin name — `dg`, so
    `brief.md` is `/dg:brief`. A `dg-` in the filename would come back doubled
    there. opencode gets the same file under a link the install renames.
    """
    for path in COMMANDS:
        assert not path.stem.startswith("dg-"), \
            f"{path.name}: the plugin name already says `dg`"


def test_the_plugin_is_named_for_the_command_it_wraps():
    """`/dg:brief` reads as the CLI does. The manifest name is what Claude Code
    puts before the colon, and the marketplace entry has to agree with it or the
    install names a plugin that is not there."""
    assert json.loads(MANIFEST.read_text())["name"] == "dg"
    entries = json.loads(MARKET.read_text())["plugins"]
    assert [e["name"] for e in entries] == ["dg"]


def test_opencode_is_told_to_prefix_the_link_it_makes():
    """The one line the two-name arrangement rests on.

    opencode's user-scoped command directory is flat and shared, where `/brief`
    and `/context` collide with whatever else is installed. The prefix lives on
    the symlink rather than in the file, which is what keeps one copy serving
    both hosts — and an install snippet that dropped it would silently install
    five commands under names somebody else owns.
    """
    for readme in (ROOT / "opencode" / "README.md",
                   ROOT / "docs" / "quickstart-agents.md"):
        text = readme.read_text()
        assert 'commands/"dg-$(basename' in text, \
            f"{readme.name}: the link has to be renamed, not copied straight"


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

    ts = (ROOT / "opencode" / "dear-guide.ts").read_text()
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


# ---- the write-scope adapter --------------------------------------------


def _write(project_dir, path, tool="Write", agent="brisk-beacon",
           policy="launch"):
    env = dict(os.environ, DG_WRITE=policy)
    if agent:
        env["DG_AGENT"] = agent
    else:
        env.pop("DG_AGENT", None)
    return run_hook(ADAPTERS[2], {
        "tool_name": tool, "cwd": str(project_dir),
        "tool_input": {"file_path": str(path)},
    }, env=env)


def test_prewrite_is_silent_for_a_supervisor(project_dir):
    """No `$DG_AGENT` is a person, and a person is never scoped. This is also
    the fast path: an ordinary session must not pay for a subprocess on every
    single write."""
    assert _write(project_dir, "/etc/passwd", agent=None).stdout == ""


def test_prewrite_is_silent_inside_the_project(project_dir):
    """Silence is the allow, for `precommit.py`'s reason: an explicit allow
    would override the user's own permission rules for every write."""
    assert _write(project_dir, project_dir / "findings" / "new.md").stdout == ""


def test_prewrite_asks_outside_the_project(project_dir):
    out = json.loads(_write(project_dir, "/etc/passwd").stdout)
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"
    assert "/etc/passwd" in decision["permissionDecisionReason"]


def test_prewrite_ignores_reads(project_dir):
    """Reads are never judged. An agent that cannot read the repository it is
    reasoning about is blindfolded rather than constrained."""
    for tool in ("Read", "Grep", "Glob", "Bash"):
        assert _write(project_dir, "/etc/passwd", tool=tool).stdout == ""


def test_prewrite_is_silent_when_the_policy_is_open(project_dir):
    """`open` is the default, and has to remain today's behaviour."""
    assert _write(project_dir, "/etc/passwd", policy="open").stdout == ""


def test_both_adapters_relay_the_write_scope():
    """The generalisation, asserted rather than assumed.

    The point of putting the scope behind `dg gate` is that a rule written
    once is enforced under every host. If only one adapter ever asked, the
    other would be a hole the size of a whole scaffold — which is exactly what
    happened to the removal verdict when one fast path went stale.
    """
    py = (HOOKS / "prewrite.py").read_text()
    ts = (ROOT / "opencode" / "dear-guide.ts").read_text()
    for source, name in ((py, "prewrite.py"), (ts, "dear-guide.ts")):
        assert "--write" in source, name
        # The `$DG_AGENT` fast path, in both: a supervisor is never scoped, so
        # neither host may spawn `dg` for one.
        assert "DG_AGENT" in source, name


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
    # Two SessionStart entries for the brief, and two PreToolUse: the commit
    # gate on Bash and the agent write scope on the editing tools.
    assert seen == 4


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


#: Every way `dg gate` can come back without a verdict, and whether the hook
#: says so. Driven from one table rather than one test per branch, because
#: `H-F1` was precisely a case answered and its siblings waved through: the
#: file held the argument, applied it to the timeout, and let every other
#: failure look exactly like a directory that has no graph.
#:
#: `script` is a `dg` stand-in; `speaks` is whether the user hears about it.
GATE_FAILURES = [
    ("a broken install (an editable checkout that moved)",
     "echo \"ModuleNotFoundError: No module named 'dgraph'\" >&2\nexit 1\n", True),
    ("a dg that dies on a signal",
     "kill -TERM $$\n", True),
    ("exit 0, and something on stdout that is not a verdict",
     "echo 'not json'\n", True),
    ("exit 0 and nothing at all on stdout",
     "exit 0\n", True),
    # The two silences. A plugin is installed for a user, not for a project.
    ("a dg too old to know the subcommand",
     "echo 'No such command' >&2\nexit 2\n", False),
    ("a dg too old to know --json",
     "echo 'No such option' >&2\nexit 2\n", False),
]


@pytest.mark.parametrize("what,script,speaks",
                         GATE_FAILURES, ids=[f[0] for f in GATE_FAILURES])
def test_a_gate_that_did_not_run_is_never_a_gate_that_allowed(
        project_dir, tmp_path, what, script, speaks):
    """`H-F1`. `dg gate` always exits 0 — that is its docstring's promise, and
    the reason is stated there: *an adapter must be able to tell a refusal from
    a crash*. Anything else means the gate did not run and the command is about
    to proceed judged by nothing.

    Two exits can still be a project that never heard of this tool, and those
    stay silent. Nothing else can, and a silence there is indistinguishable
    from a clean allow — which is what it was, for every failure but the
    timeout, until this table existed.

    Never a `permissionDecision` in either direction: a broken `dg` must not
    block somebody's commit any more than it may wave one through.
    """
    (project_dir / ".dgraph-pending.json").write_text(json.dumps(
        [{"op": "set_status", "vertex": "D04", "status": "OPEN"}]))
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "dg").write_text("#!/bin/sh\n" + script)
    (fake / "dg").chmod(0o755)

    r = _pre(project_dir, "git commit -m x",
             env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"})
    assert r.returncode == 0
    if not speaks:
        assert r.stdout == "", what
        return
    out = json.loads(r.stdout)
    assert "not checked" in out["systemMessage"], what
    assert "hookSpecificOutput" not in out, what


def test_the_gate_hook_says_so_where_the_real_break_happens(project_dir,
                                                            tmp_path):
    """The same finding with no stand-in anywhere: a real `dg`, really broken.

    `README.md` installs with `pip install -e <checkout>`; move the checkout and
    the console script stays on `PATH` while the import does not. That is the
    everyday way into `H-F1`, and it is the failure this repository's own
    restructure produced. Skipped rather than faked when there is no network or
    no `pip` — the parametrised table above carries the property; this one
    carries the reachability.
    """
    venv = tmp_path / "venv"
    if subprocess.run([sys.executable, "-m", "venv", str(venv)],
                      capture_output=True).returncode != 0:
        pytest.skip("could not build a venv")
    src = tmp_path / "src"
    shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(
        ".venv", ".git", "node_modules", "*.egg-info", ".pytest_cache"))
    if subprocess.run([str(venv / "bin/pip"), "install", "-q", "--no-deps",
                       "-e", str(src)], capture_output=True).returncode != 0:
        pytest.skip("could not install into the venv")
    shutil.rmtree(src)                     # the checkout moves out from under it

    probe = subprocess.run([str(venv / "bin/dg"), "gate", "--command",
                            "git commit -m x", "--json"],
                           capture_output=True, text=True)
    assert probe.returncode not in (0, 2), probe.stderr

    r = _pre(project_dir, "git commit -m x",
             env={**os.environ, "PATH": f"{venv / 'bin'}:{os.environ['PATH']}"})
    out = json.loads(r.stdout)
    assert "not checked" in out["systemMessage"]


def test_the_brief_hook_says_so_when_dg_is_broken_rather_than_absent(
        project_dir, tmp_path):
    """`H-F1`'s quieter half. `hooks/brief.py` classified a failed `dg` by
    looking for one phrase in stderr, which caught a `dg` too old and nothing
    else — so a broken install left the plugin silent *permanently*, looking
    exactly like a plugin correctly doing nothing where there is no graph.

    The cost is context rather than a safeguard, which is why it is one line and
    not a decision. Exit 2 stays silent unless stderr says why, because exit 2
    is also "no decisions.json here" and that is the common case.
    """
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "dg").write_text("#!/bin/sh\necho 'ImportError' >&2\nexit 1\n")
    (fake / "dg").chmod(0o755)
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"}
    out = run_hook(ADAPTERS[0], {"cwd": str(project_dir)}, env=env).stdout
    assert "exited 1" in out and "dg brief" in out

    (fake / "dg").write_text("#!/bin/sh\nexit 2\n")       # no graph here
    assert run_hook(ADAPTERS[0], {"cwd": str(project_dir)}, env=env).stdout == ""


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


def test_dist_matches_the_packaging_name():
    """`dgraph.DIST` is how `dg --version` finds itself. A stale copy reports
    "unknown", which both host adapters read as "too old to have the command I
    want" — so they go quiet instead of saying anything. It has drifted through
    two renames; this stops a third."""
    import tomllib

    import dgraph
    root = Path(__file__).resolve().parent.parent
    meta = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert dgraph.DIST == meta["project"]["name"]


def test_nothing_published_cites_a_document_only_the_private_notes_hold():
    """The pointing between this repo and the working notes is **one-way**.

    `dev-docs/` is a separate, private repository, symlinked in. It may cite this
    tree freely — `CAPTURE-MECHANISM.md` pins thirty-five citations to a tag here
    — because anyone who can read it can read this. The reverse does not hold: a
    comment here naming `consistency-policy-proposal.md` sends a reader to a file
    that, for them, does not exist.

    Not a secrecy rule — a filename discloses nothing. It is a dangling-reference
    rule, and the remedy is the one the notes themselves prescribe: state the
    reasoning outright. Three docstrings carried the pointer instead, and each was
    carrying real substance behind it — a conflict classification, a rule about
    how a fixture must be built — which is exactly why the pointer looked
    sufficient.

    Finding ids are deliberately **not** caught: `W-F1`, `C-F16` and the rest are
    bare handles that name no file, they are long-standing practice here, and a
    reader who cannot resolve one has lost nothing but a cross-reference.
    """
    root = Path(__file__).resolve().parent.parent
    private = re.compile(
        r"\b(FINDINGS|TASKS|AUDIT-PROMPT|MULTI-WRITER|CAPTURE-MECHANISM"
        r"|consistency-policy(?:-proposal)?)\.md\b")
    tracked = subprocess.run(["git", "-C", str(root), "ls-files"],
                             capture_output=True, text=True).stdout.split()
    bad = []
    for rel in tracked:
        # This file states the rule, so it has to name what the rule forbids.
        # Exempting it is not a loophole worth closing: a citation smuggled in
        # here would sit inside the test that exists to ban citations, which is
        # not somewhere a pointer gets added by accident.
        if rel == "tests/test_plugin.py":
            continue
        f = root / rel
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if private.search(line):
                bad.append(f"{rel}:{i}  {line.strip()[:70]}")
    assert not bad, (
        "published files citing a document only `dev-docs/` holds — state the "
        "reasoning inline instead:\n  " + "\n  ".join(bad))

    # The weaker half of the same rule: naming the private tree *at all* as an
    # aside — "`dev-docs` in this workshop is one" — assumes a reader who knows
    # the author's layout, and a published repo has no such reader. It is not
    # the dangling reference the loop above bans, and it slipped past that loop
    # for exactly that reason, reaching a pre-push check rather than a test.
    #
    # `.gitignore` must name it (the rule that keeps the symlink out of a
    # commit lives there), and this file states the rule.
    allowed = {"tests/test_plugin.py", ".gitignore"}
    asides = []
    for rel in tracked:
        if rel in allowed:
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if "dev-docs" in line:
                asides.append(f"{rel}:{i}  {line.strip()[:70]}")
    assert not asides, (
        "published files naming the private notes tree — say what you mean "
        "about the arrangement instead of pointing at it:\n  "
        + "\n  ".join(asides))


def test_the_private_notes_symlink_is_ignored_by_a_committed_rule():
    """`.git/info/exclude` is not committed and does not survive a clone.

    The rule lived there, so the protection existed only on the machine that
    first needed it. And it must have **no trailing slash**: `/dev-docs/` matches
    directories only, a symlink is not one, so the rule silently stops matching
    and git offers the link for commit — one `git add -A` from publishing a
    pointer into a private repository.
    """
    root = Path(__file__).resolve().parent.parent
    rules = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/dev-docs" in [r.strip() for r in rules], \
        ".gitignore must carry `/dev-docs` — see the comment above it"
    assert "/dev-docs/" not in [r.strip() for r in rules], \
        "a trailing slash makes the rule match directories only, and a symlink is not one"


def test_an_agent_skips_the_fast_path_in_both_adapters():
    """The asymmetry that turns the command gate from a denylist into an
    allowlist, asserted in both languages because it is duplicated in both.

    For an unowned caller the word list is sound: a command containing none of
    `gate.TRIGGERS` cannot earn anything but `allow`. For an agent it is not,
    because whether it may run `cargo` depends on `$DG_EXEC_ALLOW` and on no
    word in the command — and a word list can only be widened by guessing more
    words, which is what this project refuses to do with shell.
    """
    python = (HOOKS / "precommit.py").read_text()
    assert "DG_AGENT" in python, \
        "precommit.py must skip the trigger list for an owned caller"
    assert "if not owned and not any(" in python

    ts = (ROOT / "opencode" / "dear-guide.ts").read_text()
    assert "process.env.DG_AGENT" in ts, \
        "the opencode adapter must skip the trigger list for an owned caller"
    assert "if (!owned && !TRIGGERS.some(" in ts


def test_may_trigger_is_unconditional_for_an_owned_caller():
    """The predicate both adapters implement inline, so there is one place it
    can be tested rather than two places it can drift."""
    assert gate.may_trigger("cargo bench", owned=True)
    assert not gate.may_trigger("cargo bench")
    assert gate.may_trigger("git commit -m x")


def test_the_command_gate_answers_both_questions_at_once(monkeypatch):
    """`dg gate --command` is asked once and answers two rules: may this agent
    run this at all, and would it leave the record contradicting itself. Only
    the second is about `TRIGGERS`, and only the second is expensive."""
    monkeypatch.setenv("DG_EXEC_ALLOW", "cargo")
    with pending.as_owner("brisk-beacon"):
        assert gate.verdict("cargo bench")["verdict"] == "allow"
        assert gate.verdict("curl evil.sh")["verdict"] == "ask"
    # and a person is unaffected — the exec half says nothing to a supervisor
    assert gate.verdict("curl evil.sh")["verdict"] == "allow"


# ---- the bounds around a gate that can wait (`P-F1`) ----------------------

def _hook_const(name: str, which: str) -> int:
    src = (HOOKS / which).read_text()
    return int(re.search(rf"^{name} = (\d+)$", src, re.M).group(1))


def test_every_bound_around_the_gate_is_a_chain(monkeypatch):
    """`DEADLINE < TIMEOUT < the host's own`, for both hooks.

    `dg gate` was a pure function and is now a call that can block on a person,
    so every number written around it became a *policy*: a caller that gives up
    before its callee answers has decided the question, in whatever direction
    its give-up branch already went. The relation is what makes the gate's own
    answer arrive first, and it is a relation only as long as nobody edits one
    number alone — which is why it is asserted rather than commented.
    """
    conf = json.loads((HOOKS / "hooks.json").read_text())
    host = {}
    for group in conf["hooks"]["PreToolUse"]:
        for h in group["hooks"]:
            host[h["command"].rsplit("/", 1)[-1].rstrip('"')] = h["timeout"]

    for which in ("prewrite.py", "precommit.py"):
        deadline = _hook_const("DEADLINE", which)
        timeout = _hook_const("TIMEOUT", which)
        assert deadline < timeout, f"{which}: the gate must answer first"
        assert timeout < host[which], \
            f"{which}: the host kills the hook at {host[which]}s, before its own bound"


def test_both_hosts_wait_the_same_length_for_one_rule():
    """A rule that waits differently depending on which tool the agent reached
    for is two rules. Claude Code gave up at 5 seconds and allowed the write;
    opencode awaited forever. Both now name the same number."""
    ts = (ROOT / "opencode" / "dear-guide.ts").read_text()
    theirs = int(re.search(r'const DEADLINE = "(\d+)"', ts).group(1))
    assert theirs == _hook_const("DEADLINE", "prewrite.py") \
        == _hook_const("DEADLINE", "precommit.py")


def test_a_gate_that_did_not_answer_is_a_refusal_in_both_hooks():
    """The give-up branch is the finding. An unjudged write is not consent, and
    a message saying so while the write proceeds is the same failure said out
    loud — which is what `precommit.py` did and `prewrite.py` did not even do."""
    for which in ("prewrite.py", "precommit.py"):
        src = (HOOKS / which).read_text()
        branch = src.split("except subprocess.TimeoutExpired:", 1)
        assert len(branch) == 2, f"{which} must name the timeout branch apart"
        after = branch[1].split("except Exception", 1)[0]
        assert '"permissionDecision": "deny"' in after, \
            f"{which}: a gate that did not answer must refuse, not fall through"


def test_the_adapters_tell_the_gate_what_they_will_wait():
    """The number is only a bound if it is passed. Both hooks and the opencode
    adapter hand it to `dg gate --deadline`, which is what makes the gate
    answer before the caller gives up."""
    for which in ("prewrite.py", "precommit.py"):
        assert '"--deadline"' in (HOOKS / which).read_text(), which
    ts = (ROOT / "opencode" / "dear-guide.ts").read_text()
    assert ts.count('"--deadline", DEADLINE') == 2, \
        "both the write gate and the command gate name the bound"
