"""The invariant behind every contest rule, checked without naming a rule.

`integrate._contest_*` spells its cases out one op kind at a time, and
`CANNOT_CONFLICT` holds a prose reason for every kind without a rule. A test
counts the two lists together (`test_every_op_kind_is_contestable_or_argued_
not_to_be`) and proves nothing about whether any entry is *true* — which is
how four of twelve reasons were false at once, each found by hand, each after
a judgement had been lost under a report printing "nothing contested". `D50`.

So this is the other kind of test. One invariant, over stores nobody wrote by
hand, and **the op kind is never consulted**:

    if this clone changed something about a record since the base, and
    replaying the arriving contribution would overwrite that change,
    the walk reports the record.

"Overwrite" is read per aspect of the record and by the aspect's shape, which
is the one place a judgement is made here and it is not about ops: a scalar
(a title, a status, a link slot) is overwritten when the probe holds a
different value from this clone's; a set (targets, premises, binds) when
something this clone added is gone or something it removed is back; an
append-only record (probes, stops, readings, history) when an
entry this clone appended is missing. Two writers editing *different* aspects
of one record is two facts landing, and asking a person about it would be
asking a question with one answer — which is why the property is about loss
and not about touch.

The other half is checked too, because a rule that reports everything passes
the first half trivially: a contested finding names a record this clone moved
since the base, one it added, or one that a change here names — the record a
fresh edge here points at is where a removal of it is reported.

A generator rather than a case list, deliberately. A case per kind is a fresh
chance to write the assertion that misses — the way all four were missed —
and a reason can go from false to true without being touched (`add_edge` did,
when `D57` moved targets out of the answer), so a hand-written scenario would
now carry a case that passes for a reason nobody updated. What this test costs
is that a failure names a seed rather than a case; the message carries the
record, the aspect, the three values and the kinds that arrived for it, which
is enough to write the case afterwards.

**Its own falsifier**, from `D50`: needing a per-kind case list to stay green.
The day this file gains `if kind == …`, it has become the enumeration it was
written to replace.
"""

import copy
import json
import random

import pytest

from dgraph import integrate, pending, task_pending
from dgraph.model import CLAIM
from dgraph.pending import ApplyError

SEEDS = range(300)
#: Ops per side. Enough that the two sides collide on a record most runs.
STEPS = 7

WORDS = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]
DATES = ["2026-02-01", "2026-02-02", "2026-02-03"]
AREAS = ["Alpha", "Beta", "Gamma"]


# ---- the generator ---------------------------------------------------------
#
# Random ops with valid *shapes*; whether one is legal against the graph it
# lands on is the store's call, and a refusal just means that step is skipped.
# Targets always point at a higher id than their source so the random graph
# stays a DAG — the walk expands a reopen over `descendants`, and a cycle is
# not a shape either store may hold, so it is not a shape worth generating.


def _text(rng):
    return " ".join(rng.sample(WORDS, 2))


def _probe(rng):
    return {"kind": f"prose.{rng.choice(WORDS)}", "args": {"text": _text(rng)}}


def _bind(rng):
    return {"kind": f"prose.{rng.choice(WORDS[:2])}", "ref": rng.choice(WORDS)}


def _held_or_fresh(rng, record, kind):
    """A bind the record holds, for an `unbind` that is to apply at all
    — the fixtures hold none, so a fresh pair would never be one."""
    if kind == "unbind" and record.binds:
        b = rng.choice(record.binds)
        return {"kind": b.kind, "ref": b.ref}
    return _bind(rng)


