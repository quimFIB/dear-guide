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

**It also acts, for one closed class of ops, and still holds no policy.** Under
a confinement floor the two stores are read-only to everything inside it, `dg
apply` cannot write and the agent's own claim on its own work reaches nothing
-- so D17 and D43 put the write here, on the outside, where the hands are. That
makes this the only process that writes a sealed store during a run, which
looks like the paragraph above being retracted and is not: `limits.mechanical`
says which ops qualify and `task_pending.vet` says whether they apply at all.
This composes neither. The claim was never that the broker does not act; it is
that the broker holds no rule of its own, and an actor that consults for every
judgement keeps it.

    gate (agent-side)  ──unix socket──►  broker (supervisor-side)
                       ◄── allow/deny
    dg apply (sealed)  ──unix socket──►  broker  ──►  tasks.json
                       ◄── applied/deny

`add_task`, a claim, a park and a link go through it; `dg task done` does not,
because finishing asserts the criteria were met and that is a judgement about
the record rather than a fact about the run. D15 draws that line and D44 puts
the link on the near side of it.

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
import time
from queue import Empty as _Empty
from queue import SimpleQueue as _Queue
from dataclasses import dataclass, field
from pathlib import Path

from dgraph import env as _env, limits, project

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

#: Who is blocked right now, and on what. One object, rewritten whole, beside
#: the log.
#:
#: **The broker owns this and nothing else can** (D28). It is the only party
#: that knows a request is outstanding and for how long; the lease file cannot
#: hold it, because the heartbeat has to keep that writable by the agent and a
#: waiting state an agent could write is one it could clear.
#:
#: Its absence is not "nobody is waiting" — it is "no broker is running", which
#: is why `waiting()` answers an empty map for both and `dg-agent list` shows
#: the column only when there is a broker to have filled it.
WAITING_NAME = ".dgraph-consent.waiting.json"

#: The relay's two files: the question, and the answer to it.
#:
#: Two rather than one field in one file, because they have **two different
#: writers** — the broker publishes the ask, and whoever is relaying writes the
#: answer — and a single file rewritten whole by both is a lost answer the
#: first time they overlap. Serial by construction: `decide` has one caller and
#: handles one request at a time, so there is never a second ask outstanding and
#: neither file needs a queue.
#: **They do not live in the project, and that is the whole of their safety.**
#: The first version put them beside the socket, where every agent may write
#: freely under `$DG_WRITE=launch` and where a confinement floor seals nothing
#: -- so an agent wrote its own `allow`, the blocked agent collected it, and the
#: log recorded `by: person` with whatever reason the forgery supplied. That is
#: the lie `answered_by` exists to prevent, arriving through the channel meant
#: to carry the truth.
#:
#: The rule they now meet is the one the memory-only grants already met:
#:
#: > **The consent channel lives where an agent cannot write without already
#: > holding the permission it is asking for.**
#:
#: It matters more than the tool's other rules because those are cooperative by
#: design and the floor is not. The broker is the one door through a kernel
#: boundary, and a self-serve door is not a door.
RELAY_SOCK_NAME = "consent.sock"


def channel_dir(root: Path | None = None) -> Path:
    """Where the relay's two files live: outside every writable root.

    `$XDG_RUNTIME_DIR` where there is one -- per-user, `0700`, and cleared at
    logout, which is right for something that means nothing once the run is
    over -- else a `0700` directory under the user's cache. Per project, so two
    fan-outs never answer each other's questions.

    Deliberately **not** configurable by the environment. A path an agent could
    name is a path an agent could point back into its own scope, which is this
    hole reopened as a flag.
    """
    import hashlib
    root = root if root is not None else project.find().root
    base = os.environ.get("XDG_RUNTIME_DIR")
    parent = (Path(base) if base else Path.home() / ".cache") / "dear-guide"
    ident = hashlib.sha256(str(limits._real(root)).encode()).hexdigest()[:16]
    out = parent / "consent" / ident
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    return out


def unrelayable(root: Path | None = None) -> str | None:
    """Why relaying would not be safe here -- or `None`.

    Checked at the door, like `unbindable` and `unattachable` beside it, and
    against `limits.writable_roots` rather than a list of its own: the question
    is exactly *could an agent write this*, and that function is what answers
    it everywhere else in the tool.
    """
    root = root if root is not None else project.find().root
    sock = channel_dir(root) / RELAY_SOCK_NAME
    where = limits._real(channel_dir(root))
    for allowed in limits.writable_roots(root):
        if limits._within(where, allowed):
            return (f"the consent channel would sit at {where}, inside "
                    f"{allowed} — which every agent may write under "
                    f"$DG_WRITE=launch, so an agent could reach it without "
                    f"needing the very permission it is asking for. "
                    f"Set $XDG_RUNTIME_DIR to a directory outside the project")
    return _too_long(sock)

#: How long a relay front end waits before answering for nobody.
#:
#: Just over the 100s both host adapters pass as `--deadline`, and that
#: relation is the whole of the number: a relay that waited `USER_WAIT` would
#: hold the broker — which answers **serially** — for fifteen minutes on a
#: request whose caller gave up after one and a half, with every other agent
#: queued behind a person who is no longer being useful. Waiting slightly
#: longer than the caller does means the person's answer is never thrown away
#: by the front end before the caller has stopped listening for it.
RELAY_WAIT = 120

#: How much longer than the caller this end holds a question open, so the
#: person's answer is never thrown away here before the caller has stopped
#: listening for it. Small: past the caller's bound the verdict is undeliverable
#: anyway, and holding the broker — which decides serially — past that point
#: queues every other agent behind somebody who can no longer help.
RELAY_MARGIN = 20

#: The rungs, per kind. `$DG_CONSENT_EXEC` sits one stricter than
#: `$DG_CONSENT_WRITE` by default because writing a file inside a known tree is
#: routine and running a program is the thing that can do anything at all.
LADDERS = {"write": ("DG_CONSENT_WRITE", "scoped"),
           "exec": ("DG_CONSENT_EXEC", "user")}

