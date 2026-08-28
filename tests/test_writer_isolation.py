"""Two writers in one clone, and the files nothing holds between them.

`tests/test_pair_lock.py` covers the two **stores**; `tests/test_staging_atomicity.py`
covers a group of writes reaching one **tray** as one write. This file covers
what neither does: the files a second writer can move under the first, where the
lock is either absent or is taken too late to protect the read that matters.

Three properties, one per finding, and they are three faces of one absence — a
path this tool writes that no lock names:

- **`W-F1`** `.dgraph-incoming.json` is read-modify-written by every `dg incoming`
  route and by nothing that holds it. It quarantines the one class of conflict
  the design reserves for a person, so a lost write there is a lost human
  judgement.
- **`W-F2`** `dg apply` reads the tray *before* it takes any lock and only
  re-takes it at the very end, inside `discard`. `applying.py` argues at length
  that the **store** must be re-read under the lock — *"a graph loaded before the
  lock was taken is a graph another writer may already have moved past"* — and
  the tray never got the same treatment.
- **`W-F3`** `ranges.issue` is an unlocked load-modify-save of
  `.dgraph-range.json`, reached from `pending.stage_all` for **both** trays. Two
  different locks over one file is no lock at all, and the comment at the call
  site says the opposite.

**How these are driven.** Following `test_pair_lock.py`: module functions in the
order the command performs them, with the file and lines named in each docstring,
because that is what makes the interleave a permitted one rather than an invented
one. Where mutual exclusion is the property, the two writers are real threads —
a simulated race cannot be fixed by a lock, so simulating one would produce a
test no fix could pass.

Each finding gets two kinds of test. The **consequence** tests state the damage
and are what a fix has to stop. The **structural** ones assert the lock file is
held at the moment it matters; they are cheap, they cannot go flaky, and they are
the codebase's own idiom (`test_stores_locks_both_halves`).
"""

from __future__ import annotations

import contextlib
import json
import threading
import time

import pytest

from dgraph import (agents, applying, cross, integrate, pending, project, ranges,
                    render, task_pending)
from dgraph.model import Graph
from dgraph.tasks import TaskGraph
from tests.conftest import FIXTURE, TASK_FIXTURE

#: How long a thread waits at a handshake before giving up on it. Bounded, and
#: the assertions are on the store rather than on the clock: with the
#: read-modify-write unsynchronised both threads arrive and it costs nothing,
#: and with it synchronised the second thread simply waits this long and then
#: does its own load-modify-save — which is the correct outcome, reached
#: slowly. A test that asserted on the timing instead would have to say how
#: fast a correct implementation is, which is not a property anybody wants
#: pinned.
HOLD = 0.3


@pytest.fixture
def proj(tmp_path, monkeypatch):
    """A project with both stores, and both of them rendered.

    Both, because `W-F3` needs two trays to race and `W-F1` needs a
    contribution that spans the pair.
    """
    (tmp_path / "decisions.json").write_text(json.dumps(FIXTURE, indent=2),
                                             encoding="utf-8")
    (tmp_path / "tasks.json").write_text(json.dumps(TASK_FIXTURE, indent=2),
                                         encoding="utf-8")
    monkeypatch.setattr(project, "_override", tmp_path)
    p = project.find()
    render.write(Graph.load(p.store), p.view)
    return p


def _run_both(first, second):
    """Run two writers at once, each held at its own handshake until the other
    has read, and return them in start order.

    The handshake is `HOLD`-bounded in both directions, so this arrangement
    forces the overlap **where the code permits one** and merely costs `HOLD`
    where it does not.
    """
    ready = threading.Barrier(2)
    out: dict[str, BaseException | None] = {}

    def go(name, fn):
        try:
            out[name] = fn(ready)
        except BaseException as exc:            # noqa: BLE001 — reported, not raised
            out[name] = exc

    threads = [threading.Thread(target=go, args=(n, f), daemon=True)
               for n, f in (("first", first), ("second", second))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5 + HOLD * 4)
        assert not t.is_alive(), "a writer never finished — deadlock"
    return out["first"], out["second"]


def _sync(ready):
    """Arrive at the handshake, and do not let a slow partner wedge the test."""
    with contextlib.suppress(threading.BrokenBarrierError):
        ready.wait(timeout=HOLD)


