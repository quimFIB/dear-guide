# Tasks

**Generated from `tasks.json` — do not hand-edit.** Run `dg task render` to
rebuild it; nothing else writes this file. The store is the source of
truth; this file is the readable view of it. `dg check` enforces the invariants.

- A **task** is a unit of work, with an explicit status.
- An **edge** relates two tasks, and says which of two things it means. A
  `precedes` edge is a prerequisite: the task it points from has to be resolved
  before the tasks it points to can start. A `prompted` edge is provenance:
  doing the task it points from turned the others up. Provenance makes nothing
  wait — a chore noticed mid-task is usually startable at once, and often has
  to land before the task that revealed it can be finished.
- **Blocked is derived, never stored.** A task is ready when everything before
  it is resolved, so there is no blocked status to keep up to date and none to
  go stale. Abandoning a prerequisite releases what waited on it — and says so:
  a release is a guess, since work that *produced* what another task consumes
  does not release it but undermines it, so `dg check` asks about anything left
  standing by a drop until somebody acts on it.
- A task may name the **decisions** it exists because of — it can rest on
  several — and the one its outcome will inform. Those live in `decisions.json`; this view names the id
  and nothing more, because it is generated from `tasks.json` alone.

Statuses: `TODO` · `DOING` · `PARKED` · `DONE` · `DROPPED`

`PARKED` is work put down without being given up on; `DROPPED` is work nobody
is going to do. Both record why and when, in the same place, and neither record
is ever cleared. What separates them is downstream: a drop releases everything
that waited on the work, because abandoning it *is* the judgement that it was
not needed, while a park holds them — so a park that is holding work up is
reported until somebody picks it up, drops it, or removes the dependency.

---

## Index

| ID | Task | Status | Waiting on | Because |
|---|---|---|---|---|
| T01 | Wire the SPRT harness to the volunteer cluster | TODO | — | — |
| T02 | Measure the binary with the weights inlined | DONE | — | — |
| T03 | Draft the release note | TODO | — | D03 |

**Ready** — nothing outstanding *in this graph* before them: T01, T03.

Work whose **Because** is not settled yet is not startable even where it appears above — this view cannot see the decision store. `dg task` reports both.

---

## Tooling

<a id="t01"></a>
### T01 — Wire the SPRT harness to the volunteer cluster
- **Status:** TODO
- **Waiting on:** —
- **Unblocks:** —
- **Evidence for:** D02

Whatever D02's answer turns out to be, it has to rest on this. Nobody has looked at what it involves.

---

## Release

<a id="t02"></a>
### T02 — Measure the binary with the weights inlined
- **Status:** DONE · 2026-03-05
- **Waiting on:** —
- **Unblocks:** —
- **Evidence for:** D03

- **The result:** bench/size.md -- 412 KB at -Os, of which the weights are 71 KB (2026-03-05)

<a id="t03"></a>
### T03 — Draft the release note
- **Status:** TODO
- **Waiting on:** —
- **Unblocks:** —
- **Because:** D03

Cannot start: there is nothing to say until D03 is answered.

---
