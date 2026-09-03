# Finding things in a graph that has grown

The design behind `dg find`, and the reasoning that is not visible in the code
it produced. **Built** — the engine is `dgraph/query.py`, the command is in
`dgraph/cli.py`, the cross-graph terms are injected by `cross.lenses`, and
`tests/test_query.py` guards it. The build-out record notes what landed and what
was deliberately left out.

Two things here changed during the build rather than before it, and both are
noted where they sit: the barrier rule (§5) is stricter than the one first
drawn, and `scope` narrows per OR-group rather than per term (§2) because the
browser's "frontier only" button needed one question asked in two vocabularies.

## The gap

*Written before `dg find` existed, and left in the present tense it was written
in: the gap is the argument for the shape, and rewriting it as history would
lose the reasoning.* There were five ways to read a graph, and all five were
structural:

| | starts from |
|---|---|
| `dg show` | everything unsettled |
| `dg why` / `dg context` | an id, and walks up |
| `dg tree` | the roots, or an id |
| `dg path A B` | two ids |
| `dg areas` | nothing — it returns counts, not rows |

Every one of them begins either at *the whole frontier* or at *an id you
already have*. Nothing gets you from a **word** to an id. Grep `dgraph/cli.py`
for `--area`, `--status`, `filter` or `search` and the only hit is `dg add
--area`, which *sets* an area. The single filtering surface in the entire tool
was the status chips in the web app.

At demo size — six decisions, six tasks — you do not notice, because `dg show`
fits on a screen and you can read it. The moment a graph outgrows a screen, the
frontier stops being a list you read and becomes a list you scan, and the
questions that have no answer at all start to matter:

- *Which decisions mention the corpus?*
- *What did we already settle about caching, before I settle it again?*
- *Which decided answers would be overturned if the embedding model changed?*
- *What is still open underneath D04?*

The last one is instructive: it is purely structural, the traversal for it
already exists (`Graph.descendants`, `dgraph/model.py`), and there is still
no way to ask it. `dg tree D04` prints the subtree and you find the open ones
with your eyes.

What is *not* missing is worth stating, because it bounds the problem. Asking
what work a decision prompted is already answerable — `cross.rests_on`
(`dgraph/cross.py`), surfaced by `dg task` and `dg context`. The graph's
relations are well served. It is its **contents** that are unreachable.

## The shape of the answer

`dg find` takes one query string and yields a set of ids.

    dg find 'embedding -status:DECIDED'
    dg find 'is:ready area:Retrieval'
    dg find 'falsifier:"the corpus changes"'
    dg find 'under:D04 is:unsettled'

One string rather than a wall of flags, because the same syntax then works in
the web app's search box, in a slash command's `$ARGUMENTS`, and in a script —
and a flag set that has to be mirrored into a text box is two vocabularies
pretending to be one.

### It is a selector, not a sixth reading

This is the constraint everything else follows from. `dg find` selects rows; it
does not invent a way of looking at them. Three rules make that concrete, and
each is a rule this codebase already lives by elsewhere.

**Output goes through `compact.listing`** (`dgraph/compact.py`) — the same
renderer behind `dg show` and `dg task`. A result row looks like a frontier row
because it *is* a frontier row, selected differently.

**Every `field:` name is a field name in the store.** `falsifier:` matches
`Edge.falsifier`. `because:` matches `Task.because`. There is no aliasing layer
and therefore nothing to keep in sync — the query vocabulary cannot drift from
the schema, because it *is* the schema.

**Every `is:` predicate delegates to a method that already exists.** `is:ready`
calls `TaskGraph.ready`; it does not re-derive readiness. This is the rule
`Graph.waiting_on` states in its own docstring (`dgraph/model.py`): one
implementation, because a vertex whose premises differ between two callers is
exactly the disagreement this tool exists to prevent. A query language that
re-implemented `ready` would be a second opinion about the graph, printed with
the authority of the first.

## The grammar

    query := term (WS term)*
    term  := ['-'] atom
    atom  := field ':' value | value
    value := word | '"phrase"' | /regex/

Terms are joined by **and**. A leading `-` negates. A bare `or` between two
terms alternates them. That is the whole grammar.

**A delimiter makes what is inside it literal, including from the parser.**
Everything between `"…"` or `/…/` is a value and nothing else: `"note: see
D04"` is a phrase containing a colon rather than a search of the `note` field,
`/foo:bar/` is a pattern containing one, and `"or"` is the word rather than the
operator. That has to be said because it was once untrue — the scanner
flattened a token back into a string and the parser re-split it on the first
`:`, so quoting, the mechanism for saying *literally this*, was the mechanism
that changed the meaning. Inside `/…/`, `\/` is a literal slash; every other
backslash passes through untouched, because `\w` has to keep meaning `\w`.
Text after a closing delimiter is a fault rather than something to join up:
`/foo/i` is somebody reaching for a regex flag, and searching for `fooi`
instead is not a service.

**A pattern with one unbounded repetition inside another is refused** —
`(\w+\s+)+` and its relatives, which take time exponential in the length of the
text. `re` offers no timeout and a running match cannot be interrupted, so the
only place to stop it is before it starts. This refuses a shape rather than
proving slowness, so it occasionally refuses something harmless; that is the
right direction to be wrong in, because the alternative is a wedged machine and
no message at all.

