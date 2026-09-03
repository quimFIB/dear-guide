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

#: Fields whose values come from a closed vocabulary, and which therefore match
#: **exactly** rather than as substrings. An id, a status and an area are things
#: a record *is*, not prose it contains, and substring matching on them answered
#: questions nobody asked: `id:0` returned every record in a small store, and in
#: a larger one returned a scattered eleven that share nothing but a digit —
#: which is the worse failure, because it can be mistaken for an answer.
#:
#: Exact is the right default here precisely *because* the escape hatch already
#: exists. `id:/^D0/` says "the D0 block" in a way the reader can see, and a
#: pattern still overrides exactness on these fields (see `Value.same`). The
#: prose fields keep substring matching for the opposite reason: exact-matching
#: a sentence is never the question, so `note:` would have required a regex
#: every single time, and a default that is never right is not a default.
EXACT = ("id", "status", "area")

#: Fields compared as dates rather than matched as text, so `date:>2026-01-01`
#: means what it looks like. ISO-8601 sorts lexicographically, which is why the
#: comparison needs no parsing and a bare `date:2026-01` still prefix-matches.
DATES = ("date", "done")

#: Date comparisons, **longest first**, because that ordering is the whole fix
#: for the worst bug this module has had. Matching one character at a time read
#: `date:>=2026-01-01` as `>` with a stray `=` on the front of the operand, and
#: `"2026-01-01" > "=2026-01-01"` is false for every date there will ever be —
#: so the most natural thing to type after learning that `>` works matched
#: nothing, always, and said exit 1: *you asked, and the answer is nothing*.
#:
#: Note what `>=` and `<=` mean against a *partial* date. `date:>=2026-01`
#: includes all of January because every fuller date sorts after the prefix;
#: `date:<=2026-01` excludes it, for the same reason. That is lexicographic
#: comparison being honest rather than a special case worth building — a
#: partial date is a prefix, and `<=` on a prefix is genuinely ambiguous. Use
#: `date:<2026-02` for "before February", which says what it means.
DATE_OPS = (">=", "<=", ">", "<")

#: What a bare field name may not contain. Whitespace ends the token, `:` ends
#: the name, and `"`/`/` open a value — so `date:2026/01` is a date, and
#: `/foo:bar/` is a pattern with a colon in it rather than a field called
#: `foo`.
_NAME_STOP = ':"/'

#: What each store answers on its own. Named here because `cross.lenses` has to
#: say what a *missing* store would have answered, and a lens that was never
#: built cannot list its own vocabulary — without this, `is:unsettled` against
#: an unreadable `decisions.json` reported "no predicate `is:unsettled`", which
#: denies a predicate that exists over a store that merely failed to parse.
#: Checked against the built lenses by a test rather than derived from them,
#: the same bargain `_UNSEARCHABLE` makes.
DECISION_PREDICATES = ("settled", "unsettled", "provisional", "shaky",
                       "blocked", "terminal", "superseded", "orphaned")
TASK_PREDICATES = ("outstanding", "resolved", "blocked", "orphaned", "parked")

#: Caps on what will be parsed at all. Nobody types a two-thousand-character
#: query, and `GET /api/find?q=` otherwise accepts whatever is sent.
#:
#: These bound the *parse*, and should not be mistaken for bounding the work: a
#: pathological pattern fits in eighteen characters. What bounds that is
#: `server.GUARDED_READS`.
MAX_QUERY = 2000
MAX_PATTERN = 200


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

    def same(self, text: str) -> bool:
        """Exact, case-insensitively — unless the reader asked for a pattern.

        A regex overrides exactness rather than being narrowed by it, which is
        the whole bargain: the fields in `EXACT` can afford a strict default
        *because* `/…/` is one keystroke away and says out loud that an
        approximation is wanted.
        """
        if self.regex:
            return re.search(self.raw, text, re.I) is not None
        return self.raw.lower() == text.lower()

    def matches_date(self, text: str) -> bool:
        if self.op == ">":
            return text > self.raw
        if self.op == "<":
            return text < self.raw
        if self.op == ">=":
            return text >= self.raw
        if self.op == "<=":
            return text <= self.raw
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
        if self.kind == "is":
            head = f"is:{self.name}"
        else:
            v = _render(self.value, bare=not self.name)
            head = f"{self.name}:{v}" if self.name else v
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


