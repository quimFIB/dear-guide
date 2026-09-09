"""The sort key for record ids — one module, imported by both stores.

Neither store may import the other (`tests/test_cross.py`), and both list
ids; a key that lived in `model` would put `tasks` on the wrong side of that
barrier. So it lives here, beside nothing else.
"""

from __future__ import annotations

import re

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