def _decision_op(rng, g):
    ids = sorted(g.vertices)
    vid = rng.choice(ids)
    above = [i for i in ids if i > vid]
    kind = rng.choice(sorted(pending.OPS))
    if kind == "add_vertex":
        return {"op": kind, "id": f"D{rng.randint(7, 10):02d}",
                "title": _text(rng), "area": rng.choice(AREAS),
                "status": "OPEN",
                **({"note": _text(rng)} if rng.random() < 0.5 else {}),
                **({"rule": _text(rng)} if rng.random() < 0.3 else {})}
    if kind == "set_fields":
        fields = rng.sample(["title", "note", "rule", "area"],
                            rng.randint(1, 2))
        return {"op": kind, "vertex": vid,
                **{f: (rng.choice(AREAS) if f == "area" else _text(rng))
                   for f in fields}}
    if kind == "set_status":
        return {"op": kind, "vertex": vid,
                "status": rng.choice(["OPEN", "PROVISIONAL"])}
    if kind == "add_edge":
        return {"op": kind, "from": vid,
                "to": rng.sample(above, min(len(above), rng.randint(1, 2)))
                if above else []}
    if kind == "remove_edge":
        e = g.active_edge(vid)
        return {"op": kind, "from": vid,
                "to": rng.sample(e.to, 1) if e is not None and e.to else []}
    if kind == "close":
        return {"op": kind, "vertex": vid, "answer": _text(rng),
                "falsifier": _text(rng), "source": rng.choice(WORDS),
                "date": rng.choice(DATES),
                "to": rng.sample(above, 1) if above and rng.random() < 0.5
                else []}
    if kind == "reopen":
        return {"op": kind, "vertex": vid, "why": _text(rng)}
    if kind == "reject":
        return {"op": kind, "vertex": vid, "answer": _text(rng),
                "source": rng.choice(WORDS), "from_source": "worker",
                "date": rng.choice(DATES)}
    if kind == "reprobe":
        return {"op": kind, "vertex": vid, "probe": _probe(rng),
                "date": rng.choice(DATES)}
    if kind in ("bind", "unbind"):
        return {"op": kind, "vertex": vid,
                "binds": [_held_or_fresh(rng, g.vertices[vid], kind)]}
    if kind == "remove_vertex":
        return {"op": kind, "vertex": vid,
                "mode": rng.choice(["sever", "splice"])}
    raise AssertionError(f"the generator has no shape for {kind}")


def _task_op(rng, tg):
    ids = sorted(tg.tasks)
    tid = rng.choice(ids)
    above = [i for i in ids if i > tid]
    dids = [f"D{n:02d}" for n in range(1, 7)]
    kind = rng.choice(sorted(task_pending.OPS))
    if kind == "add_task":
        return {"op": kind, "id": f"T{rng.randint(5, 8):02d}",
                "title": _text(rng), "area": rng.choice(AREAS),
                **({"because": rng.sample(dids, 1)} if rng.random() < 0.4
                   else {}),
                **({"evidence_for": rng.choice(dids)} if rng.random() < 0.4
                   else {})}
    if kind == "set_fields":
        fields = rng.sample(["title", "note", "done_when", "area"],
                            rng.randint(1, 2))
        return {"op": kind, "task": tid,
                **{f: (rng.choice(AREAS) if f == "area" else _text(rng))
                   for f in fields}}
    if kind == "set_status":
        status = rng.choice(["TODO", "DOING", "PARKED", "DROPPED", "DONE"])
        op = {"op": kind, "task": tid, "status": status}
        if status in ("PARKED", "DROPPED"):
            op.update(why=_text(rng), date=rng.choice(DATES))
        if status == "DONE":
            op.update(outcome=_text(rng), done=rng.choice(DATES))
        if rng.random() < 0.3:
            op["note"] = _text(rng)
        return op
    if kind == "set_link":
        op = {"op": kind, "task": tid}
        pick = rng.random()
        if pick < 0.4:
            op["because"] = rng.sample(dids, rng.randint(1, 2))
        elif pick < 0.8:
            op["evidence_for"] = rng.choice(dids)
        else:
            op["clear"] = [rng.choice(["because", "evidence_for"])]
        return op
    if kind == "read_evidence":
        return {"op": kind, "task": tid, "against": rng.choice(dids),
                "note": _text(rng), "date": rng.choice(DATES)}
    if kind == "reprobe":
        return {"op": kind, "task": tid, "probe": _probe(rng),
                "date": rng.choice(DATES)}
    if kind in ("bind", "unbind"):
        return {"op": kind, "task": tid,
                "binds": [_held_or_fresh(rng, tg.tasks[tid], kind)]}
    if kind in ("add_dep", "remove_dep"):
        return {"op": kind, "from": tid, "kind": rng.choice(["precedes",
                                                             "prompted"]),
                "to": rng.sample(above, 1) if above else []}
    if kind == "remove_task":
        return {"op": kind, "task": tid, "mode": "sever"}
    raise AssertionError(f"the generator has no shape for {kind}")


