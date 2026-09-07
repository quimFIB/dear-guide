"""What the tray would change, as a difference against the store.

`dg check --staged` answers whether the graph the tray would produce is sound
(`D70`). This answers what it would *look like*: which records an act adds,
removes or moves, and which edges it draws or cuts — for the browser canvas,
which draws the store by default and previews on request (`D88`). Nothing
here writes; `pending.preview_ops` applies to a copy, and the store and both
trays are exactly as they were afterwards.

Two readers, one grain. The scope is the act: `all` is every op staged, and
an op's id names the act it was staged in — the same set `dg apply --group`
takes and the tray's ✓ applies, because previewing one member of an act
would draw a graph nobody proposed (an edge to a vertex the other half adds).
`select` resolves the scope across both trays for that reason: an act may
span them, and half a preview is the same lie as half an apply.
"""

from __future__ import annotations

from dgraph import pending, task_pending
from dgraph.model import Graph
from dgraph.tasks import TaskGraph

ALL = "all"


def select(ops: list[dict], task_ops: list[dict], scope: str,
           agent: str | None = None) -> tuple[list[dict], list[dict], str]:
    """`(decision ops, task ops, label)` the scope names, or `ValueError`.

    `agent` narrows `all` to one writer's ops — the browser's tray is
    narrowed the same way, and a preview of "everything" under a narrowing
    that shows one proposal would draw the other writers' work unannounced.
    An act is never narrowed: it is one writer's by construction.
    """
    if scope == ALL:
        if agent is not None:
            ops, _ = pending.mine(ops, pending.addressed(agent))
            task_ops, _ = pending.mine(task_ops, pending.addressed(agent))
        n = len(ops) + len(task_ops)
        if not n:
            raise ValueError("nothing staged" + (f" by {agent}" if agent else ""))
        return ops, task_ops, (f"everything staged by {agent}" if agent
                               else "everything staged") + f" ({n} op(s))"
    both = list(ops) + list(task_ops)
    found = next((o for o in both if o.get("ref") == scope), None)
    if found is None:
        raise ValueError(f"nothing staged with id {scope}")
    keep = {o.get("ref") for o in pending.group_of(both, found)}
    d = [o for o in ops if o.get("ref") in keep]
    t = [o for o in task_ops if o.get("ref") in keep]
    n = len(d) + len(t)
    label = (f"act {scope} ({n} ops)" if n > 1
             else f"{found.get('op', '?')} {_subject(found)} ({scope})")
    return d, t, label


def _subject(o: dict) -> str:
    return str(o.get("vertex") or o.get("task") or o.get("id")
               or o.get("from") or "")


def graph_diff(before: Graph, after: Graph) -> dict:
    """Records added, removed, moved to another status or otherwise changed,
    and active edges drawn or cut, between two decision graphs.

    Compared as the store would write them — `Graph.to_dict` is the one
    serialiser, so every field counts, the ones `extra` carries included.
    """
    return _diff(
        {v["id"]: v for v in before.to_dict()["vertices"]},
        {v["id"]: v for v in after.to_dict()["vertices"]},
        {(e.src, t) for e in before.edges if e.active for t in e.to},
        {(e.src, t) for e in after.edges if e.active for t in e.to},
    )


def task_diff(before: TaskGraph, after: TaskGraph) -> dict:
    """`graph_diff`'s twin. Edges carry their kind: a `precedes` is drawn on
    the canvas and a `prompted` is not, and the page has to know which."""
    return _diff(
        {t["id"]: t for t in before.to_dict()["tasks"]},
        {t["id"]: t for t in after.to_dict()["tasks"]},
        {(e.src, t, e.kind) for e in before.edges for t in e.to},
        {(e.src, t, e.kind) for e in after.edges for t in e.to},
    )


def _diff(before: dict[str, dict], after: dict[str, dict],
          edges_before: set, edges_after: set) -> dict:
    common = before.keys() & after.keys()
    status = {k: [before[k].get("status"), after[k].get("status")]
              for k in sorted(common)
              if before[k].get("status") != after[k].get("status")}
    changed = [k for k in sorted(common)
               if k not in status and before[k] != after[k]]
    return {
        "added": sorted(after.keys() - before.keys()),
        "removed": sorted(before.keys() - after.keys()),
        "status": status,
        "changed": changed,
        "edges_added": sorted(list(e) for e in edges_after - edges_before),
        "edges_removed": sorted(list(e) for e in edges_before - edges_after),
    }


def preview(g: Graph | None, tg: TaskGraph | None,
            ops: list[dict], task_ops: list[dict]
            ) -> tuple[Graph | None, TaskGraph | None, dict]:
    """Both stores with their ops applied to copies, and the two diffs.

    `pending.ApplyError` propagates: a tray that will not preview is a fact
    the reader is owed (`D70` calls it a blocking finding), not a graph drawn
    without the op that failed.
    """
    pg = pending.preview_ops(g, ops) if g is not None else None
    ptg = task_pending.preview_ops(tg, task_ops) if tg is not None else None
    diff = {
        "decisions": graph_diff(g, pg) if g is not None else None,
        "tasks": task_diff(tg, ptg) if tg is not None else None,
    }
    return pg, ptg, diff