# ---- W-F2 · the tray, read before the lock that protects it --------------
#
# `cli.apply` (cli.py:1206-1216) is:
#
#     ops      = _staged_ops(proj.pending, …)        ← no lock held
#     task_ops = _staged_ops(proj.task_pending, …)   ← no lock held
#     with applying.writing(proj):                   ← the stores, not the trays
#         _apply_decisions(ops, …)
#
# and `server._apply` (server.py:788-789) is the same two reads before the same
# span. `pending.held` is first taken at the very end, inside `discard`, so
# everything between runs on a snapshot any writer may legally change.
#
# **The property is agreement, not prevention.** A drop that arrives before the
# read must leave the op unapplied; one that arrives after the discard must be
# refused, because by then the op really is in the store and `dg drop` saying so
# is correct. The defect is the *third* outcome, which only the window makes
# possible: the drop succeeds, says which op it removed, and the op lands anyway.
# Asserting "a dropped op never lands" instead would demand something no correct
# implementation provides — the arrival order genuinely decides which of the two
# legitimate outcomes happens.


def _apply_staged():
    """The host sequence for "apply everything staged", and the lock `W02` adds.

    `applying.trays` is the span both hosts wrap their tray reads in. It is a
    context manager rather than an `apply_everything()` function because
    `apply_decisions(ops)` must keep meaning *apply this batch*: a caller may
    legitimately hand it part of a tray, and `test_apply_keeps_what_was_staged_
    while_it_ran` and `test_an_id_survives_another_writers_apply` both pin that.
    A first prototype re-read the tray inside `apply_decisions` and broke them
    both.

    **Resolved before any thread starts.** `_run_both` reports what a writer
    raised rather than raising it, so a missing route resolved inside a thread
    would leave the apply silently not happening and the assertion trivially
    satisfied — a vacuous green, which is the shape this file checks for
    elsewhere.
    """
    span = getattr(applying, "trays", None)
    assert span is not None, (
        "`applying.trays` does not exist. Reading a tray and applying it is one "
        "act, and it is spread over a read in cli.py/server.py and a write in "
        "applying.py with nothing held between them — see W-F2.")

    def go(p):
        with span(p), applying.writing(p):
            d = applying.apply_decisions(pending.load(p.pending))
            t = applying.apply_tasks(pending.load(p.task_pending))
            return d, t
    return go


def _drop_racing_an_apply(p, tray, ref):
    """One apply and one unstaging, at once, with the apply held at a handshake
    for the length of the second writer's whole window."""
    apply_staged = _apply_staged()

    def apply(ready):
        _sync(ready)
        return apply_staged(p)

    def unstage(ready):
        _sync(ready)
        return pending.drop(ref, tray)

    return _run_both(apply, unstage)


def test_a_drop_that_reports_success_is_honoured(proj):
    """Either the op is unstaged or it is applied — never both, and never with
    `dg drop` reporting the op it removed by name while the op reaches the store.

    Both commands reporting success over one op is the signature of `C-F10` and
    `C-F29`, *"the one that lost work and reported success"*, a third time.
    """
    pending.stage_all([
        {"op": "add_vertex", "id": "D07", "title": "keeper", "area": "Alpha",
         "status": "OPEN"},
        {"op": "add_vertex", "id": "D08", "title": "unstaged", "area": "Alpha",
         "status": "OPEN"},
    ], proj.pending)
    ref = pending.load(proj.pending)[1]["ref"]

    _, unstaged = _drop_racing_an_apply(proj, proj.pending, ref)

    landed = "D08" in Graph.load(proj.store).vertices
    assert not (landed and not isinstance(unstaged, BaseException)), (
        f"`dg drop` reported removing {unstaged} and the op is in the store")
    assert "D07" in Graph.load(proj.store).vertices, (
        "the rest of the batch must still apply — refusing it would trade one "
        "silent loss for a loud one")


