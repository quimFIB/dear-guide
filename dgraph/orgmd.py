"""Org prose -> markdown, for the generated views only.

`decisions.json` holds prose in the dialect it was typed. Emacs users compose
in full org (`dg decide --edit`), so an answer may carry org links, tables and
verbatim markers; the web app's textarea, `md_import.py`, the markdown compose
buffer every other editor gets, and any future agent plugin all produce
markdown instead. The store therefore holds both dialects, and the views have
to cope with either.

That mixture fixes this module's default scope. Every unconditional rule below
rewrites a construct markdown has no syntax for, so running it over markdown is
a no-op — which is the property that keeps a mixed store safe, and is tested
directly:

    [[dg:D04][label]]     ->  [label](#d04)
    [[file:x.md][text]]   ->  [text](x.md)
    |---+---|             ->  |---|---|
    =verbatim=  ~code~    ->  `code`

Single-marker emphasis is the one construct where the two dialects collide —
org `*bold*` and markdown `*italic*` are the same syntax with different
meaning, so no unconditional rewrite can be right for both. The resolution is
**provenance**: the editor path tags what it composes (`format: "org"` on the
record), and callers pass that tag as `fmt`. Org-tagged prose additionally
converts

    *bold*     ->  **bold**
    /italic/   ->  _italic_

honouring org's own emphasis boundaries, and never inside a code span or a
link target. Untagged prose — the web form, `md_import.py`, an agent posting
ops, the markdown compose buffer — is markdown and keeps markdown's meaning,
untouched.

The store holds a field's bytes as typed, with one exception, and it is the
tag's own: a record carries **one** `format` for every prose field on it, so
when an op writes one of those fields in the other dialect the fields beside
it are converted into the writer's dialect (`convert`, in `pending` and
`task_pending`) and the tag says what all of them now are. The alternative
was a tag true of one field and false of the next. `to_org` exists for that
conversion's other direction, and for showing markdown prose in the org
buffer; the views still only ever convert *to* markdown.
"""

from __future__ import annotations

import re

#: Org's own emphasis boundaries (`org-emphasis-regexp-components`): a marker
#: only opens after one of these, and only closes before one of these. Honouring
#: them is what keeps `lr=0.001` and `vocab=32000` from being read as verbatim.
_PRE = r"[-—\s('\"{]"
_POST = r"[-—\s.,:!?;'\")}\[]"

_VERBATIM = re.compile(
    rf"(^|{_PRE})([=~])([^\s](?:[^\n]*?[^\s])?)\2(?={_POST}|$)",
    re.M,
)

#: `[[target]]` or `[[target][label]]`. Markdown has no `[[`, so this cannot
#: fire on markdown — a reference link `[text][ref]` opens with a single bracket.
_LINK = re.compile(r"\[\[([^\[\]]+?)\](?:\[([^\[\]]*?)\])?\]")

#: A table rule made only of pipes, dashes and org's `+` column joints. A
#: markdown rule (`|---|---|`) matches too and converts to itself; one carrying
#: alignment colons does not match at all, and is already markdown.
_TABLE_RULE = re.compile(r"^([ \t]*)(\|[-+|\s]*\|)([ \t]*)$", re.M)


#: Org single-marker emphasis, converted only for org-tagged prose. The body
#: excludes its own marker and newlines, so already-portable `**bold**` never
#: matches and a span cannot cross paragraphs; the boundaries are org's own,
#: which is what keeps `2*3*4`, `report/x.md` and `and/or` untouched — org
#: itself does not read those as emphasis either.
_BOLD = re.compile(rf"(^|{_PRE})\*([^\s*](?:[^\n*]*?[^\s*])?)\*(?={_POST}|$)", re.M)
_ITALIC = re.compile(rf"(^|{_PRE})/([^\s/](?:[^\n/]*?[^\s/])?)/(?={_POST}|$)", re.M)

#: Segments emphasis must never rewrite: a code span (org verbatim has already
#: become backticks by the time emphasis runs) and a markdown link target,
#: where a path like `(/docs/)` is exactly the shape `/italic/` takes.
_PROTECTED = re.compile(r"(`[^`\n]+`|\]\([^)\n]*\))")


def _emphasis(seg: str) -> str:
    seg = _BOLD.sub(r"\1**\2**", seg)
    return _ITALIC.sub(r"\1_\2_", seg)


def _verbatim(m: re.Match[str]) -> str:
    pre, marker, body = m.group(1), m.group(2), m.group(3)
    # A body that itself starts or ends with the marker is not org verbatim; it
    # is markdown that happens to double the character, e.g. GFM `~~strike~~`.
    if body.startswith(marker) or body.endswith(marker):
        return m.group(0)
    return f"{pre}`{body}`"


def _link(m: re.Match[str]) -> str:
    target, label = m.group(1).strip(), (m.group(2) or "").strip()
    if target.startswith("dg:"):
        vid = target[3:].strip()
        return f"[{label or vid}](#{vid.lower()})"
    if target.startswith("file:"):
        target = target[5:].strip()
    return f"[{label or target}]({target})"


