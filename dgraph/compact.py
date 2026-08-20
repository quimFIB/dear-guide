"""One line per thing: the schematic rendering the read commands default to.

`dg show`, `dg task` and `dg context` all answer "what is the shape of this
right now?", and all three used to answer it at length — two of them inside
box-drawn tables that wrap a title across four rows, the third by printing
every ancestor's full answer, evidence and falsifier. Both forms are read in a
terminal *and* piped into an agent's context, where chrome and prose are paid
for in tokens on every session.

So the default is a listing: **one line per item, columns that line up, prose
reduced to its first sentence.** Nothing is dropped — `--full` on each command
restores the detailed form. This module owns only the shaping; what to say
stays with the module that knows it (`brief.rows`, `cross`, `context`).

Three rules the layout keeps, in this order:

- **An id is never clipped.** A line whose id is truncated is a line nobody can
  follow up, which is worse than a line that runs past the margin. Titles and
  prose absorb the width instead.
- **The columns align.** The whole value of a listing over a paragraph is that
  the eye reads down one column, so the title is padded to a common width even
  when the aside beside it is empty.
- **A column is there for every row or for none.** Anything that fits on some
  rows and not others (`tails`, below) is dropped everywhere rather than left
  ragged, because a column with holes in it reads as missing data.
"""

from __future__ import annotations

import re

#: The default line budget, used where there is no terminal to ask. Wider than
#: `context.WIDTH` (76), which is prose and wraps; these are single lines, and
#: the aside is allowed past the margin when nothing else fits.
WIDTH = 88

#: The most a listing will spread, however wide the terminal. Past about this
#: the columns drift far enough apart that the eye stops associating them, and
#: a listing whose columns do not associate is just a table without the rules.
MAX_WIDTH = 116

#: A title never shrinks below this, however long the asides are. Below about
#: this much a decision title stops being recognisable, and an unrecognisable
#: listing is not a shorter one — it is one you have to expand every row of.
MIN_TITLE = 26

#: How much of the line the asides may claim before titles start paying.
MAX_ASIDE = 34


#: A Rich style tag, but not one a caller escaped. `cli._x` writes a literal
#: bracket as `\[`, and a width computed by counting those as markup would pad
#: every title holding a bracket four characters short.
_TAG = re.compile(r"(?<!\\)\[/?[a-z0-9 #_.]*\]")


def visible(s: str) -> str:
    """`s` with its style tags removed — what the reader actually sees.

    Every width in this module is measured against this. Padding a coloured
    field by `len()` counts the tags as characters and knocks the column out of
    line by however much styling it happened to carry.
    """
    return _TAG.sub("", s or "")


def clip(s: str, n: int) -> str:
    """`s` in at most `n` visible characters, with an ellipsis where it was cut."""
    s = " ".join((s or "").split())
    if len(visible(s)) <= n:
        return s
    # Never end on the backslash of an escaped bracket: Rich would then read
    # the next character as part of an escape that is no longer there.
    return s[: max(n - 1, 0)].rstrip().rstrip("\\") + "…"


def pad(s: str, n: int) -> str:
    """`s` left-aligned in `n` visible columns."""
    return s + " " * max(n - len(visible(s)), 0)


#: A bullet, a heading or a quote — the marker plus the space that makes it one.
#: The space matters: `*HNSW*, M=32` opens with an asterisk and is a sentence,
#: and treating it as a list item skipped the one line that said what was
#: decided. A prefix test alone got that wrong; this is why the check below is
#: a function.
_MARKERS = ("- ", "* ", "+ ", "> ", "#")


def is_structure(line: str) -> bool:
    """Whether this line is layout rather than prose.

    A table row, a list item, a heading, a fence or a horizontal rule. A gist
    made of these says nothing — "| index | recall@10 |" summarises nothing —
    so they are skipped when looking for the sentence that does.
    """
    line = line.strip()
    if not line:
        return False
    if line.startswith(("|", "```", "~~~")):
        return True
    if set(line) <= set("-*_ ") and len(line) >= 3:      # a horizontal rule
        return True
    return line.startswith(_MARKERS)