It is a guard against an **honest mistake**, and it should not be read as more.
Bounded repetition over overlapping alternatives goes straight through and is
just as bad — `/(.|.|.){11}ZZZZ/` takes 6.6 seconds against the six-record demo
store and triples with each `+1` on the count, in eighteen characters. Deciding
the general case is ambiguity detection on the NFA, and every cheap
approximation either refuses patterns people legitimately write or leaks a
family like that one. What bounds a *hostile* pattern is §7: `/api/find` is the
one read route that requires the token, because it is the one whose cost the
caller chooses.

**There are no parentheses**, and this is a decision rather than an omission.
The grammar has to be typeable by someone in a hurry and by an agent that has
read one paragraph of help, and both of those write `a b -c` correctly far more
often than they write `(a or b) and not c`. Nesting is the feature people ask
for and then do not use; the cost of leaving it out is that a genuinely complex
query has to be run twice and the results compared, which is rare enough to
pay for.

### Bare words search prose, and only prose

A bare word is a case-insensitive substring match over the record's prose:
`title`, `note`, `answer`, `falsifier`, `summary`, `why`, `outcome`, `source`.

Deliberately *not* over ids, statuses or areas. If `dg find open` matched every
vertex whose status is `OPEN`, then the single most natural query anyone would
type would return almost the whole store, and the word "open" — which appears
constantly in real answers — would become unsearchable. `status:OPEN` is one
keystroke more and says what it means.

### Fields are the stored fields

Decisions: `id title area status note answer falsifier source date summary why`.

Tasks: `id title area status note done outcome readings why`.

`why`, `outcome` and `done` are the exceptions that prove the rule, and each
arrived the same way: the store folded a scalar into an archival list, and the
term outlived the field. Every reason work stopped lives in `stops` and every
result it produced lives in `completions`, both appended and never cleared —
but a searcher asking *why is this not being done* types `why:`, and one
looking for a measurement types a word from it, so both terms are kept and
answered out of the list behind them. A hit in an entry that is not the live
one is labelled `stopped earlier because` or `produced earlier`, exactly as a
decision's archived answer is labelled `superseded answer`: all three are
things that stopped being current and are worth finding anyway. Retiring a
working query to reflect a refactor would be the vocabulary drifting from what
people ask.

`done` is the one that deliberately does **not** read the list. `done:>=` asks
when this work *is* finished, so it reads the derived live value and answers
`no` for work that was finished and then picked back up — the same reason a
decision's `date` is not read from its superseded edges, where answering from
an overturned record would quietly change what every existing date query means.

The two link fields are **not** in that list. `because` and `evidence_for` are
withheld from the generic field table and re-offered as the structural terms
`because:` and `evidence:` below — a plain string match on them would be a
second implementation of `cross.rests_on`, which is the one thing §5 forbids.

#### `id`, `status` and `area` match exactly; the prose fields match substrings

A record's id, status and area are things it **is**, drawn from a closed
vocabulary. The rest — `title note answer falsifier summary why outcome
source` — are prose it *contains*. The two want opposite defaults, and giving
them one shared default was a mistake with a memorable symptom: `id:0` returned
every record in a ten-record store, because ids are zero-padded and every id
below ten carries a `0`. In a twenty-five-record store it returned a scattered
eleven — `D01`–`D09`, `D10`, `D20` — which is the worse failure, because a
plausible-looking subset can be mistaken for an answer where an obviously
absurd one cannot.

**Exact is affordable here precisely because the escape hatch already exists.**
`id:/^D0/` says "the D0 block" in a way the reader can see and check, and a
pattern overrides exactness rather than being narrowed by it. That is the same
trade §8 makes for fuzzy matching: approximation is available, and it is
written down.

**And it stops at the prose fields for the mirror reason.** Exact-matching a
sentence is never the question anybody has, so `note:` would have required a
regex one hundred percent of the time. A default that is never the right answer
is not a default, it is a toll.

A coupling that *used* to be here, recorded because its removal is the kind
of thing a reader would otherwise reconstruct: a blocked vertex once stored
`BLOCKED:D05`, premise id and all, so `status:` offered both the stored string
and its `BLOCKED` prefix. There is no stored blocked status now — a waiting
vertex is `OPEN`, and *held up* is `is:blocked`, derived from the edges as the
task store's always was. `status:BLOCKED` matches nothing, and says so as an
empty result rather than a fault, because the field is real and the value is
merely absent.

Dates compare — `date:>2026-01-01`, `date:>=2026-01-01`, `date:<=2026-03` —
and otherwise prefix-match, so `date:2026-01` is that month. Both the strict
and the inclusive operator exist because only one of them did once, and
`date:>=2026-01-01` was read as `>` against an operand of `=2026-01-01`: it
matched nothing, for every store, forever, and reported it as exit 1. An
operator with no date after it is a fault for the mirror reason — `date:>`
compared every date against the empty string and matched them all.

Against a *partial* date the two are not symmetric: `date:>=2026-01` includes
all of January because a fuller date sorts after the prefix, and
`date:<=2026-01` excludes it for the same reason. That is lexicographic
comparison being honest rather than a special case worth building. Ask for
"before February" as `date:<2026-02`, which says what it means.

