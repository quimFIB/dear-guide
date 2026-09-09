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

from dgraph import areas, cross, editor, orgmd, pending, project, ranges
from dgraph import tags as _tags
from dgraph.editor import EditorAbort, EditorError
from dgraph.model import Graph
from dgraph.ids import idkey
#: The prose a task's `format` covers. Imported rather than restated, and read
#: by `_org` below: the list that decides when an op *claims* org and the list
#: that decides what the store honours it for have to be one list, and the way
#: they stop being one is by each module keeping its own.
from dgraph.task_pending import PROSE
from dgraph.tasks import ID_RE, RESOLVED, STATUSES, UNFINISHED, TaskGraph


def _header(title: str, *, decisions: bool = False, **props: str) -> str:
    """`editor._header` with the task store's keywords.

    Built from the model's own status lists rather than spelled out again. Org
    colours the keywords it is told about, and the buffer is the one place a
    writer types a status by hand: a line naming a status the store does not
    have, or missing one it does, is wrong exactly where being wrong shows.

    `decisions` is whether this project has a decision store, which decides
    one key: see the `keys` argument below.
    """
    return editor._HEADER.format(
        title=title,
        # No *walk* keys, ever. `dgraph.el` reads the decision store and only
        # that: `dgraph-readonly-commands` is `("export")` and the guard tests
        # the first argument, so `dg task export` is refused as `dg task` and
        # the file cannot reach this store at all. Advertising `C-c d p` here
        # bound it to "This buffer is not composing a decision", which is both
        # an error and, in a task buffer, a confusing one.
        #
        # `C-c d v` is the exception, and it took a while to see. It looks up a
        # *decision*, which is the other store — the one `dgraph.el` can read.
        # A task buffer names the decision it is `because` of right there in
        # its Context, so this is the buffer where wanting to read one is most
        # likely, and it was the buffer that did not offer it. Only where that
        # store exists: a tasks-only project would bind it to a failing
        # `dg export`.
        keys=editor._KEYS_ALWAYS + (editor._KEYS_VISIT if decisions else ""),
        todo=" ".join(s for s in STATUSES if s in UNFINISHED),
        done=" ".join(s for s in STATUSES if s in RESOLVED),
        props=editor._props(props),
    )


def _tag(op: dict, tag: str | None = "org") -> dict:
    """Tag `op` with its buffer's dialect, where it writes prose the store
    will convert. `tag` is `None` for the markdown buffer, which claims
    nothing (`editor.Dialect`).

    A task carries one `format` for its whole record, and `task_pending`
    applies it to whichever of `PROSE` the op actually writes. An op claiming a
    dialect while writing none of them is a claim that silently does nothing;
    an op writing one without claiming it renders org as markdown, which is the
    defect this pairing exists to have closed. Both sides read `PROSE`, so
    neither can be extended without the other following.
    """
    if tag and any(op.get(f) for f in _TAGGED):
        op["format"] = tag
    return op


#: What `_tag` and `_seed_in` treat as prose: `PROSE` plus a stop's `why`.
#: `task_pending.PROSE` leaves `why` out because its loop assigns fields and a
#: `why` is appended to `stops` instead — but the tag covers it all the same
#: (`task_render` converts it through `t.format`, `_apply_one` counts it as
#: prose written), so a reason composed in org must claim org as an outcome
#: does, or `*what stopped it*` renders as italic (`T143`).
_TAGGED = PROSE + ("why",)


def next_id(tg: TaskGraph) -> str:
    """`editor.next_id`'s twin — see there. The `D` grant and the `T` grant are
    one grant, so a clone that allocates decisions from a range allocates work
    from the same one."""
    n = ranges.next_number(
        "T", (int(t[1:]) for t in tg.tasks if t[1:].isdigit()))
    return f"T{n:02d}"