def test_a_task_drop_that_reports_success_is_honoured(proj):
    """The same, on the tray that has no orphan check to catch it afterwards.

    Stated separately rather than parametrised because the two trays are two
    files with two locks, and `C-F28` is the precedent for a rule carried to one
    store and not the other.
    """
    pending.stage_all([
        {"op": "add_task", "id": "T07", "title": "keeper", "area": "Alpha",
         "status": "TODO"},
        {"op": "add_task", "id": "T08", "title": "unstaged", "area": "Alpha",
         "status": "TODO"},
    ], task_pending.path())
    ref = pending.load(task_pending.path())[1]["ref"]

    _, unstaged = _drop_racing_an_apply(proj, task_pending.path(), ref)

    landed = "T08" in TaskGraph.load(proj.tasks).tasks
    assert not (landed and not isinstance(unstaged, BaseException)), (
        f"`dg task drop-op` reported removing {unstaged} and it is in the store")


def test_a_revision_accepted_into_the_tray_is_not_stranded(proj):
    """`dg edit`'s window, which lands worse than `dg drop`'s.

    The stale op applies; `discard` cannot find the revision, so it stays; and
    the next apply is refused for an id that now exists. The store holds the
    wording the edit was correcting and the correction sits in a tray that will
    never accept it — `C-F26`'s cost (*"discarded everything the edit said,
    silently, while reporting success"*) reached by another route.
    """
    pending.stage_all([{"op": "add_vertex", "id": "D07", "title": "as first typed",
                        "area": "Alpha", "status": "OPEN"}], proj.pending)
    ref = pending.load(proj.pending)[0]["ref"]
    revised = {"op": "add_vertex", "id": "D07", "title": "as corrected",
               "area": "Alpha", "status": "OPEN"}

    apply_staged = _apply_staged()

    def apply(ready):
        _sync(ready)
        return apply_staged(proj)

    def edit(ready):
        _sync(ready)
        return pending.replace(ref, revised, proj.pending)

    _, edited = _run_both(apply, edit)

    title = Graph.load(proj.store).vertices["D07"].title
    left = [o.get("title") for o in pending.load(proj.pending)]
    assert not (not isinstance(edited, BaseException)
                and title == "as first typed" and left), (
        f"`dg edit` was accepted, the store kept {title!r}, and the revision is "
        f"stranded in a tray that can never apply it: {left}")


def test_the_tray_is_held_while_the_batch_it_gave_up_is_applied(proj, monkeypatch):
    """The structural half, and the one that asserts the **shipped host** holds
    it rather than that a lock exists somewhere.

    Driven through `dg apply` itself for that reason: `applying.trays` being
    correct says nothing about whether `cli.apply` wraps its two `_staged_ops`
    calls in it, and that omission is the whole of `W-F2`. Shape 15 — the layer
    verified has to be the layer that ships. `server._apply` reaches the same
    span one call in.

    The probe is `pending.apply_all`, which runs inside `apply_decisions` after
    that command has taken the tray's lock and before it discards the ops. It
    calls through; nothing is stubbed.
    """
    from typer.testing import CliRunner

    from dgraph.cli import app

    pending.stage_all([{"op": "add_vertex", "id": "D07", "title": "x",
                        "area": "Alpha", "status": "OPEN"}], proj.pending)
    lock = proj.pending.with_name(proj.pending.name + ".lock")
    seen = []
    real = pending.apply_all
    monkeypatch.setattr(
        pending, "apply_all",
        lambda *a, **k: (seen.append(lock.exists()), real(*a, **k))[1])

    res = CliRunner().invoke(app, ["--project", str(proj.root), "apply"])

    assert res.exit_code == 0, res.output
    assert seen and all(seen), (
        "`dg apply` did not hold the tray while applying the batch it read from "
        "it — any writer may drop, edit or clear it in that window")


# ---- W-F3 · the id watermark, guarded by whichever tray lock ------------
#
# `pending.stage_all` (pending.py:243-261) holds *a* tray lock and then calls
# `ranges.note_ops` -> `ranges.issue`, which is `load` -> compare -> `save` on
# `.dgraph-range.json`. There are two trays and one range file. The comment
# there claims the scope covers it; these say what it costs when it does not.


@pytest.fixture
def granted(proj):
    """A clone with a grant, and a mark on each half of it."""
    ranges.save({"D": ranges.Grant(50, 99, 55), "T": ranges.Grant(50, 99, 65)},
                proj.root)
    return proj