**An unknown field is a fault, and this does real work.** A mistyped field name
that silently matched nothing would be the worst outcome available: an empty
result read as "there is nothing", when the truth is "you asked wrong". So
`dg find falsifer:corpus` exits 2 and says the field does not exist.

But a field known to *one* store is not an error — it narrows to that store.
Tasks have no `falsifier`, so `dg find falsifier:corpus` is implicitly a
decisions query, and nobody has to type `--decisions` to say what the field
already said. The rule is: **a field unknown to every store in scope is a
fault; a field known to some store scopes the query to it.**

#### A negated term is vacuously true where the field does not exist

A consequence of the two rules above, kept deliberately rather than patched,
because it is what the words mean:

    dg find 'is:outstanding or -falsifier:zzz'
    → every decision AND every task

A task has no `falsifier`, so "this task's falsifier does not contain `zzz`" is
true of all of them. That is ordinary vacuous truth, and the alternative —
making a term false under negation because the field is missing — would mean
`-x` and `x` were both false for the same record, which is worse than
surprising: it is not a negation any more.

What makes it *look* wrong is the scoping rule rather than the logic. Alone,
`-falsifier:zzz` narrows to decisions and the vacuity is invisible; it only
surfaces when the term shares an OR-group with a term the other store knows,
because the group then keeps both lenses. So the same term contributes
differently depending on its company — which is exactly what per-group
narrowing is for, seen from an angle where it is unflattering.

Note the asymmetry is only in the negated direction: `is:outstanding or
falsifier:zzz` returns the three outstanding tasks and nothing extra, because
the positive form of a missing field simply never matches.

**What would reopen it:** somebody being misled in practice, not merely
surprised in a test. The fix then is not to change the logic but to say so in
the output — a note that a term was vacuous for one of the stores.

### `is:` — the table, and what guards it

Each predicate names the callable it delegates to.

**This table is checked against the code**, and that sentence replaces one
claiming the same thing while it was not true. Three links in the chain, and
for a while only two existed:

- **code → it works.** `test_every_predicate_delegates_to_something_that_exists`
  walks each lens's own `predicates` dict and asserts every entry returns a
  `bool` for every id. A predicate whose underlying method was renamed away
  fails here.
- **code ↔ the in-code list.** `query.DECISION_PREDICATES` and
  `TASK_PREDICATES` exist so `cross.lenses` can name what a *missing* store
  would have answered, and `test_the_predicate_lists_match_the_lenses` pins
  them to the built lenses by set equality in both directions. That is the
  implementation's real specification for the **base** predicates.
- **code → this table.** Two tests, one per half of a row.
  `test_the_is_table_documents_every_predicate` parses the rows below and
  compares the **term names** against `cross.lenses(g, tg)`, which is the one
  call holding the base and the injected predicates together — the surface
  `dg find` and `GET /api/find` are actually built on, so the table is held to
  what a reader can type. Equality both ways: a row for a predicate that does
  not exist sends somebody to write a query that exits 2, which is the same
  defect pointing the other way. `test_the_tables_delegate_to_things_that_exist`
  then resolves every **symbol** the other columns cite, here and in the
  structural-terms table below.

There were `file:line` citations here once, and they are gone. Seventeen of
them had rotted — `tasks.py:350` naming `TaskGraph.blocked` after it moved to
602 — which is what a reference needing re-verification on every edit does. The
symbol is the durable half, and the half a reader follows; the module beside it
narrows the search without pretending to precision it cannot keep.

The third link was missing for as long as this section claimed to have it, and
four rows went with it. `resolved` and `parked` are base predicates, so the
second link forced them into the tuples — the code stayed consistent and the
consistency stopped at the edge of the repository. `implemented` and
`awaiting-evidence` are injected by `cross.lenses` and are in neither tuple, by
design, so only the first link saw them at all. All four were legal, tested,
and invisible from here.

Worth naming what that was, since the document argues against it everywhere
else: a second copy of a fact, in a place where nothing could tell it had gone
stale. The rule this file states for `field:` — *no aliasing layer, so the
vocabulary cannot drift from the schema* — is the one the `is:` table was
exempt from, and the test is what ends the exemption.

| `is:` | on decisions | on tasks |
|---|---|---|
| `settled` | `Vertex.settled` (`model.py`) | — |
| `unsettled` | `Graph.frontier` (`model.py`) | — |
| `decidable` | unsettled, `waiting_on` empty, no running evidence (`brief.evidence_map`) | — |
| `awaiting-evidence` | unsettled, with a live `evidence_for` task (`cross.pending_evidence`) | — |
| `implemented` | has work resting on it (`cross.rests_on`) | — |
| `provisional` | `base_status` | — |
| `shaky` | `context.SHAKY` (`context.py`) | — |
| `terminal` | `Edge.terminal` (`model.py`) | — |
| `superseded` | `Graph.history` non-empty (`model.py`) | — |
| `blocked` | unsettled ∧ `Graph.waiting_on` non-empty (`model.py`) | `TaskGraph.blocked` (`tasks.py`) |
| `ready` | — | `TaskGraph.ready` ∧ not `cross.gated_by` |
| `outstanding` | — | `Task.unfinished` (`tasks.py`) |
| `resolved` | — | `Task.resolved` — `DONE` or `DROPPED` |
| `parked` | — | `Task.parked` |
| `gated` | — | `cross.gated_by` (`cross.py`) |
| `unharvested` | — | `cross.unharvested` (`cross.py`) |
| `orphaned` | the `no_orphans` finding | `TaskGraph.abandoned_origins` (`tasks.py`) |

