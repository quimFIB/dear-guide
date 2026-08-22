---
description: Find decisions and work by what they say, not by where they sit
argument-hint: <query> [--decisions|--tasks] [--ids] [--full]
allowed-tools: Bash(dg find:*)
---

!`dg find $ARGUMENTS`

Every other reading starts from the frontier or from an id you already have.
This one starts from a word, which is what makes it the command to reach for
before settling something: *was this already decided?*

A bare word searches prose — titles, notes, answers, falsifiers, outcomes.
`field:value` matches one stored field, `is:name` asks a derived question
(`is:decidable`, `is:ready`, `is:unsettled`), and `under:D04` scopes to the part
of the graph that decision opened. Terms are ANDed, `-` negates one, `or`
alternates two.

The aside on each row says *why* it matched, so a row can be judged without
opening it. An empty result means nothing in the store contains that string —
it is a fact, not a threshold, and worth trusting. A query that cannot be
answered says so instead, and the two are different exit codes: a misspelt
field, an id that names no record, a predicate this project has no store for,
and two terms no single store can satisfy all report themselves rather than
coming back empty.

`--ids` gives bare ids for a pipe: `dg find 'is:decidable' --ids` names every
question that could be settled right now, and `dg context <id>` on each gives
the reasoning behind it.

A decision's answers include the ones it used to have. `answer:`, `falsifier:`
and `source:` read its superseded edges alongside its active one, and a hit in
one is labelled `superseded answer` rather than passed off as the current
answer — a reversal's reasoning is often the only place a rejected approach is
written down. `--active` narrows them to what still stands.
