"""An arriving contribution, expressed as ops against the graph you have.

`F-F3`'s mechanism. The three ways two divergent stores can be brought
together are not equivalent, and the choice here is the whole design:

    git text-merge      fails LOUD   — a conflict, in a file with no semantics
    id-keyed union      fails SILENT — the naive improvement, and the worst
    replay through vet  fails LOUD, and BEFORE the write

The union is worse than the merge it replaces, which is why the policy has to
name which merge it means. Under a union a removal always loses to any side
that still names the record, and nothing reports that a deletion was reverted;
two answers to one question become an id-keyed pick; a park is erased by a
completion because a whole task record is taken from one side. Replayed as
ops, every one of those is something a person has to **drop on purpose**.

**There is no merge driver with invariant knowledge of its own.** This module
derives ops and nothing else. `dg apply` is the merge test, `validate` is the
judge, and a difference this module cannot express as an op is *reported*
rather than invented — see `Unexpressible`. That boundary is the point: a
driver that quietly repaired what it could not express would be a second
implementation of the rules, one commit away from disagreeing with the first.

## Three graphs, not two

Ops are derived from **base → theirs**, then replayed onto **ours**. The base
is what the contributor started from, and it is what makes a removal a
removal: `D07` absent from an arriving store means *deleted* only if the base
had it, and means *never seen* otherwise. A two-graph diff cannot tell those
apart, and guessing is how a deletion gets silently reverted.

So a base is required and its absence is refused rather than worked around.
`git merge-base` supplies it in the case this exists for — a fan-out where
every worker branched from one commit.

## What is left to the seam

A derived op can be **contested**: it applies, but the graph it arrives at
disagrees with what it asserts. That is not this module's call, and it is not
`apply`'s either — only a person knows whether two answers to one question are
a supersession or two questions worded as one. The ops go to
`.dgraph-incoming.json`, never to the tray, so that nothing in this clone
reads an unadjudicated op as though it had been accepted.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field

from dgraph.model import Graph
from dgraph.tasks import TaskGraph

#: The fields `set_fields` carries, in the order a report reads best. Imported
#: rather than restated: an op that writes a field this list forgets is a
#: difference that vanishes at integration, which is the union failure mode
#: reappearing inside the mechanism built to replace it.
from dgraph.pending import FIELDS


@dataclass
class Derived:
    """What an arriving store amounts to, as ops plus what could not be said.

    Two lists rather than one, because they need different treatment and
    collapsing them is how the second becomes invisible. `ops` is replayed;
    `unexpressible` is *printed*, and printing it is the whole of what this
    module can honestly do about it.
    """

    ops: list[dict] = field(default_factory=list)
    unexpressible: list[str] = field(default_factory=list)


def decisions(base: Graph, theirs: Graph) -> Derived:
    """The arriving decision store as ops against `base`.

    Ordered the way the acts were: a record exists before anything attaches to
    it, and a question is reopened before it is answered again. Within that,
    ids are walked in sorted order — the true composition order is not
    recoverable from a store, and a stable order is worth more than a guessed
    one, since it is what makes two runs of this comparable.
    """
    out = Derived()
    _areas(base.areas, theirs.areas, "decision", out)

    # Additions first, so that an edge or a removal later in the list can name
    # a vertex this contribution introduced.
    for vid in sorted(set(theirs.vertices) - set(base.vertices)):
        v = theirs.vertices[vid]
        out.ops.append({"op": "add_vertex", "id": vid, "title": v.title,
                        "area": v.area, "status": v.status,
                        **({"note": v.note} if v.note else {}),
                        **({"format": v.format} if v.format else {})})

    # Edges first, into a list of their own, so that `_fields` can be told
    # which vertices already have a `close` or a `reopen` coming. Both of those
    # ops write `note` and `format` themselves — a close clears them, a reopen
    # sets the note to its `why` — so deriving a `set_fields` for the same
    # change would stage two ops for one act, and the second would read in the
    # tray as a wording somebody edited by hand.
    edge_ops: dict[str, list[dict]] = {}
    for vid in sorted(theirs.vertices):
        side = Derived()
        _edges(base, theirs, vid, side)
        edge_ops[vid] = side.ops
    settled = {vid for vid, ops in edge_ops.items()
               if any(o["op"] in ("close", "reopen") for o in ops)}

    for vid in sorted(set(base.vertices) & set(theirs.vertices)):
        _fields(base.vertices[vid], theirs.vertices[vid], vid, "vertex", out,
                skip=("note", "format") if vid in settled else ())
    for vid in sorted(theirs.vertices):
        out.ops.extend(edge_ops[vid])

    # Removals last, and this is the ordering that matters most. A removal
    # rewrites the edges around it, so a `remove_vertex` replayed before an
    # `add_edge` that names the removed vertex leaves the second op refusing —
    # and a person reading that refusal would be reading about the wrong act.
    for vid in sorted(set(base.vertices) - set(theirs.vertices)):
        out.ops.append({"op": "remove_vertex", "vertex": vid, "mode": "sever"})
    return out


def tasks(base: TaskGraph, theirs: TaskGraph) -> Derived:
    """The arriving task store as ops against `base`. `decisions`' twin."""
    out = Derived()
    _areas(base.areas, theirs.areas, "task", out)

    for tid in sorted(set(theirs.tasks) - set(base.tasks)):
        t = theirs.tasks[tid]
        out.ops.append({"op": "add_task", "id": tid, "title": t.title,
                        "area": t.area,
                        **({"note": t.note} if t.note else {}),
                        **({"because": t.because} if t.because else {}),
                        **({"evidence_for": t.evidence_for}
                           if t.evidence_for else {})})

    for tid in sorted(theirs.tasks):
        was = base.tasks.get(tid)
        t = theirs.tasks[tid]
        if was is not None:
            # A task the base did not have carries its wording and its links
            # inside `add_task` already; deriving them again would stage two
            # ops for one act and read, in the tray, as two.
            _fields(was, t, tid, "task", out)
            _links(was, t, tid, out)
        _status(was, t, tid, out)
    _deps(base, theirs, out)
    for tid in sorted(theirs.tasks):
        _readings(base.tasks.get(tid), theirs.tasks[tid], tid, out)
    for tid in sorted(set(base.tasks) - set(theirs.tasks)):
        out.ops.append({"op": "remove_task", "task": tid, "mode": "sever"})
    return out


