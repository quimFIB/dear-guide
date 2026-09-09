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

Prose comes back as typed - full org is fine in the org buffer, markdown in
the markdown one (`dgraph/mdbuffer.py`) - and is tagged with its dialect. The
views convert for display; see `dgraph/orgmd.py`, which also says what
happens when the two dialects meet on one record.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import textwrap
from datetime import date as _date
from pathlib import Path

from dgraph import areas, mdbuffer, orgmd, pending, project, ranges
from dgraph import tags as _tags
from dgraph.model import Graph, probe_fault, status_fault, idkey

ELISP = Path(__file__).resolve().parent / "elisp" / "dgraph.el"

STATUSES = ("DECIDED", "OPEN", "REOPENED", "PROVISIONAL")


class EditorAbort(RuntimeError):
    """The user declined: empty buffer, untouched template, or a failed editor."""


class EditorError(RuntimeError):
    """The buffer came back malformed or incomplete. Nothing is staged."""


# ---- template ------------------------------------------------------------

#: Comment lines are stripped, git-style. `#+` keywords and drawer lines are
#: not comments and must survive - getting this regex wrong silently eats the
#: metadata drawer.
_COMMENT = re.compile(r"^[ \t]*#(?!\+)(?: .*)?$\n?", re.M)

_HEAD = re.compile(r"^(\*+)[ \t]+(.*?)[ \t]*$", re.M)
_PROP = re.compile(r"^[ \t]*:([A-Z_]+):[ \t]*(.*?)[ \t]*$", re.M)
_CHECKED = re.compile(r"^[ \t]*[-+*][ \t]+\[[Xx]\][ \t]+(D\d+)", re.M)

#: The keys every buffer has. `C-c C-o` is org's own `org-open-at-point`, which
#: the `dg:` link type makes work everywhere.
_KEYS_ALWAYS = "#   C-c C-o  follow a dg: link"

#: ...and the two that only a buffer with premises to walk can offer.
#: `dgraph-parent` and `dgraph-ancestors` resolve `:DGRAPH_VERTEX:`, so a
#: buffer without one — `dg add`, and both task buffers — had them bound to an
#: error and advertised in its own header. Interface audit F8: the header is
#: the only documentation these keys have, and it was documenting what they
#: could not do. Written by `_header`'s caller rather than assumed, so a new
#: buffer kind has to say which it is.
#:
#: Under `C-c d` rather than `C-c C-<letter>`, because this is an org buffer
#: and the mode namespace is org's: the old `C-c C-v` shadowed the entire
#: `org-babel` prefix map. `dgraph-prefix` in `dgraph.el` has the argument.
_KEYS_WALK = "      C-c d p  premise      C-c d a  ancestors"

#: `dgraph-visit` is the one navigation key that does **not** need a vertex:
#: it prompts with completion over the whole graph and shows whatever is
#: picked. So it was gated on the wrong condition — bound beside the two above
#: and therefore absent from `dg add` and both task buffers, which is where
#: looking a decision up is *most* useful, since those are the buffers with no
#: premise to walk to. What it does need is a decision store to read, because
#: `dgraph.el` reaches it through `dg export`; a tasks-only project has none,
#: and advertising a key that would error is the F8 shape again.
_KEYS_VISIT = "\n#   C-c d v  look up any decision"

_HEADER = """\
# -*- mode: org; -*-
# {title}
# Lines starting with "# " are ignored. Only the "Input" subtree is read back;
# Context is reference material and cannot change what gets staged.
#   C-c C-c  save and stage        C-c C-k  abort, stage nothing
{keys}
# An empty Input aborts, exactly like an empty git commit message.
# A "*" (heading) or "#" (comment) at column 0 ends a field. Escape as ",*" / ",#".
#+TODO: {todo} | {done}
:PROPERTIES:
{props}
:END:
"""


def _props(d: dict[str, str]) -> str:
    return "\n".join(f":DGRAPH_{k.upper()}: {v}" for k, v in d.items() if v is not None)


def _header(title: str, *, decisions: bool = True, **props: str) -> str:
    """The buffer preamble. `decisions` says whether a decision store exists.

    Two independent conditions, because the keys need different things. The
    walk keys need a *vertex* — `dg add` composes one that does not exist yet,
    so it has no premises to offer. `visit` needs only a *store*, which every
    decision buffer has and a task buffer in a tasks-only project does not.
    """
    return _HEADER.format(
        title=title,
        keys=_KEYS_ALWAYS
        + (_KEYS_WALK if props.get("vertex") else "")
        + (_KEYS_VISIT if decisions else ""),
        todo=" ".join(s for s in STATUSES if s != "DECIDED"),
        done="DECIDED",
        props=_props(props),
    )


def _escape(body: str) -> str:
    """The inverse of `_body`'s comma-unescape, applied to seeded content.

    A stored line whose first non-blank character is `*` (an org heading) or
    `#` (a comment) would otherwise be read as the end of the field or stripped
    — and `#` also decides, one module over, whether `mdbuffer.render` treats
    the line as a comment on the way to markdown. One comma is added before
    such a run and `_body` removes exactly one, so any stored text — including
    one already starting with `,*` or `,#` — round-trips unchanged. D101.
    """
    return re.sub(r"^([ \t]*)(,*[#*])", r"\1,\2", body, flags=re.M)