`awaiting-evidence` and `decidable` are the same partition of the frontier read
from both sides, which is why they arrived together: an unsettled question is
either waiting on a spike or it is answerable now. `parked` arrived with the
status it names — a store that gained a way to stop work without abandoning it
needs a way to ask which work that is.

`is:blocked` computes differently in the two stores, and that is correct rather
than a wart. It means *held up*, and the two stores are held up by different
things: a decision by a premise that is not settled, a task by an unresolved
prerequisite. One word, one meaning, two derivations — which is the same
arrangement `dg areas` already makes when it prints two tables that share their
areas and not their vocabularies (`dgraph/cli.py`).

`is:decidable` is the one predicate with no single existing method behind it,
because it is the conjunction `dg show` already computes inline when it decides
whether to print "decidable now". Building it means lifting that conjunction
into a named function and having both callers use it — which is a small
improvement to `dg show` that falls out of doing this properly.

### Structural terms, where search meets the traversals

| term | means | delegates to |
|---|---|---|
| `under:D04` | strict descendants | `Graph.descendants` (`model.py`) |
| `above:D04` | strict ancestors | `Graph.ancestors` (`model.py`) |
| `waits:D02` | rests on, directly | `Graph.depends` / `TaskGraph.prerequisites` |
| `because:D04` | work justified by that decision | `cross.rests_on` — *injected* |
| `evidence:D04` | work bearing on that decision | `cross.evidence` — *injected* |
| `after:T02` | work downstream of that task | `TaskGraph.unblocks`, transitively |
| `during:T02` | work that task turned up | `TaskGraph.prompted` (`tasks.py`) |

`because:` and `evidence:` are marked *injected* because they cross the barrier;
see below. The rest read one store and live in `query.py`.

**A vocabulary trap worth the help text.** "What did D04 prompt?" is the natural
English for `because:D04`, but **`prompted` is a term of art here and it does
not mean that**: it is one of the two task→task edge kinds
(`tasks.KINDS = ("precedes", "prompted")`), meaning work discovered while doing
another *task*. Decisions do not prompt work in this vocabulary — work exists
`because` of a decision. So `because:D04` and `during:T02` are different
relations in different stores, and the help for both should say which.

These are what make `find` more than a grep. `under:` in particular turns it
into a subgraph browser:

    dg find 'under:D04 is:unsettled'

*What is still open in the part of the graph D04 opened.* Every piece of that
already exists; the only thing missing was a way to say it.

## Where the engine lives, and what it may not import

One module, `dgraph/query.py`, exporting three things:

    parse(source)                  -> Query          | raises Fault(column, reason)
    select(query, graph, ...)      -> list[str]      # matching ids, sorted
    explain(query, graph, id)      -> list[Match]    # which field matched, and where

`explain` is not a convenience. It is what makes the result a search rather
than a filtered list: a row that says *why* it is there can be judged without
opening it. It is also the reason the CLI can show a snippet from the matching
field instead of repeating the title.

### `query.py` never names the link at all

Three of the predicates above — `is:ready`, `is:gated`, `is:unharvested` — need
both stores. There is an obvious way to get them, and it is worth being precise
about why it loses, because the obvious *statement* of the rule ("`query.py`
must not import `cross`") is simply false.

**Importing `cross` is not the line.** Seven modules already do — `brief`,
`check`, `applying`, `context`, `task_editor`, `cli`, `server` — and nine
import both `model` and `tasks`. `test_only_cross_reasons_about_the_link`
(`test_only_cross_reasons_about_the_link`, `tests/test_cross.py`) says so outright: *"Aggregators (`cli`, `check`,
`brief`) may load both stores — that is what composing a report means. What
they must not do is decide what the link means."*

So the barrier has two halves, and only the second one is about `query.py`:

- The **structural** half — `model` and `tasks` cannot import each other
  (`test_the_two_models_are_mutually_ignorant`, `tests/test_cross.py`). That keeps either model from growing a
  dependency on the other's vocabulary. `query.py` is not a model and this half
  does not constrain it.
- The **semantic** half — every module outside the allowlist
  `{cross, tasks, cli, task_render}` is scanned for the literal `.because` or
  `.evidence_for`, and fails if it names either. That is the half `query.py`
  lands in.

**The tempting line, and why it is wrong.** It looks as though the split should
be between *comparing* the link and *resolving* it — `because:D04` merely asks
whether a field equals the string `D04`, which resolves nothing, while
`is:gated` asks whether the decision that string names is settled.

That line does not survive contact with the code. `cross.rests_on` is the whole
of `because:D04`:

    def rests_on(tg: TaskGraph, did: str) -> list[str]:
        """The tasks that exist because of this decision. The derived reverse."""
        return sorted(t.id for t in tg.tasks.values() if t.because == did)