def gist(prose: str | None, fmt: str | None = None) -> str:
    """A note, an answer or an outcome in one sentence's worth of one line.

    The **first paragraph**, joined — not the first line. An answer is stored
    wrapped, so its first line is usually a fragment cut mid-sentence, and a
    listing built out of fragments is one nobody can read down. Paragraphs that
    are structure (a table, a list) are skipped in favour of the prose around
    them, and a body that is only structure summarises as empty rather than as
    a row of pipes.

    Converted through `orgmd` first, so an org answer reads here exactly as it
    reads in the generated view — the same rule `context._wrap` follows.
    """
    if not prose:
        return ""
    from dgraph import orgmd
    prose = orgmd.to_markdown(prose, fmt) or prose
    para: list[str] = []
    for line in prose.strip().splitlines():
        stripped = line.strip()
        if not stripped or is_structure(stripped):
            if para:
                break
            continue
        para.append(stripped)
    return " ".join(" ".join(para).split())


def listing(entries: list[tuple[str, str, str, str]], width: int = WIDTH,
            indent: str = "  ", prose_aside: bool = False,
            markup: bool = False, tails: list[str] | None = None) -> list[str]:
    """`(id, status, title, aside)` rows, as aligned single lines.

    Two shapes, because the aside is two different things. By default it is a
    short cross-reference (`waits D04`, `unblocks D05`) that must survive
    whole, so it is written after the title and allowed past the margin. With
    `prose_aside` it is a clipped sentence — a decision's answer — and then it
    is a column in its own right, sharing the leftover width with the title
    rather than running off the end of the line.

    Either way the title column takes whatever the ids and the statuses leave,
    so a graph of roots gets long titles and a tangled one gets short ones: the
    width goes where the information is.

    `tails` are per-row extras — an area name, typically — that are worth
    showing and not worth squeezing a title for. They are appended if every
    line still fits the width and dropped from every row if any line does not,
    so the column either reads down cleanly or is not there at all.
    """
    if not entries:
        return []
    if tails:
        with_tails = [(v, s, t, " · ".join(x for x in (a, tail) if x))
                      for (v, s, t, a), tail in zip(entries, tails)]
        lines = listing(with_tails, width, indent, prose_aside, markup)
        if max(len(visible(ln)) for ln in lines) <= width:
            return lines
        # Otherwise fall through and lay the rows out without their tails.

    def w(s: str) -> int:
        return len(visible(s)) if markup else len(s)

    idw = max(w(e[0]) for e in entries)
    stw = max(w(e[1]) for e in entries)
    titles = max(w(e[2]) for e in entries)
    # Two gaps of two spaces before the title, and the "  ·  " before an aside.
    fixed = len(indent) + idw + 2 + stw + 2
    # In both branches below, `min(titles, ...)` is the outer call and
    # `max(MIN_TITLE, ...)` the inner one. The floor exists to stop a crowded
    # line clipping a title to nothing, not to pad every short title out to it;
    # the other order left sixteen spaces of nothing between a short title and
    # its aside.
    if prose_aside:
        budget = max(width - fixed - 5, MIN_TITLE)
        tw = min(titles, max(MIN_TITLE, budget * 2 // 5))
        asw = max(budget - tw, 20)
    else:
        asw = min(max(w(e[3]) for e in entries), MAX_ASIDE)
        tw = min(titles, max(MIN_TITLE,
                             width - fixed - (5 + asw if asw else 0)))
    out = []
    for vid, status, title, aside in entries:
        line = (indent + pad(vid, idw) + "  " + pad(status, stw) + "  "
                + pad(clip(title, tw), tw))
        if aside:
            line += "  ·  " + (clip(aside, asw) if prose_aside else aside)
        out.append(line.rstrip())
    return out


def hint(command: str, what: str) -> str:
    """The line that says the detail is one flag away, never that it is gone.

    Every compact view carries one. A reader who cannot tell whether the tool
    is summarising or simply does not know has to go and check, which costs
    more than the line saves.
    """
    return f"  [dim]`{command}` for {what}[/]"


def plain_hint(command: str, what: str) -> str:
    """`hint` for the renderings that are printed, not `con.print`ed."""
    return f"  `{command}` for {what}"