def _field(name: str, hint: str = "", body: str = "") -> str:
    # The value slot sits directly under the heading, above the hint, so the
    # first line a cursor lands on is where the value goes. The hint follows
    # as a comment. `_body` strips comments in any position, so the order is
    # free to the parser — and putting the value first is what keeps a
    # markdown editor with conceal on (which hides the `<!-- -->` delimiters)
    # from making the comment interior look like the only place to type.
    # D100, audit AA-F5.
    out = [f"** {name}", _escape(body.rstrip("\n")) if body else ""]
    if hint:
        out += [f"# {ln}" for ln in hint.splitlines()]
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
    # Every piece of prose below is shown as org, whatever dialect it was
    # typed in: the buffer is an org file, and `mdbuffer` converts the whole
    # of it to markdown for the other editors — which is only right if what
    # it is given is uniformly org. `orgmd.convert` is the identity for org.
    if v.note:
        out.append(_quote(orgmd.convert(v.note, v.format, "org")))
    if v.rule:
        # Read back where the answer is composed (`D75`).
        out.append("   rule for settling:")
        out.append(_quote(orgmd.convert(v.rule, v.format, "org"), "      "))

    chain = _ctx.chain(g, vid)
    by_id = {p.id: p for p in chain}

    for parent in deps:
        p = by_id[parent]
        out.append(f"** {p.status.split(':')[0]} {_node_line(g, parent)}")
        if p.answer is not None:
            out.append("   :PROPERTIES:")
            out.append(f"   :FALSIFIER: "
                       f"{orgmd.convert(p.falsifier, p.format, 'org') or '—'}")
            out.append(f"   :SOURCE:    {p.source or '—'}")
            out.append(f"   :DATE:      {p.date or '—'}")
            out.append("   :END:")
            out.append(_quote(orgmd.convert(p.answer, p.format, "org")))
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
            out.append(f"   - “{orgmd.convert(h.summary, h.format, 'org')}” → "
                       f"{h.replaced_by or '(undecided)'}")
            if h.why:
                out.append(_quote(orgmd.convert(h.why, h.format, "org"), "     "))
    return "\n".join(out) + "\n"


def render_close(g: Graph, vid: str, seed: dict | None = None) -> str:
    seed = seed or {}
    v = g.vertices[vid]
    linked = set(g.children(vid))
    boxes = []
    for other in sorted(g.vertices, key=idkey):
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
        + _field("Probe",
                 "Optional. The falsifier's mechanical twin, as JSON:\n"
                 '{"kind": "<domain>.<name>", "args": {...}}. Archived with '
                 "the answer on reopen.",
                 _probe_text(seed.get("probe")))
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


def render_reprobe(g: Graph, vid: str, seed: dict | None = None) -> str:
    """The buffer for `dg reprobe --edit`: just the probe, as JSON. Reuses the
    `## Probe` field the close/add buffers already carry (`D103`) so a probe —
    structured JSON, the worst thing to type on a flag — can be composed in an
    editor with the same hint and the same `_parse_probe` validation."""
    seed = seed or {}
    v = g.vertices[vid]
    return (
        _header(f"dg reprobe {vid} — {v.title}", op="reprobe", vertex=vid,
                project=str(project.find().root))
        + "\n* Input\n"
        + _field("Probe",
                 "The new rule for settling it, as JSON:\n"
                 '{"kind": "<domain>.<name>", "args": {...}}. Appended and '
                 "dated; the earlier probes stay.",
                 _probe_text(seed.get("probe")))
        + "\n" + _context(g, vid)
    )


def amend_seed(g: Graph, vid: str) -> dict:
    """The record as the amend buffer's seed: the fields amend may correct and
    the dialect they are stored in, so `seed_in` can show them in the buffer's.
    `compose` builds it where no seed was given."""
    v = g.vertices[vid]
    return {"title": v.title, "area": v.area, "note": v.note or "",
            "rule": v.rule or "", "tags": list(v.tags or ()),
            "format": v.format}


def render_amend(g: Graph, vid: str, seed: dict | None = None) -> str:
    """The buffer for `dg amend --edit`: the fields amend may correct, prefilled
    with the record as it stands (`D103`). Parsed back to a `set_fields` op that
    carries only what changed — an answer is never among them, the line amend is
    drawn along. Reuses the `## Note` field the add buffer already renders.

    `seed` is the record as `seed_in` converted it — prose in the buffer's
    dialect, whatever the record's — and is what is rendered. Reading the
    record here instead ignored the conversion `compose` had just done, so a
    markdown buffer showed org `*bold*` and what came back was tagged
    markdown: every span the person left alone, relabelled. Audit `AC-F4`.
    """
    s = seed if seed is not None else amend_seed(g, vid)
    return (
        _header(f"dg amend {vid}", op="amend", vertex=vid,
                project=str(project.find().root))
        + "\n* Input\n"
        + _field("Title", "How the question is referred to — not a claim it "
                          "makes.", s["title"])
        + _field("Area", "One area, or a new one; areas accumulate.", s["area"])
        + _field("Tags", "Comma-separated words this is filed under beside its "
                         "area. The whole set: empty drops every tag.",
                 ", ".join(s.get("tags") or ()))
        + _field("Note", "What is undecided, and why. Prose; may be emptied.",
                 s.get("note") or "")
        + _field("Rule", "What would settle this, in prose. May be emptied.",
                 s.get("rule") or "")
        + "\n" + _context(g, vid)
    )


def next_id(g: Graph) -> str:
    """The next unused `D` id. `task_editor.next_id`'s twin, for this store.

    A function rather than an expression inside `render_add`, because the org
    buffer is no longer the only door that has to prefill it: `/api/graph`
    sends it to the browser's new-decision form. Two doors offering different
    "next" ids is the kind of disagreement this codebase spends its comments
    preventing — and it is why this clone's grant is read here rather than in
    each door, so that all of them offer an id from the same range.

    `max(stored) + 1` where there is no grant, which is every single-writer
    project. Raises `ranges.RangeError` on a grant that is used up: the caller
    decides what to do about it, since one of them is a browser payload and one
    is a terminal.
    """
    n = ranges.next_number(
        "D", (int(v[1:]) for v in g.vertices if v[1:].isdigit()))
    return f"D{n:02d}"


