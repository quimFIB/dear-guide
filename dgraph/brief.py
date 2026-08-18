"""What an agent needs to know about the graph, right now.

The brief is the payload a coding-agent host injects at the start of a session,
and it is deliberately not `dg show`. Three differences, each load-bearing:

- **`PROVISIONAL` is not in the frontier.** `Graph.frontier()` returns the
  unsettled vertices, and `PROVISIONAL` counts as settled — yet it means "this
  answer rests on a premise that is under review", which is precisely what
  somebody about to build on it needs told. It gets its own section.
- **The staging area and the validity check are part of the situation.** Work
  staged and never applied is invisible in every read command, and
  `.dgraph-pending.json` is gitignored, so it is also invisible in a diff.
- **Plain text, fixed width, no boxes.** This is read through a pipe by a
  program, and paid for in tokens by every session.

`data()` and `text()` render the same information from the same walk of the
graph, so a host that parses JSON and a human reading the terminal cannot be
told different things.
"""

from __future__ import annotations

from dataclasses import dataclass

from dgraph import pending, project
from dgraph.check import run as _check_run
from dgraph.model import Graph

LIMIT = 12          # frontier entries before the brief starts summarising
NOTE_WIDTH = 72     # a note is prose; only its first line, only this much


@dataclass(frozen=True)
class Row:
    """One frontier entry, as both renderings need it."""
    id: str
    status: str
    title: str
    area: str
    waiting_on: list[str]
    unblocks: list[str]


def rows(g: Graph) -> list[Row]:
    """The frontier, with the two things that say which item to pick up.

    Shared with `dg show`'s table so the two cannot disagree about what is open
    or about what each thing is waiting for.
    """
    out = []
    for vid in g.frontier():
        v = g.vertices[vid]
        out.append(Row(
            id=vid, status=v.status, title=v.title, area=v.area,
            waiting_on=g.waiting_on(vid),
            # What deciding this releases. `children` alone would overstate it:
            # a target can depend on this vertex without being blocked by it.
            unblocks=[c for c in g.children(vid)
                      if g.vertices[c].blocker == vid],
        ))
    return out


def attention(g: Graph) -> list[dict]:
    """The PROVISIONAL vertices, and why each one is."""
    return [
        {"id": vid, "title": g.vertices[vid].title, "area": g.vertices[vid].area,
         "because": g.provisional_because(vid)}
        for vid in sorted(g.vertices)
        if g.vertices[vid].base_status == "PROVISIONAL"
    ]


def counts(g: Graph) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in g.vertices.values():
        out[v.base_status] = out.get(v.base_status, 0) + 1
    return out


#: The empty task summary, so every return site and every consumer sees the
#: same shape whether or not the project tracks work.
NO_TASKS = {"counts": {}, "ready": 0, "blocked": 0, "under_review": [],
            "unharvested": []}


def tasks(proj: project.Project) -> dict:
    """A *bounded* summary of the task store.

    Deliberately never enumerates the backlog. The brief is injected into every
    agent session and paid for in tokens each time, so what it carries has to
    stay roughly flat as the task list grows: counts, the ready/blocked split,
    and only the tasks whose premise went back under review — which is rare,
    bounded by a reversal, and the one thing nobody can afford to miss.
    """
    if not proj.has_tasks:
        return dict(NO_TASKS)
    try:
        from dgraph.tasks import TaskGraph
        tg = TaskGraph.load(proj.tasks)
    except Exception:
        # `_check_run` has already reported the unreadable store.
        return dict(NO_TASKS)

    # Every cross-graph reading comes from `cross`, so the rule for "is this
    # premise shaky?" exists once rather than here as well.
    from dgraph import cross

    g = None
    if proj.has_decisions:
        try:
            g = Graph.load(proj.store)
        except Exception:
            g = None

    if g is None:
        ready = [t for t in tg.frontier() if tg.ready(t)]
        reviewed: list[dict] = []
        loose: list[dict] = []
    else:
        ready = [t for t in tg.frontier() if cross.ready(tg, g, t)]
        reviewed = cross.under_review(tg, g)
        loose = cross.unharvested(tg, g)
    return {
        "counts": tg.counts(),
        "ready": len(ready),
        "blocked": len(tg.frontier()) - len(ready),
        "under_review": reviewed,
        "unharvested": loose,
    }