@dataclass(frozen=True)
class Tok:
    """One whitespace-delimited token, already taken apart.

    The scanner knows where a value begins — that is what `_NAME_STOP` and the
    delimiter rules are for — and this carries that knowledge out rather than
    flattening it back into a string. An earlier version returned the joined
    text and let `parse` re-split it on the first `:`, which made a colon
    inside a quoted phrase indistinguishable from the field separator:
    `"note: nobody has"` silently became a search of the `note` field for
    `" nobody has"`, with no fault, different rows, and no way to tell. A value
    that was quoted or delimited is marked as such here, and `parse` never
    looks inside it again.

    It also retires the control character this module used to smuggle
    regex-ness through the same string. A sentinel that leaks — and it did,
    into ``unknown field `\\x00foo` `` — is a sentinel that was carrying
    structure a field should have carried.
    """

    name: str            # field or predicate name, "" for a bare value
    value: str
    regex: bool = False
    quoted: bool = False
    negated: bool = False
    column: int = 0

    @property
    def is_or(self) -> bool:
        """Whether this token is the alternation operator rather than the word.

        Quoting is how a reader says *literally this*, so `"or"` is the word.
        Testing the token's parts is the only way to know that; testing the
        joined text, as this used to, made `x "or" y` mean `x OR y` and left
        the commonest word in English unsearchable.
        """
        return (not self.name and not self.negated
                and not self.regex and not self.quoted
                and self.value.lower() == "or")


def _tokens(source: str) -> list[Tok]:
    """Split on whitespace, keeping quoted phrases and regexes whole.

    Hand-written rather than `shlex`, which would strip the `/` delimiters it
    knows nothing about and would treat a lone apostrophe in a title as an
    unterminated quote.
    """
    out: list[Tok] = []
    i, n = 0, len(source)
    while i < n:
        if source[i].isspace():
            i += 1
            continue
        tok, i = _token(source, i)
        out.append(tok)
    return out


def _token(source: str, i: int) -> tuple[Tok, int]:
    n, start = len(source), i
    negated = source[i] == "-" and i + 1 < n and not source[i + 1].isspace()
    if negated:
        i += 1
    name, i = _name(source, i, start)
    text, regex, quoted, i = _text(source, i, start)
    return Tok(name, text, regex, quoted, negated, start), i


def _name(source: str, i: int, column: int) -> tuple[str, int]:
    """A leading `field:`, if there is one: bare characters up to a colon.

    A `"` or `/` ends the scan without ending it in a colon, which is what
    makes `/foo:bar/` a pattern rather than a field named `foo` — the colon
    the old parser split on is inside a value that had already begun.
    """
    n, j = len(source), i
    while j < n and not source[j].isspace() and source[j] not in _NAME_STOP:
        j += 1
    if j < n and source[j] == ":":
        if j == i:
            raise Fault("a term cannot start with ':'", column)
        return source[i:j], j + 1
    return "", i


def _text(source: str, i: int, column: int) -> tuple[str, bool, bool, int]:
    """The value at `i`, as `(text, regex, quoted, next_index)`."""
    n = len(source)
    if i < n and source[i] in '"/':
        closer = source[i]
        body, j = _delimited(source, i + 1, closer, column)
        if j < n and not source[j].isspace():
            # `/foo/i` is somebody reaching for a regex flag, and `"a"b` is a
            # typo. Both used to be swallowed and searched for as `fooi` and
            # `ab`. Naming them is the whole habit of this command.
            raise Fault(f"text after the closing {closer}", j)
        return body, closer == "/", closer == '"', j
    j = i
    while j < n and not source[j].isspace():
        j += 1
    return source[i:j], False, False, j


def _delimited(source: str, i: int, closer: str, column: int) -> tuple[str, int]:
    r"""Up to the closing delimiter, with `\<delimiter>` standing for a literal one.

    **Only the delimiter is escapable.** Every other backslash passes through
    with the character after it, unchanged, which is what lets `/\w+/` mean
    what it says: collapsing `\\` to `\` here would hand `re` a dangling
    escape, and `/a\\/` — a pattern matching a backslash — would stop
    compiling. So `\/` is the one sequence this consumes, and it is the one
    that was previously impossible to write.
    """
    n, buf = len(source), []
    while i < n:
        c = source[i]
        if c == "\\" and i + 1 < n:
            nxt = source[i + 1]
            buf.append(nxt if nxt == closer else c + nxt)
            i += 2
            continue
        if c == closer:
            return "".join(buf), i + 1
        buf.append(c)
        i += 1
    raise Fault(f"unterminated {closer}", column)


def _value(tok: Tok, field_name: str) -> Value:
    if tok.regex:
        if len(tok.value) > MAX_PATTERN:
            raise Fault(f"pattern longer than {MAX_PATTERN} characters",
                        tok.column)
        try:
            re.compile(tok.value)
        except re.error as exc:
            raise Fault(f"bad regex: {exc}", tok.column) from None
        if _may_blow_up(tok.value):
            raise Fault(
                "that pattern can backtrack exponentially — one repetition "
                "inside another, like `(\\w+\\s+)+`. Narrow the inner part, "
                "or match it once", tok.column)
        return Value(tok.value, regex=True)
    if not tok.quoted and field_name in DATES:
        for op in DATE_OPS:
            if tok.value.startswith(op):
                rest = tok.value[len(op):]
                if not rest:
                    raise Fault(f"`{field_name}:{op}` needs a date after it",
                                tok.column)
                return Value(rest, op=op)
    return Value(tok.value)