def next_offer(stored: Graph | None = None) -> str:
    """The id a **form** should prefill: `next_id` over the store *and the tray*.

    `next_id` stays a function of a graph, because plenty of callers already
    hold the effective one. This is the different question — *what may I offer
    somebody who is about to stage* — and every door that prefills has it.

    It is composed here for the same reason the grant is read in `next_id`
    rather than in each door, and the docstring there already says it: a door
    that has to remember something is a door that will not. Three doors ask
    this (`dg add`'s refusal, `dg range`'s report, the browser's new-decision
    form) and the browser asked it of the store alone — so it offered an id
    another writer had already staged, which `stage` then refused, since that
    vets against exactly the preview this reads. Audit `G-F5`.

    **`render_add` is the fourth site that prefills and must not call this.**
    It is already handed the effective graph by both of its doors — `cli.add`
    passes `eff`, `/api/compose` passes `pending.preview(g)` — so previewing
    again would apply the tray twice. That is the reason for the parameter's
    name: what this takes is the **stored** graph, and a caller holding an
    effective one already has its answer.

    Raises `ranges.RangeError` on a used-up grant, like `next_id`, and
    `pending.ApplyError` where the tray no longer applies — both are things the
    caller must say rather than swallow, and both are already handled by the
    three doors' existing report helpers.
    """
    return next_id(pending.preview(
        Graph.load() if stored is None else stored))


def _area_hint(counts: dict) -> str:
    """The Area field's hint, naming the areas in use beside the slot. The
    same list sits under Context, which is where nobody looked (T131)."""
    names = ", ".join(counts)
    if not names:
        return "One in use, or a new one — areas accumulate."
    return textwrap.fill(f"In use: {names}. One of those, or a new one — "
                         f"areas accumulate.", 72)


def render_add(g: Graph, seed: dict | None = None) -> str:
    seed = seed or {}
    nxt = next_id(g)
    return (
        _header("dg add — a new decision vertex", op="add_vertex",
                project=str(project.find().root))
        + "\n* Input\n"
        + _field("Id", f"Like D07. Next unused: {nxt}", seed.get("id") or nxt)
        + _field("Title", "One line: the question this decision answers.",
                 seed.get("title", ""))
        + _field("Area", _area_hint(areas.counts(g.areas, g.vertices.values())),
                 seed.get("area", ""))
        + _field("Tags", "Optional. Comma-separated words this is filed under "
                         "beside its area.",
                 ", ".join(seed.get("tags", ())))
        + _field("Status", f"One of: {', '.join(STATUSES)}. Default OPEN.\n"
                           "A vertex waits on whatever in After is unsettled.",
                 seed.get("status", "OPEN"))
        + _field("After", "Optional. Comma-separated parents that open this.",
                 ", ".join(seed.get("after", [])) if seed.get("after") else "")
        + _field("Note", "Optional prose for a decision with no answer yet.",
                 seed.get("note", ""))
        + _field("Rule", "Optional. What would settle this, in prose — read "
                         "back at `dg decide`.", seed.get("rule", ""))
        + _field("Probe",
                 "Optional. The rule for settling this, as JSON:\n"
                 '{"kind": "<domain>.<name>", "args": {...}}. Appended and '
                 "dated; `dg reprobe` changes it.",
                 _probe_text(seed.get("probe")))
        + "\n* Context\n** Areas in use\n"
        + ("".join(f"   - {a}  ({n})\n" for a, n in
                   areas.counts(g.areas, g.vertices.values()).items())
           or "   (none yet — the first record starts the vocabulary)\n")
        + "** Frontier (still open or blocked)\n"
        + "".join(f"   - {_node_line(g, f)} {g.vertices[f].status}\n"
                  for f in g.frontier())
    )


RENDERERS = {"close": render_close, "reopen": render_reopen,
             "add_vertex": render_add, "reprobe": render_reprobe,
             "amend": render_amend}

#: The prose each buffer seeds and reads back — the fields a record's
#: `format` covers, by op. `source` is not prose: the views never convert it.
SEED_PROSE = {"close": ("answer", "falsifier", "summary"),
              "reopen": ("why", "summary"),
              "add_vertex": ("note", "rule"),
              "reprobe": (),  # a probe is JSON, not prose
              "amend": ("note", "rule")}


def seed_in(seed: dict | None, kind: str, dialect: str) -> dict | None:
    """`seed` as the buffer's dialect, so what the person sees is what the
    buffer claims. A seed's `format` says what it was typed as — `"org"`
    from an op composed in emacs, nothing from the web form, the flags or
    an op composed in markdown. Converted here, once, for both doors: a
    buffer that showed org prose and tagged what came back as markdown
    would have relabelled every `*span*` in it."""
    if not seed:
        return seed
    tag = DIALECTS[dialect].tag
    frm = seed.get("format")
    if (frm == "org") == (tag == "org"):
        return seed
    out = dict(seed)
    for f in SEED_PROSE.get(kind, ()):
        if out.get(f):
            out[f] = orgmd.convert(out[f], frm, tag)
    out["format"] = tag
    return out