@pytest.fixture
def raced(granted, monkeypatch):
    """`granted`, with the watermark's read forced to complete in both writers
    before either writes it back.

    The handshake goes on `ranges.load` rather than on the callers, so what is
    forced is the overlap `stage_all` already permits between the two trays and
    nothing else. One patch rather than one per thread: both writers stage a
    single op and so call this exactly once, and two threads patching one module
    attribute is a race in the harness rather than in the subject.
    """
    ready = threading.Barrier(2)
    real = ranges.load

    def load(root=None):
        out = real(root)
        _sync(ready)
        return out

    monkeypatch.setattr(ranges, "load", load)
    return granted


def _stage_both(p):
    """A decision stage and a task stage at once — different tray locks, one
    range file."""
    return _run_both(
        lambda _: pending.stage_all(
            [{"op": "add_vertex", "id": "D56", "title": "d", "area": "Alpha",
              "status": "OPEN"}], p.pending),
        lambda _: pending.stage_all(
            [{"op": "add_task", "id": "T66", "title": "t", "area": "Alpha",
              "status": "TODO"}], task_pending.path()),
    )


def test_two_trays_cannot_lose_each_other_s_watermark(raced, monkeypatch):
    """Both marks must move.

    The two writers hold `.dgraph-pending.json.lock` and
    `.dgraph-task-pending.json.lock` and write one `.dgraph-range.json`, so the
    later save carries the earlier one's mark away — and which one loses is
    whichever got there first, so both halves are asserted.
    """
    _stage_both(raced)

    monkeypatch.undo()          # read the file, do not re-enter the handshake
    marks = ranges.load(raced.root)
    assert (marks["D"].issued, marks["T"].issued) == (56, 66), (
        f"a watermark was lost: D={marks['D'].issued}, T={marks['T'].issued}")


def test_a_lost_watermark_re_offers_an_id_the_grant_already_issued(raced,
                                                                   monkeypatch):
    """The consequence, which is the reason the mark exists at all.

    `ranges.py`: *"Two branches in one worktree share a grant and start from the
    same store, so both allocate the same id."* `next_number` maxes the mark
    against every granted id still in the store or the tray, so the loss is
    invisible until the store moves out from under it — which is exactly what a
    checkout does, and why the file is gitignored.

    Both prefixes, for the reason above: the race decides which one loses.
    """
    was = {p: p.read_bytes() for p in (raced.store, raced.tasks)}
    _stage_both(raced)
    with applying.writing(raced):
        applying.apply_decisions(pending.load(raced.pending))
        applying.apply_tasks(pending.load(task_pending.path()))

    # What `git checkout` does to the other branch: the stores rewind and the
    # gitignored range file does not.
    for path, blob in was.items():
        path.write_bytes(blob)
    monkeypatch.undo()

    d_taken = [v[1:] for v in Graph.load(raced.store).vertices]
    t_taken = [t[1:] for t in TaskGraph.load(raced.tasks).tasks]
    offers = (ranges.next_number("D", d_taken, raced.root),
              ranges.next_number("T", t_taken, raced.root))
    assert offers[0] > 56 and offers[1] > 66, (
        f"the grant re-offered an id it has already issued: D{offers[0]:02d}, "
        f"T{offers[1]:02d} — two branches in one worktree now hold one id, "
        f"which is what the mark exists to stop")


def test_the_range_file_is_held_while_the_mark_is_raised(granted, monkeypatch):
    """The structural half. `issue` must be safe on its own terms rather than by
    inheriting whichever of the two tray locks its caller happened to hold."""
    lock = ranges.path(granted.root).with_name(project.RANGE_NAME + ".lock")
    seen = []
    real = ranges.save
    monkeypatch.setattr(ranges, "save", lambda g, root=None:
                        (seen.append(lock.exists()), real(g, root))[1])

    ranges.issue("D", 56, granted.root)

    assert seen == [True], (
        "the range file was not held while its watermark was being raised")


# ---- W-F1 · the quarantine file, which nothing holds --------------------
#
# `cli.incoming` (cli.py:3996-4041) is `load_incoming` -> mutate the dict ->
# `write_incoming`, per route, with nothing held across it; and `--adopt` is
# `adopt_ops` -> stage into one tray -> stage into the other -> `clear_incoming`,
# which is four writes and no lock.


