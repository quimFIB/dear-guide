#!/usr/bin/env python3
"""Turn `results/raw.json` into the tables the README quotes.

The one number worth deriving is the **scaling exponent**: fit
`log(time) = a + α·log(size)` over the sizes an op was actually measured at,
and α says what shape the op has. α≈1 is linear in the store, α≈2 is one of
the linear scans nested inside a walk. It is fitted over the largest three
points, because the small sizes are dominated by fixed costs and drag the fit
toward 1 for everything.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent


def slope(points: list[tuple[float, float]]) -> float | None:
    """Least-squares α over log-log points, or None if there are too few."""
    pts = [(math.log(x), math.log(y)) for x, y in points if x > 0 and y > 0]
    if len(pts) < 2:
        return None
    n = len(pts)
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    den = sum((x - mx) ** 2 for x, _ in pts)
    return None if den == 0 else sum((x - mx) * (y - my) for x, y in pts) / den


def table(rows: list[dict], layer: str, sizes: list[int]) -> str:
    """One row per op, one column per size, plus two fitted exponents.

    `α` is fitted over the largest three sizes and `α₂` over the largest two.
    Both are shown because they disagree where it matters: a sparse size grid
    (the CLI is measured at four sizes, not six) lets a cheap small point drag
    a three-point fit down, and `α₂` is then the one to read.
    """
    ops: dict[str, dict[int, dict]] = {}
    for r in rows:
        if r["layer"] != layer:
            continue
        # Density ops are named `density.d8.validate`: the degree is the
        # column, so it must come out of the row label or every op gets one
        # row per degree.
        name = r["op"].split(".", 2)[2] if layer == "density" else r["op"]
        ops.setdefault(name, {})[r["size"]] = r
    head = "| op | " + " | ".join(f"{s:,}" for s in sizes) + " | α | α₂ | note |"
    sep = "|---|" + "---|" * (len(sizes) + 3)
    out = [head, sep]
    for op, by_size in ops.items():
        cells, pts, note = [], [], ""
        for s in sizes:
            r = by_size.get(s)
            if r is None:
                cells.append("–")
            elif r["skipped"]:
                cells.append(f"*skip ~{r['projected_s']:.0f}s*")
            else:
                ms = r["best_s"] * 1e3
                cells.append(f"{ms:,.3f}" if ms < 10 else f"{ms:,.1f}")
                pts.append((float(s), ms))
                note = r.get("note", "") or note
        a, a2 = slope(pts[-3:]), slope(pts[-2:])
        out.append(f"| `{op}` | " + " | ".join(cells) + " | "
                   + (f"{a:.2f}" if a is not None else "–") + " | "
                   + (f"{a2:.2f}" if a2 is not None else "–") + f" | {note} |")
    return "\n".join(out)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--raw", type=Path, nargs="+",
                   default=[HERE / "results" / "raw.json"],
                   help="One or more raw.json files; rows are merged.")
    p.add_argument("--out", type=Path, default=HERE / "results" / "tables.md")
    a = p.parse_args()
    docs = [json.loads(f.read_text(encoding="utf-8")) for f in a.raw]
    d = docs[0]
    rows = [r for doc in docs for r in doc["rows"]]
    for doc in docs[1:]:
        d["stores"].update(doc["stores"])
    d["elapsed_s"] = sum(doc["elapsed_s"] for doc in docs)
    sizes = sorted({r["size"] for r in rows if r["layer"] == "library"})
    cli_sizes = sorted({r["size"] for r in rows if r["layer"] == "cli"})
    dens = sorted({r["size"] for r in rows if r["layer"] == "density"})

    parts = [
        "# Measured tables\n",
        f"Generated {d['generated']} · Python {d['python']} · "
        f"{d['elapsed_s']}s of measurement.\n",
        "All figures are **milliseconds, best of n**. `α` is the fitted "
        "log-log exponent over the largest three sizes measured.\n",
        "## Store shapes\n",
        "| store | vertices | edge records | arcs | tasks | decisions.json |",
        "|---|---|---|---|---|---|",
    ]
    for name, s in d["stores"].items():
        parts.append(f"| `{name}` | {s['vertices']:,} | {s['edge_records']:,} | "
                     f"{s['arcs']:,} | {s['tasks']:,} | "
                     f"{s['store_bytes'] / 1e6:.2f} MB |")
    parts += ["\n## Library (in-process, store already loaded)\n",
              table(rows, "library", sizes),
              "\n## CLI (subprocess: interpreter + parse + render)\n",
              table(rows, "cli", cli_sizes)]
    if dens:
        parts += ["\n## Density sweep (1,000 vertices, out-degree varied)\n",
                  table(rows, "density", dens)]
    a.out.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(a.out)


if __name__ == "__main__":
    main()
