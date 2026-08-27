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

There are two renderings, and the default is the short one. `compact()` gives
the chain schematically — the arrow line, then one clipped line per premise —
because that is what a person asking a question in a terminal wants, and it is
what `dg context` prints unless asked otherwise. `text()` gives every answer,
falsifier and source, which is the form to paste into a subagent's prompt, and
is what `--full` returns.

Three properties are deliberate, the first two borrowed from `brief.py`:

- **`data()`, `text()` and `compact()` come from the same walk.** A host that
  parses JSON, an agent handed the full text and a human reading a terminal are
  told the same thing at three lengths.
- **Plain text, fixed width.** This is piped into a subagent's prompt. Rich
  soft-wraps at `$COLUMNS`, which would move the ids onto the wrong lines.
- **The short form says it is short.** Every compact rendering ends with the
  flag that expands it, so nobody has to guess whether the tool is summarising
  or simply does not know.

A task id is accepted too, and resolves through `dgraph.cross` — the only module
allowed to see both stores. The chain behind a task is the chain behind the
decision it exists `because` of, which is precisely the context missing when
work is dispatched on its own.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field

from dgraph import compact as _c
from dgraph.model import Graph, rival_note
from dgraph.tasks import TaskGraph, done_label, stop_label

WIDTH = 76

#: The compact rendering's budget. Wider than `WIDTH`, because those are
#: wrapped paragraphs and these are single lines that must not fold; and no
#: wider than 80, because this is printed plain — nothing reflows it, so it has
#: to fit the narrowest terminal anyone actually uses. Deliberately fixed
#: rather than read from the terminal: `dg context` is piped into a subagent's
#: prompt at least as often as it is read, and output that changes shape with
#: `$COLUMNS` is output two readers can disagree about.
COMPACT_WIDTH = 80


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
        # The one an agent needs most, because an agent composes against what
        # it is told and never opens the file. `None` where the store is
        # sound, which is every store this tool can write.
        "rival_answers": (rival_note(len(g.rival_answers(vid)))
                          if g.rival_answers(vid) else None),
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
        # What the evidence actually produced. Printed beside the answer,
        # because an outcome that arrived after the answer is the one thing
        # nobody was told to read against it — see
        # `cross.evidence_after_deciding`, which goes on saying so in the
        # store long after the decide-time warning has scrolled away.
        "outcomes": {t: tg.tasks[t].outcome
                     for t in cross.evidence(tg, did)
                     if tg.tasks[t].status == "DONE" and tg.tasks[t].outcome},
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
        "outcome": t.outcome, "done": t.done,
        # Every completion besides the live pair above, for the reason `stops`
        # is sent whole: an agent reading this needs to know whether this work
        # has been finished before, and what it produced that time. The two
        # scalars are the live reading and stay, so nothing that already reads
        # them had to change.
        "completions": [{"date": c.date, "outcome": c.outcome}
                        for c in t.completions],
        # The word for the live one, from `tasks.DONE_LABEL`, never chosen here.
        "done_label": done_label(t.status),
        # The live reason where the status makes that claim, and every
        # stoppage besides. An agent reading this needs both: why it is stopped
        # now, and whether this is the third time.
        "why": t.stopped_because,
        # The word for it, from `tasks.STOP_LABEL` and never chosen here.
        # Three renderers used to pick their own, and this one picked it by
        # testing the status a second time — on top of `stopped_because`,
        # which has already gated on it — so the PARKED reason was dropped.
        "stop_label": stop_label(t.status),
        "stops": [{"why": k.why, "date": k.date} for k in t.stops],
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
    because = ", ".join(link["because"])
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
    """Everything `data()` holds, for a terminal or a prompt.

    The form to hand a subagent: each premise's answer in full, its evidence
    and its falsifier. `compact()` is what a person reading a terminal gets by
    default; both walk the same chain, so neither can say something the other
    denies.
    """
    return _decision_text(d) if d["kind"] == "decision" else _task_text(d)