def next_offer(stored: TaskGraph | None = None) -> str:
    """`editor.next_offer`'s twin — see there. The store and the task tray."""
    from dgraph import task_pending
    return next_id(task_pending.preview(
        TaskGraph.load() if stored is None else stored))


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
        # As org, whatever it was typed as — `editor._context` says why.
        out.append(editor._quote(orgmd.convert(t.note, t.format, "org")))
    # Read through `cross`, like every other cross-graph reading. This module
    # never touches the link fields itself: assembling what the link says from
    # a task's own attributes is the second implementation of the rule that
    # module exists to be the only one of. A project with no decision store
    # gets no link section, which is the ordinary case rather than an error.
    link = cross.task_link(tg, g, tid) if g is not None else None
    if link and (link["because"] or link["evidence_for"]):
        out.append("** Why this work exists")
        if link["because"]:
            for did in link["because"]:
                out.append(f"   because   {_decision_line(g, did)}")
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


def _csv(val) -> str:
    """A seeded id list as the buffer's comma-separated line. The page's seed
    sends `because` as a string and a staged op holds it as a list (`_parse_add`,
    `task_pending.compose_add`); both are one field here. Audit `AC-F1`."""
    if not val:
        return ""
    return val if isinstance(val, str) else ", ".join(val)


def render_add(tg: TaskGraph, g: Graph | None, seed: dict | None = None) -> str:
    """The template for a new task.

    Every field the flag path takes, because a buffer that covers half of them
    is one you have to leave half-way through — and the two relations are the
    fields most easily got wrong from memory, which is the case for showing
    the backlog beside them rather than asking the writer to hold it in mind.
    """
    seed = seed or {}
    nxt = next_id(tg)
    ready = [t for t in sorted(tg.tasks, key=idkey) if tg.ready(t)]
    return (
        _header("dg task add — a new task", op="add_task",
                decisions=g is not None,
                project=str(project.find().root))
        + "\n* Input\n"
        + editor._field("Id", f"Like T07. Next unused: {nxt}",
                        seed.get("id") or nxt)
        + editor._field("Title", "One line: the work to be done.",
                        seed.get("title", ""))
        + editor._field("Area",
                        editor._area_hint(areas.counts(tg.areas, tg.tasks.values())),
                        seed.get("area", ""))
        + editor._field("Tags", "Optional. Comma-separated words this is "
                                "filed under beside its area.",
                        ", ".join(seed.get("tags", ())))
        + editor._field("After", "Optional. Comma-separated tasks that must be\n"
                                 "resolved before this can start.",
                        ", ".join(seed.get("after", ())))
        + editor._field("Discovered during",
                        "Optional. Comma-separated tasks whose doing turned\n"
                        "this one up. Provenance: it makes nothing wait.",
                        ", ".join(seed.get("discovered_during", ())))
        + editor._field("Because",
                        "Optional. Comma-separated decisions this work exists\n"
                        "because of. Work can rest on several at once.",
                        _csv(seed.get("because")))
        + editor._field("Evidence for",
                        "Optional. The decision this work will inform.",
                        seed.get("evidence_for", ""))
        + editor._field("Note", "Optional prose: what this involves, or what "
                                "is unclear about it.", seed.get("note", ""))
        + editor._field("Done when", "Optional. What finished looks like, in "
                                     "prose — read back at `dg task done`.",
                        seed.get("done_when", ""))
        + editor._field("Probe",
                        "Optional. Its definition of done as a criterion, "
                        'JSON:\n{"kind": "<domain>.<name>", "args": {...}}. '
                        "Appended and dated; `dg task reprobe` changes it.",
                        editor._probe_text(seed.get("probe")))
        + "\n* Context\n** Areas in use\n"
        + ("".join(f"   - {a}  ({n})\n" for a, n in
                   areas.counts(tg.areas, tg.tasks.values()).items())
           or "   (none yet — the first record starts the vocabulary)\n")
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
                decisions=g is not None,
                status="DONE",
                project=str(project.find().root),
                date=seed.get("done") or _date.today().isoformat())
        + "\n* Input\n"
        + editor._field(
            "Outcome",
            "What did this produce? A path, a PR, a measurement — enough that\n"
            "somebody who did not do the work can find what it left behind.\n"
            "Full org is fine."
            + (f"\nDone when: {t.done_when}" if t.done_when else "")
            + (f"\nNote: {tid} still waits on {', '.join(waiting)}."
               if waiting else ""),
            seed.get("outcome", ""))
        + "\n" + _task_context(tg, g, tid)
    )


