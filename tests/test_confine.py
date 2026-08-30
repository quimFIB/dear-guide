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


# ---- the preflight, and the refusal it guards -----------------------------
#
# The finding this module was built around: the runner's own sandbox does not
# fail closed. With a dependency missing it warns on the child's stderr and
# runs the command unconfined — and in a headless fan-out nobody reads that
# stream, so the run reports itself configured while nothing is in force.

def test_a_project_that_asked_for_no_floor_is_not_told_it_is_missing_one():
    assert confine.preflight(mode_="off") is None
    assert confine.preflight(mode_="off", backend_="bwrap") is None


def test_a_required_floor_with_no_usable_backend_is_a_finding(monkeypatch):
    monkeypatch.setattr(confine, "available",
                        lambda b: (False, "bubblewrap is not installed"))
    why = confine.preflight(mode_="require", backend_="bwrap")
    assert why and "not installed" in why
    assert "=off and mean it" in why, "the refusal must name the honest way out"


def test_an_unreadable_policy_is_itself_the_finding():
    assert "not one of" in confine.preflight(mode_="requrie")
    assert "not one of" in confine.preflight(mode_="require", backend_="seatbelt")


def test_check_refuses_a_run_that_would_believe_it_is_confined(tmp_path, monkeypatch):
    """`dg-agent env --check` is what `fanout/launch.sh` runs before the first
    agent starts, so this is where the refusal has to land: at the launcher,
    where it can still be fixed."""
    from typer.testing import CliRunner
    from dgraph.agent_cli import app
    monkeypatch.setattr(confine, "available", lambda b: (False, "nothing here"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DG_CONFINE", "require")
    assert CliRunner().invoke(app, ["env", "--check"]).exit_code == 1
    monkeypatch.setenv("DG_CONFINE", "off")
    assert CliRunner().invoke(app, ["env", "--check"]).exit_code == 0


def test_run_refuses_before_it_claims_a_name(tmp_path, monkeypatch):
    """Nothing spawned and no name claimed, which is the promise `_compose`
    already makes for a value it cannot read."""
    from typer.testing import CliRunner
    from dgraph import agents
    from dgraph.agent_cli import app
    monkeypatch.setattr(confine, "available", lambda b: (False, "nothing here"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DG_CONFINE", "require")
    res = CliRunner().invoke(app, ["run", "--", "true"])
    assert res.exit_code == 2
    assert agents.load(tmp_path) == {}, "a name was claimed for a run that never happened"


def test_only_the_host_neutral_half_is_prepended(tmp_path, monkeypatch):
    """`dg-agent run` prepends an argv prefix and nothing else. A backend whose
    floor is the runner's own settings has nothing to prepend — its half rides
    the spawn line, which is where anything host-specific belongs."""
    from dgraph.agent_cli import _floor_prefix
    monkeypatch.chdir(tmp_path)
    assert _floor_prefix({}) == []
    assert _floor_prefix({"DG_CONFINE": "require", "DG_FLOOR": "host"}) == []
    prefix = _floor_prefix({"DG_CONFINE": "require", "DG_FLOOR": "bwrap"})
    assert prefix and prefix[0] == "bwrap" and prefix[-1] == "--"


# ---- backend x launcher: is the carrier this backend needs applied? --------
#
# `P-F2`. The preflight asks whether a backend is *available*. Neither half of
# it asked whether the carrier that backend renders was ever *applied*, and for
# `host` — the default — `dg-agent run` applies nothing at all.

def test_a_backend_says_which_half_of_the_launch_carries_it(tmp_path):
    """The seam, asked as a question rather than inferred at each call site.

    `bwrap` renders an argv prefix, which anything can prepend; `host` renders
    settings, which only a spawn line speaking that runner's vocabulary can
    carry. Every refusal below turns on this distinction, so it is derived from
    the rendering rather than written down beside it."""
    assert confine.configures_runner("host", tmp_path)
    assert not confine.configures_runner("bwrap", tmp_path)


def test_a_run_that_cannot_apply_its_own_floor_is_refused(tmp_path, monkeypatch):
    """The finding: `$DG_CONFINE=require` with the default backend spawned an
    unconfined child while every surface reported a floor."""
    from typer.testing import CliRunner
    from dgraph import agents
    from dgraph.agent_cli import app
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(confine, "available", lambda b: (True, ""))

    res = CliRunner().invoke(app, ["run", "--", "true"], env={
        "DG_CONFINE": "require", "DG_FLOOR": "host"})
    assert res.exit_code == 2, res.output
    assert agents.load(tmp_path) == {}, "a name was claimed for a run that never happened"

    # …and the token is what says the other half was applied upstream.
    ok = CliRunner().invoke(app, ["run", "--floor-applied", "--", "true"], env={
        "DG_CONFINE": "require", "DG_FLOOR": "host"})
    assert ok.exit_code == 0, ok.output


def test_the_token_is_only_about_the_half_this_command_cannot_apply(tmp_path,
                                                                    monkeypatch):
    """`bwrap` is prepended here, so it needs no assertion from anybody — and
    claiming one must not be how a launcher gets out of a floor that *is*
    applicable."""
    from typer.testing import CliRunner
    from dgraph.agent_cli import app, _floor_prefix
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(confine, "available", lambda b: (True, ""))
    assert _floor_prefix({"DG_CONFINE": "require", "DG_FLOOR": "bwrap"})
    res = CliRunner().invoke(app, ["run", "--", "true"], env={
        "DG_CONFINE": "require", "DG_FLOOR": "bwrap"})
    assert res.exit_code == 0, res.output


# ---- the store is sealed on purpose; say so (`P-F4`) ----------------------

def test_a_sealed_store_says_which_rule_sealed_it(tmp_path, monkeypatch):
    """`T04` taught the *gate* to name the store rather than leaving the kernel
    to say it. `dg`'s own writes do not go through the gate and still met the
    kernel: `dg apply --mine` — the step the agent loop runs right after
    `dg task start` — failed with `Device or resource busy` reported as
    "tasks.json could not be read", which is wrong about the operation and
    names a `dg check` that will report a healthy graph."""
    from dgraph import project
    store = tmp_path / project.STORE_NAME
    store.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("DG_CONFINE", "require")

    def sealed(*a, **kw):
        raise OSError(16, "Device or resource busy")

    monkeypatch.setattr(project.os, "replace", sealed)
    with pytest.raises(project.Sealed) as exc:
        project.write_atomic(store, "{}")

    said = str(exc.value)
    assert "confinement floor" in said and project.STORE_NAME in said
    assert "dg apply" in said or "supervisor" in said
    assert "could not be read" not in said


def test_an_ordinary_write_failure_is_not_dressed_up_as_a_floor(tmp_path,
                                                                monkeypatch):
    """A full disk under a confined run is still a full disk. Only the two
    errors a read-only bind actually raises, and only on a protected path."""
    from dgraph import project
    monkeypatch.setenv("DG_CONFINE", "require")
    store = tmp_path / project.STORE_NAME
    store.write_text("{}", encoding="utf-8")

    def full(*a, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(project.os, "replace", full)
    with pytest.raises(OSError) as exc:
        project.write_atomic(store, "{}")
    assert not isinstance(exc.value, project.Sealed)

    # …and an ordinary file, sealed or not, is nobody's record.
    other = tmp_path / "findings.md"
    monkeypatch.setattr(project.os, "replace",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            OSError(16, "Device or resource busy")))
    with pytest.raises(OSError) as exc:
        project.write_atomic(other, "x")
    assert not isinstance(exc.value, project.Sealed)


def test_no_floor_declared_means_no_floor_blamed(tmp_path, monkeypatch):
    """`$DG_CONFINE` unset is the default. A busy store there is a fact about
    the filesystem, not about a rule nobody asked for."""
    from dgraph import project
    monkeypatch.delenv("DG_CONFINE", raising=False)
    store = tmp_path / project.TASKS_NAME
    store.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(project.os, "replace",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            OSError(16, "Device or resource busy")))
    with pytest.raises(OSError) as exc:
        project.write_atomic(store, "{}")
    assert not isinstance(exc.value, project.Sealed)
