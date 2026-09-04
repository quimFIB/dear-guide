"""Adopt a store somebody prepared elsewhere, and say what is wrong with it.

`decisions.json` and `tasks.json` are the input format — there has never been an
import step, and a hand-written store works the moment it is in place. What was
missing is the *diagnostics*. `Graph.load` builds dataclasses straight out of
the parsed JSON, so a document with one wrong key reports

    TypeError: Vertex.__init__() got an unexpected keyword argument 'owner'

and a document whose `vertices` is an object rather than a list reports
`string indices must be integers`. Both are true and neither says which record,
which field, or what to write instead — and this is the format a person types by
hand and an agent generates, so it is exactly where a good message pays.

So this module shape-checks the parsed document *before* anything is
constructed, then builds it through `Graph.from_dict` / `TaskGraph.from_dict` —
the same path `load` uses, so a document accepted here is one the store would
have accepted anyway. It never repairs and never guesses: an unrecognised field
is refused, not dropped, because dropping it silently loses whatever the writer
meant by it.

`md_import` is the neighbouring door and a different job: it reads one legacy
markdown dialect and *reconciles* two disagreeing representations of the same
relation. Nothing here reconciles anything.

**`dg export` output is accepted.** Its payload is the store plus blocks the
browser would otherwise recompute — `derived`, `frontier`, and on a scoped
export `ancestors` and `counts`. Those are outputs, not inputs: they are
recognised by name, dropped, and recomputed from the edges. That is the one
exception to refusing what is not in the schema, and it is narrow on purpose —
a *named* derived block is a thing whose meaning is known and reproducible,
which is exactly what an unrecognised field is not. The import says which
blocks it recomputed rather than passing over them in silence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

from dgraph.model import EDGE_FIELDS, Graph
from dgraph.tasks import TaskGraph


class ShapeError(ValueError):
    """The document is not a store of this kind. Carries what to fix."""


class Loaded(NamedTuple):
    """A graph, and what was thrown away to build it.

    `recomputed` names the derived blocks a `dg export` payload carried. It is
    returned rather than printed here — this module has no console — and the
    caller says so, because a document silently losing part of itself is the
    thing the rest of this module exists to prevent.
    """

    graph: Graph | TaskGraph
    recomputed: list[str]


#: Blocks `dg export` and the web app add on top of the store: everything the
#: browser would otherwise compute for itself. Dropped on import and rebuilt
#: from the edges, which is what makes `dg export | dg import` a round trip.
#: Listed rather than pattern-matched, so a new derived block has to be added
#: here deliberately instead of being tolerated by accident.
DERIVED = ("derived", "frontier", "counts", "ancestors")


#: What each store holds, and what a record in it may say. The required fields
#: come first in each tuple; everything after is optional. Kept here rather than
#: read off the dataclasses so that adding a field to `Vertex` is a deliberate
#: decision about the *file format* too, not something an import silently starts
#: accepting.
SCHEMA = {
    "decisions": {
        "collection": "vertices",
        "record": "decision",
        "required": ("id", "title", "area", "status"),
        "optional": ("note", "format", "probes", "binds", "rule"),
        "edge_required": ("from",),
        # The store's own list, so a field added to `Edge` is accepted here
        # the same day. This door still *refuses* a key outside it, and that
        # is the difference from `Graph.load`: a load carries what it cannot
        # read (`Vertex.extra`), because the store is already the record; an
        # import adopts a document somebody prepared, and a key nobody named
        # is the writer's mistake to fix, not the store's to keep.
        "edge_optional": ("to", "active", *EDGE_FIELDS),
    },
    "tasks": {
        "collection": "tasks",
        "record": "task",
        "required": ("id", "title", "area"),
        # `done` and `outcome` are fields of the record again since `D81`.
        # `completions` is deliberately absent: a document written between
        # `F-F5` and `D81` carries it, and `dg task import` refuses rather than
        # folding, because which of several entries was live is a judgement
        # this cannot make for somebody.
        # `stops` and `readings` were missing here for as long as the store
        # has held them, so `dg task import` refused every store with a
        # parked task or a read result — the drift this table's own comment
        # warns of, arriving through the door it was written to guard.
        "optional": ("status", "note", "done", "outcome",
                     "why", "format", "because", "evidence_for", "stops",
                     "readings", "probes", "binds", "done_when"),
        "edge_required": ("from",),
        # `kind` is required by the store and deliberately not checked here:
        # `tasks._edge` refuses an absent one with a message that also names
        # the migration it usually means (a store written before kinds
        # existed). Checking it first would replace that with a worse one.
        "edge_optional": ("to", "kind"),
    },
}


def read(path: Path, kind: str) -> Loaded:
    """One prepared document, checked and built, or `ShapeError` saying why not.

    `kind` is `"decisions"` or `"tasks"`. The caller validates the *invariants*
    afterwards; everything refused here is a document that could not be loaded
    at all, which is a different failure and deserves a different message.

    A `dg export` payload is accepted: its derived blocks are stripped first
    and named in the result, so the round trip works and does not pretend the
    file was already a bare store.
    """
    spec = SCHEMA[kind]
    text = path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        # Line and column, because the file is typically long and generated.
        raise ShapeError(
            f"{path.name} is not valid JSON: {exc.msg} at line {exc.lineno}, "
            f"column {exc.colno}"
        ) from None

    recomputed: list[str] = []
    if isinstance(raw, dict):
        recomputed = [k for k in DERIVED if k in raw]
        raw = {k: v for k, v in raw.items() if k not in DERIVED}

    _check(raw, spec, path.name)
    builder = Graph if kind == "decisions" else TaskGraph
    try:
        return Loaded(builder.from_dict(raw), recomputed)
    except ValueError as exc:
        # The loaders' own refusals — duplicate ids, a task edge with no kind.
        # They are already written for a person, so they are passed through
        # rather than restated less well.
        raise ShapeError(str(exc)) from None


def _check(raw: object, spec: dict, name: str) -> None:
    """Everything that must hold before a record is handed to a dataclass."""
    if not isinstance(raw, dict):
        raise ShapeError(
            f"{name} holds {_kind_of(raw)}, not an object — a store is "
            f'{{"areas": [...], "{spec["collection"]}": [...], "edges": [...]}}'
        )
    coll = spec["collection"]
    if coll not in raw:
        raise ShapeError(
            f'{name} has no "{coll}" key, so it is not a {spec["record"]} '
            f'store. '
            + (f'It has {", ".join(sorted(map(str, raw)))} instead.'
               if raw else "It is empty.")
            + _other_store(raw, coll)
        )
    if not isinstance(raw[coll], list):
        raise ShapeError(
            f'{name}: "{coll}" is {_kind_of(raw[coll])}, and must be a list — '
            f"one object per {spec['record']}, in an array"
        )
    if "areas" in raw and not (isinstance(raw["areas"], list)
                               and all(isinstance(a, str) for a in raw["areas"])):
        raise ShapeError(f'{name}: "areas" must be a list of strings')

    allowed = set(spec["required"]) | set(spec["optional"])
    for i, rec in enumerate(raw[coll]):
        where = _where(spec["record"], i, rec)
        if not isinstance(rec, dict):
            raise ShapeError(f"{name}: {where} is {_kind_of(rec)}, not an object")
        missing = [f for f in spec["required"] if f not in rec]
        if missing:
            raise ShapeError(
                f"{name}: {where} has no {_and(missing)} — every "
                f"{spec['record']} needs {_and(list(spec['required']))}")
        unknown = sorted(set(rec) - allowed)
        if unknown:
            raise ShapeError(
                f"{name}: {where} has {_and(unknown)}, which a {spec['record']} "
                f"does not have. The fields are: "
                f"{', '.join(spec['required'])} (required), "
                f"{', '.join(spec['optional'])}.")


    edges = raw.get("edges", [])
    if not isinstance(edges, list):
        raise ShapeError(f'{name}: "edges" is {_kind_of(edges)}, and must be a list')
    edge_allowed = set(spec["edge_required"]) | set(spec["edge_optional"])
    for i, e in enumerate(edges):
        if not isinstance(e, dict):
            raise ShapeError(f"{name}: edge {i} is {_kind_of(e)}, not an object")
        missing = [f for f in spec["edge_required"] if f not in e]
        if missing:
            raise ShapeError(
                f"{name}: edge {i} has no {_and(missing)} — "
                f'"from" is the id the edge leaves, "to" the list it reaches')
        unknown = sorted(set(e) - edge_allowed)
        if unknown:
            raise ShapeError(
                f"{name}: edge {i} has {_and(unknown)}, which an edge does not "
                f"have. The fields are: {', '.join(sorted(edge_allowed))}.")
        if "to" in e and not isinstance(e["to"], list):
            raise ShapeError(
                f'{name}: edge {i} has "to": {_kind_of(e["to"])}, and it must '
                f"be a list of ids even when there is one")


def _other_store(raw: dict, wanted: str) -> str:
    """The mix-up worth naming: the right file for the other command.

    `dg import` on a `tasks.json` is the likeliest way to reach this message,
    and "no vertices key" is a poor way to be told you typed the wrong verb.
    """
    other = {"vertices": ("tasks", "dg task import"),
             "tasks": ("vertices", "dg import")}[wanted]
    return (f" This looks like the other store — try `{other[1]}`."
            if other[0] in raw else "")


def _where(record: str, i: int, rec: object) -> str:
    """Name the record the reader can find: by its id where it has one."""
    if isinstance(rec, dict) and isinstance(rec.get("id"), str):
        return f"{record} {rec['id']}"
    return f"{record} {i} (no id)" if isinstance(rec, dict) else f"{record} {i}"


def _kind_of(x: object) -> str:
    return {dict: "an object", list: "a list", str: "a string",
            int: "a number", float: "a number", bool: "a boolean",
            type(None): "null"}.get(type(x), type(x).__name__)


def _and(items: list[str]) -> str:
    """`"a"`, `"a" and "b"`, `"a", "b" and "c"` — quoted, because these are
    field names and an unquoted one reads as prose."""
    q = [f'"{i}"' for i in items]
    return q[0] if len(q) == 1 else ", ".join(q[:-1]) + f" and {q[-1]}"