def to_markdown(text: str | None, fmt: str | None = None) -> str | None:
    """Rewrite the org-only constructs in `text`. Markdown passes through.

    `fmt` is the record's provenance tag. `"org"` additionally converts
    single-marker emphasis (see the module docstring); anything else — None,
    absent, unknown — leaves emphasis exactly as typed, because without
    provenance no rewrite can be right for both dialects.
    """
    if not text:
        return text
    out = _LINK.sub(_link, text)
    out = _VERBATIM.sub(_verbatim, out)
    out = _TABLE_RULE.sub(lambda m: m.group(1) + m.group(2).replace("+", "|") + m.group(3), out)
    if fmt == "org":
        out = "".join(
            seg if _PROTECTED.fullmatch(seg) else _emphasis(seg)
            for seg in _PROTECTED.split(out)
        )
    return out


# ---- the other direction ---------------------------------------------------
#
# Markdown -> org, for the one place prose has to *become* org: a record whose
# tag says org and which is about to hold prose typed as markdown, or the org
# buffer seeded with prose that was typed as markdown. Same boundaries as
# above, so the two directions agree about what an emphasis span is.

_MD_CODE = re.compile(r"`([^`\n]+)`")
_MD_LINK = re.compile(r"\[([^\[\]\n]*)\]\(([^)\s]+)\)")
_MD_BOLD = re.compile(rf"(^|{_PRE})\*\*([^\s*](?:[^\n*]*?[^\s*])?)\*\*(?={_POST}|$)", re.M)
_MD_STAR = re.compile(rf"(^|{_PRE})\*([^\s*](?:[^\n*]*?[^\s*])?)\*(?={_POST}|$)", re.M)
_MD_UNDER = re.compile(rf"(^|{_PRE})_([^\s_](?:[^\n_]*?[^\s_])?)_(?={_POST}|$)", re.M)
_MD_TABLE_RULE = re.compile(r"^([ \t]*)\|([-|\s:]*)\|([ \t]*)$", re.M)
#: Segments the org emphasis pass must not enter: verbatim and links, which
#: the passes before it have just produced.
_ORG_PROTECTED = re.compile(r"(=[^=\n]+=|\[\[[^\]]*\](?:\[[^\]]*\])?\])")
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")


def _md_link(m: re.Match[str]) -> str:
    label, target = m.group(1).strip(), m.group(2).strip()
    if re.fullmatch(r"#d\d+", target):
        return f"[[dg:{target[1:].upper()}][{label or target[1:].upper()}]]"
    if not _SCHEME.match(target):
        target = "file:" + target
    return f"[[{target}][{label}]]" if label else f"[[{target}]]"


def _org_emphasis(seg: str) -> str:
    # Single markers first: `**bold**` cannot match them (the body may not
    # start with `*`, and the inner `*` has no boundary before it), and doing
    # bold first would hand the italic pass a `*bold*` to turn into `/bold/`.
    seg = _MD_STAR.sub(r"\1/\2/", seg)
    seg = _MD_UNDER.sub(r"\1/\2/", seg)
    return _MD_BOLD.sub(r"\1*\2*", seg)


def to_org(text: str | None) -> str | None:
    """Rewrite markdown's constructs as org's. The inverse of `to_markdown`
    with `fmt="org"`, to the extent the two syntaxes map: links, code spans,
    table rules and emphasis. Prose with none of them passes through."""
    if not text:
        return text
    out = _MD_LINK.sub(_md_link, text)
    out = _MD_CODE.sub(r"=\1=", out)
    out = _MD_TABLE_RULE.sub(
        lambda m: f"{m.group(1)}|{m.group(2).replace('|', '+').replace(':', '-')}|{m.group(3)}",
        out)
    return "".join(
        seg if _ORG_PROTECTED.fullmatch(seg) else _org_emphasis(seg)
        for seg in _ORG_PROTECTED.split(out)
    )


def convert(text: str | None, frm: str | None, to: str | None) -> str | None:
    """`text`, typed in dialect `frm`, as dialect `to`. `"org"` or anything
    else (markdown) on either side; the same dialect on both is the identity.

    The one function the store and the buffers call when prose crosses a
    dialect: a record's tag covers every prose field it holds, so a field
    written in the other dialect either converts or falsifies the tag for
    the fields beside it."""
    frm, to = (frm == "org"), (to == "org")
    if frm == to or not text:
        return text
    return to_org(text) if to else to_markdown(text, fmt="org")


def cell(text: str | None) -> str:
    """A value bound for a markdown table cell.

    A `|` splits the row and a raw newline ends it — prose is composed in an
    editor, so both are routine — and either silently corrupts every row after
    it. Shared by both generated views; nothing here knows what it is escaping.
    """
    return (text or "").replace("|", "\\|").replace("\n", "<br>")


def anchor(vid: str) -> str:
    """The explicit anchor `render.py` emits so `dg:` links actually resolve.

    GitHub's generated heading slugs depend on the title, which this module does
    not have; an emitted anchor is stable regardless of what a decision is
    called.
    """
    return f'<a id="{vid.lower()}"></a>'
