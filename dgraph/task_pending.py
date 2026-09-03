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
from datetime import date as _date
from collections.abc import Callable
from pathlib import Path

from dgraph import project, ranges
from dgraph.pending import (FIELDS, ApplyError, already,
                            area_counts, owner, refuse_area,
                            stored_area_counts, vet_fields)
from dgraph.pending import _register, bind_step, probe_entry
from dgraph.model import Graph, bind_fault, probe_fault
from dgraph.tasks import (ID_RE, KINDS, MISSING_EDGE, REMOVAL_MODES, STATUSES,
                          Completion, Reading, Stop, Task, TaskEdge,
                          TaskGraph, _fold_because, matches)
from dgraph.violation import Violation

OPS = {"add_task", "add_dep", "remove_dep", "remove_task", "set_status",
       "set_link", "read_evidence", "set_fields", "reprobe", "bind", "unbind"}

#: An extra validator over a proposed task graph — see `apply_all`.
Checker = Callable[[TaskGraph], list[Violation]]


def path() -> Path:
    return project.find().task_pending


#: The prose a task holds, all of it covered by the record's one `format`.
#: Shared with `task_editor.PROSE`, which decides when an op claims org.
#: Prose whose dialect follows `Task.format`. A stop's `why` is prose
#: too, and converted through the same field — but it is written by the
#: append above rather than by the field loop, so it is not listed here.
PROSE = ("note", "outcome")


