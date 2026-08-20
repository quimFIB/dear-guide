"""The reasoning a node rests on, recovered in one walk.

`dg node` shows one decision and `dg export` returns ancestor *ids*; neither
answers the question somebody actually has before building on a decision or
handing work to a subagent: **why is this settled, and what would unsettle it?**
That answer is the chain of premises, each with the answer it reached, the
evidence that reached it, and the falsifier that would overturn it.

The rendering already existed once, inside the org compose buffer
(`dgraph/editor.py`), where only an editor could see it. This module owns the
*walk*; `editor` renders it as org and this module renders it as plain text and
as JSON. Three consumers, one traversal, so they cannot disagree about what a
decision rests on.

Two properties are deliberate, both borrowed from `brief.py`:

- **`data()` and `text()` come from the same walk.** A host that parses JSON and
  a human reading a terminal are told the same thing.
- **Plain text, fixed width.** This is piped into a subagent's prompt. Rich
  soft-wraps at `$COLUMNS`, which would move the ids onto the wrong lines.

A task id is accepted too, and resolves through `dgraph.cross` — the only module
allowed to see both stores. The chain behind a task is the chain behind the
decision it exists `because` of, which is precisely the context missing when
work is dispatched on its own.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field

from dgraph.model import Graph
from dgraph.tasks import TaskGraph

WIDTH = 76


class UnknownNode(LookupError):
    """No such id in either store. Carries what the caller should say."""


@dataclass
class Premise:
    """One ancestor of a decision, with everything that makes it a premise."""

    id: str
    title: str
    area: str
    status: str
    depth: int
    answer: str | None = None
    falsifier: str | None = None
    source: str | None = None
    date: str | None = None
    #: The answer's dialect: "org", else markdown. Carried rather than resolved
    #: here so `data()` stays faithful to the store and only `text()` converts.
    format: str | None = None
    #: What else this premise opened — the siblings of the node we came from.
    #: Present because a premise that opened five questions is a load-bearing
    #: one, and that is invisible from the chain alone.
    also_opened: list[str] = field(default_factory=list)
    #: True when this premise is itself unsettled or under review, which is the
    #: single most important thing about a chain: an answer resting on it is
    #: provisional whatever its own status says.
    shaky: bool = False


@dataclass
class Superseded:
    summary: str | None
    replaced_by: str | None
    why: str | None


def _base(status: str) -> str:
    return status.split(":")[0]


#: A premise in one of these states does not hold the weight put on it.
SHAKY = frozenset({"OPEN", "BLOCKED", "REOPENED", "PROVISIONAL"})


def chain(g: Graph, vid: str) -> list[Premise]:
    """Every decision `vid` rests on, nearest premise last.

    Ordered by depth then id — the same order `editor._context` used, which
    reads as the order the project actually decided things in: roots first, the
    decision's immediate premises last.

    Depths come from one walk (`Graph.depths`) rather than one per ancestor:
    each call is a full traversal, so asking per node made a deep chain
    quadratic and a long one hang.
    """
    depth = g.depths(vid)
    return [_premise(g, a, of=vid, depth=depth.get(a, 0)) for a in
            sorted(g.ancestors(vid), key=lambda a: (depth.get(a, 0), a))]


def _premise(g: Graph, aid: str, of: str, depth: int) -> Premise:
    v = g.vertices[aid]
    e = g.active_edge(aid)
    decided = e is not None and e.decided
    return Premise(
        id=aid, title=v.title, area=v.area, status=v.status, depth=depth,
        answer=e.answer if decided else None,
        falsifier=e.falsifier if decided else None,
        source=e.source if decided else None,
        date=e.date if decided else None,
        format=e.format if decided else None,
        # Only siblings of the node being explained, not every child: the
        # question is what else this premise is holding up alongside us.
        also_opened=[c for c in g.children(aid) if c != of],
        shaky=_base(v.status) in SHAKY,
    )


def decision(g: Graph, vid: str, tg: TaskGraph | None = None) -> dict:
    """The context behind one decision."""
    v = g.vertices[vid]
    e = g.active_edge(vid)
    decided = e is not None and e.decided
    premises = chain(g, vid)
    out = {
        "kind": "decision",
        "id": vid, "title": v.title, "area": v.area, "status": v.status,
        "depth": g.depth(vid), "note": v.note, "format": v.format,
        "depends_on": g.depends(vid), "opens": g.children(vid),
        "answer": e.answer if decided else None,
        "falsifier": e.falsifier if decided else None,
        "source": e.source if decided else None,
        "date": e.date if decided else None,
        "answer_format": e.format if decided else None,
        "chain": [p.__dict__ for p in premises],
        "superseded": [
            Superseded(h.summary, h.replaced_by, h.why).__dict__
            for h in g.history(vid)
        ],
        "shaky_premises": [p.id for p in premises if p.shaky],
    }
    out["work"] = _work(tg, vid) if tg is not None else None
    return out


def _work(tg: TaskGraph, did: str) -> dict:
    """The two directions work relates to a decision, from `cross` alone."""
    from dgraph import cross
    return {
        "rests_on": cross.rests_on(tg, did),
        "evidence": cross.evidence(tg, did),
        "pending_evidence": cross.pending_evidence(tg, did),
    }


def task(tg: TaskGraph, tid: str, g: Graph | None = None) -> dict:
    """The context behind one task: its own graph, then its premise's chain.

    The premise's whole chain is pulled in rather than named, because that is
    the thing a dispatched agent cannot look up for itself without knowing to.

    Every reading of the link between the two stores comes from `cross`; this
    function never reads the task's link fields directly, which is the rule
    `tests/test_cross.py` enforces.
    """
    from dgraph import cross
    t = tg.tasks[tid]
    out = {
        "kind": "task",
        "id": tid, "title": t.title, "area": t.area, "status": t.status,
        "note": t.note, "format": t.format,
        "outcome": t.outcome, "done": t.done, "why": t.why,
        "prerequisites": tg.prerequisites(tid),
        "waiting_on": tg.waiting_on(tid),
        "unblocks": tg.unblocks(tid),
        # Provenance, kept apart from the three above and never folded into
        # them: this is what turned the work up, not what it waits on, and an
        # agent handed one list would read the second as the first.
        "discovered_during": tg.discovered_during(tid),
        "prompted": tg.prompted(tid),
        "because": None, "evidence_for": None,
        "premise": None, "chain": [], "shaky_premises": [],
        "gated_by": None, "ready": tg.ready(tid), "verdict": None,
    }
    if g is None:
        # A tasks-only project, or an unreadable decision store. Say so rather
        # than reporting "no premise", which is a different fact.
        out["verdict"] = ("the decision store is not available here, so this "
                          "task's premise could not be checked")
        return out
    link = cross.task_link(tg, g, tid)
    out["because"] = link["because"]
    out["evidence_for"] = link["evidence_for"]
    out["gated_by"] = link["gated_by"]
    out["ready"] = link["ready"]
    if link["premise"]:
        out["premise"] = decision(g, link["premise"], tg)
        out["chain"] = out["premise"]["chain"]
        out["shaky_premises"] = out["premise"]["shaky_premises"]
    out["verdict"] = _verdict(g, link, out)
    return out


def _verdict(g: Graph, link: dict, out: dict) -> str:
    """One sentence saying whether this work stands on solid ground.

    The point of handing a chain to an agent is that it acts on it, so the
    reading is stated rather than left to be inferred from five statuses. The
    *facts* come from `cross.task_link` and from the chain walk above; only the
    phrasing is decided here.
    """
    because = link["because"]
    if link["dangling"]:
        return (f"the premise {because} is not in the decision store — the "
                f"link is dangling and `dg check` will say so")
    if link["gated_by"]:
        v = g.vertices[link["gated_by"]]
        return (f"this work waits on {link['gated_by']} ({_base(v.status)}), "
                f"which is not settled — starting it now is a bet on the answer")
    if out["premise"] and _base(out["premise"]["status"]) == "PROVISIONAL":
        return (f"the premise {because} is PROVISIONAL: it rests on something "
                f"under review, so this work may not survive the outcome")
    if out["shaky_premises"]:
        return (f"the premise {because} is settled, but it rests on "
                f"{', '.join(out['shaky_premises'])}, which is not — treat the "
                f"conclusion as load-bearing but not final")
    if out["waiting_on"]:
        return (f"blocked on {', '.join(out['waiting_on'])}; the reasoning "
                f"behind it is settled")
    if link["evidence_for"] and link["unfinished"]:
        return (f"this work is evidence for {link['evidence_for']}, which stays "
                f"open until it lands — record it with `dg task done`")
    if not because:
        return "no premise is recorded for this work, so nothing here can go stale"
    return "the reasoning behind this work is settled all the way down"


# ---- resolution ----------------------------------------------------------


def data(proj=None, vid: str = "") -> dict:
    """The context behind one id, whichever store it lives in.

    Loads only what it needs: a D-id in a project with no `tasks.json` never
    touches the task store, and a tasks-only project never needs a decision
    store to describe a task with no premise.
    """
    from dgraph import project as _project
    proj = proj or _project.find()

    g = tg = None
    if proj.has_decisions:
        g = Graph.load(proj.store)
    if proj.has_tasks:
        tg = TaskGraph.load(proj.tasks)

    if g is not None and vid in g.vertices:
        return decision(g, vid, tg)
    if tg is not None and vid in tg.tasks:
        return task(tg, vid, g)
    raise UnknownNode(_unknown(vid, g, tg))


def _unknown(vid: str, g: Graph | None, tg: TaskGraph | None) -> str:
    """Why the id did not resolve — including the case that matters most, an
    id of the right shape in a store the project does not have."""
    if vid.startswith("T") and tg is None:
        return f"{vid} looks like a task id, but this project has no tasks.json"
    if vid.startswith("D") and g is None:
        return f"{vid} looks like a decision id, but this project has no decisions.json"
    known = []
    if g is not None:
        known.append(f"{len(g.vertices)} decision(s)")
    if tg is not None:
        known.append(f"{len(tg.tasks)} task(s)")
    return f"unknown id {vid} — this project has {' and '.join(known) or 'neither store'}"


# ---- the text rendering --------------------------------------------------


def _wrap(s: str | None, indent: str = "       ", fmt: str | None = None) -> list[str]:
    """Prose, indented and wrapped, with its own line breaks kept.

    Wrapping paragraph-by-paragraph rather than over the whole string, because
    an answer may contain a table: collapsing every newline turns one into a
    single unreadable run. Org prose is converted first, exactly as the
    generated view converts it, so the same answer reads the same way in
    `decision-graph.md` and here.
    """
    if not s:
        return []
    from dgraph import orgmd
    s = orgmd.to_markdown(s, fmt) or s
    width = max(WIDTH - len(indent), 20)
    out: list[str] = []
    for line in s.strip().splitlines():
        if not line.strip():
            out.append("")
        # A table row or a list item is structure; wrapping it destroys it.
        elif line.lstrip().startswith(("|", "-", "*", "#")) or len(line) <= width:
            out.append(indent + line.rstrip())
        else:
            out += [indent + w for w in textwrap.wrap(line, width)]
    return out


def _premise_lines(p: dict) -> list[str]:
    mark = " ←— not settled" if p["shaky"] else ""
    out = [f"  {p['id']}  {_base(p['status']):<12}{p['title']}{mark}"]
    if p["answer"]:
        out += _wrap(p["answer"], fmt=p.get("format"))
    else:
        out.append("       (no answer recorded)")
    if p["falsifier"]:
        out.append("       falsifier:")
        out += _wrap(p["falsifier"], "         ")
    if p["source"]:
        out.append(f"       source: {p['source']}"
                   + (f"  ·  {p['date']}" if p["date"] else ""))
    if p["also_opened"]:
        out.append(f"       also opened: {', '.join(p['also_opened'])}")
    return out


def text(d: dict) -> str:
    """The same information as `data()`, for a terminal or a prompt."""
    return _decision_text(d) if d["kind"] == "decision" else _task_text(d)


def _decision_text(d: dict) -> str:
    out = [f"{d['id']}  {_base(d['status'])}  {d['title']}  [{d['area']}]"]
    if d["depends_on"]:
        out.append(f"  rests on {', '.join(d['depends_on'])}"
                   + (f" · opens {', '.join(d['opens'])}" if d["opens"] else ""))
    elif d["opens"]:
        out.append(f"  a root · opens {', '.join(d['opens'])}")
    if d["answer"]:
        out.append("")
        out += _wrap(d["answer"], "  ", d.get("answer_format"))
        if d["falsifier"]:
            out.append("  falsifier: " + " ".join(d["falsifier"].split()))
        if d["source"]:
            out.append(f"  source: {d['source']}"
                       + (f"  ·  {d['date']}" if d["date"] else ""))
    elif d["note"]:
        out.append("")
        out += _wrap(d["note"], "  ", d.get("format"))

    if d["chain"]:
        out += ["", f"RESTS ON ({len(d['chain'])}) — nearest premise last"]
        for p in d["chain"]:
            out += _premise_lines(p)
    else:
        out += ["", "RESTS ON — nothing; this is a root"]

    if d["superseded"]:
        out += ["", f"SUPERSEDED HERE ({len(d['superseded'])})"]
        for h in d["superseded"]:
            out.append(f"  “{h['summary']}” → {h['replaced_by'] or '(undecided)'}")
            out += _wrap(h["why"], "    ")

    w = d.get("work")
    if w and (w["rests_on"] or w["evidence"]):
        out += ["", "WORK"]
        if w["rests_on"]:
            out.append(f"  because of this: {', '.join(w['rests_on'])}")
        if w["evidence"]:
            pend = set(w["pending_evidence"])
            out.append("  evidence for it: " + ", ".join(
                f"{t}*" if t in pend else t for t in w["evidence"]))
            if pend:
                out.append("  (* not finished yet)")

    if d["shaky_premises"]:
        out += ["", f"→ this rests on {', '.join(d['shaky_premises'])}, which "
                    f"is not settled."]
    return "\n".join(out) + "\n"


def _task_text(d: dict) -> str:
    out = [f"{d['id']}  {d['status']}  {d['title']}  [{d['area']}]"]
    bits = []
    if d["prerequisites"]:
        bits.append("after " + ", ".join(d["prerequisites"]))
    if d["unblocks"]:
        bits.append("unblocks " + ", ".join(d["unblocks"]))
    if d["discovered_during"]:
        bits.append("found doing " + ", ".join(d["discovered_during"]))
    if d["prompted"]:
        bits.append("turned up " + ", ".join(d["prompted"]))
    if bits:
        out.append("  " + " · ".join(bits))
    if d["waiting_on"]:
        out.append(f"  waiting on {', '.join(d['waiting_on'])}")
    if d["note"]:
        out.append("")
        out += _wrap(d["note"], "  ", d.get("format"))
    if d["outcome"]:
        out.append(f"  outcome: {d['outcome']}"
                   + (f"  ·  {d['done']}" if d["done"] else ""))
    # Gated on the status for the reason the two generated views are: `why`
    # says why the work is not being done, and this text is piped into a
    # subagent's prompt, where "dropped: …" under a DOING task is a claim it
    # would act on.
    if d["why"] and d["status"] == "DROPPED":
        out.append(f"  dropped: {d['why']}")

    p = d["premise"]
    if p:
        out += ["", f"BECAUSE  {p['id']}  {_base(p['status'])}  {p['title']}"]
        if p["answer"]:
            out += _wrap(p["answer"], "  ", p.get("format"))
        if p["falsifier"]:
            out.append("  falsifier: " + " ".join(p["falsifier"].split()))
        if p["source"]:
            out.append(f"  source: {p['source']}"
                       + (f"  ·  {p['date']}" if p["date"] else ""))
        if d["chain"]:
            out += ["", f"WHICH RESTS ON ({len(d['chain'])}) — nearest premise last"]
            for q in d["chain"]:
                out += _premise_lines(q)
    elif d["because"]:
        out += ["", f"BECAUSE  {d['because']}  — not found in the decision store"]

    if d["evidence_for"]:
        out += ["", f"EVIDENCE FOR  {d['evidence_for']} — that decision is "
                    f"waiting on what this work finds"]

    out += ["", f"→ {d['verdict']}"]
    return "\n".join(out) + "\n"