_STOP_VERB = {"PARKED": "park", "DROPPED": "drop"}


def render_stop(tg: TaskGraph, g: Graph | None, tid: str, status: str,
                seed: dict | None = None) -> str:
    """The template for putting work down or giving it up (`T143`, on `D121`).

    `render_done`'s shape — one field, the record beside it — for the other
    two acts a person composes a reason for. The reason is the store's only
    archived record: nothing clears it, and a task put down three times says
    so three times, which is why the earlier stops are in view here. The
    fallout of a drop is not in the buffer: `--keep`/`--drop-too` are verdicts
    on other work and stay flags, as the browser keeps them radio buttons.
    """
    seed = seed or {}
    t = tg.tasks[tid]
    verb = _STOP_VERB[status]
    hint = ("What stopped it? Kept after it resumes — a task put down three\n"
            "times says so three times." if status == "PARKED" else
            "Why is this not being done? Nothing clears it — the record is\n"
            "the point.") + "\nFull org is fine."
    if t.stops:
        hint += "\nStopped before: " + "; ".join(
            f"{k.date} — {k.why}" for k in t.stops)
    return (
        _header(f"dg task {verb} {tid} — {t.title}", op="set_status", task=tid,
                decisions=g is not None,
                status=status,
                project=str(project.find().root),
                date=seed.get("date") or _date.today().isoformat())
        + "\n* Input\n"
        + editor._field("Why", hint, seed.get("why", ""))
        + "\n" + _task_context(tg, g, tid)
    )


def render_resolve(tg: TaskGraph, g: Graph | None, tid: str,
                   seed: dict | None = None) -> str:
    """The browser's one buffer for the task panel's three closing boxes
    (`D122`, `T144`): Outcome, Why parked, Why dropped — the fields of
    `render_done` and the two `render_stop`s side by side, each seeded from
    its box and filled back to it. Browser only: the CLI's three commands are
    three acts and keep their one-field buffers. It parses to fields, not to
    an op, because no status is known until the reader presses a button — and
    the boxes the pressed button does not read are discarded, which the
    decision accepts and the hint says.
    """
    seed = seed or {}
    t = tg.tasks[tid]
    waiting = tg.waiting_on(tid)
    stops = ("\nStopped before: " + "; ".join(
        f"{k.date} — {k.why}" for k in t.stops)) if t.stops else ""
    return (
        _header(f"dg task done | park | drop {tid} — {t.title}", op="resolve",
                task=tid, decisions=g is not None,
                project=str(project.find().root),
                date=_date.today().isoformat())
        + "\n* Input\n"
        + editor._field(
            "Outcome",
            "Fill the field for the act you mean; the browser fills all three\n"
            "boxes and the button you press decides — the other two are\n"
            "discarded.\n\n"
            "What did this produce? A path, a PR, a measurement — enough that\n"
            "somebody who did not do the work can find what it left behind.\n"
            "Full org is fine."
            + (f"\nDone when: {t.done_when}" if t.done_when else "")
            + (f"\nNote: {tid} still waits on {', '.join(waiting)}."
               if waiting else ""),
            seed.get("outcome", ""))
        + editor._field(
            "Why parked",
            "What stopped it? Kept after it resumes — a task put down three\n"
            "times says so three times." + stops,
            seed.get("why_park", ""))
        + editor._field(
            "Why dropped",
            "Why is this not being done? Nothing clears it — the record is\n"
            "the point.",
            seed.get("why_drop", ""))
        + "\n" + _task_context(tg, g, tid)
    )