# ---- the pieces ----------------------------------------------------------


def _areas(base: list[str], theirs: list[str], what: str,
           out: Derived) -> None:
    """An area list that grew. Reported, never expressed.

    No op writes `areas` — it is the one field of either store that only
    `init` and `import` set — so a contribution that added one arrives with
    every record in it failing `area_known`. Saying so here turns a wall of
    identical refusals into one line naming the cause, which is the same
    grouping every finding in this pass gets.
    """
    fresh = [a for a in theirs if a not in base]
    if fresh:
        out.unexpressible.append(
            f"the arriving {what} store adds the area(s) "
            f"{', '.join(fresh)} — no op writes an area list, so every record "
            f"filed under one will be refused until it exists here")


def _fields(was, now, rid: str, what: str, out: Derived, skip=()) -> None:
    """`set_fields` for the wording that changed. Nothing where none did."""
    changed = {f: getattr(now, f) for f in FIELDS
               if f not in skip
               and getattr(was, f, None) != getattr(now, f, None)}
    if changed:
        key = "vertex" if what == "vertex" else "task"
        out.ops.append({"op": "set_fields", key: rid, **changed})


def _edges(base: Graph, theirs: Graph, vid: str, out: Derived) -> None:
    """The acts that turned `base`'s edges for `vid` into `theirs`'.

    Read off the archive rather than guessed at: **every reopen leaves a
    record**, so a history that grew by two entries is two reopens, and each
    archived edge carries the `why` and the `summary` the op was given. That
    is the one place a store remembers *acts* rather than state, and it is why
    this direction is recoverable at all.
    """
    old_hist, new_hist = base.history(vid), theirs.history(vid)
    was, now = base.active_edge(vid), theirs.active_edge(vid)

    # A reopen per archived edge that is new. Emitted before the close below,
    # because that is the order they happened in and the order that leaves the
    # vertex able to be decided again.
    for e in new_hist[len(old_hist):]:
        out.ops.append({"op": "reopen", "vertex": vid,
                        "why": e.why or "reopened elsewhere",
                        **({"summary": e.summary} if e.summary else {}),
                        **({"format": e.format} if e.format else {})})

    if now is None:
        if was is not None and was.to:
            out.ops.append({"op": "remove_edge", "from": vid,
                            "to": sorted(was.to)})
        return

    if now.decided and not _same_answer(was, now):
        out.ops.append({"op": "close", "vertex": vid, "answer": now.answer,
                        "source": now.source, "to": sorted(now.to),
                        **({"falsifier": now.falsifier}
                           if now.falsifier else {}),
                        **({"date": now.date} if now.date else {}),
                        **({"format": now.format} if now.format else {})})
        return

    # A bare edge: the targets are all it holds, so a difference in them is an
    # `add_edge` or a `remove_edge` and nothing else. A decided edge's targets
    # travel inside the `close` above, because they are part of the answer.
    old_to = set(was.to) if was is not None else set()
    gained, lost = sorted(set(now.to) - old_to), sorted(old_to - set(now.to))
    if gained:
        out.ops.append({"op": "add_edge", "from": vid, "to": gained})
    if lost:
        out.ops.append({"op": "remove_edge", "from": vid, "to": lost})


def _same_answer(was, now) -> bool:
    """Whether these two active edges carry the same decision.

    Compared on the payload a `close` writes, not on the whole edge: `to` is
    handled beside this and `replaced_by` is written later by a different act.
    """
    return was is not None and was.decided and all(
        getattr(was, f) == getattr(now, f)
        for f in ("answer", "falsifier", "source"))


def _links(was, now, tid: str, out: Derived) -> None:
    """`set_link` for a cross-store link that moved, or was dropped."""
    fields = {f: getattr(now, f) for f in ("because", "evidence_for")
              if getattr(was, f, None) != getattr(now, f)}
    if not fields:
        return
    op = {"op": "set_link", "task": tid}
    clear = [f for f, v in fields.items() if v is None]
    op.update({f: v for f, v in fields.items() if v is not None})
    if clear:
        op["clear"] = clear
    out.ops.append(op)