#: Anything at least this large is `re`'s "no upper bound".
_ENDLESS = 4294967295


def _may_blow_up(pattern: str) -> bool:
    r"""Whether a pattern has an unbounded repetition inside another one.

    That shape — `(a+)+`, `(\w+\s+)+`, `(x+x+)+y` — is what makes a regex take
    exponential time in the length of the text. It is not hypothetical:
    `dg find '/^(\w+\s+)+\w+!/'` over six hundred records did not return within
    two minutes. `re` offers no timeout and a running match cannot be
    interrupted — `SIGALRM` only fires on the main thread, and the server is
    threaded — so the only place to stop it is before it starts.

    **Read what this does not do.** It refuses one shape, and there are others.
    Bounded repetition over overlapping alternatives goes straight through and
    is just as bad: `/(.|.|.){11}ZZZZ/` takes 6.6 seconds against the six-record
    demo store, and each `+1` on the count triples it. `MAX_PATTERN` is no help
    — that pattern is eighteen characters. Deciding the general case means
    proving no input drives exponential path exploration, which is ambiguity
    detection on the NFA, and every cheap approximation either refuses patterns
    people legitimately write or leaks a family like this one.

    So this is a guard against an **honest mistake**, not against an attacker.
    What bounds a hostile pattern is that the route carrying one requires the
    token: see `server.GUARDED_READS`. Anyone typing `dg find` at their own
    terminal can still hang it, and can still press ctrl-C.

    It refuses a shape rather than proving slowness, so `(ab+c)+` is caught and
    is harmless — the right direction to be wrong in, since the reader gets a
    sentence telling them what to change. And it reads `re`'s private parse
    tree, so it fails **open** if a future version moves it: a best-effort
    guard that stops working is worth more than one that turns every pattern
    into a crash.
    """
    try:
        import re._parser as parser

        return _nested_repeat(parser.parse(pattern), inside=False)
    except Exception:
        return False


def _nested_repeat(seq, *, inside: bool) -> bool:
    for op, arg in seq:
        name = getattr(op, "name", str(op))
        if name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"):
            _, hi, body = arg
            endless = int(hi) >= _ENDLESS
            if endless and inside:
                return True
            if _nested_repeat(body, inside=inside or endless):
                return True
        elif name == "SUBPATTERN":
            if _nested_repeat(arg[3], inside=inside):
                return True
        elif name == "BRANCH":
            if any(_nested_repeat(b, inside=inside) for b in arg[1]):
                return True
        elif name in ("ASSERT", "ASSERT_NOT"):
            if _nested_repeat(arg[1], inside=inside):
                return True
        elif name == "ATOMIC_GROUP":
            if _nested_repeat(arg, inside=inside):
                return True
    return False


def _term(tok: Tok) -> Term:
    # An empty value is a fault however it was written. `title:""` and `//`
    # both match every record, which is never the question somebody meant to
    # ask and is exactly the shape that reads as an answer.
    if tok.name == "is":
        if not tok.value:
            raise Fault("`is:` needs a predicate", tok.column)
        return Term("is", tok.value, None, tok.negated, tok.column)
    if tok.name:
        if not tok.value:
            raise Fault(f"`{tok.name}:` needs a value", tok.column)
        return Term("named", tok.name, _value(tok, tok.name), tok.negated,
                    tok.column)
    if not tok.value:
        raise Fault("empty term", tok.column)
    return Term("prose", "", _value(tok, ""), tok.negated, tok.column)


def parse(source: str) -> Query:
    """A query string as an AND of ORs. Raises `Fault` with a column."""
    if len(source) > MAX_QUERY:
        raise Fault(f"query longer than {MAX_QUERY} characters", MAX_QUERY)
    toks = _tokens(source)
    if not toks:
        raise Fault("empty query", 0)
    groups: list[list[Term]] = []
    joining = False
    for tok in toks:
        if tok.is_or:
            if not groups:
                raise Fault("`or` needs a term before it", tok.column)
            joining = True
            continue
        term = _term(tok)
        if joining:
            groups[-1].append(term)
            joining = False
        else:
            groups.append([term])
    if joining:
        raise Fault("`or` needs a term after it", toks[-1].column)
    return Query(groups)


# ---- rendering a query back to text --------------------------------------