A pure string comparison, over the task store only, taking no `Graph` at all —
and it lives in `cross.py` regardless. Because the interpretation is not in the
`==`; it is in the claim that `t.because == did` **means** "this work exists
because of that decision". That is the derived reverse of the link, and it is
reasoning whatever its arithmetic looks like. `task_render` is allowed to name
the field precisely because it *prints the id and joins nothing* — it makes no
claim at all.

**So the rule is simpler and stricter than the one I first drew:
`query.py` never names the link.** Every term that touches `because` or
`evidence_for` is injected, and `select()` takes a mapping of extra predicates.

**Where the injection lives, decided while building it.** Not `cli`, which was
the first answer: the browser needs the identical surface, and `server.py` is
*not* on the allowlist — so a second copy would have grown there, further from
the first than `brief`'s was. It lives in `cross.lenses`, the module whose whole
job is "everything needing both stores", and `cli` and `server` both call it.
One implementation, so a query typed into the page and the same query typed at
a shell cannot mean different things.

This also puts the design back in line with its own first principle (§1): every
predicate delegates to a method that already exists. `cross.rests_on` and
`cross.evidence` *are* `because:` and `evidence:`. Re-implementing them in
`query.py` as generic field matches would be a second implementation of the
derived reverse — which is exactly the `brief` failure below, repeated.

**The injected set has two tiers, and they degrade differently.**

*Needs only the task store* — `because:` (`cross.rests_on`) and `evidence:`
(`cross.evidence`). Available whenever there is a task store; a missing
decision store costs them nothing, since neither reads one.

*Needs both stores* — `is:ready`, `is:gated`, `is:unharvested`, and
`is:decidable` (through `brief.evidence_map`, which returns `{}` unless both
stores exist).

The tiers matter because absence hurts them differently. `is:decidable`
degrades **correctly**: with no task store there is no evidence task, so
nothing can be waiting on a spike, and "decidable" means what it should.
`is:ready` degrades **incorrectly**: `gated_by` needs a `Graph`, and the
None-tolerant wrapper `cli._gated_by` answers `None` when there is no decision
store, so a gated task would read as ready. That is the right
answer for `dg task`, which is asking "can I start this?" — and the wrong
answer for a query predicate, which is asserting a property.

**So a predicate whose store is absent is a fault, not a silent weakening.**
`dg find is:ready` on a project with no `decisions.json` exits 2 saying
`is:ready needs a decision store`. This is the unknown-field rule again, for
the same reason: the alternative is a result set that is quietly answering a
different question than the one asked, and an empty or over-full result read as
fact. A predicate that cannot mean what it says should refuse to mean anything.

**The precedent, which is why this is not fussiness.** The same test's docstring
records what happened last time: *"`brief` first shipped with its own copy of
the 'is this premise shaky?' rule, and this test is what found it."* A second
opinion about the link is not a hypothetical failure mode here; it is one that
already occurred once and was caught by machinery. `query.py` is a library that
both `cli` and `server` call, so a link rule living in it would be the copy with
the widest reach.

**Two further reasons, each sufficient on its own.**

*A tasks-only project has no decision store.* `dg task` works with no
`decisions.json` at all. Had `query.py` called `cross.gated_by` directly it
would need a `Graph` that may not exist, and would have to invent a policy for
its absence. With injection there is no policy to invent: `cross.lenses` simply
omits those predicates, and `query.vet` then turns a query naming one into a
fault rather than a quiet half-answer.

*It keeps `query.py` testable against one store.* Its tests need a `Graph` or a
`TaskGraph`, never a project holding both — which is the difference between
testing a selector and standing up a fixture.

**A note on enforcement.** The semantic half is enforced textually — the test
greps module source for `.because`. Under the stricter rule that is enough:
`query.py` genuinely never names either field, so adding it to the scanned set
costs nothing and catches the regression on the day somebody finds it easier to
inline `t.because == did` than to thread a predicate through. Under the *first*
rule it would not have been enough, because `query.py` reaches fields through
`getattr` over a generated table and the literal would never have appeared —
which is a good reason to prefer a rule the existing machinery can actually
check.

## The command

    dg find QUERY [--decisions | --tasks] [--active] [--ids]
                  [--full | --json | --limit N]        # the report
                  [--subgraph [--hops N]]             # or the slice

Both stores by default, with a labelled section each:

    $ dg find embedding

    DECISIONS  2 match of 31
      D04  OPEN     Which embedding model for the index   · title
      D11  DECIDED  Vector dimension                      · falsifier: "…if the embedding model changes…"

    TASKS  1 match of 18
      T05  TODO     Build the recall harness              · note: "…compare embedding variants…"

A section per store rather than one merged list, following `dg areas`, which
prints two tables precisely because `OPEN` and `TODO` are not columns of the
same table. `--decisions` and `--tasks` narrow when you already know.

**The aside carries the match evidence.** This is the one place `find` departs
from `dg show`'s row, and it is the point of the command: `dg show` puts
*waits/unblocks* in the aside because you are triaging, while `find` puts *the
field that matched and a snippet of it* there because you are identifying. When
the hit was in the title, the title is already the evidence, so the aside falls
back to waits/unblocks and nothing is wasted.

Ids are never clipped, per the README's *“Ids are the exception to the clipping”* — a listing you cannot follow up is
not a shorter listing, it is a worse one. Snippets clip through
`compact.clip`.

**The flags.**

