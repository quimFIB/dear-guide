"""The confinement floor: one policy, rendered per backend.

The gate and the broker judge what the host routes to them. A floor is the same
two lists enforced *below* the tool layer, so a shell redirection — which no
gate sees — is refused by the kernel rather than by a rule nobody consulted.

The point of these is that both backends render **the same policy**. A mount
set built from its own idea of the writable roots would be free to seal
something the gate allows, or allow something it refuses, and only one of those
is visible.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

from dgraph import confine, limits


def test_the_policy_is_read_from_limits_and_not_restated(tmp_path):
    c = confine.policy(tmp_path)
    assert c.writable == limits.writable_roots(tmp_path)
    assert c.protected == limits.protected_paths(tmp_path)


@pytest.mark.parametrize("backend", confine.BACKENDS)
def test_both_backends_express_the_same_two_lists(tmp_path, backend):
    # The stores have to exist: `bwrap` binds only what is there, since it
    # refuses a source that is not, and a project with no task store yet is
    # ordinary. `test_only_protected_paths_that_exist_are_bound` pins that.
    (tmp_path / "decisions.json").write_text("{}")
    (tmp_path / "tasks.json").write_text("{}")
    c = confine.policy(tmp_path)
    rendered = json.dumps(confine.render(backend, tmp_path).__dict__)
    for path in c.writable + c.protected:
        assert path in rendered, f"{backend} lost {path}"


def test_an_unknown_backend_is_refused_rather_than_defaulted(tmp_path):
    with pytest.raises(ValueError):
        confine.render("seatbelt", tmp_path)


# ---- bwrap: an argv prefix -----------------------------------------------

def test_the_writable_bind_comes_before_the_protection_over_it(tmp_path):
    """bwrap applies operations in sequence, so a writable root bound *after*
    the read-only files under it would hide the protection, silently."""
    argv = confine.render("bwrap", tmp_path).prefix
    (tmp_path / "decisions.json").write_text("{}")
    argv = confine.render("bwrap", tmp_path).prefix
    assert argv.index("--bind") < argv.index("--ro-bind", argv.index("--bind"))


def test_only_protected_paths_that_exist_are_bound(tmp_path):
    """bwrap refuses to bind a source that is not there, and a project whose
    task store has not been created yet is ordinary."""
    (tmp_path / "decisions.json").write_text("{}")
    argv = confine.render("bwrap", tmp_path).prefix
    assert str(tmp_path / "decisions.json") in argv
    assert str(tmp_path / "tasks.json") not in argv


def test_the_prefix_ends_so_a_command_can_follow(tmp_path):
    assert confine.render("bwrap", tmp_path).prefix[-1] == "--"


def test_it_does_not_clear_the_environment_it_was_given(tmp_path):
    """`dg-agent run` composes the child's environment, and clearing it here
    would strip the very remit it was composed to carry."""
    assert "--clearenv" not in confine.render("bwrap", tmp_path).prefix


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap absent")
def test_the_rendered_prefix_actually_enforces_the_policy(tmp_path):
    """The one test here that proves rather than asserts. `sed -i` is the row
    that matters: it does not write in place, it renames a temporary file over
    the target, so a protection that only refused `open` for writing would be
    defeated by it."""
    ok, _ = confine.available("bwrap")
    if not ok:
        pytest.skip("user namespaces unavailable here")
    store = tmp_path / "decisions.json"
    store.write_text('{"vertices":[],"edges":[]}')
    (tmp_path / ".dgraph-pending.json").write_text("[]")
    # Genuinely outside: `/tmp` is itself a writable root, so a sibling of
    # `tmp_path` is inside the policy and proves nothing.
    outside = pathlib.Path.home() / ".dg-confine-breach"
    argv = confine.render("bwrap", tmp_path).prefix

    def run(command):
        return subprocess.run(argv + ["/bin/sh", "-c", command],
                              capture_output=True, text=True).returncode

    assert run(f"echo S > {tmp_path}/.dgraph-pending.json") == 0, "the tray"
    assert run(f"echo F > {tmp_path}/notes.md") == 0, "ordinary work"
    assert run(f"echo C > {store}") != 0
    assert run(f"sed -i s/vertices/X/ {store}") != 0, "the rename route"
    assert run(f"rm -f {store}") != 0
    assert run(f"echo O > {outside}") != 0
    assert store.read_text() == '{"vertices":[],"edges":[]}'
    assert not outside.exists()


# ---- host: settings the spawn line carries -------------------------------

def test_the_host_backend_carves_the_record_out_of_the_writable_roots(tmp_path):
    fs = confine.render("host", tmp_path).settings["sandbox"]["filesystem"]
    assert fs["allowWrite"] == limits.writable_roots(tmp_path)
    assert fs["denyWrite"] == limits.protected_paths(tmp_path)


def test_the_host_backend_leaves_reads_alone(tmp_path):
    """Reads are never judged — an agent that cannot read the repository it is
    reasoning about is blindfolded rather than constrained."""
    fs = confine.render("host", tmp_path).settings["sandbox"]["filesystem"]
    assert "denyRead" not in fs


def test_the_host_backend_closes_its_own_way_out(tmp_path):
    """A sandbox with a documented escape is a sandbox nobody has to break."""
    box = confine.render("host", tmp_path).settings["sandbox"]
    assert box["allowUnsandboxedCommands"] is False


def test_the_host_backend_carries_no_argv_prefix(tmp_path):
    """It configures the runner rather than wrapping the command, which is why
    `Launch` has two fields: an argv prefix is host-neutral and settings are
    not, so they are applied in different places."""
    assert confine.render("host", tmp_path).prefix == []


def test_settings_travel_as_a_literal_string_on_argv(tmp_path):
    arg = confine.settings_arg(confine.render("host", tmp_path))
    assert json.loads(arg)["sandbox"]["enabled"] is True
    assert confine.settings_arg(confine.render("bwrap", tmp_path)) is None


# ---- is it going to work here? -------------------------------------------

def test_availability_is_probed_rather_than_looked_up():
    """`shutil.which` is not enough, and the difference is not hypothetical:
    bubblewrap installs fine where user namespaces are off, and the host
    runner's sandbox was found present, configured, and silently disabled
    because a second dependency was missing."""
    ok, why = confine.available("bwrap")
    assert ok or why, "a refusal must say why"
    ok, why = confine.available("nonsense")
    assert not ok and "not one of" in why


def test_the_two_policy_values_are_refused_rather_than_widened():
    """The exception `$DG_BUDGET` already is. A misread policy here is not a
    rule weakened by a notch — it is a run that believes it is confined and is
    not, which is the one thing a floor exists to rule out."""
    assert confine.mode("") == "off" and confine.backend("") == "host"
    with pytest.raises(ValueError):
        confine.mode("requrie")
    with pytest.raises(ValueError):
        confine.backend("seatbelt")