def _render(v: Value, *, bare: bool) -> str:
    """A value as text that parses back to the same value.

    Textual stability is not the property that matters here; semantic stability
    is. The old rule quoted anything containing a space, regexes included, so
    `title:/a b/` rendered as `title:"/a b/"` and came back a *literal*. That
    string is what `--json` reports as the query it ran and what the browser
    writes back into its search box, so a reader who copied it out got a
    different question than the one that was answered.
    """
    if v.regex:
        return "/" + v.raw.replace("/", "\\/") + "/"
    text = v.op + v.raw
    if _needs_quoting(text, bare=bare):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def _needs_quoting(text: str, *, bare: bool) -> bool:
    if not text or any(c.isspace() for c in text):
        return True
    if text[0] in '"/-':
        return True
    # Only a value with no field in front of it can be re-read as `field:value`
    # or as the alternation operator, so only that one pays for the quotes.
    return bare and (":" in text or text.lower() == "or")


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
    #: Where a structural term's *argument* lives, when it is not this store.
    #: `because:D05` is a term on the task lens whose argument is a decision
    #: id, so resolving it against the task ids would refuse every valid query
    #: that uses it. Absent means "this store's own ids".
    arg_kind: dict[str, str] = field(default_factory=dict)
    #: Predicates this lens would offer if the project had the other store,
    #: as `name -> the store it needs`. Not a capability — the opposite: a
    #: record of what was left out, so `vet` can say *this project has no
    #: decision store* instead of *no such predicate*. See `_no_predicate`.
    withheld: dict[str, str] = field(default_factory=dict)
    #: What to *call* the field a hit landed in, given the text that matched.
    #: Selection never consults it — a term matches or it does not — but a row
    #: that says `answer:` about text the current answer does not contain is
    #: the same conflation the web panel used to make, so the lens that knows
    #: which record a value came from is the one that names it. Identity by
    #: default; only the decision lens has two records per field to tell apart.
    #: Naming is not identity: two lenses over the same store are the same
    #: lens, so this stays out of `==` and `repr` the way `memo` does.
    label: Callable[[str, str, str], str] = field(
        default=lambda rid, name, text: name, compare=False, repr=False)
    #: Memo for structural walks, for the life of this lens. See `_walk`.
    #: Neither this nor `_ids` is part of the surface a store offers, so both
    #: stay out of `==` and `repr`.
    memo: dict = field(default_factory=dict, compare=False, repr=False)
    _ids: set = field(default_factory=set, compare=False, repr=False)

    def prose(self) -> tuple[str, ...]:
        return tuple(f for f in PROSE if f in self.fields)

    def holds(self, rid: str) -> bool:
        """Whether this store has a record by that id.

        Structural terms take an id, and `model.children`/`depends` skip ids
        that name no vertex — right for traversal, because `validate()` is what
        reports a dangling edge, and wrong for search, where it turned
        `under:D0` into an empty result reading as *nothing is under D04*.
        """
        if not self._ids:
            self._ids.update(self.ids)
        return rid in self._ids


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


#: Edge fields whose superseded copies join the search alongside the active
#: one. `summary` and `why` are not here because they exist nowhere else: a
#: superseded edge is the only kind that has them, so they were always read
#: from history. `date` is deliberately left out — `date:>=2026-01-01` asks
#: when this decision was *settled*, and answering it from a record that was
#: overturned would quietly change what every existing date query means.
_ARCHIVED_PROSE = ("answer", "falsifier", "source")


#: Stored fields deliberately outside the query vocabulary. `to` and `active`
#: are the graph structure, `src` and `replaced_by` are the history's own
#: bookkeeping, and `format` is a rendering hint — none of them is something a
#: person searching would think to name.
_UNSEARCHABLE = {"decisions": ("src", "to", "active", "format", "replaced_by"),
                 # `stops` is a list of records, so a field term could never
                 # match one; its prose is reachable under `why:`, which is
                 # also what a person searching would name. Offering it as a
                 # field that always returns nothing is the drift `_excluded`
                 # exists to prevent, arriving by the front door.
                 "tasks": ("format", "stops", "completions")}


def _excluded(kind: str, *classes) -> frozenset:
    """`_UNSEARCHABLE[kind]`, checked against the classes it claims to name.

    This list is an editorial judgement, so unlike the field names themselves
    it cannot be read off the dataclass. What it *can* be is verified. A name
    here that no longer exists is a rename that left the list behind — and the
    field it used to hide is now quietly searchable, which is exactly the drift
    `_fields_of` exists to prevent, reintroduced by the retyped list beside it.
    Failing loudly is the whole point: it is a programming error, not a query.
    """
    have = set(_fields_of(*classes))
    stale = [n for n in _UNSEARCHABLE[kind] if n not in have]
    if stale:
        raise AssertionError(
            f"query.py withholds {kind} fields that no longer exist: "
            f"{', '.join(stale)} — renamed in "
            f"{', '.join(c.__name__ for c in classes)}?")
    return frozenset(_UNSEARCHABLE[kind])