def _status(was, now, tid: str, out: Derived) -> None:
    """The status changes, as the ops that produce them.

    A completion and a stoppage are both **appends**, so a record that gained
    two of either is two acts, and each carries the date and the prose its op
    was given. That is the same property `_edges` leans on in the other store,
    and it is why a park erased by a completion cannot happen here: the two
    ops both land.
    """
    old_stops = list(was.stops) if was is not None else []
    old_done = list(was.completions) if was is not None else []
    prior = was.status if was is not None else "TODO"

    for k in now.stops[len(old_stops):]:
        # PARKED or DROPPED — the status says which, and only the last entry
        # can be the live one, so anything but the last is a stoppage the work
        # has since come back from.
        kind = now.status if k is now.stops[-1] and now.status in (
            "PARKED", "DROPPED") else "PARKED"
        out.ops.append({"op": "set_status", "task": tid, "status": kind,
                        "why": k.why, "date": k.date})
        prior = kind
    for c in now.completions[len(old_done):]:
        if prior == "DONE":
            # `set_status` refuses DONE -> DONE, so a second completion needs
            # the restart that produced it. Recovering *which* status it was
            # picked back up into is not possible from the record; DOING is
            # the one the command offers.
            out.ops.append({"op": "set_status", "task": tid, "status": "DOING"})
        out.ops.append({"op": "set_status", "task": tid, "status": "DONE",
                        "outcome": c.outcome, "done": c.date})
        prior = "DONE"

    if now.status != prior:
        if now.status in ("PARKED", "DROPPED"):
            out.unexpressible.append(
                f"{tid} arrives {now.status} with no reason recorded — "
                f"`task_park_complete` refuses it, and no op can invent one")
            return
        out.ops.append({"op": "set_status", "task": tid,
                        "status": now.status,
                        **({"note": now.note} if now.note else {})})


def _readings(was, now, tid: str, out: Derived) -> None:
    """`read_evidence` per reading that is new. The third append-only record.

    Like `stops` and `completions`, a reading is a dated act, so a list that
    grew is that many acts and each carries the note its op was given. `dg
    confirm` is the door, but the op it stages is this one and replaying it
    reproduces the record exactly."""
    old = len(was.readings) if was is not None else 0
    for r in now.readings[old:]:
        out.ops.append({"op": "read_evidence", "task": tid,
                        "against": r.against, "note": r.note, "date": r.date})


def _deps(base: TaskGraph, theirs: TaskGraph, out: Derived) -> None:
    """`add_dep` / `remove_dep`, per source **and per kind**.

    Per kind because that is what the store holds: `precedes` and `prompted`
    are two claims and can both hold between the same pair, so a diff that
    treated an edge as one thing would drop whichever of the two it read
    second — and the two mean opposite things about whether anything waits.
    """
    def by_kind(tg: TaskGraph) -> dict[tuple[str, str], set[str]]:
        out_: dict[tuple[str, str], set[str]] = {}
        for e in tg.edges:
            out_.setdefault((e.src, e.kind), set()).update(e.to)
        return out_

    old, new = by_kind(base), by_kind(theirs)
    for key in sorted(set(old) | set(new)):
        src, kind = key
        if src not in theirs.tasks:
            continue          # the removal below takes its edges with it
        gained = sorted(new.get(key, set()) - old.get(key, set()))
        lost = sorted(old.get(key, set()) - new.get(key, set()))
        if gained:
            out.ops.append({"op": "add_dep", "from": src, "kind": kind,
                            "to": gained})
        if lost:
            out.ops.append({"op": "remove_dep", "from": src, "kind": kind,
                            "to": lost})


# ---- the collection pass -------------------------------------------------
#
# Every apply path in both stores is a fail-fast loop: `pending.vet_all`,
# `pending.preview`, `pending.apply_all`, `pending.preview_ops`. None
# accumulates, and that is **correct for the caller they were written for** —
# staging is interactive, one op at a time, each vetted at the moment it was
# typed, so you never meet two refusals at once because you never compose two
# ops at once.
#
# Integration changes the caller and keeps the code: N ops, composed elsewhere,
# arriving together. Fail-fast then means a twelve-op contribution with three
# conflicts is four round-trips, in composition order rather than importance
# order, and the person cannot see the invariant failure — the thing that might
# make them reject the whole contribution — until they have adjudicated three
# unrelated questions. That inverts the point of having a seam at all.


@dataclass
class Finding:
    """One thing wrong with an arriving op, and every op it took down with it.

    `grouped` is the half that keeps a report readable. One record removed
    upstream produces a refusal for every arriving op that names it, and they
    are **one cause, not five** — a reader who has to work that out from five
    lines is a reader who stops reading at two.
    """

    kind: str                      # "contested" | "inapplicable"
    store: str                     # "decisions" | "tasks"
    at: int                        # the op's index in its side's derived list
    record: str | None
    message: str
    grouped: list[int] = field(default_factory=list)
    #: The op itself, so that a person can answer *this one* rather than a
    #: position. Positions do not survive: the ops that reach the file are the
    #: ones that applied, expanded, so an index into the derived list names a
    #: different op by the time anybody reads it.
    op: dict | None = None
    #: How the person answered it: `"take"` the arriving one, `"keep"` this
    #: store's. `None` until they do, and adoption refuses while any is None.
    resolution: str | None = None
    #: Why it would not apply, on a **contested** finding that also refused.
    #: One finding rather than two, because they are one fact seen twice: the
    #: refusal is the consequence of the disagreement, and reporting them as
    #: separate lines makes a reader count two conflicts where there is one.
    refusal: str | None = None


