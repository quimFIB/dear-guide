"""The id range this clone allocates from, and what it has already issued.

Audit `F-F3`'s companion. Two clones of one graph both compute `next_id` as
`max(stored ids) + 1`, so on a shared base they do not *sometimes* collide —
they collide **by construction**, every time, for every vertex and every task
either of them adds. A grant per clone is what makes the collision rare.

**It is not what makes it safe.** Integration catches a collision either way;
what a grant buys is that the report a person reads at the seam is not a
rename line per record anyone added, which is the volume that trains a reader
to stop reading it. So the layering is deliberate and no layer stands alone:

    a grant     makes a collision RARE      ← so the report stays readable
    replay      makes a collision CAUGHT    ← contested at the seam
    a rename    makes it CHEAP              ← inside the arriving contribution

A grant that turns out wrong therefore costs noise, not correctness.

**Every clone gets one, `main` included.** Granting ranges to worktrees and
saying nothing about the checkout nobody made is the same bug rescheduled: with
a global-max `next_id`, a `main` that has just integrated `D50`–`D57` computes
`D58` next, which is inside the grant the contributor is still holding. Both
clones configured correctly, neither at fault. So `next_id` is max-within-my-
range rather than global max, and a clone with no grant keeps today's exact
behaviour — the file is absent in every single-writer project and nothing here
fires.

## The watermark, and why a range alone is not enough

A range is scoped to a **checkout**. The thing that needs a distinct range is a
**contribution**, and those are not the same object:

    .dgraph-range.json   D50-D99            ← lives in the working tree
                                │
                  ┌─────────────┴─────────────┐
             branch X                    branch Y
             base max = D49              base max = D49
             next_id  -> D50             next_id  -> D50

Two branches in one worktree share a grant and start from the same store, so
both allocate the same id — and a range on its own cannot see it. `vet` asks
*is this id inside my range*, and `D50` is. Nothing asks *has this range
already issued `D50`*, because the only memory of what was issued is the store,
and switching branches switches the store.

So the file records a high-water mark, and it survives a checkout for the same
reason the trays do: it is gitignored, so `git checkout` never touches it. That
is not a coincidence to lean on quietly — it is the same property
`.dgraph-pending.json` already depends on, and it is why this file is named in
`project.IGNORE` beside them.

**The cost is gaps**, and it is accepted: abandon branch X and `D50` is burned.
The sequence stopped being dense the moment ranges existed.

**What it does not reach:** two clones granted the same range by a careless
harness. The mark is per clone and cannot see that. Integration catches it as a
rename line, which is the layering above working as designed.

## Why the file is shaped this way

`{"D": {"range": [50, 99], "issued": 57}}` is the shape that forecloses
nothing. `"issued": 57` becomes `"issued": [50, 51, 57]` if a full allocation
log is ever wanted, and `_mark` reads either. Delete the file and `next_id`
falls back to `max(store) + 1`, which is the behaviour every existing project
has. Add a `"branch"` key and grants become per branch without breaking a
reader. The one choice that would have been expensive later is a bare
`[lo, hi]` pair, which is why each store's entry is an object with named keys.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from dgraph import project

#: The two id prefixes, and the order `dg range` reports them in. One grant
#: covers both: a contribution is a contribution to the project, not to one of
#: its stores, and a worker that had a `D` range and no `T` range would collide
#: on every task it added while its decisions were safe.
PREFIXES = ("D", "T")


class RangeError(RuntimeError):
    """A grant that cannot answer — exhausted, or malformed on disk."""


@dataclass(frozen=True)
class Grant:
    """One store's range and its high-water mark.

    `issued` is the highest number this clone has committed to, or `None` where
    it has committed to nothing yet. `None` rather than `lo - 1` because the
    file is read by people: an empty grant should say nothing about what it has
    issued rather than name a number outside itself.
    """

    lo: int
    hi: int
    issued: int | None = None

    @property
    def size(self) -> int:
        return self.hi - self.lo + 1

    def holds(self, n: int) -> bool:
        return self.lo <= n <= self.hi


def path(root: Path | None = None) -> Path:
    return (root or project.find().root) / project.RANGE_NAME


def load(root: Path | None = None) -> dict[str, Grant]:
    """Every grant this clone holds, or `{}` where it holds none.

    A missing file is the ordinary case and is not an error: single-writer
    projects never grow one, and every caller here treats `{}` as "behave the
    way this tool always has".

    A file that is *present and unreadable* is a different thing and raises.
    Falling back to the global maximum there would silently hand out ids inside
    somebody else's grant, which is exactly the failure the file exists to
    prevent — and it would do it at the moment the operator most believes they
    are protected.
    """
    p = path(root)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        out = {}
        for prefix in PREFIXES:
            entry = raw.get(prefix)
            if entry is None:
                continue
            lo, hi = entry["range"]
            out[prefix] = Grant(int(lo), int(hi), _mark(entry.get("issued")))
        return out
    except (ValueError, KeyError, TypeError) as exc:
        raise RangeError(
            f"{p.name} could not be read ({exc}) — it grants this clone an id "
            f"range, and allocating without it would hand out ids inside "
            f"another writer's grant. Fix it, or delete it to go back to "
            f"allocating from the whole sequence"
        ) from None


def _mark(raw: object) -> int | None:
    """The high-water mark from whatever `issued` holds.

    A number today. `max()` over a list if this ever becomes a full allocation
    log, which is the growth the module docstring reserves — read through one
    function so that growth is one edit rather than one per caller.
    """
    if raw is None:
        return None
    if isinstance(raw, list):
        return max((int(n) for n in raw), default=None)
    return int(raw)


def save(grants: dict[str, Grant], root: Path | None = None) -> None:
    """Write the file, or remove it when there is nothing left to grant."""
    p = path(root)
    if not grants:
        p.unlink(missing_ok=True)
        return
    body = {}
    for prefix in PREFIXES:
        g = grants.get(prefix)
        if g is None:
            continue
        entry: dict = {"range": [g.lo, g.hi]}
        if g.issued is not None:
            entry["issued"] = g.issued
        body[prefix] = entry
    # One line per store rather than `indent=2` throughout: `[50, 99]` split
    # over four lines is the file's most important fact made least readable,
    # and this is a file people open.
    rows = ",\n".join(f'  "{k}": {json.dumps(v)}' for k, v in body.items())
    project.write_atomic(p, "{\n" + rows + "\n}\n")


def grant(prefix: str, root: Path | None = None) -> Grant | None:
    """This clone's grant for one store, or `None` where it has none."""
    return load(root).get(prefix)