def supersedes(kind: str, op: dict):
    """What a revision of `op` takes out of the tray, or None for "nothing".

    Only an `add_vertex` supersedes anything: `close` and `reopen` each parse
    back to exactly one op, so a revision of either is a swap and nothing else.
    A vertex's parents are edges, staged as separate ops, and re-stating them in
    the buffer has to retract the old ones.

    An `add_edge` naming other vertices as well keeps them. Nothing in the tool
    stages one — every producer writes a single target — but `_apply_one` unions
    targets and the web API takes an op as data, so dropping such an op whole
    would lose an attachment the edit never mentioned.

    Lives here rather than in `dg edit` because the browser revises staged ops
    too, and this is the rule that decides what a revision *retracts* — a
    second door re-deriving it is how the tray comes to hold both readings of
    what a vertex rests on and apply their union.
    """
    if kind != "add_vertex":
        return None
    vid = op.get("id")

    def supersede(other: dict) -> dict | None:
        if other.get("op") != "add_edge" or vid not in (other.get("to") or []):
            return other
        rest = [t for t in other["to"] if t != vid]
        return {**other, "to": rest} if rest else None

    return supersede



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
    text = re.sub(r"^([ \t]*),(,*[#*])", r"\1\2", text, flags=re.M)
    return mdbuffer.trim(text)      # blank lines at the ends, never a dedent (D104)


def _meta(text: str) -> dict[str, str]:
    end = text.find(":END:")
    head = text[:end] if end != -1 else text
    return {m.group(1)[len("DGRAPH_"):].lower(): m.group(2)
            for m in _PROP.finditer(head) if m.group(1).startswith("DGRAPH_")}


class Dialect:
    """One reading of the buffer: how its metadata, its fields and their
    bodies are found, and what the prose it yields is tagged as.

    The org reading is this module's three primitives; the markdown reading
    is `dgraph/mdbuffer.py`'s. `tag` is the `format` an op claims — org for
    the org buffer, nothing for markdown, which is what untagged prose means
    in the store (`dgraph/orgmd.py`). `mark1`/`mark2` are how a refusal
    spells the headings the person typed.
    """

    def __init__(self, name, mark1, mark2, meta, sections, body, tag, no_op):
        self.name, self.mark1, self.mark2 = name, mark1, mark2
        self.meta, self.sections, self.body, self.tag = meta, sections, body, tag
        self.no_op = no_op          # the refusal for a buffer naming no op


ORG = Dialect("org", "* Input", "**", _meta, _sections, _body, "org",
              "no :DGRAPH_OP: in the buffer's properties drawer")
MARKDOWN = Dialect("markdown", "# Input", "##",
                   mdbuffer.meta, mdbuffer.sections, mdbuffer.body, None,
                   "no `op:` in the buffer's front matter")
DIALECTS = {"org": ORG, "markdown": MARKDOWN}


def _forced_dialect() -> str | None:
    """`$DG_EDIT_FORMAT`, for a person whose non-emacs editor speaks org, or
    who wants markdown in emacs — and for tests, which pin it."""
    val = os.environ.get("DG_EDIT_FORMAT", "").strip().lower()
    if not val:
        return None
    if val not in DIALECTS:
        raise EditorError(f"$DG_EDIT_FORMAT is {val!r} — org or markdown")
    return val


def cli_dialect() -> str:
    """The dialect `dg … --edit` composes in: org for emacs, markdown for any
    other editor. The buffer's format follows the editor because the editor
    is what makes a format pleasant: org outside emacs is a file whose
    headings and checkboxes nothing understands, and whose `*emphasis*`
    the store then converts as org's."""
    forced = _forced_dialect()
    if forced:
        return forced
    cmd = os.environ.get("DG_EDIT_CMD", "").strip()
    return "org" if is_emacs(cmd or resolve_editor()) else "markdown"


def gui_dialect() -> str:
    """The same rule for the browser's door, over the editor it resolves."""
    forced = _forced_dialect()
    if forced:
        return forced
    return "org" if gui_editor()["emacs"] else "markdown"


ALLOWED = {
    "close": {"answer", "source", "falsifier", "opens", "summary", "probe"},
    "reprobe": {"probe"},
    "amend": {"title", "area", "note", "rule", "tags"},
    "reopen": {"why", "summary"},
    "add_vertex": {"id", "title", "area", "status", "after", "note", "probe",
                   "rule", "tags"},
}


def parse(
    text: str,
    *,
    g: Graph,
    expect_kind: str | None = None,
    expect_vertex: str | None = None,
    expect_index: int | None = None,
    new_area: bool = False,
    explain=None,
    dialect: str = "org",
) -> list[dict]:
    """Buffer -> op dicts ready for `pending.stage`. Raises rather than guessing.

    `dialect` names the reading (`DIALECTS`): the org buffer or the markdown
    one. The two differ only in how the buffer is *read* — every check below
    is the same — and in the `format` the prose is tagged with.

    `explain(id)`, if given, answers why a record `g` lacks is unknown here —
    `dg edit` passes the tray's own account (*added by an act staged after
    this one*, `D97`), which the parser cannot know and the refusal should
    say instead of *unknown*.
    """
    if not text.strip():
        raise EditorAbort("empty buffer — nothing staged")

    d = DIALECTS[dialect]
    meta = d.meta(text)
    kind = meta.get("op")
    if not kind:
        raise EditorError(d.no_op)
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

    sections = d.sections(text)
    unknown = sorted(k for k in sections if k not in ALLOWED.get(kind, set()))
    if unknown:
        # quoted back as the user spelled them, so a typo is recognisable
        names = ", ".join(f"{d.mark2} {sections[u][0]}" for u in unknown)
        raise EditorError(f"unknown field(s) under Input: {names}")
    raw = {k: v[1] for k, v in sections.items()}
    f = {k: d.body(v) for k, v in raw.items()}
    if not any(v for k, v in f.items() if k != "opens"):
        raise EditorAbort("template came back untouched — nothing staged")

    if kind == "close":
        return _parse_close(g, meta, f, raw, explain=explain, tag=d.tag)
    if kind == "reopen":
        return _parse_reopen(meta, f, tag=d.tag)
    if kind == "add_vertex":
        return _parse_add(g, f, new_area=new_area, explain=explain, tag=d.tag)
    if kind == "reprobe":
        return _parse_reprobe(meta, f)
    if kind == "amend":
        return _parse_amend(g, meta, f, tag=d.tag)
    raise EditorError(f"cannot compose a {kind!r} op")


