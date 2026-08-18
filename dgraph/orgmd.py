"""Org prose -> markdown, for the generated views only.

`decisions.json` holds prose exactly as it was typed. Emacs users compose in
full org (`dg decide --edit`), so an answer may carry org links, tables and
verbatim markers; the web app's textarea, `md_import.py` and any future agent
plugin all produce markdown instead. The store therefore holds both dialects,
and the views have to cope with either.

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
ops — is markdown and keeps markdown's meaning, untouched. Conversion is
display-only either way: the store holds the bytes as typed, so being wrong
here is cosmetic and never data loss.
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