def _apply_one(tg: TaskGraph, op: dict) -> None:
    kind = op.get("op")
    if kind not in OPS:
        raise ApplyError(f"unknown task op {kind!r}")

    if kind == "add_task":
        if op["id"] in tg.tasks:
            # The same two readings the decision store tells apart, through the
            # same helper: an id taken by *this* task means another writer
            # applied it and nothing was lost, while an id taken by something
            # else is a clash and re-staging under a fresh id is right. Shared
            # rather than reimplemented, because a rule applied in one store and
            # not its twin is the shape most of this tool's audit findings took.
            raise already(op["id"], matches(tg.tasks[op["id"]], op), "task")
        # The same registration `pending._apply_one` makes, through the same
        # function: an area is registered by the op that first files a record
        # under it, in the store that op writes and only that one.
        _register(tg, op.get("area"))
        tg.tasks[op["id"]] = Task(
            id=op["id"], title=op["title"], area=op["area"],
            status=op.get("status", "TODO"), note=op.get("note"),
            format=op.get("format") if op.get("note") else None,
            because=_fold_because(op.get("because")),
            evidence_for=op.get("evidence_for"),
            # `pending._apply_one`'s twin: the first appended entry.
            probes=[e for e in (probe_entry(op),) if e is not None],
        )
        return

    if kind in ("bind", "unbind"):
        # `pending._apply_one`'s twin, through the one function that knows
        # what the two ops do to a list.
        tid = op["task"]
        if tid not in tg.tasks:
            raise ApplyError(f"unknown task {tid!r}")
        tg.tasks[tid].binds = bind_step(tg.tasks[tid].binds, op)
        return

    if kind == "reprobe":
        # Appended, never assigned — `model.Probe` has the argument. Only on
        # unfinished work: a definition of done written after the work is
        # DONE is the writer certifying its own result one op removed, which
        # is the case D15 excluded `done` to prevent (proposal C3).
        tid = op["task"]
        if tid not in tg.tasks:
            raise ApplyError(f"unknown task {tid!r}")
        t = tg.tasks[tid]
        if not t.unfinished:
            raise ApplyError(
                f"{tid} is {t.status} — a definition of done is written "
                f"before the work is finished; `dg task start {tid}` first")
        entry = probe_entry(op)
        if entry is None:
            raise ApplyError(f"reprobing {tid} needs the criterion: --probe")
        t.probes.append(entry)
        return

    if kind in ("add_dep", "remove_dep"):
        # `kind` is required in the op as it is in the store, and for the same
        # reason: a tray is read by a person running `dg task pending` before
        # it is read by this function, and an op that omits which relation it
        # edits cannot be reviewed. A tray staged before kinds existed fails
        # here, and `preview` turns that into "missing required field 'kind'".
        edge_kind = op["kind"]
        if edge_kind not in KINDS:
            raise ApplyError(
                f"unknown edge kind {edge_kind!r} — one of {', '.join(KINDS)}"
            )
        src = op["from"]
        if src not in tg.tasks:
            raise ApplyError(f"unknown task {src!r}")

        if kind == "add_dep":
            targets = sorted(set(op["to"]))
            # Merged into an edge of the *same* kind. Matching on `src` alone
            # would fold a prompted edge into a precedes one and silently
            # assert an ordering nobody claimed.
            for e in tg.edges:
                if e.src == src and e.kind == edge_kind:
                    e.to = sorted(set(e.to) | set(targets))
                    return
            tg.edges.append(TaskEdge(src=src, to=targets, kind=edge_kind))
            return

        # The undo `add_dep` never had. Without it the only editable structure
        # in the task graph is what was declared when a task was created, and
        # every later correction is a hand-edit of tasks.json.
        targets = set(op["to"])
        hit = [e for e in tg.edges
               if e.src == src and e.kind == edge_kind and set(e.to) & targets]
        if not hit:
            raise ApplyError(
                MISSING_EDGE[edge_kind].format(
                    src=src, other=", ".join(sorted(targets))
                ) + " — nothing to remove"
            )
        for e in hit:
            e.to = sorted(set(e.to) - targets)
        tg.edges = [e for e in tg.edges if e.to]
        return

    if kind == "remove_task":
        # The decision store's `remove_vertex`, with two differences that both
        # come from tasks not being decisions. No edge carries a payload, so
        # nothing here can rewrite an answer and there is no reopen-first
        # refusal. And nothing outside this store names a task — `because` and
        # `evidence_for` point *at* decisions, never back — so a removal has no
        # cross-store fallout to check, which the decision side cannot say.
        tid = op["task"]
        if tid not in tg.tasks:
            raise ApplyError(f"unknown task {tid!r}")
        mode = op.get("mode", "sever")
        if mode not in REMOVAL_MODES:
            raise ApplyError(
                f"unknown removal mode {mode!r} — one of "
                f"{', '.join(REMOVAL_MODES)}"
            )
        into = op.get("into")
        if mode == "into" and (into == tid or into not in tg.tasks):
            raise ApplyError(f"cannot merge {tid} into {into!r}")
        # Reconnected per kind, never across one. Splicing a prerequisite into
        # a provenance edge would assert an ordering nobody claimed, which is
        # the distinction the kinds exist to keep.
        for k in KINDS:
            before, after = tg._in(tid, k), tg._out(tid, k)
            if mode == "splice":
                pairs = [(p, c) for p in before for c in after]
            elif mode == "into":
                pairs = ([(p, into) for p in before]
                         + [(into, c) for c in after])
            else:
                pairs = []
            for src, dst in pairs:
                if src != dst:
                    _apply_one(tg, {"op": "add_dep", "from": src,
                                    "to": [dst], "kind": k})

        for e in tg.edges:
            e.to = [t for t in e.to if t != tid]
        tg.edges = [e for e in tg.edges if e.src != tid and e.to]
        del tg.tasks[tid]
        return

    if kind == "set_link":
        # The emergent case: work turned up a question, so the link is added
        # after the fact — often after the task is already done.
        tid = op["task"]
        if tid not in tg.tasks:
            raise ApplyError(f"unknown task {tid!r}")
        for fld in ("because", "evidence_for"):
            if op.get(fld) is not None:
                val = op[fld]
                setattr(tg.tasks[tid], fld,
                        _fold_because(val) if fld == "because" else val)
        # Cleared through a separate key, because an absent field and a field
        # set to nothing have to stay distinguishable in a stored op.
        for fld in op.get("clear", ()):
            if fld not in ("because", "evidence_for"):
                raise ApplyError(f"cannot clear {fld!r}")
            setattr(tg.tasks[tid], fld, [] if fld == "because" else None)
        return

    if kind == "read_evidence":
        # Appended, never assigned, like a stop: this and `stops` are the two
        # archived records here, and nothing downstream ever clears either.
        tid = op["task"]
        if tid not in tg.tasks:
            raise ApplyError(f"unknown task {tid!r}")
        t = tg.tasks[tid]
        did = op["against"]
        # Whether this task is evidence for *that* question is not asked here:
        # what the link means is `cross`'s to decide and a test enforces it, so
        # the pairing is refused at the door (`dg confirm --against`, which has
        # to see the decision store anyway) and flagged in the store by
        # `task_reading_stale`. What is asked here is only what this module can
        # see: the task exists, it produced something, and the entry is not a
        # second copy of one already recorded.
        if t.unfinished:
            raise ApplyError(
                f"{tid} is {t.status} — there is no result to read against an "
                f"answer yet")
        if not op.get("note"):
            raise ApplyError(
                f"reading {tid} against {did} needs what it showed: --note")
        if any(r.against == did and r.date == op["date"] for r in t.readings):
            # The same refusal a second park gets, for the same reason: two
            # entries would claim two readings where there was one, and this
            # record is kept forever. A reading on a *later* date is a genuine
            # second reading and is allowed.
            raise ApplyError(
                f"{tid} was already read against {did} on {op['date']}")
        t.readings.append(Reading(date=op["date"], note=op["note"],
                                  against=did))
        return

    if kind == "set_fields":
        # `pending._apply_one`'s twin, and audit `F-F6` is what both are for:
        # `title` and `area` were mutable fields with no mutator in either
        # store, so correcting a typo meant editing `tasks.json` by hand.
        #
        # One difference from the decision store, and it is the records
        # differing rather than the rule: a vertex's `format` describes its
        # note and is dropped with it, while a task's covers its whole record —
        # the note *and* every outcome — so emptying the note here leaves the
        # dialect alone. `task_render` converts both through the one field.
        tid = op["task"]
        if tid not in tg.tasks:
            raise ApplyError(f"unknown task {tid!r}")
        t = tg.tasks[tid]
        _register(tg, op.get("area"))
        for fld in FIELDS:
            if fld in op:
                setattr(t, fld, op[fld])
        return

    if kind == "set_status":
        tid = op["task"]
        if tid not in tg.tasks:
            raise ApplyError(f"unknown task {tid!r}")
        t = tg.tasks[tid]
        was = t.status
        t.status = op["status"]
        if t.status in ("PARKED", "DROPPED"):
            # One append for both, because a park and a drop record the same
            # fact. Appended, never assigned: this is the store's only archived
            # record, and nothing downstream ever clears it.
            #
            # Stopping work that is already stopped is refused rather than
            # merged or appended. Appending would claim two stoppages where
            # there was one; merging would edit a record that is kept forever.
            # The caller wanted to amend a reason, and this op cannot tell that
            # from a genuine second stoppage.
            if was == t.status:
                verb = "parked" if t.status == "PARKED" else "dropped"
                raise ApplyError(
                    f"{tid} is already {t.status} — `dg task start {tid}` "
                    f"first, or the record would claim it was {verb} twice")
            if not op.get("why"):
                raise ApplyError(
                    f"{'parking' if t.parked else 'dropping'} {tid} needs a "
                    f"reason: --why")
            t.stops.append(Stop(why=op["why"], date=op["date"]))
        wrote_prose = False
        if t.status == "DONE":
            # The same shape as the stop above, and refused for the same
            # reason. Finishing work that is already finished cannot be told
            # from amending the outcome of the one completion there was, and
            # this record is kept forever — but unlike a stop the way forward
            # is a real one, so the refusal names it rather than only saying no.
            if was == "DONE":
                raise ApplyError(
                    f"{tid} is already DONE ({t.done}, {t.outcome!r}) — "
                    f"`dg task start {tid}` first, then finish it again to "
                    f"record a second completion; both are kept, and the "
                    f"later one is the live result")
            # An op-shape rule, beside `--why` above and `--note` on a reading,
            # not the record-completeness rule `validate` keeps: without an
            # outcome there is nothing to append, and no later op in the batch
            # can supply one now that a second `set_status DONE` is refused.
            if not op.get("outcome"):
                raise ApplyError(
                    f"finishing {tid} needs what it produced: --outcome")
            t.completions.append(Completion(date=op["done"],
                                            outcome=op["outcome"]))
            wrote_prose = True
        # Neither `done` nor `outcome` is among these, and `why` is not either:
        # all three travel in the op under those names and go to an appended
        # record above. There is no live copy of any of them left to fall out
        # of step with the status.
        for fld in ("note",):
            if op.get(fld) is not None:
                setattr(t, fld, op[fld])
                wrote_prose = wrote_prose or fld in PROSE
        # A task has one `format` for its whole record — `task_render` converts
        # its note, its outcome and its `why` through the same field — so the
        # dialect follows any prose the op writes, not the note alone. It used
        # to follow the note alone, which meant an outcome composed in org (the
        # only door that produces org) was stored as org and rendered as
        # markdown: `*HNSW*` in, italic out, silently.
        if op.get("format") is not None and wrote_prose:
            t.format = op["format"]
        return


