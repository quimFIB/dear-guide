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
UNSETTLED = {"OPEN", "BLOCKED", "REOPENED"}


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
    prefix `D07: ` and a stage-time caller can say it plainly. `ids` is what a
    blocker may name; `of` is the vertex the status belongs to, when there is
    one, so `BLOCKED:<itself>` can be told from `BLOCKED:<other>`.
    """
    base, sep, blocker = status.partition(":")
    if base != "BLOCKED":
        # A blocker on anything else is a dependency asserted by a field that
        # is not read as one — the second copy this model exists to refuse.
        return None if base in SIMPLE_STATUSES and not sep \
            else f"illegal status {status!r}"
    if not blocker:
        return "BLOCKED must name a blocker"
    if of is not None and blocker == of:
        return "blocked by itself"
    if blocker not in ids:
        return f"blocked by unknown vertex {blocker}"
    return None


@dataclass
class Vertex:
    id: str
    title: str
    area: str
    status: str
    note: str | None = None  # prose for a vertex with no decision yet
    format: str | None = None  # the note's dialect: "org", else markdown

    @property
    def base_status(self) -> str:
        return self.status.split(":")[0]

    @property
    def blocker(self) -> str | None:
        return self.status.split(":", 1)[1] if ":" in self.status else None

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
        return cls(
            areas=raw.get("areas", []),
            vertices={v["id"]: Vertex(**v) for v in raw["vertices"]},
            edges=[
                Edge(
                    src=e["from"],
                    to=list(e.get("to", [])),
                    active=e.get("active", True),
                    answer=e.get("answer"),
                    falsifier=e.get("falsifier"),
                    source=e.get("source"),
                    date=e.get("date"),
                    summary=e.get("summary"),
                    replaced_by=e.get("replaced_by"),
                    why=e.get("why"),
                    format=e.get("format"),
                    from_source=e.get("from_source"),
                )
                for e in raw["edges"]
            ],
        )

    def to_dict(self) -> dict:
        def edge_dict(e: Edge) -> dict:
            d: dict = {"from": e.src, "to": e.to, "active": e.active}
            for k in ("answer", "falsifier", "source", "date", "summary",
                      "replaced_by", "why", "format", "from_source"):
                v = getattr(e, k)
                if v is not None:
                    d[k] = v
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
                    k: val
                    for k, val in (
                        ("id", v.id), ("title", v.title), ("area", v.area),
                        ("status", v.status), ("note", v.note),
                        ("format", v.format),
                    )
                    if val is not None
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

    def rejected(self, vid: str) -> list[Edge]:
        """Answers offered to this question and not adopted, oldest first.

        `history`'s counterpart. A reader needs both and needs them apart: one
        says *we changed our mind*, the other says *somebody else answered this
        differently and we did not take it*.
        """
        return sorted(
            (e for e in self.edges
             if e.src == vid and e.from_source is not None),
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

        On a graph with a cycle this can disagree with `depth(vid)` asked one
        vertex at a time, and so can `depth` with itself: an in-cycle parent
        counts 0, so the answer depends on which vertex the walk entered the
        cycle from. `validate` reports a cycle as an error and neither reading
        is meaningful on one. On an acyclic graph — every graph the tool will
        write — the two agree exactly, and the tests pin that.
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
                todo = [q for q in parents
                        if q not in memo and q not in on_path]
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

        for vid, vert in self.vertices.items():
            if not vid.startswith("D") or not vid[1:].isdigit():
                add("ids_wellformed", f"malformed id {vid!r}")
            fault = status_fault(vert.status, ids, of=vid)
            if fault:
                add("status_legal", f"{vid}: {fault}")
            elif vert.base_status == "BLOCKED":
                # Legal and resolving, so what is left are the two rules about
                # what the block *means* — that it is backed by an edge, and
                # that the blocker has not since settled.
                tgt = vert.blocker
                if tgt not in self.depends(vid):
                    # Before the staleness check below, because it is the more
                    # fundamental fault: if the block is not backed by an edge,
                    # whether the blocker has settled does not matter — the
                    # status is wrong either way, and reporting both would
                    # describe one contradiction twice.
                    add(
                        "block_is_a_premise",
                        f"{vid} is BLOCKED:{tgt} but does not rest on {tgt} — "
                        f"a block *is* a dependency, and dependency lives in "
                        f"the edge list, never in a second copy in a status "
                        f"field. `dg dep {vid} --after {tgt}` records it; "
                        f"otherwise {vid} should be OPEN",
                    )
                elif self.vertices[tgt].settled:
                    add(
                        "stale_block",
                        f"{vid} is BLOCKED:{tgt} but {tgt} is now "
                        f"{self.vertices[tgt].status} — nothing is blocking it, "
                        f"so it should be OPEN",
                    )

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
            edge = self.active_edge(vid)
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
            stack = [(vid, iter(self.children(vid)))]
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
                    stack.append((c, iter(self.children(c))))
                    trail.append(c)
                    break
                else:
                    colour[n] = 2
                    stack.pop()
                    trail.pop()

        return v
