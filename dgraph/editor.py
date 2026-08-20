"""Compose a decision in an editor, the way `git commit` composes a message.

`dg decide --edit` writes an org buffer holding the input fields plus context,
launches an editor on it, blocks, then parses what comes back and hands the
caller an op dict. The CLI owns the workflow throughout: the editor only ever
edits a file. `dgraph/elisp/dgraph.el` adds emacs affordances on top - `dg:`
links, a read-only context subtree, `C-c C-c` to finish - but nothing here
depends on them, and the elisp never touches the graph.

Two rules shape the parser:

- **Only the `* Input` subtree is read.** Context is reference material; a user
  who mangles it cannot change the staged op. That is the actual guarantee, and
  it is why the emacs read-only guard is a convenience rather than a mechanism.
- **A half-filled template is loud, never silent.** An untouched buffer aborts;
  a buffer with prose but a missing required field raises. Quietly staging a
  decision missing its source is the failure this tool exists to prevent.

Prose comes back as typed - full org is fine, and is stored verbatim. The views
convert for display; see `dgraph/orgmd.py`.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import textwrap
from datetime import date as _date
from pathlib import Path

from dgraph import project
from dgraph.model import Graph, status_fault

ELISP = Path(__file__).resolve().parent / "elisp" / "dgraph.el"

STATUSES = ("DECIDED", "OPEN", "BLOCKED", "REOPENED", "PROVISIONAL")


class EditorAbort(RuntimeError):
    """The user declined: empty buffer, untouched template, or a failed editor."""


class EditorError(RuntimeError):
    """The buffer came back malformed or incomplete. Nothing is staged."""


# ---- template ------------------------------------------------------------

#: Comment lines are stripped, git-style. `#+` keywords and drawer lines are
#: not comments and must survive - getting this regex wrong silently eats the
#: metadata drawer.
_COMMENT = re.compile(r"^[ \t]*#(?!\+).*$\n?", re.M)

_HEAD = re.compile(r"^(\*+)[ \t]+(.*?)[ \t]*$", re.M)
_PROP = re.compile(r"^[ \t]*:([A-Z_]+):[ \t]*(.*?)[ \t]*$", re.M)
_CHECKED = re.compile(r"^[ \t]*[-+*][ \t]+\[[Xx]\][ \t]+(D\d+)", re.M)

_HEADER = """\
# -*- mode: org; -*-
# {title}
# Lines starting with "# " are ignored. Only the "Input" subtree is read back;
# Context is reference material and cannot change what gets staged.
#   C-c C-c  save and stage        C-c C-k  abort, stage nothing
#   C-c C-o  follow a dg: link     C-c C-p  parent      C-c C-a  ancestors
# An empty Input aborts, exactly like an empty git commit message.
# A "*" at column 0 ends a field - org reads it as a heading. Escape it as ",*".
#+TODO: {todo} | {done}
:PROPERTIES:
{props}
:END:
"""


def _props(d: dict[str, str]) -> str:
    return "\n".join(f":DGRAPH_{k.upper()}: {v}" for k, v in d.items() if v is not None)


def _header(title: str, **props: str) -> str:
    return _HEADER.format(
        title=title,
        todo=" ".join(s for s in STATUSES if s != "DECIDED"),
        done="DECIDED",
        props=_props(props),
    )


def _escape(body: str) -> str:
    """The inverse of `_body`'s comma-unescape, applied to seeded content.

    A stored line whose first non-blank character is `*` would otherwise come
    back as an org heading: the parser would read it as the end of the field —
    or of the whole Input subtree — and refuse the tool's own output
    (`dg edit N` on a close whose answer carries such a line). One comma is
    added per line and `_body` removes exactly one, so any stored text,
    including one already starting with `,*`, round-trips unchanged.
    """
    return re.sub(r"^([ \t]*)(,*\*)", r"\1,\2", body, flags=re.M)


def _field(name: str, hint: str = "", body: str = "") -> str:
    out = [f"** {name}"]
    if hint:
        out += [f"# {ln}" for ln in hint.splitlines()]
    out.append(_escape(body.rstrip("\n")) if body else "")
    # exactly one blank line after every field, whether or not it was seeded
    return "\n".join(out).rstrip("\n") + "\n\n"


def _quote(text: str | None, indent: str = "   ") -> str:
    if not text:
        return ""
    return textwrap.indent(text.strip(), indent)


def _node_line(g: Graph, vid: str) -> str:
    v = g.vertices[vid]
    return f"[[dg:{vid}][{vid} — {v.title}]]"


def _context(g: Graph, vid: str) -> str:
    """Immediate context: the edge that led here, the premises, the chain.

    Baked in rather than fetched, so it is there for any editor. The elisp can
    reach the rest of the graph on demand.

    The *walk* — which ancestors, in what order, carrying which fields —
    belongs to `dgraph.context`, which `dg context` renders as plain text and
    the web app reads as JSON. This function only renders it as org. Three
    consumers cannot disagree about what a decision rests on when only one of
    them traverses.
    """
    from dgraph import context as _ctx
    v = g.vertices[vid]
    out = ["* Context", "** This decision",
           f"   {vid} — {v.title}",
           f"   area {v.area} · status {v.status} · depth {g.depth(vid)}"]
    deps = g.depends(vid)
    kids = g.children(vid)
    out.append(f"   depends on {', '.join(deps) or '—'} · opens {', '.join(kids) or '—'}")
    if v.note:
        out.append(_quote(v.note))

    chain = _ctx.chain(g, vid)
    by_id = {p.id: p for p in chain}

    for parent in deps:
        p = by_id[parent]
        out.append(f"** {p.status.split(':')[0]} {_node_line(g, parent)}")
        if p.answer is not None:
            out.append("   :PROPERTIES:")
            out.append(f"   :FALSIFIER: {p.falsifier or '—'}")
            out.append(f"   :SOURCE:    {p.source or '—'}")
            out.append(f"   :DATE:      {p.date or '—'}")
            out.append("   :END:")
            out.append(_quote(p.answer))
        if p.also_opened:
            out.append(f"   also opened: {', '.join(p.also_opened)}")

    if chain:
        out.append("** Ancestor chain")
        out.append("   | depth | node | status | date |")
        out.append("   |-------+------+--------+------|")
        for p in chain:
            out.append(f"   | {p.depth} | {_node_line(g, p.id)} "
                       f"| {p.status} | {p.date or '—'} |")

    hist = g.history(vid)
    if hist:
        out.append("** Superseded here")
        for h in hist:
            out.append(f"   - “{h.summary}” → {h.replaced_by or '(undecided)'}")
            if h.why:
                out.append(_quote(h.why, "     "))
    return "\n".join(out) + "\n"


def render_close(g: Graph, vid: str, seed: dict | None = None) -> str:
    seed = seed or {}
    v = g.vertices[vid]
    linked = set(g.children(vid))
    boxes = []
    for other in sorted(g.vertices):
        if other == vid:
            continue
        mark = "X" if other in linked or other in (seed.get("to") or []) else " "
        tail = "   (linked)" if other in linked else ""
        boxes.append(f"- [{mark}] {other} — {g.vertices[other].title}{tail}")

    return (
        _header(f"dg decide {vid} — {v.title}", op="close", vertex=vid,
                project=str(project.find().root),
                date=seed.get("date") or _date.today().isoformat())
        + "\n* Input\n"
        + _field("Answer",
                 "What was decided, and on what evidence. Full org is fine.\n"
                 "*single asterisks* render as italic outside emacs; use **bold**.",
                 seed.get("answer", ""))
        + _field("Source", 'A report/ path, a script, or "discussion". '
                           "file: links welcome.", seed.get("source", ""))
        + _field("Falsifier",
                 "What evidence would reopen this? Required unless this opens\n"
                 'nothing. "ANALYTIC — …" if no measurement could overturn it.',
                 seed.get("falsifier", ""))
        + _field("Opens", "Mark with X. Boxes marked (linked) are already edges\n"
                          "and stay regardless of the box.", "\n".join(boxes))
        + _field("Summary", "Optional. Short label, used if this answer is ever "
                            "superseded.", seed.get("summary", ""))
        + "\n" + _context(g, vid)
    )


def render_reopen(g: Graph, vid: str, seed: dict | None = None) -> str:
    seed = seed or {}
    from dgraph import pending

    v = g.vertices[vid]
    drags = [o["vertex"] for o in pending.expand(g, {"op": "reopen", "vertex": vid,
                                                     "why": "?"})
             if o["op"] == "set_status"]
    ctx = _context(g, vid).replace(
        "* Context\n",
        "* Context\n** Becomes PROVISIONAL if you do this\n"
        f"   {', '.join(drags) or 'nothing'}\n", 1)
    e = g.active_edge(vid)
    return (
        _header(f"dg reopen {vid} — {v.title}", op="reopen", vertex=vid,
                project=str(project.find().root))
        + "\n* Input\n"
        + _field("Why", "What new evidence or argument challenges the answer?",
                 seed.get("why", ""))
        + _field("Summary", "Short label for the answer being superseded.\n"
                 f"Current answer: {((e.answer or '')[:60] if e else '')}…",
                 seed.get("summary", ""))
        + "\n" + ctx
    )


def render_add(g: Graph, seed: dict | None = None) -> str:
    seed = seed or {}
    nxt = f"D{max((int(v[1:]) for v in g.vertices if v[1:].isdigit()), default=0) + 1:02d}"
    return (
        _header("dg add — a new decision vertex", op="add_vertex",
                project=str(project.find().root))
        + "\n* Input\n"
        + _field("Id", f"Like D07. Next unused: {nxt}", seed.get("id") or nxt)
        + _field("Title", "One line: the question this decision answers.",
                 seed.get("title", ""))
        + _field("Area", f"One of: {', '.join(g.areas)}", seed.get("area", ""))
        + _field("Status", f"One of: {', '.join(STATUSES)}. Default OPEN.\n"
                           "BLOCKED needs a target, e.g. BLOCKED:D04.",
                 seed.get("status", "OPEN"))
        + _field("After", "Optional. Comma-separated parents that open this.",
                 ", ".join(seed.get("after", [])) if seed.get("after") else "")
        + _field("Note", "Optional prose for a decision with no answer yet.",
                 seed.get("note", ""))
        + "\n* Context\n** Areas\n"
        + "".join(f"   - {a}\n" for a in g.areas)
        + "** Frontier (still open or blocked)\n"
        + "".join(f"   - {_node_line(g, f)} {g.vertices[f].status}\n"
                  for f in g.frontier())
    )


RENDERERS = {"close": render_close, "reopen": render_reopen, "add_vertex": render_add}


def render_op(g: Graph, i: int, op: dict) -> str:
    """Re-render an already-staged op for revision (`dg edit N`)."""
    kind = op.get("op")
    if kind not in RENDERERS:
        raise EditorError(
            f"op {i} is {kind!r} — derived ops are not edited directly; "
            f"use `dg drop {i}`"
        )
    seed = dict(op)
    if kind == "add_vertex":
        # The parents come from the *graph*, not from the op: an `add_vertex`
        # carries no `after`, because a dependency is an edge and edges are
        # staged as their own ops beside it. `g` here is the effective graph
        # without this op (`cli._eff(g, skip=i)`), so the vertex is absent but
        # every staged edge pointing at it still applies — which is precisely
        # the set the batch attaches.
        #
        # Seeded rather than left blank because a blank field on a vertex that
        # *is* attached is a buffer lying about the thing it is editing: the
        # obvious reading is "no parents", and saving it meant them. Audit F26.
        seed.setdefault("after", g.depends(seed.get("id") or ""))
    text = (render_add(g, seed) if kind == "add_vertex"
            else RENDERERS[kind](g, op["vertex"], seed))
    return text.replace(":END:", f":DGRAPH_INDEX: {i}\n:END:", 1)


# ---- parsing -------------------------------------------------------------


def _sections(text: str) -> dict[str, tuple[str, str]]:
    """Split the `* Input` subtree into `** Field` -> body.

    Headings are found with a plain regex, which is exactly org's own rule: org
    treats a `*` at column 0 as a heading even inside `#+BEGIN_EXAMPLE`, so being
    cleverer here would make the parser disagree with the editor.
    """
    heads = list(_HEAD.finditer(text))
    start = next((h for h in heads if h.group(1) == "*"
                  and h.group(2).lower() == "input"), None)
    if start is None:
        raise EditorError("no `* Input` section — the buffer is not a dg template")
    out: dict[str, tuple[str, str]] = {}      # lowercase key -> (as typed, body)
    cur: str | None = None
    shown: str = ""
    at = start.end()
    for h in heads:
        if h.start() < start.end():
            continue
        if len(h.group(1)) == 1:            # `* Context` or beyond: Input is done
            if cur is not None:
                out[cur] = (shown, text[at:h.start()])
            cur = None
            break
        if len(h.group(1)) == 2:            # a field
            if cur is not None:
                out[cur] = (shown, text[at:h.start()])
            shown = h.group(2).split(":")[0].strip()
            name = shown.lower()
            if name in out:
                raise EditorError(f"duplicate field `** {shown}` under Input")
            cur, at = name, h.end()
        # deeper headings (***) are body, and are left in place
    if cur is not None:
        out[cur] = (shown, text[at:])
    return out


def _body(raw: str) -> str:
    """Strip comments, org's comma-escape, and common indentation.

    The unescape removes exactly one comma from a `,,,*`-style run — the
    mirror of `_escape` adding one — so escape∘unescape is the identity for
    any content, however many literal commas it starts with.
    """
    text = _COMMENT.sub("", raw)
    text = re.sub(r"^([ \t]*),(,*\*)", r"\1\2", text, flags=re.M)
    return textwrap.dedent(text).strip()


def _meta(text: str) -> dict[str, str]:
    end = text.find(":END:")
    head = text[:end] if end != -1 else text
    return {m.group(1)[len("DGRAPH_"):].lower(): m.group(2)
            for m in _PROP.finditer(head) if m.group(1).startswith("DGRAPH_")}


ALLOWED = {
    "close": {"answer", "source", "falsifier", "opens", "summary"},
    "reopen": {"why", "summary"},
    "add_vertex": {"id", "title", "area", "status", "after", "note"},
}


def parse(
    text: str,
    *,
    g: Graph,
    expect_kind: str | None = None,
    expect_vertex: str | None = None,
    expect_index: int | None = None,
) -> list[dict]:
    """Buffer -> op dicts ready for `pending.stage`. Raises rather than guessing."""
    if not text.strip():
        raise EditorAbort("empty buffer — nothing staged")

    meta = _meta(text)
    kind = meta.get("op")
    if not kind:
        raise EditorError("no :DGRAPH_OP: in the buffer's properties drawer")
    if expect_kind and kind != expect_kind:
        raise EditorError(f"buffer is a {kind!r} template, expected {expect_kind!r}")
    if expect_vertex and meta.get("vertex") != expect_vertex:
        raise EditorError(
            f"buffer targets {meta.get('vertex')!r}, not {expect_vertex!r} — "
            f"a decision cannot be retargeted by editing; drop and re-stage"
        )
    if expect_index is not None and meta.get("index") != str(expect_index):
        raise EditorError(f"buffer is for staged op {meta.get('index')}, "
                          f"not {expect_index}")

    sections = _sections(text)
    unknown = sorted(k for k in sections if k not in ALLOWED.get(kind, set()))
    if unknown:
        # quoted back as the user spelled them, so a typo is recognisable
        names = ", ".join(f"** {sections[u][0]}" for u in unknown)
        raise EditorError(f"unknown field(s) under Input: {names}")
    raw = {k: v[1] for k, v in sections.items()}
    f = {k: _body(v) for k, v in raw.items()}
    if not any(v for k, v in f.items() if k != "opens"):
        raise EditorAbort("template came back untouched — nothing staged")

    if kind == "close":
        return _parse_close(g, meta, f, raw)
    if kind == "reopen":
        return _parse_reopen(meta, f)
    if kind == "add_vertex":
        return _parse_add(g, f)
    raise EditorError(f"cannot compose a {kind!r} op")


def _need(f: dict[str, str], name: str) -> str:
    val = f.get(name, "").strip()
    if not val:
        raise EditorError(f"{name.capitalize()} is empty — nothing staged")
    return val


def _parse_close(g: Graph, meta: dict, f: dict, raw: dict) -> list[dict]:
    vid = meta.get("vertex")
    if vid not in g.vertices:
        raise EditorError(f"unknown vertex {vid!r}")
    picked = _CHECKED.findall(raw.get("opens", ""))
    unknown = [t for t in picked if t not in g.vertices]
    if unknown:
        raise EditorError(f"unknown target(s): {', '.join(unknown)}")
    # Already-linked children survive whatever the checkbox says: `_apply_one`
    # unions op["to"] with the edge's targets, so the buffer must not imply a
    # dependency can be dropped here.
    to = sorted(set(picked) | set(g.children(vid)))
    if vid in to:
        raise EditorError(f"{vid} cannot open itself")
    op = {
        "op": "close", "vertex": vid,
        "answer": _need(f, "answer"), "source": _need(f, "source"),
        "falsifier": f.get("falsifier", "").strip() or None,
        "to": to,
        "date": meta.get("date") or _date.today().isoformat(),
        # Provenance: this buffer is org, so the views may convert its
        # emphasis. Applies to whatever the op writes — including an op that
        # started life in the web form and was revised here, which makes it
        # org by virtue of having been edited as org.
        "format": "org",
    }
    if to and not op["falsifier"]:
        raise EditorError(
            "Falsifier is empty and this decision opens "
            f"{', '.join(to)} — state what evidence would reopen it, or open nothing"
        )
    if f.get("summary", "").strip():
        op["summary"] = f["summary"].strip()
    return [op]


def _parse_reopen(meta: dict, f: dict) -> list[dict]:
    op = {"op": "reopen", "vertex": meta.get("vertex"), "why": _need(f, "why"),
          "format": "org"}
    if f.get("summary", "").strip():
        op["summary"] = f["summary"].strip()
    return [op]


def _parse_add(g: Graph, f: dict) -> list[dict]:
    vid = _need(f, "id")
    if not re.fullmatch(r"D\d+", vid):
        raise EditorError(f"malformed id {vid!r} — expected something like D07")
    if vid in g.vertices:
        raise EditorError(f"{vid} already exists")
    area = _need(f, "area")
    if area not in g.areas:
        raise EditorError(f"unknown area {area!r} — one of: {', '.join(g.areas)}")
    status = f.get("status", "").strip() or "OPEN"
    # Checked here as well as by the `vet_all` the caller runs, because this
    # message names the field the user typed — `** Status` — where a refusal
    # from the staging layer names an op they never wrote. The same reason the
    # id and the area are checked here rather than left to `vet`.
    fault = status_fault(status, g.vertices, of=vid)
    if fault:
        raise EditorError(f"Status: {fault} — one of {', '.join(STATUSES)}")
    op = {"op": "add_vertex", "id": vid, "title": _need(f, "title"),
          "area": area, "status": status}
    if f.get("note", "").strip():
        op["note"] = f["note"].strip()
        op["format"] = "org"
    ops = [op]
    parents = [p.strip() for p in f.get("after", "").split(",") if p.strip()]
    # A block is a dependency; see the same addition in `cli.add`. Staged here
    # too so the composed batch reads as what it does, rather than gaining an
    # edge at apply time that the buffer never mentioned.
    if op["status"].startswith("BLOCKED:"):
        blocker = op["status"].split(":", 1)[1]
        if blocker and blocker not in parents:
            parents.append(blocker)
    for parent in parents:
        if parent not in g.vertices:
            raise EditorError(f"unknown parent {parent!r} in After")
        ops.append({"op": "add_edge", "from": parent, "to": [vid]})
    return ops


# ---- launching -----------------------------------------------------------


def resolve_editor() -> str:
    """`$DG_EDITOR` > `$VISUAL` > `$EDITOR` > emacs.

    Emacs is the fallback, so an unconfigured user gets the full experience,
    while someone who has set `$EDITOR` keeps their editor — ignoring it would
    be the surprising choice for a command-line tool.
    """
    for var in ("DG_EDITOR", "VISUAL", "EDITOR"):
        val = os.environ.get(var)
        if val and val.strip():
            return val.strip()
    return "emacs"


def is_emacs(editor: str) -> bool:
    argv = shlex.split(editor)
    return bool(argv) and "emacs" in Path(argv[0]).name


def command(editor: str, path: Path) -> list[str]:
    """The argv to run. Only emacs is handed the elisp."""
    override = os.environ.get("DG_EDIT_CMD")
    if override:
        argv = shlex.split(override)
        return [a.replace("{file}", str(path)) for a in argv] + (
            [] if any("{file}" in a for a in argv) else [str(path)])
    argv = shlex.split(editor)
    if is_emacs(editor):
        argv += ["-l", str(ELISP)]
    return argv + [str(path)]


def resolve_gui_editor() -> str:
    """The editor for the web app: `$DG_GUI_EDITOR`, else emacs.

    Deliberately does **not** consult `$EDITOR`/`$VISUAL`. Those name a terminal
    editor by convention, and the web path has no terminal to lend it — the
    server process holds whatever stdio `dg serve` was started with, so `vim`
    would either die immediately or block the request forever with nowhere to
    draw. Better to run emacs than to honour a variable that cannot work here.
    """
    return os.environ.get("DG_GUI_EDITOR", "").strip() or "emacs"


def _windowed() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def gui_available() -> bool:
    """Whether `launch_gui` can work at all — what the web app asks before it
    offers the button."""
    return _windowed() or bool(os.environ.get("DG_EDIT_CMD"))


def launch_gui(path: Path) -> int:
    """Launch a windowed editor and block — the launcher the web app passes.

    The check is up front because the failure it prevents is the worst one
    available: an editor with no window and no terminal leaves the browser's
    request hanging with no way to tell it why.
    """
    if not os.environ.get("DG_EDIT_CMD") and not _windowed():
        raise EditorError(
            "no DISPLAY or WAYLAND_DISPLAY — the browser can only drive a "
            "windowed editor. Compose from the terminal instead: "
            "`dg decide <id> --edit`"
        )
    return launch(path, editor=resolve_gui_editor())


def launch(path: Path, editor: str | None = None) -> int:
    """Run the editor and block. The seam tests replace."""
    editor = editor or resolve_editor()
    argv = command(editor, path)
    env = dict(os.environ, DG_PROJECT=str(path.parent))
    try:
        return subprocess.call(argv, env=env)
    except FileNotFoundError:
        raise EditorError(
            f"{shlex.split(editor)[0]!r} not found — set $DG_EDITOR, or pass the "
            f"fields as flags (--answer/--source/…)"
        ) from None


def _acquire_buffer(path: Path) -> Path:
    """Take the project's one compose buffer, or say who already holds it.

    There is a single buffer per project (the `COMMIT_EDITMSG` property), and
    the web app has always refused a second session rather than overwrite a
    buffer someone is typing in. This extends the same refusal across
    processes — a second `dg decide --edit` in another terminal, or a CLI
    session racing the web app — via a pid-stamped lock file beside the
    buffer. A lock whose pid is gone is a crashed session and is stolen.
    """
    lock = path.with_name(path.name + ".lock")
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                pid = int(lock.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                raise EditorError(
                    f"a compose session may already be open ({lock} exists and "
                    f"is unreadable) — finish it, or delete the file if it is "
                    f"left over from a crash"
                ) from None
            if _alive(pid):
                raise EditorError(
                    f"a compose session is already open for this project "
                    f"(pid {pid}) — finish it with C-c C-c or abort it with "
                    f"C-c C-k; if it is dead, delete {lock}"
                )
            lock.unlink(missing_ok=True)  # crashed session; take over
            continue
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return lock
    raise EditorError(f"could not take {lock} — try again")


#: One implementation, shared with the tray lock in `dgraph/project.py`: both
#: locks decide whether to steal by asking whether the holder still exists, and
#: two answers to that question is how two locks come to disagree.
_alive = project.alive


def compose(
    g: Graph,
    kind: str,
    *,
    vertex: str | None = None,
    seed: dict | None = None,
    index: int | None = None,
    op: dict | None = None,
    launcher=None,
) -> list[dict]:
    """Render a buffer, hand it to the editor, and parse what comes back."""
    path = project.find().edit
    if op is not None and index is not None:
        text = render_op(g, index, op)
    elif kind == "add_vertex":
        text = render_add(g, seed)
    else:
        text = RENDERERS[kind](g, vertex, seed)
    lock = _acquire_buffer(path)
    try:
        path.write_text(text, encoding="utf-8")
        before = path.read_text(encoding="utf-8")

        code = (launcher or launch)(path)
        if code != 0:
            raise EditorAbort(f"editor exited with status {code} — nothing staged")

        after = path.read_text(encoding="utf-8")
        if after == before:
            raise EditorAbort("buffer was not changed — nothing staged")
        return parse(after, g=g, expect_kind=kind, expect_vertex=vertex,
                     expect_index=index)
    finally:
        lock.unlink(missing_ok=True)
