"""Org prose -> markdown, for the generated views only.

`decisions.json` holds prose exactly as it was typed. Emacs users compose in
full org (`dg decide --edit`), so an answer may carry org links, tables and
verbatim markers; the web app's textarea, `md_import.py` and any future agent
plugin all produce markdown instead. The store therefore holds both dialects,
and the views have to cope with either.

That mixture is what fixes this module's scope. Every rule below rewrites a
construct markdown has no syntax for, so running it over markdown is a no-op —
which is the property that keeps a mixed store safe, and is tested directly:

    [[dg:D04][label]]     ->  [label](#d04)
    [[file:x.md][text]]   ->  [text](x.md)
    |---+---|             ->  |---|---|
    =verbatim=  ~code~    ->  `code`

Single-marker emphasis is deliberately left alone. Org `*bold*` and markdown
`*italic*` are the same syntax with different meaning, so no rewrite can be
right for both; `/x/` -> `*x*` would wreck `report/x.md` and `and/or`. The cost
is that org bold shows as italic outside emacs, and `**bold**` is the portable
spelling. Conversion is display-only, so being wrong here is cosmetic and never
data loss.
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


def to_markdown(text: str | None) -> str | None:
    """Rewrite the org-only constructs in `text`. Markdown passes through."""
    if not text:
        return text
    out = _LINK.sub(_link, text)
    out = _VERBATIM.sub(_verbatim, out)
    out = _TABLE_RULE.sub(lambda m: m.group(1) + m.group(2).replace("+", "|") + m.group(3), out)
    return out


def anchor(vid: str) -> str:
    """The explicit anchor `render.py` emits so `dg:` links actually resolve.

    GitHub's generated heading slugs depend on the title, which this module does
    not have; an emitted anchor is stable regardless of what a decision is
    called.
    """
    return f'<a id="{vid.lower()}"></a>'
