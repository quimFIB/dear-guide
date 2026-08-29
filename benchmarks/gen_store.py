#!/usr/bin/env python3
"""Synthetic `dear-guide` stores, for measuring how the tool scales.

A generated store has to be one the tool would accept, or the measurement is
of the error path. So the shape here is constrained by `Graph.validate` and
`tasks.TaskGraph.validate` rather than chosen freely:

- **A layered DAG.** Vertex `i` sits in layer `i // width`, and its active edge
  targets `degree` vertices drawn from the next two layers. Forward-only edges
  make it acyclic by construction, and a fixed layer count (20) means the
  *depth* stays constant as the store grows — so a size sweep varies width and
  edge count alone, and the traversal numbers are not silently a depth sweep.
- **Unsettled work lives at the bottom.** `propagation` refuses a DECIDED
  vertex resting on an unsettled premise, so every OPEN vertex is a sink in the
  last layer. A slice of them then point (with an answerless edge) at BLOCKED
  and PROVISIONAL vertices, which is the only arrangement in which those two
  statuses are legal *and* clean: a block must be backed by an edge, and a
  PROVISIONAL vertex must have an unsettled ancestor or `stale_provisional`
  fires.
- **Reversals are real edges.** A fraction of decided vertices carry a
  superseded edge with its own answer, falsifier and `why`. They matter to the
  measurement twice over: every `active_edge` scan walks past them, and
  `dg find` reads their prose unless `--active` is passed.

Marker words are planted so a search can be aimed:

    quokka       ~1% of answers      a rare hit
    threshold    ~50% of answers     a common hit
    zzzabsent    never               the empty result, which is the expensive
                                     one — it triggers `query.words()`

Usage:
    python gen_store.py OUTDIR --vertices 1000 --degree 6
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

LAYERS = 20
AREAS = ["Corpus", "Harness", "Falsity", "Difficulty", "Benchmark",
         "Retrieval", "Scoring", "Infra"]

NOUNS = ["threshold", "corpus", "splitter", "obligation", "helper", "lemma",
         "target", "contestant", "scorer", "baseline", "axiom", "pin",
         "restatement", "counterexample", "harness", "sweep", "tier"]
VERBS = ["measured", "regenerated", "refused", "carried", "separated",
         "reproduced", "discharged", "propagated", "settled", "narrowed"]


def _sentence(rng: random.Random, words: int) -> str:
    parts = [rng.choice(NOUNS if i % 3 else VERBS) for i in range(words)]
    return " ".join(parts).capitalize() + "."


def _prose(rng: random.Random, sentences: int) -> str:
    return " ".join(_sentence(rng, rng.randint(6, 14)) for _ in range(sentences))


def build_decisions(n: int, degree: int, *, seed: int = 7,
                    superseded_frac: float = 0.15) -> dict:
    rng = random.Random(seed)
    width = max(1, n // LAYERS)
    ids = [f"D{i:05d}" for i in range(1, n + 1)]
    layer = {ids[i]: min(i // width, LAYERS - 1) for i in range(n)}
    by_layer: dict[int, list[str]] = {}
    for vid, l in layer.items():
        by_layer.setdefault(l, []).append(vid)

    last = LAYERS - 1
    bottom = by_layer.get(last, [])
    # The bottom layer splits three ways: OPEN sinks, and the BLOCKED and
    # PROVISIONAL vertices that hang off them.
    n_open = max(1, len(bottom) * 3 // 5)
    opens = bottom[:n_open]
    rest = bottom[n_open:]
    blocked = rest[: len(rest) // 2]
    provisional = rest[len(rest) // 2:]

    vertices, edges = [], []
    status = {}
    for vid in ids:
        status[vid] = "DECIDED"
    for vid in opens:
        status[vid] = "OPEN"
    for vid in provisional:
        status[vid] = "PROVISIONAL"

    # Answerless edges from the OPEN sinks: they carry the block and give the
    # PROVISIONAL vertices the unsettled ancestor they need.
    hangers = blocked + provisional
    for i, vid in enumerate(hangers):
        parent = opens[i % len(opens)]
        edges.append({"from": parent, "to": [vid], "active": True})
        if vid in blocked:
            status[vid] = f"BLOCKED:{parent}"

    for i, vid in enumerate(ids):
        l = layer[vid]
        area = AREAS[i % len(AREAS)]
        v = {"id": vid, "title": f"{_sentence(rng, 7)[:-1]} — {vid}",
             "area": area, "status": status[vid]}
        if status[vid] != "DECIDED":
            v["note"] = _prose(rng, 2)
        vertices.append(v)

        if status[vid] != "DECIDED":
            continue
        pool = [t for ll in (l + 1, l + 2) for t in by_layer.get(ll, [])]
        # Never point into the hangers: their only premise must be an OPEN
        # sink, or `depends` gives a PROVISIONAL vertex a settled parent too
        # and the arrangement stops meaning what it says.
        pool = [t for t in pool if t not in hangers]
        k = min(degree, len(pool))
        to = sorted(rng.sample(pool, k)) if k else []
        answer = _prose(rng, 3)
        if i % 100 == 0:
            answer = "The quokka " + answer
        edges.append({
            "from": vid, "to": to, "active": True,
            "answer": answer,
            "falsifier": _sentence(rng, 12),
            "source": f"T{i % 400:04d}",
            "date": f"2026-{1 + (i % 12):02d}-{1 + (i % 27):02d}",
        })
        if to and rng.random() < superseded_frac:
            edges.append({
                "from": vid, "to": to[:1], "active": False,
                "answer": _prose(rng, 2),
                "falsifier": _sentence(rng, 10),
                "source": f"T{(i + 7) % 400:04d}",
                "date": f"2025-{1 + (i % 12):02d}-{1 + (i % 27):02d}",
                "summary": _sentence(rng, 6),
                "why": _sentence(rng, 9),
                "replaced_by": _sentence(rng, 5),
            })

    return {"areas": AREAS, "vertices": vertices, "edges": edges}


def build_tasks(n: int, degree: int, decision_ids: list[str], decided: set[str],
                *, seed: int = 11, evidence_frac: float = 0.0,
                open_ids: list[str] | None = None) -> dict:
    rng = random.Random(seed)
    width = max(1, n // LAYERS)
    ids = [f"T{i:05d}" for i in range(1, n + 1)]
    layer = {ids[i]: min(i // width, LAYERS - 1) for i in range(n)}
    by_layer: dict[int, list[str]] = {}
    for tid, l in layer.items():
        by_layer.setdefault(l, []).append(tid)

    tasks, edges = [], []
    for i, tid in enumerate(ids):
        l = layer[tid]
        # Finished work at the top, outstanding work at the bottom, and the
        # split is monotone in the layer because it has to be:
        # `task_done_before_prerequisite` refuses a DONE task whose
        # prerequisite is not resolved, so a DONE task below an outstanding one
        # is not a store the tool would accept. A TODO in the middle layers
        # still has unresolved prerequisites, so readiness stays non-trivial.
        # PARKED and DROPPED are confined to the final layer, which has no
        # successors, for a second reason of the same kind: `parked_holding_work`
        # and `released_by_drop` fire over every unfinished dependant, so
        # stopped work higher up would make `dg check`'s cost a rendering
        # measurement instead of an invariant one.
        if l < LAYERS // 2:
            st = "DONE"
        elif l < LAYERS - 1:
            st = ("DOING", "TODO")[i % 2]
        else:
            st = ("TODO", "PARKED", "TODO", "DROPPED")[i % 4]
        t = {"id": tid, "title": f"{_sentence(rng, 6)[:-1]} — {tid}",
             "area": AREAS[i % len(AREAS)], "status": st,
             "note": _prose(rng, 2)}
        if st == "DONE":
            t["completions"] = [{"date": f"2026-0{1 + i % 9}-1{i % 10}",
                                 "outcome": _sentence(rng, 8)}]
        if st in ("PARKED", "DROPPED"):
            t["stops"] = [{"why": _sentence(rng, 7),
                           "date": f"2026-0{1 + i % 9}-1{i % 10}"}]
        if i % 3 == 0 and decision_ids:
            t["because"] = [decision_ids[(i * 13) % len(decision_ids)]]
        # `--evidence-frac` off by default, and the branch consumes no
        # randomness when it is, so every store generated before this existed
        # regenerates byte-identically.
        #
        # It points at an **OPEN** decision, never a decided one, and that is
        # a correctness requirement rather than a stylistic choice.
        # `evidence_for` is the one edge kind running task -> decision, so it
        # is what makes a cross-graph cycle possible at all: pair it with a
        # `because` edge (decision -> task) reaching the same place and
        # `link_acyclic` fires. Pointing it at an OPEN vertex is safe because
        # those are sinks in the last layer -- nothing is reachable from them,
        # so no path can come back round. Naively targeting any decision
        # produced 7,644 cycle findings at degree 6 and 44,383 at degree 32.
        #
        # It is also the honest semantics: evidence bears on a question that is
        # still open. A finished task pointing at a settled decision is the
        # `evidence_after_deciding` warning, which is a real finding and not a
        # shape a generator should mass-produce.
        if evidence_frac and open_ids and i % max(1, int(1 / evidence_frac)) == 0:
            t["evidence_for"] = open_ids[(i * 29) % len(open_ids)]
        tasks.append(t)

        pool = [x for ll in (l + 1, l + 2) for x in by_layer.get(ll, [])]
        k = min(degree, len(pool))
        if k:
            edges.append({"from": tid, "to": sorted(rng.sample(pool, k)),
                          "kind": "precedes"})
        if k and i % 5 == 0:
            edges.append({"from": tid, "to": sorted(rng.sample(pool, min(2, k))),
                          "kind": "prompted"})
    return {"areas": AREAS, "tasks": tasks, "edges": edges}


def write(outdir: Path, n: int, degree: int, *, tasks_ratio: float = 0.5,
          task_degree: int | None = None, evidence_frac: float = 0.0) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    d = build_decisions(n, degree)
    decided = {v["id"] for v in d["vertices"] if v["status"] == "DECIDED"}
    open_ids = sorted(v["id"] for v in d["vertices"] if v["status"] == "OPEN")
    tn = max(1, int(n * tasks_ratio))
    t = build_tasks(tn, task_degree if task_degree is not None else degree,
                    sorted(decided), decided, evidence_frac=evidence_frac,
                    open_ids=open_ids)
    (outdir / "decisions.json").write_text(
        json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (outdir / "tasks.json").write_text(
        json.dumps(t, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "vertices": len(d["vertices"]),
        "edge_records": len(d["edges"]),
        "arcs": sum(len(e["to"]) for e in d["edges"]),
        "active_edges": sum(1 for e in d["edges"] if e["active"]),
        "tasks": len(t["tasks"]),
        "task_edges": len(t["edges"]),
        "store_bytes": (outdir / "decisions.json").stat().st_size,
        "tasks_bytes": (outdir / "tasks.json").stat().st_size,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("outdir", type=Path)
    p.add_argument("--vertices", type=int, default=1000)
    p.add_argument("--degree", type=int, default=6)
    # The task store used to be pinned at half the decision count and the same
    # degree, with no `evidence_for` link ever written -- so `cross.evidence`
    # and everything reading it answered on an empty set in every measurement
    # taken before these existed. The defaults reproduce that exactly.
    p.add_argument("--tasks-ratio", type=float, default=0.5,
                   help="tasks per decision (0.5 = the original shape)")
    p.add_argument("--task-degree", type=int, default=None,
                   help="task out-degree; defaults to --degree")
    p.add_argument("--evidence-frac", type=float, default=0.0,
                   help="fraction of tasks carrying `evidence_for` (0 = none)")
    a = p.parse_args()
    print(json.dumps(write(a.outdir, a.vertices, a.degree,
                           tasks_ratio=a.tasks_ratio,
                           task_degree=a.task_degree,
                           evidence_frac=a.evidence_frac), indent=2))