def _decision_text(d: dict) -> str:
    out = [f"{d['id']}  {_base(d['status'])}  {d['title']}  [{d['area']}]"]
    if d["depends_on"]:
        out.append(f"  rests on {', '.join(d['depends_on'])}"
                   + (f" · opens {', '.join(d['opens'])}" if d["opens"] else ""))
    elif d["opens"]:
        out.append(f"  a root · opens {', '.join(d['opens'])}")
    # Before the answer, for the reason `dg node` puts it there: a reader who
    # meets the caveat below has already read the answer as *the* answer.
    if d.get("rival_answers"):
        out += ["", "!! " + d["rival_answers"]]
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
            # Beside the answer, not behind an id. A reader who has to look
            # the task up to find out what it measured does not look it up.
            for t, outcome in (w.get("outcomes") or {}).items():
                out += _wrap(f"{t} found: {outcome}", "    ")

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
    # Every completion, oldest first, with the live one marked — the same
    # list and the same marker the stops below use, and for the same reason:
    # an earlier result is not superseded prose, it is what the work produced
    # that time round, and printing only the live one hides it entirely.
    if d["completions"]:
        out += ["", f"OUTCOME ({len(d['completions'])}) — oldest first"]
        last = len(d["completions"]) - 1
        for i, c in enumerate(d["completions"]):
            mark = (f"  ← {d['done_label']}"
                    if d["done_label"] and i == last else "")
            out.append(f"  {c['date']}  {c['outcome']}{mark}")
    # Every stoppage, oldest first, with the live one marked — one list and
    # one marker, the construction `task_render` and `app.html` both use.
    #
    # The live reason used to be printed here on its own, under a
    # `status == "DROPPED"` test laid on top of `Task.stopped_because`, which
    # has already gated on the status. The second test swallowed every PARKED
    # reason: the one status whose entire point is that somebody wrote down
    # why the work stopped. `stop_label` decides the word now, in one place.
    if d["stops"]:
        out += ["", f"STOPPED ({len(d['stops'])}) — oldest first"]
        last = len(d["stops"]) - 1
        for i, k in enumerate(d["stops"]):
            mark = (f"  ← {d['stop_label'].lower()}"
                    if d["stop_label"] and i == last else "")
            out.append(f"  {k['date']}  {k['why']}{mark}")

    p = d["premise"]
    if p:
        out += ["", f"BECAUSE  {p['id']}  {_base(p['status'])}  {p['title']}"]
        # The sharpest case in the finding: an agent composing against a
        # premise, told one answer, with no way to know a second exists. It is
        # said here and not only on the decision's own context, because this
        # is the reading the agent actually asked for.
        if p.get("rival_answers"):
            out += ["", "  !! " + p["rival_answers"]]
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


# ---- the compact rendering -----------------------------------------------


def _chain_arrow(ids: list[str], shaky: set[str], last: str) -> str:
    """`D01 → D02 → D04! → T04` — the whole chain on one line.

    The schematic that makes the compact form worth reading: the shape of the
    reasoning, in the order it was built, with `!` on every link that does not
    hold. A reader who only takes one line from this command should take this
    one.
    """
    return " → ".join([f"{i}!" if i in shaky else i for i in ids] + [last])


def _fold(bits: list[str], prefix: str = "  ", blank: bool = False) -> list[str]:
    """Short phrases joined with `·`, wrapped where too many of them fit.

    The one place this module's two rules have to be reconciled: the width is
    fixed and an id is never clipped, so a node with a dozen premises folds
    onto a second line rather than running past the margin.

    Returned as a list so a caller can splice it in without testing for empty,
    which is where the stray blank lines came from.
    """
    if not bits:
        return []
    return ([""] if blank else []) + textwrap.wrap(
        prefix + " · ".join(bits), COMPACT_WIDTH,
        subsequent_indent=" " * len(prefix))


