"""The decision graph: vertices, edges, and the invariants over them.

`decisions.json` is the source of truth; `decision-graph.md` is a rendered view
of it. The schema is a plain graph:

  vertex  a decision that must be made, with an explicit status
  edge    a dependency, which gains a decision payload once that decision is
          made. An edge with no `answer` means "B depends on A, and A is not
          settled yet".

Dependency is therefore the graph structure and is never stored separately —
storing it twice, in two directions, is what let 11 of 55 nodes disagree under
the previous markdown-native format.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from dgraph import areas as _areas
from dgraph import project
from dgraph.probe import (Probe, probe_args_limit,  # noqa: F401 — re-exported
                          probe_entry_fault, probe_fault, probes_from,
                          probes_to)
from dgraph.violation import Violation  # re-exported: callers import it from here
from dgraph.violation import cycle_from

#: What every reader says when a vertex holds more than one active edge. One
#: sentence in one place, because four renderers show an answer and four
#: independent phrasings is how the PARKED reason came to be printed by one of
#: them and dropped by the other two. It names the count, the rule, and the
#: fact that what is shown is arbitrary — that last part being the whole
#: finding: without it a reader concludes the answer they were shown is *the*
#: answer.
def rival_note(n: int) -> str:
    return (f"{n + 1} active edges — this question holds more than one current "
            f"answer, which no `dg` command can write and `dg check` refuses "
            f"as `one_active_edge`. Which one is shown below is arbitrary. It "
            f"is what a git text-merge of two clones leaves behind; "
            f"`dg integrate` is the way to bring one in that does not.")


SIMPLE_STATUSES = {"DECIDED", "OPEN", "REOPENED", "PROVISIONAL"}
UNSETTLED = {"OPEN", "REOPENED"}

#: What a decision *asserts*: the fields two answers are compared on, by
#: `pending.already` when a close meets an edge already decided and by
#: `integrate._same_answer` when an arriving store is read against a base.
#: One tuple, because a field one of the two comparisons forgets is a
#: difference that vanishes at integration.
#: `probe` is in it because it is the falsifier's mechanical twin (`D71`):
#: two closes that agree on every sentence and differ on what a machine would
#: run to overturn them are two claims about what settles the question, and
#: a seam that called them the same would keep one probe and drop the other
#: with nothing recording the choice.
CLAIM = ("answer", "falsifier", "source", "probe")

#: Everything a `close` writes onto the active edge, a `reopen` archives onto
#: the superseded copy and clears from the live one, and a `reject` files
#: beside the answer that stood. `CLAIM` plus when and in what dialect.
#:
#: Read rather than restated by every site that copies a decision from one
#: record to another — the apply path, the archive, the integration seam, the
#: rejected-answer keepsake. Before this tuple existed each of those named the
#: fields by hand, and a copy that names four fields **silently drops** a
#: fifth: the store keeps loading, `dg check` says nothing, and the field is
#: gone from the archive the day somebody adds one. A field added here is
#: carried by all of them, and `tests/test_payload.py` pushes one value per
#: field through every site to prove it.
PAYLOAD = ("answer", "falsifier", "source", "date", "format", "probe")

#: The bound on a probe's serialised `args`, in characters. The synopsis rule
#: (`limits.TERSE_DEFAULT`) applied to the one payload field that is not
#: prose: a probe's arguments are a fingerprint of an artefact — a hash, a
#: path, a name — and never the artefact. A domain that needs more than this
#: puts it in a file under the project and names the path, which is the rule
#: every prose field already follows. Read from `env` rather than restated so
#: the two doors cannot come to differ about what "too long" means.
#:
#: Every optional field an edge record holds in the store, in the order the
#: file writes them. `from_dict` reads exactly these and `to_dict` writes
#: exactly these; `json_import.SCHEMA` accepts exactly these. Anything else on
#: a stored edge is `Edge.extra`.
EDGE_FIELDS = ("answer", "falsifier", "source", "date", "summary",
               "replaced_by", "why", "format", "from_source", "probe")

#: Every field a vertex record holds. `id`, `title`, `area` and `status` are
#: required; the rest optional. Anything else is `Vertex.extra`.
VERTEX_FIELDS = ("id", "title", "area", "status", "note", "format", "probes")

#: What a store written before 2026-09-03 spells a waiting vertex as. Folded to
#: `OPEN` on load and never written again: whether a vertex waits is derived
#: from its edges (`waiting_on`), the way a task's readiness always was. The
#: stored form was a second copy of an edge — `block_is_a_premise` and
#: `stale_block` existed only to keep the copy honest, and a slice that trimmed
#: the edge left the copy naming a vertex it no longer held (audit `U-F1`,
#: `D68`). The premise itself is not touched here: the old rule made it an
#: error for the edge to be missing, so a valid store already has it.
_FOLDED_STATUS = "BLOCKED"


def fold_status(status: str) -> str:
    """`BLOCKED:<id>` and bare `BLOCKED` become `OPEN`; anything else is itself."""
    return "OPEN" if status.partition(":")[0] == _FOLDED_STATUS else status


def status_fault(status: str, ids, of: str | None = None) -> str | None:
    """Why `status` is not a legal status, or None if it is.

    **The one implementation, and there were three.** `Graph.validate`'s
    `status_legal` branch, the pre-staging check now in
    `pending.compose_add`, and an inline
    copy in `pending.vet` — and a fourth route, `dg add --edit`, with no copy at
    all, which was audit F30. Predictably they had drifted:

    - neither stage-time copy noticed a vertex blocked by itself, which
      `validate` refuses;
    - `validate` accepted `OPEN:D99` — it tested `base_status`, so a colon on a
      status that is not `BLOCKED` went unread, while `Vertex.blocker` went on
      reporting `D99` as a blocker nothing checks. Both stage-time copies
      refused it. Folding them together tightens the validator to match, which
      is the right direction: that state is unreachable through the tool and can
      only arrive by hand-edit or merge, which is what `validate` is for.

    Returns the *reason*, without a vertex id in front of it, so `validate` can
    prefix `D07: ` and a stage-time caller can say it plainly. `ids` and `of`
    are kept for the callers that pass them; nothing here reads them since
    `BLOCKED:<id>` stopped being a status (`D68`) — a status that named
    another vertex was a dependency asserted by a field that is not read as
    one, the second copy this model exists to refuse.
    """
    if status in SIMPLE_STATUSES:
        return None
    if status.partition(":")[0] == _FOLDED_STATUS:
        return ("BLOCKED is not stored — a vertex waits when a premise it "
                "rests on is unsettled, so name the premise with --after and "
                "leave the status OPEN")
    return f"illegal status {status!r}"


@dataclass
class Vertex:
    id: str
    title: str
    area: str
    status: str
    note: str | None = None  # prose for a vertex with no decision yet
    format: str | None = None  # the note's dialect: "org", else markdown
    #: Every rule for settling this question that was written down, oldest
    #: first, and never cleared — `Probe` has the argument. Meaningful while
    #: the vertex is unsettled: the answer, once given, carries its own probe
    #: on the edge, and this list is then the record of what the question was
    #: going to be judged by before it was.
    probes: list[Probe] = field(default_factory=list)
    #: Fields the store holds that this version of the tool does not read.
    #: Carried from load to save verbatim, and reported by `dg check` as
    #: `unknown_field`, a warning — from `check.py` and not from `validate`,
    #: for the reason `verbose_field` is: it is not a store invariant, and no
    #: write path may consult it. **Never dropped and never crashed on.**
    #:
    #: The case is version skew, which is this tool's ordinary state: the
    #: plugin cache does not refresh itself, so two clones of one graph run
    #: two versions of `dg` for weeks at a time. Before this field existed an
    #: older install did one of two things with a key it did not know, both
    #: wrong: on an edge it silently dropped the key on its next save, so a
    #: newer install's field vanished from the shared store with nothing to
    #: say so; on a vertex it raised in the constructor, which is a blocking
    #: `store_loads`, and the commit gate then denied **every commit in that
    #: clone** until somebody upgraded. `from_source` went through the first
    #: of those the week it was added. Carrying the key and naming it is the
    #: only behaviour under which a store written by a newer tool is safe in
    #: an older one — which is what lets a field be added at all.
    #:
    #: Not a place to put anything on purpose. A field the tool reads is a
    #: field on the dataclass; this holds what it *cannot* read yet.
    extra: dict = field(default_factory=dict)

    @property
    def probe(self) -> Probe | None:
        """The live rule for settling: the last entry, or None."""
        return self.probes[-1] if self.probes else None

    @property
    def base_status(self) -> str:
        """The status. Once the part of `BLOCKED:<id>` before the colon; kept
        because every reader that wanted the status without a blocker in it
        asked for this, and they were right to."""
        return self.status

    @property
    def settled(self) -> bool:
        return self.base_status not in UNSETTLED


@dataclass
class Edge:
    src: str
    to: list[str]
    active: bool = True
    answer: str | None = None
    falsifier: str | None = None
    source: str | None = None
    date: str | None = None
    summary: str | None = None
    replaced_by: str | None = None  # superseded edges: the answer that won
    why: str | None = None  # superseded edges: what overturned it
    #: Where an answer came from that was **never current here** — the
    #: contribution it arrived in, when somebody kept this store's answer at
    #: the integration seam instead.
    #:
    #: The third kind of edge, and it has to be a third rather than reusing
    #: `active: false`, because an inactive edge means *this was the answer,
    #: and then it was overturned*. File a rejected one that way and `dg node`
    #: renders it under **Superseded** with an empty `why`, asserting it was
    #: once current when it never was — which is a claim about this project's
    #: history that nobody made. Without somewhere honest to put it, the seam
    #: is a choice between losing an answer and lying about it.
    #:
    #: `active` is False on these too: they are not the answer that stands.
    #: What separates them from history is that they have no `why` and no
    #: `replaced_by` — nothing overturned them — and `Graph.history` leaves
    #: them out for that reason. `Graph.rejected` is how to read them.
    from_source: str | None = None
    #: Provenance of the prose the views render from this record — the
    #: answer/falsifier of an active edge, the why/summary of a superseded one:
    #: "org" when composed through the editor, else markdown. `replaced_by` is
    #: written later by a different op and is deliberately not covered.
    format: str | None = None
    #: The falsifier's mechanical twin (`D71`): `{"kind", "args"}`, a
    #: criterion some domain could evaluate to say whether this answer has
    #: been overturned. In `PAYLOAD`, so it is written by the close, archived
    #: by a reopen with the answer it belonged to and cleared from the live
    #: edge — a probe that outlived its answer would fire against a decision
    #: nobody holds. Shape-checked by `probe_fault` and read no further here:
    #: evaluating one is a domain's job (`dgraph.domains`, when it lands).
    probe: dict | None = None
    #: As `Vertex.extra`: what the store holds and this version cannot read,
    #: carried verbatim and warned about. Stays on the live edge across a
    #: reopen and is not copied to the archive, because what an unknown field
    #: *means* under a reopen — a claim to archive, or an address to keep — is
    #: exactly what this version does not know; the install that does know
    #: puts the field in `PAYLOAD` and the archive follows.
    extra: dict = field(default_factory=dict)

    @property
    def decided(self) -> bool:
        """An edge with a payload records a decision; without one it is only a
        declared dependency awaiting the source vertex being settled."""
        return self.answer is not None

    @property
    def terminal(self) -> bool:
        return self.decided and not self.to


@dataclass
class Graph:
    areas: list[str] = field(default_factory=list)
    vertices: dict[str, Vertex] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    # ---- construction ----------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> Graph:
        raw = json.loads((path or project.find().store).read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> Graph:
        """Build a graph from the parsed store, without reading a file.

        Split out of `load` so `dgraph.json_import` can shape-check a candidate
        document and then build it through this exact path. Two construction
        paths would be two schemas, and the one nobody ran would drift.
        """
        # Refused here, not in validate(): building the vertex dict collapses
        # duplicates (last one wins), so by the time validate() runs, the
        # discarded decision is invisible and "unique ids" can never fire.
        counts = Counter(v["id"] for v in raw["vertices"])
        dupes = sorted(i for i, n in counts.items() if n > 1)
        if dupes:
            raise ValueError(
                f"duplicate vertex id(s): {', '.join(dupes)} — one entry per "
                f"id; merge or renumber them by hand"
            )
        # Known keys by name, everything else into `extra` — never `**v`,
        # which is what made an unknown vertex key a crash (see `Vertex.extra`).
        known_e = {"from", "to", "active", *EDGE_FIELDS}
        return cls(
            areas=raw.get("areas", []),
            vertices={v["id"]: Vertex(
                **{k: v[k] for k in VERTEX_FIELDS
                   if k in v and k not in ("status", "probes")},
                status=fold_status(v["status"]),
                probes=probes_from(v.get("probes"), v["id"]),
                extra={k: x for k, x in v.items() if k not in VERTEX_FIELDS})
                for v in raw["vertices"]},
            edges=[
                Edge(
                    src=e["from"],
                    to=list(e.get("to", [])),
                    active=e.get("active", True),
                    **{k: e.get(k) for k in EDGE_FIELDS},
                    extra={k: x for k, x in e.items() if k not in known_e},
                )
                for e in raw["edges"]
            ],
        )

    def to_dict(self) -> dict:
        def edge_dict(e: Edge) -> dict:
            d: dict = {"from": e.src, "to": e.to, "active": e.active}
            for k in EDGE_FIELDS:
                v = getattr(e, k)
                if v is not None:
                    d[k] = v
            d.update(e.extra)      # written back as read: see `Vertex.extra`
            return d

        # `areas` is a registry rather than a whitelist, so a record may
        # legitimately be filed under an area the list does not mention — a
        # store written before the list stopped being enforced, a hand-edited
        # one, an imported one. Unlisted areas therefore sort *after* every
        # declared one and among themselves by name, so they group rather than
        # interleaving at whatever `99` happened to collide with.
        order = _areas.order(self.areas)
        verts = sorted(
            self.vertices.values(), key=lambda v: (order(v.area), v.id)
        )
        edges = sorted(self.edges, key=lambda e: (e.src, not e.active, e.date or ""))
        return {
            "areas": self.areas,
            "vertices": [
                {
                    **{k: val for k, val in (
                        ("id", v.id), ("title", v.title), ("area", v.area),
                        ("status", v.status), ("note", v.note),
                        ("format", v.format), ("probes", probes_to(v.probes)),
                    ) if val is not None},
                    **v.extra,     # written back as read: see `Vertex.extra`
                }
                for v in verts
            ],
            "edges": [edge_dict(e) for e in edges],
        }

    def save(self, path: Path | None = None) -> None:
        text = json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"
        project.write_atomic(path or project.find().store, text)

    # ---- queries ---------------------------------------------------------

    def by_src(self) -> dict[str, list[Edge]]:
        """`src -> its edge records, in store order`, in one pass.

        The five readers below each scan the whole edge list to answer about
        one vertex, and every surface that shows a store asks all of them for
        every vertex — `dg find`, and the web view's payload. Grouping first
        turns each of those from a scan into a dict lookup.

        Store order is preserved because `active_edge` is *first*-wins where a
        store holds rival answers, and a grouping that reordered the records
        would silently pick a different answer.

        Public, unlike `_reverse`, because callers outside this module do the
        per-vertex looping — but built inside a call and dropped with it just
        the same. Nothing here caches it, and no write path has to clear it:
        one pass over 11,000 records costs under a millisecond, which is less
        than a single one of the scans it replaces.
        """
        out: dict[str, list[Edge]] = {}
        for e in self.edges:
            out.setdefault(e.src, []).append(e)
        return out

    def active_edge(self, vid: str, _by=None) -> Edge | None:
        """The current answer, or None. **First wins where there are two.**

        Which is the right behaviour for a traversal — `children` needs an
        answer to follow and any of them will do — and the wrong one for a
        reader, which is what `F-F4` is. Anything that *shows* an answer to a
        person asks `rival_answers` first.
        """
        if _by is not None:
            for e in _by.get(vid, ()):
                if e.active:
                    return e
            return None
        for e in self.edges:
            if e.src == vid and e.active:
                return e
        return None

    def rival_answers(self, vid: str, _by=None) -> list[Edge]:
        """The active edges beyond the first, where a store holds more than one.

        Empty in every store this tool can produce: `one_active_edge` refuses
        two, blocking, so no `dg apply` writes one. What does produce one is a
        git text-merge of two clones that each settled the same inherited
        vertex — the store loads, `dg check` refuses it, and every reader that
        asks `active_edge` shows one answer with no sign the other exists. The
        reader is told something false and cannot tell.

        So this is not a query anybody has a use for; it is the thing four
        renderers have to ask before they print an answer, kept in one place
        so that four of them cannot phrase it four ways — the rule
        `stop_label` and `done_label` already follow.
        """
        if _by is not None:
            return [e for e in _by.get(vid, ()) if e.active][1:]
        return [e for e in self.edges if e.src == vid and e.active][1:]

    def history(self, vid: str, _by=None) -> list[Edge]:
        """Superseded decisions for a vertex, oldest first.

        **Not every inactive edge.** An answer that arrived at the integration
        seam and was not adopted is inactive too, and it is not history: it was
        never the answer, so nothing overturned it and it has no place in a
        list of reversals. `rejected` reads those. The separation is what stops
        `dg node` claiming a project once believed something it never did — and
        it is what stops `dg integrate` reading a rejected answer as a reopen,
        since that derivation counts archived edges to recover the acts.
        """
        rows = (e for e in _by.get(vid, ())) if _by is not None else (
            e for e in self.edges if e.src == vid)
        return sorted(
            (e for e in rows if not e.active and e.from_source is None),
            key=lambda e: e.date or "",
        )

    def rejected(self, vid: str, _by=None) -> list[Edge]:
        """Answers offered to this question and not adopted, oldest first.

        `history`'s counterpart. A reader needs both and needs them apart: one
        says *we changed our mind*, the other says *somebody else answered this
        differently and we did not take it*.

        Takes the grouping for the same reason `history` does, and it is the
        reason the pair has to be read together: both doors that ask this ask
        it once per vertex — the renderer and the web payload — and on a store
        with no declined answers at all it still scanned every edge to find
        none. Empty answer, full price.
        """
        rows = _by.get(vid, ()) if _by is not None else (
            e for e in self.edges if e.src == vid)
        return sorted(
            (e for e in rows if e.from_source is not None),
            key=lambda e: e.date or "",
        )

    def children(self, vid: str, _by=None) -> list[str]:
        """The vertices this decision opens.

        A target naming no vertex is skipped, for the reason `depends` gives
        below — the two directions of the same walk, so they filter the same
        way. Only the traversal is filtered: the edge keeps the id, `validate`
        reports it, and every write path unions against `Edge.to` rather than
        against this, so a dangling target is never dropped from the store.
        """
        e = self.active_edge(vid, _by)
        return [t for t in e.to if t in self.vertices] if e else []

    def _reverse(self) -> dict[str, set[str]]:
        """`target -> the sources that point at it`, in one pass over the edges.

        The same relation `depends` computes, built for every vertex at once.
        It is deliberately **not** cached on the graph: callers build it inside
        one call and drop it, so there is no lifetime to reason about and no
        write path that has to remember to clear it. A caller that holds one
        across a mutation is holding a stale answer, which is why nothing here
        returns it to the outside.
        """
        into: dict[str, set[str]] = {}
        for e in self.edges:
            if e.active and e.src in self.vertices:
                for t in e.to:
                    into.setdefault(t, set()).add(e.src)
        return into

    def depends(self, vid: str, _into: dict[str, set[str]] | None = None) -> list[str]:
        """Derived, never stored: the parents that point at this vertex.

        An edge source that names no vertex is skipped: it cannot be a premise,
        and `validate()` reports the dangling edge itself. Returning it here
        would make every traversal that looks premises up crash on a graph
        `validate()` is in the middle of judging.

        `_into` is a reverse index from `_reverse`, for callers asking this of
        every vertex in turn — one scan of the edge list instead of one per
        vertex. It is an optimisation and nothing else: the answer is identical,
        and passing nothing recomputes it the direct way.
        """
        if _into is not None:
            return sorted(_into.get(vid, ()))
        return sorted(
            {e.src for e in self.edges
             if e.active and vid in e.to and e.src in self.vertices}
        )

    def waiting_on(self, vid: str,
                   _into: dict[str, set[str]] | None = None) -> list[str]:
        """The premises this vertex rests on that are not settled yet.

        One implementation because there are three callers with the same
        question — `dg show`'s "Waiting on" column, the `propagation` check
        below, and the brief — and a vertex whose premises differ between two
        of them is exactly the disagreement this tool exists to prevent.
        """
        return [p for p in self.depends(vid, _into)
                if not self.vertices[p].settled]

    def _forward(self) -> dict[str, list[str]]:
        """`src -> the vertices its active edge opens`, in one pass.

        The `children` side of `_reverse`, and it has to keep `active_edge`'s
        first-wins rule: where a store holds rival answers, `children` follows
        the first and `depends` sees them all, so the two directions are *not*
        transposes of each other and cannot share one structure. Built inside
        the call and dropped with it, as everything derived here is.
        """
        out: dict[str, list[str]] = {}
        for e in self.edges:
            if e.active and e.src not in out:
                out[e.src] = [x for x in e.to if x in self.vertices]
        return out

    def descendants(self, vid: str) -> set[str]:
        # One forward index for the whole walk. Without it every step rescans
        # the edge list to find one active edge.
        kids = self._forward()
        seen: set[str] = set()
        stack = list(kids.get(vid, ()))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(kids.get(cur, ()))
        return seen

    def ancestors(self, vid: str,
                  _into: dict[str, set[str]] | None = None) -> set[str]:
        """Everything `vid` rests on, transitively.

        Builds its own reverse index when the caller has none. Without one this
        rescanned the whole edge list at every step of the walk, which on a
        10,000-vertex store was 3.3 s for a single question — `dg find above:`
        and `dg context` each ask it once, and paid that. `_into` is still
        taken so a caller already holding one does not build a second.
        """
        if _into is None:
            _into = self._reverse()
        seen: set[str] = set()
        stack = list(self.depends(vid, _into))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(self.depends(cur, _into))
        return seen

    def provisional_because(self, vid: str,
                            _into: dict[str, set[str]] | None = None) -> list[str]:
        """The premises under review that a PROVISIONAL vertex rests on.

        The transitive form of `waiting_on`: `pending.expand` marks every decided
        descendant of a reopened vertex PROVISIONAL, so the cause can be any
        distance up the chain.

        The store never records *why* a vertex is PROVISIONAL — `derived_from`
        lives only in the staged op — so this is recomputed, and an empty result
        is meaningful: every premise has been settled again and the vertex is
        waiting for someone to re-examine it (`stale_provisional`).
        """
        return sorted(
            a for a in self.ancestors(vid, _into) if not self.vertices[a].settled
        )

    def provisional_causes(self) -> dict[str, list[str]]:
        """Every PROVISIONAL vertex's unsettled premises, over one index.

        `provisional_because` per vertex is a full upward walk *and* rebuilds
        the adjacency at every step of it, which is the cubic shape `validate`
        used to have — `dg brief` reaches it by this route rather than through
        `validate`, so removing it there did not remove it here. Sharing one
        reverse index across the walks leaves the walks and drops the rescan.

        Still one walk per vertex, so this is quadratic and not linear. The
        boolean question — *is anything unsettled above this?* — is answered
        for every vertex at once by `stale_provisional`; this one returns
        *which* premises, which a reader has to be told one vertex at a time.
        """
        into = self._reverse()
        prov = [vid for vid, v in self.vertices.items()
                if v.base_status == "PROVISIONAL"]
        if not prov:
            return {}

        # One pass in topological order instead of one upward walk per
        # PROVISIONAL vertex. Each vertex's unsettled ancestors are the union
        # of its parents', plus any parent that is itself unsettled — so
        # carrying that set down the order answers every vertex at once. The
        # set is a Python int used as a bitset, one bit per unsettled vertex,
        # because the union is then a single `|` rather than a set merge.
        unsettled = [v for v, vert in self.vertices.items() if not vert.settled]
        bit = {v: 1 << i for i, v in enumerate(unsettled)}
        indeg = {v: len(into.get(v, ())) for v in self.vertices}
        kids: dict[str, list[str]] = {}
        for tgt, srcs in into.items():
            if tgt not in self.vertices:
                continue          # a dangling target: `validate` reports it,
            for s in srcs:        # and a walk that trusts it crashes first
                kids.setdefault(s, []).append(tgt)
        order: list[str] = []
        stack = [v for v, d in indeg.items() if d == 0]
        while stack:
            n = stack.pop()
            order.append(n)
            for c in kids.get(n, ()):
                indeg[c] -= 1
                if indeg[c] == 0:
                    stack.append(c)

        if len(order) != len(self.vertices):
            # A cycle: the order does not cover it, and a vertex inside one has
            # no well-defined set of ancestors *above* it. `validate` reports
            # the cycle; until somebody fixes it, fall back to the walk, which
            # has its own `seen` guard and answers something rather than
            # silently claiming those vertices rest on nothing.
            return {vid: self.provisional_because(vid, into) for vid in prov}

        mask: dict[str, int] = {}
        for v in order:
            m = 0
            for parent in into.get(v, ()):
                m |= mask.get(parent, 0) | bit.get(parent, 0)
            mask[v] = m
        # Testing every unsettled vertex against every PROVISIONAL one, which
        # is O(provisional x unsettled) and only bites on a graph already in
        # trouble -- 1.2 s at 3,000 of each, against 66 ms on a real
        # 10,000-vertex store where a hundred vertices are PROVISIONAL.
        #
        # Iterating the set bits instead (`m & -m`) was tried and is *slower*:
        # where the answers are large the bit loop does the same work in Python
        # that this does in C, and where they are small this was never the
        # cost. Left as the simpler of two equals.
        return {vid: sorted(u for u in unsettled if mask[vid] & bit[u])
                for vid in prov}

    def stale_provisional(self) -> list[str]:
        """The PROVISIONAL vertices whose every premise has been settled again.

        `provisional_because(vid)` answers this one vertex at a time, and each
        call is a full upward walk — so asking it once per PROVISIONAL vertex
        was the cubic term in `validate`, and the largest single cost the tool
        had. This asks the question the other way round and pays for one walk
        total.

        A vertex rests on an unsettled premise exactly when it is *reachable*,
        along active edges, from some unsettled vertex. So collecting the
        descendants of the whole unsettled set in one pass answers every
        vertex's question at once, and what is left over is this list.

        The walk follows every active edge rather than `children`, which takes
        only the first: `provisional_because` reaches through `depends`, which
        sees them all, and the two must agree on a store holding rival answers
        — `validate` has to survive one in order to report it.
        """
        prov = [vid for vid, v in self.vertices.items()
                if v.base_status == "PROVISIONAL"]
        if not prov:
            # The walk below is one pass over every edge, and with nothing
            # PROVISIONAL there is no question for it to answer. Worth the two
            # lines: the per-vertex form this replaced short-circuited here for
            # free — it never started a walk — so without this the rewrite is
            # *slower* on a large store that happens to have nothing under
            # review, which is an ordinary state and not a corner case.
            return []
        kids: dict[str, list[str]] = {}
        for e in self.edges:
            if e.active:
                for t in e.to:
                    if t in self.vertices:
                        kids.setdefault(e.src, []).append(t)
        seen: set[str] = set()
        stack = [v for v, vert in self.vertices.items() if not vert.settled]
        while stack:
            for nxt in kids.get(stack.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return [vid for vid in prov if vid not in seen]

    def unpropagated(self) -> list[tuple[str, str]]:
        """DECIDED vertices resting on an unsettled premise, each with it.

        The `propagation` rule as data. `validate` turns each pair into a
        finding and `pending.repairs` turns each into the op that clears it, so
        the rule has one implementation and the two cannot disagree about which
        vertices are affected — the same reason `waiting_on` has one
        implementation and three callers.
        """
        decided = [(vid, v) for vid, v in sorted(self.vertices.items())
                   if v.base_status == "DECIDED"]
        if not decided:
            return []                      # as `stale_provisional`, same reason
        into = self._reverse()
        return [(vid, p) for vid, v in decided
                for p in self.waiting_on(vid, into)]

    def roots(self) -> list[str]:
        into = self._reverse()
        return sorted(v for v in self.vertices if not into.get(v))

    def frontier(self) -> list[str]:
        return sorted(
            v.id for v in self.vertices.values() if v.base_status in UNSETTLED
        )

    def depth(self, vid: str) -> int:
        """Longest path from any root — the rank used for graph layout."""
        return self.depths(vid)[vid]

    def depths(self, vid: str, _into: dict[str, set[str]] | None = None) -> dict[str, int]:
        """The depth of `vid` *and* of everything it rests on, in one walk.

        The walk already computes every ancestor's depth on its way to `vid`
        and used to throw them away, so a caller wanting the depth of a whole
        chain paid for the traversal once per node — quadratic in the chain,
        which on a deep graph is the difference between instant and hanging.

        Iterative on an explicit stack: a chain a few hundred decisions deep is
        a legitimate graph and must not hit the recursion limit.
        """
        if _into is None:
            _into = self._reverse()   # as `ancestors`: one index for the walk,
        memo: dict[str, int] = {}     # not a rescan of the edge list per step
        on_path: set[str] = set()  # cycle guard; validate() reports cycles properly
        stack = [vid]
        while stack:
            n = stack[-1]
            if n in memo:
                stack.pop()
                continue
            on_path.add(n)
            todo = [p for p in self.depends(n, _into)
                    if p not in memo and p not in on_path]
            if todo:
                stack.extend(todo)
                continue
            parents = self.depends(n, _into)
            memo[n] = 0 if not parents else 1 + max(
                memo.get(p, 0) for p in parents  # .get: an in-cycle parent counts 0
            )
            on_path.discard(n)
            stack.pop()
        return memo

    def all_depths(self) -> dict[str, int]:
        """Every vertex's depth, in one shared memo.

        `depths(vid)` already keeps what it computes on the way to one vertex,
        but `depth(vid)` throws that memo away when it returns — so asking for
        the depth of *every* vertex, which is what the web view's payload does,
        pays for the whole traversal once per vertex. This runs the same walk
        from every unvisited start and keeps one memo across all of them, over
        one shared reverse index.

        Where the relation has a cycle the shared memo is order-dependent — an
        in-cycle parent counts 0, so the answer turns on which vertex the walk
        entered the cycle from, and `depth(vid)` asked one vertex at a time
        enters from somewhere else. So this falls back to the per-vertex walk
        there, exactly as `provisional_causes` does and for the same reason:
        two readings of one record, on two surfaces, is worse than paying for
        the slow one on a store that is already broken.

        **The fallback is not conditioned on `validate`, and must not be.** The
        earlier version of this docstring excused the disagreement on the
        ground that *"`validate` reports a cycle as an error"*, and that is not
        true of every cycle this walk can meet. `validate`'s acyclic check
        follows `children`, which takes only the *first* active edge; this walk
        follows `depends`, which sees them all. A vertex carrying two active
        edges — what a git text-merge of two clones produces, and what
        `rival_answers` exists for — can therefore close a cycle that the
        depth walks see and the cycle check does not, and `validate` reports
        `one_active_edge` while saying nothing about `acyclic`. The store loads,
        `dg serve` draws it, and the layout disagreed with `dg context` with
        nothing on either surface to explain why.
        """
        into = self._reverse()
        memo: dict[str, int] = {}
        for start in self.vertices:
            if start in memo:
                continue
            on_path: set[str] = set()
            stack = [start]
            while stack:
                n = stack[-1]
                if n in memo:
                    stack.pop()
                    continue
                on_path.add(n)
                parents = self.depends(n, into)
                todo = []
                for q in parents:
                    if q in on_path:
                        # The walk has come back to a vertex it is still
                        # inside: a cycle in `depends`. Detected here rather
                        # than by a topological pre-pass, so the ordinary
                        # acyclic store pays nothing for the guard — a
                        # pre-pass cost 1.6x of this whole function on a
                        # 10,000-vertex store, to answer a question the walk
                        # was already in a position to answer.
                        return {vid: self.depths(vid, into)[vid]
                                for vid in self.vertices}
                    if q not in memo:
                        todo.append(q)
                if todo:
                    stack.extend(todo)
                    continue
                memo[n] = 0 if not parents else 1 + max(
                    memo.get(q, 0) for q in parents
                )
                on_path.discard(n)
                stack.pop()
        return memo

    def path(self, a: str, b: str) -> list[str] | None:
        """Any decision path from a to b, following active edges."""
        stack = [(a, [a])]
        seen = set()
        while stack:
            cur, trail = stack.pop()
            if cur == b:
                return trail
            if cur in seen:
                continue
            seen.add(cur)
            for nxt in self.children(cur):
                stack.append((nxt, trail + [nxt]))
        return None

    # ---- invariants ------------------------------------------------------

    def validate(self) -> list[Violation]:
        v: list[Violation] = []
        def add(c, m, severity="error"):
            v.append(Violation(c, m, severity))
        ids = set(self.vertices)
        by_src = self.by_src()   # one grouping for the per-vertex reads below

        for vid, vert in self.vertices.items():
            if not vid.startswith("D") or not vid[1:].isdigit():
                add("ids_wellformed", f"malformed id {vid!r}")
            fault = status_fault(vert.status, ids, of=vid)
            if fault:
                add("status_legal", f"{vid}: {fault}")
            for p in vert.probes:
                fault = probe_entry_fault(p)
                if fault:
                    add("probe_wellformed", f"{vid}: {fault}")
            # There is no rule about a *waiting* vertex, and its absence is the
            # point: whether a vertex waits is `waiting_on`, read off the
            # edges, so nothing stored can disagree with it. `block_is_a_premise`
            # and `stale_block` were the two rules that kept a stored copy
            # honest, and went with the copy (`D68`).

        seen_active: set[str] = set()
        for e in self.edges:
            if e.src not in ids:
                add("no_dangling_refs", f"edge from unknown vertex {e.src}")
                continue
            for t in e.to:
                # Targets are only checked on an *active* edge. A superseded one
                # records what an answer opened at the time, and `dg rm` deletes
                # vertices that a past answer may perfectly well have opened —
                # so a target naming nothing is a historical fact there, not a
                # broken reference, and reporting it forever would be a blocking
                # finding with no repair. Nothing can introduce a *new* dangling
                # target into an inactive edge: `reopen` copies `to` from an
                # active edge that has already been validated, and
                # `remove_vertex` leaves inactive edges alone.
                if t not in ids and e.active:
                    add("no_dangling_refs", f"edge {e.src} -> unknown vertex {t}")
                if t == e.src:
                    add("no_dangling_refs", f"{e.src}: edge to itself")
            if e.probe is not None:
                # Every edge, archived ones included: a probe was checked at
                # the door when it was staged, so one that fails here arrived
                # by hand-edit or merge, and an archived probe is still the
                # record of what a past answer pre-committed to.
                fault = probe_fault(e.probe)
                if fault:
                    add("probe_wellformed", f"{e.src}: {fault}")
            if e.from_source is not None:
                # A record of an answer this project did **not** take. It has
                # to say whose it was and what it said, or it is an empty
                # inactive edge asserting nothing — and it must carry neither
                # `why` nor `replaced_by`, because those are history's fields
                # and nothing overturned this: it was never current.
                missing = [f for f in ("answer", "source")
                           if not getattr(e, f)]
                if missing:
                    add("rejected_complete",
                        f"{e.src}: an answer recorded as not adopted is "
                        f"missing its {' and '.join(missing)}")
                stale = [f for f in ("why", "replaced_by") if getattr(e, f)]
                if stale:
                    add("rejected_complete",
                        f"{e.src}: an answer that was never current carries "
                        f"{' and '.join(stale)} — those say a decision was "
                        f"overturned, and this one was declined")
                if e.active:
                    add("rejected_complete",
                        f"{e.src}: an answer marked as not adopted is the "
                        f"active edge — it cannot both stand and have been "
                        f"declined")
            if e.active:
                if e.src in seen_active:
                    add(
                        "one_active_edge",
                        f"{e.src}: more than one active edge — a question has one "
                        f"current answer; supersede the old one instead",
                    )
                seen_active.add(e.src)

        for vid, vert in self.vertices.items():
            edge = self.active_edge(vid, by_src)
            if vert.base_status == "DECIDED":
                if edge is None or not edge.decided:
                    add(
                        "decided_complete",
                        f"{vid}: DECIDED with no decision edge carrying an answer",
                    )
                    continue
                for fld in ("source", "date"):
                    if not getattr(edge, fld):
                        add("decided_complete", f"{vid}: DECIDED without a {fld}")
                if not edge.falsifier and edge.to:
                    add(
                        "decided_complete",
                        f"{vid}: DECIDED without a falsifier. State what evidence "
                        f"would reopen it, or make it terminal.",
                    )
            elif vert.base_status in UNSETTLED and edge is not None and edge.decided:
                add(
                    "open_not_overspecified",
                    f"{vid} is {vert.status} but carries a decided edge",
                )

        for vid in self.stale_provisional():
            add(
                "stale_provisional",
                f"{vid} is PROVISIONAL but every premise it rests on is "
                f"settled again — re-examine it, then `dg confirm {vid}`",
                "warning",
            )

        for vid, p in self.unpropagated():
            add(
                "propagation",
                f"{vid} is DECIDED but rests on {p} "
                f"({self.vertices[p].status}) — `dg repair` marks it "
                f"PROVISIONAL, or settle the premise with `dg decide {p}`",
            )

        touched = {e.src for e in self.edges} | {
            t for e in self.edges for t in e.to
        }
        for vid in sorted(ids - touched):
            add("no_orphans", f"{vid} is connected to nothing", "warning")

        # Iterative DFS colouring, for the same reason `depth` is iterative: a
        # deep chain is a legal graph, and the validator crashing on one would
        # be the fail-open the check exists to prevent.
        colour: dict[str, int] = {}
        for vid in self.vertices:
            if colour.get(vid):
                continue
            colour[vid] = 1
            stack = [(vid, iter(self.children(vid, by_src)))]
            trail = [vid]
            while stack:
                n, kids = stack[-1]
                for c in kids:          # `children` has already dropped ids
                    if colour.get(c) == 1:   # naming no vertex
                        add("acyclic",
                            f"cycle: {' -> '.join(cycle_from(trail, c))}")
                        continue
                    if colour.get(c) == 2:
                        continue
                    colour[c] = 1
                    stack.append((c, iter(self.children(c, by_src))))
                    trail.append(c)
                    break
                else:
                    colour[n] = 2
                    stack.pop()
                    trail.pop()

        return v