def decision_lens(g, *, predicates=None, structural=None, arg_kind=None,
                  withheld=None, hide: Iterable[str] = (),
                  archived: bool = True) -> Lens:
    """The decision store as a queryable surface.

    A vertex and its edges are one record here, which is why `answer:` and
    `falsifier:` work as fields of a decision even though they are stored on
    the edge: the split is how dependency stays the graph structure, not
    something a person searching has to know.

    `archived` decides whether that includes the edges a reversal replaced.
    With it on, `answer:` means *any answer this decision has ever had* — the
    reversal's own prose becomes findable, and a hit in it is labelled
    `superseded answer` rather than passed off as the current one. With it off
    the search is the active edge alone, which is what `dg find --active` asks
    for when the archive is noise rather than the point.
    """
    from dgraph.model import Edge, Vertex

    hidden = set(hide)
    out_of = _excluded("decisions", Vertex, Edge)
    names = tuple(f for f in _fields_of(Vertex, Edge)
                  if f not in hidden and f not in out_of)

    # `values` is called once per *field* per vertex, and each call asked the
    # graph for the active edge, the rival answers and the history again. A
    # decision has seven prose fields, so answering one search cost roughly
    # twenty full scans of the edge list per vertex — and `rival_answers` is
    # the sharpest of them, a scan with no early exit that returns `[]` in
    # every store this tool can write.
    #
    # The cache lives on this lens, which the caller builds per invocation and
    # drops with it. That is the same lifetime `_walk`'s memo below already
    # relies on, and for the same reason: nothing is written down, and nothing
    # outlives the question being asked.
    # Three caches and not one tuple, because the callers want different
    # subsets and the lookups are not equally cheap. `active_edge` stops at the
    # first match; `rival_answers` is a full scan with no early exit. Fetching
    # all three together made `is:terminal` — which wants only the active edge —
    # about five times *slower* than the uncached version it replaced, and
    # `status:` pay for a history it never reads. Cache per lookup, fetch on
    # demand.
    # One grouping of the edge records for the whole lens, so each of the
    # three lookups below is a dict hit rather than a scan. Built here and
    # dropped with the lens, exactly as the caches are.
    by_src = g.by_src()
    # The reverse index is only wanted by `is:orphaned`, and building it for
    # every lens would tax every other query to pay for one. Built on first
    # ask, then held for the life of the lens like the caches below.
    _rev: list = []

    def _rev_lazy():
        if not _rev:
            _rev.append(g._reverse())
        return _rev[0]

    _active: dict[str, object] = {}
    _rivals: dict[str, list] = {}
    _hist: dict[str, list] = {}
    _MISS = object()          # `active_edge` returns None legitimately

    def active_of(vid: str):
        got = _active.get(vid, _MISS)
        if got is _MISS:
            got = _active[vid] = g.active_edge(vid, by_src)
        return got

    def rivals_of(vid: str) -> list:
        got = _rivals.get(vid)
        if got is None:
            got = _rivals[vid] = g.rival_answers(vid, by_src)
        return got

    def hist_of(vid: str) -> list:
        got = _hist.get(vid)
        if got is None:
            got = _hist[vid] = g.history(vid, by_src)
        return got

    def values(vid: str, name: str) -> list[str]:
        v = g.vertices[vid]
        out = _texts([getattr(v, name, None)])
        if name == "id":
            out = [vid]
        if name == "status":
            # One form: the stored status. It used to offer `BLOCKED` beside
            # `BLOCKED:D05`, because the premise was part of the string; a
            # waiting vertex is `OPEN` now and `is:blocked` asks about the
            # edges (`D68`).
            out = _texts([v.status])
        e = active_of(vid)
        if e is not None:
            out += _texts([getattr(e, name, None)])
        # And every *rival* answer, in the store `one_active_edge` refuses.
        # `active_edge` is first-wins, so without this the second answer is
        # unsearchable — and `dg find` answering "nothing" about a sentence
        # that is sitting in the store is the failure this whole command is
        # shaped to avoid. Empty in every store this tool can write.
        out += _texts(getattr(other, name, None) for other in rivals_of(vid))
        if name in ("summary", "why") or (archived and name in _ARCHIVED_PROSE):
            out += _texts(getattr(h, name, None) for h in hist_of(vid))
        return out

    def label(vid: str, name: str, text: str) -> str:
        """Which of a decision's records this text came from.

        Compared against the active edge by value rather than guessed: if the
        current answer says it, the hit is current, and anything else under a
        field that reads history is a reversal's words. An answer that survived
        a reopen unchanged is reported as current, which it is.
        """
        if name not in _ARCHIVED_PROSE:
            return name
        e = active_of(vid)
        return name if e is not None and getattr(e, name, None) == text \
            else f"superseded {name}"

    preds: dict[str, Callable[[str], bool]] = {
        "settled": lambda vid: g.vertices[vid].settled,
        "unsettled": lambda vid: not g.vertices[vid].settled,
        "provisional": lambda vid: g.vertices[vid].base_status == "PROVISIONAL",
        "shaky": lambda vid: g.vertices[vid].base_status in _shaky(),
        # Derived, as the task store's always was: unsettled, and resting on a
        # premise that is unsettled too. What `waits …` in the listing says.
        "blocked": lambda vid: (not g.vertices[vid].settled
                                and bool(g.waiting_on(vid))),
        "terminal": lambda vid: (e := active_of(vid)) is not None and e.terminal,
        "superseded": lambda vid: bool(hist_of(vid)),
        # Both indexed: this is asked of every vertex in the store, so the
        # scanning form was two full passes over the edge list per vertex --
        # 3.4 s at 5,000 vertices, the last per-vertex scan left in `dg find`.
        "orphaned": lambda vid: (not g.depends(vid, _rev_lazy())
                                 and not g.children(vid, by_src)),
    }
    preds.update(predicates or {})

    struct: dict[str, Callable[[str], set[str]]] = {
        "under": lambda arg: g.descendants(arg),
        "above": lambda arg: g.ancestors(arg),
        # "who rests on `arg`?" read forwards instead of asked of every vertex.
        # `depends` unions *every* active edge, so this must too — `children`
        # takes the first and would miss a rival answer's targets.
        "waits": lambda arg: {x for e in by_src.get(arg, ()) if e.active
                              for x in e.to if x in g.vertices},
    }
    struct.update(structural or {})

    return Lens(
        kind="decisions", ids=sorted(g.vertices), values=values, fields=names,
        predicates=preds, structural=struct,
        row=lambda vid: (g.vertices[vid].base_status, g.vertices[vid].title,
                         g.vertices[vid].area),
        arg_kind=dict(arg_kind or {}), withheld=dict(withheld or {}),
        label=label,
    )