`--ids` prints bare ids, one per line, and nothing else. This is the composable
form, and the reason the command is worth building for agents as much as
people:

    dg find 'is:decidable' --ids | xargs -n1 dg context

`--json` emits `{query, scope, decisions, tasks}` with each row carrying
`matched: [{field, snippet}]`, from the same walk as the text — the guarantee
`brief.py` states for itself, that a program parsing JSON and a person reading
the terminal cannot be told different things.

Every store the project has gets a key, always. A store the query was not about
is `null`; one that was, and matched nothing, is `[]`. The distinction is the
same one `GET /api/find` draws, and it exists because omitting the key left a
consumer unable to tell "scoped away" from "no matches" without re-parsing the
query — and left `data["decisions"]` raising `KeyError` on a query that had a
perfectly good answer. A fault under `--json` is reported *as* json,
`{query, fault, column}`: exit 2 already says the query was wrong, and a script
should not have to parse a caret diagram to find out why.

`--limit`, default 20 per section, with a `compact.hint` line saying how many
were withheld. `dgraph/brief.py` makes the general rule: output that agents
pay for in tokens has to stay roughly flat as the store grows. It counts rows,
so it starts at 1 — `--full` is how to ask for all of them, which leaves
nothing for a `0` to mean, and a negative once sliced rows off the *end* while
reporting them as withheld.

`--full` gives the table, clipping nothing, as everywhere else.

`--subgraph` changes what the matches *are*. Without it they are the answer;
with it they are a **seed**, and what comes back is the subgraph they induce —
both stores, as one JSON object:

    dg find --subgraph 'id:D04'              # D04 and everything connected
    dg find --subgraph --hops 1 'id:D04'     # D04 and its neighbours
    dg find --subgraph --hops 0 'area:consent'   # just the matches, and the
                                                 # edges among them

`--hops 0` is the literal induced subgraph, and the other depths are that plus
growth. The default is the whole connected cone. A negative count is refused,
by `cross.induced` itself, so the browser's `/api/find` refuses it in the same
words.

`--subgraph` replaces the report, so the report's flags — `--full`, `--json`,
`--limit` — are **refused** with it rather than ignored, for the reason `--hops`
alone is refused: a flag accepted and ignored is the mistake a run cannot show
you. Two compose: `--ids` prints the slice's ids, one per line, and `--active`
narrows what *seeds* to what still stands. The slice itself always carries
superseded edges, because it is a store and a store keeps its history.

**The growth walks both stores as one digraph** — decision→decision,
task→task, `because` and `evidence_for` — which is the same union
`cross._union_edges` builds for the acyclicity check, and deliberately so: a
slice must not be able to follow an edge the cycle proof does not know about.
The practical consequence is that seeding a decision reaches the work resting
on it. A per-store closure would return no tasks at all, and `--subgraph
'id:D04'` would answer a question nobody asked.

It counts hops in the *union*, so one hop from a decision reaches its premises,
its dependents and the work that rests on it alike. Counting per store would
make a task one hop away in the joined reading and unreachable in the decision
reading, from the same number.

**`--hops` and not `--depth`**, because `depth` already means a vertex's rank
in the DAG everywhere else here — `derived[…].depth`, the layered layout,
`all_depths` — and one word cannot carry both readings in the one place a
reader sees neither definition.

A slice keeps every edge whose source it contains, **even when every target was
trimmed away**: a decision's answer lives on its edge, so dropping the edge
would hand back a D04 with no answer, the slice saying *still open* about
something that is decided. Task edges carry no payload beyond their kind, so
there the empty one goes. Whatever the slice still names but no longer contains
comes back under `boundary` — a neighbourhood admitting to being one, rather
than presenting itself as the whole graph. `boundary` is non-empty for a
bounded `--hops`, and also for a `prompted` edge leaving the cone, since the
union follows dependency and provenance is not one.

`--active` arrived after the rest, with the archive. `answer:`, `falsifier:` and
`source:` read a decision's **superseded** edges as well as its active one, and
label a hit `superseded answer:` where it landed there — because a reversal's
reasoning is often the only place a rejected approach is written down, and a
search that could not reach it would send the reader back to `dg export`.
`--active` narrows them to the answer that currently stands. The default is the
archive for the same reason the store keeps it: the question *was this already
tried?* is answered by the edges nobody is standing on any more.

**Exit codes** are 0 for matches, 1 for none, 2 for a query that cannot be
answered as asked. `dg path` already exits 1 when there is no path
(`cli.path`, `dgraph/cli.py`), so "the question was well formed and the
answer is empty"
already has a code, and a script can say
`dg find X >/dev/null && echo "already settled"`.

Exit 2 covers six things, and they are one thing: a malformed query, an unknown
field, a predicate whose store is absent, a structural term naming an id that
does not exist, a query no single store can answer, and a query that
contradicts its own flags — `--hops` without `--subgraph` among them, since a
flag that is accepted and ignored is the one mistake a run cannot show you. Each is "you did not ask what you think you asked",
and each must be distinguishable from exit 1, which means "you asked, and the
answer is nothing". Collapsing those two is how an empty result becomes a false
fact.

