"""A probe: the typed criterion a domain could evaluate, and its shape.

`{"kind": "<domain>.<name>", "args": {...}}` — the falsifier's mechanical
twin on a decided edge (`D71`), and the appended, dated rule for settling an
open question or definition of done for a task. This module holds the shape
check and the appended-entry record, and **nothing about either store**: it
is imported by `model` and by `tasks`, which may not see each other
(`tests/test_cross.py`), and a shape that lived in one of them would have
made the other import it. `model` re-exports every name here, so callers
that reach the shape through it still resolve.

The one thing read from outside the two models is `limits.TERSE_DEFAULT`,
through `env`, for the bound on `args`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


def probe_args_limit() -> int:
    """`limits.TERSE_DEFAULT`, fetched when asked.

    Imported inside rather than at the top: `env` is host policy, and
    `model` learning nothing about hosts is a boundary worth one local
    import.
    """
    from dgraph.env import TERSE_DEFAULT
    return TERSE_DEFAULT


@dataclass
class Probe:
    """One criterion written for a task or an open vertex, and when.

    The archived form of the slot `D71` puts on those two records — the
    twin of a task's definition of done, or of an open question's rule for
    settling. Appended and never assigned, exactly as `tasks.Stop` is and
    for the same reason: a pre-commitment that can be rewritten to match
    what was done is not a pre-commitment, and `dg reprobe` is the one door
    that changes it, by adding a dated entry (proposal §Reconciliation C3).
    The last entry is the live one. `dg probe` presents the date beside the
    act, so *probe rewritten today, done fired today* is on the page a
    supervisor reads — visible, not prevented.

    Three fields: the criterion's two, and the date. Not who, for the reason
    `Stop` gives.
    """

    kind: str
    args: dict
    date: str

    @property
    def criterion(self) -> dict:
        """The `{kind, args}` a domain reads — what `probe_fault` judges."""
        return {"kind": self.kind, "args": self.args}


def probes_from(raw, rid: str) -> list[Probe]:
    """The stored `probes` list as records, refused at load if malformed.

    Refused here rather than in `validate`, as a malformed `stops` entry
    is: `Probe(**p)` on a dict missing `date` raises somewhere unhelpful,
    and an entry dropped here would be invisible by the time anything could
    report it. Shared by both stores, since the field is the same shape on
    a task and a vertex and a second copy would drift.
    """
    try:
        return [Probe(**p) for p in (raw or [])]
    except TypeError as exc:
        raise ValueError(
            f"{rid}: malformed probes entry — each needs a `kind`, an "
            f"`args` and a `date`, and nothing else ({exc})") from None


def probes_to(probes: list[Probe]) -> list[dict] | None:
    """The stored form: absent rather than `[]` where there are none."""
    return [{"kind": p.kind, "args": p.args, "date": p.date}
            for p in probes] or None


def probe_entry_fault(p: Probe) -> str | None:
    """`probe_fault` on an appended entry, plus its date."""
    if not p.date:
        return "a probes entry is missing its date"
    return probe_fault(p.criterion)


def probe_fault(probe: object) -> str | None:
    """Why `probe` is not a well-formed criterion, or None if it is.

    The whole of what the core checks about a probe, and the one
    implementation of it: `Graph.validate` (`probe_wellformed`, blocking),
    `pending.vet` for an op arriving as data, `dg decide --probe` and the
    editor's `** Probe` field all ask this and print the sentence it returns.
    Three doors with three copies is how `status_fault` got its docstring.

    The shape is `{"kind": "<domain>.<name>", "args": {...}}` and nothing
    else (`D71`): `kind` a string with a dot separating a non-empty domain
    prefix from a non-empty name, `args` an object, and no third key — a key
    the core carried without a name would be one a domain came to rely on
    and nothing checked. `args` is read no further than its serialised
    length, bounded at `probe_args_limit()`: what is inside it is the
    domain's business, how much of it there is is the store's.
    """
    if not isinstance(probe, dict):
        return (f"a probe is an object {{\"kind\", \"args\"}}, not "
                f"{type(probe).__name__}")
    extra = sorted(k for k in probe if k not in ("kind", "args"))
    if extra:
        return (f"a probe carries only kind and args — "
                f"{', '.join(extra)} is not read by anything")
    fault = kind_fault(probe.get("kind"), "a probe's")
    if fault:
        return fault
    args = probe.get("args")
    if not isinstance(args, dict):
        return ("a probe's args is an object, even an empty one — "
                f"{type(args).__name__} is not")
    size = len(json.dumps(args, ensure_ascii=False, separators=(",", ":")))
    cap = probe_args_limit()
    if size > cap:
        return (f"a probe's args serialise to {size} characters, over the "
                f"{cap} the store holds — args are a fingerprint of an "
                f"artefact, not the artefact; put it in a file under the "
                f"project and name the path")
    return None


# ---- the address ---------------------------------------------------------


@dataclass(frozen=True)
class Bind:
    """What a record is *about*, in a domain's terms: `{kind, ref}`.

    `{"kind": "rocq.constant", "ref": "Closure.closed_under_step"}` — how a
    relation finds its endpoints and how a probe's domain finds its subject.
    Not a claim, so it is not archived; not a wording, so `dg amend` cannot
    reach it. Written by `bind` and `unbind` ops that take the union and the
    difference the way `add_edge` and `remove_edge` do, and for the reason
    proposal C2 gives: a field a `set_fields` assigns has the semantics of a
    scalar, and two clones binding different refs to one record would then
    be a keep-or-take whose only right answer is the union. An edge already
    accumulates; a bind is that kind of thing.

    On the vertex rather than the edge because the subject persists across
    reversals, and an edge is remade by every reopen. Frozen, so a list of
    them can be read as the set it is.
    """

    kind: str
    ref: str

    @property
    def spelled(self) -> str:
        """`kind:ref` — the form `dg bind` takes and the views print."""
        return f"{self.kind}:{self.ref}"


def kind_fault(kind: object, of: str) -> str | None:
    """Why `kind` is not `<domain>.<name>`, or None. `of` names the record
    kind for the sentence — a probe's, a bind's."""
    if not isinstance(kind, str):
        return f"{of} kind is a string like \"prose.rule\""
    domain, dot, name = kind.partition(".")
    if not dot or not domain or not name:
        return f"{of} kind is <domain>.<name> — {kind!r} does not name a domain"
    if kind != kind.strip() or any(c.isspace() for c in kind):
        return f"{of} kind carries no whitespace: {kind!r}"
    return None


def bind_fault(bind: object) -> str | None:
    """Why `bind` is not a well-formed `{kind, ref}`, or None if it is.

    The shape and nothing else, as `probe_fault` is for a probe: the core
    checks that a bind names a domain and a non-empty ref, and reads the
    ref no further — what it means is the domain's business.
    """
    if not isinstance(bind, dict):
        return (f"a bind is an object {{\"kind\", \"ref\"}}, not "
                f"{type(bind).__name__}")
    extra = sorted(k for k in bind if k not in ("kind", "ref"))
    if extra:
        return (f"a bind carries only kind and ref — "
                f"{', '.join(extra)} is not read by anything")
    fault = kind_fault(bind.get("kind"), "a bind's")
    if fault:
        return fault
    ref = bind.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        return "a bind's ref is a non-empty string"
    return None


def spell_bind(text: str) -> dict:
    """`kind:ref` as typed on a command line → `{kind, ref}`.

    Split on the first colon: a kind has none (`kind_fault` says so), and a
    ref may have any number. Not shape-checked here — `bind_fault` is asked
    by every door, and this only unpacks the spelling.
    """
    kind, _, ref = text.partition(":")
    return {"kind": kind, "ref": ref}


def binds_from(raw, rid: str) -> list[Bind]:
    """The stored `binds` list as records, refused at load if malformed —
    `probes_from`'s twin, for the same reason."""
    try:
        return [Bind(**b) for b in (raw or [])]
    except TypeError as exc:
        raise ValueError(
            f"{rid}: malformed binds entry — each needs a `kind` and a "
            f"`ref`, and nothing else ({exc})") from None


def binds_to(binds: list[Bind]) -> list[dict] | None:
    return [{"kind": b.kind, "ref": b.ref} for b in binds] or None


def binds_fault(binds: list[Bind]) -> list[str]:
    """Every fault in a record's binds: each pair's shape, and a duplicate."""
    out = []
    seen = set()
    for b in binds:
        fault = bind_fault({"kind": b.kind, "ref": b.ref})
        if fault:
            out.append(fault)
        elif b in seen:
            out.append(f"bound to {b.spelled} twice — a bind is a set")
        seen.add(b)
    return out
