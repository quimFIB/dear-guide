"""Applying a staged batch: the one implementation, for every host.

`dg apply` and the web app's Apply button do the same thing, and the *order* in
which they do it is the correctness argument:

1. **render first** — rendering is pure, so a rendering bug aborts before
   anything has been written;
2. **then the store**, which is the thing that must not be lost;
3. **then take the applied ops out of the tray** — they are in the store now,
   and leaving them staged would re-apply them. Out, not *cleared*: an apply
   is not instantaneous, and whatever was staged while it ran is the next
   batch, not this one's leftovers;
4. **then the view**, last, because it is the one failure that is recoverable:
   `dg render` regenerates it.

Ordering is half the argument and was for a long time mistaken for all of it.
It covers a failure *between* the four writes; it says nothing about a failure
*inside* one, and a half-written `decisions.json` is the state no `dg` command
can repair. Every write below therefore goes through `project.write_atomic`,
which swaps a fully-written file into place or leaves the old one untouched.

And atomicity is not isolation. A write that lands whole can still land *on top
of* another writer's, and the sequence here is a read-modify-write: load the
store, apply a batch to a copy, save it. Two hosts sharing a project — which
`commands/serve.md` tells the user to do — could each load the store, each
apply their own batch, and the later write would erase the earlier one's
decisions from the store while `discard` took its ops out of the tray. An
*applied* batch, reported as applied, gone with no error and nothing in any
diff. So the whole sequence is held: see `_writing` below.

And the tray is half of that sequence, which took a second finding to see. The
store is re-read under its lock; the **tray was read by the host before any lock
existed**, so an unstaging arriving between that read and `discard` succeeded,
named the op it removed, and watched it land anyway. `trays()` below is the
other half, and the two together give the whole act one order: trays, then
stores, from the read through the discard.

That order lived twice — once in `cli.py`, once inlined in `server.py` — and a
third copy was about to appear when the browser learned to apply task ops. It
lives here now. Nothing in this module prints or exits; it returns a `Result`
and lets each host say it in its own idiom, which is what kept the two copies
from being shared in the first place.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

from dgraph import agents, cross, pending, project, render, task_pending, task_render
from dgraph.model import Graph
from dgraph.tasks import TaskGraph

#: How long an apply waits for another one to finish. Longer than the trays'
#: own wait, because what is held across is a validate and a render rather than
#: a few hundred bytes of JSON, and giving up early would drop the writer back
#: to the unsynchronised behaviour this exists to remove.
APPLY_WAIT = 10.0


def _writing(proj: project.Project):
    """Hold the project's stores for the duration of one apply.

    Both stores, not only the one being written. The one *not* being written is
    held because it is being **read**: `cross.guard_decisions` judges a decision
    batch against the task store, so a task apply landing halfway through would
    have the guard vouch for a pairing that never existed.

    `project.stores` nests, so this is a no-op inside a `writing()` that spans
    both batches — which is how `dg apply` and the web app hold the pair across
    the two of them without deadlocking on the second acquisition.
    """
    return project.stores(proj, wait=APPLY_WAIT)


def writing(proj: project.Project | None = None):
    """Hold both stores across a **sequence** of applies. What a host wraps
    `dg apply` in, because that command runs two of them.

    The two batches are independent by design — "a task batch that will not
    apply must never stop a decision batch that would" — and that independence
    is about *refusal*, not about isolation. Each `apply_*` used to take the
    lock for itself, so between the decision write and the task write the lock
    was free and the pair on disk was a state nothing had validated: the
    decision batch is judged against `cross.tasks_after`, the task graph as it
    will stand *once the task batch lands*. Half of that is not a state anyone
    approved, and it is one `dg check` reports and the commit gate denies —
    for every agent in the repository, not just the one applying. Audit F27.

    Holding the pair across both closes it for any other *writer*, and for any
    reader that takes the same lock — `check.run` does. What it does not close
    is a process dying between the two writes: two files cannot be swapped as
    one, and the honest scope of this is "no observer sees the intermediate",
    not "the intermediate cannot exist". The remedy for that case is the one
    that was always there — run `dg apply` again; the batch that did not land
    is still in its tray.
    """
    return project.stores(proj or project.find(), wait=APPLY_WAIT)


@contextlib.contextmanager
def trays(proj: project.Project | None = None, *, wait: float = APPLY_WAIT):
    """Hold both staging trays across a **read, apply and discard**.

    What a host wraps `dg apply` in, *outside* `writing()`, and the half of the
    lock story `writing()` is not.

    `writing()` closed the store side: the batch is re-read under the lock
    because "a graph loaded before the lock was taken is a graph another writer
    may already have moved past". The tray had the identical problem and never
    got the identical treatment — both hosts read their trays before taking any
    lock, and `pending.held` was first taken at the very end, inside `discard`.
    Everything in between ran on a snapshot any writer could legally change, so
    a `dg drop` arriving in that window **succeeded, named the op it removed,
    and the op landed anyway**: two commands reporting success over one op, with
    nothing in any diff. `dg edit` was the same window with a worse landing —
    the stale wording in the store and the correction stranded in a tray that
    can never apply it. Audit W-F2.

    What this does **not** promise is that a dropped op never lands. A drop that
    arrives before the read leaves the op unapplied; one that arrives after the
    discard is *correctly* refused, because by then the op really is in the
    store. Arrival order decides between those two and both are right. What is
    closed is the third outcome, where the two reports contradict each other.

    **Ordering.** Trays before stores, everywhere, so two holders cannot deadlock
    against each other — `apply_decisions` takes its own tray before `_writing`
    for that reason alone, and both acquisitions are no-ops inside this span
    because `project.held` nests. `discard`, not `clear`, still applies: work
    staged *while* an apply runs belongs to the next batch, and holding the tray
    makes such a writer **wait**, never lose the op.

    Trays the project does not have are still held: a tray is created by staging,
    so unlike a store its absence is not a reason to skip the lock — the writer
    this is guarding against may be the one about to create it.
    """
    proj = proj or project.find()
    with contextlib.ExitStack() as stack:
        for path in (proj.pending, proj.task_pending):
            stack.enter_context(pending.held(path, wait=wait))
        yield


@dataclass(frozen=True)
class Result:
    """What happened, in enough detail for either host to report it.

    `view_error` is the partial success: the store was written and the batch is
    safe, but the generated view is now stale. It is not an exception because it
    is not a failure of the apply — reporting it as one would tell a user their
    work was lost when it was not.
    """

    applied: int
    dry_run: bool
    store: str          # the file that was written, for the message
    view: str           # the file that was regenerated
    view_error: str | None = None
    graph: Graph | TaskGraph | None = None   # the result, for a caller that wants it
    #: Premises that moved between staging this batch and applying it — see
    #: `pending.drift`. Reported, never a refusal: the invariants already refuse
    #: the dangerous case (a decided answer on a reopened premise is a blocking
    #: `propagation` finding), so what is left here is the batch that applies
    #: cleanly while resting on something that has quietly changed underneath.
    #: Carried on `Result` rather than printed here for the reason `view_error`
    #: is: this module says nothing, and each host reports in its own idiom.
    drift: tuple[dict, ...] = ()

    @property
    def ok(self) -> bool:
        return self.view_error is None


def apply_decisions(ops: list[dict], dry_run: bool = False,
                    g: Graph | None = None) -> Result:
    """Validate a decision batch against a copy, then write it.

    Raises `pending.ApplyError` if the batch would leave the graph invalid;
    nothing has been written when it does.

    `g` is the store to apply against, for the **dry run only**: a caller that
    has already loaded it passes it rather than having it read twice. Both
    hosts pass nothing — the CLI used to pass a graph it loaded through a
    helper that exits the process, which is how a missing decision store came
    to abort an applicable task batch. The write path deliberately ignores it
    and re-reads under the lock either way: a graph loaded before the lock was
    taken is a graph another writer may already have moved past.
    """
    proj = project.find()
    if dry_run:
        g = Graph.load(proj.store) if g is None else g
        return Result(len(ops), True, proj.store.name, proj.view.name,
                      graph=pending.apply_all(g, ops, cross.guard_decisions()),
                      drift=tuple(pending.drift(g, ops)))
    # The tray before the stores, and its own even when a host already holds it
    # through `trays()` — `project.held` nests, so the inner take is free, and
    # taking it here is what gives every writer in the tool one lock order.
    # Without it this function is store-then-tray (`_writing`, then `discard`)
    # while a host is tray-then-store, which is a deadlock between two writers
    # rather than a lost update. Audit W-F2.
    with pending.held(proj.pending, wait=APPLY_WAIT), _writing(proj):
        # Re-read under the lock, whatever the caller passed. `g` was loaded
        # before this function was called and therefore before the lock was
        # taken, so applying to it would write a result computed from a store
        # that has since moved — the lost update the lock is here to stop. The
        # cost is one file read; the ops are then judged against the store they
        # will actually land on, and an op that no longer applies is refused
        # loudly by `apply_all` rather than silently overwriting.
        g = Graph.load(proj.store)
        # Before anything is applied, and against the store as re-read: what
        # moved under this batch while it sat in the tray.
        moved = tuple(pending.drift(g, ops))
        # `cross.guard_decisions()` judges the result against the work resting
        # on it: a decision batch can close a cycle through the task store
        # (`D02 opens D01`, with a task between them), and neither store's own
        # validator can see it. Called inside the lock, so the task store it
        # reads cannot change under it.
        out = pending.apply_all(g, ops, cross.guard_decisions())
        view_text = render.render(out)
        out.save(proj.store)
        # `discard`, not `clear`: anything staged while this apply was running
        # belongs to the next batch, and clearing would drop it silently.
        pending.discard(ops, proj.pending)
        try:
            project.write_atomic(proj.view, view_text)
        except OSError as exc:
            return Result(len(ops), False, proj.store.name, proj.view.name,
                          view_error=str(exc), graph=out, drift=moved)
    return Result(len(ops), False, proj.store.name, proj.view.name, graph=out,
                  drift=moved)


def _record_holdings(ops, tg, proj) -> None:
    """Who is holding live work, kept OUTSIDE the store.

    Here rather than in `_apply_one`, which builds a graph and must not write
    files, and here rather than on the `Task`, which is the point: the task
    store is committed and kept forever, agent names are recycled, and "who
    finished this" is noise six months later that a recycled name no longer even
    identifies. Who has work right now is a fact about a RUN, so it lives with
    the names themselves in `.dgraph-agents.json` -- scratch, gitignored, gone
    when the run is.

    Best-effort by design. A run whose holdings could not be written is still a
    run whose WORK was applied, and the store is the thing that must not be lost;
    this is a convenience for triage during a fan-out.
    """
    if not any(o.get("op") == "set_status" for o in ops):
        return
    try:
        for op in ops:
            if op.get("op") != "set_status":
                continue
            tid = op.get("task")
            if tid is None or tid not in tg.tasks:
                continue
            if tg.tasks[tid].status == "DOING":
                agents.hold(op.get("by"), tid, proj.root)
            else:
                agents.drop_hold(tid, proj.root)
    except (OSError, ValueError):
        pass


def apply_tasks(ops: list[dict], dry_run: bool = False,
                tg: TaskGraph | None = None) -> Result:
    """The same for a task batch. Independent of the decision one on purpose:
    a task batch that will not apply must never stop one that would."""
    proj = project.find()
    if dry_run:
        tg = TaskGraph.load(proj.tasks) if tg is None else tg
        return Result(len(ops), True, proj.tasks.name, proj.task_view.name,
                      graph=task_pending.apply_all(tg, ops, cross.guard_tasks()))
    with pending.held(proj.task_pending, wait=APPLY_WAIT), _writing(proj):
        tg = TaskGraph.load(proj.tasks)     # under the lock; see apply_decisions
        out = task_pending.apply_all(tg, ops, cross.guard_tasks())
        view_text = task_render.render(out)
        out.save(proj.tasks)
        _record_holdings(ops, out, proj)
        pending.discard(ops, task_pending.path())
        try:
            project.write_atomic(proj.task_view, view_text)
        except OSError as exc:
            return Result(len(ops), False, proj.tasks.name, proj.task_view.name,
                          view_error=str(exc), graph=out)
    return Result(len(ops), False, proj.tasks.name, proj.task_view.name, graph=out)