def _parse_amend(g: Graph, meta: dict, f: dict, tag: str | None = "org") -> list[dict]:
    """A `set_fields` op carrying only the fields the buffer changed. Title and
    area cannot be blanked; note and rule can be emptied to clear them. Nothing
    changed is an abort, like an untouched template. `format` rides along when a
    prose field (note, rule) was touched, since the buffer's prose is the tag's
    dialect — the same rule `_parse_close` follows for an answer."""
    vid = meta.get("vertex")
    if vid not in g.vertices:
        raise EditorError(f"unknown vertex {vid!r}")
    v = g.vertices[vid]
    op: dict = {"op": "set_fields", "vertex": vid}
    for field, current in (("title", v.title), ("area", v.area),
                           ("note", v.note or ""), ("rule", v.rule or "")):
        new = _val(f, field)
        if field in ("title", "area") and not new:
            raise EditorError(f"{field.capitalize()} is empty — a {field} is "
                              f"required and cannot be blanked here")
        if field in ("note", "rule"):
            # Judged against the record as the buffer showed it — converted
            # into the buffer's dialect, as `render_amend` seeded it — so a
            # note left alone is not staged as a rewrite of itself (`AC-F4`).
            current = mdbuffer.trim(orgmd.convert(current, v.format, tag) or "")
        else:
            current = (current or "").strip()
        if new != current:
            op[field] = new if new or field in ("title", "area") else None
    # Tags are the whole set, as `dg amend --tag/--untag` stages them: the
    # buffer shows what the record holds and what comes back replaces it. The
    # slot was missing from this buffer while the flags and the add buffer
    # both had it — audit `AD-F3`.
    tags = _tags.clean(f["tags"]) if f.get("tags", "").strip() else []
    if tags != list(v.tags or ()):
        op["tags"] = tags
    if len(op) == 2:
        raise EditorAbort("nothing changed — nothing staged")
    if tag and any(k in op for k in ("note", "rule")):
        op["format"] = tag
    return [op]


def _parse_reprobe(meta: dict, f: dict) -> list[dict]:
    """A reprobe is the new rule, as JSON. Empty is an abort, like any
    untouched field. `_parse_probe` refuses malformed JSON by the field's
    name, the same as the close buffer's probe."""
    text = f.get("probe", "").strip()
    if not text:
        raise EditorError("Probe is empty — a reprobe is the new rule for "
                          "settling, as JSON")
    return [{"op": "reprobe", "vertex": meta.get("vertex"),
             "probe": _parse_probe(text)}]


#: The fields whose bytes are kept as typed — leading indentation included
#: (`D104`). Everything else is a one-line value and is stripped.
PROSE_FIELDS = frozenset({"answer", "falsifier", "summary", "why", "note",
                          "rule", "outcome", "done when"})


def _val(f: dict[str, str], name: str) -> str:
    """A parsed field's value: prose as `_body` left it, a one-line value
    stripped. `""` when absent or blank."""
    val = f.get(name, "")
    if not val.strip():
        return ""
    if name in PROSE_FIELDS:
        return val
    one = val.strip()
    if "\n" in one:
        # Two lines in a one-line field is the person having typed under the
        # wrong heading, or over a hint — `D211\nTEST1` came back from vim
        # with the next field's value under this one. Say where the value
        # goes, as the empty case does (D100), rather than quote the mess
        # as *malformed*. T132, met running T125.
        n = len(one.splitlines())
        raise EditorError(
            f"{name.capitalize()} has {n} lines — one value, on the line "
            f"directly under the `{name.capitalize()}` heading, above the "
            f"hint; the rest belongs under its own heading.")
    return one


def _need(f: dict[str, str], name: str) -> str:
    val = _val(f, name)
    if not val:
        # Name the slot: the value goes on the line under the heading, above
        # the hint. A person can type among the hint by mistake, where the
        # parser strips it — so an empty field says where the value belongs
        # rather than only that it is empty. Dialect-agnostic: the org hint
        # is a `#` line, the markdown hint a `>` line, and in both the value
        # is the line under the heading. D100, audit AA-F5.
        raise EditorError(
            f"{name.capitalize()} is empty — nothing staged. The value goes "
            f"on the line directly under the `{name.capitalize()}` heading, "
            f"above the hint.")
    return val


def _unknown(fallback: str, ids: list[str], explain) -> EditorError:
    """The refusal for ids `g` lacks: the caller's account where it has one,
    the plain *unknown* otherwise."""
    said = [w for w in (explain(i) for i in ids) if w] if explain else []
    return EditorError("\n".join(said) if said else fallback)


