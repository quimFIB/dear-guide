"""Tags: the free labels a record carries beside its area, in both stores.

A tag is a **word for a person**, and that sentence is the whole of `D95`.
It sits beside two things it is deliberately not. It is not the area: the
area is one required name every section, sort key and coining rule reads, a
partition, and a tag set is optional and non-exclusive -- *what does this
touch*, not *where is this filed*. And it is not a bind: a bind is an
address (`kind:ref`) that a domain's evaluator resolves, so its kind has to
name a domain; a tag names nothing but itself. Two label sets on one record
is the cost `D95` paid, and its falsifier -- a real store whose tags turn out
to be spellings of areas -- is what would fuse them.

Like an area, a tag is **not a claim**: `dg amend --tag` rewrites the set and
supersedes nothing, `tags` sits in `pending.FIELDS`, and nothing here is ever
archived. Like an area, the set accumulates with use and is validated
nowhere; what stands in for a whitelist is the same similarity guard
`areas.similar` runs at the one staging door, with `--new-tag` as the
override. Unlike an area there is no `$DG_TAG` policy: an agent may coin a
tag freely until one abuses it, which is the thing that would add the dial.

Stored as `tags`, a list, **absent when empty** the way every other optional
field of either store is -- so a store written before tags existed loads and
saves byte-for-byte unchanged, which is the first line of `T93`'s definition
of done.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from dgraph import areas as _areas

#: The field's name in both stores and in the query grammar. `tags:` rather
#: than `tag:`, because `query._fields_of` reads the vocabulary off the
#: dataclasses and the framework has no aliasing layer -- `binds:` is the
#: precedent.
FIELD = "tags"


def clean(raw: Iterable[str] | str | None) -> list[str]:
    """The tag list a flag or a body handed over, tidied and in order.

    Each item may itself be comma-separated -- `--tag a,b --tag c` -- since a
    repeated option and a list are both things a person types for a set.
    Whitespace is stripped, an empty item dropped, and a repeat kept once in
    first-use order. Nothing is refused here: `fault` is the judge, so a door
    that only tidies cannot silently pass what a door that judges would not.
    """
    if raw is None:
        return []
    items = [raw] if isinstance(raw, str) else list(raw)
    out: list[str] = []
    for item in items:
        for part in str(item).split(","):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out


def fault(tags: object) -> str | None:
    """Why this is not a stored tag list, or None. Read on every door an op
    enters through, so a body posted as data is held to the flag's shape."""
    if not isinstance(tags, list):
        return "tags is a list of words"
    for t in tags:
        if not isinstance(t, str) or not t.strip():
            return "a tag is a non-empty word"
        if t != t.strip():
            return f"tag {t!r} carries whitespace at an end"
        if "," in t:
            return f"tag {t!r} holds a comma — one tag per item"
    if len(set(tags)) != len(tags):
        return "a tag is listed once"
    return None


def tags_of(record) -> list[str]:
    """One record's tags, whether a dataclass or the raw dict from a store."""
    if isinstance(record, dict):
        return list(record.get(FIELD) or [])
    return list(getattr(record, FIELD, None) or [])


def counts(records: Iterable) -> dict[str, int]:
    """How many records carry each tag, first-use order."""
    out: dict[str, int] = {}
    for rec in records:
        for t in tags_of(rec):
            out[t] = out.get(t, 0) + 1
    return out


_STORED: dict[tuple, dict[str, int]] = {}


def stored_counts(path: Path | None) -> dict[str, int]:
    """A store's tags, read off disk -- `areas.stored_counts`'s twin, for the
    same reason: one function over raw records serves both stores from
    anywhere, and a store that is absent or will not parse is empty rather
    than an error."""
    if path is None or not path.exists():
        return {}
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        if key in _STORED:
            return _STORED[key]
        raw = json.loads(path.read_text(encoding="utf-8"))
        got = counts(raw.get("vertices") or raw.get("tasks") or [])
    except (OSError, ValueError, AttributeError):
        return {}
    _STORED[key] = got
    return got


def refuse(tags: list[str], *, known: dict[str, int],
           new_tag: bool = False) -> str | None:
    """Why these tags may not be filed yet -- or None if they may.

    `known` is the union of both stores' tags and the tray's, exactly as
    `pending.refuse_area` reads the areas. A tag already in use is silent, a
    genuinely new one is silent, and only a new one that **resembles** one in
    use is refused, naming what it resembles -- `areas.similar` is the judge,
    and the argument for what it catches and what it lets past is there.
    `new_tag` is the override, and like `new_area` it never persists into an
    op: `apply` does not recheck, so a permission written into the tray would
    travel with the record.
    """
    if new_tag:
        return None
    rows = []
    for tag in tags:
        if tag in known:
            continue
        close = _areas.similar(tag, known)
        if close:
            rows.append((tag, close))
    if not rows:
        return None
    lines = []
    for tag, close in rows:
        lines.append(f"tag {tag!r} is new, and close to tags already in use:")
        lines += [f"    {c}  ({known[c]} filed)" for c in close]
    return ("\n".join(lines)
            + "\nIf it is genuinely a different tag, restage with --new-tag. "
              "Tags accumulate: nothing has to be declared first.")
