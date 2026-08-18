"""The staging area: compose decisions, review them, then apply atomically.

Staged ops live in `.dgraph-pending.json` (gitignored) and do not touch the
store until `apply`. Apply mutates a copy, validates it, and only then writes —
a tool that can corrupt the source of truth is worse than no tool.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import replace as _dc_replace
from datetime import date as _date
from pathlib import Path

from dgraph import project
from dgraph.model import SIMPLE_STATUSES, UNSETTLED, Edge, Graph, Vertex
from dgraph.violation import Violation

OPS = {"close", "reopen", "add_vertex", "add_edge", "set_status"}

#: An extra validator over a proposed graph — see `apply_all`.
Checker = Callable[[Graph], list[Violation]]


class ApplyError(RuntimeError):
    pass


def _clip(text: str) -> str:
    """An answer's first line, cut to label length — the fallback for the two
    places a short label is filed when none was given."""
    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    return first if len(first) <= 60 else first[:59] + "…"


# ---- store ---------------------------------------------------------------


def load(path: Path | None = None) -> list[dict]:
    p = path or project.find().pending
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def save(ops: list[dict], path: Path | None = None) -> None:
    p = path or project.find().pending
    if ops:
        p.write_text(json.dumps(ops, indent=2, ensure_ascii=False) + "\n", "utf-8")
    elif p.exists():
        p.unlink()


def stage(op: dict, path: Path | None = None) -> list[dict]:
    ops = load(path)
    ops.append(op)
    save(ops, path)
    return ops


def drop(i: int, path: Path | None = None) -> list[dict]:
    ops = load(path)
    if not 0 <= i < len(ops):
        raise IndexError(f"no staged op {i}")
    ops.pop(i)
    save(ops, path)
    return ops


def replace(i: int, op: dict, path: Path | None = None) -> list[dict]:
    """Swap one staged op for a revised version, in place.

    `dg edit N` composes a replacement rather than dropping and re-staging,
    because re-staging would move the op to the end of the batch and any derived
    `set_status` ops would then apply before the thing they were derived from.
    """
    ops = load(path)
    if not 0 <= i < len(ops):
        raise IndexError(f"no staged op {i}")
    ops[i] = op
    save(ops, path)
    return ops


def clear(path: Path | None = None) -> None:
    save([], path)


def preview(g: Graph, path: Path | None = None, *, skip: int | None = None) -> Graph:
    """The graph as it will stand once the staged ops apply.

    What every stage-time guard consults. A guard that reads the store alone
    judges a graph the user has already moved past: it refuses the chain they
    just staged (`add --after` a staged vertex), accepts a duplicate of it
    (`dg decide` twice), demands a reopen that is already staged, and lets
    `expand` miss a descendant whose decision is staged but not applied.
    `skip` leaves one op out — `dg edit N` revises op N against the graph as
    it stands *without* that op.

    Deliberately not validated: mid-batch the combined state may be
    legitimately transitional (a child closed before its premise, with the
    premise's close still to come). Raises ApplyError only if a staged op
    cannot apply at all — the staging area itself needs attention before
    anything more goes into it.
    """
    out = copy.deepcopy(g)
    for i, op in enumerate(load(path)):
        if i == skip:
            continue
        _apply_one(out, op)
    return out


def vet(g: Graph, op: dict) -> None:
    """Raise ApplyError if `op` could not be staged against `g`.

    The stage-time guard for callers that receive ops as data — the web API,
    chiefly. The CLI's commands each run their own richer checks first; this is
    the shared floor: the op must apply to the effective graph, every target it
    names must exist, and any status it writes must be legal. Validation-level
    rules (propagation, falsifiers) stay with `apply`, where a transitional
    mid-batch state is allowed.
    """
    probe = copy.deepcopy(g)
    try:
        _apply_one(probe, op)
    except KeyError as exc:
        raise ApplyError(f"op is missing required field {exc.args[0]!r}") from None
    unknown = [t for t in (op.get("to") or []) if t not in g.vertices]
    if unknown:
        raise ApplyError(f"unknown target(s): {', '.join(unknown)}")
    status = op.get("status")
    if status is not None:
        base, _, blocker = status.partition(":")
        legal = (bool(blocker) and blocker in g.vertices) if base == "BLOCKED" \
            else (base in SIMPLE_STATUSES and not blocker)
        if not legal:
            raise ApplyError(f"illegal status {status!r}")


# ---- propagation ---------------------------------------------------------


def _settles(op: dict) -> str | None:
    """The vertex this op settles, if any — the trigger for unblocking."""
    if op["op"] == "close":
        return op["vertex"]
    if op["op"] == "set_status" and op["status"].split(":")[0] not in UNSETTLED:
        return op["vertex"]
    return None


def expand(g: Graph, op: dict) -> list[dict]:
    """Derive the ops a change implies.

    Status propagates in both directions, and neither is safe to leave to the
    person typing the command:

    - Reopening a vertex puts every decided descendant on a premise under
      review, so each becomes PROVISIONAL.
    - Settling a vertex releases everything `BLOCKED:` on it. Without this the
      block goes stale, `apply` refuses the whole batch, and the remedy — one
      `set_status` per blocked vertex — is left to be worked out by hand.

    Computing either by hand across the graph is the mistake the validator
    exists to catch, so the tool does it.
    """
    out = [op]
    if op["op"] == "reopen":
        for d in sorted(g.descendants(op["vertex"])):
            if g.vertices[d].base_status == "DECIDED":
                out.append({
                    "op": "set_status", "vertex": d, "status": "PROVISIONAL",
                    "derived_from": op["vertex"],
                })

    settled = _settles(op)
    if settled is not None:
        for vid, v in sorted(g.vertices.items()):
            if v.base_status == "BLOCKED" and v.blocker == settled:
                out.append({
                    "op": "set_status", "vertex": vid, "status": "OPEN",
                    "derived_from": settled,
                })
    return out


# ---- apply ---------------------------------------------------------------


def _apply_one(g: Graph, op: dict) -> None:
    kind = op.get("op")
    if kind not in OPS:
        raise ApplyError(f"unknown op {kind!r}")

    if kind == "add_vertex":
        if op["id"] in g.vertices:
            raise ApplyError(f"{op['id']} already exists")
        g.vertices[op["id"]] = Vertex(
            id=op["id"], title=op["title"], area=op["area"],
            status=op.get("status", "OPEN"), note=op.get("note"),
            # the tag describes the note; without one it describes nothing
            format=op.get("format") if op.get("note") else None,
        )
        return

    vid = op.get("vertex") or op.get("from")
    if vid not in g.vertices:
        raise ApplyError(f"unknown vertex {vid!r}")

    if kind == "set_status":
        g.vertices[vid] = _dc_replace(g.vertices[vid], status=op["status"])
        return

    if kind == "add_edge":
        e = g.active_edge(vid)
        targets = list(op["to"])
        if e is None:
            g.edges.append(Edge(src=vid, to=sorted(set(targets))))
        else:
            e.to = sorted(set(e.to) | set(targets))
        return

    if kind == "close":
        e = g.active_edge(vid)
        targets = sorted(set(op.get("to", [])) | set(e.to if e else []))
        payload = dict(
            answer=op["answer"], falsifier=op.get("falsifier"),
            source=op["source"], date=op.get("date") or _date.today().isoformat(),
            format=op.get("format"),
        )
        if e is None:
            g.edges.append(Edge(src=vid, to=targets, **payload))
        else:
            if e.decided:
                raise ApplyError(
                    f"{vid} already has a decided edge — reopen it first"
                )
            e.to = targets
            for k, v in payload.items():
                setattr(e, k, v)
        # A re-decision closes out whatever it replaced. Without a summary the
        # answer's first line stands in — the same default `reopen` uses for
        # the superseded side. Leaving `replaced_by` empty is worse than a
        # rough label: the reversal record would say "(undecided)" forever
        # about a question that has an answer.
        label = op.get("summary") or _clip(op["answer"])
        for old in g.history(vid):
            if old.replaced_by is None:
                old.replaced_by = label
        g.vertices[vid] = _dc_replace(g.vertices[vid], status="DECIDED",
                                      note=None, format=None)
        return

    if kind == "reopen":
        e = g.active_edge(vid)
        if e is None or not e.decided:
            raise ApplyError(f"{vid} has no decision to reopen")
        # The answer is superseded; the dependency structure is not. The edge
        # keeps its targets and loses only its payload.
        g.edges.append(Edge(
            src=vid, to=list(e.to), active=False,
            answer=e.answer, falsifier=e.falsifier, source=e.source,
            date=e.date, summary=op.get("summary") or _clip(e.answer or ""),
            replaced_by=None, why=op["why"],
            # the superseded record's rendered prose (why, summary) comes from
            # this op; the archived answer/falsifier are never rendered again
            format=op.get("format"),
        ))
        e.answer = e.falsifier = e.source = e.date = None
        e.format = None
        g.vertices[vid] = _dc_replace(
            g.vertices[vid], status="REOPENED",
            note=op.get("note") or op["why"], format=op.get("format"),
        )
        return


def apply_all(g: Graph, ops: list[dict],
              also: Checker | None = None) -> Graph:
    """Apply to a copy and validate. Raises rather than returning a bad graph.

    `also` is an extra validator run over the result, supplied by the caller so
    this module never learns what a second store is. It exists for the
    cross-graph invariants: they are the only ones a decision batch can break
    that `Graph.validate` cannot see, and a batch that breaks them must abort
    here rather than be discovered later by `dg check`, when the store is
    already written and the commit gate is already denying.

    Only *blocking* findings refuse, so demoting a cross-graph check to a
    warning relaxes this guard on its own, with no change in this module.
    """
    out = copy.deepcopy(g)
    for op in ops:
        _apply_one(out, op)
    problems = [p for p in out.validate() if p.blocking]
    problems += [p for p in also(out) if p.blocking] if also else []
    if problems:
        raise ApplyError(
            "would leave the graph invalid:\n  "
            + "\n  ".join(str(p) for p in problems)
        )
    return out