@pytest.fixture
def contested(proj):
    """A contribution this store contests, built the way `dg integrate` builds
    one: `integrate.plan` over ours/base/theirs, then `save_incoming`.

    Base has `D05` OPEN. This store settles it one way and the arriving side
    settles it another, which is `one_active_edge` — a *semantic* conflict, the
    class that is always put to a person rather than resolved by a rule, because
    the two sides disagree about what is true and no merge strategy can know
    which is right.
    """
    base_g = Graph.load(proj.store)
    base_tg = TaskGraph.load(proj.tasks)

    def settled(answer, falsifier):
        """One side's store, settled through the real op path.

        `pending.expand` derives the group a `close` implies — here the release
        of `D06`, which is `BLOCKED:D05` — so the side is valid because the tool
        refused anything else.

        Driven through the real op path rather than hand-written, and that is the
        method rather than a convenience: an integration conflict is only worth
        testing if **each side was individually correct**, so that the damage
        appears at integration and nowhere earlier. A fixture that hand-wrote the
        JSON could produce a side that was already invalid, and proving two
        broken stores conflict proves nothing.
        """
        g = Graph.load(proj.store)
        op = {"op": "close", "vertex": "D05", "answer": answer,
              "source": "notes.md", "falsifier": falsifier, "opens": []}
        return pending.apply_all(g, pending.expand(g, op))

    # Both derived from the pristine store, before either is written back:
    # `settled` reads `proj.store`, so saving ours first would compose theirs on
    # top of ours and the two sides would agree.
    theirs_g = settled("The arriving answer.", "the corpus changes")
    ours_g = settled("This store's answer.", "it stops being measurable")
    ours_g.save(proj.store)

    rep = integrate.plan(ours_g, base_tg, base_g, base_tg, theirs_g, base_tg,
                         guard=cross.guard_pair())
    assert rep.contested, "the fixture did not produce a contested op"
    integrate.save_incoming(rep, source="b/master", base="0000000",
                            root=proj.root)
    return proj


#: The two entry points `W01` has to add, and the reason these tests name them
#: rather than driving the three calls the command makes today.
#:
#: `answer_one(raw, ref, choice)` takes a dict the caller loaded, and `--adopt`
#: is `adopt_ops` then two `stage_all`s then `clear_incoming`. **No lock placed
#: inside those functions can make either sequence atomic**, because the load
#: that has to be inside the critical section happens in the caller. So the fix
#: is a route that owns the whole read-modify-write, and a test that kept
#: driving the old shape would be one no correct fix could turn green — which is
#: shape 6, a done-condition narrower than the claim, arriving through the test
#: instead of through the spec.
#:
#: Today both are absent and these fail on the assertion below, which says so.
ROUTES = ("answer", "adopt")


def _route(name):
    fn = getattr(integrate, name, None)
    assert fn is not None, (
        f"`integrate.{name}` does not exist. Answering a conflict and adopting "
        f"a contribution are each one act on `.dgraph-incoming.json`, and each "
        f"is spread over a load in `cli.py` and a write in `integrate.py` with "
        f"nothing held between them — see W-F1. A lock alone cannot close that; "
        f"the route has to own the load.")
    return fn


def test_two_writers_cannot_both_settle_one_conflict(contested):
    """Both are told they settled it; the file holds one answer.

    `--take` on a contested `close` is not a small act — it reopens this store's
    decision and writes the arriving answer over it, leaving a permanent
    `replaced_by` record. An agent told *"keeping this store's"* and then finding
    the arriving answer in the store has been shown something false about the one
    act the design reserves for a person.

    The loser must be **refused**, not merely last: two contradictory successes
    is the defect, and a lock that only orders the writes still produces them.
    """
    answer = _route("answer")
    ref = integrate.load_incoming(contested.root)["contested"][0]["ref"]
    first, second = _run_both(
        lambda _: answer(contested.root, ref, "take"),
        lambda _: answer(contested.root, ref, "keep"))

    settled = [r for r in (first, second) if not isinstance(r, BaseException)]
    assert len(settled) == 1, (
        f"both writers were told they settled one conflict: {settled}")
    # `answer_one` reports the choice in prose, so the file's code is checked
    # against the phrase that code produces rather than against the word.
    said = {"take": "the arriving one", "keep": "this store's"}
    kept = integrate.load_incoming(contested.root)["contested"][0]["resolution"]
    assert said[kept] in str(settled[0]), (
        f"the file records {kept!r} and the writer who was told they settled it "
        f"was told {settled[0]!r}")