#: What each relation spec is called, reads as, and refuses to do to itself.
#: One table, because the two kinds differ only in these three strings and a
#: second copy is how one of them came to be spelled differently in an error.
REL = {
    "precedes": {"flag": "--after", "reads": "after",
                 "self": "cannot come after itself"},
    "prompted": {"flag": "--discovered-during", "reads": "discovered during",
                 "self": "cannot be discovered during itself"},
}


def held(tg: TaskGraph, tid: str, kind: str) -> list[str]:
    """What already relates to `tid` this way — the reverse of the stored edge."""
    return (tg.prerequisites(tid) if kind == "precedes"
            else tg.discovered_during(tid))


def relation_ops(tg: TaskGraph, tid: str, others: list[str],
                 kind: str) -> tuple[list[dict], list[str], list[str]]:
    """The ops for one kind of edge, and what was fresh versus already held.

    Computing the ops is split from staging and from saying so, so that a
    command taking two relation specs can build both groups before writing
    either — otherwise the tray holds half of what was asked for.
    """
    already_held = [o for o in others if o in held(tg, tid, kind)]
    fresh = [o for o in others if o not in already_held]
    return ([{"op": "add_dep", "from": o, "to": [tid], "kind": kind}
             for o in fresh], fresh, already_held)


def check_relation(tg: TaskGraph, tid: str, others: list[str],
                   kind: str) -> None:
    """Refuse a relation spec that names nothing, or names `tid` itself."""
    unknown = [o for o in others if o not in tg.tasks]
    if unknown:
        raise ApplyError(f"unknown task(s): {', '.join(unknown)}")
    if tid in others:
        raise ApplyError(f"{tid} {REL[kind]['self']}")


