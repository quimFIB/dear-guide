"""The two limits on an agent: where it may write, and how long it may run.

What is pinned here is mostly the *narrowness*. A scope that refused too much
would be switched off within a day, and a budget that parked work early would
be worse than none — so the tests that matter are the ones asserting the limit
does NOT fire: on a supervisor, on a read, on the project it was launched in,
and on a lease that has time left.
"""

import json
from datetime import datetime, timedelta

import pytest

from dgraph import agents, gate, limits


# ---- the write scope -----------------------------------------------------


def test_a_supervisor_is_never_scoped(tmp_path):
    """`None` owner is a person at whichever door, exactly as in
    `cross.refuse_close`. Every value of `$DG_WRITE` leaves them alone."""
    assert limits.refuse_write("/etc/passwd", None, tmp_path,
                               chosen="launch") is None


def test_open_is_the_default_and_scopes_nothing(tmp_path, monkeypatch):
    """Unset must be today's behaviour. A project that has never heard of this
    pays nothing, which is the rule every adapter here already follows."""
    monkeypatch.delenv(limits.WRITE_ENV, raising=False)
    assert limits.write_policy() == "open"
    assert limits.refuse_write("/etc/passwd", "brisk-beacon", tmp_path) is None


def test_launch_allows_the_project_and_temp(tmp_path):
    for target in (tmp_path / "findings" / "new.md", "/tmp/scratch/x"):
        assert limits.refuse_write(target, "brisk-beacon", tmp_path,
                                   chosen="launch") is None


def test_launch_asks_about_anywhere_else(tmp_path):
    why = limits.refuse_write("/etc/passwd", "brisk-beacon", tmp_path,
                              chosen="launch")
    assert why and "brisk-beacon" in why and "/etc/passwd" in why
    # The reason has to name the way out, or it is a refusal nobody can act on.
    assert "person's call" in why


def test_a_symlink_out_of_the_project_is_outside_it(tmp_path):
    """The scope is about where the bytes land, not what the path looks like.

    A project that holds a symlink pointing outside itself would otherwise be a
    hole the length of whatever it links to, and a checkout linking its notes
    or its data in from elsewhere is an ordinary arrangement rather than an
    exotic one.
    """
    # Deliberately NOT a link into another temporary directory: `/tmp` is a
    # writable root in its own right, so such a link is in scope and the test
    # would pass for the wrong reason. `/etc` is outside both roots.
    (tmp_path / "link").symlink_to("/etc")
    assert limits.refuse_write(tmp_path / "link" / "passwd", "brisk-beacon",
                               tmp_path, chosen="launch") is not None


def test_the_scope_is_the_project_not_the_working_directory(tmp_path):
    """A `cd` is not a change of remit.

    Anchoring to the caller's cwd would widen the scope every time an agent
    walked somewhere else — the one direction a limit must not move on its own.
    """
    roots = limits.writable_roots(tmp_path)
    assert str(tmp_path.resolve()) in roots
    assert not any(str(tmp_path.parent.resolve()) == r for r in roots)


def test_an_unknown_policy_is_open_rather_than_an_error(monkeypatch):
    """A typo in a launcher's environment must not make the tool unusable for
    the supervisor too. `cross.policy` decided this first."""
    monkeypatch.setenv(limits.WRITE_ENV, "lauch")
    assert limits.write_policy() == "open"