ALLOWED = {
    "add_task": {"id", "title", "area", "after", "discovered during",
                 "because", "evidence for", "note", "probe", "done when",
                 "tags"},
    "resolve": {"outcome", "why parked", "why dropped"},
    "amend": {"title", "area", "note", "done when", "tags"},
    # By status: a finishing buffer takes the outcome, a stopping one the
    # reason. `_allowed` picks; the key here is the one `parse` falls back to.
    "set_status": {"outcome"},
    "set_status:PARKED": {"why"},
    "set_status:DROPPED": {"why"},
}


def _allowed(kind: str, meta: dict) -> set[str]:
    """The fields a buffer of this kind may carry under Input — for a
    `set_status`, the ones its status writes."""
    if kind == "set_status":
        keyed = ALLOWED.get(f"set_status:{meta.get('status')}")
        if keyed is not None:
            return keyed
    return ALLOWED.get(kind, set())


def amend_seed(tg: TaskGraph, tid: str) -> dict:
    """The record as the amend buffer's seed — the fields amend may correct
    and the dialect they are stored in, so `_seed_in` can show them in the
    buffer's. `editor.amend_seed`'s twin."""
    t = tg.tasks[tid]
    return {"title": t.title, "area": t.area, "note": t.note or "",
            "done_when": t.done_when or "", "tags": list(t.tags or ()),
            "format": t.format}


def render_amend(tg: TaskGraph, g: Graph | None, tid: str,
                 seed: dict | None = None) -> str:
    """`editor.render_amend`'s twin for a task (`D103`): the fields amend may
    correct, prefilled with the task as it stands. Reuses the `## Note` and
    `## Done when` fields the add buffer already renders.

    `seed` is the record as `_seed_in` converted it — prose in the buffer's
    dialect, whatever the record's. Read from the record only where no seed
    was given, which no compose door does: showing stored org in a markdown
    buffer and tagging what came back as markdown relabelled every `*span*`
    a person left alone. Audit `AC-F4`.
    """
    s = seed if seed is not None else amend_seed(tg, tid)
    return (
        _header(f"dg task amend {tid}", op="amend", task=tid,
                project=str(project.find().root))
        + "\n* Input\n"
        + editor._field("Title", "One line: the work to be done.", s["title"])
        + editor._field("Area", "One area, or a new one; areas accumulate.",
                        s["area"])
        + editor._field("Tags", "Comma-separated words this is filed under "
                                "beside its area. The whole set: empty drops "
                                "every tag.", ", ".join(s.get("tags") or ()))
        + editor._field("Note", "Optional prose: what this involves. May be "
                                "emptied.", s.get("note") or "")
        + editor._field("Done when", "What finished looks like, in prose. May "
                                     "be emptied.", s.get("done_when") or "")
        + "\n" + _task_context(tg, g, tid)
    )


