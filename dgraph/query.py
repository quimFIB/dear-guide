"""Selecting records by what they say, not by where they sit in the graph.

Every other reading in this tool is structural: `dg show` starts at the
frontier, `dg context` and `dg tree` start at an id you already have, `dg path`
at two. None of them gets you from a **word** to an id. This module is that
step, and nothing more — it returns a set of ids, which the caller renders in
the shape it already uses for everything else.

**A selector, not a sixth view.** Three rules keep it one:

- every ``field:`` name is a field name in the store, taken from the
  dataclasses rather than retyped beside them, so the query vocabulary cannot
  drift from the schema;
- every ``is:`` predicate delegates to a method that already exists — a query
  language that re-derived ``ready`` would be a second opinion about the graph,
  printed with the authority of the first;
- rendering happens in the caller, through `dgraph.compact`.

**What this module may not know.** It never names `because` or `evidence_for`.
Those are the cross-graph link, and `dgraph/cross.py` owns what the link
*means* — including `rests_on`, which looks like a plain string comparison and
is really the derived reverse of the relation. `tests/test_cross.py` scans every
module outside a four-name allowlist for those fields, and this one is inside
that scan. Terms over the link therefore arrive through `Lens.structural`,
supplied by a caller that is allowed to reason about it; `Lens.hide` is how that
caller also removes them from the generic field table it would otherwise be
matched by. See `dgraph/cli.py`'s `_lenses`.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

#: Prose a bare word searches. Deliberately not ids, statuses or areas: if
#: `dg find open` matched every OPEN vertex then the most natural query anybody
#: could type would return most of the store, and "open" — a word that appears
#: constantly in real answers — would stop being searchable. `status:OPEN` is
#: one keystroke more and says what it means.
PROSE = ("title", "note", "answer", "falsifier", "summary", "why", "outcome",
         "source")

#: Fields compared as dates rather than matched as text, so `date:>2026-01-01`
#: means what it looks like. ISO-8601 sorts lexicographically, which is why the
#: comparison needs no parsing and a bare `date:2026-01` still prefix-matches.
DATES = ("date", "done")


#: Marks a tokenised value as a regex. A control character, so it cannot occur
#: in a query somebody typed and needs no escaping anywhere.
REGEX_MARK = "\0"


class Fault(Exception):
    """A query that cannot be answered as asked.

    Carries the column so the caller can point at it. This is deliberately the
    same exception for a malformed query, an unknown field and an unavailable
    predicate: all three mean "you did not ask what you think you asked", and
    what matters is that every one of them is distinguishable from *no matches*.
    Collapsing those two is how an empty result becomes a false fact.
    """

    def __init__(self, reason: str, column: int = 0) -> None:
        super().__init__(reason)
        self.reason = reason
        self.column = column


# ---- the parsed form -----------------------------------------------------


@dataclass(frozen=True)
class Value:
    """The right-hand side of a term, and how it is compared.

    `regex` and `phrase` exist so that fuzziness is something the reader wrote
    down and can therefore check — the trade the whole design makes. Matching
    is case-insensitive throughout, including regexes: a store mixes prose with
    identifiers, and a reader who wanted case would have said so with `/…/` if
    the flag existed, which is a knob this can grow later without breaking a
    query that works today.
    """

    raw: str
    regex: bool = False
    #: `>` or `<` for a date comparison, else "".
    op: str = ""

    def matches(self, text: str) -> bool:
        if self.regex:
            return re.search(self.raw, text, re.I) is not None
        return self.raw.lower() in text.lower()

    def matches_date(self, text: str) -> bool:
        if self.op == ">":
            return text > self.raw
        if self.op == "<":
            return text < self.raw
        return text.startswith(self.raw) or self.matches(text)


@dataclass(frozen=True)
class Term:
    """One term. `named` covers both a stored field and a structural relation.

    Which of the two a name is cannot be known while parsing — `under` is a
    traversal on the decision store and nothing at all on a tasks-only project —
    and resolving it here would mean handing `parse` a store, which is the one
    thing that keeps it testable on its own. So the distinction is made against
    a `Lens` at match time, structural first.
    """

    kind: str            # "prose" | "named" | "is"
    name: str            # field or relation name, predicate name, "" for prose
    value: Value | None
    negated: bool = False
    column: int = 0

    def __str__(self) -> str:
        v = ""
        if self.value:
            v = f"/{self.value.raw}/" if self.value.regex else self.value.op + self.value.raw
            if " " in v:
                v = f'"{v}"'
        head = f"is:{self.name}" if self.kind == "is" else \
               (f"{self.name}:{v}" if self.name else v)
        return ("-" if self.negated else "") + head


@dataclass
class Query:
    """An AND of ORs, and deliberately no deeper.

    `or` merges the term that follows it into the group the term before it
    opened, so `a b or c` is `a AND (b OR c)`. There are no parentheses. The
    grammar has to be typeable by somebody in a hurry and by an agent that has
    read one paragraph of help, and both write `a b -c` correctly far more often
    than `(a or b) and not c`. A genuinely complex question is asked twice and
    the answers compared, which is rare enough to pay for.
    """

    groups: list[list[Term]] = field(default_factory=list)

    @property
    def terms(self) -> list[Term]:
        return [t for grp in self.groups for t in grp]

    def __str__(self) -> str:
        return " ".join(" or ".join(str(t) for t in grp) for grp in self.groups)


# ---- parsing -------------------------------------------------------------


def _tokens(source: str) -> list[tuple[str, int]]:
    """Split on whitespace, keeping quoted phrases and regexes whole.

    Hand-written rather than `shlex`, which would strip the `/` delimiters it
    knows nothing about and would treat a lone apostrophe in a title as an
    unterminated quote.

    A `/` only opens a regex where a *value* may start — at the beginning of a
    token or straight after the `:` — so `date:2026/01` is a date and not an
    unterminated pattern. A quote opens a phrase in the same two places, for the
    same reason: a store is full of prose with apostrophes in it.
    """
    out: list[tuple[str, int]] = []
    i, n = 0, len(source)
    while i < n:
        if source[i].isspace():
            i += 1
            continue
        start, buf, closer = i, [], None
        while i < n and (closer is not None or not source[i].isspace()):
            c = source[i]
            if closer is not None:
                if c == closer:
                    closer = None
                else:
                    buf.append(c)
            elif c in '"/' and _at_value(buf):
                closer = c
                if c == "/":
                    buf.append(REGEX_MARK)
            else:
                buf.append(c)
            i += 1
        if closer is not None:
            raise Fault(f"unterminated {closer}", start)
        out.append(("".join(buf), start))
    return out


def _at_value(buf: list[str]) -> bool:
    """Whether a value may begin here: nothing yet, a bare `-`, or after `:`."""
    return not buf or buf == ["-"] or buf[-1] == ":"


def _value(raw: str, field_name: str, column: int) -> Value:
    if raw.startswith(REGEX_MARK):
        body = raw[1:]
        try:
            re.compile(body)
        except re.error as exc:
            raise Fault(f"bad regex: {exc}", column) from None
        return Value(body, regex=True)
    if field_name in DATES and raw[:1] in (">", "<"):
        return Value(raw[1:], op=raw[0])
    return Value(raw)


def parse(source: str) -> Query:
    """A query string as an AND of ORs. Raises `Fault` with a column."""
    toks = _tokens(source)
    if not toks:
        raise Fault("empty query", 0)
    groups: list[list[Term]] = []
    joining = False
    for raw, col in toks:
        if raw.lower() == "or" and not raw.startswith(REGEX_MARK):
            if not groups:
                raise Fault("`or` needs a term before it", col)
            joining = True
            continue
        negated = raw.startswith("-") and len(raw) > 1
        body = raw[1:] if negated else raw
        name, sep, rhs = body.partition(":")
        if sep and not name:
            raise Fault("a term cannot start with ':'", col)
        if sep:
            if name == "is":
                if not rhs:
                    raise Fault("`is:` needs a predicate", col)
                term = Term("is", rhs.lstrip(REGEX_MARK), None, negated, col)
            else:
                if not rhs:
                    raise Fault(f"`{name}:` needs a value", col)
                term = Term("named", name, _value(rhs, name, col), negated, col)
        else:
            if not body:
                raise Fault("empty term", col)
            term = Term("prose", "", _value(body, "", col), negated, col)
        if joining:
            groups[-1].append(term)
            joining = False
        else:
            groups.append([term])
    if joining:
        raise Fault("`or` needs a term after it", toks[-1][1])
    return Query(groups)


# ---- what a store offers -------------------------------------------------


@dataclass
class Lens:
    """One store, as the six things a query needs to ask of it.

    Built by `decision_lens` / `task_lens` below, then handed extra predicates
    and structural terms by a caller that may see more than one store. That
    injection is the whole of how the cross-graph barrier survives this module:
    everything reaching across arrives as a callable, so the rule about what the
    link *means* stays in exactly one place.
    """

    kind: str                                     # "decisions" | "tasks"
    ids: list[str]
    values: Callable[[str, str], list[str]]       # (id, field) -> texts
    fields: tuple[str, ...]
    predicates: dict[str, Callable[[str], bool]]
    structural: dict[str, Callable[[str], set[str]]]
    #: Rendering aid, not part of selection: `(status, title, area)` per id.
    row: Callable[[str], tuple[str, str, str]]

    def prose(self) -> tuple[str, ...]:
        return tuple(f for f in PROSE if f in self.fields)


def _fields_of(*classes) -> tuple[str, ...]:
    """The declared field names, in declaration order, deduplicated.

    Read off the dataclasses rather than listed here, so that adding a field to
    the store adds it to the query vocabulary and renaming one cannot leave a
    term behind that quietly matches nothing.
    """
    seen: dict[str, None] = {}
    for cls in classes:
        for f in dataclasses.fields(cls):
            seen.setdefault(f.name, None)
    return tuple(seen)


def _texts(vals: Iterable[object]) -> list[str]:
    return [v for v in vals if isinstance(v, str) and v]


def decision_lens(g, *, predicates=None, structural=None,
                  hide: Iterable[str] = ()) -> Lens:
    """The decision store as a queryable surface.

    A vertex and its active edge are one record here, which is why `answer:`
    and `falsifier:` work as fields of a decision even though they are stored on
    the edge: the split is how dependency stays the graph structure, not
    something a person searching has to know.
    """
    from dgraph.model import Edge, Vertex

    hidden = set(hide)
    names = tuple(f for f in _fields_of(Vertex, Edge)
                  if f not in hidden and f not in ("src", "to", "active",
                                                   "format", "replaced_by"))

    def values(vid: str, name: str) -> list[str]:
        v = g.vertices[vid]
        out = _texts([getattr(v, name, None)])
        if name == "id":
            out = [vid]
        e = g.active_edge(vid)
        if e is not None:
            out += _texts([getattr(e, name, None)])
        if name in ("summary", "why"):
            out += _texts(getattr(h, name, None) for h in g.history(vid))
        return out

    preds: dict[str, Callable[[str], bool]] = {
        "settled": lambda vid: g.vertices[vid].settled,
        "unsettled": lambda vid: not g.vertices[vid].settled,
        "provisional": lambda vid: g.vertices[vid].base_status == "PROVISIONAL",
        "shaky": lambda vid: g.vertices[vid].base_status in _shaky(),
        "blocked": lambda vid: g.vertices[vid].base_status == "BLOCKED",
        "terminal": lambda vid: (e := g.active_edge(vid)) is not None and e.terminal,
        "superseded": lambda vid: bool(g.history(vid)),
        "orphaned": lambda vid: not g.depends(vid) and not g.children(vid),
    }
    preds.update(predicates or {})

    struct: dict[str, Callable[[str], set[str]]] = {
        "under": lambda arg: g.descendants(arg),
        "above": lambda arg: g.ancestors(arg),
        "waits": lambda arg: {v for v in g.vertices if arg in g.depends(v)},
    }
    struct.update(structural or {})

    return Lens(
        kind="decisions", ids=sorted(g.vertices), values=values, fields=names,
        predicates=preds, structural=struct,
        row=lambda vid: (g.vertices[vid].base_status, g.vertices[vid].title,
                         g.vertices[vid].area),
    )


def _shaky() -> frozenset:
    from dgraph.context import SHAKY
    return SHAKY


def task_lens(tg, *, predicates=None, structural=None,
              hide: Iterable[str] = ()) -> Lens:
    """The task store as a queryable surface.

    `hide` is how the caller removes the cross-graph link fields from the
    generic table. Without it the fields would be reachable by name — this
    module builds the table from the dataclass and so would offer them without
    ever mentioning them — and a generic string match on `because` would be a
    second implementation of `cross.rests_on`.
    """
    from dgraph.tasks import Task

    hidden = set(hide)
    names = tuple(f for f in _fields_of(Task)
                  if f not in hidden and f != "format")

    def values(tid: str, name: str) -> list[str]:
        if name == "id":
            return [tid]
        return _texts([getattr(tg.tasks[tid], name, None)])

    preds: dict[str, Callable[[str], bool]] = {
        "outstanding": lambda tid: tg.tasks[tid].unfinished,
        "resolved": lambda tid: tg.tasks[tid].resolved,
        "blocked": lambda tid: tg.blocked(tid),
        "orphaned": lambda tid: bool(tg.abandoned_origins(tid)),
    }
    preds.update(predicates or {})

    struct: dict[str, Callable[[str], set[str]]] = {
        "waits": lambda arg: {t for t in tg.tasks if arg in tg.prerequisites(t)},
        "after": lambda arg: _reach(tg.unblocks, arg),
        "during": lambda arg: set(tg.prompted(arg)),
    }
    struct.update(structural or {})

    return Lens(
        kind="tasks", ids=sorted(tg.tasks), values=values, fields=names,
        predicates=preds, structural=struct,
        row=lambda tid: (tg.tasks[tid].status, tg.tasks[tid].title,
                         tg.tasks[tid].area),
    )


def _reach(step: Callable[[str], list[str]], start: str) -> set[str]:
    """Everything reachable from `start`, excluding it. Iterative, like the
    model's own walks, so a cycle the validator is about to report does not
    blow the stack first."""
    seen: set[str] = set()
    stack = list(step(start))
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(step(cur))
    return seen


# ---- selecting -----------------------------------------------------------


@dataclass(frozen=True)
class Match:
    """Why a row is in the result: the field, and the text that matched.

    This is what makes the output a search rather than a filtered list. A row
    that says where it was hit can be judged without opening it.
    """

    field: str
    text: str
    term: str


def vet(q: Query, lenses: list[Lens]) -> None:
    """Refuse a query no store can answer, before any of them tries.

    A mistyped field that silently matched nothing would be the worst outcome
    available: an empty result read as "there is nothing", when the truth is
    "you asked wrong". So a name unknown to *every* lens is a fault — while a
    name known to one is not, and simply scopes the query to it.
    """
    for t in q.terms:
        if t.kind == "prose":
            continue
        if any(_knows(l, t) for l in lenses):
            continue
        if t.kind == "is":
            offer = sorted({p for l in lenses for p in l.predicates})
            raise Fault(f"no predicate `is:{t.name}` — try "
                        f"{', '.join(offer)}", t.column)
        offer = sorted({f for l in lenses for f in (*l.fields, *l.structural)})
        raise Fault(f"unknown field `{t.name}` — try {', '.join(offer)}",
                    t.column)


def scope(q: Query, lenses: list[Lens]) -> list[Lens]:
    """The lenses a query is actually about.

    A field only tasks have scopes the query to tasks, so `falsifier:corpus`
    needs no `--decisions`. A query naming nothing store-specific keeps every
    lens it was given.

    **Narrowing is per group, not per term**, because the terms in a group are
    alternatives. `is:unsettled or is:outstanding` asks one question in two
    vocabularies — "what is still live?" — and narrowing term by term would
    take the decision store away on the second half and the task store away on
    the first, leaving nothing and reporting a contradiction that is not there.
    A group keeps every lens that knows *any* of its alternatives.
    """
    keep = list(lenses)
    for grp in q.groups:
        able = [l for l in lenses if any(_knows(l, t) for t in grp)]
        if able and len(able) < len(lenses):
            keep = [l for l in keep if l in able]
    return keep


def _knows(l: Lens, t: Term) -> bool:
    if t.kind == "prose":
        return True
    if t.kind == "is":
        return t.name in l.predicates
    return t.name in l.structural or t.name in l.fields


def _hit(l: Lens, rid: str, t: Term) -> Match | None:
    """Whether this term matches, ignoring negation, and where."""
    if t.kind == "prose":
        for name in l.prose():
            for text in l.values(rid, name):
                if t.value.matches(text):
                    return Match(name, text, str(t))
        return None
    if t.kind == "is":
        pred = l.predicates.get(t.name)
        return Match("", "", str(t)) if pred and pred(rid) else None
    fn = l.structural.get(t.name)
    if fn is not None:
        # Structural first: a relation and a stored field may share a name, and
        # the relation is the one somebody meant — `waits:` is a walk, not a
        # string sitting in a record.
        return Match("", "", str(t)) if rid in fn(t.value.raw) else None
    if t.name not in l.fields:
        return None
    for text in l.values(rid, t.name):
        ok = (t.value.matches_date(text) if t.name in DATES
              else t.value.matches(text))
        if ok:
            return Match(t.name, text, str(t))
    return None


def select(q: Query, l: Lens) -> list[str]:
    """The ids this query selects from one store, in id order.

    Sorted by id, never by relevance. Ids are allocated monotonically, so id
    order is roughly the order the questions were asked — and, unlike a score,
    it does not reshuffle when an unrelated record is added, which is what lets
    a query be re-run after an edit and its output diffed.
    """
    return [rid for rid in l.ids if _keeps(q, l, rid)]


def _keeps(q: Query, l: Lens, rid: str) -> bool:
    for grp in q.groups:
        if not any((_hit(l, rid, t) is not None) != t.negated for t in grp):
            return False
    return True


def explain(q: Query, l: Lens, rid: str) -> list[Match]:
    """Which fields matched, for a row `select` returned.

    Only positive, content-bearing terms produce one: a `-status:DECIDED` says
    nothing about why this row is here, and `under:D04` matched no text and
    should not pretend it did.
    """
    out: list[Match] = []
    for t in q.terms:
        if t.negated:
            continue
        m = _hit(l, rid, t)
        if m is not None and m.field:
            out.append(m)
    return out


def words(l: Lens) -> set[str]:
    """Every distinct word in the store's prose.

    For the empty-result suggestion. Computed on demand and never stored: an
    index would be the only derived structure here that is kept rather than
    recomputed, and a stored derivative that can go stale is what this store
    refuses to keep.
    """
    out: set[str] = set()
    for rid in l.ids:
        for name in l.prose():
            for text in l.values(rid, name):
                out |= {w for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text)}
    return {w.lower() for w in out}
