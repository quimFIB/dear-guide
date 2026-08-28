"""Areas: the vocabulary a project files its records under, in both stores.

An area is a **label on a record**, not a schema. `pending.FIELDS` is where
that was already settled -- `dg amend D05 --area corpus` supersedes nothing and
archives nothing, because "a title and an area are not claims" -- and this
module takes that sentence at its word. `Graph.areas` and `TaskGraph.areas` are
**accumulating registries in first-use order**, written by the op that first
files a record under one, and membership is validated nowhere.

## Why the list is an ordering hint and not a whitelist

Because the whitelist could not be added to. No op wrote `areas`; it was the
one field of either store that only `init` and `import` set, so a contribution
introducing an area arrived with every record in it refused, and
`integrate.py` had to report that as `unexpressible` -- a finding with no fix.
The two lists were also independent while `dg areas` and `/api/areas` both said
the stores *share* their areas, so three commands reached a store pair whose
lists disagreed. Both problems are one problem: a vocabulary declared at `init`
is declared when the project knows least, and in a graph elaborated backwards
from a sink the areas are a *finding*.

## What that costs, and what pays for it

It costs the typo check, which was the whitelist's real work. That is bought
back in two places, and the division matters. `pending.refuse_area` catches
what a machine can be certain of -- a spelling that normalises to one already
in use, or a slip of a character or two -- and is judged at `pending.stage_all`,
the one staging door, with `--new-area` as the override. Everything wider is a
question about *intent*, which two strings cannot answer: `corpus-design` under
a project that has `corpus` is a sub-area, not a misspelling, and it is neither
refused nor caught. What answers that is `$DG_AREA` for whether an agent may
coin at all, and saying so where staged work is reviewed for whether anybody
should look. See `similar` below.

`refuse_area` lives in `pending` rather than here because it needs a writer --
`pending.owner()` and the launcher's `$DG_AREA` -- while everything in this
module is a question about strings and records that any reader may ask.

## Reading order, and the record that used to render nowhere

Every reader takes the **union** of what is declared and what is used, and the
two renderers used to iterate `for area in g.areas` -- so a record filed under
an unlisted area appeared in no section at all. `validate` made that
unreachable, and dropping the invariant makes it reachable, so `sections`
below is the fix that has to land in the same pass. `order` is its twin for the
sort keys: an unlisted area sorts after every declared one and among its own
kind by name, so unlisted areas group rather than interleaving wherever a
sentinel index happened to fall.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Callable, Iterable
from pathlib import Path

#: What separates one word of an area name from the next, for normalisation.
#: `corpus-design`, `corpus_design` and `corpus design` are one area written
#: three ways, and an exact match after normalising is a typo with near
#: certainty rather than a resemblance worth arguing about.
_SEP = re.compile(r"[-_\s]+")

#: How close two area names have to be before the guard says anything.
#: `difflib` over short lowercase labels, so nothing is added to the dependency
#: footprint -- the same principle `dg-agent setup --plain` is built on, that a
#: guard must need nothing the tool did not already have.
#:
#: **What this number buys, measured rather than assumed.** At 0.8 over short
#: labels it reports a slip of one or two characters and nothing wider:
#: `harnes`/`harness` is 0.92 and reported, `corpus-design`/`corpus` is 0.63
#: and is not. Lowering it far enough to reach the second would flag unrelated
#: short names against each other, and the second is not a typo anyway -- see
#: `similar` below, which is where the argument is. `test_areas.py` pins both
#: sides, because the branch this number governs went a week with no test on it
#: and a docstring that was wrong about it. Audit `R-F3`.
CUTOFF = 0.8

#: How many near matches a refusal names. Three, because the refusal's job is
#: to let somebody recognise the area they meant, and a list long enough to
#: have to scan has stopped doing that.
MATCHES = 3


def normal(area: str) -> str:
    """An area name with the differences that are never meaningful taken out:
    case, surrounding space, and how its words are joined."""
    return _SEP.sub("-", (area or "").strip().casefold()).strip("-")


def area_of(record) -> str | None:
    """One record's area, whether it is a dataclass or the raw dict from a
    store file. Both shapes reach this module, and neither is worth a branch at
    every call site."""
    if isinstance(record, dict):
        return record.get("area")
    return getattr(record, "area", None)


def used(records: Iterable) -> list[str]:
    """The areas records actually carry, first-use order, no repeats."""
    out: list[str] = []
    for rec in records:
        area = area_of(rec)
        if area and area not in out:
            out.append(area)
    return out


def registry(declared: Iterable[str], records: Iterable) -> list[str]:
    """The union a reader consults: declared areas in their order, then areas
    that are used and unlisted, sorted so they group."""
    declared = list(declared)
    return declared + sorted(set(used(records)) - set(declared))


def counts(declared: Iterable[str], records: Iterable) -> dict[str, int]:
    """One store's registry with how much is filed under each.

    A declared area holding nothing keeps its zero row, which is more
    informative than the row vanishing: it is part of how the project was filed,
    and `dg areas` showing the zero is what makes `dg areas prune` a decision
    somebody can take rather than a cleanup that happens on its own.
    """
    out = {a: 0 for a in declared}
    for rec in records:
        area = area_of(rec)
        if area:
            out[area] = out.get(area, 0) + 1
    return out


def order(declared: Iterable[str]) -> Callable[[str], tuple]:
    """A sort key for an area, putting unlisted ones last and together.

    Returns `(rank, name)` rather than a bare rank, so that areas outside the
    declared list group by name instead of interleaving at whatever sentinel
    index they all shared. A caller appends its own id: `(order(v.area), v.id)`.
    """
    ranks = {a: i for i, a in enumerate(declared)}
    size = len(ranks)
    return lambda area: (ranks.get(area, size), "" if area in ranks else area)


def sections(declared: Iterable[str], records: Iterable) -> list[str]:
    """The areas a rendered view has a section for, in the order they appear.

    `registry`, under the name the renderers ask for it by. Named separately
    because the renderers are where getting this wrong was silent: iterating
    the declared list alone dropped a record from the document entirely, with
    nothing above the index to say a section was missing.
    """
    return registry(declared, records)


#: `{(path, mtime, size): counts}`. A guard reading the *other* store reads it
#: off disk -- the caller holds only the one it is writing -- and `vet_all` puts
#: that on the path of every op in a group. Keyed on what a write changes, so a
#: store that moved under us is re-read rather than remembered wrong.
_STORED: dict[tuple, dict[str, int]] = {}


def stored_counts(path: Path | None) -> dict[str, int]:
    """A store's registry, read off disk, for the store the caller does not hold.

    Reads the `areas` list and each record's `area` **as strings**, which is
    what lets one function serve both stores from anywhere: nothing here learns
    what a task is, exactly as `limits.TERSE_FIELDS` is one tuple of string keys
    for both. A store that is absent or will not parse is an empty registry
    rather than an error -- a project with one store is ordinary, and a broken
    twin has its own message elsewhere and must not turn a refusal about an area
    into a traceback.
    """
    if path is None or not path.exists():
        return {}
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        if key in _STORED:
            return _STORED[key]
        raw = json.loads(path.read_text(encoding="utf-8"))
        records = raw.get("vertices") or raw.get("tasks") or []
        got = counts(raw.get("areas") or [], records)
    except (OSError, ValueError, AttributeError):
        return {}
    _STORED[key] = got
    return got


def similar(area: str, known: Iterable[str]) -> list[str]:
    """The areas already in use that this one might be a misspelling of.

    Two branches, and they catch different things.

    **Normalised matches, first and exactly.** `Corpus` against `corpus`,
    `corpus_design` against `corpus-design`: case and how words are joined are
    never meaningful, so an exact match after normalising is a typo with near
    certainty, and reporting one as a mere resemblance would understate it --
    the refusal can name the canonical form.

    **Then `difflib`, which catches a slip of one or two characters** --
    `harnes` against `harness`, `corpu` against `corpus`. That is the whole of
    what it catches at `CUTOFF`, and the whole of what a machine can decide
    from two strings.

    **What it deliberately does NOT catch is a sub-area.** `corpus-design`
    under a project that has `corpus` scores 0.63 and is not reported, and
    lowering `CUTOFF` to reach it would flag every short label against every
    other. That is the right outcome and not a gap: a narrower area is not a
    misspelling of a wider one, no threshold distinguishes the two, and a scout
    naming a corner it has just found is the case dropping the whitelist was
    *for* -- refusing it would put back the wall of identical refusals that
    `integrate.py` could only file as `unexpressible`.

    So *may an agent coin vocabulary at all* is not asked here. It is
    `$DG_AREA` -- `strict` refuses an agent any area new to the union and sends
    it back as a proposal a person reads -- which is the launcher's rule and is
    argued where it is set. And *is this new area worth a second look* is
    answered by saying so where staged work is reviewed, not by refusing it.
    This docstring named `corpus-design` as a case `difflib` caught for a week,
    while no test reached the `difflib` branch at all. Audit `R-F3`.
    """
    want = normal(area)
    by_normal: dict[str, list[str]] = {}
    for a in known:
        by_normal.setdefault(normal(a), []).append(a)
    if want in by_normal:
        return by_normal[want]
    close = difflib.get_close_matches(want, list(by_normal),
                                      n=MATCHES, cutoff=CUTOFF)
    return [a for n in close for a in by_normal[n]]