def _parse_amend(tg: TaskGraph, meta: dict, f: dict,
                 tag: str | None = "org") -> list[dict]:
    """A task `set_fields` carrying only the fields the buffer changed. Title
    and area cannot be blanked; note and done_when may be emptied. Nothing
    changed is an abort. `editor._parse_amend`'s twin.

    *Changed* is judged against the record **as the buffer showed it** — its
    prose converted into the buffer's dialect, the way `render_amend` seeded
    it — so an org note left alone in a markdown buffer is not staged as a
    markdown rewrite of itself (`AC-F4`). `format` rides along by `_tag`'s
    rule: whenever the op writes any of `PROSE`, not only `done_when` — a
    note typed in emacs is org too (`AC-F5`).
    """
    tid = meta.get("task")
    if tid not in tg.tasks:
        raise EditorError(f"unknown task {tid!r}")
    t = tg.tasks[tid]
    op: dict = {"op": "set_fields", "task": tid}
    for field, key, current in (("title", "title", t.title),
                                ("area", "area", t.area),
                                ("note", "note", t.note or ""),
                                ("done when", "done_when", t.done_when or "")):
        new = editor._val(f, field)
        if key in ("title", "area") and not new:
            raise EditorError(f"{field.capitalize()} is empty — a {key} is "
                              f"required and cannot be blanked here")
        if key in PROSE:
            current = editor.mdbuffer.trim(orgmd.convert(current, t.format, tag) or "")
        else:
            current = (current or "").strip()
        if new != current:
            op[key] = new if new or key in ("title", "area") else None
    # The whole tag set, as the flags stage it (`AD-F3`).
    tags = _tags.clean(f["tags"]) if f.get("tags", "").strip() else []
    if tags != list(t.tags or ()):
        op["tags"] = tags
    if len(op) == 2:
        raise EditorAbort("nothing changed — nothing staged")
    return [_tag(op, tag)]


def compose_amend(tg: TaskGraph, g: Graph | None, tid: str, launcher=None,
                  dialect: str | None = None) -> list[dict]:
    dialect = dialect or editor.cli_dialect()
    if tid not in tg.tasks:
        raise EditorError(f"unknown task {tid!r}")
    seed = _seed_in(amend_seed(tg, tid), dialect)
    return editor.run(
        render_amend(tg, g, tid, seed),
        lambda after: parse(after, tg=tg, g=g, expect_kind="amend",
                            expect_task=tid, dialect=dialect),
        launcher=launcher, dialect=dialect)


def render_op(tg: TaskGraph, g: Graph | None, i: int, op: dict) -> str:
    """Re-render a staged task op for revision (`dg task edit N`).
    `editor.render_op`'s twin: the composed task ops are an `add_task` and a
    `set_status` that finishes work; the rest are derived or structural and are
    not edited in place, the same line `editor.render_op` draws for decisions."""
    kind = op.get("op")
    seed = dict(op)
    if kind == "add_task":
        # The edges come from the *graph*, not from the op — `editor.render_op`
        # says why: an `add_task` carries no `after`, its relations are
        # `add_dep` ops staged beside it, and `tg` is the effective graph
        # without this op, so the task is absent and every staged edge
        # pointing at it still applies. Seeded rather than left blank because
        # a blank field on a task that *is* attached is a buffer lying about
        # the thing it is editing (`F26`); the twin was written without this
        # and said *After* was empty while the tray held the edge (`AC-F2`).
        tid = seed.get("id") or ""
        seed.setdefault("after", tg.prerequisites(tid))
        seed.setdefault("discovered_during", tg.discovered_during(tid))
        text = render_add(tg, g, seed)
    elif kind == "set_status" and op.get("status") == "DONE":
        text = render_done(tg, g, op["task"], seed)
    elif kind == "set_status" and op.get("status") in _STOP_VERB:
        # Composed since `T143`, so revised where it was composed. A drop's
        # cascade op is the same shape and opens here too; revising the
        # reason it carries is harmless.
        text = render_stop(tg, g, op["task"], op["status"], seed)
    else:
        raise EditorError(
            f"op {i} is {kind!r} — derived or structural, not composed in an "
            f"editor; `dg task drop-op {i}` removes it")
    return text.replace(":END:", f":DGRAPH_INDEX: {i}\n:END:", 1)


def supersedes(kind: str, op: dict):
    """What a revision of `op` takes out of the task tray, or None for
    "nothing". `editor.supersedes`'s twin, for the same reason it exists
    there: an `add_task` comes back with one `add_dep` per relation named in
    the buffer, and re-stating them has to retract the ones the old version
    staged, or the tray holds both readings of what the task waits on and
    applies their union (`AC-F2`). Both edge kinds, because the buffer
    re-states both. An `add_dep` naming other tasks as well keeps them."""
    if kind != "add_task":
        return None
    tid = op.get("id")

    def supersede(other: dict) -> dict | None:
        if other.get("op") != "add_dep" or tid not in (other.get("to") or []):
            return other
        rest = [t for t in other["to"] if t != tid]
        return {**other, "to": rest} if rest else None

    return supersede


