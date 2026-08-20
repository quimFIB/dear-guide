"""decisions.json -> decision-graph.md.

The markdown is a generated view. Never hand-edit it: run `dg render` (or any
command that applies decisions) and the file is rebuilt from the store.

Stored prose may be org (composed with `dg decide --edit`) or markdown, so every
prose field goes through `orgmd.to_markdown` on the way out. Each section also
carries an explicit anchor, which is what makes an org `[[dg:D04]]` link resolve
here: GitHub's own heading slugs depend on the decision's title, and titles
change.
"""

from __future__ import annotations

from pathlib import Path

from dgraph import orgmd, project
from dgraph.model import Edge, Graph

NONE = "—"

PREAMBLE = """# Decision graph

**Generated from `decisions.json` — do not hand-edit.** Run `dg render`, or any
`dg` command that applies decisions, to rebuild it. The store is the source of
truth; this file is the readable view of it. `dg check` enforces the invariants.

- A **vertex** is a decision the project must make. Vertices are permanent.
- An **edge** is a dependency, which gains a decision payload once that decision
  is made. Edges are permanent and append-only: a reversal marks the old edge
  inactive and it moves to [Superseded edges](#superseded-edges), never
  overwritten. An edge drawn without an answer is a dependency on a question
  that is not settled yet.
- **Status is explicit.** Never infer it from out-degree — a vertex may have
  outgoing edges and still be reopened for re-evaluation.

Statuses: `DECIDED` · `OPEN` · `BLOCKED:<id>` · `REOPENED` · `PROVISIONAL`
"""

SUPERSEDED_INTRO = """Permanent record, rendered from the inactive edges. A
decision that was overturned is kept, not deleted — how a project changed its
mind is usually worth more than the conclusion it landed on."""


#: Shared with the task view; see `orgmd.cell`.
_cell = orgmd.cell


def _resolves_cell(g: Graph, vid: str) -> str:
    e = g.active_edge(vid)
    if e is None or not e.decided:
        return NONE
    return "TERMINAL" if not e.to else ", ".join(e.to)


def _index(g: Graph) -> str:
    rows = [
        "| ID | Decision | Status | Resolves to |",
        "|---|---|---|---|",
    ]
    order = {a: i for i, a in enumerate(g.areas)}
    for v in sorted(g.vertices.values(), key=lambda v: (order.get(v.area, 99), v.id)):
        rows.append(f"| {v.id} | {_cell(v.title)} | {v.status} | {_resolves_cell(g, v.id)} |")
    frontier = ", ".join(g.frontier())
    rows.append("")
    rows.append(
        f"**Frontier** — filter to `OPEN` and `BLOCKED`: {frontier}."
    )
    return "## Index\n\n" + "\n".join(rows) + "\n"


def _section(g: Graph, vid: str) -> str:
    v = g.vertices[vid]
    e = g.active_edge(vid)
    deps = ", ".join(g.depends(vid)) or NONE
    status = f"{v.status} · {e.date}" if e is not None and e.decided and e.date else v.status

    out = [f"{orgmd.anchor(v.id)}", f"### {v.id} — {v.title}"]
    out.append(f"- **Status:** {status}")
    out.append(f"- **Depends on:** {deps}")
    out.append(
        f"- **Falsifier:** "
        f"{orgmd.to_markdown(e.falsifier if e else None, fmt=e.format if e else None) or NONE}"
    )
    out.append("")

    if e is not None and e.decided:
        targets = "TERMINAL" if not e.to else ", ".join(e.to)
        out.append(f"**Resolves to → {targets}**")
        out.append(orgmd.to_markdown(e.answer, fmt=e.format).strip())
        # source: links convert universally; emphasis deliberately never does —
        # a source is a path or a citation, where a `/` pair is data
        out.append(f"*Source:* {orgmd.to_markdown(e.source)}")
    elif v.note:
        out.append(orgmd.to_markdown(v.note, fmt=v.format).strip())

    hist = g.history(vid)
    if hist:
        out.append("")
        # summary carries its record's tag; replaced_by was written by a later
        # op whose dialect this record does not know, so it stays untouched
        out.append("*Superseded here:* " + " · ".join(
            f"\u201c{orgmd.to_markdown(h.summary, fmt=h.format)}\u201d \u2192 "
            f"{orgmd.to_markdown(h.replaced_by) or '*(undecided)*'}"
            for h in hist
        ))
    return "\n".join(out) + "\n"


def _superseded(g: Graph) -> str:
    rows = [
        "| Vertex | Superseded answer | Replaced by | What changed it |",
        "|---|---|---|---|",
    ]
    inactive: list[Edge] = [e for e in g.edges if not e.active]
    for e in sorted(inactive, key=lambda e: (e.src, e.date or "")):
        rows.append(
            f"| {e.src} | {_cell(orgmd.to_markdown(e.summary, fmt=e.format))} "
            f"| {_cell(orgmd.to_markdown(e.replaced_by)) or '*(undecided)*'} "
            f"| {_cell(orgmd.to_markdown(e.why, fmt=e.format))} |"
        )
    return "## Superseded edges\n\n" + SUPERSEDED_INTRO + "\n\n" + "\n".join(rows) + "\n"


def render(g: Graph) -> str:
    parts = [PREAMBLE, "---\n", _index(g), "---\n"]
    for area in g.areas:
        ids = sorted(v.id for v in g.vertices.values() if v.area == area)
        parts.append(f"## {area}\n")
        parts.extend(_section(g, vid) for vid in ids)
        parts.append("---\n")
    parts.append(_superseded(g))
    return "\n".join(parts).rstrip("\n") + "\n"


def write(g: Graph, path: Path | None = None) -> Path:
    target = path or project.find().view
    project.write_atomic(target, render(g))
    return target