The last two arrived late, by being found missing. `under:D0` — a dropped digit
— walked a subgraph rooted at nothing and returned an empty result that read as
*nothing is under D04*; the traversals tolerate an unknown id on purpose,
because `validate()` is what reports a dangling edge, and search inherited that
tolerance where it could not afford it. And `falsifier:corpus because:D05` asks
for a record that is both a decision and a task: each half is answerable, the
pair is not, and it scoped to no store at all and printed nothing. Both are the
same failure as a mistyped field, one level further in, and both are named here
so that the next reader counts six rather than rediscovering them.

**A mistyped id is offered a near miss**, the way a mistyped word is — as a
suggested re-run, never as rows folded into the answer. §8 argues that case at
length; it applies identically here.

**A flag that contradicts the query is refused.** `dg find --decisions
because:D04` asks for decisions while naming a field only tasks have. The
unknown-field rule would scope it to tasks; the flag says decisions. Rather
than let either win, the pair exits 2. Guessing is available and both guesses
mislead: honouring the flag returns an empty decisions section for a query that
had a perfectly good answer, and honouring the field silently ignores an
argument the user typed on purpose. The failure of the first is invisible,
which settles it — the same asymmetry that decides the ranking and fuzzy-match
questions in §8.

**The staging tray** is not searched into the results. `find` reads the store,
like `dg show` does, and appends a tail line when the tray also matches —
`2 more staged — dg pending`. A staged op is a different kind of thing from a
record, and folding them together would mean a result set where some rows can
be followed up with `dg context` and some cannot. `brief.py` already treats the
tray as part of the situation but keeps it in its own section, for the same
reason.

## The other consumers

The engine is worth more than the command, because there were three other places
that needed exactly this and had nothing. All three have it now.

**The web app** has a search box over `GET /api/find?q=`. The status chips and
`frontier only` became sugar that write `status:X` and the frontier predicate
into that box, so the page has one filter and it is a query string
(`app.html`, `QUERY` and `syncChips`). That also killed a live bug: `filter.status`
had been a single `Set` shared by both the decisions and the tasks tab, so
selecting `DECIDED` and switching to tasks hid everything until you noticed and
untoggled it. One filter cannot have that failure.

**And it is the read route that requires the token**, which moved a line this
project had drawn elsewhere. `server.py` guarded on GET versus POST, on the
stated grounds that "the API only moves data around" — true of every other read
here, each of which returns a fixed payload, and false of this one, whose cost
is chosen by whoever calls it. A page the user merely visits can reach
`127.0.0.1:8765` with a `Host` header that passes, and about twenty characters
of regex buys unbounded CPU there. GET/POST was always standing in for
*effect-and-cost*, so the guard now names that directly and `GUARDED_READS`
lists the reads it catches.

Requiring a custom header is what does the work, more than the token's secrecy:
a `no-cors` request cannot carry one, and a `cors` request carrying one triggers
a preflight this server never answers. The page therefore sends `X-DG-Token` on
every request rather than only the mutating ones, so the next route of this kind
is covered when it is written rather than when somebody notices.

**A `/dg:find` slash command** — `commands/find.md`, one file for both hosts,
wrapping `dg find $ARGUMENTS`. Before it, an agent asked "did we already decide
this?" had `dg show` and hope.

**`skills/dear-guide/SKILL.md`** gained one row in its reading table.

**`dg brief` does not get a search.** It is a fixed payload injected into every
session; a query surface on it would be a query surface nobody typed.

## What this deliberately is not

Two of these will read as oversights unless the reasoning is written down, so
it is, each with the conditions that would reopen it.

### No ranking. Results sort by id.

*Ranking answers a question this tool does not have.* Search engines rank
because the corpus is unbounded and you want the best few. Here the corpus is
one project's decisions, and the question is almost never "the best match" — it
is *all of them*, because the whole reason to run `dg find caching` is to learn
whether this was already settled. A ranking that truncates gives a wrong answer
to that question. A ranking that does not truncate only changes the row order.

*Id order is time order, and it is stable.* Ids are allocated monotonically, so
sorting by id is roughly sorting by when the question was asked, and the store
already sorts by `(area, id)` (`Graph.to_dict`, `dgraph/model.py`). That
order is predictable, which is what lets you run a query, change something, run
it again and read the difference. A relevance order does not have that
property: it reshuffles on edits to the *store* rather than to the matched set,
so adding one unrelated decision changes the term statistics and moves rows
that did not change. `dg find X --ids | xargs` would stop being reproducible.

*It is the one derived value you could not check by hand.* Everything `dg`
derives is verifiable by reading two records — `waiting_on` is "the premises
that are not settled", `ready` is "TODO with nothing outstanding". You can
confirm either by looking. A relevance score depends on the whole corpus and
cannot be confirmed by looking at the row it labels. This is the falsifier
discipline turned on the tool itself: every decision here has to record what
would overturn it, and a score has nothing that would.

**What would reopen it:** a broad query against a large store — "embedding"
matching forty rows — proving genuinely painful in practice. The first answer
is a narrower query, which the grammar makes cheap and which is checkable where
a score is not. If that is not enough, the thing to add is **a sort key you can
name** — `--sort date`, `--sort depth`, `--sort area` — not a score. Ordering
somebody chose is a different thing from ordering somebody computed.

### No fuzzy matching, no stemming, no index.