def test_the_gate_answers_a_write_and_never_denies(tmp_path, monkeypatch):
    """End to end through the entry point both adapters call.

    Only `allow` or `ask`: the rule is consent, not prohibition, and a refusal
    the person cannot lift from where they are standing is indistinguishable
    from a broken tool.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DG_AGENT", "brisk-beacon")
    monkeypatch.setenv(limits.WRITE_ENV, "launch")
    assert gate.write_verdict(str(tmp_path / "in.md"))["verdict"] == "allow"
    assert gate.write_verdict("/etc/passwd")["verdict"] == "ask"


def test_the_write_gate_fails_open(monkeypatch):
    """The asymmetry with `gate.verdict`, which fails closed.

    An unjudged commit may record a contradiction permanently; an unjudged
    write is an ordinary file operation nobody asked to be consulted about.
    Failing closed here would turn every unreadable project into a wall of
    prompts about writes nobody was worried about.
    """
    def boom(*a, **k):
        raise RuntimeError("no project")
    monkeypatch.setattr(limits, "refuse_write", boom)
    assert gate.write_verdict("/etc/passwd")["verdict"] == "allow"


# ---- the budget ----------------------------------------------------------


@pytest.mark.parametrize("text,seconds", [
    ("1800", 1800), ("30m", 1800), ("2h", 7200), ("1d", 86400), ("45s", 45),
    ("infinite", None), ("", None), (None, None), ("none", None),
])
def test_spans_that_parse(text, seconds):
    assert limits.span(text) == seconds


@pytest.mark.parametrize("text", ["5x", "half an hour", "1h30m", "-5", "m"])
def test_spans_that_do_not(text):
    with pytest.raises(limits.BadSpan):
        limits.span(text)


def test_zero_is_refused_rather_than_read_as_either_thing():
    """`0` is a plausible typo for "no budget" and the two readings are
    opposites, so it is refused instead of guessed at."""
    with pytest.raises(limits.BadSpan) as exc:
        limits.span("0")
    assert "opposites" in str(exc.value)


def test_show_span_is_never_lossy():
    for seconds in (45, 60, 90, 1800, 3600, 5400, 86400):
        assert limits.span(limits.show_span(seconds)) == seconds


def test_a_claim_records_the_budget_it_was_given(store):
    name = agents.claim(store, budget=1800)
    rec = agents.load(store)[name]
    assert rec["budget"] == 1800 and rec["started"]
    assert agents.remaining(rec) > 1700


def test_no_budget_leaves_the_lease_as_it_was(store):
    """The default has to stay the shape every existing lease already has."""
    name = agents.claim(store)
    assert "budget" not in agents.load(store)[name]
    assert agents.remaining(agents.load(store)[name]) is None


def test_a_budget_with_no_start_is_no_limit_rather_than_spent(store):
    """A hand-edited or older lease. Reading it as expired would park an
    agent's work on the strength of a field that was never set."""
    name = agents.claim(store, budget=1800)
    leases = agents.load(store)
    del leases[name]["started"]
    agents.save(leases, store)
    assert agents.remaining(agents.load(store)[name]) is None
    assert agents.over_budget(store) == []


def test_over_budget_finds_the_spent_and_leaves_the_rest(store):
    spent = agents.claim(store, budget=60)
    fine = agents.claim(store, budget=3600)
    infinite = agents.claim(store)
    leases = agents.load(store)
    long_ago = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    for n in (spent, fine, infinite):
        leases[n]["started"] = long_ago
    agents.save(leases, store)

    names = [r["agent"] for r in agents.over_budget(store)]
    assert spent in names
    # `fine` has an hour of budget and two hours elapsed -- it IS spent. The
    # point of the case is `infinite`, which never is however long it runs.
    assert infinite not in names


def test_an_infinite_budget_never_expires(store):
    name = agents.claim(store, budget=None)
    leases = agents.load(store)
    leases[name]["started"] = "2000-01-01T00:00:00"
    agents.save(leases, store)
    assert agents.over_budget(store) == []


def test_over_budget_reports_an_agent_holding_nothing(store):
    """"Died before it started" is invisible in every other reading of a run:
    no task is DOING, so a roster of parked work would never show it."""
    name = agents.claim(store, budget=60)
    leases = agents.load(store)
    leases[name]["started"] = "2000-01-01T00:00:00"
    agents.save(leases, store)
    [rec] = agents.over_budget(store)
    assert rec["agent"] == name and rec["holding"] == []


# ---- liveness: reported, never acted on ----------------------------------