def _shaky() -> frozenset:
    from dgraph.context import SHAKY
    return SHAKY


def task_lens(tg, *, predicates=None, structural=None, arg_kind=None,
              withheld=None, hide: Iterable[str] = ()) -> Lens:
    """The task store as a queryable surface.

    `hide` is how the caller removes the cross-graph link fields from the
    generic table. Without it the fields would be reachable by name — this
    module builds the table from the dataclass and so would offer them without
    ever mentioning them — and a generic string match on `because` would be a
    second implementation of `cross.rests_on`.
    """
    from dgraph.tasks import Task

    hidden = set(hide)
    out_of = _excluded("tasks", Task)
    # `why`, `outcome` and `done` are the names here that are not fields on
    # `Task`. Every reason a task stopped lives in `stops` and every result it
    # produced lives in `completions`, and a searcher asking "why is this not
    # being done" types `why:` — so each term is offered and answered out of
    # the list behind it. Withholding one because the dataclass lost the field
    # would retire a working query to reflect a refactor, which is the
    # vocabulary drifting from what people actually ask.
    names = tuple(f for f in (*_fields_of(Task), "why", "outcome", "done")
                  if f not in hidden and f not in out_of)

    def values(tid: str, name: str) -> list[str]:
        if name == "id":
            return [tid]
        t = tg.tasks[tid]
        # A hit in an entry that is not the live one is labelled as past, the
        # way a decision's superseded answer is: both are things that stopped
        # being current and are worth finding anyway.
        if name == "why":
            return _texts(k.why for k in t.stops)
        if name == "outcome":
            return _texts(c.outcome for c in t.completions)
        # `done` is deliberately not read from the list, for the reason
        # `_ARCHIVED_PROSE` leaves a decision's `date` out: `done:>=` asks when
        # this work was finished, and answering it from a completion the status
        # no longer claims would quietly change what every existing date query
        # means. The derived property is the live reading, and it is None for
        # work that was finished and picked back up — which is the right answer
        # to "is this done", not a gap.
        return _texts([getattr(t, name, None)])

    def label(tid: str, name: str, text: str) -> str:
        """`why:` and `outcome:` read lists; only the last entry can be live."""
        t = tg.tasks[tid]
        if name == "why" and t.stopped_because != text:
            return "stopped earlier because"
        if name == "outcome" and t.outcome != text:
            return "produced earlier"
        return name

    _adj: list = []

    def _adj_lazy():
        if not _adj:
            _adj.append(tg._adjacency())
        return _adj[0]

    preds: dict[str, Callable[[str], bool]] = {
        "outstanding": lambda tid: tg.tasks[tid].unfinished,
        "resolved": lambda tid: tg.tasks[tid].resolved,
        "blocked": lambda tid: tg.blocked(tid, _adj_lazy()),
        "orphaned": lambda tid: bool(tg.abandoned_origins(tid, _adj_lazy())),
        # Not covered by `is:blocked`: a parked task may have every
        # prerequisite resolved and still be put down, which is the point of
        # having a status for it. Blocked is what the graph says; parked is
        # what somebody decided.
        "parked": lambda tid: tg.tasks[tid].parked,
    }
    preds.update(predicates or {})

    struct: dict[str, Callable[[str], set[str]]] = {
        # `prerequisites` and `unblocks` are exact inverses — both union every
        # `precedes` edge of that kind — so the reverse question is the forward
        # lookup, and does not need asking of every task.
        "waits": lambda arg: set(tg.unblocks(arg, _adj_lazy())),
        "after": lambda arg: _reach(tg.unblocks, arg),
        "during": lambda arg: set(tg.prompted(arg)),
    }
    struct.update(structural or {})

    return Lens(
        kind="tasks", ids=sorted(tg.tasks), values=values, fields=names,
        predicates=preds, structural=struct,
        row=lambda tid: (tg.tasks[tid].status, tg.tasks[tid].title,
                         tg.tasks[tid].area),
        arg_kind=dict(arg_kind or {}), withheld=dict(withheld or {}),
        label=label,
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

    A mistyped *argument* is the same failure one level down, which is why
    `_resolve` is called from inside here rather than offered beside it: a
    caller that vetted and forgot to resolve would be back to reporting
    `under:D0` as a fact about D04.
    """
    for t in q.terms:
        if t.kind == "prose":
            continue
        if any(_knows(l, t) for l in lenses):
            continue
        if t.kind == "is":
            raise Fault(_no_predicate(t, lenses), t.column)
        offer = sorted({f for l in lenses for f in (*l.fields, *l.structural)})
        raise Fault(f"unknown field `{t.name}` — try {', '.join(offer)}",
                    t.column)
    _resolve(q, lenses)


def _no_predicate(t: Term, lenses: list[Lens]) -> str:
    """Why `is:<name>` cannot be answered — which is two different sentences.

    A predicate the tool has never heard of is a typo, and the offer list is
    the right help. A predicate that exists but whose store this project lacks
    is *not* a typo, and telling somebody to "try blocked, orphaned,
    outstanding, resolved" when they asked for `is:ready` sends them to fix the
    wrong thing: the question was fine, the project is missing the decision
    store that answers it. `withheld` is how `cross.lenses` says which is which
    — it is the code that decided not to install the predicate, so it is the
    only place that knows why.
    """
    for l in lenses:
        need = l.withheld.get(t.name)
        if need:
            return (f"`is:{t.name}` needs a {need} store, and this project "
                    f"has none")
    offer = sorted({p for l in lenses for p in l.predicates})
    return f"no predicate `is:{t.name}` — try {', '.join(offer)}"


def _resolve(q: Query, lenses: list[Lens]) -> None:
    """Refuse a structural term whose argument names no record.

    `under:` `above:` `waits:` `after:` `during:` `because:` `evidence:` all
    take an id, and `model.children`/`depends` skip ids that name no vertex —
    correct for traversal, because `validate()` is what reports a dangling
    edge, and wrong here, where it turned `under:D0` into an empty result that
    reads as *nothing is under D04*. A dropped digit is the likeliest typo in
    this whole grammar and was the only one with no rescue at all.

    This refuses; it does not resolve. `under:D0` must not quietly become
    `under:D04` — prefix resolution is a separate decision recorded as out of
    scope, and making a search feature the place it arrived unannounced is
    exactly what that note was protecting against.
    """
    by_kind = {l.kind: l for l in lenses}
    for t in q.terms:
        if t.kind != "named" or t.value is None:
            continue
        spaces: list[Lens] = []
        for l in lenses:
            if t.name in l.structural:
                owner = by_kind.get(l.arg_kind.get(t.name, l.kind))
                # No lens for the argument's store means this project cannot
                # check the id — `because:D05` on a tasks-only project. Silence
                # is right there: we do not know that it is wrong.
                if owner is None or owner.holds(t.value.raw):
                    break
                spaces.append(owner)
            elif t.name in l.fields:
                break            # matched as text somewhere; it is not an id
        else:
            if spaces:
                raise Fault(_no_such(t, spaces), t.column)


#: What a store's records are called in a sentence.
_NOUN = {"decisions": "decision", "tasks": "task"}


def _no_such(t: Term, spaces: list[Lens]) -> str:
    """The message for an argument that names nothing, with a near miss.

    The suggestion is a re-run to accept, never a result folded in — the same
    rule the prose *did you mean* follows, and for the same reason: an empty
    result stays a fact only while nothing has been guessed on the reader's
    behalf.
    """
    import difflib

    pool = sorted({rid for l in spaces for rid in l.ids})
    # Sorted back into id order after ranking: a short id is equidistant from
    # most of its neighbours, and `difflib` breaking that tie by its own
    # internal order reads as a guess about which one was meant.
    near = sorted(difflib.get_close_matches(t.value.raw, pool, n=2, cutoff=0.6))
    what = " or ".join(dict.fromkeys(_NOUN.get(l.kind, l.kind) for l in spaces))
    tip = f" — did you mean {', '.join(near)}?" if near else ""
    return f"`{t.name}:` names no {what} `{t.value.raw}`{tip}"


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

    **Narrowing to nothing is a fault, not an empty answer.** Two groups can
    each be answerable and have no store in common — `falsifier:corpus
    because:D05` asks for a record that is both a decision and a task, and
    there is no such thing. That used to leave `keep` empty, which every caller
    read as "nothing matched": the CLI printed the hint line and *nothing else*
    and exited 1, and `--json` returned a bare `query` key with neither store
    in it. It is the exact failure this command is built to refuse, arriving
    through the one path that had no guard on it, so it is raised from here
    where both callers already catch it.
    """
    keep, narrowed_by = list(lenses), None
    for grp in q.groups:
        able = [l for l in lenses if any(_knows(l, t) for t in grp)]
        if not able or len(able) == len(lenses):
            continue
        # Identity, not `in`: `Lens` has a generated `__eq__` that would
        # compare id lists and dicts of closures, and it is two lenses of the
        # same kind away from being wrong as well as slow.
        nxt = [l for l in keep if any(l is x for x in able)]
        if not nxt:
            raise Fault(_no_common_store(grp, able, narrowed_by, keep),
                        grp[0].column)
        keep, narrowed_by = nxt, grp
    return keep


def _no_common_store(grp: list[Term], able: list[Lens],
                     earlier: list[Term] | None, keep: list[Lens]) -> str:
    """Name both halves of the contradiction, not just the one that tripped it.

    A reader who typed two good terms needs to know *which pair* cannot hold
    together; being told that one of them is unanswerable would send them to
    delete the wrong one.
    """
    def phrase(terms):
        return " or ".join(str(t) for t in terms)

    here = f"`{phrase(grp)}` is about {' or '.join(l.kind for l in able)}"
    if earlier is None:                     # unreachable while `keep` starts full
        return f"no store can answer this query — {here}"
    there = f"`{phrase(earlier)}` is about {' or '.join(l.kind for l in keep)}"
    return (f"no store can answer this query — {there}, {here}, and no record "
            f"is both")


def _knows(l: Lens, t: Term) -> bool:
    if t.kind == "prose":
        return True
    if t.kind == "is":
        return t.name in l.predicates
    return t.name in l.structural or t.name in l.fields


def _walk(l: Lens, t: Term, fn: Callable[[str], set[str]]) -> set[str]:
    """The ids a structural term selects, computed once per lens.

    `fn(arg)` returns the same whole set for every candidate row, and this used
    to be called once per row — building the set, testing one membership, and
    throwing it away. On a 600-vertex store that made `above:` take fifteen
    seconds against a walk that costs twenty milliseconds, and the cost grew
    with the cube of the store: the one command whose reason for existing is a
    graph too big to read was the one that could not be run on one.

    The memo lives on the lens, which is built per invocation and dropped with
    it. That is deliberately *not* the index `docs/query-framework.md` argues
    against — nothing is written down, and nothing outlives the question.
    """
    key = (t.name, t.value.raw)
    if key not in l.memo:
        l.memo[key] = fn(t.value.raw)
    return l.memo[key]


def _hit(l: Lens, rid: str, t: Term) -> Match | None:
    """Whether this term matches, ignoring negation, and where."""
    if t.kind == "prose":
        for name in l.prose():
            for text in l.values(rid, name):
                if t.value.matches(text):
                    return Match(l.label(rid, name, text), text, str(t))
        return None
    if t.kind == "is":
        pred = l.predicates.get(t.name)
        return Match("", "", str(t)) if pred and pred(rid) else None
    fn = l.structural.get(t.name)
    if fn is not None:
        # Structural first: a relation and a stored field may share a name, and
        # the relation is the one somebody meant — `waits:` is a walk, not a
        # string sitting in a record.
        if (l.arg_kind.get(t.name, l.kind) == l.kind
                and not l.holds(t.value.raw)):
            # `waits:` exists on both lenses, so a decision id used to be
            # walked over the task store too — ten of the twenty-four seconds
            # that query took, spent proving an empty set empty.
            return None
        return Match("", "", str(t)) if rid in _walk(l, t, fn) else None
    if t.name not in l.fields:
        return None
    for text in l.values(rid, t.name):
        if t.name in DATES:
            ok = t.value.matches_date(text)
        elif t.name in EXACT:
            ok = t.value.same(text)
        else:
            ok = t.value.matches(text)
        if ok:
            return Match(l.label(rid, t.name, text), text, str(t))
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
