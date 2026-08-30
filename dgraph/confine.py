"""The confinement floor: one policy, rendered per backend.

`limits` already answers *where may an agent write* and *what inside that must
it not touch*. A floor is those two answers enforced below the tool layer, so
that a shell redirection or a `patch` — neither of which any gate sees — is
refused by the kernel rather than by a rule nobody consulted.

**This module renders; it decides nothing.** The roots come from
`limits.writable_roots` and the protected paths from `limits.protected_paths`,
which is what keeps a mount set and a verdict from disagreeing about what the
record is. Add a backend by teaching it to express the same two lists.

Two exist, and they differ in *shape* rather than in strictness:

    bwrap   wraps the command      -> an argv prefix
    host    configures the runner  -> settings the spawn line must carry

That asymmetry is why `Launch` has two fields instead of one. It also decides
where each half is applied: an argv prefix is host-neutral, so `dg-agent run`
can prepend it to anything; settings are the runner's own vocabulary, so they
belong in the spawn line, which is already the host-specific string.

**Neither is on by default.** The broker ships without a floor and
`$DG_CONFINE` is `off` unless a launcher says otherwise — the floor is the
second layer, and the portable one is the first.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from dgraph import limits

#: The backends, and what each costs.
#:
#: `host` reaches Linux, macOS and Windows because the runner already
#: implements all three, and it is the reason cross-platform reach outweighed
#: host neutrality here: rolling one arena would mean bubblewrap *and* seatbelt
#: *and* Windows ACLs, three sandboxes to keep true, for a tool whose boundary
#: is that it runs anywhere Python does.
#:
#: `bwrap` reaches only Linux and works under **any** runner, including one
#: whose own sandbox does not exist. It is what a host-neutral or an opencode
#: run uses.
BACKENDS = ("host", "bwrap")

#: `$DG_CONFINE`. Whether a floor is required at all.
#:
#: `off` unless a launcher says otherwise, so nothing existing breaks and `dg`
#: gains no hard dependency. `dg-agent setup` proposes `require` anyway, which
#: is the pattern `fanout.Plan` already follows for three other values: a
#: fan-out is where the failure these guard against actually happens.
CONFINE_ENV = "DG_CONFINE"
CONFINE_MODES = ("off", "require")

#: `$DG_FLOOR`. Which backend, when one is required.
FLOOR_ENV = "DG_FLOOR"


@dataclass(frozen=True)
class Confinement:
    """The policy, before anybody has said how to express it."""

    writable: list[str]
    protected: list[str]


def policy(root: Path | None) -> Confinement:
    """What a floor must enforce — the same two lists the gate judges against.

    Read from `limits` rather than restated, because a floor built from its own
    idea of the writable roots would be free to seal something the gate allows,
    or allow something the gate refuses, and only one of those is visible.
    """
    return Confinement(writable=limits.writable_roots(root),
                       protected=limits.protected_paths(root))


@dataclass
class Launch:
    """How a backend changes the spawn.

    `prefix` is argv to put in front of the command, and is host-neutral —
    anything can be wrapped. `settings` is the runner's own configuration, and
    is not: only a spawn line that knows which runner it is calling can carry
    it. Keeping them apart is what lets `dg-agent run` apply one and leave the
    other to `fanout.HOSTS`.
    """

    prefix: list[str] = field(default_factory=list)
    settings: dict | None = None


def render(backend: str, root: Path | None) -> Launch:
    if backend not in BACKENDS:
        raise ValueError(f"${FLOOR_ENV}={backend} is not one of "
                         f"{', '.join(BACKENDS)}")
    return (_bwrap if backend == "bwrap" else _host)(policy(root))


def _bwrap(c: Confinement) -> Launch:
    """A mount namespace: read-only everywhere, writable where the policy says.

    **Order is load-bearing.** bwrap applies operations in sequence, so a
    writable root must be bound before the protected files under it are bound
    read-only over the top — the other way round and the writable bind hides
    the protection, silently.

    A read-only bind over a file refuses **rename and unlink**, not merely
    `open` for writing. That is what makes it hold against `sed -i`, which does
    not write in place but writes a temporary file and renames over the target.

    `--die-with-parent` because the budget is enforced by the parent and a
    child that outlived it would be an agent nobody is holding a lease for.
    `--new-session` so the tree cannot reach the launcher's terminal.

    Not `--clearenv`: `dg-agent run` composes the child's environment already,
    and clearing it here would strip the very remit it was composed to carry.
    """
    argv = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
            "--unshare-user", "--die-with-parent", "--new-session"]
    for w in c.writable:
        argv += ["--bind", w, w]
    for p in c.protected:
        # Only what exists: bwrap refuses to bind a source that is not there,
        # and a project whose task store has not been created yet is ordinary.
        if os.path.exists(p):
            argv += ["--ro-bind", p, p]
    argv.append("--")
    return Launch(prefix=argv)


def _host(c: Confinement) -> Launch:
    """The runner's own sandbox, as settings it must be started with.

    `denyWrite` carves the record back out of `allowWrite`, which is what lets
    both lists come from one policy: the writable roots go in whole and the
    stores are removed by name.

    `allowUnsandboxedCommands` is false because a sandbox with a documented way
    out is a sandbox nobody has to break. `denyRead` is deliberately empty:
    reads are never judged, and an agent that cannot read the repository it is
    reasoning about is blindfolded rather than constrained.

    Only honoured from a launcher's own settings, never from the project's —
    which is the property that matters here, since the project is exactly what
    the agent may write.
    """
    return Launch(settings={"sandbox": {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
        "filesystem": {"allowWrite": list(c.writable),
                       "denyWrite": list(c.protected)},
    }})


def settings_arg(launch: Launch) -> str | None:
    """`--settings` as a literal JSON string, for a spawn line to carry.

    A string on argv rather than a file, and deliberately: settings are read at
    launch, and a file inside the project would be one the agent could edit —
    though not, as it happens, to any effect, since a runner that honours these
    only from a launcher's own settings ignores the project's either way. Argv
    is the version that is obviously safe rather than safe by a second rule.
    """
    return None if launch.settings is None else json.dumps(
        launch.settings, separators=(",", ":"))


# ---- is this backend actually going to work? -----------------------------


def available(backend: str) -> tuple[bool, str]:
    """Whether `backend` would confine anything here, and why not if not.

    **Probed rather than looked up.** `shutil.which` is not enough and the
    difference is not hypothetical: bubblewrap installs fine on a hardened
    kernel and inside a container where user namespaces are off, and the host
    runner's own sandbox was found present, configured, and silently disabled
    because a second dependency was missing — it warned on the child's stderr,
    which in a headless run nobody reads.

    That is what this exists for, and it is why the answer carries a reason.
    """
    if backend == "bwrap":
        if shutil.which("bwrap") is None:
            return False, "bubblewrap is not installed"
        try:
            r = subprocess.run(["bwrap", "--ro-bind", "/", "/", "--dev", "/dev",
                                "--unshare-user", "true"],
                               capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"bubblewrap could not be run ({exc})"
        if r.returncode != 0:
            return False, ("bubblewrap is installed but cannot create a "
                           "namespace here — user namespaces are often off in "
                           "a container or on a hardened kernel")
        return True, ""
    if backend == "host":
        missing = [tool for tool in ("bwrap", "socat") if shutil.which(tool) is None]
        if missing:
            return False, (f"the host sandbox needs {' and '.join(missing)}, "
                           f"which {'is' if len(missing) == 1 else 'are'} not "
                           f"installed — without it the runner warns on the "
                           f"child's stderr and runs unconfined")
        return True, ""
    return False, f"{backend!r} is not one of {', '.join(BACKENDS)}"


def mode(value: str | None = None) -> str:
    """`$DG_CONFINE`, refused rather than widened when it cannot be read.

    The exception `$DG_BUDGET` already is, and for its reason: a misread policy
    here is not a rule weakened by a notch, it is a run that believes it is
    confined and is not.
    """
    raw = (value if value is not None
           else os.environ.get(CONFINE_ENV) or "").strip().lower()
    if not raw:
        return CONFINE_MODES[0]
    if raw not in CONFINE_MODES:
        raise ValueError(f"${CONFINE_ENV}={raw} is not one of "
                         f"{', '.join(CONFINE_MODES)}")
    return raw


def backend(value: str | None = None) -> str:
    """`$DG_FLOOR`, refused rather than widened, for the reason `mode` is."""
    raw = (value if value is not None
           else os.environ.get(FLOOR_ENV) or "").strip().lower()
    if not raw:
        return BACKENDS[0]
    if raw not in BACKENDS:
        raise ValueError(f"${FLOOR_ENV}={raw} is not one of "
                         f"{', '.join(BACKENDS)}")
    return raw


def preflight(root: Path | None = None, *, mode_: str | None = None,
              backend_: str | None = None) -> str | None:
    """Why the declared floor would not confine anything here — or `None`.

    **This is the finding the whole module was built around.** The runner's own
    sandbox does not fail closed: with a dependency missing it prints a warning
    on the child's stderr and runs the command unconfined. In a headless
    fan-out nobody reads that stream, so the run reports itself as configured —
    `env.json` says `require`, `dg-agent env` prints the remit — while nothing
    is in force. A rule that is removed silently in the direction of more
    permission is the failure this project already names for its own variables;
    this is the same failure one layer down.

    So the answer is a refusal at the launcher, where it can still be fixed,
    rather than a warning at a child nobody is watching.

    Silent when `$DG_CONFINE=off`, which is the default: a project that never
    asked for a floor must not be told it is missing one.
    """
    try:
        if mode(mode_) == "off":
            return None
        chosen = backend(backend_)
    except ValueError as exc:
        return str(exc)
    ok, why = available(chosen)
    if ok:
        return None
    return (f"${CONFINE_ENV}=require and ${FLOOR_ENV}={chosen}, but {why}. "
            f"Install what is missing, choose another backend, or say "
            f"${CONFINE_ENV}=off and mean it — a run that believes it is "
            f"confined and is not is worse than one that knows it is not.")