#: What a contested op means, by op. **Contested is a provenance property, not
#: a graph one**: `set_fields D07 -> "B"` is a perfectly valid op and nothing
#: about the result reveals the conflict, so it cannot be found by validating
#: the outcome. It is found by asking what the op asserts and what this graph
#: already says — which is why these are spelled out one by one rather than
#: derived from a refusal.
def _contest(g, base, op: dict, store: str) -> tuple[str | None, str] | None:
    """Whether this op arrives at a graph that disagrees with what it asserts.

    **Measured against the base, not against the op.** An op says *make it
    this*, so comparing what it writes with what is here reports every op as
    contested — which is the shape the first version of this had, and it made
    an ordinary retitle nobody else touched look like a conflict. The question
    is whether *this* clone moved the same thing since the base: two writers
    changing one record is contested, one writer changing it is a change.
    """
    if store == "decisions":
        return _contest_decision(g, base, op)
    return _contest_task(g, base, op)


def _contest_decision(g: Graph, base: Graph, op: dict):
    kind, vid = op.get("op"), op.get("vertex") or op.get("id")
    if kind == "add_vertex" and vid in g.vertices:
        return vid, f"{vid} is taken — here it is {g.vertices[vid].title!r}"
    if vid not in g.vertices or vid not in base.vertices:
        return None
    mine, was = g.vertices[vid], base.vertices[vid]
    if kind == "set_fields":
        # Moved here *and* to somewhere else. Two writers making the same edit
        # is not a conflict, and reporting it as one puts a question to a
        # person whose two answers are identical.
        moved = [f for f in FIELDS
                 if f in op and getattr(mine, f) != getattr(was, f)
                 and getattr(mine, f) != op[f]]
        if moved:
            f = moved[0]
            return vid, (f"{vid} {f} differs — here {getattr(mine, f)!r}, "
                         f"arriving {op[f]!r}")
    if kind == "close":
        e, e_was = g.active_edge(vid), base.active_edge(vid)
        mine_answer = e.answer if e is not None and e.decided else None
        base_answer = e_was.answer if e_was is not None and e_was.decided else None
        if mine_answer is not None and mine_answer != base_answer:
            return vid, (f"{vid} was answered here too — "
                         f"{_clip(mine_answer)!r} against "
                         f"{_clip(op['answer'])!r}")
    return None


def _contest_task(tg: TaskGraph, base: TaskGraph, op: dict):
    kind, tid = op.get("op"), op.get("task") or op.get("id")
    if kind == "add_task" and tid in tg.tasks:
        return tid, f"{tid} is taken — here it is {tg.tasks[tid].title!r}"
    if tid not in tg.tasks or tid not in base.tasks:
        return None
    mine, was = tg.tasks[tid], base.tasks[tid]
    if kind == "set_fields":
        # See `_contest_decision`: the same edit made twice is not a conflict.
        moved = [f for f in FIELDS
                 if f in op and getattr(mine, f) != getattr(was, f)
                 and getattr(mine, f) != op[f]]
        if moved:
            f = moved[0]
            return tid, (f"{tid} {f} differs — here {getattr(mine, f)!r}, "
                         f"arriving {op[f]!r}")
    if (kind == "set_status" and op.get("status") == "DONE"
            and len(mine.completions) > len(was.completions)):
        return tid, (f"{tid} was finished here too ({mine.done}, "
                     f"{_clip(mine.outcome)!r}) — arriving "
                     f"{_clip(op.get('outcome'))!r}")
    return None


def _clip(text: str | None, width: int = 48) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[:width - 1] + "…"


def _subjects(op: dict) -> set[str]:
    """Every record an op names, for the grouping above.

    Both stores in one function because the keys are the same four and the
    question is the same: if one of these is already broken, this op is not a
    second fault — it is the same fault, seen again.
    """
    out = {op.get(k) for k in ("vertex", "task", "id", "from", "against")}
    out |= set(op.get("to") or [])
    return {x for x in out if isinstance(x, str)}


def _walk(g, base, ops: list[dict], store: str,
          expand) -> tuple[list[Finding], object, list[dict]]:
    """Replay `ops` onto a copy of `g`, collecting rather than stopping.

    Two outcomes per op, and the distinction is what decides who is asked:

    **contested** — it applies, but this graph disagrees with what it asserts.
    Recorded and **applied to the probe**, so the ops after it are judged
    against a coherent graph rather than against one missing an act they build
    on. Applying it here is an enumeration technique and not a decision to
    accept it: nothing on disk moves until somebody has answered.

    **inapplicable** — it cannot apply at all. Recorded and skipped, and every
    later op naming the same record is folded into that one finding.
    """
    from dgraph.pending import ApplyError
    probe = copy.deepcopy(g)
    findings: list[Finding] = []
    kept: list[dict] = []
    by_record: dict[str, Finding] = {}

    for i, op in enumerate(ops):
        contested = None
        hit = _contest(probe, base, op, store)
        if hit is not None:
            rid, why = hit
            contested = Finding("contested", store, i, rid, why, op=op)
            findings.append(contested)
        # Expanded against the probe rather than against the arriving store:
        # what a reopen drags into PROVISIONAL is a fact about *this* graph,
        # and the arriving side computed it about a different one.
        # Expanded ops inherit their parent's ref: a reopen and the
        # PROVISIONAL statuses it derives are one act, and answering the act
        # has to reach all of them.
        group = [{**one, "iref": op.get("iref")}
                 for one in (expand(probe, op) if expand else [op])]
        try:
            for one in group:
                _apply(probe, one, store)
        except ApplyError as exc:
            if contested is not None:
                contested.refusal = str(exc)
                for s in _subjects(op):
                    by_record.setdefault(s, contested)
                # **Carried even though it did not apply**, and this is the
                # difference between a seam and a report. A contested `close`
                # cannot be replayed onto an answered question — that is the
                # refusal — but *taking* it is a real choice, and taking it
                # means replaying it behind the reopen the store would demand
                # of anybody. An op nobody can name is an op nobody can
                # choose, so it stays in the file. It is deliberately not
                # applied to the probe: the graph a person has not adjudicated
                # yet is the graph as it stands.
                kept.append(op)
                continue
            named = _subjects(op) & set(by_record)
            if named:
                by_record[sorted(named)[0]].grouped.append(i)
                continue
            rid = next(iter(sorted(_subjects(op))), None)
            f = Finding("inapplicable", store, i, rid, str(exc), op=op)
            findings.append(f)
            for s in _subjects(op):
                by_record.setdefault(s, f)
            continue
        kept.extend(group)
    return findings, probe, kept