def compose_edit(tg: TaskGraph, g: Graph | None, i: int, op: dict,
                 launcher=None, dialect: str | None = None,
                 explain=None) -> list[dict]:
    """`editor.compose` for a staged task op. `explain` is `dg task edit`'s
    account of an id `tg` lacks (`D97`), handed through to the parser."""
    dialect = dialect or editor.cli_dialect()
    kind = op.get("op")
    # Shown in the buffer's dialect, whatever the op was composed in — the
    # rule `compose_add` and `compose_done` already follow (`AC-F4`).
    op = _seed_in(op, dialect)
    return editor.run(
        render_op(tg, g, i, op),
        lambda after: parse(after, tg=tg, g=g, expect_kind=kind,
                            expect_task=op.get("task"), dialect=dialect,
                            explain=explain),
        launcher=launcher, dialect=dialect)


def parse(text: str, *, tg: TaskGraph, g: Graph | None,
          expect_kind: str | None = None,
          expect_task: str | None = None,
          new_area: bool = False,
          dialect: str = "org",
          explain=None) -> list[dict]:
    """Buffer -> task ops ready for `task_pending`. Raises rather than guessing.

    The same contract as `editor.parse`, kept deliberately close to it: only
    the `* Input` subtree is read, an untouched template aborts, and a buffer
    missing a required field raises rather than staging something partial.
    """
    if not text.strip():
        raise EditorAbort("empty buffer — nothing staged")

    d = editor.DIALECTS[dialect]
    meta = d.meta(text)
    kind = meta.get("op")
    if not kind:
        raise EditorError(d.no_op)
    if expect_kind and kind != expect_kind:
        raise EditorError(f"buffer is a {kind!r} template, expected {expect_kind!r}")
    if expect_task and meta.get("task") != expect_task:
        raise EditorError(
            f"buffer targets {meta.get('task')!r}, not {expect_task!r} — a task "
            f"cannot be retargeted by editing; abort and re-run"
        )

    sections = d.sections(text)
    unknown = sorted(k for k in sections if k not in _allowed(kind, meta))
    if unknown:
        names = ", ".join(f"{d.mark2} {sections[u][0]}" for u in unknown)
        raise EditorError(f"unknown field(s) under Input: {names}")
    f = {k: d.body(v[1]) for k, v in sections.items()}
    if not any(f.values()):
        raise EditorAbort("template came back untouched — nothing staged")

    if kind == "add_task":
        return _parse_add(tg, g, f, new_area=new_area, tag=d.tag,
                          explain=explain)
    if kind == "set_status":
        return _parse_status(tg, meta, f, tag=d.tag)
    if kind == "resolve":
        return _parse_resolve(tg, meta, f, tag=d.tag)
    if kind == "amend":
        return _parse_amend(tg, meta, f, tag=d.tag)
    raise EditorError(f"cannot compose a {kind!r} task op")


def _need(f: dict[str, str], name: str) -> str:
    val = editor._val(f, name)      # prose as typed, one-liners stripped (D104)
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


def _targets(tg: TaskGraph, tid: str, raw: str, field: str,
             explain=None) -> list[str]:
    out = []
    for other in [x.strip() for x in raw.split(",") if x.strip()]:
        if other == tid:
            raise EditorError(f"{field}: {tid} cannot come after itself")
        if other not in tg.tasks:
            raise editor._unknown(f"{field}: unknown task {other!r}", [other],
                                  explain)
        if other not in out:
            out.append(other)
    return out


