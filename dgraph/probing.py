"""The reading behind `dg probe`: every pre-commitment, beside what it is
judged against, and what a domain says about it.

Present mode is the base (proposal §The door, `D72`, `D73`). A record with a
pre-commitment is one of three things, and each is presented beside the
thing a reader would check it against:

- a **decided edge** with a falsifier or a probe — beside the world as the
  reader knows it, and for a PROVISIONAL vertex beside the ancestors whose
  edges are dated after its own (`settled again since this was decided`),
  which is a heuristic and is labelled as one (proposal C5);
- an **unfinished task** with a definition of done — beside the work;
- an **open vertex** with a rule for settling — beside the outcomes of the
  tasks that are evidence for it.

Built over one `by_src`, one reverse index and one `provisional_causes` for
the whole listing, never an accessor per record (proposal E4), and read
against both trays first: a record a staged op already names says so, and
says which of two things that op is — **the act a firing would produce**
(a reopen or a confirm on a decision, a close on an open question, a finish
on a task), in which case `--stage` has nothing to compose (proposal C7); or
merely an op that names the record, a dep or a retitle, which is worth a
line and changes nothing. The two used to read the same, so a staged
dependency would have read as a firing already acted on (audit `N-F4`).
Nothing here writes; the door produces a listing and, one day, ops — R3 is
`domains.evaluate`'s.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dgraph import cross, domains
from dgraph.model import Graph
from dgraph.tasks import TaskGraph

#: Above this many records with a pre-commitment, a bare `dg probe` prints
#: the count and asks for a scope rather than presenting everything.
SCREEN = 40

#: The prose domain's kinds for a pre-commitment that has no probe of its
#: own: the criterion *is* the prose, and `prose` presents it.
FALSIFIER = "prose.falsifier"
DONE_WHEN = "prose.done_when"
RULE = "prose.rule"


@dataclass
class Row:
    """One pre-commitment, ready to present."""

    id: str
    slot: str                       # edge | task | vertex
    title: str
    status: str
    text: str                       # the prose pre-commitment, or ""
    probe: dict | None              # the mechanical twin, or None
    probe_date: str | None = None   # when the live task/vertex probe was written
    beside: list[str] = field(default_factory=list)   # what it is judged against
    staged: str | None = None       # the label of a staged op naming it
    verdict: str = "unjudged"
    sentence: str = ""
    area: str | None = None         # the record's own area, for `--area`

    @property
    def kind(self) -> str:
        if self.probe:
            return self.probe["kind"]
        return {"edge": FALSIFIER, "task": DONE_WHEN, "vertex": RULE}[self.slot]

    def item(self) -> domains.Item:
        args = dict(self.probe["args"]) if self.probe else {"text": self.text}
        return domains.Item(self.id, self.kind, args, self, self.slot)

    @property
    def prefixes(self) -> set[str]:
        """The domain prefixes this row's criterion reaches — one, or for a
        `core.all_of` its members', since a composite is one criterion and
        `--domain` selects records, not members (`D85`)."""
        if self.probe and self.kind == domains.ALL_OF:
            members = self.probe.get("args", {}).get("probes") or []
            return {m["kind"].partition(".")[0] for m in members
                    if isinstance(m, dict) and isinstance(m.get("kind"), str)}
        return {self.kind.partition(".")[0]}


@dataclass
class Scope:
    ids: list[str] = field(default_factory=list)
    provisional: bool = False
    area: str | None = None
    since: str | None = None
    all: bool = False
    #: Domain prefixes (`rocq`, `bench`): records whose probe kind — or any
    #: `core.all_of` member's — carries one. How "run the cheap ones" is
    #: said, and how a slow domain is reached by name (`D85`).
    domain: list[str] = field(default_factory=list)

    @property
    def given(self) -> bool:
        return bool(self.ids or self.provisional or self.area or self.since
                    or self.all or self.domain)


def rows(g: Graph | None, tg: TaskGraph | None,
         d_ops: list[dict] | None = None,
         t_ops: list[dict] | None = None) -> list[Row]:
    """Every record with a pre-commitment, unscoped, over one pass each."""
    out: list[Row] = []
    if g is not None:
        by_src = g.by_src()
        into = g._reverse()
        because = g.provisional_causes()
        later = _later_ancestors(g, by_src, into)
        evidence = _evidence(tg)
        staged_d = _staged(d_ops or [], ("vertex", "from", "id"))
        for vid in sorted(g.vertices):
            v = g.vertices[vid]
            e = g.active_edge(vid, by_src)
            if e is not None and e.decided and (e.falsifier or e.probe):
                beside = []
                if v.base_status == "PROVISIONAL":
                    beside = _provisional_beside(g, vid, e, later.get(vid, []),
                                                 because.get(vid, []), by_src)
                out.append(Row(vid, "edge", v.title, v.status,
                               e.falsifier or "", e.probe, beside=beside,
                               staged=_label("edge", staged_d.get(vid, [])),
                               area=v.area))
            elif not v.settled and (v.rule or v.probe):
                p = v.probe
                out.append(Row(vid, "vertex", v.title, v.status, v.rule or "",
                               p.criterion if p else None,
                               p.date if p else None,
                               beside=evidence.get(vid, []),
                               staged=_label("vertex", staged_d.get(vid, [])),
                               area=v.area))
    if tg is not None:
        staged_t = _staged(t_ops or [], ("task", "from", "id"))
        for tid in sorted(tg.tasks):
            t = tg.tasks[tid]
            if t.unfinished and (t.done_when or t.probe):
                p = t.probe
                beside = [f"{t.status.lower()}"
                          + (f" — {_clip(t.note)}" if t.note else "")]
                out.append(Row(tid, "task", t.title, t.status, t.done_when or "",
                               p.criterion if p else None, p.date if p else None,
                               beside=beside,
                               staged=_label("task", staged_t.get(tid, [])),
                               area=t.area))
    return out


def select(all_rows: list[Row], g: Graph | None, scope: Scope) -> list[Row]:
    """The rows `scope` names. `--all` is every row; nothing given is the
    caller's problem (`ask_for_scope`)."""
    if scope.all:
        return list(all_rows)
    want = set(scope.ids)
    out = []
    for r in all_rows:
        if want and r.id in want:
            out.append(r)
            continue
        if scope.provisional and r.status == "PROVISIONAL":
            out.append(r)
            continue
        if scope.area and _area(r, g) == scope.area:
            out.append(r)
            continue
        if scope.since and _date_of(r, g) and _date_of(r, g) >= scope.since:
            out.append(r)
            continue
        if scope.domain and r.prefixes & set(scope.domain):
            out.append(r)
    return out