def test_a_heartbeat_is_stamped_and_debounced(store, monkeypatch):
    """Every `dg` call goes through `touch`, so it must not write every time."""
    name = agents.claim(store)
    agents.touch(name, store, now="2026-08-28T12:00:00")
    first = agents.load(store)[name]["last_seen"]
    # Inside the window: no new stamp, and no write.
    agents.touch(name, store, now="2026-08-28T12:00:10")
    assert agents.load(store)[name]["last_seen"] == first
    # Past it: stamped.
    agents.touch(name, store, now="2026-08-28T12:05:00")
    assert agents.load(store)[name]["last_seen"] == "2026-08-28T12:05:00"


def test_a_heartbeat_never_raises(store):
    """It runs before every command. One that could fail a command would be a
    liveness signal that costs liveness."""
    agents.touch(None, store)                      # a supervisor
    agents.touch("never-claimed", store)           # no lease to stamp
    agents.touch("x", store / "nowhere")           # no project
    assert agents.load(store) == {}


def test_silence_is_only_reported_while_holding_work(store):
    """An agent silent and holding nothing has cost nobody anything, and a
    column of that is noise in front of every supervisor."""
    idle = agents.claim(store)
    leases = agents.load(store)
    leases[idle]["last_seen"] = "2000-01-01T00:00:00"
    agents.save(leases, store)
    assert agents.silent(store) == []

    agents.hold(idle, "T01", store)
    [rec] = agents.silent(store)
    assert rec["agent"] == idle and rec["holding"] == ["T01"]


def test_an_unstamped_lease_is_not_silent(store):
    """Never seen is not the same as not seen lately: a lease from before this
    existed, or an agent that has not run a command yet."""
    name = agents.claim(store)
    agents.hold(name, "T01", store)
    assert agents.quiet_for(agents.load(store)[name]) is None
    assert agents.silent(store) == []


def test_silence_does_not_make_an_agent_over_budget(store):
    """The line the whole design rests on. A forty-minute build is silent in
    exactly the way a corpse is, so silence must never reach the verb that
    parks work — only an elapsed budget does, and that is a fact about a clock.
    """
    name = agents.claim(store, budget=7200, now="2026-08-28T12:00:00")
    leases = agents.load(store)
    leases[name]["last_seen"] = "2026-08-28T12:01:00"
    leases[name]["holding"] = ["T01"]
    agents.save(leases, store)

    at = "2026-08-28T12:41:00"                       # 40m silent, 1h20m of budget left
    assert agents.silent(store, now=at)              # reported...
    assert agents.over_budget(store, now=at) == []   # ...and nothing acts on it


def test_the_silence_window_is_configurable(monkeypatch):
    assert limits.silent_after() == limits.SILENT_DEFAULT
    monkeypatch.setenv(limits.SILENT_ENV, "40m")
    assert limits.silent_after() == 2400
    monkeypatch.setenv(limits.SILENT_ENV, "nonsense")
    assert limits.silent_after() == limits.SILENT_DEFAULT


@pytest.mark.parametrize("seconds,shown", [
    (9, "9s"), (59, "59s"), (60, "1m"), (2401, "40m"), (3600, "1h"),
    (5400, "1h30m"), (86400, "1d"), (210067925, "2431d"),
])
def test_approx_span_reads_like_a_person_wrote_it(seconds, shown):
    """`show_span` cannot round — it round-trips through `span` — so it renders
    2401 seconds as `2401s`, which is correct and unreadable."""
    assert limits.approx_span(seconds) == shown


# ---- the stranding guard -------------------------------------------------


def test_prune_keeps_a_lease_the_caller_named(store):
    """`keep` is how the CLI stops `prune` stranding a task. The set is the
    caller's to compute: knowing a held task is still DOING means reading the
    task store, and this module does not know what a task is."""
    holder, idle = agents.claim(store), agents.claim(store)
    agents.hold(holder, "T01", store)
    assert agents.prune(store, keep=[holder]) == [idle]
    assert list(agents.load(store)) == [holder]


def test_prune_without_keep_is_exactly_what_it_was(store):
    """The default has to stay today's behaviour, or every existing caller
    changes meaning."""
    holder, idle = agents.claim(store), agents.claim(store)
    agents.hold(holder, "T01", store)
    assert sorted(agents.prune(store)) == sorted([holder, idle])
    assert agents.load(store) == {}