def evidence_map(proj: project.Project) -> dict[str, list[str]]:
    """Unfinished evidence tasks, per decision they inform.

    Kept out of `Row` on purpose: `waiting_on` holds decision ids and callers
    dereference them into the decision store, so a task id in that list would
    eventually be looked up as a decision and crash. The two lists stay apart
    in the data and are joined only where they are printed.
    """
    if not (proj.has_tasks and proj.has_decisions):
        return {}
    try:
        from dgraph import cross
        from dgraph.tasks import TaskGraph
        g, tg = Graph.load(proj.store), TaskGraph.load(proj.tasks)
    except Exception:
        return {}
    out = {}
    for vid in g.frontier():
        waiting = cross.pending_evidence(tg, vid)
        if waiting:
            out[vid] = waiting
    return out


def data(proj: project.Project | None = None) -> dict:
    """The brief as data, for a host adapter.

    Reports `project: null` rather than failing when there is no graph. An
    adapter runs this on every session in every directory, and a session must
    never break because a project has never heard of this tool. The text form
    keeps the error, because a human who typed `dg brief` wants to be told.
    """
    proj = proj or project.find()
    if not proj.exists:
        return {"project": None, "counts": {}, "frontier": [], "attention": [],
                "staged": 0, "violations": [], "tasks": dict(NO_TASKS)}
    # `str(Violation)` verbatim, so an adapter quoting a refusal quotes the
    # same words `dg check` would have printed.
    violations = [
        {"check": v.check, "blocking": v.blocking, "text": str(v)}
        for v in _check_run(proj)
    ]
    try:
        staged = len(pending.load(proj.pending))
    except Exception as exc:
        # Unreadable is not "none staged" — that is the reading that loses
        # work. Report it the way the gate will refuse it.
        staged = 0
        violations.append({
            "check": "store_loads", "blocking": True,
            "text": f"[store_loads] {proj.pending.name} could not be read: "
                    f"{exc}",
        })
    try:
        g = Graph.load(proj.store)
    except Exception:
        # `_check_run` above has already turned the load failure into a
        # `store_loads` violation; a brief that says so is worth more than a
        # crash, which the host hooks read as "nothing to say" — silence at
        # the one moment the graph most needs attention.
        return {"project": str(proj.root), "counts": {}, "frontier": [],
                "attention": [], "staged": staged, "violations": violations,
                "tasks": tasks(proj)}
    ev = evidence_map(proj)
    return {
        "project": str(proj.root),
        "counts": counts(g),
        "frontier": [
            {"id": r.id, "status": r.status, "title": r.title, "area": r.area,
             "waiting_on": r.waiting_on, "unblocks": r.unblocks,
             "evidence": ev.get(r.id, [])}
            for r in rows(g)
        ],
        "attention": attention(g),
        "staged": staged,
        "violations": violations,
        "tasks": tasks(proj),
    }


def _note_line(note: str | None) -> str | None:
    """A note's first line, clipped. Notes and answers contain newlines."""
    if not note:
        return None
    first = note.strip().splitlines()[0].strip()
    if not first:
        return None
    return first if len(first) <= NOTE_WIDTH else first[:NOTE_WIDTH - 1] + "…"


