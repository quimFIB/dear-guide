"""Compose a task in an editor: `dg task add --edit`, `dg task done --edit`.

The twin of `dgraph/editor.py`, and a separate module on purpose. Both stores
now have a compose buffer; what they must never have is *one* buffer. A single
renderer that knows both records is a place the two can drift into each other,
and the barrier between them — separate store, separate tray, separate view —
is structural precisely so that it does not depend on anyone remembering.

So the reuse runs one way. This module calls `editor`'s primitives: the buffer
lock and the abort rules (`editor.run`), the header, the `** Field` shaping and
the org comma-escape. `editor` learns nothing about tasks.

What differs is not decoration. A decision's buffer exists because a decision
has fields you have to *argue* — the falsifier above all. A task's exists
because of one field, the outcome: what a piece of work produced is the thing
most worth writing carefully and the most awkward to type between quotes. The
templates below are shaped by that, which is why `Outcome` is alone under Input
in `render_done` while everything else about the task is context beside it.

`dg task drop` deliberately has no buffer. Its prose is one line, and the real
work of dropping is the verdict on each released or orphaned task — an
interactive question the CLI already asks. A buffer there would either
duplicate that in checkboxes or come back unable to finish the command.
"""

from __future__ import annotations

from datetime import date as _date

from dgraph import cross, editor, project
from dgraph.editor import EditorAbort, EditorError  # noqa: F401  (re-exported)
from dgraph.model import Graph
#: `PROSE` is imported rather than restated: the list that decides when an op
#: *claims* org and the list that decides what the store honours it for have to
#: be one list.
from dgraph.task_pending import PROSE  # noqa: F401
from dgraph.tasks import ID_RE, TaskGraph

#: The task store's statuses, for the buffer's `#+TODO:` line. Org colours a
#: keyword it knows; the decision list would colour the wrong words here.
STATUSES = ("TODO", "DOING", "DONE", "DROPPED")


def _header(title: str, **props: str) -> str:
    """`editor._header` with the task store's keywords."""
    return editor._HEADER.format(
        title=title, todo="TODO DOING", done="DONE DROPPED",
        props=editor._props(props),
    )


def next_id(tg: TaskGraph) -> str:
    return f"T{max((int(t[1:]) for t in tg.tasks if t[1:].isdigit()), default=0) + 1:02d}"


def _decision_line(g: Graph | None, did: str | None) -> str:
    """A `D`-id as the other store knows it, or as much as can be said."""
    if not did:
        return "—"
    if g is None or did not in g.vertices:
        return f"{did} (not in this project's decision graph)"
    v = g.vertices[did]
    return f"{did} — {v.title} · {v.status}"


def _task_context(tg: TaskGraph, g: Graph | None, tid: str) -> str:
    """One task and everything around it, as reference material.

    The premise is here rather than merely named, because the question the
    outcome has to answer — did this settle what it was for? — cannot be
    answered from an id.
    """
    t = tg.tasks[tid]
    out = ["* Context", "** This task",
           f"   {tid} — {t.title}",
           f"   area {t.area} · status {t.status}",
           f"   waits on {', '.join(tg.waiting_on(tid)) or '—'} · "
           f"unblocks {', '.join(tg.unblocks(tid)) or '—'}"]
    if t.note:
        out.append(editor._quote(t.note))
    # Read through `cross`, like every other cross-graph reading. This module
    # never touches the link fields itself: assembling what the link says from
    # a task's own attributes is the second implementation of the rule that
    # module exists to be the only one of. A project with no decision store
    # gets no link section, which is the ordinary case rather than an error.
    link = cross.task_link(tg, g, tid) if g is not None else None
    if link and (link["because"] or link["evidence_for"]):
        out.append("** Why this work exists")
        if link["because"]:
            out.append(f"   because   {_decision_line(g, link['because'])}")
        if link["evidence_for"]:
            out.append(f"   evidence for "
                       f"{_decision_line(g, link['evidence_for'])}")
            out.append("   # Finishing this leaves that question waiting for "
                       "the conclusion.")
            out.append("   # Record it with `dg decide`, or say here what it "
                       "showed.")
    prompted = tg.prompted(tid)
    if prompted:
        out.append("** Turned up doing it")
        out += [f"   - {p} — {tg.tasks[p].title}" for p in prompted]
    return "\n".join(out) + "\n"