def _apply(probe, op: dict, store: str) -> None:
    from dgraph import pending, task_pending
    (pending if store == "decisions" else task_pending)._apply_one(probe, op)


@dataclass
class Report:
    """One arriving contribution, judged whole.

    **Both stores together, and that asymmetry with `dg apply` is deliberate.**
    The two trays are independent on purpose — *a task batch that will not
    apply must never stop a decision batch that would* — because they are one
    writer's two unfinished thoughts. A contribution is somebody else's
    finished one, and half of it is not a smaller version of it: `because` and
    `evidence_for` hold a bare `D`-id in the other file and both cross-store
    link invariants are blocking, so a contribution that moves a link and
    removes its old target is consistent as a whole and inconsistent in either
    half alone. Judging the halves apart produces a refusal for an
    inconsistency the contribution does not have.
    """

    d_ops: list[dict] = field(default_factory=list)
    t_ops: list[dict] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    unexpressible: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    #: Warnings the contribution introduces. **Reported, never acted on.**
    #: `released_by_drop`, `parked_holding_work` and their kind fire in one
    #: integration order and not the other, so a signal that depends on who
    #: integrated first is not a signal to act on — it is a note.
    warnings: list[str] = field(default_factory=list)
    #: Class M's renames: an arriving id that was taken here, given a free one
    #: inside the arriving contribution. Reported, never asked about.
    renamed: list[str] = field(default_factory=list)
    #: What the arriving side touched, whether or not anything was wrong with
    #: it. A clean `dg check` after an integration is not evidence that the
    #: work arrived, so the report says what *was integrated* and not only
    #: what was found wrong.
    touched: list[str] = field(default_factory=list)
    derived: int = 0

    @property
    def contested(self) -> list[Finding]:
        return [f for f in self.findings if f.kind == "contested"]

    @property
    def inapplicable(self) -> list[Finding]:
        return [f for f in self.findings if f.kind == "inapplicable"]

    @property
    def clean(self) -> int:
        """Ops nobody has to look at. Counted by `(store, index)`, never by
        index alone: the two sides number from zero, so `d1` and `t1` would
        otherwise be one op and the count would flatter the contribution."""
        touched = {(f.store, f.at) for f in self.findings}
        touched |= {(f.store, i) for f in self.findings for i in f.grouped}
        return max(self.derived - len(touched), 0)

    @property
    def ok(self) -> bool:
        """Whether this could be adopted with nobody asked anything."""
        return not (self.findings or self.blocking or self.unexpressible)


def plan(ours_g, ours_tg, base_g, base_tg, theirs_g, theirs_tg,
         guard=None, next_free=None) -> Report:
    """Everything `dg integrate` knows before it writes anything.

    Derives both halves, replays each onto its own store collecting rather
    than stopping, and then judges the pair — which is the step that needs
    both halves in hand and is the reason this function takes six graphs.

    `guard` takes **both** proposed graphs and returns what the pair
    introduces, supplied by the caller for the reason `pending.apply_all`
    documents about `also`: this module must not learn what the other store
    means. Both, not one each, because each half has to be judged against what
    the other half will hold — an arriving `add_task T50 --because D50` whose
    `D50` arrives in the same contribution is otherwise refused for an
    inconsistency the contribution does not have.

    **Supplying it is not optional in practice.** The run behind this design
    put an arriving contribution through `apply_all` without the cross guards
    and both link invariants were silent: the dangling reference landed, the
    cycle landed, `dg check` found them afterwards, and the commit gate then
    denied every commit in the repository with a hand-edit as the only exit.
    Cross-store safety is a wiring requirement here, not something replay
    inherits.
    """
    from dgraph import pending

    d = decisions(base_g, theirs_g) if theirs_g is not None else Derived()
    t = tasks(base_tg, theirs_tg) if theirs_tg is not None else Derived()
    rep = Report(unexpressible=d.unexpressible + t.unexpressible,
                 derived=len(d.ops) + len(t.ops))

    # Class M first, before anything is replayed and before any ref is handed
    # out: a renamed op is the op a person will be shown, and renaming after
    # the refs were issued would hand somebody a ref for an id that no longer
    # exists.
    if next_free is not None:
        rep.renamed = rename_collisions(d.ops, t.ops, ours_g, ours_tg,
                                        next_free)

    # Every derived op gets a ref, before anything is replayed. The seam is
    # per op — adopt this one, keep mine — so a person has to be able to name
    # one, and a position cannot serve: the ops that reach the file are the
    # ones that applied, *expanded*, so an index into the derived list names a
    # different op by the time it is read. Two writers' halves are numbered
    # apart for the same reason `Report.clean` counts them apart.
    for side, tag in ((d.ops, "d"), (t.ops, "t")):
        for i, op in enumerate(side):
            op["iref"] = f"{tag}{i}"

    probe_g, probe_tg = ours_g, ours_tg
    if theirs_g is not None:
        found, probe_g, kept = _walk(ours_g, base_g, d.ops, "decisions",
                                     pending.expand)
        rep.findings += found
        rep.d_ops = kept
    if theirs_tg is not None:
        found, probe_tg, kept = _walk(ours_tg, base_tg, t.ops, "tasks", None)
        rep.findings += found
        rep.t_ops = kept

    # The invariants, over the pair, once. Every op that survived the walk is
    # already applied to a probe, so what is left is to ask whether the graphs
    # they produce are ones the store may hold — including the two cross-store
    # rules, which is the half neither store can ask alone.
    was = set()
    for graph in (ours_g, ours_tg):
        if graph is not None:
            was |= {str(p) for p in graph.validate()}
    for graph in (probe_g, probe_tg):
        if graph is None:
            continue
        for p in graph.validate():
            if p.blocking:
                rep.blocking.append(str(p))
            elif str(p) not in was:
                rep.warnings.append(str(p))
    if guard is not None and probe_g is not None and probe_tg is not None:
        for p in guard(probe_g, probe_tg):
            (rep.blocking if p.blocking else rep.warnings).append(str(p))

    rep.touched = sorted({s for op in [*rep.d_ops, *rep.t_ops]
                          for s in _subjects(op)})
    return rep


