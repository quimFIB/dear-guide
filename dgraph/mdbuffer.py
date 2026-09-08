"""The compose buffer as markdown, for an editor that is not emacs.

`dgraph/editor.py` and `dgraph/task_editor.py` render one buffer, in org, and
`dgraph/elisp/dgraph.el` makes it pleasant in emacs. Every other editor used
to get the same org file, and whatever it typed came back tagged `format:
"org"` — so a vim user composed in a dialect their editor does not know and
their `*emphasis*` was then converted as org's. This module is the other
dialect: the same buffer, rendered as markdown for the editor and parsed back
as markdown by the same parsers, with the prose left untagged, which is what
markdown is in the store.

The org renderers stay the one place the buffer's *shape* is decided.
`render` converts their output rather than rendering again, so a field added
to a template reaches both dialects from one edit; the three parsing
primitives below are the markdown readings of `editor._meta`, `_sections` and
`_body`, and `editor.DIALECTS` is where a parser picks one set or the other.

What the conversion does, line by line:

    :PROPERTIES: … :END:      ->  ---  key: value  ---   (front matter)
    # comment lines           ->  <!-- … --> blocks
    * Heading / ** Field      ->  # Heading / ## Field
    #+TODO: …, the mode line  ->  dropped (org's)
    ,*escaped line            ->  *line   (org's comma-escape undone)
    Context: [[dg:D04][label]] -> label; drawers, tables and emphasis via `orgmd`

and the guidance sentences that are about org or emacs say the markdown thing
instead (`_SAY`). Only the `Input` section is read back, so the Context
conversion may be lossy; the Input conversion is exact, and round-trips are
tested.
"""

from __future__ import annotations

import re

from dgraph import orgmd

#: An ATX heading at column 0. `\#` is a literal, which is how a field's body
#: keeps a `#` at the start of a line (`body` unescapes it).
_HEAD = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$", re.M)
_FRONT = re.compile(r"\A---[ \t]*\n(.*?)^---[ \t]*$\n?", re.S | re.M)
_COMMENT = re.compile(r"<!--.*?-->[ \t]*\n?", re.S)

_ORG_HEAD = re.compile(r"^(\*+)[ \t]+(.*?)[ \t]*$")
_ORG_COMMENT = re.compile(r"^[ \t]*#(?!\+)(?: ?(.*))?$")
_ORG_DRAWER = re.compile(r"^:PROPERTIES:\n(.*?)^:END:\n", re.S | re.M)
_ORG_PROP = re.compile(r"^[ \t]*:([A-Z_]+):[ \t]*(.*?)[ \t]*$")
_DG_LINK = re.compile(r"\[\[dg:[^\]]*\]\[([^\]]*)\]\]")

#: Guidance the org buffer gives that is about org or emacs, and what the
#: markdown buffer says instead. Matched on the comment's text; an empty
#: replacement drops the line. The key lines are matched by their `C-c`.
_SAY = (
    ('Lines starting with "# " are ignored. Only the "Input" subtree is read back;',
     'Lines beginning with > are hints and are ignored. Only the "Input" section is read back;'),
    ('A "*" (heading) or "#" (comment) at column 0 ends a field. Escape as ",*" / ",#".',
     'A "#" (heading) or ">" (hint) at column 0 ends a field. Escape as "\\#" / "\\>".'),
    ("Full org is fine.", "Markdown is fine."),
    ("*single asterisks* render as italic outside emacs; use **bold**.", ""),
    ("file: links welcome.", "Links welcome."),
)
_KEYS = "Save and exit to stage."


def _said(text: str) -> str | None:
    """A comment line's text as the markdown buffer says it; None to drop."""
    if "C-c C-c" in text:
        return _KEYS
    if "C-c " in text:
        return None
    for org, md in _SAY:
        if org in text:
            text = text.replace(org, md)
            return text if text.strip() else None
    return text


def _context_line(ln: str) -> str | None:
    """A Context line as markdown. Reference material, so lossy is fine."""
    s = ln.strip()
    if s in (":PROPERTIES:", ":END:"):
        return None
    m = _ORG_PROP.match(ln)
    if m:
        pad = ln[: len(ln) - len(ln.lstrip())]
        return f"{pad}{m.group(1).lower()}: {m.group(2)}"
    ln = _DG_LINK.sub(r"\1", ln)
    return orgmd.to_markdown(ln, fmt="org")