def _parse_add(tg: TaskGraph, g: Graph | None, f: dict, *,
               new_area: bool = False,
               tag: str | None = "org", explain=None) -> list[dict]:
    tid = _need(f, "id")
    if not ID_RE.fullmatch(tid):
        raise EditorError(f"malformed id {tid!r} — expected something like T07\n"
                          f"decisions are D-ids and live in a different store")
    if tid in tg.tasks:
        raise EditorError(f"{tid} already exists")
    area = _need(f, "area")
    # `editor._parse_add`'s twin — see there.
    why = pending.refuse_area(
        area, own=areas.counts(tg.areas, tg.tasks.values()),
        other=areas.stored_counts(project.find().store),
        owner=pending.owner(), new_area=new_area)
    if why is not None:
        raise EditorError(f"Area: {why}")
    op = {"op": "add_task", "id": tid, "title": _need(f, "title"), "area": area}
    if editor._val(f, "note"):
        op["note"] = editor._val(f, "note")
    because_raw = f.get("because", "").strip()
    if because_raw:
        op["because"] = [_premise(g, d.strip(), "Because")
                         for d in because_raw.split(",") if d.strip()]
    ef_raw = f.get("evidence for", "").strip()
    if ef_raw:
        op["evidence_for"] = _premise(g, ef_raw, "Evidence for")
    if editor._val(f, "done when"):
        op["done_when"] = editor._val(f, "done when")
    if f.get("tags", "").strip():
        op["tags"] = _tags.clean(f["tags"])
    if f.get("probe", "").strip():
        op["probe"] = editor._parse_probe(f["probe"])
        op["date"] = _date.today().isoformat()
    ops = [_tag(op, tag)]
    # One group, in the order the CLI stages them: the task, then its edges.
    # A task landing without them is not a partial batch something refuses —
    # it is a task that reads as startable. Audit F28.
    for field, key, edge in (("After", "after", "precedes"),
                             ("Discovered during", "discovered during",
                              "prompted")):
        for other in _targets(tg, tid, f.get(key, ""), field, explain):
            ops.append({"op": "add_dep", "from": other, "to": [tid],
                        "kind": edge})
    return ops


def _parse_status(tg: TaskGraph, meta: dict, f: dict,
                  tag: str | None = "org") -> list[dict]:
    """A finishing or a stopping buffer, by the status its header carries:
    `DONE` writes the outcome and the date it was done, `PARKED` and `DROPPED`
    the reason and the date of the stop — the op shapes `dg task done`, `park`
    and `drop` stage from flags."""
    tid = meta.get("task")
    if tid not in tg.tasks:
        raise EditorError(f"unknown task {tid!r}")
    status = meta.get("status", "DONE")
    date = meta.get("date") or _date.today().isoformat()
    # Provenance, exactly as `editor._parse_close` records it: this buffer is
    # org, so the views must convert its emphasis rather than read `*HNSW*` as
    # markdown's italic. Claimed through `_org` rather than written in, so the
    # claim is checked against what the store will honour. A task carries one
    # `format` for its whole record, so the caller says so when the record
    # already held prose written somewhere else.
    if status in _STOP_VERB:
        return [_tag({"op": "set_status", "task": tid, "status": status,
                      "why": _need(f, "why"), "date": date}, tag)]
    return [_tag({
        "op": "set_status", "task": tid, "status": status,
        "outcome": _need(f, "outcome"),
        "done": date,
    }, tag)]


def _parse_resolve(tg: TaskGraph, meta: dict, f: dict,
                   tag: str | None = "org") -> list[dict]:
    """The three-box buffer's fields (`D122`). Not an op: `op` says `resolve`
    so nothing downstream mistakes it for one, and the page reads the three
    values and the dialect they were typed in. Whichever are filled come
    back; the ones left blank come back empty so the page can clear a box
    the reader emptied in the buffer."""
    tid = meta.get("task")
    if tid not in tg.tasks:
        raise EditorError(f"unknown task {tid!r}")
    out = {"op": "resolve", "task": tid,
           "outcome": editor._val(f, "outcome"),
           "why_park": editor._val(f, "why parked"),
           "why_drop": editor._val(f, "why dropped")}
    if tag and any(out[k] for k in _RESOLVE):
        out["format"] = tag
    return [out]