def test_a_contribution_is_not_adopted_twice(contested):
    """Two adoptions each stage the whole contribution and each clear the file,
    so the trays hold it twice and the record that it arrived is gone.

    The duplicate is refused at apply, which is right; what is left is not. In
    the run this was found by, the task half landed, the decision half did not,
    `.dgraph-incoming.json` had been deleted by both, and `dg check` said
    `✓ all invariants hold` — the third clause of *"quarantined together,
    adjudicated together, applied together"* failing quietly.
    """
    adopt = _route("adopt")
    root = contested.root
    ref = integrate.load_incoming(root)["contested"][0]["ref"]
    _route("answer")(root, ref, "keep")

    def stage(d_ops, t_ops):
        """The receiving store's staging step, which the route takes from its
        caller so `integrate` never learns what either store means."""
        if d_ops:
            pending.stage_all(d_ops, contested.pending)
        if t_ops:
            pending.stage_all(t_ops, task_pending.path())

    landed = _run_both(lambda _: adopt(root, stage), lambda _: adopt(root, stage))

    staged = pending.load(contested.pending) + pending.load(task_pending.path())
    bare = sorted(json.dumps({k: v for k, v in o.items() if k != "ref"},
                             sort_keys=True) for o in staged)
    # Asserted before the duplicate check, and deliberately: `_run_both` reports
    # what a writer raised rather than raising it, so an adoption that failed
    # for any reason at all would leave the trays empty and satisfy "no
    # duplicates" on nothing. That vacuous green happened twice while this file
    # was being written.
    assert bare, f"nothing was adopted at all: {landed}"
    assert len(bare) == len(set(bare)), (
        f"the contribution reached the trays more than once: {bare}")
    assert not integrate.path(root).exists(), (
        "the contribution was adopted and the file that recorded it survives")


def test_the_quarantine_file_is_held_while_it_is_answered(contested, monkeypatch):
    """The structural half. Every route that answers a conflict, adopts one or
    discards one is a read-modify-write of this file, and it is the only file the
    tool writes whose contents are a person's judgement."""
    answer = _route("answer")
    root = contested.root
    lock = integrate.path(root).with_name(project.INCOMING_NAME + ".lock")
    seen = []
    real = integrate.write_incoming
    monkeypatch.setattr(integrate, "write_incoming", lambda raw, r=None:
                        (seen.append(lock.exists()), real(raw, r))[1])

    ref = integrate.load_incoming(root)["contested"][0]["ref"]
    answer(root, ref, "keep")

    assert seen == [True], (
        "the quarantine file was not held while it was being answered")


# ---- M-F1 · the grant file, written by two doors and locked by one ------
#
# `W-F3` locked `ranges.issue`, which is the *watermark* writer. The **grant**
# writer — `dg range --set`, `dg range --clear`, both straight to `ranges.save`
# — was never in that reading, so the file went back to two writers and one
# lock from the other side. The two are not symmetric: `issue` is a
# load-modify-save whose critical section starts at the load, while a grant is
# written blind, which is why the lock now sits on the write rather than on
# each door.


def test_a_fresh_grant_is_not_written_away_by_a_watermark(proj, monkeypatch):
    """`dg range --set` says "granted D50-D99" and the file says something else.

    The operator is told twice — the success line, and the table the same
    command prints under it, which loads before the other writer's save lands.
    Afterwards this clone allocates from the range it was told it had given up,
    which is inside the range another writer is still holding: the one failure
    `.dgraph-range.json` exists to prevent, reached through the command that
    sets it up.
    """
    ranges.save({p: ranges.Grant(1, 49) for p in ranges.PREFIXES}, proj.root)
    ready = threading.Barrier(2)
    real = ranges.load

    def load(root=None):
        """The watermark's read, held open for the length of the other
        writer's whole window. Patched on `load` rather than on `issue` so what
        is forced is the overlap the code permits and nothing else."""
        got = real(root)
        _sync(ready)
        time.sleep(HOLD)
        return got

    monkeypatch.setattr(ranges, "load", load)

    def issuing(_):
        return ranges.issue("D", 13, proj.root)

    def setting(ready_):
        _sync(ready_)
        return ranges.save({p: ranges.Grant(50, 99) for p in ranges.PREFIXES},
                           proj.root)

    issued, set_ = _run_both(issuing, setting)
    # Before the assertion on the file: `_run_both` reports what a writer
    # raised, so a watermark write that never happened would leave the grant
    # intact and satisfy this on nothing.
    for name, out in (("issue", issued), ("save", set_)):
        assert not isinstance(out, BaseException), f"the {name} writer: {out!r}"

    monkeypatch.setattr(ranges, "load", real)
    got = ranges.grant("D", proj.root)
    assert got is not None, "the grant file was lost entirely"
    assert (got.lo, got.hi) == (50, 99), (
        f"`dg range --set 50-99` reported a grant of 50-99 and the file holds "
        f"{got.lo}-{got.hi} — the watermark's save carried the old range back")