# ---- quarantine ----------------------------------------------------------
#
# **Not the tray, and this reverses the obvious answer.** Putting arriving ops
# in the tray has one good argument behind it — the tray is gitignored and
# never merges — and that argument holds. The one that does not is residency,
# and it fails on one line of `pending.preview`'s docstring: the tray is *what
# every stage-time guard consults*. An unadjudicated `set_fields D07 -> "B"`
# put there makes `dg node D07` answer "B" in this clone, to a bystander who
# agreed to nothing, and the next thing they compose is composed against it.
#
#     incoming ──adjudicate──▶ tray ──dg apply──▶ store
#     someone else's           yours,            committed
#     not yet accepted         accepted          & validated
#
# The tempting split — clean ops straight to the tray, contested held back —
# breaks on dependency: `add_edge D09 -> D07` is clean and meaningless without
# the contested `add_vertex D09` above it, and staging it alone wedges the tray
# with an op that can never apply, which is `F30` all over again. Dragging the
# transitive closure across is the same problem in a harder form, and the
# stronger reason is semantic: a contribution is atomic, and if the person
# rejects op 1 then op 2 is already sitting in *your* tray as though you had
# chosen it.
#
# The alternative of one tray with `preview` skipping flagged ops reaches the
# same graph and costs three things: a contested flag it needs anyway, a
# transitive skip closure (without which `add_edge D09 -> D07` lands on *your*
# `D09`, valid and silent and wrong), and a `preview` that answers differently
# from `apply` — the bug class this codebase spends its comments preventing.


def path(root=None):
    from dgraph import project
    return (root or project.find().root) / project.INCOMING_NAME


def load_incoming(root=None) -> dict:
    p = path(root)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_incoming(rep: Report, *, source: str, base: str, root=None) -> None:
    """Quarantine a contribution. One file for both halves, always both keys,
    so a reader never has to tell an absent half from an empty one."""
    from dgraph import project
    # The findings travel with the ops. Contested-ness is a **provenance**
    # property — `set_fields D07 -> "B"` is a valid op and nothing about the
    # result reveals the conflict — so it cannot be re-derived later from the
    # file alone, and a reader that could not see it would read a quarantined
    # contribution as merely unapplied.
    body = {"source": source, "base": base,
            "contested": [{"ref": (f.op or {}).get("iref"), "store": f.store,
                           "record": f.record, "message": f.message,
                           "refusal": f.refusal, "op": f.op,
                           "resolution": None}
                          for f in rep.contested],
            "inapplicable": [f.message for f in rep.inapplicable],
            "blocking": list(rep.blocking),
            "unexpressible": list(rep.unexpressible),
            "decisions": rep.d_ops, "tasks": rep.t_ops}
    project.write_atomic(path(root),
                         json.dumps(body, indent=2, ensure_ascii=False) + "\n")


def write_incoming(raw: dict, root=None) -> None:
    """The file back to disk after somebody answered part of it."""
    from dgraph import project
    project.write_atomic(path(root),
                         json.dumps(raw, indent=2, ensure_ascii=False) + "\n")


def clear_incoming(root=None) -> None:
    path(root).unlink(missing_ok=True)


def waiting(root=None) -> int:
    """How many arriving ops are unadjudicated. What the gate keys on.

    A file check rather than a flag on the ops, which also answers the open
    question of where "contested" lives: a second file. The asymmetry the gate
    needs falls straight out of it — one of these is your own unfinished
    thought and the other is somebody else's finished one.
    """
    try:
        raw = load_incoming(root)
    except Exception:
        return 0
    return len(raw.get("decisions", [])) + len(raw.get("tasks", []))


