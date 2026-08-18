"""The opencode adapter.

The same rule as the Claude Code hooks: it translates and decides nothing, so
`dgraph/gate.py` and `dgraph/brief.py` stay the only implementations. What is
checked here is that it parses, that it keeps to those two verbs, and that the
hooks it registers are hooks opencode actually has.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ADAPTER = ROOT / "opencode" / "decision-graph.ts"
README = ROOT / "opencode" / "README.md"

def test_the_opencode_adapter_parses():
    """No build step, here or in `dgraph/static/app.html`, so a syntax check is
    the only thing standing between a stray comma and a plugin that never loads.
    Node strips the type annotations itself."""
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    r = subprocess.run(["node", "--check", str(ADAPTER)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_opencode_adapter_decides_nothing_either():
    """The same rule as the Claude Code hooks: it may name `dg brief` and
    `dg gate` and nothing else about the graph."""
    text = ADAPTER.read_text()
    assert '"brief"' in text and '"gate"' in text
    for token in ("--dry-run", "no-verify", "rev-parse", "--cached",
                  "decisions.json", "stale_view", "shlex", "split("):
        assert token not in text, token


def test_the_opencode_adapter_carries_the_reason_into_the_throw():
    """`permission.ask` cannot hold a reason, so throwing is the only way the
    model learns why it was stopped. A throw without the reason would be a
    refusal it retries."""
    text = ADAPTER.read_text()
    throws = [ln for ln in text.splitlines() if "throw new Error" in ln]
    assert throws
    body = text[text.index('"tool.execute.before"'):]
    assert body.count("throw new Error") == 2          # deny, and ask
    assert "verdict.reason" in body


def test_the_opencode_adapter_registers_hooks_that_exist():
    """Guards against a hook name that quietly never fires. Checked against the
    installed type definitions when they are there."""
    types = Path.home() / ".config/opencode/node_modules/@opencode-ai/plugin/dist/index.d.ts"
    if not types.exists():
        pytest.skip("@opencode-ai/plugin not installed")
    declared = types.read_text()
    used = set(re.findall(r'^    "([a-z][a-z.]+)": async', ADAPTER.read_text(),
                          re.M))
    assert used, "no hooks found — did the file change shape?"
    for name in used:
        assert f'"{name}"?' in declared, name


def test_both_hosts_share_one_skill():
    """The claim the plan rests on: the skill is installed, not ported."""
    readme = README.read_text()
    assert "skills/decisions" in readme
    assert not (ROOT / "opencode" / "skills").exists()
