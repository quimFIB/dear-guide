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
ADAPTER = ROOT / "opencode" / "dear-guide.ts"
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
    # Three: the commit gate's `deny` and `ask`, and the write scope's, which
    # collapses both into one throw because that gate answers only `ask` —
    # `deny` is honoured there purely so a future rule that refuses arrives
    # working rather than silently ignored.
    assert body.count("throw new Error") == 3
    assert "verdict.reason" in body
    # The write scope's throw carries its own reason under a different name.
    # Asserted separately rather than by scanning each `throw` line, because a
    # throw spans several lines and the reason is not on the first of them.
    assert "v.reason" in body


#: The opencode hooks this adapter is allowed to register, and why each one.
#: Four mechanisms, and every one of them is named in the module docstring.
#:
#: Here rather than derived, because it is the assertion that runs **everywhere**
#: — the type definitions below are checked only where opencode happens to be
#: installed, which is not most machines and is not CI. A hook added to the
#: adapter fails this and has to be argued for; that is the point of a list that
#: cannot be generated.
HOOKS_USED = {
    "chat.message": "the brief, on the first message of a session",
    "experimental.session.compacting": "the brief again, surviving a compaction",
    "event": "forgetting a session's brief once it has been compacted",
    "tool.execute.before": "the gate",
}


def adapter_hooks() -> set[str]:
    """Every hook key the adapter registers.

    Both spellings. The first version of this read `"([a-z][a-z.]+)": async`
    and so required the quotes — which three of the four keys happen to carry
    and `event` does not, so the guard against a hook that never fires read
    three of the four things it guards.
    """
    return set(re.findall(r'^    "?([a-z][a-z.]*)"?: async',
                          ADAPTER.read_text(), re.M))


def test_the_opencode_adapter_registers_the_hooks_it_means_to():
    """Guards against a hook name that quietly never fires — a plugin whose
    typo'd key is simply never called, which looks from the outside exactly like
    a plugin that is working.

    Equality both ways: a hook registered and not listed here is unargued, and a
    hook listed and not registered is a mechanism that has silently gone.
    """
    assert adapter_hooks() == set(HOOKS_USED), \
        f"registered {sorted(adapter_hooks())}, expected {sorted(HOOKS_USED)}"


def test_every_hook_the_adapter_uses_is_one_opencode_declares(record_property):
    """The other half, against the real type definitions — the only thing that
    can say a name is *real* rather than merely intended.

    It can only run where opencode is installed, so it records whether it did.
    A guard that evaporates on the machines that do not have the dependency is
    not a weak guard, it is an absent one, and a green suite says nothing about
    it: that is why `HOOKS_USED` above is checked unconditionally and this is
    the extra.
    """
    types = (Path.home() /
             ".config/opencode/node_modules/@opencode-ai/plugin/dist/index.d.ts")
    record_property("checked_against_opencode_types", types.exists())
    if not types.exists():
        pytest.skip("@opencode-ai/plugin not installed — HOOKS_USED still ran")
    declared = types.read_text()
    for name in sorted(HOOKS_USED):
        assert re.search(rf'^\s+"?{re.escape(name)}"?\?:', declared, re.M), name


def test_both_hosts_share_one_skill():
    """The claim the plan rests on: the skill is installed, not ported."""
    readme = README.read_text()
    assert "skills/dear-guide" in readme
    assert not (ROOT / "opencode" / "skills").exists()
