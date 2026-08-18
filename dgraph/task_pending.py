"""Staging for the task store.

The store half of `dgraph/pending.py` — `load`, `save`, `stage`, `drop`,
`replace`, `clear` — treats ops as opaque dicts and takes a path, so it is
reused verbatim; only the *apply* half is typed on a graph, and this is the task
version of it.

The staging file is `.dgraph-task-pending.json`, deliberately separate from the
decision one. `pending.preview` walks every op in a pending file and
`_apply_one` raises on any op it does not recognise, so mixing the two kinds
would break every decision command until the file was cleared.

**There is no `expand` here, and that is the design working.** Reopening a
decision has to mark its decided descendants PROVISIONAL, because decision
status is stored and would otherwise go stale. Task blocked-ness is derived, so
finishing a task propagates nothing at all — the next `waiting_on` call simply
sees the new status. Nothing to compute, nothing to leave inconsistent.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path

from dgraph import project
from dgraph.pending import ApplyError
from dgraph.tasks import STATUSES, Task, TaskEdge, TaskGraph
from dgraph.violation import Violation

OPS = {"add_task", "add_dep", "remove_dep", "set_status", "set_link"}

#: An extra validator over a proposed task graph — see `apply_all`.
Checker = Callable[[TaskGraph], list[Violation]]


def path() -> Path:
    return project.find().task_pending


def _apply_one(tg: TaskGraph, op: dict) -> None:
    kind = op.get("op")
    if kind not in OPS:
        raise ApplyError(f"unknown task op {kind!r}")

    if kind == "add_task":
        if op["id"] in tg.tasks:
            raise ApplyError(f"{op['id']} already exists")
        tg.tasks[op["id"]] = Task(
            id=op["id"], title=op["title"], area=op["area"],
            status=op.get("status", "TODO"), note=op.get("note"),
            format=op.get("format") if op.get("note") else None,
            because=op.get("because"), evidence_for=op.get("evidence_for"),
        )
        return

    if kind == "add_dep":
        src = op["from"]
        if src not in tg.tasks:
            raise ApplyError(f"unknown task {src!r}")
        targets = sorted(set(op["to"]))
        for e in tg.edges:
            if e.src == src:
                e.to = sorted(set(e.to) | set(targets))
                return
        tg.edges.append(TaskEdge(src=src, to=targets))
        return

    if kind == "remove_dep":
        # The undo `add_dep` never had. Without it the only editable structure
        # in the task graph is what was declared when a task was created, and
        # every later correction is a hand-edit of tasks.json.
        src = op["from"]
        targets = set(op["to"])
        hit = [e for e in tg.edges if e.src == src and set(e.to) & targets]
        if not hit:
            raise ApplyError(
                f"{src} is not a prerequisite of "
                f"{', '.join(sorted(targets))} — nothing to remove"
            )
        for e in hit:
            e.to = sorted(set(e.to) - targets)
        tg.edges = [e for e in tg.edges if e.to]
        return

    if kind == "set_link":
        # The emergent case: work turned up a question, so the link is added
        # after the fact — often after the task is already done.
        tid = op["task"]
        if tid not in tg.tasks:
            raise ApplyError(f"unknown task {tid!r}")
        for fld in ("because", "evidence_for"):
            if op.get(fld) is not None:
                setattr(tg.tasks[tid], fld, op[fld])
        # Cleared through a separate key, because an absent field and a field
        # set to nothing have to stay distinguishable in a stored op.
        for fld in op.get("clear", ()):
            if fld not in ("because", "evidence_for"):
                raise ApplyError(f"cannot clear {fld!r}")
            setattr(tg.tasks[tid], fld, None)
        return

    if kind == "set_status":
        tid = op["task"]
        if tid not in tg.tasks:
            raise ApplyError(f"unknown task {tid!r}")
        t = tg.tasks[tid]
        was = t.status
        t.status = op["status"]
        if was == "DONE" and t.status != "DONE":
            # The date and the outcome describe a completion that no longer
            # holds. Task readiness is derived and cannot go stale; this is the
            # one thing the task store does keep, so it is cleared here rather
            # than left to rot.
            t.done = t.outcome = None
        for fld in ("done", "outcome", "note", "why"):
            if op.get(fld) is not None:
                setattr(t, fld, op[fld])
        if op.get("format") is not None and op.get("note") is not None:
            t.format = op["format"]
        return


def preview(tg: TaskGraph, p: Path | None = None, *, skip: int | None = None) -> TaskGraph:
    """The task graph as it will stand once the staged ops apply."""
    from dgraph import pending

    out = copy.deepcopy(tg)
    for i, op in enumerate(pending.load(p or path())):
        if i == skip:
            continue
        try:
            _apply_one(out, op)
        except KeyError as exc:
            raise ApplyError(
                f"staged op {i} is missing required field {exc.args[0]!r}"
            ) from None
    return out


def vet(tg: TaskGraph, op: dict) -> None:
    """Raise if `op` could not be staged against `tg`.

    The shared stage-time floor, matching `pending.vet`: the op must apply, its
    targets must exist, and any status it writes must be legal. Completeness
    rules (an outcome on a DONE task) stay with `apply`, where a transitional
    mid-batch state is allowed.
    """
    probe = copy.deepcopy(tg)
    try:
        _apply_one(probe, op)
    except KeyError as exc:
        raise ApplyError(f"op is missing required field {exc.args[0]!r}") from None
    unknown = [t for t in (op.get("to") or []) if t not in tg.tasks]
    if unknown:
        raise ApplyError(f"unknown task(s): {', '.join(unknown)}")
    status = op.get("status")
    if status is not None and status not in STATUSES:
        raise ApplyError(f"illegal status {status!r} — one of {', '.join(STATUSES)}")
    if (op.get("op") == "set_status" and status == tg.tasks[op["task"]].status
            and not any(op.get(f) for f in ("outcome", "note", "why"))):
        # A no-op that reads like progress. Refused with the current status
        # named, since the caller is often an agent that has lost track.
        raise ApplyError(f"{op['task']} is already {status}")


def apply_all(tg: TaskGraph, ops: list[dict],
              also: Checker | None = None) -> TaskGraph:
    """Apply to a copy and validate. Raises rather than returning a bad graph.

    `also` carries the cross-graph invariants, passed in by the caller for the
    reason `pending.apply_all` documents: this module cannot see a decision,
    and must not have to. Only blocking findings refuse.
    """
    out = copy.deepcopy(tg)
    for op in ops:
        _apply_one(out, op)
    problems = [p for p in out.validate() if p.blocking]
    problems += [p for p in also(out) if p.blocking] if also else []
    if problems:
        raise ApplyError(
            "would leave the task graph invalid:\n  "
            + "\n  ".join(str(p) for p in problems)
        )
    return out
