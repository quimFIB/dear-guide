"""tasks.json -> tasks.md.

The markdown is a generated view. Never hand-edit it: run `dg task render` and
the file is rebuilt from the store. Nothing else writes it — not `dg apply`,
not the bootstrap doors — so a view exists only once somebody has asked for one.

Deliberately a separate document from `decision-graph.md`, and nothing about
tasks is ever rendered into that one. A task count in the decision view would
mean *filing a chore makes the decision view stale* — the decision record
churning on work that has nothing to do with it, which is tasks drowning
decisions, implemented in code.

That argument used to rest on `stale_view` being a blocking violation and a
commit-gate denial, which it no longer is (see `check._decisions`). It does not
need to: the point is that the two documents move for their own reasons, and a
generated file that churns is one nobody reads a diff of.
"""

from __future__ import annotations

from pathlib import Path

from dgraph import areas as _areas
from dgraph import orgmd, project
from dgraph.tasks import TaskGraph, done_label, stop_label

NONE = "—"

PREAMBLE = """# Tasks

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
"""


def _index(tg: TaskGraph) -> str:
    rows = [
        "| ID | Task | Status | Waiting on | Because |",
        "|---|---|---|---|---|",
    ]
    order = _areas.order(tg.areas)
    for t in sorted(tg.tasks.values(), key=lambda t: (order(t.area), t.id)):
        waiting = ", ".join(tg.waiting_on(t.id)) or NONE
        rows.append(f"| {t.id} | {orgmd.cell(t.title)} | {t.status} | {waiting} "
                    f"| {', '.join(t.because) or NONE} |")
    ready = ", ".join(t for t in sorted(tg.tasks) if tg.ready(t))
    rows.append("")
    # Qualified, because this file is rendered from one store: `tg.ready` means
    # "nothing outstanding in *this* graph", and a task whose premise is still
    # undecided is not startable however this column reads. `dg task` joins the
    # two stores and is the honest answer; saying so here beats printing a
    # claim the tool itself contradicts.
    rows.append(f"**Ready** — nothing outstanding *in this graph* before them: "
                f"{ready or NONE}.")
    if any(t.because for t in tg.tasks.values()):
        rows.append("")
        rows.append("Work whose **Because** is not settled yet is not startable "
                    "even where it appears above — this view cannot see the "
                    "decision store. `dg task` reports both.")
    return "## Index\n\n" + "\n".join(rows) + "\n"


def _section(tg: TaskGraph, tid: str) -> str:
    t = tg.tasks[tid]
    status = f"{t.status} · {t.done}" if t.status == "DONE" and t.done else t.status

    out = [f"{orgmd.anchor(t.id)}", f"### {t.id} — {t.title}"]
    out.append(f"- **Status:** {status}")
    out.append(f"- **Waiting on:** {', '.join(tg.waiting_on(tid)) or NONE}")
    out.append(f"- **Unblocks:** {', '.join(tg.unblocks(tid)) or NONE}")
    # Only where they hold, unlike the two lines above. Provenance is absent
    # for most work, and an em dash on every section would train the eye past
    # the line on the tasks where it says something.
    if tg.discovered_during(tid):
        out.append(f"- **Discovered during:** "
                   f"{', '.join(tg.discovered_during(tid))}")
    if tg.prompted(tid):
        out.append(f"- **Turned up:** {', '.join(tg.prompted(tid))}")
    # The link the whole cross-graph design exists for. Stored on the task, so
    # printing it needs nothing from the decision store and keeps this view's
    # staleness independent of `decisions.json` — leaving it out only hid it.
    if t.because:
        out.append(f"- **Because:** {', '.join(t.because)}")
    if t.evidence_for:
        out.append(f"- **Evidence for:** {t.evidence_for}")
    out.append("")

    if t.note:
        out.append(orgmd.to_markdown(t.note, fmt=t.format).strip())
        out.append("")
    # Every completion, live one included, drawn exactly as the stops below
    # are — because it is the same kind of record. Work finished, picked back
    # up and finished again has two results, and the earlier one is not
    # superseded prose: it is what the work produced that time. Printing only
    # the live one would put the first result nowhere a reader can reach it.
    if t.completions:
        label = done_label(t.status)
        last = len(t.completions) - 1
        out.append("*Outcome:* " + " · ".join(
            f"{c.date} — {orgmd.to_markdown(c.outcome, fmt=t.format)}"
            + (f" **({label})**" if label and i == last else "")
            for i, c in enumerate(t.completions)))
    # Every stoppage, live one included — the record is what kept stopping this
    # work, and a list that omitted the current entry would read as though the
    # present were not part of the history.
    #
    # Once, not twice. The live reason used to be printed above as well, so a
    # task stopped a single time had its reason in the file twice and nothing
    # said which entry was current. The list carries the label instead, on the
    # last entry and only while the status still claims it — `app.html` already
    # draws it this way, and `tasks.stop_label` is where the word comes from,
    # so the two cannot disagree.
    if t.stops:
        # Only where there is something to separate from. A task with neither
        # a note nor an outcome already ends on the blank line after the field
        # list, and appending a second was the stray double blank.
        if out[-1] != "":
            out.append("")
        label = stop_label(t.status)
        last = len(t.stops) - 1
        out.append("*Stopped:* " + " · ".join(
            f"{k.date} — {orgmd.to_markdown(k.why, fmt=t.format)}"
            + (f" **({label.lower()})**" if label and i == last else "")
            for i, k in enumerate(t.stops)))
    return "\n".join(out).rstrip("\n") + "\n"


def render(tg: TaskGraph) -> str:
    parts = [PREAMBLE, "---\n", _index(tg), "---\n"]
    # `render._index`'s twin — see there for the record that rendered nowhere.
    for area in _areas.sections(tg.areas, tg.tasks.values()):
        ids = sorted(t.id for t in tg.tasks.values() if t.area == area)
        if not ids:
            continue
        parts.append(f"## {area}\n")
        parts.extend(_section(tg, tid) for tid in ids)
        parts.append("---\n")
    return "\n".join(parts).rstrip("\n") + "\n"


def write(tg: TaskGraph, path: Path | None = None) -> Path:
    target = path or project.find().task_view
    project.write_atomic(target, render(tg))
    return target