def ask_for_scope(all_rows: list[Row], scope: Scope) -> str | None:
    """Why a bare call is refused, or None: more than a screen of records
    and no scope named (proposal E4)."""
    if scope.given or len(all_rows) <= SCREEN:
        return None
    return (f"{len(all_rows)} records carry a pre-commitment — more than a "
            f"screen. Name a scope: an id, --provisional, --area, --since "
            f"DATE, --domain PREFIX, or --all")


def judge(selected: list[Row], root: Path, *,
          timeout: float | None = None) -> list[Row]:
    """Every selected row through the batched evaluator, verdict filled in.

    `timeout` is the door's override; `None` lets each domain's declared
    deadline stand (`D85`). A row whose prefix no installed domain claims is
    `unjudged` with no sentence of its own: the reason is said once per
    prefix, by `not_covered`, not once per row (T71).
    """
    items = [r.item() for r in selected]
    results = domains.evaluate(items, root, timeout=timeout)
    missing = domains.unavailable([r.kind for r in selected])
    for r in selected:
        res = results.get(r.id)
        if res is not None:
            r.verdict, r.sentence = res.verdict, res.sentence
        if r.kind.partition(".")[0] in missing:
            r.sentence = ""
    return selected


def not_covered(selected: list[Row]) -> list[str]:
    """One line per prefix this machine cannot judge, for the door's footer:
    which prefixes the verdict above does not cover, and why."""
    out = []
    for prefix, (why, kinds) in domains.unavailable([r.kind for r in selected]).items():
        n = sum(1 for r in selected if r.kind.partition(".")[0] == prefix)
        out.append(f"`{prefix}.` not judged here — {n} record(s): {why}")
    return out


def findings(selected: list[Row]):
    """The rows as `Violation`s with origin `domain` — what the opt-in and
    the exit code read."""
    items = [r.item() for r in selected]
    results = {r.id: domains.Result(r.verdict, r.sentence) for r in selected}
    return domains.findings(items, results)


