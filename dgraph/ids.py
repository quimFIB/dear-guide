"""Record ids: the one sort key, and the one table of where an id can be written.

Neither store may import the other (`tests/test_cross.py`), and both list
ids; a key that lived in `model` would put `tasks` on the wrong side of that
barrier. So it lives here, beside the other thing both stores share: the
fields that hold an id.

`ID_FIELDS` is that table (`D108`). Three readers used to keep a list each —
`integrate._ID_KEYS` for renumbering a contribution whose ids collide,
`pending.REFERENCES` for the tray's walk (which act adds the id this op
names), and `dg rm` for what still points at a record — and the three had
drifted: `derived_from` was renumbered by nobody, and a reading's `against`
was read by nobody on the way out. Two marks per field:

- **renumbered** on integrate: every field, since an old id left anywhere is
  a record quietly pointing at the wrong thing;
- **live**: a dependency the tray walk must resolve before the op can land.
  `id` is what an op introduces, not what it names; `against` is an
  archived reading, kept when the link moves and read against the store by
  `dg confirm`, so nothing waits on it.

A field that holds a list of ids is renumbered element-wise.
"""

from __future__ import annotations

import re

#: field -> (renumbered on integrate, a live dependency for the tray walk).
ID_FIELDS: dict[str, tuple[bool, bool]] = {
    "id": (True, False),
    "vertex": (True, True),
    "task": (True, True),
    "from": (True, True),
    "to": (True, True),
    "into": (True, True),
    "because": (True, True),
    "evidence_for": (True, True),
    "derived_from": (True, True),
    "against": (True, False),
}
#: The record-side spellings of op-side fields, for a test that reads the
#: models against this table: an edge's `src` is an op's `from`.
ALIASES = {"src": "from"}
RENUMBERED = tuple(k for k, (r, _) in ID_FIELDS.items() if r)
LIVE = tuple(k for k, (_, live) in ID_FIELDS.items() if live)

_ID_PARTS = re.compile(r"^([A-Za-z]*)0*(\d+)$")


def idkey(rid: str) -> tuple:
    """The sort key for a record id: by prefix, then by *number*.

    Ids are zero-padded to two digits (`D01`), which made string order look
    like numeric order until the hundredth record — then `D100` sorted before
    `D71` on every listing, the frontier included. Every `sorted(...)` over
    ids reads through this so the order a person sees is the order the
    questions were asked in. Audit `AD-F5`."""
    m = _ID_PARTS.match(rid)
    return (m.group(1), int(m.group(2)), rid) if m else (rid, -1, rid)