def _require(tg: TaskGraph, tid: str) -> None:
    if tid not in tg.tasks:
        raise ApplyError(f"unknown task {tid}")


def compose_dep(tg: TaskGraph, *, tid: str, after: list[str] | None = None,
                discovered_during: list[str] | None = None
                ) -> tuple[list[dict], list[tuple[str, list[str], list[str]]]]:
    """`(ops, said)` for relating `tid` to other work.

    Two relations, and they make different claims. `precedes` is a
    prerequisite: it makes this task wait. `prompted` is provenance: it records
    which work turned this one up, makes it wait on nothing, and frequently
    runs the other way from the ordering — a cleanup noticed mid-task usually
    has to land *before* that task can be finished.

    Both kinds come back in one op list, because `--after X
    --discovered-during Y` is one statement about how this task relates to the
    others and half of it is a different statement.

    `said` is `(kind, fresh, already)` per relation, for a surface to report.
    """
    after = list(after or [])
    discovered_during = list(discovered_during or [])
    _require(tg, tid)
    if not after and not discovered_during:
        raise ApplyError("nothing to record")
    rels = [(others, kind) for others, kind
            in ((after, "precedes"), (discovered_during, "prompted")) if others]
    # Every spec checked before any op is built, so a bad second one cannot
    # leave the first one's edges staged alone.
    for others, kind in rels:
        check_relation(tg, tid, others, kind)
    ops, said = [], []
    for others, kind in rels:
        more, fresh, already = relation_ops(tg, tid, others, kind)
        ops += more
        said.append((kind, fresh, already))
    return ops, said