def _parse_close(g: Graph, meta: dict, f: dict, raw: dict,
                 explain=None, tag: str | None = "org") -> list[dict]:
    vid = meta.get("vertex")
    if vid not in g.vertices:
        raise EditorError(f"unknown vertex {vid!r}")
    picked = _CHECKED.findall(raw.get("opens", ""))
    unknown = [t for t in picked if t not in g.vertices]
    if unknown:
        raise _unknown(f"unknown target(s): {', '.join(unknown)}", unknown, explain)
    # Already-linked children survive whatever the checkbox says: `_apply_one`
    # unions op["to"] with the edge's targets, so the buffer must not imply a
    # dependency can be dropped here.
    to = sorted(set(picked) | set(g.children(vid)))
    if vid in to:
        raise EditorError(f"{vid} cannot open itself")
    op = {
        "op": "close", "vertex": vid,
        "answer": _need(f, "answer"), "source": _need(f, "source"),
        "falsifier": _val(f, "falsifier") or None,
        "to": to,
        "date": meta.get("date") or _date.today().isoformat(),
    }
    # Provenance: an org buffer's prose is org, so the views may convert its
    # emphasis. Applies to whatever the op writes — including an op that
    # started life in the web form and was revised here, which makes it org
    # by virtue of having been edited as org. A markdown buffer claims
    # nothing, because untagged is what markdown is in the store.
    if tag:
        op["format"] = tag
    if to and not op["falsifier"]:
        raise EditorError(
            "Falsifier is empty and this decision opens "
            f"{', '.join(to)} — state what evidence would reopen it, or open nothing"
        )
    if _val(f, "summary"):
        op["summary"] = _val(f, "summary")
    if f.get("probe", "").strip():
        op["probe"] = _parse_probe(f["probe"])
    return [op]


def _probe_text(probe: dict | None) -> str:
    """A probe as the buffer shows it: pretty JSON, or nothing."""
    return (json.dumps(probe, indent=2, ensure_ascii=False, sort_keys=True)
            if probe else "")


def _parse_probe(text: str) -> dict:
    """`** Probe`'s body back into a value, refused by the field's name.

    JSON rather than org, because a probe is a typed value a domain reads
    and not prose a person does: the buffer shows it as data so what is
    saved is exactly what was shown.
    """
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EditorError(f"Probe is not JSON: {exc.msg} at line {exc.lineno}, "
                          f"column {exc.colno}")
    fault = probe_fault(value)
    if fault:
        raise EditorError(f"Probe: {fault}")
    return value


def _parse_reopen(meta: dict, f: dict, tag: str | None = "org") -> list[dict]:
    op = {"op": "reopen", "vertex": meta.get("vertex"), "why": _need(f, "why")}
    if tag:
        op["format"] = tag
    if _val(f, "summary"):
        op["summary"] = _val(f, "summary")
    return [op]


def _parse_add(g: Graph, f: dict, *, new_area: bool = False,
               explain=None, tag: str | None = "org") -> list[dict]:
    vid = _need(f, "id")
    if not re.fullmatch(r"D\d+", vid):
        raise EditorError(f"malformed id {vid!r} — expected something like D07")
    if vid in g.vertices:
        raise EditorError(f"{vid} already exists")
    area = _need(f, "area")
    # The similarity guard rather than membership, because areas accumulate:
    # a new one is legitimate and a near-miss of an existing one almost never
    # is. Checked here as well as by the `vet_all` the caller runs, for the
    # reason the id and the status are — this message names the field the user
    # typed, `** Area`, where a refusal from the staging layer names an op they
    # never wrote.
    why = pending.refuse_area(
        area, own=areas.counts(g.areas, g.vertices.values()),
        other=areas.stored_counts(project.find().tasks),
        owner=pending.owner(), new_area=new_area)
    if why is not None:
        raise EditorError(f"Area: {why}")
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
    if _val(f, "note"):
        op["note"] = _val(f, "note")
    if _val(f, "rule"):
        op["rule"] = _val(f, "rule")
    # The tag covers both the note and the rule (`Vertex.format`), so it is
    # claimed when either is written — a rule alone used to go untagged.
    if tag and (op.get("note") or op.get("rule")):
        op["format"] = tag
    if f.get("tags", "").strip():
        op["tags"] = _tags.clean(f["tags"])
    if f.get("probe", "").strip():
        op["probe"] = _parse_probe(f["probe"])
        op["date"] = _date.today().isoformat()
    ops = [op]
    parents = [p.strip() for p in f.get("after", "").split(",") if p.strip()]
    for parent in parents:
        if parent not in g.vertices:
            raise _unknown(f"unknown parent {parent!r} in After", [parent], explain)
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
    """The editor string the browser's door runs — see `gui_editor` for the
    whole answer, including why there might be none."""
    return gui_editor()["editor"]


# ---- the browser's door ---------------------------------------------------

def _windowed() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


#: Editors that draw their own window, by the name of the executable. Anything
#: not here is taken to be a terminal editor and is given a terminal window to
#: run in. The bias is deliberate: an unknown windowed editor wrapped in a
#: terminal still works (the terminal sits blank behind it), while an unknown
#: terminal editor run with no terminal dies or hangs, and the request with it.
GUI_EDITORS = frozenset({
    "gvim", "mvim", "gedit", "gnome-text-editor", "kate", "kwrite", "code",
    "codium", "code-insiders", "subl", "sublime_text", "zed", "zeditor",
    "mousepad", "xed", "pluma", "geany", "leafpad", "notepadqq", "atom",
    "mate", "textmate",
})