# ---- the seam ------------------------------------------------------------
#
# Eleven of the fifteen ways two writers can disagree are mechanical: there is
# one correct outcome and a machine reaches it. Three are genuinely semantic —
# two agents said different things about the same object and nothing but a
# person knows which is right — and one is invisible to any mechanism at all.
#
# A seam that asks about all fifteen is a seam an orchestrator learns to click
# through, and then the three that mattered go past unread. So the design goal
# is not "surface conflicts to the user"; it is **spend the human's attention
# only on the three, and make each arrive as a question with two candidate
# answers rather than as a merge to resolve.**
#
#   H1  two answers to one question      take theirs · keep mine
#   H2  two completions of one task      take theirs · keep mine
#   H3  two wordings of one record       take theirs · keep mine
#
# H1 is the one that needs a record on the losing side, and it is why the
# `reject` op exists: when a person keeps this store's answer, the arriving
# answer and its falsifier survive only in a branch. Filed as an ordinary
# superseded edge it would read as *we believed this and changed our mind*.
# Without somewhere honest to put it the seam is a choice between losing an
# answer and lying about it.


def answer_one(raw: dict, ref: str, choice: str) -> str:
    """Record how a person answered one contested op. Mutates `raw`.

    Returns a line saying what was settled, or raises `LookupError` naming the
    refs that are open. Nothing is staged here and nothing is dropped: the
    choice is written down and `adopt_ops` below acts on all of them at once,
    so a half-answered seam is never half-applied.
    """
    for f in raw.get("contested", []):
        if f.get("ref") == ref:
            f["resolution"] = choice
            side = "the arriving one" if choice == "take" else "this store's"
            return f"{ref}: keeping {side} — {f.get('message', '')}"
    open_refs = [f.get("ref") for f in raw.get("contested", [])
                 if not f.get("resolution")]
    raise LookupError(
        f"no contested op {ref!r}"
        + (f" — {', '.join(str(r) for r in open_refs)} are open"
           if open_refs else " — nothing is contested"))


def adopt_ops(raw: dict) -> tuple[list[dict], list[dict], list[str]]:
    """`(decision ops, task ops, notes)` for a contribution every conflict of
    which has an answer.

    Built here rather than in the command because the two choices are not
    symmetric edits to a list. *Take* means the arriving op stays and gains
    whatever act makes it legal here — an answer cannot be written over an
    answer, so it needs the reopen the store would demand of anyone. *Keep*
    means the arriving op goes, and where it carried a claim, a record of it
    stays behind.
    """
    kept = {f["ref"]: f for f in raw.get("contested", [])}
    notes: list[str] = []
    out: dict[str, list[dict]] = {"decisions": [], "tasks": []}

    for half in ("decisions", "tasks"):
        for op in raw.get(half, []):
            f = kept.get(op.get("iref"))
            bare = {k: v for k, v in op.items() if k != "iref"}
            if f is not None and f.get("resolution") == "split":
                out["decisions"].extend(_split_ops(bare, f, raw))
                notes.append(
                    f"{f['ref']}: {f['split_id']} opens nothing and rests on "
                    f"nothing — `dg dep {f['split_id']} --after …` once you "
                    f"have decided what it follows from. Its title is the "
                    f"arriving side's wording of the question; `dg amend` it "
                    f"if that is not what was answered.")
                continue
            if f is None or f.get("resolution") == "take":
                if f is not None:
                    out[half].extend(_enabler(bare, raw))
                out[half].append(bare)
                continue
            # Kept ours. The op does not land, and what it asserted survives
            # only if this store has a place for it.
            record = _keepsake(bare, raw)
            if record is not None:
                out["decisions"].append(record)
                notes.append(f"{f['ref']}: the arriving answer is kept as "
                             f"offered-and-not-adopted on {f['record']}")
            elif bare.get("op") == "set_status" and bare.get("outcome"):
                notes.append(
                    f"{f['ref']}: the arriving outcome is not recorded "
                    f"anywhere — {bare['outcome']!r}. There is no place in the "
                    f"task store for a result this work did not produce here; "
                    f"write it down deliberately if it is worth keeping.")
            else:
                notes.append(f"{f['ref']}: the arriving op is dropped")
    return out["decisions"], out["tasks"], notes


def _split_ops(op: dict, f: dict, raw: dict) -> list[dict]:
    """The new question, and the arriving answer on it.

    The title is the arriving side's wording of the original — the closest
    thing on record to the question they were actually answering. Guessing a
    better one is not this function's to do, and `adopt_ops` says so where a
    person will read it.
    """
    vid = f["split_id"]
    return [
        {"op": "add_vertex", "id": vid, "title": f["split_title"],
         "area": f["split_area"], "status": "OPEN"},
        {**{k: v for k, v in op.items() if k != "vertex"},
         "vertex": vid, "to": []},
    ]


def _enabler(op: dict, raw: dict) -> list[dict]:
    """What has to happen first for an arriving op to be legal here.

    Never a special case in the store: both of these are the ordinary route
    the tool already demands of anybody. An answer is not written over an
    answer — you reopen, which keeps the old one as history — and a task is not
    finished twice, you start it again, which keeps the old completion.
    """
    src = raw.get("source", "another writer")
    if op.get("op") == "close":
        return [{"op": "reopen", "vertex": op["vertex"],
                 "why": f"superseded by the answer that arrived from {src}"}]
    if op.get("op") == "set_status" and op.get("status") == "DONE":
        return [{"op": "set_status", "task": op["task"], "status": "DOING"}]
    return []