def render_add(tg: TaskGraph, g: Graph | None, seed: dict | None = None) -> str:
    """The template for a new task.

    Every field the flag path takes, because a buffer that covers half of them
    is one you have to leave half-way through — and the two relations are the
    fields most easily got wrong from memory, which is the case for showing
    the backlog beside them rather than asking the writer to hold it in mind.
    """
    seed = seed or {}
    nxt = next_id(tg)
    ready = [t for t in sorted(tg.tasks) if tg.ready(t)]
    return (
        _header("dg task add — a new task", op="add_task",
                project=str(project.find().root))
        + "\n* Input\n"
        + editor._field("Id", f"Like T07. Next unused: {nxt}",
                        seed.get("id") or nxt)
        + editor._field("Title", "One line: the work to be done.",
                        seed.get("title", ""))
        + editor._field("Area", f"One of: {', '.join(tg.areas)}",
                        seed.get("area", ""))
        + editor._field("After", "Optional. Comma-separated tasks that must be\n"
                                 "resolved before this can start.",
                        ", ".join(seed.get("after", ())))
        + editor._field("Discovered during",
                        "Optional. Comma-separated tasks whose doing turned\n"
                        "this one up. Provenance: it makes nothing wait.",
                        ", ".join(seed.get("discovered_during", ())))
        + editor._field("Because",
                        "Optional. The decision this work exists because of.",
                        seed.get("because", ""))
        + editor._field("Evidence for",
                        "Optional. The decision this work will inform.",
                        seed.get("evidence_for", ""))
        + editor._field("Note", "Optional prose: what this involves, or what "
                                "is unclear about it.", seed.get("note", ""))
        + "\n* Context\n** Areas\n"
        + "".join(f"   - {a}\n" for a in tg.areas)
        + "** Outstanding work\n"
        + ("".join(f"   - {t} {tg.tasks[t].status} — {tg.tasks[t].title}"
                   f"{'' if t not in ready else '   (startable)'}\n"
                   for t in tg.frontier()) or "   (none)\n")
        + _open_decisions(g)
    )


def _open_decisions(g: Graph | None) -> str:
    """The frontier of the *other* store, for `Because` and `Evidence for`.

    Absent, not empty, where there is no decision graph: a project that tracks
    only work is ordinary, and a heading with nothing under it reads as a
    store that exists and is empty.
    """
    if g is None:
        return ""
    return ("** Undecided questions\n"
            + ("".join(f"   - {v} {g.vertices[v].status} — "
                       f"{g.vertices[v].title}\n" for v in g.frontier())
               or "   (none)\n"))


def render_done(tg: TaskGraph, g: Graph | None, tid: str,
                seed: dict | None = None) -> str:
    """The template for finishing a task.

    One field. Everything else the command needs it already knows, and the rest
    of the record is beside it as context — including the premise, because an
    outcome written without the question in view is the one most likely to say
    what was done rather than what it showed.
    """
    seed = seed or {}
    t = tg.tasks[tid]
    waiting = tg.waiting_on(tid)
    return (
        _header(f"dg task done {tid} — {t.title}", op="set_status", task=tid,
                status="DONE",
                project=str(project.find().root),
                date=seed.get("done") or _date.today().isoformat())
        + "\n* Input\n"
        + editor._field(
            "Outcome",
            "What did this produce? A path, a PR, a measurement — enough that\n"
            "somebody who did not do the work can find what it left behind.\n"
            "Full org is fine."
            + (f"\nNote: {tid} still waits on {', '.join(waiting)}."
               if waiting else ""),
            seed.get("outcome", ""))
        + "\n" + _task_context(tg, g, tid)
    )


ALLOWED = {
    "add_task": {"id", "title", "area", "after", "discovered during",
                 "because", "evidence for", "note"},
    "set_status": {"outcome"},
}


def parse(text: str, *, tg: TaskGraph, g: Graph | None,
          expect_kind: str | None = None,
          expect_task: str | None = None) -> list[dict]:
    """Buffer -> task ops ready for `task_pending`. Raises rather than guessing.

    The same contract as `editor.parse`, kept deliberately close to it: only
    the `* Input` subtree is read, an untouched template aborts, and a buffer
    missing a required field raises rather than staging something partial.
    """
    if not text.strip():
        raise EditorAbort("empty buffer — nothing staged")

    meta = editor._meta(text)
    kind = meta.get("op")
    if not kind:
        raise EditorError("no :DGRAPH_OP: in the buffer's properties drawer")
    if expect_kind and kind != expect_kind:
        raise EditorError(f"buffer is a {kind!r} template, expected {expect_kind!r}")
    if expect_task and meta.get("task") != expect_task:
        raise EditorError(
            f"buffer targets {meta.get('task')!r}, not {expect_task!r} — a task "
            f"cannot be retargeted by editing; abort and re-run"
        )

    sections = editor._sections(text)
    unknown = sorted(k for k in sections if k not in ALLOWED.get(kind, set()))
    if unknown:
        names = ", ".join(f"** {sections[u][0]}" for u in unknown)
        raise EditorError(f"unknown field(s) under Input: {names}")
    f = {k: editor._body(v[1]) for k, v in sections.items()}
    if not any(f.values()):
        raise EditorAbort("template came back untouched — nothing staged")

    if kind == "add_task":
        return _parse_add(tg, g, f)
    if kind == "set_status":
        return _parse_done(tg, meta, f)
    raise EditorError(f"cannot compose a {kind!r} task op")


