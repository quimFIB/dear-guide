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

There are five ways to read a graph today, and all five are structural:

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
is the status chips in the web app (`dgraph/static/app.html:150`, predicate at
`:362`).

At demo size — six decisions, six tasks — you do not notice, because `dg show`
fits on a screen and you can read it. The moment a graph outgrows a screen, the
frontier stops being a list you read and becomes a list you scan, and the
questions that have no answer at all start to matter:

- *Which decisions mention the corpus?*
- *What did we already settle about caching, before I settle it again?*
- *Which decided answers would be overturned if the embedding model changed?*
- *What is still open underneath D04?*

The last one is instructive: it is purely structural, the traversal for it
already exists (`Graph.descendants`, `dgraph/model.py:256`), and there is still
no way to ask it. `dg tree D04` prints the subtree and you find the open ones
with your eyes.

What is *not* missing is worth stating, because it bounds the problem. Asking
what work a decision prompted is already answerable — `cross.rests_on`
(`dgraph/cross.py:27`), surfaced by `dg task` and `dg context`. The graph's
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

**Output goes through `compact.listing`** (`dgraph/compact.py:160`) — the same
renderer behind `dg show` and `dg task`. A result row looks like a frontier row
because it *is* a frontier row, selected differently.

**Every `field:` name is a field name in the store.** `falsifier:` matches
`Edge.falsifier`. `because:` matches `Task.because`. There is no aliasing layer
and therefore nothing to keep in sync — the query vocabulary cannot drift from
the schema, because it *is* the schema.

**Every `is:` predicate delegates to a method that already exists.** `is:ready`
calls `TaskGraph.ready`; it does not re-derive readiness. This is the rule
`Graph.waiting_on` states in its own docstring (`dgraph/model.py:246`): one
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

Tasks: `id title area status note outcome why because evidence_for done`.

Dates compare — `date:>2026-01-01`, `date:<2026-03` — and otherwise
prefix-match, so `date:2026-01` is that month.

**An unknown field is a fault, and this does real work.** A mistyped field name
that silently matched nothing would be the worst outcome available: an empty
result read as "there is nothing", when the truth is "you asked wrong". So
`dg find falsifer:corpus` exits 2 and says the field does not exist.

But a field known to *one* store is not an error — it narrows to that store.
Tasks have no `falsifier`, so `dg find falsifier:corpus` is implicitly a
decisions query, and nobody has to type `--decisions` to say what the field
already said. The rule is: **a field unknown to every store in scope is a
fault; a field known to some store scopes the query to it.**

### `is:` — the table is the specification

Each predicate names the callable it delegates to. This table is not
documentation of the implementation; it is the implementation's only
specification, and a test asserts every name on it resolves to a callable that
exists, so it cannot quietly stop being true.

| `is:` | on decisions | on tasks |
|---|---|---|
| `settled` | `Vertex.settled` (`model.py:86`) | — |
| `unsettled` | `Graph.frontier` (`model.py:309`) | — |
| `decidable` | unsettled, `waiting_on` empty, no running evidence (`brief.evidence_map`) | — |
| `provisional` | `base_status` | — |
| `shaky` | `context.SHAKY` (`context.py:102`) | — |
| `terminal` | `Edge.terminal` (`model.py:115`) | — |
| `superseded` | `Graph.history` non-empty (`model.py:214`) | — |
| `blocked` | `base_status == BLOCKED` | `TaskGraph.blocked` (`tasks.py:350`) |
| `ready` | — | `TaskGraph.ready` ∧ not `cross.gated_by` |
| `outstanding` | — | `Task.unfinished` (`tasks.py:125`) |
| `gated` | — | `cross.gated_by` (`cross.py:42`) |
| `unharvested` | — | `cross.unharvested` (`cross.py:128`) |
| `orphaned` | the `no_orphans` finding | `TaskGraph.abandoned_origins` (`tasks.py:331`) |

`is:blocked` computes differently in the two stores, and that is correct rather
than a wart. It means *held up*, and the two stores are held up by different
things: a decision by a premise it names in its status, a task by an unresolved
prerequisite. One word, one meaning, two derivations — which is the same
arrangement `dg areas` already makes when it prints two tables that share their
areas and not their vocabularies (`dgraph/cli.py:601`).

`is:decidable` is the one predicate with no single existing method behind it,
because it is the conjunction `dg show` already computes inline when it decides
whether to print "decidable now". Building it means lifting that conjunction
into a named function and having both callers use it — which is a small
improvement to `dg show` that falls out of doing this properly.

### Structural terms, where search meets the traversals

| term | means | delegates to |
|---|---|---|
| `under:D04` | strict descendants | `Graph.descendants` (`model.py:256`) |
| `above:D04` | strict ancestors | `Graph.ancestors` (`model.py:267`) |
| `waits:D02` | rests on, directly | `Graph.depends` / `TaskGraph.prerequisites` |
| `because:D04` | work justified by that decision | `cross.rests_on` — *injected* |
| `evidence:D04` | work bearing on that decision | `cross.evidence` — *injected* |
| `after:T02` | work downstream of that task | `TaskGraph.unblocks`, transitively |
| `during:T02` | work that task turned up | `TaskGraph.prompted` (`tasks.py:294`) |

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
(`tests/test_cross.py:383`) says so outright: *"Aggregators (`cli`, `check`,
`brief`) may load both stores — that is what composing a report means. What
they must not do is decide what the link means."*