def text(proj: project.Project | None = None, limit: int = LIMIT) -> str:
    """The brief as prose, for injection into a session and for a terminal."""
    proj = proj or project.find()
    d = data(proj)
    try:
        g = Graph.load(proj.store)
    except FileNotFoundError:
        # No store is the caller's case to handle (the CLI checks first and
        # says so); only an *unreadable* store gets the degraded brief.
        raise
    except Exception:
        blocking = [v for v in d["violations"] if v["blocking"]]
        out = [f"DECISION GRAPH  {proj.root}  (store unreadable)",
               "",
               f"CHECK: {len(blocking)} error(s) -- fix before committing"]
        out += [f"  {v['text']}" for v in blocking[:limit]]
        return "\n".join(out)
    fr, att = rows(g), attention(g)

    tally = ", ".join(f"{k} {n}" for k, n in sorted(d["counts"].items()))
    out = [
        f"DECISION GRAPH  {proj.root}  {len(g.vertices)} decisions: {tally}",
        "Record what gets settled with `dg` -- see the decisions skill.",
        "",
        f"FRONTIER ({len(fr)}) -- not settled",
    ]
    width = max((len(r.status) for r in fr[:limit]), default=0)
    for r in fr[:limit]:
        out.append(f"  {r.id}  {r.status:<{width}}  {r.title}  [{r.area}]")
        # An open decision with a spike running is not decidable now, and
        # saying so is a correction to the most-read line in the tool.
        pending_ev = {e["id"]: e["evidence"] for e in d["frontier"]}.get(r.id, [])
        aside = ("waiting on " + ", ".join(r.waiting_on) if r.waiting_on
                 else "waiting on evidence from " + ", ".join(pending_ev)
                 if pending_ev else "decidable now")
        if r.unblocks:
            aside += "; unblocks " + ", ".join(r.unblocks)
        out.append(f"       {aside}")
        note = _note_line(g.vertices[r.id].note)
        if note:
            out.append(f"       note: {note}")
    if len(fr) > limit:
        out.append(f"  +{len(fr) - limit} more -- `dg show`")

    # Empty sections still print. Silence cannot be read as "none": it reads
    # identically to "this brief does not cover that".
    out += ["", f"RESTING ON A PREMISE UNDER REVIEW ({len(att)})"
                + (" -- PROVISIONAL, so not in the frontier" if att else "")]
    for a in att[:limit]:
        why = (f"rests on {', '.join(a['because'])}" if a["because"]
               else f"premises settled again -- `dg confirm {a['id']}`")
        out.append(f"  {a['id']}  {a['title']}  [{a['area']}]  {why}")
    if len(att) > limit:
        out.append(f"  +{len(att) - limit} more")

    out += ["", f"STAGED BUT NOT APPLIED: {d['staged']}"
                + (" -- `dg pending`, then `dg apply`" if d["staged"] else "")]

    # Printed only where a task store exists. Empty sections print for the
    # decision half because silence there is ambiguous, but a project that has
    # never heard of tasks must pay nothing at all for the feature.
    tk = d["tasks"]
    if proj.has_tasks:
        tally = ", ".join(f"{k} {n}" for k, n in sorted(tk["counts"].items()))
        total = sum(tk["counts"].values())
        out += ["", f"TASKS  {total}: {tally}"
                    f"   ({tk['ready']} ready, {tk['blocked']} blocked)"]
        if tk["unharvested"]:
            out.append(f"  evidence in hand, nothing recorded "
                       f"({len(tk['unharvested'])}):")
            for u in tk["unharvested"][:limit]:
                out.append(f"    {u['id']}  {u['title']}  "
                           f"-> `dg decide {u['decision']}`")
        if tk["under_review"]:
            out.append(f"  premise under review ({len(tk['under_review'])}) "
                       f"-- work resting on a decision being re-examined")
            for u in tk["under_review"][:limit]:
                out.append(f"    {u['id']}  {u['title']}  "
                           f"<- because {u['because']}, {u['premise_status']}")
            if len(tk["under_review"]) > limit:
                out.append(f"    +{len(tk['under_review']) - limit} more")

    blocking = [v for v in d["violations"] if v["blocking"]]
    warnings = [v for v in d["violations"] if not v["blocking"]]
    if blocking:
        out.append(f"CHECK: {len(blocking)} error(s) -- fix before committing")
        out += [f"  {v['text']}" for v in blocking[:limit]]
    elif warnings:
        out.append(f"CHECK: clean, {len(warnings)} warning(s) -- `dg check`")
    else:
        out.append("CHECK: clean")
    return "\n".join(out)