#: Terminal emulators, with the argv that opens a window, runs the command
#: inside it and **blocks until the command exits**. The blocking is the whole
#: point and the one thing each entry had to get right: `xfce4-terminal -x`
#: without `--disable-server` hands the command to a running instance and
#: returns at once, so the request sees an unchanged buffer and says
#: "cancelled" while the editor is still open. Probed in this order when
#: neither `$DG_TERMINAL` nor `$TERMINAL` says which one to use.
TERMINALS: tuple[tuple[str, list[str]], ...] = (
    ("xfce4-terminal", ["xfce4-terminal", "--disable-server", "-x"]),
    ("gnome-terminal", ["gnome-terminal", "--wait", "--"]),
    ("konsole", ["konsole", "-e"]),
    ("alacritty", ["alacritty", "-e"]),
    ("kitty", ["kitty"]),
    ("wezterm", ["wezterm", "start", "--always-new-process", "--"]),
    ("foot", ["foot"]),
    ("terminator", ["terminator", "--no-dbus", "-x"]),
    ("xterm", ["xterm", "-e"]),
    ("urxvt", ["urxvt", "-e"]),
    ("st", ["st", "-e"]),
)

#: The flags that make emacs draw in the terminal instead of a window.
_EMACS_TTY_FLAGS = frozenset({"-nw", "--no-window-system", "-t", "--tty"})


def _is_terminal_editor(argv: list[str]) -> bool:
    name = Path(argv[0]).name
    if name.startswith("emacs"):
        return any(a in _EMACS_TTY_FLAGS for a in argv[1:])
    return name not in GUI_EDITORS


def _windowed_form(editor: str) -> str | None:
    """`emacs -nw` is emacs told not to draw a window because it was started
    from a terminal. Given a display, the same emacs draws its own — so the
    flag is dropped rather than the whole thing wrapped in a terminal it does
    not need. Only emacs proper: `emacsclient -t` stripped of `-t` reuses a
    frame that may not exist, which is a different program's behaviour, not
    the same one's."""
    argv = shlex.split(editor)
    name = Path(argv[0]).name
    if name.startswith("emacs") and not name.startswith("emacsclient"):
        kept = [a for a in argv[1:] if a not in _EMACS_TTY_FLAGS]
        if len(kept) != len(argv) - 1:
            return shlex.join([argv[0]] + kept)
    return None


def _terminal() -> tuple[list[str] | None, str | None]:
    """The terminal emulator to lend a terminal editor, as an argv prefix and
    the name to say. `$DG_TERMINAL` is taken whole (it must carry its own
    "run this" flag); `$TERMINAL` is the desktop convention and names a bare
    program, looked up in the table or given `-e`, which is what most of them
    take; then the table is probed in order."""
    own = os.environ.get("DG_TERMINAL", "").strip()
    if own:
        argv = shlex.split(own)
        return argv, Path(argv[0]).name
    conv = os.environ.get("TERMINAL", "").strip()
    if conv:
        argv = shlex.split(conv)
        name = Path(argv[0]).name
        for known, prefix in TERMINALS:
            if name == known:
                return prefix, name
        return argv + ["-e"], name
    for name, prefix in TERMINALS:
        if shutil.which(name):
            return prefix, name
    return None, None


def gui_editor() -> dict:
    """What the browser's door will run, or why it cannot.

    The order is the CLI's, with two entries in front of it that exist only
    here: `$DG_EDIT_CMD` (an exact argv, `{file}` substituted) and
    `$DG_GUI_EDITOR` (an editor promised to draw its own window). After those,
    the editor the tool is configured with — `$DG_EDITOR`, `$VISUAL`, `$EDITOR`
    — and finally emacs. A terminal editor is not refused and not run bare:
    it is opened in a terminal window that blocks until it exits, which is the
    thing `$EDITOR` used to be ignored here for lacking.

    Returns the same dict the page reads. `available` is decided *here*, before
    a button is drawn, because the failure a click would otherwise meet is the
    worst one there is: a request that hangs with nothing to type in. `reason`
    is a sentence for the page when the answer is no, and it names the
    variable that would change it — this door's, not the CLI's.
    """
    ans: dict = {"editor": None, "name": None, "emacs": False, "terminal": None,
                 "source": None, "available": False, "reason": None}

    def refuse(why: str) -> dict:
        ans["reason"] = why
        return ans

    def missing(name: str, setting: str) -> dict:
        return refuse(f"{name!r} is not on the server's PATH — {setting}")

    override = os.environ.get("DG_EDIT_CMD", "").strip()
    if override:
        argv = shlex.split(override)
        ans.update(editor=override, name=Path(argv[0]).name,
                   emacs=is_emacs(override), source="DG_EDIT_CMD")
        if not shutil.which(argv[0]):
            return missing(argv[0], "fix $DG_EDIT_CMD")
        ans["available"] = True
        return ans

    if not _windowed():
        return refuse("no DISPLAY or WAYLAND_DISPLAY — the browser can only "
                      "drive a windowed editor. Compose from the terminal "
                      "instead: `dg decide <id> --edit`")

    given = os.environ.get("DG_GUI_EDITOR", "").strip()
    if given:
        argv = shlex.split(given)
        ans.update(editor=given, name=Path(argv[0]).name, emacs=is_emacs(given),
                   source="DG_GUI_EDITOR")
        if not shutil.which(argv[0]):
            return missing(argv[0], "fix $DG_GUI_EDITOR, or unset it to fall "
                           "back to $EDITOR")
        ans["available"] = True
        return ans

    for var in ("DG_EDITOR", "VISUAL", "EDITOR"):
        val = os.environ.get(var, "").strip()
        if not val:
            continue
        editor = _windowed_form(val) or val
        argv = shlex.split(editor)
        ans.update(editor=editor, name=Path(argv[0]).name,
                   emacs=is_emacs(editor), source=var)
        if not shutil.which(argv[0]):
            return missing(argv[0], f"it is what ${var} names; set "
                           f"$DG_GUI_EDITOR to override it for the browser")
        if _is_terminal_editor(argv):
            prefix, term = _terminal()
            if prefix is None:
                return refuse(
                    f"{ans['name']} (from ${var}) is a terminal editor and no "
                    f"terminal emulator was found to open it in — set "
                    f"$DG_TERMINAL (e.g. 'xfce4-terminal --disable-server -x') "
                    f"or $DG_GUI_EDITOR to a windowed editor")
            if not shutil.which(prefix[0]):
                return missing(prefix[0], "fix $DG_TERMINAL or $TERMINAL")
            ans["terminal"] = term
        ans["available"] = True
        return ans

    ans.update(editor="emacs", name="emacs", emacs=True, source="default")
    if not shutil.which("emacs"):
        return refuse("no editor: emacs is not installed and no $EDITOR is "
                      "set — set $EDITOR (a terminal editor is opened in a "
                      "terminal window) or $DG_GUI_EDITOR")
    ans["available"] = True
    return ans