def test_the_grant_file_is_held_while_a_grant_is_written(proj, monkeypatch):
    """The structural half. Every writer of this file, not only `issue`."""
    lock = ranges.path(proj.root).with_name(project.RANGE_NAME + ".lock")
    seen = []
    real = project.write_atomic
    monkeypatch.setattr(project, "write_atomic", lambda p, t:
                        (seen.append((p.name, lock.exists())), real(p, t))[1])
    ranges.save({p: ranges.Grant(50, 99) for p in ranges.PREFIXES}, proj.root)
    assert (project.RANGE_NAME, True) in seen, (
        f"the grant file was written with nothing holding it: {seen}")


# ---- M-F2 · the quarantine's producer, which took no lock ---------------
#
# `W-F1` locked every `dg incoming` route — the readers and the answerers. The
# command that *creates* the file was not among them: `cli.integrate` is
# `waiting()` -> plan -> `save_incoming`, an unlocked read-check-act whose
# check is the whole of what keeps one contribution in the file at a time.


@pytest.fixture
def two_contributions(proj):
    """A repository with two branches, each carrying a decision this clone has
    not got. Real git, because the window the lock has to cover is the
    `merge-base` and the four `git show`s `dg integrate` runs inside it."""
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(proj.root), *args], check=True,
                       capture_output=True, text=True)

    git("init", "-q", ".")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-qm", "base")
    for branch, vid in (("theirs", "D08"), ("theirs2", "D09")):
        git("checkout", "-q", "-b", branch, "HEAD~0" if branch == "theirs"
            else "master")
        raw = json.loads(proj.store.read_text(encoding="utf-8"))
        raw["vertices"] = [v for v in raw["vertices"]
                           if v["id"] not in ("D08", "D09")]
        raw["vertices"].append({"id": vid, "title": f"From {branch}",
                                "area": "Alpha", "status": "OPEN",
                                "note": "?"})
        proj.store.write_text(json.dumps(raw), encoding="utf-8")
        git("commit", "-qam", branch)
    git("checkout", "-q", "master")
    return proj


def test_two_integrations_cannot_both_report_a_quarantine(two_contributions,
                                                          monkeypatch):
    """One of them has to be refused, and the refusal is what the guard is.

    `dg integrate` already refuses a second contribution — *"one at a time,
    because the second would be judged against a graph nobody has agreed to
    yet"* — and that refusal was a read outside every lock. Both callers
    therefore passed it, both printed `quarantined in .dgraph-incoming.json`,
    and one contribution was gone with no error anywhere: the ops, the report
    the operator had just read, and the record that the branch ever arrived.
    """
    from dgraph import cli

    root = two_contributions.root
    ready = threading.Barrier(2)
    real = integrate.waiting

    def waiting(r=None):
        """The check, held open across the other caller's whole window."""
        got = real(r)
        _sync(ready)
        time.sleep(HOLD)
        return got

    monkeypatch.setattr(integrate, "waiting", waiting)

    def integrating(ref):
        return lambda _: cli.integrate(ref=ref, base=None)

    outcomes = dict(zip(("theirs", "theirs2"),
                        _run_both(integrating("theirs"),
                                  integrating("theirs2"))))
    monkeypatch.setattr(integrate, "waiting", real)

    landed = [ref for ref, out in outcomes.items()
              if not isinstance(out, BaseException)]
    raw = integrate.load_incoming(root)
    # Before the count, and deliberately: `_run_both` reports what a writer
    # raised, so two refusals would satisfy "at most one landed" on nothing.
    assert raw, f"neither contribution was quarantined at all: {outcomes}"
    assert len(landed) == 1, (
        f"{len(landed)} callers were told their contribution was quarantined "
        f"and the file holds one: {outcomes}")
    assert raw.get("source") == landed[0], (
        f"{landed[0]} was told it was quarantined and the file holds "
        f"{raw.get('source')!r}")


