"""The opencode adapter.

The same rule as the Claude Code hooks: it translates and decides nothing, so
`dgraph/gate.py` and `dgraph/brief.py` stay the only implementations. What is
checked here is that it parses, that it keeps to those two verbs, and that the
hooks it registers are hooks opencode actually has.
"""

import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ADAPTER = ROOT / "opencode" / "dear-guide.ts"
README = ROOT / "opencode" / "README.md"

def test_the_opencode_adapter_parses():
    """No build step, so a syntax check is the only thing standing between a
    stray comma and a plugin that never loads. Node's `--check` does not strip
    type annotations, so the real V2 loader is the check: if the adapter is
    installed in the opencode config, `opencode2 plugin list` parses and loads
    it, and a failure shows up as a missing plugin."""
    if shutil.which("opencode2") is None:
        pytest.skip("opencode2 not installed")
    installed = Path.home() / ".config/opencode/plugins/dear-guide.ts"
    if not installed.exists():
        pytest.skip("adapter is not installed in the opencode config")
    last = None
    for _ in range(5):
        last = subprocess.run(["opencode2", "plugin", "list"],
                              capture_output=True, text=True, timeout=60)
        if last.returncode == 0 and "dear-guide" in last.stdout:
            return
        time.sleep(1)
    pytest.fail(f"opencode2 did not load dear-guide:\n{last.stdout}\n{last.stderr}")


def test_the_opencode_adapter_decides_nothing_either():
    """The same rule as the Claude Code hooks: it may name `dg brief` and
    `dg gate` and nothing else about the graph."""
    text = ADAPTER.read_text()
    assert '"brief"' in text and '"gate"' in text
    for token in ("--dry-run", "no-verify", "rev-parse", "--cached",
                  "decisions.json", "stale_view", "shlex", "split("):
        assert token not in text, token


#: OpenCode V2's write tools and the argument each names its target with
#: (`path`). The adapter also accepts V1's `filePath`, but the V2 pair is what
#: this host uses; a rename would silently stop the scope applying.
OPENCODE_WRITERS = {"write": "path", "edit": "path"}


def test_the_write_scope_names_the_tools_opencode_actually_has():
    ts = ADAPTER.read_text()
    block = ts[ts.index("const WRITERS"):ts.index("const field")]
    for tool, field in OPENCODE_WRITERS.items():
        assert f"{tool}: \"{field}\"" in block, (tool, field)


def test_read_is_not_judged_even_though_it_carries_a_path():
    """`read` takes `filePath` too. An agent that cannot read the repository it
    is reasoning about is blindfolded rather than constrained, so the writers
    map is an allowlist and this is the case that proves it is one."""
    ts = ADAPTER.read_text()
    block = ts[ts.index("const WRITERS"):ts.index("const field")]
    for tool in ("read", "grep", "glob", "list"):
        assert f"{tool}:" not in block, tool


def test_patch_is_excluded_and_the_reason_is_written_down():
    """`patch` takes `patchText`, one diff that may touch many files, so there
    is no single path to hand the gate. It was in the map with `filePath`,
    which read as undefined and skipped silently — a write tool with no scope,
    looking exactly like a working one. Judging it means parsing a diff for its
    targets, which is policy in an adapter and not what this file may hold."""
    ts = ADAPTER.read_text()
    block = ts[ts.index("const WRITERS"):ts.index("const field")]
    assert "patch:" not in block
    assert "patchText" in ts and "KNOWN HOLE" in ts


def test_the_opencode_adapter_carries_the_reason_into_the_throw():
    """`permission.ask` cannot hold a reason, so throwing is the only way the
    model learns why it was stopped. A throw without the reason would be a
    refusal it retries."""
    text = ADAPTER.read_text()
    throws = [ln for ln in text.splitlines() if "throw new Error" in ln]
    assert throws
    body = text[text.index('ctx.tool.hook("execute.before"'):]
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


#: The V2 hooks this adapter is allowed to register, and why each one.
#: Four mechanisms, and every one of them is named in the module docstring.
#:
#: Here rather than derived, because it is the assertion that runs **everywhere**
#: — the type definitions below are checked only where OpenCode happens to be
#: installed, which is not most machines and is not CI. A hook added to the
#: adapter fails this and has to be argued for; that is the point of a list that
#: cannot be generated.
HOOKS_USED = {
    "prompt": "the brief, on the first prompt of a session",
    "compaction": "the brief again, surviving a compaction",
    "tool.execute.before": "the gate",
    "event.subscribe": "forgetting a session's brief once it has been compacted",
}


def adapter_hooks() -> set[str]:
    """Every V2 hook the adapter registers, in `session`/`tool`/`event` form."""
    text = ADAPTER.read_text()
    hooks = set(re.findall(r'ctx\.session\.hook\("([^"]+)"', text))
    hooks |= {f"tool.{m}" for m in re.findall(r'ctx\.tool\.hook\("([^"]+)"', text)}
    if "ctx.event.subscribe" in text:
        hooks.add("event.subscribe")
    return hooks


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
    """The other half, against the real V2 type definitions — the only thing
    that can say a name is *real* rather than merely intended.

    It can only run where the V2 `@opencode/plugin` types are installed, so it
    records whether it did. A guard that evaporates on the machines that do not
    have the dependency is not a weak guard, it is an absent one, and a green
    suite says nothing about it: that is why `HOOKS_USED` above is checked
    unconditionally and this is the extra.
    """
    candidates = list(
        (Path.home() / ".cache/opencode/npm").glob(
            "**/@opencode/plugin/dist/promise/session.d.ts"
        )
    )
    record_property("checked_against_opencode_types", bool(candidates))
    if not candidates:
        pytest.skip("V2 @opencode/plugin types not installed — HOOKS_USED still ran")
    types_dir = candidates[0].parent
    checks = {
        "prompt": ("session.d.ts", "readonly prompt:"),
        "compaction": ("session.d.ts", "readonly compaction:"),
        "tool.execute.before": ("tool.d.ts", '"execute.before"'),
        "event.subscribe": ("event.d.ts", "subscribe"),
    }
    for name, (filename, needle) in checks.items():
        declared = (types_dir / filename).read_text()
        assert needle in declared, (name, filename)


def test_both_hosts_share_one_skill():
    """The claim the plan rests on: the skill is installed, not ported."""
    readme = README.read_text()
    assert "skills/dear-guide" in readme
    assert not (ROOT / "opencode" / "skills").exists()