def gui_available() -> bool:
    """Whether `launch_gui` can work at all — what the web app asks before it
    offers the button."""
    return gui_editor()["available"]


def gui_command(path: Path) -> list[str]:
    """The argv the browser's door runs: the editor's own command, inside a
    terminal window when the editor needs one. Raises where `gui_editor` says
    no, with its reason."""
    plan = gui_editor()
    if not plan["available"]:
        raise EditorError(plan["reason"])
    argv = command(plan["editor"], path)
    if plan["terminal"]:
        prefix, _ = _terminal()
        argv = list(prefix) + argv
    return argv


def launch_gui(path: Path) -> int:
    """Launch a windowed editor and block — the launcher the web app passes.

    Everything that could be refused is refused before the process starts,
    because the failure it prevents is the worst one available: an editor with
    no window and no terminal leaves the browser's request hanging with no way
    to tell it why.
    """
    argv = gui_command(path)
    env = dict(os.environ, DG_PROJECT=str(path.parent))
    try:
        return subprocess.call(argv, env=env)
    except FileNotFoundError:
        raise EditorError(
            f"{argv[0]!r} not found — set $DG_GUI_EDITOR (or $DG_EDIT_CMD "
            f"with {{file}}), or compose from the terminal: "
            f"`dg decide <id> --edit`"
        ) from None


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
    lock = path.with_name(project.EDIT_LOCK_NAME)
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
#:
#: The same is true of *releasing*, which took a second finding to see. The two
#: locks differ in how they acquire — this one refuses where `project.held`
#: waits, because a person is typing in the file it guards and making them wait
#: is the wrong answer — and that difference does not reach the release. "Is
#: this still my lock?" has one right answer either way, so it is answered here
#: by the same function. Audit `M-F5`.
_alive = project.alive
_holder = project.holder


def compose(
    g: Graph,
    kind: str,
    *,
    vertex: str | None = None,
    seed: dict | None = None,
    index: int | None = None,
    op: dict | None = None,
    new_area: bool = False,
    launcher=None,
    explain=None,
    dialect: str | None = None,
) -> list[dict]:
    """Render a buffer, hand it to the editor, and parse what comes back.

    `dialect` is the buffer's — `cli_dialect()` unless the caller has
    resolved an editor of its own, as the browser's door has."""
    dialect = dialect or cli_dialect()
    if kind == "amend" and seed is None and op is None:
        if vertex not in g.vertices:
            raise EditorError(f"unknown vertex {vertex!r}")
        seed = amend_seed(g, vertex)
    seed = seed_in(seed, kind, dialect)
    op = seed_in(op, kind, dialect)
    if op is not None and index is not None:
        text = render_op(g, index, op)
    elif kind == "add_vertex":
        text = render_add(g, seed)
    else:
        text = RENDERERS[kind](g, vertex, seed)
    return run(text, lambda after: parse(after, g=g, expect_kind=kind,
                                         expect_vertex=vertex,
                                         expect_index=index,
                                         new_area=new_area,
                                         explain=explain,
                                         dialect=dialect),
               launcher=launcher, dialect=dialect)


def run(text: str, parse_back, *, launcher=None,
        dialect: str = "org") -> list[dict]:
    """The compose *workflow*, with the record type taken out of it.

    Take the project's one buffer, write `text`, run the editor, refuse a
    buffer that came back unchanged, and hand what did come back to
    `parse_back`. Everything here is true of composing anything: the lock, the
    abort rules, and the guarantee that a failed parse stages nothing.

    What is *not* here is the template and the parser, and that is the whole of
    what "a task is not a decision" means at this layer. `compose` above is
    this for decisions; `dgraph/task_editor.py` is this for work, and calls in
    rather than teaching this module what a task is — a module that renders
    both records is one in which the two can drift into each other.

    `text` is always the org rendering; the markdown buffer is derived from
    it here (`mdbuffer.render`), so the renderers stay one per record.
    """
    path = project.find().buffer(dialect)
    if dialect == "markdown":
        text = mdbuffer.render(text)
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
        return parse_back(after)
    finally:
        # Only a lock this process still holds. Both refusals in
        # `_acquire_buffer` tell a person to delete this file, so a lock
        # replaced under a live session is a case that happens — and deleting
        # the *new* holder's lock on the way out admits a second composer to
        # one buffer, which is the single thing this lock exists to prevent.
        # A lock that has become unreadable is left for the person those
        # refusals already ask to look. Audit `M-F5`, and `C-F12` in the lock
        # this shares its answer with.
        if _holder(lock) == os.getpid():
            lock.unlink(missing_ok=True)
