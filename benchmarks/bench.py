#!/usr/bin/env python3
"""How `dear-guide` scales: find operations and graph traversals.

Two layers are measured, because they answer different questions:

- **Library** — `Graph`, `TaskGraph` and `dgraph.query` called in-process on an
  already-loaded store. This is the shape of the algorithm, with no interpreter
  startup or terminal rendering in the way.
- **CLI** — `dg …` as a subprocess. This is what a person actually waits for,
  and it includes the fixed cost (interpreter, imports, store parse) that the
  library numbers deliberately exclude.

Every op is run until either `--budget` seconds have passed or `--max-reps`
repetitions are done, whichever comes first, and both the best and the median
are kept. The best is the honest number for comparing algorithms; the median is
closer to what a machine with other work on it delivers.

An op is **skipped** at a size when its cost at the previous size, extrapolated
quadratically, would exceed `--cap` seconds. Quadratic because the linear scans
in `active_edge` and `depends` make that the shape to fear, and a skip that
names its projection is more useful than an hour spent confirming it.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import gen_store  # noqa: E402  (needs HERE on the path)

from dgraph import cross, project, query, render  # noqa: E402
from dgraph.model import Graph  # noqa: E402
from dgraph.tasks import TaskGraph  # noqa: E402


# ---- timing ---------------------------------------------------------------


class Runner:
    """Times ops, and refuses the ones a projection says will not finish."""

    def __init__(self, budget: float, max_reps: int, cap: float):
        self.budget, self.max_reps, self.cap = budget, max_reps, cap
        self.min_reps = 1
        self.history: dict[str, tuple[int, float]] = {}   # op -> (size, best)
        self.rows: list[dict] = []

    def project(self, key: str, size: int) -> float | None:
        prev = self.history.get(key)
        if prev is None:
            return None
        psize, pbest = prev
        return pbest * (size / psize) ** 2 if psize else None

    def run(self, key: str, size: int, fn, *, inner: int = 1,
            layer: str = "library", note: str = "") -> dict | None:
        est = self.project(key, size)
        if est is not None and est > self.cap:
            row = {"op": key, "size": size, "layer": layer, "skipped": True,
                   "projected_s": round(est, 3), "note": note}
            self.rows.append(row)
            print(f"    {key:<26} SKIPPED (projected {est:.1f}s > {self.cap}s)")
            return row
        times = []
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
        while len(times) < self.max_reps and (
                len(times) < self.min_reps or sum(times) < self.budget):
            t0 = time.perf_counter()
            fn()
            times.append(time.perf_counter() - t0)
        best, med = min(times) / inner, statistics.median(times) / inner
        self.history[key] = (size, best)
        row = {"op": key, "size": size, "layer": layer, "skipped": False,
               "best_s": best, "median_s": med, "reps": len(times),
               "inner": inner, "note": note}
        self.rows.append(row)
        print(f"    {key:<26} {best * 1e3:10.3f} ms  "
              f"(median {med * 1e3:.3f}, n={len(times)}) {note}")
        return row


# ---- the store under test -------------------------------------------------


class Fixture:
    """A generated project, plus the handful of ids the ops are aimed at."""

    def __init__(self, root: Path, size: int, degree: int):
        self.root, self.size, self.degree = root, size, degree
        self.meta = gen_store.write(root, size, degree)
        self.g = Graph.load(root / "decisions.json")
        self.tg = TaskGraph.load(root / "tasks.json")
        # Views, so `dg check` measures invariants rather than `stale_view`.
        (root / "decision-graph.md").write_text(render.render(self.g),
                                                encoding="utf-8")
        from dgraph import task_render
        (root / "tasks.md").write_text(task_render.render(self.tg),
                                       encoding="utf-8")
        ids = sorted(self.g.vertices)
        self.first = ids[0]
        self.mid = ids[len(ids) // 2]
        self.last = ids[-1]
        self.root_id = self.g.roots()[0]
        self.sink = next(v for v in reversed(ids)
                         if not self.g.children(v) and self.g.depends(v))
        self.decided_mid = next(v for v in ids[len(ids) // 2:]
                                if self.g.vertices[v].base_status == "DECIDED")
        tids = sorted(self.tg.tasks)
        self.task_mid = tids[len(tids) // 2]

    def stats(self) -> dict:
        g = self.g
        return {
            **self.meta,
            "roots": len(g.roots()),
            "frontier": len(g.frontier()),
            "descendants_of_root": len(g.descendants(self.root_id)),
            "ancestors_of_sink": len(g.ancestors(self.sink)),
            "max_depth": max(g.depths(self.sink).values()) if g.vertices else 0,
        }


# ---- library ops ----------------------------------------------------------


def library_ops(r: Runner, f: Fixture) -> None:
    g, tg, n = f.g, f.tg, f.size
    store = f.root / "decisions.json"

    r.run("load.decisions", n, lambda: Graph.load(store))
    r.run("load.tasks", n, lambda: TaskGraph.load(f.root / "tasks.json"))

    # --- the two primitives every traversal is built out of.
    # `active_edge` scans the edge list from the front and stops at the first
    # match, and the store is written sorted by `from` — so the same call costs
    # very different amounts depending on where the id sorts. Both ends are
    # measured because the gap is the finding.
    k = 200
    r.run("active_edge.first_id", n,
          lambda: [g.active_edge(f.first) for _ in range(k)], inner=k,
          note="id sorts first in the edge list")
    r.run("active_edge.last_id", n,
          lambda: [g.active_edge(f.last) for _ in range(k)], inner=k,
          note="id sorts last")
    r.run("children.mid", n, lambda: [g.children(f.mid) for _ in range(k)],
          inner=k)
    r.run("depends.mid", n, lambda: [g.depends(f.mid) for _ in range(k)],
          inner=k, note="full edge scan, no early exit")
    r.run("history.mid", n, lambda: [g.history(f.mid) for _ in range(k)],
          inner=k)

    # --- whole-graph traversals
    r.run("descendants.root", n, lambda: g.descendants(f.root_id),
          note=f"reaches {len(g.descendants(f.root_id))} vertices")
    r.run("ancestors.sink", n, lambda: g.ancestors(f.sink),
          note=f"reaches {len(g.ancestors(f.sink))} vertices")
    r.run("depths.sink", n, lambda: g.depths(f.sink))
    r.run("path.root_to_sink", n, lambda: g.path(f.root_id, f.sink))
    r.run("roots", n, g.roots)
    r.run("frontier", n, g.frontier)
    r.run("unpropagated", n, g.unpropagated)

    # --- whole-store passes
    r.run("validate", n, g.validate)
    r.run("render.decisions", n, lambda: render.render(g))
    r.run("tasks.blocked_ids", n, tg.blocked_ids)

    # --- find
    def lens_pair():
        return cross.lenses(g, tg)

    r.run("query.lenses_build", n, lens_pair,
          note="eager work done before any query runs")

    def find(q: str, *, decisions_only: bool = True, archived: bool = True,
             explain: bool = False):
        def go():
            ls = cross.lenses(g, tg, archived=archived)
            if decisions_only:
                ls = [l for l in ls if l.kind == "decisions"]
            parsed = query.parse(q)
            query.vet(parsed, ls)
            for l in query.scope(parsed, ls):
                hits = query.select(parsed, l)
                if explain:
                    for rid in hits[:20]:
                        query.explain(parsed, l, rid)
        return go

    r.run("find.prose_rare", n, find("quokka"), note="~1% of answers")
    r.run("find.prose_common", n, find("threshold"), note="most answers")
    r.run("find.prose_absent", n, find("zzzabsent"), note="matches nothing")
    r.run("find.prose_rare_active", n, find("quokka", archived=False),
          note="--active: skips superseded prose")
    r.run("find.prose_regex", n, find("/quokk[a-z]/"))
    r.run("find.field_status", n, find("status:DECIDED"))
    r.run("find.field_date_range", n, find("date:>=2026-06-01"))
    r.run("find.is_orphaned", n, find("is:orphaned"),
          note="depends+children per vertex")
    r.run("find.is_terminal", n, find("is:terminal"))
    r.run("find.is_decidable", n, find("is:decidable"))
    r.run("find.under_root", n, find(f"under:{f.root_id}"),
          note="one memoised descendants walk")
    r.run("find.above_sink", n, find(f"above:{f.sink}"))
    r.run("find.waits_mid", n, find(f"waits:{f.decided_mid}"),
          note="depends() per vertex")
    r.run("find.both_stores", n, find("threshold", decisions_only=False),
          note="decision + task lenses, as `dg find` runs it")

    def words():
        ls = cross.lenses(g, tg)
        query.words([l for l in ls if l.kind == "decisions"][0])
    r.run("query.words", n, words,
          note="the empty-result suggestion pass")


# ---- CLI ops --------------------------------------------------------------


def cli_ops(r: Runner, f: Fixture, reps: int) -> None:
    env = dict(os.environ, DG_PROJECT=str(f.root),
               PYTHONPATH=str(ROOT), COLUMNS="100", NO_COLOR="1")
    dg = [sys.executable, "-c",
          "import sys; from dgraph.cli import main; sys.exit(main())"]

    def cmd(*args):
        def go():
            subprocess.run(dg + list(args), env=env, cwd=str(f.root),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return go

    def bare():
        subprocess.run([sys.executable, "-c", "import dgraph.cli"], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    old = r.budget, r.max_reps, r.min_reps
    # Subprocesses: a fixed rep count, since the budget rule would give a slow
    # command one sample and a fast one seven.
    r.budget, r.max_reps, r.min_reps = 0.0, reps, reps
    r.run("cli.import_only", f.size, bare, layer="cli",
          note="interpreter + imports, no store")
    r.run("cli.find_rare", f.size, cmd("find", "quokka"), layer="cli")
    r.run("cli.find_absent", f.size, cmd("find", "zzzabsent"), layer="cli",
          note="empty result: adds the words() pass")
    r.run("cli.find_under_root", f.size, cmd("find", f"under:{f.root_id}"),
          layer="cli")
    r.run("cli.find_waits", f.size, cmd("find", f"waits:{f.decided_mid}"),
          layer="cli")
    r.run("cli.check", f.size, cmd("check"), layer="cli")
    # The door over everything: one `by_src`, one reverse index, one
    # `provisional_causes` (proposal E4). The generator's falsifiers are
    # status-uniform, which the proposal names as the corpus lesson; a store
    # with real ones is the measurement still to take.
    r.run("cli.probe_all", f.size, cmd("probe", "--all"), layer="cli",
          note="present mode, prose domain only")
    r.run("cli.brief", f.size, cmd("brief"), layer="cli")
    r.run("cli.node", f.size, cmd("node", f.decided_mid), layer="cli")
    r.run("cli.context", f.size, cmd("context", f.decided_mid), layer="cli")
    r.run("cli.render", f.size, cmd("render"), layer="cli")
    r.budget, r.max_reps, r.min_reps = old


# ---- main -----------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--work", type=Path, required=True,
                   help="Where the generated stores go.")
    p.add_argument("--out", type=Path, default=HERE / "results")
    p.add_argument("--sizes", type=int, nargs="+",
                   default=[200, 500, 1000, 2500, 5000, 10000])
    p.add_argument("--degree", type=int, default=6)
    p.add_argument("--densities", type=int, nargs="*", default=[2, 4, 8, 16],
                   help="Out-degrees to sweep at --density-size.")
    p.add_argument("--density-size", type=int, default=1000)
    p.add_argument("--cli-sizes", type=int, nargs="*",
                   default=[200, 1000, 5000, 10000])
    p.add_argument("--budget", type=float, default=1.2)
    p.add_argument("--max-reps", type=int, default=7)
    p.add_argument("--cli-reps", type=int, default=3)
    p.add_argument("--cap", type=float, default=45.0)
    p.add_argument("--deadline", type=float, default=1800.0,
                   help="Wall-clock seconds for the whole run.")
    a = p.parse_args()

    started = time.time()
    a.out.mkdir(parents=True, exist_ok=True)
    r = Runner(a.budget, a.max_reps, a.cap)
    stats: dict[str, dict] = {}

    def left() -> float:
        return a.deadline - (time.time() - started)

    for n in a.sizes:
        if left() < 60:
            print(f"[deadline] stopping before size {n}")
            break
        print(f"\n=== size {n}, degree {a.degree} "
              f"({left():.0f}s left) ===")
        f = Fixture(a.work / f"n{n}d{a.degree}", n, a.degree)
        stats[f"n{n}d{a.degree}"] = f.stats()
        print("    " + json.dumps(f.stats()))
        library_ops(r, f)
        if n in a.cli_sizes:
            cli_ops(r, f, a.cli_reps)

    # Density sweep: hold the vertex count still and vary the edge count, so
    # the two variables the size sweep moves together can be told apart.
    for d in a.densities:
        if left() < 60:
            print("[deadline] stopping before the density sweep")
            break
        n = a.density_size
        print(f"\n=== density: size {n}, degree {d} ({left():.0f}s left) ===")
        f = Fixture(a.work / f"n{n}d{d}", n, d)
        key = f"density.d{d}"
        stats[f"n{n}d{d}"] = f.stats()
        print("    " + json.dumps(f.stats()))
        g = f.g
        for name, fn, inner in (
            ("descendants.root", lambda: g.descendants(f.root_id), 1),
            ("ancestors.sink", lambda: g.ancestors(f.sink), 1),
            ("depends.mid", lambda: [g.depends(f.mid) for _ in range(200)], 200),
            ("validate", g.validate, 1),
            ("find.prose_rare", None, 1),
            ("find.waits_mid", None, 1),
        ):
            if fn is None:
                q = "quokka" if name.endswith("rare") else f"waits:{f.decided_mid}"

                def fn(q=q):
                    ls = [l for l in cross.lenses(g, f.tg)
                          if l.kind == "decisions"]
                    parsed = query.parse(q)
                    query.vet(parsed, ls)
                    for l in query.scope(parsed, ls):
                        query.select(parsed, l)
            r.run(f"{key}.{name}", d, fn, inner=inner, layer="density",
                  note=f"{f.meta['arcs']} arcs")

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "params": {k: (str(v) if isinstance(v, Path) else v)
                   for k, v in vars(a).items()},
        "stores": stats,
        "rows": r.rows,
        "elapsed_s": round(time.time() - started, 1),
    }
    (a.out / "raw.json").write_text(json.dumps(payload, indent=2) + "\n",
                                    encoding="utf-8")

    with (a.out / "results.csv").open("w", encoding="utf-8") as fh:
        fh.write("layer,op,size,best_ms,median_ms,reps,skipped,projected_s,note\n")
        for row in r.rows:
            fh.write(",".join([
                row["layer"], row["op"], str(row["size"]),
                f"{row.get('best_s', 0) * 1e3:.4f}" if not row["skipped"] else "",
                f"{row.get('median_s', 0) * 1e3:.4f}" if not row["skipped"] else "",
                str(row.get("reps", "")),
                "yes" if row["skipped"] else "no",
                f"{row.get('projected_s', '')}",
                '"' + row.get("note", "").replace('"', "'") + '"',
            ]) + "\n")
    print(f"\nwrote {a.out / 'raw.json'} and {a.out / 'results.csv'} "
          f"in {payload['elapsed_s']}s")


if __name__ == "__main__":
    main()