def _diverge(rng, graph, store):
    """`graph` after `STEPS` random ops, each applied the way the walk
    applies them — expanded, for the decision store — or skipped."""
    out = copy.deepcopy(graph)
    gen = _decision_op if store == "decisions" else _task_op
    for _ in range(STEPS):
        op = gen(rng, out)
        try:
            for one in (pending.expand(out, op) if store == "decisions"
                        else [op]):
                (pending if store == "decisions"
                 else task_pending)._apply_one(out, one)
        except ApplyError:
            continue
    return out


# ---- what a record holds, by aspect ----------------------------------------
#
# Three shapes, and the shape is the whole of what "overwritten" means:
#   scalar   — the probe holds a different value from this clone's
#   set      — something this clone added is gone, or something it removed
#              is back
#   append   — an entry this clone appended is missing
# Nothing here reads an op.


def _frozen(x):
    return json.dumps(x, sort_keys=True, default=str)


def _vertex_aspects(g, vid):
    v = g.vertices[vid]
    e = g.active_edge(vid)
    return {
        **{f: ("scalar", getattr(v, f))
           for f in ("title", "area", "status", "note", "format", "rule")},
        "probes": ("append", tuple(_frozen((p.kind, p.args, p.date))
                                   for p in v.probes)),
        "binds": ("set", frozenset((b.kind, b.ref) for b in v.binds)),
        "opens": ("set", frozenset(g.children(vid))),
        "answer": ("scalar", tuple(getattr(e, k) for k in CLAIM)
                   if e is not None and e.decided else None),
        "history": ("append", tuple(_frozen((h.answer, h.why, h.date))
                                    for h in g.history(vid))),
        "rejected": ("append", tuple(_frozen((h.answer, h.from_source))
                                     for h in g.rejected(vid))),
    }


def _task_aspects(tg, tid):
    t = tg.tasks[tid]
    return {
        **{f: ("scalar", getattr(t, f))
           for f in ("title", "area", "status", "note", "format",
                     "done_when", "evidence_for")},
        "because": ("set", frozenset(t.because)),
        "binds": ("set", frozenset((b.kind, b.ref) for b in t.binds)),
        "relations": ("set", frozenset((e.kind, to) for e in tg.edges
                                       if e.src == tid for to in e.to)),
        "probes": ("append", tuple(_frozen((p.kind, p.args, p.date))
                                   for p in t.probes)),
        "outcome": ("scalar", (t.done, t.outcome)),
        "stops": ("append", tuple(_frozen((s.date, s.why))
                                  for s in t.stops)),
        "readings": ("append", tuple(_frozen((r.date, r.against, r.note))
                                     for r in t.readings)),
    }


#: What an aspect holds on a record the base did not have.
EMPTY = {"scalar": None, "set": frozenset(), "append": ()}


def _overwritten(shape, base, ours, probe):
    """What of this clone's change the probe does not hold — `None` where
    it holds all of it. For a set, the elements themselves, so that a
    report naming the element counts: a removal there of a record this
    clone made `D01` open is reported on the record removed, and that is
    the right place for it."""
    if shape == "scalar":
        return [ours] if probe != ours else None
    if shape == "set":
        lost = ((ours - base) - probe) | ((base - ours) & probe)
        return sorted(lost, key=str) or None
    new = ours[len(base):]
    return [x for x in new if x not in probe] or None