def _chain_lines(ids: list[str], shaky: set[str], last: str) -> list[str]:
    """The arrow line, folded to the margin, with its legend where it fits.

    Ids are never clipped, so a chain seventeen deep folds rather than running
    — and the legend is what moves, because it explains the marks rather than
    carrying any.
    """
    lines = textwrap.wrap("CHAIN  " + _chain_arrow(ids, shaky, last),
                          COMPACT_WIDTH, subsequent_indent=" " * 7,
                          break_long_words=False, break_on_hyphens=False)
    if not shaky:
        return lines
    if len(lines[-1]) + 19 <= COMPACT_WIDTH:
        lines[-1] += "    ! = not settled"
    else:
        lines.append(" " * 7 + "! = not settled")
    return lines


def _premise_row(p: dict) -> tuple[str, str, str, str]:
    """One premise as a listing row: what it answered, in one line."""
    answer = _c.gist(p["answer"], p.get("format"))
    if not answer:
        # Two different facts, and collapsing them is the mistake: a premise
        # with no answer *because it is still open* is the thing the chain
        # exists to surface, while a settled one with an empty answer is a
        # record someone left half-written.
        answer = "not settled" if p["shaky"] else "no answer recorded"
    return (p["id"], _base(p["status"]), p["title"], answer)


def compact(d: dict) -> str:
    """The chain schematically: one line per premise, prose clipped.

    `text()` prints every answer, falsifier and source, which is right when the
    output is going into a subagent's prompt and wrong when a person is asking
    a question in a terminal. Same walk, same verdict, same ids — only the
    prose is clipped, and the closing line says which flag brings it back.
    """
    # Only the title is clipped, never the assembled line: `clip` normalises
    # whitespace, and running the head through it closed up the double spaces
    # that separate the id from the status from the title.
    lead = f"{d['id']}  {_base(d['status'])}  "
    title = _c.clip(d["title"],
                    COMPACT_WIDTH - len(lead) - len(d["area"]) - 4)
    out = [f"{lead}{title}  [{d['area']}]"]
    out += _fold(_relations(d))

    # The node's own answer, or its note where it has no answer yet. One line,
    # and the line a reader wants first: `dg context D02` that does not say
    # what D02 decided is a command you have to follow with `dg node`.
    # Before that line, because it is a caveat *about* it — and this is the
    # surface the finding is really about: the compact form is what an agent
    # reads, and an agent composes against what it is told. Not clipped: the
    # whole point is that the reader cannot tell from the answer alone, so a
    # sentence trimmed to a width is a sentence that could lose the reason.
    if d.get("rival_answers"):
        out += textwrap.wrap("!! " + d["rival_answers"], COMPACT_WIDTH,
                             initial_indent="  ", subsequent_indent="     ")
    own = _c.gist(d.get("answer"), d.get("answer_format")) or _c.gist(
        d.get("note"), d.get("format"))
    if own:
        out.append("  " + _c.clip(own, COMPACT_WIDTH - 2))

    # Why the work stopped, on its own line. The default form rendered neither
    # the parked nor the dropped reason, so the one-line answer to "what is
    # happening with this task" left out the only sentence anybody wrote about
    # why nothing is.
    if d["kind"] == "task" and d.get("why"):
        said = f"{d['stop_label'].lower()}: " + (
            _c.gist(d["why"], d.get("format")) or "")
        out.append("  " + _c.clip(said, COMPACT_WIDTH - 2))

    chain_ids = [p["id"] for p in d["chain"]]
    shaky = set(d["shaky_premises"])

    premise = d["premise"] if d["kind"] == "task" else None
    if premise:
        chain_ids = chain_ids + [premise["id"]]
        if _base(premise["status"]) in SHAKY:
            shaky.add(premise["id"])

    if chain_ids:
        out += [""] + _chain_lines(chain_ids, shaky, d["id"])
        out += _c.listing([_premise_row(p) for p in d["chain"]],
                          width=COMPACT_WIDTH, prose_aside=True)
    elif d["kind"] == "decision":
        out += ["", "CHAIN  a root — it rests on nothing"]

    # The premise is not one row among the others: it is the reason this work
    # exists, and clipping it into the listing's aside column is how it stopped
    # being visible. It gets its own two lines, as `text()` gives it its own
    # section.
    if premise:
        lead = f"BECAUSE  {premise['id']}  {_base(premise['status'])}  "
        out += ["", lead + _c.clip(premise["title"],
                                   COMPACT_WIDTH - len(lead))]
        # The premise carries the same caveat, and this is where it lands on
        # somebody who is about to act: they asked what this work rests on,
        # and the answer they are shown is one of two.
        if premise.get("rival_answers"):
            out += textwrap.wrap("!! " + premise["rival_answers"],
                                 COMPACT_WIDTH, initial_indent="         ",
                                 subsequent_indent="         ")
        # Not `_premise_row`: the premise arrives as a `decision()` dict, whose
        # answer lives under different keys and which carries no `shaky` flag.
        said = _c.gist(premise["answer"], premise.get("answer_format"))
        out.append("         " + _c.clip(
            said or ("not settled" if premise["id"] in shaky
                     else "no answer recorded"), COMPACT_WIDTH - 9))
    elif d["kind"] == "task" and d["because"]:
        out += ["", f"BECAUSE  {d['because']} — not in the decision store"]
    elif d["kind"] == "task":
        out += ["", "BECAUSE  no premise is recorded for this work"]

    if d["kind"] == "task" and d["evidence_for"]:
        out += ["", f"EVIDENCE FOR  {d['evidence_for']} — that decision is "
                    f"waiting on what this work finds"]

    if d["kind"] == "decision":
        out += _fold(_decision_work(d), prefix="WORK  ", blank=True)
        if d["superseded"]:
            out += ["", f"SUPERSEDED HERE ({len(d['superseded'])}) — "
                        f"`dg node {d['id']}` for what was replaced"]

    out += [""] + textwrap.wrap(f"→ {_reading(d)}", COMPACT_WIDTH,
                                subsequent_indent="  ")
    out.append(_c.plain_hint(f"dg context {d['id']} --full",
                             "each answer, its evidence and its falsifier"))
    return "\n".join(out) + "\n"