def compose_undep(tg: TaskGraph, *, tid: str, after: list[str] | None = None,
                  discovered_during: list[str] | None = None
                  ) -> tuple[list[dict], list[tuple[str, list[str]]]]:
    """`(ops, said)` for removing relations. Releases `tid` if it waited only
    on what is going.

    Naming the kind is required rather than inferred from the pair, because
    both kinds can hold between the same two tasks: guessing would delete the
    ordering when the correction was to the provenance.
    """
    after = list(after or [])
    discovered_during = list(discovered_during or [])
    _require(tg, tid)
    if not after and not discovered_during:
        raise ApplyError("nothing to remove")
    ops, said = [], []
    for others, kind in ((after, "precedes"),
                         (discovered_during, "prompted")):
        if not others:
            continue
        have = held(tg, tid, kind)
        unknown = [o for o in others if o not in have]
        if unknown:
            raise ApplyError(
                MISSING_EDGE[kind].format(src=", ".join(unknown), other=tid)
                + f"\n`dg task node {tid}` lists both relations")
        ops += [{"op": "remove_dep", "from": o, "to": [tid], "kind": kind}
                for o in others]
        said.append((kind, others))
    return ops, said


def compose_link(tg: TaskGraph, g: Graph | None, *, tid: str,
                 because: list[str] | None = None,
                 evidence_for: str | None = None) -> list[dict]:
    """The op pointing `tid` at a decision.

    The emergent case: work turns up a question nobody had written down, so the
    decision is recorded and then the work says which question it raised —
    after the fact, and often after the task is already done.
    """
    _require(tg, tid)
    because = list(because) if because else []
    if not because and not evidence_for:
        raise ApplyError("nothing to link — give a because or an evidence-for")
    # Every premise and the evidence target, checked before any op is built, so
    # a bad second one cannot leave the first staged alone.
    for did in because:
        _premise_exists(g, did, "--because")
    if evidence_for:
        _premise_exists(g, evidence_for, "--evidence-for")
    op = {"op": "set_link", "task": tid}
    # A task rests on a set of premises, so linking appends. Adding never loses
    # a premise, so there is no overwrite to refuse — only duplicates to avoid
    # by keeping the set a set.
    if because:
        current = list(getattr(tg.tasks[tid], "because"))
        for did in because:
            if did not in current:
                current.append(did)
        op["because"] = current
    if evidence_for:
        # `evidence_for` is the one decision this work informs — a single slot,
        # by design. It is not an override site: silently re-pointing it is how
        # evidence walks away from a question nobody closed. Refuse a link onto
        # a different decision and say the correction, rather than letting the
        # second link erase the first.
        #
        # **Why one slot, when `because` above holds a set.** The two fields
        # are not the same shape and the asymmetry is the design, not an
        # oversight. A task rests on however many premises hold it up, and each
        # of them gates it: adding one duplicates nothing. But a task informs a
        # question by *producing a sentence* — its outcome — and one sentence
        # answers one question.
        #
        # So the test that separates the two cases is: **does one outcome
        # sentence answer both questions?** One benchmarking effort can bear on
        # "pick an LLM" and "pick a server to host it", and the honest record
        # there is two tasks — "Benchmark LLM" and "Benchmark servers to host
        # the LLM" — because they vary different things, finish at different
        # times, and end in different sentences. If it takes two sentences,
        # they were always two pieces of work and the field was never the
        # problem. If one sentence really does answer both, the work should not
        # be split at all, and the reading belongs against one question with the
        # other left open — see *Maybe later: one measurement read against a
        # second question*, which is that case written down.
        #
        # **The counter, which is real and is not softened.** Under the split, a
        # later reader sees two results and cannot tell they came from one
        # measurement. That is a genuine loss: two outcomes transcribed from one
        # run are the same fact stored twice, in the arrangement where the
        # copies can disagree, which is what `Stop`'s docstring gives as the
        # reason that record was folded rather than duplicated.
        #
        # **What would reopen it:** finding yourself writing two tasks whose
        # outcomes transcribe one run — worth counting when it happens rather
        # than predicting. The model is most of the way to the alternative
        # already: `Reading.against` is a free `D`-id and `read_evidence`
        # deliberately declines to check it against `evidence_for`, so a result
        # could be read against a second question without the link moving. Two
        # enforcement points stand in the way, and `task_reading_stale` would
        # have to tell "read against a question this work never informed" apart
        # from "read against one the link has since moved off".
        current = getattr(tg.tasks[tid], "evidence_for")
        if current and current != evidence_for:
            raise ApplyError(
                f"{tid} informs {current}, not {evidence_for}\n"
                f"a task informs one decision, so link it across "
                f"(unlink {current}, then link {evidence_for}):\n"
                f"  dg task unlink {tid} --evidence-for\n"
                f"  dg task link {tid} --evidence-for {evidence_for}\n"
                f"if it informs both, that is two tasks — one outcome sentence "
                f"answers one question:\n"
                f"  dg task add -t '…' --evidence-for {evidence_for}")
        op["evidence_for"] = evidence_for
    return [op]