#: The one request that is not a consent question. `LADDERS` is a map from a
#: kind to the rung that answers it, and there is no rung here: `limits.
#: mechanical` decides, mechanically, and no person is ever asked. Kept out of
#: `LADDERS` rather than given a fourth rung so that `unattachable` and the
#: rung readings go on describing exactly the questions a person can be asked.
APPLY_KIND = "apply"

#: How long a writer waits for its own ops to land. Nobody is being asked, so
#: this bounds a queue rather than a person: the broker decides serially, and a
#: mechanical apply can arrive behind an `exec` question somebody is thinking
#: about. Short on purpose -- giving up leaves the ops staged, which is exactly
#: where a refusal leaves them, and a supervisor applies them at the end anyway.
APPLY_WAIT = 60.0

RUNGS = ("off", "auto", "scoped", "user")

#: How long a `user` request may wait when **nobody named a bound** — a person
#: asking `dg gate` at a terminal, who can wait as long as they like.
#:
#: It is not the bound that matters in a run. **The caller names that** (D26):
#: an adapter passes `--deadline`, the gate answers `deny` before it runs out,
#: and the adapter's own give-up branch is never reached. That placement is the
#: whole of `P-F1`'s fix, and the reason is that the waiting is paid for by the
#: caller — a callee that outlasts its caller has its verdict discarded by
#: whatever that caller's timeout branch already did, which for
#: `hooks/prewrite.py` was to allow the write in silence.
USER_WAIT = 900


#: What `AF_UNIX` allows a socket path, in bytes, less the room `serve` needs
#: for its `.{pid}` staging suffix. The kernel's own limit is 108 including the
#: terminator and is not raisable; a path over it fails at `bind` and at
#: `connect` alike.
SUN_PATH_MAX = 100


def socket_path(root: Path | None = None) -> Path:
    return (root or project.find().root) / SOCKET_NAME


def _too_long(path: Path) -> str | None:
    """Why this socket path will not bind — or `None`. The kernel's cap.

    Shared by the broker's socket and the relay's, because the limit is the
    kernel's and not either socket's, and a second copy would be a second
    number to keep at 100.
    """
    room = len(str(path).encode()) + len(f".{2**22}")
    if room <= SUN_PATH_MAX:
        return None
    return (f"a unix socket path is capped at {SUN_PATH_MAX} bytes and this "
            f"one needs {room}:\n  {path}\nNothing here can raise that — it "
            f"is the kernel's limit.")


def unbindable(root: Path | None = None) -> str | None:
    """Why a broker could not listen in this project — or `None`.

    A deep checkout is an ordinary thing: a nested workspace, a CI working
    directory. The failure was an `OSError: AF_UNIX path too long` raised
    through typer as a traceback, and — on the gate's side of the same
    limit — a `deny` reading *"the broker did not answer"*, which names a
    supervisor who was never reachable rather than a path that is too long.
    `P-F9`.
    """
    path = str(socket_path(root))
    room = len(path.encode()) + len(f".{2**22}")
    if room <= SUN_PATH_MAX:
        return None
    return (f"a unix socket path is capped at {SUN_PATH_MAX} bytes and this "
            f"project needs {room}:\n  {path}\nNothing here can raise that — "
            f"it is the kernel's limit. Run the fan-out from a shorter path, "
            f"or symlink this checkout somewhere shorter and work through the "
            f"link.")


def unattachable(rungs: dict[str, str], auto: object = None) -> str | None:
    """Why this broker could not honour the rungs it was given — or `None`.

    `auto` is the rung that decides *here* instead of at a person, so it needs
    a policy attached to decide with. With none, `decide` takes the auto branch
    and answers `deny` to every request — while the banner still prints
    `auto`, `dg-agent list` shows nobody blocked, since `_waiting` is published
    only around a prompt, and the terminal stays as quiet as it does when
    nothing has been asked yet. Three readings that all say the run is healthy.

    That is the failure this process was added to remove, arriving in the
    process itself: a run reporting itself configured while nothing whatever is
    in force. So it is refused **at the door** — like `unbindable` above, like
    `confine.preflight` one layer down — rather than once per request, and the
    refusal names the way out, because a check that offered none would be
    answered by deleting the check.

    Takes the policy that will actually be attached rather than reading a
    module global: the day one exists, this opens by itself and there is no
    second place to remember.
    """
    if auto is not None:
        return None
    named = sorted(k for k, v in (rungs or {}).items()
                   if v == "auto" and k in LADDERS)
    if not named:
        return None
    which = ", ".join(f"${LADDERS[k][0]}" for k in named)
    is_are = "is" if len(named) == 1 else "are"
    return (f"{which} {is_are} set to `auto`, and this broker has no auto "
            f"policy to attach — the rung would answer `deny` to every request "
            f"while reporting itself as configured. Use `scoped`, where you "
            f"are asked once per command and `[s]cope` remembers what you "
            f"granted; or `off`, where this answers nothing and the gate's own "
            f"verdict stands.")


#: What the host adapters give the gate, and therefore the shortest bound any
#: request will carry in an ordinary run. Read from `hooks/prewrite.py` by
#: `tests/test_plugin.py` rather than copied — this is the *floor check's*
#: number and the chain test is what keeps it the same one.
ADAPTER_DEADLINE = 100


def unwaitable(wait: float | None) -> str | None:
    """Why this `--relay-wait` would discard answers — or `None`.

    Refused **at the door**, like `unbindable`, `unattachable` and
    `unrelayable` beside it, and for the reason they are: a relay that will
    throw away verdicts is knowable before anything binds, and a check made
    once per request would be a refusal the supervisor meets in the middle of
    a run.

    Under the caller's bound the relay gives up first, and the answer a person
    was two seconds from giving is discarded with nobody told. `wait_for` now
    reads the bound out of each request, so this only bites a request that
    carries none — but that is exactly the terminal case, and a number chosen
    below the adapters' is a number chosen wrong. Audit `G-F3`.
    """
    if wait is None or wait > ADAPTER_DEADLINE:
        return None
    return (f"--relay-wait {wait:g} is under the {ADAPTER_DEADLINE}s both host "
            f"adapters give the gate, so a request carrying no deadline of its "
            f"own would be abandoned here while its caller was still waiting — "
            f"and the answer you were about to give would be thrown away with "
            f"nothing to say so. Use at least {ADAPTER_DEADLINE + 1}; the "
            f"default is {RELAY_WAIT}.")


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