def _need(f: dict[str, str], name: str) -> str:
    val = f.get(name, "").strip()
    if not val:
        raise EditorError(f"{name.capitalize()} is empty — nothing staged")
    return val


def _premise(g: Graph | None, did: str, field: str) -> str:
    """A `D`-id from the buffer, checked against the other store.

    Checked here as well as by the caller's vet, because this message names the
    field as it was typed — `** Because` — where a refusal from the staging
    layer names an op the writer never wrote.
    """
    if g is None:
        raise EditorError(
            f"{field}: {did} names a decision, but this project has no "
            f"{project.STORE_NAME} — `dg init`, or leave the field empty"
        )
    if did not in g.vertices:
        raise EditorError(f"{field}: unknown decision {did!r}")
    return did


def _targets(tg: TaskGraph, tid: str, raw: str, field: str) -> list[str]:
    out = []
    for other in [x.strip() for x in raw.split(",") if x.strip()]:
        if other == tid:
            raise EditorError(f"{field}: {tid} cannot come after itself")
        if other not in tg.tasks:
            raise EditorError(f"{field}: unknown task {other!r}")
        if other not in out:
            out.append(other)
    return out


def _parse_add(tg: TaskGraph, g: Graph | None, f: dict) -> list[dict]:
    tid = _need(f, "id")
    if not ID_RE.fullmatch(tid):
        raise EditorError(f"malformed id {tid!r} — expected something like T07\n"
                          f"decisions are D-ids and live in a different store")
    if tid in tg.tasks:
        raise EditorError(f"{tid} already exists")
    area = _need(f, "area")
    if area not in tg.areas:
        raise EditorError(f"unknown area {area!r} — one of: {', '.join(tg.areas)}")
    op = {"op": "add_task", "id": tid, "title": _need(f, "title"), "area": area}
    if f.get("note", "").strip():
        op["note"] = f["note"].strip()
        op["format"] = "org"
    for field, key in (("Because", "because"), ("Evidence for", "evidence_for")):
        val = f.get(field.lower(), "").strip()
        if val:
            op[key] = _premise(g, val, field)
    ops = [op]
    # One group, in the order the CLI stages them: the task, then its edges.
    # A task landing without them is not a partial batch something refuses —
    # it is a task that reads as startable. Audit F28.
    for field, key, edge in (("After", "after", "precedes"),
                             ("Discovered during", "discovered during",
                              "prompted")):
        for other in _targets(tg, tid, f.get(key, ""), field):
            ops.append({"op": "add_dep", "from": other, "to": [tid],
                        "kind": edge})
    return ops


def _parse_done(tg: TaskGraph, meta: dict, f: dict) -> list[dict]:
    tid = meta.get("task")
    if tid not in tg.tasks:
        raise EditorError(f"unknown task {tid!r}")
    return [{
        "op": "set_status", "task": tid, "status": meta.get("status", "DONE"),
        "outcome": _need(f, "outcome"),
        "done": meta.get("date") or _date.today().isoformat(),
        # Provenance, exactly as `editor._parse_close` records it: this buffer
        # is org, so the views must convert its emphasis rather than read
        # `*HNSW*` as markdown's italic. A task carries one `format` for its
        # whole record — see `PROSE` — so the caller says so when the record
        # already held prose written somewhere else.
        "format": "org",
    }]


def compose_add(tg: TaskGraph, g: Graph | None, seed: dict | None = None,
                launcher=None) -> list[dict]:
    return editor.run(
        render_add(tg, g, seed),
        lambda after: parse(after, tg=tg, g=g, expect_kind="add_task"),
        launcher=launcher)


def compose_done(tg: TaskGraph, g: Graph | None, tid: str,
                 seed: dict | None = None, launcher=None) -> list[dict]:
    return editor.run(
        render_done(tg, g, tid, seed),
        lambda after: parse(after, tg=tg, g=g, expect_kind="set_status",
                            expect_task=tid),
        launcher=launcher)