def render(org: str) -> str:
    """The org buffer a renderer produced, as the markdown buffer."""
    front: list[str] = []
    m = _ORG_DRAWER.search(org)
    if m:
        for raw in m.group(1).splitlines():
            p = _ORG_PROP.match(raw)
            if p:
                key = p.group(1)
                key = key[len("DGRAPH_"):] if key.startswith("DGRAPH_") else key
                front.append(f"{key.lower()}: {p.group(2)}")
        org = org[: m.start()] + org[m.end():]

    out: list[str] = []
    comments: list[str] = []
    in_context = False
    in_input = False

    def flush() -> None:
        # Hints are blockquotes, not HTML comments. A markdown editor with
        # conceal on hides `<!-- -->` delimiters, so the hint and the value
        # slot become indistinguishable and a value typed among the hint is
        # silently dropped (D100, audit AA-F5). A `>` blockquote is shown by
        # every markdown editor, never concealed, so the hint stays visibly a
        # hint. `body` strips leading `>` lines.
        if comments:
            out.extend(("> " + c).rstrip() for c in comments)
            comments.clear()

    for ln in org.split("\n"):
        if ln.startswith("# -*-") or ln.startswith("#+"):
            continue
        c = _ORG_COMMENT.match(ln)
        if c:
            said = _said(c.group(1) or "")
            if said is not None:
                comments.append(said)
            continue
        flush()
        h = _ORG_HEAD.match(ln)
        if h:
            title = h.group(2)
            if len(h.group(1)) == 1:
                in_context = title.lower() == "context"
                in_input = title.lower() == "input"
            if in_context:
                title = _DG_LINK.sub(r"\1", title)
            out.append("#" * len(h.group(1)) + " " + title)
            continue
        if in_context:
            conv = _context_line(ln)
            if conv is not None:
                out.append(conv)
            continue
        # Input: undo org's comma-escape — one comma, the mirror of
        # `editor._escape` — then escape what markdown itself would read: a
        # leading `#` is a heading, a leading `>` a hint (`body` strips them).
        # A leading `*` needs no markdown escape — it is an ordinary list item,
        # not a heading — so it is only un-commaed. D101.
        #
        # One backslash is added before a leading run of backslashes ending in
        # `#` or `>`, and `body` removes exactly one — the same construction as
        # org's comma — so a stored `\#` (markdown's own literal `#`) renders
        # `\\#` and comes back `\#`, instead of colliding with the escape of a
        # stored `#` and coming back without its backslash. Audit `AC-F6`.
        ln = re.sub(r"^([ \t]*),(,*[#*])", r"\1\2", ln)
        ln = re.sub(r"^([ \t]*)(\\*[#>])", r"\1\\\2", ln)
        if not in_input and ln.strip() == "" and (not out or out[-1].strip() == ""):
            # The gap the dropped mode line, `#+TODO` and drawer leave is
            # closed here, outside Input only: inside it a blank line is the
            # value's, and a run of them was collapsed too. Audit `AC-F7`.
            continue
        out.append(ln)
    flush()
    text = "\n".join(out).lstrip("\n")
    if front:
        text = "---\n" + "\n".join(front) + "\n---\n" + text
    return text


# ---- parsing primitives — the markdown readings of editor's three ---------


def meta(text: str) -> dict[str, str]:
    m = _FRONT.match(text)
    if not m:
        return {}
    out = {}
    for ln in m.group(1).splitlines():
        if ":" in ln:
            k, _, v = ln.partition(":")
            out[k.strip().lower()] = v.strip()
    return out


def sections(text: str) -> dict[str, tuple[str, str]]:
    """Split the `# Input` section into `## Field` -> body. `editor._sections`
    with markdown's headings, and the same rule: a heading at column 0 is a
    heading, whatever surrounds it, because that is what the editor shows."""
    from dgraph.editor import EditorError
    heads = list(_HEAD.finditer(text))
    start = next((h for h in heads if len(h.group(1)) == 1
                  and h.group(2).lower() == "input"), None)
    if start is None:
        raise EditorError("no `# Input` section — the buffer is not a dg template")
    out: dict[str, tuple[str, str]] = {}
    cur: str | None = None
    shown = ""
    at = start.end()
    for h in heads:
        if h.start() < start.end():
            continue
        if len(h.group(1)) == 1:
            if cur is not None:
                out[cur] = (shown, text[at:h.start()])
            cur = None
            break
        if len(h.group(1)) == 2:
            if cur is not None:
                out[cur] = (shown, text[at:h.start()])
            shown = h.group(2).split(":")[0].strip()
            name = shown.lower()
            if name in out:
                raise EditorError(f"duplicate field `## {shown}` under Input")
            cur, at = name, h.end()
    if cur is not None:
        out[cur] = (shown, text[at:])
    return out


def body(raw: str) -> str:
    """Strip hint blockquotes and the `\\#` escape, then common indentation.

    A hint is a `>` line (see `render`); a value that genuinely begins with
    `>` is escaped `\\>` and unescaped here, the mirror of the `\\#` escape.
    Exactly one backslash is removed from a leading run ending in `#` or `>`
    (`render` adds exactly one), so `\\\\#` comes back `\\#`: escape∘unescape is
    the identity for any content. Audit `AC-F6`."""
    text = re.sub(r"^[ \t]*>.*(?:\n|$)", "", raw, flags=re.M)
    text = re.sub(r"^([ \t]*)\\(\\*[#>])", r"\1\2", text, flags=re.M)
    return trim(text)


def trim(text: str) -> str:
    """A field's body with the blank lines at either end removed, and nothing
    else. The parsers used to `dedent` and `strip` here, which flattened a
    value that was all indented — a markdown code block — into a paragraph on
    any edit of the record; the value slot sits at column 0 (`D100`), so
    indentation in a body is the writer's. `D104`, audit `AC-F7`."""
    return re.sub(r"\A(?:[ \t]*\n)+", "", text.rstrip())
