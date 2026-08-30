"""The consent broker: the supervisor's half of a verdict that waits.

`dg gate` is a pure function -- a path or a command in, a verdict out. Where it
cannot answer it says `ask`, and in a headless run there is nobody to ask, so
`ask` collapses into a refusal that was never the consent the rule intended.
This is the process standing where that person would be.

**It holds no policy.** `limits` decides what is out of scope and `gate`
composes the verdict; a request arrives here already carrying the gate's own
conclusion and its reason. What this adds is the one thing a pure function
cannot have: somebody to ask, a memory of what was already granted, and a view
of every agent at once.

    gate (agent-side)  ──unix socket──►  broker (supervisor-side)
                       ◄── allow/deny

**Never `ask`.** Resolving is the whole job; an adapter that received `ask`
from here would be back where it started.

Two rungs decide who answers, and they are read *here* rather than by the gate,
which runs inside the agent's own process -- see `LADDERS`.

**Grants are memory, never a file.** `.dgraph-agents.json` has to stay
writable by agents because the heartbeat stamps it, so a grant written there
would be a grant an agent could award itself. Held here they die with the run,
which is what lease-scoped already meant.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path

from dgraph import limits, project

#: Where the broker listens, beside both trays. `.dgraph-*` is already in the
#: `.gitignore` `dg init` writes, so it is covered the day it first appears --
#: it is scratch, it is per-checkout, and a socket that travelled between
#: clones would name a supervisor who is not running here.
SOCKET_NAME = ".dgraph-consent.sock"

#: What was asked and what was answered, one JSON object a line. The shape
#: `net/egress.py` uses for the same reason: *the log is the evidence for the
#: claim*. A run with no record of what was granted cannot be audited
#: afterwards, and the grants themselves are gone the moment the broker exits.
LOG_NAME = ".dgraph-consent.jsonl"

#: The rungs, per kind. `$DG_CONSENT_EXEC` sits one stricter than
#: `$DG_CONSENT_WRITE` by default because writing a file inside a known tree is
#: routine and running a program is the thing that can do anything at all.
LADDERS = {"write": ("DG_CONSENT_WRITE", "scoped"),
           "exec": ("DG_CONSENT_EXEC", "user")}

RUNGS = ("off", "auto", "scoped", "user")

#: How long a `user` request may wait. Deliberately generous and deliberately
#: not the real bound: the agent's own `$DG_BUDGET` is what actually stops it,
#: and a gate timeout shorter than a person's attention would turn every
#: considered answer into a refusal.
USER_WAIT = 900


def socket_path(root: Path | None = None) -> Path:
    return (root or project.find().root) / SOCKET_NAME


def rung(kind: str, environ: dict | None = None) -> str:
    """Which rung governs `kind`, read from the broker's own environment.

    **Read here and never in the gate**, which is the placement this design
    turns on. The gate runs inside the agent's process, so a ladder read there
    is a ladder the agent can widen; read at broker startup it is out of reach.
    That makes these the first policy variables in the tool that are not
    cooperative, and it costs nothing to get.

    An unreadable value raises rather than widening. These are read once, by a
    process a person just started, where a typo can still be fixed -- the
    fail-open rule that protects the shared tray has nothing to protect here.
    """
    name, default = LADDERS[kind]
    raw = ((os.environ if environ is None else environ).get(name) or "").strip().lower()
    if not raw:
        return default
    if raw not in RUNGS:
        raise ValueError(f"${name}={raw} is not one of {', '.join(RUNGS)}")
    return raw


@dataclass
class Grant:
    """One thing an agent may keep doing without being asked again.

    A write grant is a **root** and a command grant is a **literal**, which is
    not a stylistic pair. A root is matched by the
    containment test `limits` already uses, and a literal has nothing to
    expand: repeating a command verbatim is free and one character different is
    a fresh decision. A prefix would have granted a shell.
    """

    kind: str
    value: str

    def covers(self, kind: str, target: str) -> bool:
        if kind != self.kind:
            return False
        if kind == "exec":
            return target.strip() == self.value
        return limits._within(limits._real(target), self.value)


@dataclass
class Broker:
    """The decider. Serves one request at a time, on purpose.

    Serial because a person answers serially, and a queue of prompts racing
    each other in one terminal is unreadable. Agents blocked behind one another
    are visible as `Waiting` in `dg-agent list` rather than inferred from
    silence.
    """

    root: Path
    #: `ask(request) -> (verdict, grant | None)`. The terminal front end by
    #: default; a test or a future TUI supplies its own. There is no TUI today
    #: -- `dg-agent setup` has both a form and a question at a time, so
    #: one can be added later against the same answers.
    prompt: object = None
    grants: dict[str, list[Grant]] = field(default_factory=dict)
    #: Set for `auto`, where the policy is evaluated here rather than by a
    #: person. Same rules, different process -- and this side can see every
    #: agent at once, which a gate in one agent's process cannot.
    auto: object = None
    log_path: Path | None = None
    #: The rungs in force, read **once** at construction rather than per
    #: request. The environment is not policy that may change mid-run: a rung
    #: that moved between two requests would be a rule nobody declared, and a
    #: broker that re-read it would answer two identical requests differently
    #: with nothing in the log to say why.
    rungs: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.rungs:
            self.rungs = {k: rung(k) for k in LADDERS}

    def granted(self, agent: str, kind: str, target: str) -> Grant | None:
        for g in self.grants.get(agent, ()):
            if g.covers(kind, target):
                return g
        return None

    def decide(self, req: dict) -> dict:
        """One request, answered. Never raises and never answers `ask`.

        The order is memory, then rung, then a person -- so a granted scope
        costs no prompt, and the round trip it does cost is a socket call
        rather than somebody's attention, which is the economy that mattered.
        """
        agent = (req.get("agent") or "").strip()
        kind = req.get("kind")
        target = req.get("target") or ""
        if kind not in LADDERS:
            return _answer(req, "deny", f"unknown request kind {kind!r}")
        if not agent:
            # A supervisor never reaches here -- `refuse_*` returns None for an
            # unowned caller long before the gate composes a request -- so an
            # unsigned one is a bug or a forgery, and either way not consent.
            return _answer(req, "deny", "no agent named in the request")

        held = self.granted(agent, kind, target)
        if held is not None:
            return _answer(req, "allow", f"already granted: {held.value}",
                           grant=None, remembered=True)

        level = self.rungs.get(kind)
        if level not in RUNGS:
            return _answer(req, "deny", f"no rung in force for {kind!r}")

        if level == "off":
            return _answer(req, "deny",
                           f"${LADDERS[kind][0]}=off — this broker answers "
                           f"nothing; the gate's own verdict stands")
        if level == "auto":
            allow, why, grant = self._auto(req)
            return self._settle(req, agent, allow, why, grant)
        if level == "scoped" or level == "user":
            if self.prompt is None:
                return _answer(req, "deny", "no way to ask — broker has no "
                                            "front end attached")
            allow, why, grant = self.prompt(req, level)
            return self._settle(req, agent, allow, why, grant)
        return _answer(req, "deny", f"unhandled rung {level!r}")

    def _auto(self, req) -> tuple[bool, str, Grant | None]:
        if self.auto is None:
            return False, "no auto policy is attached to this broker", None
        return self.auto(req)

    def _settle(self, req, agent, allow, why, grant) -> dict:
        if allow and grant is not None:
            self.grants.setdefault(agent, []).append(grant)
        return _answer(req, "allow" if allow else "deny", why,
                       grant=grant)

    # ---- the socket ------------------------------------------------------

    def serve(self, stop: threading.Event | None = None) -> None:
        """Listen until `stop` is set. One connection, one request, one answer.

        Connection per request rather than a session, because a request is
        already the unit a gate blocks on: a gate that died mid-wait leaves a
        closed socket and nothing to reap.
        """
        path = self.root / SOCKET_NAME
        # Bound under another name and renamed into place once it is *accepting*.
        # `bind` creates the file, and `listen` is what starts answering — so
        # binding directly at `path` opens a window where `listening()` is true
        # and a connection gets ECONNREFUSED, which `consult` correctly reads as
        # an unreachable decider and turns into a deny. A spurious refusal at a
        # blocked agent is exactly what this whole seam exists to avoid, and the
        # rename is atomic.
        staging = path.with_name(f"{SOCKET_NAME}.{os.getpid()}")
        for stale in (path, staging):
            with contextlib.suppress(FileNotFoundError):
                stale.unlink()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(str(staging))
            # 0700, because the socket *is* the authority to grant: anyone who
            # can connect can answer as the supervisor.
            os.chmod(staging, 0o700)
            srv.listen(16)
            os.rename(staging, path)
            srv.settimeout(0.5)
            while stop is None or not stop.is_set():
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                with conn:
                    self._one(conn)
        finally:
            srv.close()
            for gone in (path, staging):
                with contextlib.suppress(FileNotFoundError):
                    gone.unlink()

    def _one(self, conn: socket.socket) -> None:
        try:
            raw = _read_line(conn)
            req = json.loads(raw) if raw else {}
        except Exception as exc:
            _write_line(conn, {"v": 1, "verdict": "deny",
                               "reason": f"unreadable request ({exc!r})"})
            return
        try:
            res = self.decide(req)
        except Exception as exc:  # never raise at a blocked agent
            res = _answer(req, "deny", f"the broker could not decide ({exc!r})")
        self.record(req, res)
        _write_line(conn, res)

    def record(self, req: dict, res: dict) -> None:
        """One line of evidence. Best-effort: a broker that stopped answering
        because its log filled a disk would be worse than an unlogged grant."""
        path = self.log_path if self.log_path is not None else self.root / LOG_NAME
        entry = {"agent": req.get("agent"), "kind": req.get("kind"),
                 "target": req.get("target"), "task": (req.get("holding") or {}).get("task"),
                 "verdict": res.get("verdict"), "reason": res.get("reason"),
                 "grant": res.get("grant"), "remembered": res.get("remembered", False)}
        with contextlib.suppress(Exception):
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _answer(req: dict, verdict: str, reason: str, *, grant: Grant | None = None,
            remembered: bool = False) -> dict:
    out = {"v": 1, "id": req.get("id"), "verdict": verdict, "reason": reason}
    if grant is not None:
        out["grant"] = {"kind": grant.kind, "value": grant.value, "until": "lease"}
    if remembered:
        out["remembered"] = True
    return out


def _read_line(conn: socket.socket) -> str:
    chunks = []
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    return b"".join(chunks).decode("utf-8", "replace").strip()


def _write_line(conn: socket.socket, obj: dict) -> None:
    with contextlib.suppress(OSError):
        conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode())


# ---- the terminal front end ----------------------------------------------


def terminal_prompt(req: dict, level: str) -> tuple[bool, str, Grant | None]:
    """Ask the person at the terminal. One line, no TUI.

    The request carries **why** -- the task it is under and the decision that
    task bears on -- because `dg context` can compute it and a prompt that says
    only "an agent wants to run a command" is one people learn to approve
    blindly.
    """
    holding = req.get("holding") or {}
    where = ", ".join(x for x in (holding.get("task"),
                                  holding.get("evidence_for")) if x)
    head = f"{req.get('agent')} wants to {_verb(req)}:"
    print(f"\n{head}\n  {req.get('target')}")
    if where:
        print(f"  ({where})")
    if req.get("gate", {}).get("reason"):
        print(f"  {req['gate']['reason']}")
    choices = "[a]llow once  [s]cope  [d]eny" if level == "scoped" \
        else "[a]llow once  [d]eny"
    while True:
        try:
            answer = input(f"  {choices}: ").strip().lower()[:1]
        except (EOFError, KeyboardInterrupt):
            print()
            return False, "no answer from the supervisor", None
        if answer == "a":
            return True, "allowed once by the supervisor", None
        if answer == "d":
            return False, "declined by the supervisor", None
        if answer == "s" and level == "scoped":
            return True, "scope granted by the supervisor", _scope_for(req)


def _verb(req: dict) -> str:
    return "run" if req.get("kind") == "exec" else "write"


def _scope_for(req: dict) -> Grant:
    """What a grant covers: a **root** for a write, the **literal** for a
    command. See `Grant` for why each is what it is."""
    target = req.get("target") or ""
    if req.get("kind") == "exec":
        return Grant("exec", target.strip())
    return Grant("write", limits._real(str(Path(target).parent)))


# ---- the gate's side -----------------------------------------------------

#: How often a blocked gate stamps its lease while it waits.
#: `agents.touch` fires per `dg` invocation, and a gate blocked for four
#: minutes is *inside* one -- so without this `dg-agent list` shows the agent
#: silent and `dg-agent expire` parks work that was only ever waiting on a
#: person. `Seen` then no longer tells blocked from alive on its own, which is
#: why the `Waiting` column is not optional.
BEAT_EVERY = 20


def listening(root: Path | None = None) -> bool:
    """Whether a broker is there to ask. Absence is not a degraded broker --
    it is *no broker*, and the gate then returns the verdict it always did."""
    try:
        return socket_path(root).is_socket()
    except OSError:
        return False


def consult(req: dict, root: Path | None = None,
            timeout: float = USER_WAIT) -> dict | None:
    """Ask the broker, blocking until it answers. `None` if there is none.

    `None` rather than a verdict, so the caller can tell *no broker* from
    *the broker said no*: the first leaves today's behaviour in place and the
    second is a decision somebody made.

    A failure to reach a socket that exists is a **deny**, not a fallthrough.
    An unreachable decider is not consent, and this is the one place in the
    tool that fails closed -- everything else here guards a rule, while this
    guards an answer nobody gave.
    """
    path = socket_path(root)
    if not listening(root):
        return None
    beat = _heartbeat(req.get("agent"), root)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(path))
            s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode())
            raw = _read_line(s)
        return json.loads(raw) if raw else _unreachable("the broker closed "
                                                        "without answering")
    except (OSError, ValueError) as exc:
        return _unreachable(f"the broker did not answer ({exc!r})")
    finally:
        beat.set()


def _unreachable(why: str) -> dict:
    return {"v": 1, "verdict": "deny",
            "reason": f"{why} — an unreachable decider is not consent. "
                      f"`dg-agent broker` is what answers this."}


def _heartbeat(agent: str | None, root: Path | None) -> threading.Event:
    """Stamp `agent`'s lease every `BEAT_EVERY` seconds until the event is set."""
    stop = threading.Event()
    if not agent:
        return stop

    def beat() -> None:
        from dgraph import agents
        while not stop.wait(BEAT_EVERY):
            with contextlib.suppress(Exception):
                agents.touch(agent, root, every=0)

    threading.Thread(target=beat, daemon=True).start()
    return stop


def request(kind: str, target: str, agent: str, reason: str,
            holding: dict | None = None, roots: list[str] | None = None) -> dict:
    """The object the gate sends, and why each field is in it.

    `id` is stable over `(agent, kind, target)` so a retry re-attaches to a
    pending decision rather than queueing a second prompt at the same person.
    `gate` carries the gate's own conclusion because **the broker decides and
    never re-derives policy** -- `limits` stays the single home, which is the
    "adapters hold no policy of their own" rule one level up.
    """
    import hashlib
    ident = hashlib.sha256(f"{agent}|{kind}|{target}".encode()).hexdigest()[:12]
    return {"v": 1, "id": ident, "agent": agent, "kind": kind,
            "target": target, "gate": {"verdict": "ask", "reason": reason},
            "holding": holding or {}, "roots": roots or []}