# ---- the pieces ------------------------------------------------------------


def _staged(ops: list[dict], keys: tuple[str, ...]) -> dict[str, list[tuple[int, dict]]]:
    """`record -> [(index, op), …]` for every staged op naming a record."""
    out: dict[str, list[tuple[int, dict]]] = {}
    for i, op in enumerate(ops):
        for k in keys:
            rid = op.get(k)
            if isinstance(rid, str):
                out.setdefault(rid, []).append((i, op))
    return out


def _is_act(slot: str, op: dict) -> bool:
    """Whether a staged `op` is the act a firing on this slot would produce
    — the thing `--stage` would otherwise compose (proposal C7)."""
    kind, status = op.get("op"), op.get("status")
    if slot == "edge":
        return kind == "reopen" or (kind == "set_status" and status == "DECIDED")
    if slot == "vertex":
        return kind == "close"
    return kind == "set_status" and status == "DONE"


def _label(slot: str, named: list[tuple[int, dict]]) -> str | None:
    """The line a row prints about the tray: the act if one is staged,
    else the first op that merely names the record."""
    if not named:
        return None
    for i, op in named:
        if _is_act(slot, op):
            return f"{op.get('op')} staged as op {i}"
    i, op = named[0]
    return f"named by a staged {op.get('op')} (op {i}) — not the act a firing would produce"


def _evidence(tg: TaskGraph | None) -> dict[str, list[str]]:
    """Per open question, the outcomes of the work that is evidence for it.

    Through `cross.reverse`, which is the one reading of the link: this
    module prints what the seam says and reasons about none of it.
    """
    if tg is None:
        return {}
    _, informs = cross.reverse(tg)
    out: dict[str, list[str]] = {}
    for did in sorted(informs):
        for tid in sorted(informs[did]):
            t = tg.tasks[tid]
            out.setdefault(did, []).append(
                f"{tid} {t.status}"
                + (f" — {_clip(t.outcome)}" if t.outcome else ""))
    return out


def _later_ancestors(g: Graph, by_src, into) -> dict[str, list[str]]:
    """For every PROVISIONAL vertex, the ancestors whose active edge — or
    latest archived edge — is dated after this vertex's own edge. One walk
    per PROVISIONAL vertex over the shared reverse index; the heuristic C5
    settles for, and labelled as one where it is shown."""
    out: dict[str, list[str]] = {}
    for vid, v in g.vertices.items():
        if v.base_status != "PROVISIONAL":
            continue
        e = g.active_edge(vid, by_src)
        mine = e.date if e is not None else None
        later = []
        for a in sorted(g.ancestors(vid, into)):
            ae = g.active_edge(a, by_src)
            dates = [ae.date] if ae is not None and ae.date else []
            dates += [h.date for h in g.history(a, by_src) if h.date]
            if mine is None or any(d > mine for d in dates):
                later.append(a)
        out[vid] = later
    return out


def _provisional_beside(g: Graph, vid: str, e, later: list[str],
                        causes: list[str], by_src) -> list[str]:
    out = []
    if causes:
        out.append("still under review: " + ", ".join(causes))
    if not later:
        out.append("no premise carries a later date than this edge "
                   f"({e.date or 'undated'}) — the falsifier stands alone")
        return out
    out.append("settled again since this was decided (by the dates, a "
               "heuristic):")
    for a in later:
        ae = g.active_edge(a, by_src)
        hist = g.history(a, by_src)
        old = _clip(hist[-1].answer) if hist else "—"
        new = _clip(ae.answer) if ae is not None and ae.decided else "—"
        out.append(f"  {a} {g.vertices[a].status}: was “{old}” → now “{new}”")
    return out


def _area(r: Row, g: Graph | None) -> str | None:
    """The row's own area, filled in by `rows` for both stores. It used to
    be read off the decision graph, so `--area` reached no task (audit
    `N-F3`)."""
    return r.area


def _date_of(r: Row, g: Graph | None) -> str | None:
    if r.probe_date:
        return r.probe_date
    if g is not None and r.id in g.vertices:
        e = g.active_edge(r.id)
        return e.date if e is not None else None
    return None


def _clip(text: str | None, width: int = 72) -> str:
    if not text:
        return ""
    one = " ".join(text.split())
    return one if len(one) <= width else one[:width - 1] + "…"