def _names(element) -> set:
    """The record ids a set element carries: the element itself, or the
    strings inside a pair like `("precedes", "T04")`."""
    if isinstance(element, str):
        return {element}
    return {x for x in element if isinstance(x, str)}


def _records(graph, store):
    return graph.vertices if store == "decisions" else graph.tasks


def _aspects(graph, rid, store):
    return (_vertex_aspects if store == "decisions" else _task_aspects)(
        graph, rid)


def _check(seed, store, base, ours, theirs):
    derive = integrate.decisions if store == "decisions" else integrate.tasks
    ops = derive(base, theirs).ops
    findings, probe, _ = integrate._walk(
        ours, base, ops, store,
        pending.expand if store == "decisions" else None)

    reported = set()
    for f in findings:
        if f.kind == "contested":
            reported.add(f.record)
            reported |= integrate._subjects(f.op or {})
    arrived = {}
    for op in ops:
        for rid in integrate._subjects(op):
            arrived.setdefault(rid, []).append(op["op"])

    moved = set()      # records this clone changed since the base…
    named = set()      # …and the records those changes name, as elements
    for rid in _records(ours, store):
        here = _aspects(ours, rid, store)
        was = (_aspects(base, rid, store) if rid in _records(base, store)
               else {a: (shape, EMPTY[shape]) for a, (shape, _) in here.items()})
        after = (_aspects(probe, rid, store)
                 if rid in _records(probe, store) else None)
        for aspect, (shape, ours_v) in here.items():
            base_v = was[aspect][1]
            if ours_v == base_v:
                continue
            moved.add(rid)
            if shape == "set":
                for x in (ours_v ^ base_v):
                    named |= _names(x)
            probe_v = EMPTY[shape] if after is None else after[aspect][1]
            lost = _overwritten(shape, base_v, ours_v, probe_v)
            if lost is None or rid in reported:
                continue
            if shape == "set" and all(_names(x) & reported for x in lost):
                continue
            pytest.fail(
                    f"seed {seed}, {store}: {rid}.{aspect} was changed here "
                    f"and the arriving side overwrote it with nothing "
                    f"reported\n  base:  {base_v!r}\n  here:  {ours_v!r}\n"
                    f"  after: {probe_v!r}\n  arriving for {rid}: "
                    f"{arrived.get(rid, [])}"
                    + ("" if after is not None else f"\n  ({rid} is gone)"))

    for f in findings:
        if f.kind != "contested":
            continue
        if (f.record in moved or f.record in named
                or f.record not in _records(base, store)):
            continue
        pytest.fail(
            f"seed {seed}, {store}: {f.record} is reported contested — "
            f"{f.message!r} — and this clone never moved it since the base, "
            f"nor anything that names it")


@pytest.mark.parametrize("seed", SEEDS)
def test_a_change_made_here_and_overwritten_there_is_reported(seed, g, tg):
    rng = random.Random(seed)
    for store, base in (("decisions", g), ("tasks", tg)):
        ours = _diverge(rng, base, store)
        theirs = _diverge(rng, base, store)
        _check(seed, store, base, ours, theirs)


def test_the_generator_reaches_every_op_kind(g, tg):
    """The property is only as wide as what the generator produces, so this
    pins that every kind either store accepts is one it can produce *and
    apply* — a shape that is always refused is a kind the property never
    sees. Every seed above shares this, which is why it is one test."""
    seen = set()
    for seed in range(200):
        rng = random.Random(seed)
        for store, base in (("decisions", g), ("tasks", tg)):
            out = copy.deepcopy(base)
            gen = _decision_op if store == "decisions" else _task_op
            mod = pending if store == "decisions" else task_pending
            for _ in range(STEPS):
                op = gen(rng, out)
                try:
                    for one in (pending.expand(out, op)
                                if store == "decisions" else [op]):
                        mod._apply_one(out, one)
                except ApplyError:
                    continue
                seen.add(op["op"])
    missing = (pending.OPS | task_pending.OPS) - seen
    assert not missing, f"never applied by the generator: {sorted(missing)}"
