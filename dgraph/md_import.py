"""Bootstrap a store from a hand-written markdown decision document.

Reads the dialect this tool renders — `### D01 — Title` sections with
`- **Status:**`, `- **Depends on:**`, `- **Falsifier:**`, a
`**Resolves to → …**` block and a `*Source:*` line, plus a trailing
`## Superseded edges` table.

Such documents store the dependency relation **twice**, in opposite directions
(`Depends on` and `Resolves to`), so the two drift apart. The import collapses
them into the single edge relation this tool uses. Where they disagree, a
declared dependency on a decided parent is honoured by adding the missing edge:
somebody wrote it down deliberately, and the parent's answer does constrain the
child. Every such repair is reported.
"""

from __future__ import annotations

import re
from pathlib import Path

from dgraph.model import Edge, Graph, Vertex

RE_AREA = re.compile(r"^## (.+)$", re.M)
RE_HEADER = re.compile(r"^### (D\d+) — (.+)$", re.M)
RE_STATUS = re.compile(r"^- \*\*Status:\*\* (.+)$", re.M)
RE_DEPENDS = re.compile(r"^- \*\*Depends on:\*\* (.+)$", re.M)
RE_FALSIFIER = re.compile(r"^- \*\*Falsifier:\*\* (.+)$", re.M)
RE_RESOLVES = re.compile(r"^\*\*Resolves to → (.+)\*\*$", re.M)
RE_SOURCE = re.compile(r"^\*Source:\* (.+)$", re.M)
RE_SUP_ROW = re.compile(r"^\| (D\d+) \| (.+?) \| (.+?) \| (.+?) \|$", re.M)

NONE = "—"
SKIP_AREAS = {"Index", "Superseded edges"}


def _ids(raw: str) -> list[str]:
    raw = raw.strip()
    if raw in (NONE, ""):
        return []
    return [x.strip() for x in raw.split(",") if x.strip() and x.strip() != NONE]


def parse(text: str) -> tuple[list[str], list[dict], list[dict]]:
    areas, area_at = [], []
    for m in RE_AREA.finditer(text):
        name = m.group(1).strip()
        if name in SKIP_AREAS:
            continue
        areas.append(name)
        area_at.append((m.start(), name))

    def area_for(pos: int) -> str:
        cur = areas[0] if areas else "General"
        for start, name in area_at:
            if start < pos:
                cur = name
        return cur

    sup_at = text.find("\n## Superseded")
    heads = list(RE_HEADER.finditer(text))
    nodes = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else (
            sup_at if sup_at != -1 else len(text)
        )
        body = text[m.start():end]
        bits = [b.strip() for b in RE_STATUS.search(body).group(1).split("·")]
        res, fal = RE_RESOLVES.search(body), RE_FALSIFIER.search(body)
        src, dep = RE_SOURCE.search(body), RE_DEPENDS.search(body)

        targets = []
        if res and res.group(1).strip() != "TERMINAL":
            targets = _ids(res.group(1))

        if res:
            tail = body[res.end():]
            cut = tail.find("*Source:*")
            prose = tail[:cut if cut != -1 else len(tail)].strip()
        else:
            prose = (body[fal.end():] if fal else body).strip().lstrip("-").strip()

        f = fal.group(1).strip() if fal else None
        nodes.append(dict(
            id=m.group(1), title=m.group(2).strip(), area=area_for(m.start()),
            status=bits[0], date=bits[1] if len(bits) > 1 else None,
            depends=_ids(dep.group(1)) if dep else [],
            targets=targets, answer=prose if res else None,
            note=None if res else (prose or None),
            falsifier=None if f in (None, NONE) else f,
            source=src.group(1).strip() if src else None,
        ))

    sup = []
    if sup_at != -1:
        for row in RE_SUP_ROW.findall(text[sup_at:]):
            sup.append(dict(node=row[0], old=row[1].strip(),
                            new=row[2].strip(), why=row[3].strip()))
    return areas, nodes, sup


def import_markdown(path: Path) -> Graph:
    areas, nodes, sup = parse(path.read_text(encoding="utf-8"))
    g = Graph(areas=areas or ["General"])
    by_id = {n["id"]: n for n in nodes}

    for n in nodes:
        g.vertices[n["id"]] = Vertex(
            id=n["id"], title=n["title"], area=n["area"],
            status=n["status"], note=n["note"],
        )

    targets = {n["id"]: list(n["targets"]) for n in nodes}
    repairs: list[str] = []
    for n in nodes:
        for parent in n["depends"]:
            if parent in targets and n["id"] not in targets[parent]:
                if by_id[parent]["answer"] is not None:
                    targets[parent].append(n["id"])
                    repairs.append(f"{parent} → {n['id']}")
    if repairs:
        print(f"reconciled {len(repairs)} dependency/resolves disagreement(s): "
              + ", ".join(repairs))

    for n in nodes:
        vid = n["id"]
        if n["answer"] is not None:
            g.edges.append(Edge(
                src=vid, to=sorted(targets[vid]), active=True,
                answer=n["answer"], falsifier=n["falsifier"],
                source=n["source"], date=n["date"],
            ))
        else:
            # Undecided: dependants become a payload-less edge, which is what a
            # dependency on an unsettled question is.
            dependants = sorted(q["id"] for q in nodes if vid in q["depends"])
            if dependants:
                g.edges.append(Edge(src=vid, to=dependants, active=True))

    for s in sup:
        g.edges.append(Edge(
            src=s["node"], to=[], active=False, answer=s["old"],
            summary=s["old"], replaced_by=s["new"], why=s["why"],
            date=by_id.get(s["node"], {}).get("date"),
        ))
    return g
