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

from dgraph import areas as _areas
from dgraph import orgmd, project
from dgraph.model import Edge, Graph, rival_note

NONE = "—"

PREAMBLE = """# Decision graph

**Generated from `decisions.json` — do not hand-edit.** Run `dg render` to
rebuild it; nothing else writes this file. The store is the source of
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


def _resolves_cell(g: Graph, vid: str, _by=None) -> str:
    e = g.active_edge(vid, _by)
    if e is None or not e.decided:
        return NONE
    return "TERMINAL" if not e.to else ", ".join(e.to)


def _index(g: Graph, _by=None) -> str:
    rows = [
        "| ID | Decision | Status | Resolves to |",
        "|---|---|---|---|",
    ]
    order = _areas.order(g.areas)
    for v in sorted(g.vertices.values(), key=lambda v: (order(v.area), v.id)):
        rows.append(f"| {v.id} | {_cell(v.title)} | {v.status} | {_resolves_cell(g, v.id, _by)} |")
    frontier = ", ".join(g.frontier())
    rows.append("")
    rows.append(
        f"**Frontier** — filter to `OPEN` and `BLOCKED`: {frontier}."
    )
    return "## Index\n\n" + "\n".join(rows) + "\n"


def _section(g: Graph, vid: str, _by=None, _into=None) -> str:
    v = g.vertices[vid]
    e = g.active_edge(vid, _by)
    deps = ", ".join(g.depends(vid, _into)) or NONE
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

    # Said in the file too, and for a sharper reason than in `dg node`:
    # `decision-graph.md` is what a reader opens when they are *not* running
    # commands, so it is the surface least likely to be read beside a
    # `dg check` that would have refused this store.
    rivals = g.rival_answers(vid, _by)
    if rivals:
        out.append("")
        out.append(f"> **{rival_note(len(rivals))}**")
        for other in rivals:
            out.append("")
            out.append(f"*Also current:* "
                       f"{orgmd.to_markdown(other.answer, fmt=other.format)}")

    turned_down = g.rejected(vid)
    if turned_down:
        out.append("")
        out.append("*Offered and not adopted:* " + " · ".join(
            f"{orgmd.to_markdown(h.from_source)} — "
            f"\u201c{orgmd.to_markdown(h.answer, fmt=h.format)}\u201d"
            for h in turned_down))

    hist = g.history(vid, _by)
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
    # History only. A rejected answer has no `replaced_by` and no `why`, so it
    # would fill this table with "*(undecided)*" against a question that is
    # decided — and claim a reversal that never happened. `_section` prints it
    # above, under its own heading.
    inactive: list[Edge] = [e for e in g.edges
                            if not e.active and e.from_source is None]
    for e in sorted(inactive, key=lambda e: (e.src, e.date or "")):
        rows.append(
            f"| {e.src} | {_cell(orgmd.to_markdown(e.summary, fmt=e.format))} "
            f"| {_cell(orgmd.to_markdown(e.replaced_by)) or '*(undecided)*'} "
            f"| {_cell(orgmd.to_markdown(e.why, fmt=e.format))} |"
        )
    return "## Superseded edges\n\n" + SUPERSEDED_INTRO + "\n\n" + "\n".join(rows) + "\n"


def render(g: Graph) -> str:
    # One grouping of the edges and one reverse index for the whole document,
    # rather than a scan of the edge list per vertex per field. Rendering is
    # what made `dg check` quadratic once the checks themselves were fixed:
    # the view is rebuilt to see whether it is stale.
    by, into = g.by_src(), g._reverse()
    parts = [PREAMBLE, "---\n", _index(g, by), "---\n"]
    # The registry, not the declared list. Iterating `g.areas` alone dropped a
    # record whose area the list does not mention out of the document entirely
    # — silently, with nothing above the index to say a section was missing.
    # `validate` used to make that unreachable; areas accumulate now, so it is
    # reachable and this is where it is closed.
    for area in _areas.sections(g.areas, g.vertices.values()):
        ids = sorted(v.id for v in g.vertices.values() if v.area == area)
        parts.append(f"## {area}\n")
        parts.extend(_section(g, vid, by, into) for vid in ids)
        parts.append("---\n")
    parts.append(_superseded(g))
    return "\n".join(parts).rstrip("\n") + "\n"


def write(g: Graph, path: Path | None = None) -> Path:
    target = path or project.find().view
    project.write_atomic(target, render(g))
    return target