class NoAnswer(Exception):
    """A front end that was asked and got nothing back.

    Distinct from a front end that answered `deny`, and the distinction is the
    whole of `answered_by`: a person declining and nobody being there produce
    the same verdict for the agent — a refusal — and must not produce the same
    *record*, because only one of them is consent somebody withheld. The first
    version of the relay logged an unanswered timeout as `person`, which is
    exactly the lie `D37`'s falsifier watches for.

    Raised by a front end, caught in `decide`, answered `unanswered`.
    """


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
    silence — **which was this docstring's claim before it was true.** Requests
    were accepted and decided in one loop, so a queued one sat unread in the
    backlog, never reached `decide`, published nothing, and read as an agent
    working normally with a fresh heartbeat. `G-F4`.

    A reader thread accepts and reads now, so an agent is visible before its
    turn; `serve` remains the **only** caller of `decide`, so serial is still
    a property of the shape and not a lock to be held. That mattered enough to
    choose the two-thread form over a thread per connection: `Relay`'s single
    slot rests on it, and converting a structural guarantee into a maintained
    one is the move this repo files as a finding when it meets it elsewhere.
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
    #: Whether a verdict this broker relays may be logged `person`. Set by the
    #: caller from `unprovable`, which reads the floor — not derived here,
    #: because the *caller* is the one that knows whether it is relaying at
    #: all, and a terminal answer never crosses the channel. `D40`.
    unprovable: bool = False
    #: The waiting map is rewritten whole and two threads reach it — the reader
    #: publishing a queued agent, and the decider publishing and clearing the
    #: one being asked. Read-modify-write on a shared file, which is the shape
    #: this repo has a whole audit pass about.
    #:
    #: **It is the only thing the two threads share**, and that is deliberate:
    #: `decide` has exactly one caller, so the grants, the log and the verdict
    #: need no lock and the serial property needs no maintaining. `G-F4`.
    _status: threading.Lock = field(default_factory=threading.Lock, repr=False)

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

        **Every exit clears the waiting state**, not only the prompted one. A
        request is published as queued before its turn comes, and most requests
        never reach a prompt at all — a remembered grant, `off`, a target
        outside the floor's mounts. Left behind, those entries are an agent
        shown as blocked forever on a decision that was made instantly. `G-F4`.
        """
        try:
            return self._decide(req)
        finally:
            with contextlib.suppress(Exception):
                self._waiting((req.get("agent") or "").strip(), None)

    def _apply(self, req: dict) -> dict:
        """Land the requester's own mechanically-appliable ops. D17, D43, D15.

        **The broker acts here, and still holds no rule.** `limits.mechanical`
        says which ops qualify and `task_pending.vet` says whether they apply
        at all; this composes neither. That is the same division the module
        header states for a verdict, reaching one act.

        **Whole or nothing.** The eligible ops go in tray order, per D42, and a
        batch that will not apply leaves everything staged rather than landing
        a prefix -- `apply_tasks` validates against a copy before it writes,
        and the trays are held across the read so a stage arriving mid-apply
        waits rather than being lost.

        Imported here rather than at the top: this module is deliberately thin
        on imports because it holds no policy, and pulling the apply stack into
        its import graph would say otherwise. `project._sealed` does the same.
        """
        from dgraph import applying, limits, pending, task_pending

        agent = (req.get("agent") or "").strip()
        if not agent:
            return _answer(req, "deny", "no agent named in the request")
        # The apply stack finds its project by walking up from the working
        # directory. `serve` stands this process in `self.root` so that answer
        # is this project, but a `Broker` constructed and driven directly never
        # ran that -- and a write to *another* project's store would be this
        # module's worst possible failure: silent, and in a file nobody
        # implicated. Refused rather than guessed at.
        try:
            found = project.find().root
        except Exception as exc:                       # noqa: BLE001
            return _answer(req, "deny", f"no project to apply into: {exc}")
        if os.path.realpath(found) != os.path.realpath(self.root):
            return _answer(req, "deny",
                           f"this broker serves {self.root}, but the working "
                           f"directory resolves to {found} — refusing to write "
                           f"a store it was not started for")
        try:
            with applying.trays():
                tray = pending.load(task_pending.path())
                mine = [op for op in tray
                        if limits.mechanical(op, agent) is None]
                if not mine:
                    refused = {limits.mechanical(op, agent) for op in tray
                               if (op.get("by") or "").strip() == agent}
                    return _answer(req, "deny",
                                   "nothing staged that a writer may land "
                                   "unattended" + (f": {'; '.join(sorted(r for r in refused if r))}"
                                                   if refused else ""))
                applying.apply_tasks(mine)
        except Exception as exc:                       # noqa: BLE001
            # Every failure is the same answer to the caller: nothing landed
            # and the ops are still staged, which is exactly where a refused
            # apply leaves them anyway. The reason travels so the agent can
            # say something truthful rather than retry blindly.
            return _answer(req, "deny", f"apply refused: {exc}")
        return _answer(req, "allow", f"applied {len(mine)} op(s) for {agent}")

    def _decide(self, req: dict) -> dict:
        agent = (req.get("agent") or "").strip()
        kind = req.get("kind")
        target = req.get("target") or ""
        if kind == APPLY_KIND:
            # Before the ladder, like `_unmountable` and for its reason: there
            # is no rung on which this is a person's answer. D17 and D43 put
            # the write here because nothing inside a floor can write a sealed
            # store, and D15 says which ops -- neither is a verdict to compose.
            return self._apply(req)
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
                           grant=None, remembered=True, by="grant")

        unmountable = self._unmountable(req)
        if unmountable:
            # Refused before the ladder, because there is no rung on which the
            # answer could be yes. The design says a grant outside the mounts
            # is unimplementable and "the ladder never offers one"; it offered
            # one, and the kernel then overruled the supervisor. `P-F11`.
            return _answer(req, "deny", unmountable)

        level = self.rungs.get(kind)
        if level not in RUNGS:
            return _answer(req, "deny", f"no rung in force for {kind!r}")

        if level == "off":
            return _answer(req, "deny",
                           f"${LADDERS[kind][0]}=off — this broker answers "
                           f"nothing; the gate's own verdict stands", by="rung")
        if level not in ("auto", "scoped", "user"):
            return _answer(req, "deny", f"unhandled rung {level!r}")
        if level == "auto":
            ask, by = (lambda: self._auto(req)), "auto"
        else:
            if self.prompt is None:
                return _answer(req, "deny", "no way to ask — broker has no "
                                            "front end attached",
                               by="unanswered")
            ask = lambda: self.prompt(req, level)
            # `D40`. The same person answered; what differs is whether this
            # process can show it was a person, and the log is where that
            # belongs rather than in the verdict.
            by = "relayed" if self.unprovable else "person"

        # Published around the wait, not inside it. `D24` gave a blocked agent
        # a heartbeat so `expire` would stop parking work that was only
        # waiting — which cost the other half of `Seen`: a blocked agent now
        # looks *alive*, which is the same ambiguity from the other side. This
        # is what tells them apart. `P-F7`.
        #
        # **Around `auto` too, and that is not the belt-and-braces it looks
        # like.** An auto policy used to be assumed instantaneous — a function
        # of the request, deciding here — and one that *waits* (a relay on the
        # `auto` rung, answered by a session elsewhere) blocks the agent
        # exactly as a person does. `unattachable` describes an unattached
        # `auto` as a run that reports itself healthy while nothing is in
        # force, and a blocked-but-invisible agent is the same reading arriving
        # by another road.
        # **A person is not asked about a caller that has gone.** Reading on
        # arrival made this reachable: a request now waits its turn, and by the
        # time the turn comes its caller may have given up — so without this
        # the queue spends the supervisor's attention on questions whose
        # answers cannot be delivered, which is precisely what `dg-agent
        # consent` refuses to let them do (`G-F2`). Checked here rather than at
        # the door because the caller can leave *during* the wait, which is the
        # case that matters.
        #
        # After the rungs, so `off` still answers `off` and a remembered grant
        # still answers instantly: those cost nobody anything and their log
        # lines are true. This is only about the prompt.
        over = gone_for(req)
        if over is not None:
            return _answer(req, "deny",
                           f"{agent} stopped waiting {int(over)}s ago — its "
                           f"deadline had passed before this request reached "
                           f"the front of the queue, and a verdict given now "
                           f"would reach nobody",
                           by="unanswered")
        # Republished, not merely left: this agent may have been queued a moment
        # ago and the map still says so. The same call clears `queued`, so the
        # transition from *waiting for a turn* to *waiting on you* is one
        # write. `G-F4`.
        self._waiting(agent, req, queued=False)
        try:
            allow, why, grant = ask()
        except NoAnswer as exc:
            # A refusal for the agent, and not a decision in the log. Every
            # seam raises it: a terminal at EOF, and either relay nobody
            # answered — the same event through different doors.
            return _answer(req, "deny", str(exc) or "nobody answered",
                           by="unanswered")
        return self._settle(req, agent, allow, why, grant, by=by)

    def _waiting(self, agent: str, req: dict | None, *,
                 queued: bool = False) -> None:
        """Publish, or clear, what `agent` is blocked on.

        Best-effort and rewritten whole, like the log and for the same reason:
        a broker that stopped answering because it could not write a status
        file would be worse than a status file that is briefly wrong.

        `queued` distinguishes *waiting on a person* from *waiting on another
        agent's turn*. Both are blocked and both must show, but they are not
        the same news: the first is answerable now and the second is not, and a
        supervisor who cannot tell them apart cannot tell one question from
        five. Rewritten whole under `_status`, because two threads publish here
        now — the accept path and the decider. `G-F4`.
        """
        path = self.root / WAITING_NAME
        with contextlib.suppress(Exception):
            with self._status:
                now = waiting(self.root)
                if req is None:
                    now.pop(agent, None)
                else:
                    now[agent] = {"kind": req.get("kind"),
                                  "target": req.get("target"),
                                  "since": time.time(), "queued": queued}
                project.write_atomic(path, json.dumps(now, ensure_ascii=False))

    def _unmountable(self, req: dict) -> str | None:
        """Why no grant could make this write work — or `None`.

        Only under a floor, and only for a write. The mounts are fixed at spawn
        and a broker cannot widen them, so consenting to a path outside them
        produces an `allow` the kernel refuses seconds later, with the agent
        holding a permission that does nothing.

        `roots` comes from the request — the gate knows what it judged against
        — and an absent one means the gate could not say, in which case this
        says nothing either rather than guessing at a policy it cannot see.
        """
        if req.get("kind") != "write":
            return None
        from dgraph import confine
        try:
            if confine.mode() == "off":
                return None
        except ValueError:
            return None
        roots = req.get("roots") or []
        if not roots:
            return None
        target = limits._real(req.get("target") or "")
        if any(limits._within(target, r) for r in roots):
            return None
        return (f"{target} is outside the confinement floor's mounts "
                f"({', '.join(roots)}), which are fixed at spawn — so no grant "
                f"made here could let the write through, and saying yes would "
                f"hand out a permission the kernel then refuses. Relaunch the "
                f"run with this path in scope if the work needs it.")

    def _auto(self, req) -> tuple[bool, str, Grant | None]:
        if self.auto is None:
            return False, "no auto policy is attached to this broker", None
        return self.auto(req)

    def _settle(self, req, agent, allow, why, grant, *, by: str) -> dict:
        if allow and grant is not None:
            self.grants.setdefault(agent, []).append(grant)
        return _answer(req, "allow" if allow else "deny", why,
                       grant=grant, by=by)

    # ---- the socket ------------------------------------------------------

    def serve(self, stop: threading.Event | None = None) -> None:
        """Listen until `stop` is set. One connection, one request, one answer.

        Connection per request rather than a session, because a request is
        already the unit a gate blocks on: a gate that died mid-wait leaves a
        closed socket and nothing to reap.
        """
        # **The project this serves is where it stands.** Since `_apply` the
        # broker writes a store, and the apply stack finds a project by walking
        # up from the working directory -- `applying.apply_tasks`,
        # `task_pending.path` and `cross.guard_tasks` all do, in three modules.
        # Threading a project through every one of them would widen a shared
        # API used by two hosts for the sake of this caller; standing in the
        # right place answers all three at once, and is true anyway: a broker
        # is started for one project and serves it until it stops. `_apply`
        # checks rather than trusts, for the embedded caller that never gets
        # here.
        with contextlib.suppress(OSError):
            os.chdir(self.root)
        why = unbindable(self.root)
        if why is not None:
            raise OSError(why)
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
            # **Two threads, and the split is exactly one question wide.**
            # Reading a request and deciding it used to be the same loop, so a
            # second agent's request sat unread in the listen backlog while the
            # first was being answered: it never reached `decide`, published no
            # waiting state, and `dg-agent list` showed it `Waiting —` beside a
            # fresh `Seen` — which reads as *alive*. That is the reading the
            # column exists to end, arriving from the other side. `G-F4`.
            #
            # Publishing *waiting* needs the request, and the request needs the
            # socket read. So the read moves and nothing else does: one reader
            # accepts, reads and publishes; this loop goes on deciding, one at
            # a time, **and it is still the only caller of `decide`.** Serial
            # stays a property of the shape rather than of a lock somebody must
            # remember to hold — which matters because `Relay`'s single slot
            # rests on it, and a maintained invariant is the thing this
            # codebase has a whole audit shape about.
            queue: "_Queue[tuple[socket.socket, dict]]" = _Queue()
            reader = threading.Thread(target=self._read_loop,
                                      args=(srv, queue, stop), daemon=True)
            reader.start()
            while stop is None or not stop.is_set():
                try:
                    conn, req = queue.get(timeout=0.2)
                except _Empty:
                    continue
                with conn:
                    with contextlib.suppress(Exception):
                        self._answer_one(conn, req)
            reader.join(timeout=2)
        finally:
            srv.close()
            for gone in (path, staging, self.root / WAITING_NAME):
                with contextlib.suppress(FileNotFoundError):
                    gone.unlink()

    def _read_loop(self, srv: socket.socket, queue, stop) -> None:
        """Accept, read, publish, hand over. Never decides.

        The whole of the second thread, and its whole job is to make an agent
        *visible* before its turn comes. It writes the waiting map and touches
        nothing else the decider owns — no grants, no log, no verdict.

        An unreadable request is answered here rather than queued: it has no
        agent to publish and no decision to make, so passing it on would put a
        non-question in front of a person.
        """
        while stop is None or not stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return                      # the socket closed under us
            try:
                raw = _read_line(conn)
                req = json.loads(raw) if raw else {}
            except Exception as exc:
                with conn:
                    with contextlib.suppress(Exception):
                        _write_line(conn, {"v": 1, "verdict": "deny",
                                           "reason": f"unreadable request ({exc!r})"})
                continue
            # Queued, and said so. Both are blocked on this broker; only one is
            # answerable now, and a supervisor who cannot tell them apart
            # cannot tell one question from five. The decider republishes with
            # `queued=False` when the turn comes.
            agent = (req.get("agent") or "").strip()
            if agent:
                with contextlib.suppress(Exception):
                    self._waiting(agent, req, queued=True)
            queue.put((conn, req))

    def _answer_one(self, conn: socket.socket, req: dict) -> None:
        try:
            res = self.decide(req)
        except Exception as exc:  # never raise at a blocked agent
            res = _answer(req, "deny", f"the broker could not decide ({exc!r})",
                          by="broker")
        # Written first, then recorded, because whether it *arrived* is part of
        # what happened. A person can spend two minutes on a prompt, and the
        # gate that asked may be gone by the time they answer.
        self.record(req, res, delivered=_write_line(conn, res))

    def record(self, req: dict, res: dict, delivered: bool = True) -> None:
        """One line of evidence. Best-effort: a broker that stopped answering
        because its log filled a disk would be worse than an unlogged grant.

        **`delivered` is the field `P-F1` added, and it is not bookkeeping.**
        The log recorded a `deny` for a request whose caller had already given
        up and allowed the write, so the one artefact a supervisor reads
        afterwards asserted the opposite of what happened. An answer nobody
        collected is a different event from one that was enforced, and only the
        socket knows which this was.
        """
        path = self.log_path if self.log_path is not None else self.root / LOG_NAME
        entry = {"agent": req.get("agent"), "kind": req.get("kind"),
                 "target": req.get("target"), "task": (req.get("holding") or {}).get("task"),
                 "verdict": res.get("verdict"), "reason": res.get("reason"),
                 # What decided, beside what was decided. Without it an `auto`
                 # verdict and a person's read identically afterwards, and the
                 # rung split records none of the accountability it claims.
                 "by": res.get("by", "broker"),
                 "grant": res.get("grant"), "remembered": res.get("remembered", False),
                 "delivered": delivered}
        with contextlib.suppress(Exception):
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


#: What produced a verdict, for the log. The vocabulary is closed and small on
#: purpose: it is read by a person asking *who actually decided this*, and a
#: field with room for prose would answer that with prose.
#:
#: `person` is the only one that means somebody was asked, **and that it can be
#: shown**. A relay front end records `person` where a confinement floor is in
#: force, because a relay is transport and the human at the other end of it is
#: the decider — which is the claim `D37`'s falsifier watches.
#:
#: `relayed` is that same human, answering through a channel nothing protects.
#: `D40`: the channel is a filesystem object and an unconfined agent shares the
#: uid that owns it, so it can write its own `allow` and the blocked agent
#: collects it. Under a floor the mounts exclude the channel and the forgery
#: needs the permission it would grant, which is `D39`'s rule; without one that
#: rule does not hold, and `person` becomes a claim this process cannot support.
#:
#: So it is not a weaker *answer* — the same person answered either way. It is a
#: weaker **warrant**, and the log is exactly where the difference belongs: a
#: supervisor reading it afterwards is asking who decided, and `relayed` says
#: *somebody did, and nothing here can prove which somebody*.
ANSWERED_BY = ("person", "relayed", "auto", "grant", "rung", "broker",
               "unanswered")


def unprovable(relaying: bool, root: Path | None = None) -> str | None:
    """Why a relayed verdict here cannot be logged `person` — or `None`.

    A **note**, not a refusal, and `D40` settled that deliberately: with no
    floor an agent can already write the project at leisure, so refusing to
    relay would close the small hole beside the open one. What it may not do is
    let the log claim more than it can show.

    Fails toward `relayed`. Where the floor cannot be established this answers
    as though there were none — because *cannot establish* and *is not there*
    are the same thing for a warrant, and the direction that guesses `person`
    is the one that writes something false.
    """
    if not relaying:
        return None                      # a terminal answer never crosses it
    from dgraph import confine as _confine
    if _confine.mode() == "require":
        return None
    return ("no confinement floor is in force, so a relayed verdict is logged "
            "`relayed` rather than `person`: the channel is a filesystem "
            "object an unconfined agent shares a uid with, and nothing here "
            "can show which hand wrote the answer. The verdict still stands "
            "and relaying still works — see `D40`")


def _answer(req: dict, verdict: str, reason: str, *, grant: Grant | None = None,
            remembered: bool = False, by: str = "broker") -> dict:
    """One verdict, and what produced it.

    `by` defaults to `broker` — the structural refusals, which no rung and no
    person had any part in: an unknown kind, an unsigned request, a target
    outside the floor's mounts. A default of `person` would have been the
    dangerous way round.
    """
    out = {"v": 1, "id": req.get("id"), "verdict": verdict, "reason": reason,
           "by": by if by in ANSWERED_BY else "broker"}
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


def _write_line(conn: socket.socket, obj: dict) -> bool:
    """Send one answer. `False` where the peer was already gone.

    The failure is suppressed — a broker must not die because an agent
    disconnected — but it is *reported*, which is the difference between a log
    that records decisions and one that records enforcement.
    """
    try:
        conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode())
        return True
    except OSError:
        return False


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
    # **The scope is named before it is granted, not after.** `[s]cope` grants
    # the target's whole *directory* — that is `D07`, a root rather than a
    # glob — and the prompt showed only the file, so a person consenting to one
    # path was granting every sibling of it without being told. `P-F11`.
    scope = _scope_for(req)
    choices = (f"[a]llow once  [s]cope ({scope.value})  [d]eny"
               if level == "scoped" else "[a]llow once  [d]eny")
    while True:
        try:
            answer = input(f"  {choices}: ").strip().lower()[:1]
        except (EOFError, KeyboardInterrupt):
            print()
            raise NoAnswer("no answer from the supervisor — the terminal "
                           "closed or the prompt was interrupted") from None
        if answer == "a":
            return True, "allowed once by the supervisor", None
        if answer == "d":
            return False, "declined by the supervisor", None
        if answer == "s" and level == "scoped":
            return (True, f"scope granted by the supervisor: {scope.value}",
                    scope)


class Relay:
    """The relay channel: one question out, one verdict back, on a connection.

    The file version this replaces published the pending question and collected
    the answer as two files. Both were **passively forgeable** — write the
    answer and walk away, and the blocked agent collects it — which is how an
    agent came to grant itself `curl … | sh` with the log recording `by:
    person`. Moving them outside every writable root fixed the easy case; a
    connection removes the artifact altogether. There is nothing lying about
    between the question and its answer, so forging one means winning a race
    against the legitimate answerer rather than leaving a file where it will be
    picked up.

    **What this is not is a boundary.** The socket is a filesystem object and
    the agent runs as the same user, so it can `connect()` like anything else.
    What actually stops it is the confinement floor, which keeps every write —
    and every socket — outside the project out of reach. That is a deliberate,
    recorded risk: see `D40`, which is open, and which is why this docstring
    says so rather than implying more.

    Serial by construction, like the broker it serves: `Broker.serve` is the
    only caller of `decide` and answers one request at a time, so there is
    exactly one question outstanding and the slot below needs no queue. A
    reader thread was added beside that loop so a queued agent is visible
    before its turn (`G-F4`) — it accepts, reads and publishes, and never
    decides, which is why this sentence still holds.
    """

    def __init__(self, root: Path | None = None, wait: float = RELAY_WAIT):
        self.root = root if root is not None else project.find().root
        self.wait = wait
        self._lock = threading.Lock()
        self._pending: dict | None = None
        self._answered = threading.Event()
        self._verdict: tuple[bool, str, Grant | None] | None = None

    # ---- the two seams ---------------------------------------------------

    def prompt(self, req: dict, level: str) -> tuple[bool, str, Grant | None]:
        """The `user`/`scoped` front end. Recorded `person`."""
        return self._await(req, level)

    def auto(self, req: dict) -> tuple[bool, str, Grant | None]:
        """The `auto` policy. Recorded `auto`.

        The same body as `prompt`, and that is the point: they differ in
        nothing but the rung they are attached to, so what a verdict is
        *called* is the only thing that separates them. Two implementations
        would be two chances for that to drift.
        """
        return self._await(req, "auto")

    def wait_for(self, req: dict) -> float:
        """How long to hold this request open: the **caller's** bound, where it
        named one.

        `RELAY_WAIT` is the fallback for a request that carries none, not the
        number. It was the number, and its comment justified it against a
        constant in another file — *"just over the 100s both host adapters
        pass"* — which is a relation maintained by hand between two files that
        cannot see each other. `--relay-wait` could break it silently, and did:
        below the caller's bound the relay gave up first and threw away an
        answer the caller was still waiting for. Audit `G-F3`.

        A small margin over the caller, so the person's answer is never
        discarded by this end before the caller has stopped listening — which
        is what the old constant was reaching for, now against the number it
        was reaching for rather than a copy of it.
        """
        named = req.get("deadline")
        if not isinstance(named, (int, float)) or named <= 0:
            return self.wait
        left = float(named) - max(0.0, time.time() - float(req.get("asked") or time.time()))
        return max(1.0, left + RELAY_MARGIN)

    def _await(self, req: dict, level: str) -> tuple[bool, str, Grant | None]:
        published = dict(req)
        published["level"] = level
        published["since"] = time.time()
        published["scope"] = _scope_for(req).value if level == "scoped" else None
        with self._lock:
            self._pending = published
            self._verdict = None
            self._answered.clear()
        held = self.wait_for(req)
        try:
            if not self._answered.wait(held):
                # The message no longer asserts anything about the caller. It
                # used to say the caller's deadline had passed, which the
                # broker had no way to know and which was false whenever
                # `--relay-wait` sat under it — a false reason written into the
                # one artefact a supervisor reads afterwards. `G-F3`.
                raise NoAnswer(
                    f"nobody answered the relay within {held:.0f}s"
                    + (f", which is the {req['deadline']:.0f}s "
                       f"{req.get('agent') or 'the caller'} said it would wait"
                       if isinstance(req.get("deadline"), (int, float))
                       and req["deadline"] > 0 else
                       " (no caller deadline was named)"))
            with self._lock:
                got = self._verdict
            if got is None:                       # answered, then withdrawn
                raise NoAnswer("the relay was answered and the verdict was lost")
            return got
        finally:
            with self._lock:
                self._pending = None

    # ---- the socket ------------------------------------------------------

    def path(self) -> Path:
        return channel_dir(self.root) / RELAY_SOCK_NAME

    def peek(self) -> dict | None:
        """The question outstanding right now, or `None`."""
        with self._lock:
            return dict(self._pending) if self._pending else None

    def answer(self, ident: str, allow: bool, why: str,
               grant: Grant | None = None) -> str | None:
        """Deliver a verdict. The reason it was refused, or `None` if taken.

        Matched on the id, so a verdict for a question that has already timed
        out is refused rather than applied to whatever is being asked now —
        ids are stable over `(agent, kind, target)`, which is exactly what
        makes a late answer dangerous.
        """
        with self._lock:
            if self._pending is None:
                return "nothing is waiting on a relayed answer"
            if self._pending.get("id") != ident:
                return (f"that verdict is for {ident}, and the question "
                        f"outstanding is {self._pending.get('id')}")
            self._verdict = (bool(allow), why, grant)
            self._answered.set()
        return None

    def serve(self, stop: threading.Event | None = None) -> None:
        """Listen for `peek` and `answer`, until `stop`.

        Its own socket rather than the broker's, and its own directory: the
        broker's lives in the project because that is where agents must reach
        it, and this one must be somewhere they cannot.
        """
        path = self.path()
        why = _too_long(path)
        if why is not None:
            raise OSError(why)
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(str(path))
            path.chmod(0o600)
            srv.listen(8)
            srv.settimeout(0.2)
            while stop is None or not stop.is_set():
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                with conn:
                    with contextlib.suppress(Exception):
                        self._handle(conn)
        finally:
            srv.close()
            with contextlib.suppress(FileNotFoundError):
                path.unlink()

    def _handle(self, conn: socket.socket) -> None:
        raw = _read_line(conn)
        try:
            msg = json.loads(raw) if raw else {}
        except ValueError:
            _write_line(conn, {"error": "not JSON"})
            return
        op = msg.get("op")
        if op == "peek":
            _write_line(conn, {"pending": self.peek()})
            return
        if op == "answer":
            grant = None
            if msg.get("grant"):
                grant = Grant(kind=msg["grant"]["kind"],
                              value=msg["grant"]["value"])
            why = self.answer(msg.get("id"), bool(msg.get("allow")),
                              str(msg.get("why") or ""), grant)
            _write_line(conn, {"ok": why is None, "error": why})
            return
        _write_line(conn, {"error": f"unknown op {op!r}"})


def _relay_call(root: Path | None, msg: dict) -> dict | None:
    """One request to a relay, or `None` where none is listening."""
    root = root if root is not None else project.find().root
    path = channel_dir(root) / RELAY_SOCK_NAME
    if not path.exists():
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(str(path))
            s.sendall((json.dumps(msg, ensure_ascii=False) + "\n").encode())
            raw = _read_line(s)
        return json.loads(raw) if raw else None
    except (OSError, ValueError):
        return None


def relaying(root: Path | None = None) -> bool:
    """Whether a relay is listening — as opposed to nothing being asked."""
    return _relay_call(root, {"op": "peek"}) is not None


def pending_ask(root: Path | None = None) -> dict | None:
    """The request waiting on a relayed answer, or `None`.

    What `dg-agent consent` reads. `None` means nothing is blocked *on a
    relay* — never that nothing is blocked, which is `waiting()`'s question.
    """
    got = _relay_call(root, {"op": "peek"})
    return (got or {}).get("pending")


def send_answer(root: Path | None, ident: str, allow: bool, why: str,
                grant: Grant | None = None) -> str | None:
    """Deliver a verdict to the relay. The refusal, or `None` if it was taken."""
    msg = {"op": "answer", "id": ident, "allow": bool(allow), "why": why}
    if grant is not None:
        msg["grant"] = {"kind": grant.kind, "value": grant.value}
    got = _relay_call(root, msg)
    if got is None:
        return "no relay is listening — is the broker running with `--relay`?"
    return None if got.get("ok") else (got.get("error") or "refused")


def gone_for(req: dict) -> float | None:
    """How long ago this request's caller stopped listening — or `None`.

    `None` means *still waiting*, or that no bound was named and it may be
    waiting indefinitely. A number means the gate has already answered itself
    and moved on, so a verdict given now reaches nobody.

    The question `dg-agent consent` could not ask before the deadline was in
    the request. Without it the command showed a dead request as live, took an
    answer for it, and printed `allowed` in green while the log recorded
    `delivered: false` — the one surface a person acts on saying the opposite
    of the one artefact they read afterwards. Audit `G-F2`.
    """
    named, asked = req.get("deadline"), req.get("asked")
    if not isinstance(named, (int, float)) or named <= 0:
        return None
    if not isinstance(asked, (int, float)):
        return None
    over = time.time() - float(asked) - float(named)
    return over if over > 0 else None


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


def waiting(root: Path | None = None) -> dict[str, dict]:
    """Who is blocked on a consent decision, by agent name.

    Empty where no broker is running, where none is blocked, and where the file
    cannot be read — three states a reader must not tell apart, because only
    one of them is about an agent and the other two are about the supervisor's
    terminal.
    """
    try:
        path = (root or project.find().root) / WAITING_NAME
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def listening(root: Path | None = None) -> bool:
    """Whether a broker is there to ask. Absence is not a degraded broker --
    it is *no broker*, and the gate then returns the verdict it always did."""
    try:
        return socket_path(root).is_socket()
    except OSError:
        return False


#: Where a detached broker's own output goes. Beside the log rather than in the
#: channel directory: this is a record of *this project's* run, and the channel
#: is deliberately somewhere an agent cannot reach.
DETACH_LOG = ".dgraph-consent.out"


def detach(root: Path | None = None, *, argv: list[str]) -> dict:
    """Start a broker in its own session and return once it is listening.

    **The step a session cannot otherwise take.** `agentic/QUICKSTART.md`
    Recipe 2 has the session hold the broker and a *terminal* run the launcher,
    because a broker started from a session's own shell call dies when that
    call returns — and the one thing the broker must do is outlive the turn
    that started it. `D53`.

    Modelled on `server.detach`, including the two properties that make it safe
    to call from a slash command: **stdout and stderr go to a file, never
    inherited**, since a child holding the caller's pipe open would hang the
    block this exists to serve; and **it is idempotent**, reporting a broker
    that is already there rather than fighting for the socket, because a
    command run twice must not punish the second run.

    **`$DG_AGENT` is scrubbed**, and that is not shared boilerplate. The
    detached child outlives the caller, so an agent that started a broker would
    leave a process holding its name for the rest of the run — and this
    particular process is the one that writes `by:` into the consent log. An
    inherited identity there is the failure `answered_by` exists to prevent,
    arriving through the front door.
    """
    import subprocess
    import sys
    import time

    root = root if root is not None else project.find().root
    if listening(root):
        return {"state": "running", "already": True,
                "socket": str(socket_path(root))}

    env = {k: v for k, v in os.environ.items() if k != _env.AGENT_ENV}
    log = Path(root) / DETACH_LOG
    with open(log, "ab", buffering=0) as fh:
        proc = subprocess.Popen(
            [sys.executable, "-m", "dgraph.agent_cli",
             "--project", str(root), "broker", *argv],
            stdin=subprocess.DEVNULL, stdout=fh, stderr=fh,
            start_new_session=True, cwd=str(root), env=env,
        )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if listening(root):
            return {"state": "running", "already": False, "pid": proc.pid,
                    "socket": str(socket_path(root))}
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    proc.kill()
    tail = ""
    try:
        tail = "\n".join(
            log.read_text(encoding="utf-8", errors="replace").strip()
            .splitlines()[-4:])
    except OSError:
        pass
    raise RuntimeError(
        "the broker did not come up"
        + (f"\n{tail}" if tail else "")
        + f"\n(full output in {DETACH_LOG})")


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
    why = unbindable(root)
    if why is not None:
        # Named apart from an unreachable decider, because it is not one: no
        # broker could ever have been listening here, and telling the agent to
        # start one would be advice that cannot be taken.
        return {"v": 1, "verdict": "deny", "reason": why}
    beat = _heartbeat(req.get("agent"), root)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(path))
            s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode())
            raw = _read_line(s)
        return json.loads(raw) if raw else _unreachable("the broker closed "
                                                        "without answering")
    except socket.timeout:
        # Named apart from every other failure, because it is the one the
        # caller chose: it asked for an answer within `timeout` and did not get
        # one. Saying "the broker did not answer" would blame a supervisor who
        # may be mid-sentence.
        return _unanswered(timeout)
    except (OSError, ValueError) as exc:
        return _unreachable(f"the broker did not answer ({exc!r})")
    finally:
        beat.set()


def _unreachable(why: str) -> dict:
    return {"v": 1, "verdict": "deny",
            "reason": f"{why} — an unreachable decider is not consent. "
                      f"`dg-agent broker` is what answers this."}


def _unanswered(seconds: float) -> dict:
    """The deadline passed with a decision still outstanding.

    A refusal rather than a silence, which is the whole of `P-F1`: an
    undecided request is not consent, and the caller that would otherwise have
    timed out was about to allow the write without saying so.
    """
    shown = f"{seconds:g} second{'' if seconds == 1 else 's'}"
    return {"v": 1, "verdict": "deny",
            "reason": f"nobody answered within {shown} — an undecided request "
                      f"is not consent. The supervisor may still be reading "
                      f"it; ask again, or raise the deadline the host gives "
                      f"this hook."}


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
            holding: dict | None = None, roots: list[str] | None = None,
            deadline: float | None = None) -> dict:
    """The object the gate sends, and why each field is in it.

    `id` is stable over `(agent, kind, target)` so a retry re-attaches to a
    pending decision rather than queueing a second prompt at the same person.
    `gate` carries the gate's own conclusion because **the broker decides and
    never re-derives policy** -- `limits` stays the single home, which is the
    "adapters hold no policy of their own" rule one level up.

    **`deadline` is how long the caller will wait, and it belongs here.** `D26`
    put the bound on the caller and the caller does pass it — to `consult`,
    which sets it on the socket and stops. Everything past that socket was then
    deciding against a number it could not see: `RELAY_WAIT` was a constant
    guessing at it, `dg-agent consent` could not tell a live request from one
    whose agent had gone, and the relay's own refusal asserted *"the caller's
    own deadline had already passed"* about a caller with ninety-five seconds
    left. One absent field, three surfaces guessing. `roots` was the same shape
    one pass earlier and was fixed the same way, for the reason that
    generalises it: the gate knows, because it is what the gate just judged
    against. Audit `G-F3`.

    `None` where nobody named one — a person at a terminal, who may wait as
    long as they like. Readers must treat that as *no bound*, never as zero.
    """
    import hashlib
    ident = hashlib.sha256(f"{agent}|{kind}|{target}".encode()).hexdigest()[:12]
    return {"v": 1, "id": ident, "agent": agent, "kind": kind,
            "target": target, "gate": {"verdict": "ask", "reason": reason},
            "holding": holding or {}, "roots": roots or [],
            "deadline": deadline, "asked": time.time()}
