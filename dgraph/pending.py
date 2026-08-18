"""The staging area: compose decisions, review them, then apply atomically.

Staged ops live in `.dgraph-pending.json` (gitignored) and do not touch the
store until `apply`. Apply mutates a copy, validates it, and only then writes —
a tool that can corrupt the source of truth is worse than no tool.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import date as _date
from pathlib import Path

from dgraph import project
from dgraph.model import UNSETTLED, Edge, Graph, Vertex

OPS = {"close", "reopen", "add_vertex", "add_edge", "set_status"}


class ApplyError(RuntimeError):
    pass


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


def clear(path: Path | None = None) -> None:
    save([], path)


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
        )
        return

    vid = op.get("vertex") or op.get("from")
    if vid not in g.vertices:
        raise ApplyError(f"unknown vertex {vid!r}")

    if kind == "set_status":
        g.vertices[vid] = replace(g.vertices[vid], status=op["status"])
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
        # A re-decision closes out whatever it replaced.
        summary = op.get("summary")
        if summary:
            for old in g.history(vid):
                if old.replaced_by is None:
                    old.replaced_by = summary
        g.vertices[vid] = replace(g.vertices[vid], status="DECIDED", note=None)
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
            date=e.date, summary=op.get("summary") or (e.answer or "")[:60],
            replaced_by=None, why=op["why"],
        ))
        e.answer = e.falsifier = e.source = e.date = None
        g.vertices[vid] = replace(
            g.vertices[vid], status="REOPENED", note=op.get("note") or op["why"]
        )
        return


def apply_all(g: Graph, ops: list[dict]) -> Graph:
    """Apply to a copy and validate. Raises rather than returning a bad graph."""
    out = copy.deepcopy(g)
    for op in ops:
        _apply_one(out, op)
    problems = [p for p in out.validate() if p.blocking]
    if problems:
        raise ApplyError(
            "would leave the graph invalid:\n  "
            + "\n  ".join(str(p) for p in problems)
        )
    return out