def _premise_exists(g: Graph | None, did: str, flag: str) -> None:
    """A decision id must name an actual decision. Shared by link's two sides."""
    if g is None:
        raise ApplyError(
            f"{flag} {did} names a decision, but this project has no decision "
            f"graph\n`dg init` starts one")
    if did not in g.vertices:
        raise ApplyError(f"unknown decision {did}\n"
                         f"`dg show` lists what is on the frontier")


def compose_unlink(tg: TaskGraph, *, tid: str,
                   because: list[str] | None = None,
                   evidence_for: bool = False) -> tuple[list[dict], list[str]]:
    """`(ops, was)` for severing `tid`'s link to a decision.

    The undo `link` never had. A link recorded against the wrong decision, or
    one that stopped being true, is a correction the tool has to be able to
    make — hand-editing the store is the failure this exists to prevent.

    `--because` names which premises to remove: a task rests on several, so
    which ones go has to be said, and it takes a set for the reason `link` does
    — `--because D01,D05` in either direction is one act. `evidence_for` is a
    single slot, so a bare flag removes it all.
    """
    _require(tg, tid)
    because = list(because) if because else []
    if not because and not evidence_for:
        raise ApplyError("nothing to unlink — give a because or an evidence-for")
    ops, was = [], []
    if evidence_for:
        had = getattr(tg.tasks[tid], "evidence_for")
        if had is None:
            raise ApplyError(f"{tid} has no --evidence-for to remove")
        ops.append({"op": "set_link", "task": tid, "clear": ["evidence_for"]})
        was.append(had)
    if because:
        current = list(getattr(tg.tasks[tid], "because"))
        # Every id checked before any is removed, so a batch naming one premise
        # it does not hold cannot half-apply — the same reason `compose_link`
        # validates all of them before building an op.
        missing = [did for did in because if did not in current]
        if missing:
            raise ApplyError(
                f"{tid} has no --because {', '.join(missing)} to remove — its "
                f"premises are {', '.join(current) or 'none'}")
        for did in because:
            current.remove(did)
        ops.append({"op": "set_link", "task": tid, "because": current})
        was += because
    return ops, was


def preview_ops(tg: TaskGraph, ops: list[dict]) -> TaskGraph:
    """`tg` with `ops` applied to a copy. Neither the store nor the tray moves."""
    out = copy.deepcopy(tg)
    for op in ops:
        _apply_one(out, op)
    return out


def introduced(tg: TaskGraph, ops: list[dict]) -> list[Violation]:
    """The findings `ops` would **add**. `pending.introduced`'s twin."""
    before = {(v.check, v.message) for v in tg.validate()}
    try:
        after = preview_ops(tg, ops)
    except ApplyError:
        return []
    return sorted((v for v in after.validate()
                   if (v.check, v.message) not in before),
                  key=lambda v: (v.check, v.message))


def releases(tg: TaskGraph, g: Graph | None, ops: list[dict]) -> list[str]:
    """The tasks that become startable if `ops` apply, and are not now.

    What a *removal* sets loose. `dg task undep`'s help promises it "releases
    this task if it waited only on that", and `unlink --because` drops a gate
    that may have been the only thing holding work back — both are statements
    a person should be able to read before deciding, not after. Computed
    against the cross-graph readiness, because a task whose premise is
    unsettled is not startable however clear its own prerequisites are.
    """
    from dgraph import cross

    def ready(graph: TaskGraph) -> set[str]:
        return {t for t in graph.tasks
                if (cross.ready(graph, g, t) if g is not None
                    else graph.ready(t))}

    try:
        after = preview_ops(tg, ops)
    except ApplyError:
        return []
    return sorted(ready(after) - ready(tg))