def _keepsake(op: dict, raw: dict) -> dict | None:
    """The record a declined op leaves behind, where the store has one.

    Only an answer does. A title has no truth value — it is how a question is
    referred to, not something the question says — so a declined retitle
    leaves nothing and needs to leave nothing. A declined outcome is a claim
    and has nowhere to go, which `adopt_ops` says out loud rather than
    quietly dropping.
    """
    if op.get("op") != "close":
        return None
    return {"op": "reject", "vertex": op["vertex"], "answer": op["answer"],
            "source": op["source"], "to": op.get("to", []),
            "falsifier": op.get("falsifier"), "date": op.get("date"),
            "format": op.get("format"),
            "from_source": raw.get("source", "another writer")}


# ---- class M: mechanical, and never asked about ---------------------------
#
# An arriving record whose id this store already holds for something else is
# **not** a question for a person. There is one correct outcome — give the
# arriving record an id that is free here — and a seam that asked would spend
# the attention the three semantic conflicts needed on bookkeeping.
#
# The rename is safe here and nowhere else, and the reason is worth stating as
# policy: it happens inside the *arriving contribution*, where the association
# between an edge and its vertex is still intact. On a flattened file — one
# array, two `D50`s, four bare id strings, nothing saying which edge meant
# which vertex — repair is guesswork. That is why there is no `dg renumber`
# command and never will be.
#
# **Only what this merge introduces.** An id is renamed when the arriving side
# *creates* it and this store already has it; an id both sides share is
# established and is left alone. That distinction is not a nicety: there are
# hundreds of decision-id citations outside any store in a repository of this
# kind, so renaming an established id is not churn, it is breaking every
# sentence that names it.


def rename_collisions(d_ops: list[dict], t_ops: list[dict], ours_g, ours_tg,
                      next_free) -> list[str]:
    """Renumber arriving records whose ids are taken here. Mutates the ops.

    `next_free(prefix, taken)` is the caller's allocator — this module must not
    learn where a clone's id range comes from — and `taken` is every id already
    spoken for, including the ones earlier renames in this same contribution
    have just claimed.
    """
    mapping: dict[str, str] = {}
    claimed = {"D": set(ours_g.vertices) if ours_g else set(),
               "T": set(ours_tg.tasks) if ours_tg else set()}
    notes = []
    for ops, prefix, held in ((d_ops, "D", claimed["D"]),
                              (t_ops, "T", claimed["T"])):
        creates = "add_vertex" if prefix == "D" else "add_task"
        for op in ops:
            rid = op.get("id")
            if op.get("op") != creates or rid not in held:
                continue
            fresh = next_free(prefix, held)
            mapping[rid] = fresh
            held.add(fresh)
            notes.append(f"{rid} → {fresh}  [dim]{rid} is taken here[/]")
    if mapping:
        for ops in (d_ops, t_ops):
            for op in ops:
                _rewrite(op, mapping)
    return notes


#: Every place an id can hide in an op. Spelled out rather than found by
#: walking values, because the two that are easy to forget are the two that
#: fail *silently*: a `BLOCKED:<id>` status is a dependency written into a
#: string, and `because` / `evidence_for` are ids in the **other** store's
#: file — which is where a decision-id collision crosses over and quietly
#: rewrites what a task's premise points at.
_ID_KEYS = ("id", "vertex", "task", "from", "against", "because",
            "evidence_for", "into")


def _rewrite(op: dict, mapping: dict[str, str]) -> None:
    for key in _ID_KEYS:
        if op.get(key) in mapping:
            op[key] = mapping[op[key]]
    if isinstance(op.get("to"), list):
        op["to"] = [mapping.get(t, t) for t in op["to"]]
    status = op.get("status")
    if isinstance(status, str) and status.startswith("BLOCKED:"):
        blocker = status.split(":", 1)[1]
        if blocker in mapping:
            op["status"] = f"BLOCKED:{mapping[blocker]}"


def split_one(raw: dict, ref: str, new_id: str, *, title: str,
              area: str) -> str:
    """Answer one contested op with *these are two questions*. Mutates `raw`.

    H1's third door, and it is load-bearing rather than a convenience. The
    other two answers both assume the two answers are to **one** question —
    take theirs and ours becomes history, keep ours and theirs is recorded as
    declined. Neither is honest when the question was worded loosely enough
    that two people answered different things: taking one would supersede an
    answer nothing contradicted, and declining the other would file a record
    saying this project turned down an answer it never disagreed with.

    So the arriving answer moves to a question of its own, carrying its
    falsifier, its source and its targets. What it does **not** get is an
    edge: attaching it under the original's premises would assert a dependency
    nobody wrote, which is the one claim this model never manufactures — the
    same reason `--splice` and `--into` are gated to a human. It lands as a
    root, `no_orphans` says so as a warning, and `dg dep` is how a person
    attaches it once they have decided what it rests on.
    """
    for f in raw.get("contested", []):
        if f.get("ref") != ref:
            continue
        op = f.get("op") or {}
        if op.get("op") != "close":
            raise ValueError(
                f"{ref} is not two answers to one question — splitting only "
                f"means anything for an answer, and this is "
                f"{op.get('op', 'something else')!r}")
        f["resolution"] = "split"
        f["split_id"] = new_id
        f["split_title"] = title
        f["split_area"] = area
        return (f"{ref}: the arriving answer moves to {new_id}, and this "
                f"store's stands on {op.get('vertex')}")
    raise LookupError(f"no contested op {ref!r}")