def _relations(d: dict) -> list[str]:
    """How this node sits among its neighbours, as short phrases."""
    if d["kind"] == "decision":
        return ([f"rests on {', '.join(d['depends_on'])}"] if d["depends_on"] else []) \
             + ([f"opens {', '.join(d['opens'])}"] if d["opens"] else [])
    bits = []
    for label, key in (("after", "prerequisites"), ("waiting on", "waiting_on"),
                       ("unblocks", "unblocks"),
                       ("found doing", "discovered_during"),
                       ("turned up", "prompted")):
        if d.get(key):
            bits.append(f"{label} {', '.join(d[key])}")
    if d.get("outcome"):
        bits.append("outcome recorded")
    return bits


def _decision_work(d: dict) -> list[str]:
    """The work either side of a decision, as short phrases. `*` is unfinished."""
    w = d.get("work") or {}
    bits = []
    if w.get("rests_on"):
        bits.append("because of this: " + ", ".join(w["rests_on"]))
    if w.get("evidence"):
        pend = set(w.get("pending_evidence") or [])
        bits.append("evidence for it: " + ", ".join(
            f"{t}*" if t in pend else t for t in w["evidence"])
            + (" (* outstanding)" if pend else ""))
    return bits


def _reading(d: dict) -> str:
    """The closing sentence: whether any of this still holds.

    A task's comes from `_verdict`, which `cross` gives the facts for. A
    decision has never had one — `text()` printed a line only when a premise
    was shaky — and silence there reads as "solid", which is the one reading
    that must never be guessed at.
    """
    if d.get("verdict"):
        return d["verdict"]
    if d["shaky_premises"]:
        return (f"this rests on {', '.join(d['shaky_premises'])}, which is not "
                f"settled — treat the conclusion as provisional")
    if _base(d["status"]) in SHAKY:
        return f"this is {_base(d['status'])}; nothing below it is in doubt"
    return "every premise under this is settled"