def compose_add(tg: TaskGraph, g: Graph | None, *, tid: str, title: str,
                area: str, new_area: bool = False,
                after: list[str] | None = None,
                discovered_during: list[str] | None = None,
                because: list[str] | None = None,
                evidence_for: str | None = None,
                note: str | None = None,
                probe: dict | None = None,
                stored: TaskGraph | None = None) -> list[dict]:
    """The op list that records a new task, validated against `tg`.

    `pending.compose_add`'s twin, and deliberately the same shape: the store's
    staging module owns the rules for what may enter its tray, and both the CLI
    and the browser reach them through one function rather than each keeping a
    copy. What that buys here is larger than on the decision side, because a
    task is born with more structure — two kinds of edge and two fields that
    cross into the other store — and every one of them is a place two doors
    could differ.

    **The task and its edges are one op list**, for the reason audit F28 gave:
    a task that lands without its prerequisites does not read as a partial
    batch something will refuse, it reads as *startable*.

    **Both relation specs are checked before any op is built**, so a typo in
    the second does not leave the new task staged alone.

    `g` is the *effective* decision graph, or None where this project tracks
    only work. It is consulted for one thing — that a `because` or an
    `evidence_for` names a decision that exists — and resolved against the tray
    so that a decision and the work it implies can be recorded in one batch.
    The converse never holds: nothing in the decision store consults this one.
    """
    after = list(after or [])
    discovered_during = list(discovered_during or [])
    blank = [name for name, val in (("id", tid), ("title", title),
                                    ("area", area)) if not (val or "").strip()]
    if blank:
        raise ApplyError(f"a task needs {', '.join(blank)}")
    if not ID_RE.fullmatch(tid):
        raise ApplyError(f"malformed id {tid!r} — expected something like T07\n"
                         f"decisions are D-ids and live in a different store")
    if tid in tg.tasks:
        raise ApplyError(f"{tid} already exists"
                         + (" in the staging area"
                            if stored is not None and tid not in stored.tasks
                            else ""))
    why = refuse_area(area, own=area_counts(tg.areas, tg.tasks.values()),
                      other=stored_area_counts(project.find().store),
                      owner=owner(), new_area=new_area)
    if why is not None:
        raise ApplyError(why)
    # `pending.compose_add`'s twin — see there. The area is checked in both the
    # composer and `vet` for the same reason the grant is: there is no longer
    # an invariant behind it to catch what slips through, because a store whose
    # records use an area its list does not mention is now legal.
    bad = ranges.fault("T", tid)
    if bad:
        raise ApplyError(bad)
    for did in because or ():
        if g is None:
            raise ApplyError(
                f"--because {did} names a decision, but this project has no "
                f"decision graph\n`dg init` starts one, or drop --because")
        if did not in g.vertices:
            raise ApplyError(f"unknown decision {did}\n"
                             f"`dg show` lists what is on the frontier")
    if evidence_for:
        if g is None:
            raise ApplyError(
                f"--evidence-for {evidence_for} names a decision, but this "
                f"project has no decision graph\n`dg init` starts one, or drop "
                f"--evidence-for")
        if evidence_for not in g.vertices:
            raise ApplyError(f"unknown decision {evidence_for}\n"
                             f"`dg show` lists what is on the frontier")
    rels = [(others, kind) for others, kind
            in ((after, "precedes"), (discovered_during, "prompted")) if others]
    for others, kind in rels:
        check_relation(tg, tid, others, kind)

    if probe is not None:
        fault = probe_fault(probe)
        if fault:
            raise ApplyError(f"--probe: {fault}")
    op = {"op": "add_task", "id": tid, "title": title, "area": area}
    if note:
        op["note"] = note
    if because:
        op["because"] = list(because)
    if evidence_for:
        op["evidence_for"] = evidence_for
    if probe is not None:
        op["probe"] = probe
        op["date"] = _date.today().isoformat()
    ops = [op]
    for others, kind in rels:
        ops += relation_ops(tg, tid, others, kind)[0]
    return ops