So the barrier has two halves, and only the second one is about `query.py`:

- The **structural** half — `model` and `tasks` cannot import each other
  (`tests/test_cross.py:374`). That keeps either model from growing a
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

    dg find QUERY [--decisions | --tasks] [--full] [--json] [--ids] [--limit N]

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

Ids are never clipped, per `README.md:235` — a listing you cannot follow up is
not a shorter listing, it is a worse one. Snippets clip through
`compact.clip`.

**The flags.**

`--ids` prints bare ids, one per line, and nothing else. This is the composable
form, and the reason the command is worth building for agents as much as
people:

    dg find 'is:decidable' --ids | xargs -n1 dg context

`--json` emits `{query, decisions: [...], tasks: [...]}` with each row carrying
`matched: [{field, snippet}]`, from the same walk as the text — the guarantee
`brief.py` states for itself, that a program parsing JSON and a person reading
the terminal cannot be told different things.

`--limit`, default 20 per section, with a `compact.hint` line saying how many
were withheld. `dgraph/brief.py:88` makes the general rule: output that agents
pay for in tokens has to stay roughly flat as the store grows.

`--full` gives the table, clipping nothing, as everywhere else.

**Exit codes** are 0 for matches, 1 for none, 2 for a query that cannot be
answered as asked. `dg path` already exits 1 when there is no path
(`cli.py:535`), so "the question was well formed and the answer is empty"
already has a code, and a script can say
`dg find X >/dev/null && echo "already settled"`.

Exit 2 covers four things, and they are one thing: a malformed query, an
unknown field, a predicate whose store is absent, and a query that contradicts
its own flags. Each is "you did not ask what you think you asked", and each
must be distinguishable from exit 1, which means "you asked, and the answer is
nothing". Collapsing those two is how an empty result becomes a false fact.

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

The engine is worth more than the command, because there are three other places
that need exactly this and currently have nothing.

**The web app** gets a search box over `GET /api/find?q=`. The existing status
chips become sugar that writes `status:X` into that box, so the page has one
predicate instead of two. This also fixes a live bug: `filter.status` is a
single `Set` shared by both the decisions and the tasks tab
(`app.html:150`, used at `:363` and `:369`), so selecting `DECIDED` and
switching to tasks currently hides everything until you notice and untoggle it.

**A `/dg:find` slash command**, one file in `commands/` for both hosts, wrapping
`dg find $ARGUMENTS`. An agent asked "did we already decide this?" currently has
`dg show` and hope.

**`skills/dear-guide/SKILL.md`** gains one row in its reading table.

**`dg brief` does not get a search.** It is a fixed payload injected into every
session; a query surface on it would be a query surface nobody typed.

## What this deliberately is not

Two of these will read as oversights unless the reasoning is written down, so
it is, in the "what would reopen it" form `TODO.md` uses.

### No ranking. Results sort by id.

*Ranking answers a question this tool does not have.* Search engines rank
because the corpus is unbounded and you want the best few. Here the corpus is
one project's decisions, and the question is almost never "the best match" — it
is *all of them*, because the whole reason to run `dg find caching` is to learn
whether this was already settled. A ranking that truncates gives a wrong answer
to that question. A ranking that does not truncate only changes the row order.

*Id order is time order, and it is stable.* Ids are allocated monotonically, so
sorting by id is roughly sorting by when the question was asked, and the store
already sorts by `(area, id)` (`Graph.to_dict`, `dgraph/model.py:171`). That
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
that is **stored** rather than recomputed, and `TODO.md:78-80` already states the
rule — a stored field that can go stale is what this store refuses to keep.

**What would reopen it:** nothing about matching quality; only a store large
enough that a linear scan is measurably slow, which is a different problem with
a different answer.

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

Not a checklist of good intentions — these are existing tests that will fail.

- A `LAYOUT` entry under `READ` with a matching `rich_help_panel`
  (`tests/test_cli.py:1611`, `:1632`), and a panel role that agrees across both
  help screens (`:1644`, `:1685`).
- The declared panel order and within-panel order must render
  (`tests/test_cli.py:1701`, `:1783`).
- `tests/test_query.py` in the `tests/test_context.py` idiom: the pure
  `parse`/`select` walk tested directly, the CLI rendering tested separately,
  and an assertion that the text and `--json` forms come from the same walk.
- A test over the `is:` table asserting every name resolves to a callable that
  exists — the one claim in this document that could silently stop being true.
- If the slash command ships, the plugin tests apply: frontmatter keys,
  `allowed-tools` covering every `!` block, and no subcommand named that does
  not exist (`tests/test_plugin.py:150`–`:200`).

## A note on this repository

`dg` does not keep its own decisions in a `dg` graph — `git ls-files` finds
only `demo/decisions.json`, which is a fictional vector-search project. So this
design has nowhere to be recorded as decisions, and is a document instead. That
is worth noticing: the first thing a query surface would make pleasant is
navigating a graph large enough that its owner had stopped keeping one by hand.
