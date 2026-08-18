"""tasks.json -> tasks.md.

The markdown is a generated view. Never hand-edit it: run `dg task render` (or
any command that applies tasks) and the file is rebuilt from the store.

Deliberately a separate document from `decision-graph.md`, and nothing about
tasks is ever rendered into that one. `decision-graph.md` is checked in and
guarded by `stale_view`, which is a blocking violation and a commit-gate denial
— so a task count in the decision view would mean *filing a chore makes the
decision view stale and denies the commit*. That is tasks drowning decisions,
implemented in code.
"""

from __future__ import annotations

from pathlib import Path

from dgraph import orgmd, project
from dgraph.tasks import TaskGraph

NONE = "—"

PREAMBLE = """# Tasks

**Generated from `tasks.json` — do not hand-edit.** Run `dg task render`, or any
`dg` command that applies tasks, to rebuild it. The store is the source of
truth; this file is the readable view of it. `dg check` enforces the invariants.

- A **task** is a unit of work, with an explicit status.
- An **edge** is a prerequisite: the task it points from has to be resolved
  before the tasks it points to can start.
- **Blocked is derived, never stored.** A task is ready when everything before
  it is resolved, so there is no blocked status to keep up to date and none to
  go stale. Abandoning a prerequisite releases what waited on it.

Statuses: `TODO` · `DOING` · `DONE` · `DROPPED`
"""


def _index(tg: TaskGraph) -> str:
    rows = [
        "| ID | Task | Status | Waiting on |",
        "|---|---|---|---|",
    ]
    order = {a: i for i, a in enumerate(tg.areas)}
    for t in sorted(tg.tasks.values(), key=lambda t: (order.get(t.area, 99), t.id)):
        waiting = ", ".join(tg.waiting_on(t.id)) or NONE
        rows.append(f"| {t.id} | {orgmd.cell(t.title)} | {t.status} | {waiting} |")
    ready = ", ".join(t for t in sorted(tg.tasks) if tg.ready(t))
    rows.append("")
    rows.append(f"**Ready** — nothing outstanding before them: {ready or NONE}.")
    return "## Index\n\n" + "\n".join(rows) + "\n"


def _section(tg: TaskGraph, tid: str) -> str:
    t = tg.tasks[tid]
    status = f"{t.status} · {t.done}" if t.status == "DONE" and t.done else t.status

    out = [f"{orgmd.anchor(t.id)}", f"### {t.id} — {t.title}"]
    out.append(f"- **Status:** {status}")
    out.append(f"- **Waiting on:** {', '.join(tg.waiting_on(tid)) or NONE}")
    out.append(f"- **Unblocks:** {', '.join(tg.unblocks(tid)) or NONE}")
    out.append("")

    if t.note:
        out.append(orgmd.to_markdown(t.note, fmt=t.format).strip())
        out.append("")
    if t.outcome:
        out.append(f"*Outcome:* {orgmd.to_markdown(t.outcome, fmt=t.format)}")
    return "\n".join(out).rstrip("\n") + "\n"


def render(tg: TaskGraph) -> str:
    parts = [PREAMBLE, "---\n", _index(tg), "---\n"]
    for area in tg.areas:
        ids = sorted(t.id for t in tg.tasks.values() if t.area == area)
        if not ids:
            continue
        parts.append(f"## {area}\n")
        parts.extend(_section(tg, tid) for tid in ids)
        parts.append("---\n")
    return "\n".join(parts).rstrip("\n") + "\n"


def write(tg: TaskGraph, path: Path | None = None) -> Path:
    target = path or project.find().task_view
    target.write_text(render(tg), encoding="utf-8")
    return target