# ---- M-F6 · the stranding guard, computed outside the lock it protects ---
#
# `agent_cli.agent_prune` — the launcher's, since the split — is:
#
#     holders = {} if force else _still_working()   # reads tasks.json, no lock
#     gone    = agents.prune(keep=holders)          # takes the lease lock, inside
#
# `_still_working` is the whole of what stops prune stranding work, and it is
# computed *before* the lock and honoured *inside* it. The guard was added
# because "the operator knows the round is over" is not reliable — its own
# docstring calls the trap "the FIRST MINUTE of an agent's life" — so a window
# that reopens it in the narrow cannot be excused by the same argument.


def _agent_taking_work(p, name, tid="T02"):
    """An agent claiming a task the way a run does: stage under its own name,
    then apply — which is what reaches `applying._record_holdings` and so
    `agents.hold`. Driven through the real ops rather than by writing the lease,
    because the lease write is the thing that has to lose the race."""
    def go(ready):
        _sync(ready)
        with pending.as_owner(name):
            pending.stage_all([{"op": "set_status", "task": tid,
                                "status": "DOING"}], p.task_pending)
        return applying.apply_tasks(pending.load(p.task_pending))
    return go


def test_prune_cannot_strand_work_an_agent_claims_while_it_runs(proj, monkeypatch):
    """The stranding `keep` exists to prevent, reached through the window.

    An agent that has claimed a name and not staged yet reads as idle, so the
    guard filters the lease against real task statuses. Between that read and
    the delete it authorises, the agent starts work: the lease gains
    `holding: [T02]`, the task becomes `DOING` — and prune, now inside the
    lease lock, deletes a lease it judged a moment earlier.

    What is left is `DOING` with no holder recorded anywhere and `dg task start`
    refusing it as taken, recoverable only by a hand-written park. Nothing
    detects it afterwards either: a task `DOING` with no holder is exactly what
    an ordinary solo `dg task start` leaves behind. And the operator is told
    `released <name>`, which is true and complete-looking.
    """
    from dgraph import agent_cli as cli

    root = proj.root
    worker = agents.claim(root)
    idle = agents.claim(root)
    ready = threading.Barrier(2)
    real = cli._still_working

    def still_working():
        """The guard's read, held open across the agent's whole window."""
        got = real()
        _sync(ready)
        time.sleep(HOLD)
        return got

    monkeypatch.setattr(cli, "_still_working", still_working)

    pruned, worked = _run_both(lambda _: cli.agent_prune(force=False),
                               _agent_taking_work(proj, worker))
    monkeypatch.setattr(cli, "_still_working", real)

    # Before the property: `_run_both` reports what a writer raised, so a prune
    # that never ran, or work that never applied, would satisfy everything
    # below on nothing.
    for label, out in (("prune", pruned), ("the agent", worked)):
        assert not isinstance(out, BaseException), f"{label}: {out!r}"
    tg = TaskGraph.load(proj.tasks)
    assert tg.tasks["T02"].status == "DOING", "the agent never took the work"
    assert idle not in agents.load(root), "prune released nothing at all"

    assert agents.holdings(root).get("T02") == worker, (
        f"T02 is DOING and no lease records who has it — prune released "
        f"{worker!r} in the window between judging it idle and deleting it; "
        f"holdings are {agents.holdings(root)}")


def test_the_keep_set_is_computed_under_the_lease_lock(proj, monkeypatch):
    """The structural half. What makes the property above hold is that the
    agent's own `agents.hold` — which takes this lock — cannot land between the
    judgement and the delete it authorises."""
    lock = agents.path(proj.root).with_name(agents.AGENTS_NAME + ".lock")
    from dgraph import agent_cli as cli

    agents.claim(proj.root)
    seen = []
    real = cli._still_working
    monkeypatch.setattr(cli, "_still_working",
                        lambda: (seen.append(lock.exists()), real())[1])
    cli.agent_prune(force=False)
    assert seen == [True], (
        "the keep set was computed with the lease file unheld, so the lease it "
        "judges may be a lease another writer has already moved past")