def compose_bind(tg: TaskGraph, *, tid: str, binds: list[dict],
                 remove: bool = False) -> tuple[dict | None, list[str], list[str]]:
    """`pending.compose_bind`'s twin — see there."""
    if tid not in tg.tasks:
        raise ApplyError(f"unknown task {tid}")
    for b in binds:
        fault = bind_fault(b)
        if fault:
            raise ApplyError(fault)
    held = {b.spelled for b in tg.tasks[tid].binds}
    spelled = [f"{b['kind']}:{b['ref']}" for b in binds]
    if remove:
        fresh = [s for s in spelled if s in held]
        already = [s for s in spelled if s not in held]
    else:
        fresh = [s for s in spelled if s not in held]
        already = [s for s in spelled if s in held]
    if not fresh:
        return None, fresh, already
    op = {"op": "unbind" if remove else "bind", "task": tid,
          "binds": [b for b, s in zip(binds, spelled) if s in fresh]}
    return op, fresh, already


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


def vet(tg: TaskGraph, op: dict, *, new_area: bool = False) -> None:
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
    # `pending.vet`'s twin — see there. The `D` grant and the `T` grant are one
    # grant, so a clone that refuses an out-of-range decision refuses an
    # out-of-range task by the same rule and the same function.
    if op.get("op") == "add_task":
        bad = ranges.fault("T", str(op.get("id") or ""))
        if bad:
            raise ApplyError(bad)
    status = op.get("status")
    if status is not None and status not in STATUSES:
        raise ApplyError(f"illegal status {status!r} — one of {', '.join(STATUSES)}")
    # `pending.vet`'s twin: the shape is refused at the door, in one sentence,
    # rather than by `apply` against a batch somebody else may share by then.
    if op.get("op") in ("add_task", "reprobe") and op.get("probe") is not None:
        fault = probe_fault(op["probe"])
        if fault:
            raise ApplyError(f"probe: {fault}")
    if op.get("op") in ("bind", "unbind"):
        for b in op.get("binds") or ():
            fault = bind_fault(b)
            if fault:
                raise ApplyError(f"bind: {fault}")
    if op.get("op") == "set_fields":
        t = tg.tasks[op["task"]]
        vet_fields(op, own=area_counts(tg.areas, tg.tasks.values()),
                   other=stored_area_counts(project.find().store),
                   record="task", new_area=new_area,
                   current={k: getattr(t, k) for k in FIELDS})
    # `outcome` is not among the fields that exempt an op from this: it used to
    # be, and that is half of how a second `dg task done` reached the store —
    # the op was let past by virtue of carrying the very field it was about to
    # destroy. A repeat DONE is now refused by `_apply_one` above, before this
    # line, and an outcome on a status that is not DONE is not a reason to
    # accept a no-op either.
    if (op.get("op") == "set_status" and status == tg.tasks[op["task"]].status
            and not any(op.get(f) for f in ("note", "why"))):
        # A no-op that reads like progress. Refused with the current status
        # named, since the caller is often an agent that has lost track.
        raise ApplyError(f"{op['task']} is already {status}")


def vet_all(tg: TaskGraph, ops: list[dict], *,
            new_area: bool = False) -> None:
    """Raise if these ops could not be staged **as a group**.

    The plural of `vet`, and the shape every group-building task command needs:
    each op is vetted against the graph the ones before it produce, never
    against the unchanged one. A group routinely builds on itself — `dg task
    add --after X` stages the task and then an edge *to* it — and vetting the
    edge against a graph without the task would refuse a group the first half
    makes legal.

    Nothing is written here. It exists so that a command can check a whole group
    before staging any of it, which is what lets the staging be a single write:
    see `pending.stage_all`, and audit F28 for what one-op-at-a-time cost.
    """
    probe = copy.deepcopy(tg)
    for op in ops:
        vet(probe, op, new_area=new_area)
        _apply_one(probe, op)


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