*Only one of the two error directions is visible.* A fuzzy match that fires
wrongly puts an extra row on screen, which you see and dismiss — cheap, and
self-correcting. A fuzzy match that fails to fire produces a row you never
learn exists, and the cost of *that* here is somebody re-deciding a question
that was already settled. The error worth minimising is the invisible one, and
exact substring matching has a property no approximate matcher can offer:
**an empty result is a fact.** "Nothing in the store contains that string" is
something you can act on. "Nothing matched, at whatever threshold happens to be
configured" is not.

*The corpus is the wrong shape for it.* Stemmers and edit distance are tuned
for natural-language prose. These stores are dense with identifiers, paths,
statuses and short technical tokens, where edit distance 1 is actively harmful:
`D04` and `D05` are one edit apart and mean unrelated things, and so do
`precedes` and `precedent`. A stemmer that folds `decide`/`decided`/`decision`
is helpful; one that folds `serve`/`serving` is not, when `serve` is a command
name. Telling those cases apart needs a stopword list and a token classifier —
configuration, which is one more thing that has to be kept true, about a corpus
that changes every time somebody records a decision.

*The escape hatch is already in the grammar.* `/regex/` expresses everything an
approximate matcher would buy — prefixes, plurals, alternation — exactly and
visibly: `/embed(ding)?s?/`. That is fuzziness the reader wrote down and can
therefore check, which is the trade every other part of this design makes.

*No index follows from all of this.* Substring and regex over a few hundred
in-memory records is a linear scan measured in microseconds; there is no
performance argument for an index at any size this tool will see. There is an
argument against one: it would be the only derived structure in the project
that is **stored** rather than recomputed, against the rule this project holds
to elsewhere — a stored field that can go stale is what it refuses to keep.

*That claim is about **matching**, and it was once quietly read as covering
everything.* A structural term is not a scan of the records; it is a walk of
the graph, and the walk returns the same whole set for every candidate row. It
used to be run once per row, which made `above:` cost fifteen seconds on six
hundred vertices where the walk itself costs twenty milliseconds — a linear
scan of prose, timed alongside it, took seven. The fix is a memo living on the
lens for the length of one query (`query._walk`), and it is deliberately not an
index: nothing is written down and nothing outlives the question. The two costs
are different in kind, and only the first one is what this section is arguing
about.

**What would reopen it:** nothing about matching quality; only a store large
enough that a linear scan is measurably slow, which is a different problem with
a different answer. A structural term that is slow again is not that condition
— it is a memo that stopped working.

*The honest cost is typos, and it gets a cheap fix instead.* `dg find embeddig`
returning nothing, with no clue why, is the real price of exactness. The
mitigation that keeps the empty result a fact: when a query matches nothing
**and** its terms are bare words, say so, and offer a *did you mean* computed
against the distinct words actually present in the store — as a suggested
re-run the reader can accept or ignore, never as results folded in silently.
That is Q14 in the build-out record, and it is the concession that makes exactness
liveable.

### Also out of scope

**Parenthesised boolean queries** and saved query aliases, both discussed above.

**Prefix id resolution** — `dg node D0` finding `D04`. Adjacent, worth doing,
and separate: there is no shared `resolve_id()` today — `cli.py` carries ten
inline membership tests and sixteen distinct "unknown vertex/task" messages
between them. That is its own cleanup, and it should not ride along inside a
search feature, where it would arrive unannounced and change how every other
command resolves an id.

## What building it has to satisfy

Not a checklist of good intentions — these were existing tests that would fail,
and they are named rather than cited by line so that they stay findable:

- A `LAYOUT` entry under `READ` with a matching `rich_help_panel`
  (`test_every_command_is_in_a_help_panel`,
  `test_each_command_declares_the_panel_the_layout_puts_it_in`), and a panel
  role that agrees across both help screens
  (`test_the_two_help_screens_agree_on_what_a_heading_means`,
  `test_every_panel_on_both_screens_has_a_role`) — all in `tests/test_cli.py`.
- The declared panel order and within-panel order must render
  (`test_the_help_renders_the_panels_in_the_declared_order`,
  `test_the_order_inside_a_panel_is_the_order_you_meet_them`).
- `tests/test_query.py` in the `tests/test_context.py` idiom: the pure
  `parse`/`select` walk tested directly, the CLI rendering tested separately,
  and an assertion that the text and `--json` forms come from the same walk.
- A test asserting every predicate delegates to something that exists — which
  landed as `test_every_predicate_delegates_to_something_that_exists`, over the
  lens's own dict rather than over the `is:` table above. That left the table
  itself unguarded, and it is the one claim in this document that did silently
  stop being true; `test_the_is_table_documents_every_predicate` closes it, and
  *§`is:` — the table, and what guards it* has the account.
- The plugin tests, which `commands/find.md` now sits under: frontmatter keys,
  `allowed-tools` covering every `!` block, and no subcommand named that does
  not exist (`test_command_mentions_only_subcommands_that_exist`,
  `tests/test_plugin.py`).

## A note on this repository

`dg` does not keep its own decisions in a `dg` graph — `git ls-files` finds
only `demo/decisions.json`, which is a fictional vector-search project. So this
design has nowhere to be recorded as decisions, and is a document instead. That
is worth noticing: the first thing a query surface would make pleasant is
navigating a graph large enough that its owner had stopped keeping one by hand.