#: The three-box buffer's values, as the page and the seed name them.
_RESOLVE = ("outcome", "why_park", "why_drop")


def _seed_in(seed: dict | None, dialect: str) -> dict | None:
    """`editor.seed_in` for a task's prose — `PROSE`, the fields one
    `Task.format` covers."""
    if not seed:
        return seed
    tag = editor.DIALECTS[dialect].tag
    frm = seed.get("format")
    if (frm == "org") == (tag == "org"):
        return seed
    out = dict(seed)
    # Each field once: `outcome` is in both tuples, and converting it twice
    # turns org bold into org italic.
    for f in dict.fromkeys(_TAGGED + _RESOLVE):
        if out.get(f):
            out[f] = orgmd.convert(out[f], frm, tag)
    out["format"] = tag
    return out


def compose_add(tg: TaskGraph, g: Graph | None, seed: dict | None = None,
                new_area: bool = False, launcher=None,
                dialect: str | None = None) -> list[dict]:
    dialect = dialect or editor.cli_dialect()
    seed = _seed_in(seed, dialect)
    return editor.run(
        render_add(tg, g, seed),
        lambda after: parse(after, tg=tg, g=g, expect_kind="add_task",
                            new_area=new_area, dialect=dialect),
        launcher=launcher, dialect=dialect)


def compose_done(tg: TaskGraph, g: Graph | None, tid: str,
                 seed: dict | None = None, launcher=None,
                 dialect: str | None = None) -> list[dict]:
    dialect = dialect or editor.cli_dialect()
    seed = _seed_in(seed, dialect)
    return editor.run(
        render_done(tg, g, tid, seed),
        lambda after: parse(after, tg=tg, g=g, expect_kind="set_status",
                            expect_task=tid, dialect=dialect),
        launcher=launcher, dialect=dialect)


def compose_stop(tg: TaskGraph, g: Graph | None, tid: str, status: str,
                 seed: dict | None = None, launcher=None,
                 dialect: str | None = None) -> list[dict]:
    """`compose_done` for the reason a task is parked or dropped (`T143`): the
    one op, with the fallout of a drop left to the caller's flags."""
    dialect = dialect or editor.cli_dialect()
    seed = _seed_in(seed, dialect)
    return editor.run(
        render_stop(tg, g, tid, status, seed),
        lambda after: parse(after, tg=tg, g=g, expect_kind="set_status",
                            expect_task=tid, dialect=dialect),
        launcher=launcher, dialect=dialect)


def compose_resolve(tg: TaskGraph, g: Graph | None, tid: str,
                    seed: dict | None = None, launcher=None,
                    dialect: str | None = None) -> list[dict]:
    """The browser's three-box buffer (`D122`): one `resolve` fields record,
    never an op. `_tcompose` does not reach this — the CLI has no act it
    would be."""
    dialect = dialect or editor.cli_dialect()
    seed = _seed_in(seed, dialect)
    return editor.run(
        render_resolve(tg, g, tid, seed),
        lambda after: parse(after, tg=tg, g=g, expect_kind="resolve",
                            expect_task=tid, dialect=dialect),
        launcher=launcher, dialect=dialect)


def compose_park(tg, g, tid, seed=None, launcher=None, dialect=None):
    return compose_stop(tg, g, tid, "PARKED", seed, launcher, dialect)


def compose_drop(tg, g, tid, seed=None, launcher=None, dialect=None):
    return compose_stop(tg, g, tid, "DROPPED", seed, launcher, dialect)