def next_number(prefix: str, taken, root: Path | None = None) -> int:
    """The next number to allocate for `prefix`, given the ids already in play.

    `taken` is every number the *effective* graph holds — the store plus the
    tray — which is what every caller already has to hand.

    Without a grant this is `max(taken) + 1`, unchanged from the day this tool
    was written. With one it is the first number above both the highest granted
    id in play and the watermark, so a branch switch cannot re-offer an id the
    other branch already took.
    """
    numbers = [int(n) for n in taken]
    g = grant(prefix, root)
    if g is None:
        return max(numbers, default=0) + 1
    mine = [n for n in numbers if g.holds(n)]
    floor = max([*mine, *( [g.issued] if g.issued is not None else [] ),
                 g.lo - 1])
    nxt = floor + 1
    if not g.holds(nxt):
        raise RangeError(
            f"this clone's {prefix} range {g.lo}-{g.hi} is used up "
            f"({g.size} ids) — `dg range --set` a fresh one before adding "
            f"more, or integrate what is here and start again"
        )
    return nxt


def issue(prefix: str, n: int, root: Path | None = None) -> None:
    """Raise the watermark to `n`, if there is a grant and `n` is inside it.

    Called where an id is **committed to** — staging — rather than where one is
    offered. The four `next_id` callers all prefill a form or advertise the
    next id to a browser, and `/api/graph` answers on every page load: bumping
    there would burn an id every time somebody refreshed the page, and burn one
    for every `dg add --edit` a writer thought better of. Staging is the first
    moment a writer has said which id they mean, which is what a watermark is
    for.

    Silent where there is no grant, and where the id sits outside one — an id
    outside the range is `vet`'s refusal to make, and a watermark that followed
    it would move the mark somewhere the grant does not reach.

    **Locked here, on its own terms.** This is a load-modify-save, and it used
    to inherit whatever lock its caller happened to hold — which is
    `pending.stage_all`, holding *a tray*. There are two trays and one range
    file: a decision stage holds `.dgraph-pending.json.lock` and a task stage
    holds `.dgraph-task-pending.json.lock`, so the two of them serialise against
    nothing and the later save carries the earlier one's mark away. Two
    different locks over one file is no lock at all, and the mark going
    backwards is the one failure this file exists to prevent — after a
    checkout, `next_number` re-offers an id the grant has already issued.
    Audit W-F3.
    """
    with project.held(path(root)):
        grants = load(root)
        g = grants.get(prefix)
        if g is None or not g.holds(n) or (g.issued is not None
                                           and n <= g.issued):
            return
        grants[prefix] = Grant(g.lo, g.hi, n)
        save(grants, root)


def note_ops(ops, root: Path | None = None) -> None:
    """Raise the watermark for every id these staged ops commit to.

    Here rather than in each caller because staging is one function
    (`pending.stage_all`) for both trays and every door — the CLI, the org
    buffer and the browser all arrive through it. A per-door version is the
    shape this codebase's audit findings keep taking.
    """
    for op in ops:
        if op.get("op") not in ("add_vertex", "add_task"):
            continue
        rid = str(op.get("id") or "")
        if rid[:1] in PREFIXES and rid[1:].isdigit():
            issue(rid[0], int(rid[1:]), root)


def fault(prefix: str, rid: str, root: Path | None = None) -> str | None:
    """Why `rid` may not be allocated here, or `None` where it may.

    The stage-time half of a grant, and the half that makes it a rule rather
    than a discipline: `next_id` only prefills a form, and `--id` is an option,
    so `dg add --id D58` walks straight past a range that lives only in the
    prompt. Shaped like `model.status_fault` — the reason without the id in
    front of it — so both stores' `vet` can say it their own way.
    """
    g = grant(prefix, root)
    if g is None or not rid[1:].isdigit():
        return None
    n = int(rid[1:])
    if g.holds(n):
        return None
    return (f"{rid} is outside this clone's {prefix} range {g.lo}-{g.hi} — "
            f"ids from another writer's grant collide on integration. "
            f"`dg range` shows the grant")
