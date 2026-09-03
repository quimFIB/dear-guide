"""Local web app for the decision graph.

Bound to 127.0.0.1 only. It calls the same `pending`, `render` and `editor`
modules as the CLI, so there is exactly one implementation of apply and one
implementation of the compose buffer.

`POST /api/compose` launches an editor on the user's desktop and blocks until it
exits — the `git commit` model, driven from the browser instead of the terminal.
Two consequences shape this module:

- **A route requires the token when the caller controls its effect or its
  cost.** Any page in the user's browser can reach a localhost server; it just
  cannot read the response cross-origin. That is tolerable for a route that
  hands back a fixed payload — asking twice costs what asking once did — and it
  is not tolerable for `/api/compose`, which starts a process, or for
  `/api/find`, which runs a caller-supplied program against the store.

  The line used to be drawn at GET versus POST, on the stated grounds that
  "the API only moves data around". `/api/find` made that false: it is a read,
  and its cost is chosen by whoever calls it. A regex of about twenty
  characters buys unbounded CPU (see `query._may_blow_up` for how far input
  validation gets, which is not far enough). GET/POST was always a proxy for
  effect-and-cost, so the guard now names the thing it was standing in for, and
  `GUARDED_READS` is the list of reads that qualify.

  The token is minted per run, embedded in the served page, and demanded back.
  Its secrecy is the smaller half of why this works: a `no-cors` request cannot
  carry a custom header at all, and a `cors` one carrying `X-DG-Token` triggers
  a preflight this server never answers. *Any* required custom header would
  close the cross-origin path; the token also closes the same-origin one.
- **One editor at a time.** There is a single buffer per project, the same
  property `COMMIT_EDITMSG` has, so a second request is refused rather than
  allowed to overwrite a buffer someone is typing in.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dgraph import areas
from dgraph import (applying, check, cross, editor, pending, project, ranges,
                    render, task_editor, task_pending)
from dgraph.model import Graph, rival_note
from dgraph import tasks as tasks_mod
from dgraph.tasks import TaskGraph, stop_label

STATIC = Path(__file__).resolve().parent / "static"

#: Minted per run: restarting `dg serve` invalidates any page left open, which
#: is the conservative direction to fail in.
TOKEN = secrets.token_urlsafe(24)
TOKEN_HEADER = "X-DG-Token"

#: How a caller that is *not* a person names itself. Absent on every request the
#: page makes, which is the point: the browser is a person's door and a person is
#: the supervisor, so what it stages is unowned.
#:
#: Without this the identity would be inherited, and inheriting is wrong here.
#: `dg serve --detach` is a `subprocess.Popen`, so an agent that set `$DG_AGENT`
#: and started the server would hand its own identity to every person who later
#: clicked in that browser — and that person's terminal `dg apply`, being
#: unowned, would then refuse to apply what they had just staged through it.
#: Doing nothing gets this wrong, which is why it is stated rather than assumed.
#: One definition, in the lower module: `pending` names it in the refusal it
#: raises when somebody stages as the reserved name, and a second literal
#: here would be free to drift from the header actually read.
AGENT_HEADER = pending.server_header()
TOKEN_MARK = "__DG_TOKEN__"

#: Reads that require the token anyway, because the caller chooses what they
#: cost. Exactly one so far: `/api/find` runs a caller-supplied regex over every
#: prose field of every record, and no cap on the query text bounds that —
#: `(.|.|.){20}ZZZZ` is eighteen characters and does not finish. Every other GET
#: here returns a fixed payload, where being made to answer twice costs what
#: answering once did.
#:
#: `/api/health` is deliberately *not* in this set: `probe()` calls it without a
#: token to find out whether a server is already running, which is the one thing
#: a stranger may usefully learn.
GUARDED_READS = frozenset({"/api/find"})

#: The only Host headers this loopback server answers. A DNS-rebound page — an
#: attacker's hostname pointed at 127.0.0.1 — connects to the same socket but
#: presents its own name here; without this check it is same-origin with the
#: server, reads the token off `/`, and can drive every mutating route. The
#: token guards against cross-origin pages; this guards the token.
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

#: Held for as long as an editor is open, which may be minutes.
_editing = threading.Lock()


def graph_payload(g: Graph) -> dict:
    d = g.to_dict()
    # The registry, not the declared list. `areas` accumulates and a record may
    # be filed under one the list does not mention, so the page's legend, its
    # colour assignment and its area field all want the union of declared and
    # used — otherwise a record renders in an unlisted colour the legend has no
    # row for, and the area field cannot offer an area that is plainly in use.
    d["areas"] = areas.registry(g.areas, g.vertices.values())
    # Three things this block wants for *every* vertex, each of which was being
    # recomputed from scratch per vertex. At 1,000 vertices the payload took
    # 139.8 s to build, 99.2 of them in `depth` and 42.2 in
    # `provisional_because`; both walk the graph, and both already have a form
    # that answers the whole store in one pass.
    into = g._reverse()
    by_src = g.by_src()
    depths = g.all_depths()
    because = g.provisional_causes()
    d["derived"] = {
        vid: {
            "depends": g.depends(vid, into),
            # The premises not settled yet — what the vertex *waits on*. Read
            # off the edges here, as everywhere: there is no stored blocked
            # status (`D68`), so this is how the page colours a waiting vertex
            # and names what it waits for.
            "waiting_on": g.waiting_on(vid, into),
            # Which premises are still under review. The panel needs it to say
            # whether a PROVISIONAL decision can be re-affirmed *yet* — and to
            # say which premise, rather than offering a button that refuses.
            #
            # Only PROVISIONAL vertices get a real answer now, where before
            # every vertex did. That is not a narrowing of what the page can
            # show: `app.html` reads this key inside `if(st==="PROVISIONAL")`
            # and nowhere else, so every other vertex's list was computed,
            # serialised and shipped to be ignored. The key stays present for
            # all of them so the payload's shape does not change.
            "provisional_because": because.get(vid, []),
            "depth": depths[vid],
            "children": g.children(vid, by_src),
            # The whole superseded edge, not a one-line epitaph for it. A
            # reversal is an edge with a payload of its own — its own targets,
            # falsifier and source — and the panel draws it as one so a reader
            # can tell which edge a sentence belongs to. Sending only the
            # summary is what made that unanswerable: the surviving answer
            # usually explains the reversal, and the explanation then reads as
            # though it were part of the record it replaced.
            "history": [
                {"summary": h.summary, "replaced_by": h.replaced_by,
                 "why": h.why, "format": h.format, "answer": h.answer,
                 "falsifier": h.falsifier, "source": h.source,
                 "date": h.date, "to": list(h.to)}
                for h in g.history(vid, by_src)
            ],
            # Kept apart from `history`, because they are different sentences
            # about this project: one says *we changed our mind*, the other
            # says *somebody else answered this and we did not take it*. Sent
            # as its own key so the page cannot merge them by accident.
            # Sent rather than computed in the page: `Graph.rival_answers` is
            # where the reading lives, and a page that worked it out from the
            # edge list would be a second implementation of an invariant.
            "rival_answers": (rival_note(len(rivals))
                              if (rivals := g.rival_answers(vid, by_src))
                              else None),
            "declined": [
                {"from_source": h.from_source, "answer": h.answer,
                 "falsifier": h.falsifier, "source": h.source,
                 "date": h.date, "to": list(h.to), "format": h.format}
                for h in g.rejected(vid, by_src)
            ],
        }
        for vid in g.vertices
    }
    d["frontier"] = g.frontier()
    # What the new-decision form prefills. Sent rather than computed in the
    # page, because `dg add --edit` prefills the same thing from the same
    # function and two doors offering different "next" ids is the disagreement
    # this codebase spends its comments preventing.
    #
    # **`next_offer`, not `next_id`.** This route sends the store — that is the
    # panel's whole reading model, the tray being its own footer — but an *id
    # to offer* is not a reading of the store: it is a claim about what
    # staging will accept, and staging vets against the tray. From the store
    # alone the form prefilled an id another writer had already staged, and
    # `stage` then refused the post it had just invited. Audit `G-F5`.
    d["next_id"], d["next_id_fault"] = _next("D", lambda: editor.next_offer(g))
    # The decision store's half of the same marker — an agent may have staged a
    # `close` on a question the panel still shows as OPEN and decidable.
    d["contested"] = contested(pending.load(), "vertex")
    return d


def _next(prefix: str, offer):
    """`(next id, why there is none)` — one of the two is always None.

    A used-up grant must not take the whole payload down with it. The page is
    still worth drawing: every reading it offers still holds, and only the one
    form that prefills an id cannot be filled. So the fault travels beside the
    id and the form says it, which is the same thing `dg add` does in a
    terminal — the difference between *this graph cannot be read* and *this
    clone cannot allocate* is the difference that decides what to do next.
    """
    try:
        return offer(), None
    except ranges.RangeError as exc:
        return None, str(exc)
    except pending.ApplyError as exc:
        # The second way the offer can fail, and it arrived with `next_offer`:
        # the id is read off the store *plus the tray*, so a tray that no
        # longer applies has no id to give. Reported like a used-up grant
        # rather than raised, for the reason above — every other reading on
        # this page still holds, and the form is the only thing that cannot be
        # filled.
        return None, f"the staged ops no longer apply cleanly: {exc}"


def task_depth(tg: TaskGraph) -> dict[str, int]:
    """Longest path from a task with no prerequisites, for the layered layout.

    Iterative for the reason `Graph.depth` is: a deep but legal graph must not
    crash the view that was about to draw it. A task inside a cycle — which
    `task_acyclic` reports and refuses to apply — is pinned at 0 rather than
    looped on, so a broken store still renders something to look at.
    """
    depth: dict[str, int] = {}
    for tid in sorted(tg.tasks):
        stack, seen = [(tid, False)], set()
        while stack:
            cur, done = stack.pop()
            if done:
                depth[cur] = max((depth.get(p, 0) + 1
                                  for p in tg.prerequisites(cur)), default=0)
                continue
            if cur in depth or cur in seen:
                continue
            seen.add(cur)
            stack.append((cur, True))
            for prereq in tg.prerequisites(cur):
                if prereq not in depth:
                    stack.append((prereq, False))
    return depth


def contested(tray: list[dict], key: str) -> dict[str, dict]:
    """Which records a staged op speaks for, by id — the panel's marker.

    **The panel reads the store and the tray keeps its own footer**, which is
    the decided reading model: a canvas holds a pan, a zoom and an open
    inspector, and folding staged ops into the graph moves all three under
    somebody mid-read. What that split does *not* buy is silence about a
    claim — a task reading `TODO · ready` while an agent holds a staged
    `set_status … DOING` sends the supervisor at work already taken, and the
    footer cannot say so because it lists ops by writer, never by which record
    each speaks for. This is that index, and nothing else: the readings stay
    the store's. Audit `G-F5`.

    `key` is `vertex` or `task` — the field the op names its subject with, and
    `id` for an `add_*`, which names its subject with that instead. First op
    wins per id, matching the tray's own order, and the writer travels with it
    (`by`, the field `stage_all` stamps) because *who* claimed it is most of
    what the marker is for — an unattributed "somebody staged something" tells
    a supervisor nothing they can act on.

    `ref` travels too, so the panel can point at the row in its own footer
    rather than making the reader find it.
    """
    out: dict[str, dict] = {}
    for op in tray or ():
        if not isinstance(op, dict):
            continue
        rid = op.get(key) or op.get("id")
        if not rid or rid in out:
            continue
        out[rid] = {"op": op.get("op"), "by": op.get("by") or None,
                    "to": op.get("status") or None, "ref": op.get("ref")}
    return out


def task_payload(tg: TaskGraph, g: Graph | None) -> dict:
    """The task graph as the browser needs it.

    Mirrors `graph_payload`: the store, plus a `derived` block per task holding
    what the view would otherwise recompute. Every cross-graph reading comes
    from `cross`, so this builder joins nothing itself — the rule
    `tests/test_cross.py` enforces and the reason `gated_by` is not simply
    `because`.
    """
    d = tg.to_dict()
    # `graph_payload`'s twin — the registry, not the declared list. See there.
    d["areas"] = areas.registry(tg.areas, tg.tasks.values())
    depth = task_depth(tg)
    d["derived"] = {
        tid: {
            "prerequisites": tg.prerequisites(tid),
            "unblocks": tg.unblocks(tid),
            "waiting_on": tg.waiting_on(tid),
            # The other edge kind. Kept as its own pair of lists and never
            # merged into the three above: provenance is not an ordering, and
            # the panel that prints them must not be able to imply it is.
            "discovered_during": tg.discovered_during(tid),
            "prompted": tg.prompted(tid),
            "depth": depth.get(tid, 0),
            # `ready` and `blocked` from the task store alone; `cross` then
            # narrows `ready` by the premise. Both are reported because the
            # panel must be able to say *why* something is not startable.
            "ready": tg.ready(tid),
            "blocked": tg.blocked(tid),
            # What to call the live stop. Sent rather than decided in the
            # browser: `tasks.STOP_LABEL` is the one table, and a panel
            # picking its own word is how the three renderers came to disagree.
            "stop_label": stop_label(tg.tasks[tid].status),
            "cross": (
                {"gated_by": None, "ready": tg.ready(tid), "premises": [],
                 "dangling": [], "gating": [], "evidence_for": None}
                if g is None else _task_cross(tg, g, tid)
            ),
        }
        for tid in tg.tasks
    }
    d["frontier"] = tg.frontier()
    d["counts"] = tg.counts()
    # Beside the readings, never inside them: every value above is still the
    # store's. See `contested`.
    d["contested"] = contested(pending.load(task_pending.path()), "task")
    # `graph_payload`'s twin: what the new-task form prefills, from the same
    # function `dg task add --edit` prefills it with.
    d["next_id"], d["next_id_fault"] = _next(
        "T", lambda: task_editor.next_offer(tg))
    return d


def _ids(body: dict, key: str) -> list[str]:
    """A comma-joined id list off a request body, or a refusal saying so.

    Every seam field arrives this way from both browser forms, and the type is
    checked rather than assumed: the unlink form used to post a boolean here,
    which reached `.strip()` and raised `AttributeError` past the handler's
    `except ApplyError` into a 500 (`V-F2`). A body that is the wrong shape is a
    bad request, and the one thing it must not do is look like a crash.
    """
    raw = body.get(key)
    if raw is None or raw is False or raw == "":
        return []
    if not isinstance(raw, str):
        raise pending.ApplyError(
            f"`{key}` must be a comma-separated list of decision ids, "
            f"not {type(raw).__name__}")
    return [x.strip() for x in raw.split(",") if x.strip()]


def _task_cross(tg: TaskGraph, g: Graph, tid: str) -> dict:
    """One task's link to the decision store, resolved for display.

    **Every premise, each resolved.** This used to send one key holding the id
    list under the name the page read as a single id, with a status and a title
    beside it describing only the first — so the joined view drew one edge of
    several, and an empty list, being truthy in JavaScript, drew a `because` row
    on every task that had no premise at all (`V-F1`).

    Resolved here rather than in the page for the reason `stop_label` is: the
    browser has no decision store to look a title up in, and a panel that
    assembled one from two payloads would be the second implementation of
    `cross.task_link`.
    """
    link = cross.task_link(tg, g, tid)
    return {
        "gated_by": link["gated_by"],
        "ready": link["ready"],
        # Ids that resolve, with what a reader needs to judge them, in the
        # order the task names them.
        "premises": [{"id": did,
                      "status": g.vertices[did].status,
                      "title": g.vertices[did].title}
                     for did in link["premises"]],
        # Ids that resolve to nothing. Named rather than counted: a task on
        # three premises with one missing needs to show *which*.
        "dangling": list(link["dangling"]),
        # The subset holding the work back, so the page can mark those edges
        # without deriving settledness from a status string of its own.
        "gating": list(link["gating"]),
        "evidence_for": link["evidence_for"],
    }


def joined_payload(g: Graph | None, tg: TaskGraph | None) -> dict:
    """What the joined view draws: which tasks hang off which decision.

    The one reading no single-store view can produce, and the reason
    `dgraph/cross.py` exists. Empty on either side when that store is absent.
    """
    if g is None or tg is None:
        return {"by_decision": {}}
    # All four of these read the whole task store, or the whole cross-graph
    # walk, to answer about one decision -- and this comprehension asks them
    # for every decision. Compute each once for the whole graph instead.
    rev = cross.reverse(tg)
    late = cross.late_evidence_all(tg, g)
    return {
        "by_decision": {
            vid: {"rests_on": cross.rests_on(tg, vid, rev),
                  "evidence": cross.evidence(tg, vid, rev),
                  "pending_evidence": cross.pending_evidence(tg, vid, rev),
                  # Results that landed *after* this answer and have never been
                  # read against it. `dg check` and `dg brief` have reported
                  # this since the finding existed; the browser showed only its
                  # benign opposite (evidence still outstanding), so a store
                  # holding an answer and a result that contradicts it read
                  # clean here.
                  "late_evidence": late.get(vid, [])}
            for vid in g.vertices
        }
    }


def context_payload(vid: str) -> dict:
    """The chain of premises behind one decision. `dg context`.

    The panel printed `depends on` and stopped at one hop, which is the
    neighbourhood rather than the reasoning — and the reading `dg context`
    exists to give, *is anything in this chain still under review*, was
    precisely what it could not say. Computed by `context.chain`, the function
    the CLI uses, because a browser deriving its own premise chain is the drift
    this tool exists to make into a test failure.
    """
    from dgraph import context as _ctx
    g = Graph.load()
    if vid not in g.vertices:
        return {"error": f"unknown decision {vid}"}
    rows = [
        {"id": p.id, "title": p.title, "status": p.status, "depth": p.depth,
         "answer": p.answer, "falsifier": p.falsifier, "source": p.source,
         "shaky": p.shaky, "also_opened": p.also_opened}
        for p in _ctx.chain(g, vid)
    ]
    return {"id": vid, "chain": rows,
            # The reading, not just the rows: a chain with an unsettled link
            # makes everything below it a bet rather than a conclusion.
            "shaky": [r["id"] for r in rows if r["shaky"]]}


def path_payload(a: str, b: str) -> dict:
    """The chain of evidence between two decisions. `dg path`.

    Clicking a node highlights its neighbourhood, which is not the same
    question: *how does this rest on that* has one answer and it is a path.
    """
    g = Graph.load()
    for vid in (a, b):
        if vid not in g.vertices:
            return {"error": f"unknown decision {vid}"}
    ids = g.path(a, b)
    if not ids:
        return {"from": a, "to": b, "path": [],
                "error": f"no decision path from {a} to {b}"}
    out = []
    for i, vid in enumerate(ids):
        e = g.active_edge(vid)
        out.append({
            "id": vid, "title": g.vertices[vid].title,
            "status": g.vertices[vid].status,
            # The step, not the whole answer: what carried the reasoning from
            # this node to the next one.
            "because": ((e.answer or "").strip().split("\n")[0]
                        if e and i < len(ids) - 1 else None),
        })
    return {"from": a, "to": b, "path": out}


def areas_payload() -> dict:
    """Counts by area and status, one block per store. `dg areas`.

    Two blocks rather than one, for the reason the command gives two tables:
    the stores share their areas and not their vocabularies, and a row summing
    `OPEN` with `TODO` would be counting questions and work as if they were the
    same thing.

    **Both blocks list every area either store knows**, which is what "share
    their areas" has always claimed and did not used to mean: the two `areas`
    lists were independent fields written only by `init`. They are registries
    now, appended to by the op that first files a record under one, and every
    reader takes the union — so an area used only for work still has a row on
    the decision side, holding zero. That zero is a real answer.
    """
    g, tg = _stores()
    every = _area_union(g, tg)
    return {
        "decisions": ({"areas": every, "counts": _by_area_status(
            {v.id: (v.area, v.status.split(":")[0]) for v in g.vertices.values()})}
            if g is not None else None),
        "tasks": ({"areas": every, "counts": _by_area_status(
            {t.id: (t.area, t.status) for t in tg.tasks.values()})}
            if tg is not None else None),
    }


def _area_union(g, tg) -> list[str]:
    """`cli._area_union`'s twin — every area either store declares or uses, the
    decision store's registry first. A project with only one store reads a
    union of one, which is the case this endpoint has always been careful to
    allow."""
    out = list(areas.registry(g.areas, g.vertices.values())) if g else []
    for a in (areas.registry(tg.areas, tg.tasks.values()) if tg else []):
        if a not in out:
            out.append(a)
    return out


def _by_area_status(rows: dict) -> dict:
    """`{area: {status: n}}`, from `{id: (area, status)}`."""
    out: dict[str, dict[str, int]] = {}
    for area, status in rows.values():
        out.setdefault(area, {})
        out[area][status] = out[area].get(status, 0) + 1
    return out


def check_payload() -> dict:
    """Every invariant, over the store **and the trays**.

    The browser had no way to learn a store was unsound. An invalid one — the
    merge, rebase, partial checkout or second clone `pending.repairs` exists
    for — looked entirely normal until an unrelated `Apply` was refused for a
    reason that had nothing to do with what was staged, with a CLI command as
    the remedy.

    Two lists, and they answer different questions. `stored` is what
    `dg check` says about the record as it sits. `staged` is what the batch
    would leave behind if it applied — which is what a person needs *before*
    staging more, and what `dg apply` will judge. The findings are printed
    verbatim, remedy strings included: a second wording here is the two doors
    disagreeing about one store.

    **`staged` is empty when the tray is**, rather than a second copy of
    `stored`. With nothing staged the two lists are identical by construction,
    and showing both would make an unsound store look twice as unsound —
    teaching the eye to skip a section that matters exactly when the tray is
    not empty.

    `repairs` is the ops that would clear every propagation finding. It is the
    one honesty command that maps to a single button, because `dg repair`
    stages that list and nothing else.
    """
    proj = project.find()
    stored = [_finding(v) for v in check.run(proj)]
    staged: list[dict] = []
    if proj.has_decisions and pending.load():
        eff = pending.preview(Graph.load())
        staged += [_finding(v) for v in eff.validate()]
    if proj.has_tasks and pending.load(task_pending.path()):
        teff = task_pending.preview(TaskGraph.load(proj.tasks))
        staged += [_finding(v) for v in teff.validate()]
    fixable = (len(pending.repairs(pending.preview(Graph.load())))
               if proj.has_decisions else 0)
    return {"stored": stored, "staged": staged, "repairs": fixable}


def _finding(v) -> dict:
    """One violation, as the page shows it. Nothing is reworded here."""
    return {"check": v.check, "message": v.message, "severity": v.severity,
            "origin": v.origin}


def health() -> dict:
    """Proof that the thing on this port is *this* tool, serving *this* project.

    `--status` and `--stop` need it. A pidfile alone cannot tell a live server
    from a recycled pid, and a `--stop` that kills whatever inherited the
    number is not acceptable. So the check is a request to the port, and only
    a reply naming this tool and this project counts as ours.
    """
    import dgraph
    return {"tool": dgraph.TOOL, "version": dgraph.version(),
            "pid": os.getpid(), "project": str(project.find().root)}


def editor_payload() -> dict:
    """What the browser needs to decide whether to offer the editor at all.

    Offering a button that cannot work is worse than not offering it: the
    failure would arrive as a hung request.
    """
    ed = editor.resolve_gui_editor()
    return {
        "editor": ed,
        "emacs": editor.is_emacs(ed),
        "available": editor.gui_available(),
        "buffer": str(project.find().edit),
    }


def stage(g: Graph, op: dict) -> list[dict]:
    """Stage an op and everything it implies. The one staging path.

    Expansion is derived from the store *plus* the already-staged ops, exactly
    as the CLI does it: a reopen must mark descendants whose close is staged
    but not applied, or the batch fails `apply` wholesale. The op is vetted
    first, matching the CLI's stage-time guards — an unknown op kind, a close
    for a decided vertex, or a dangling target is refused here rather than
    staged as a batch-poisoning op `apply` rejects later.
    """
    eff = pending.preview(g)
    # The same policy the CLI applies, at the same moment. The page itself
    # stages as nobody and is never refused; this bites for a caller driving the
    # API with an `X-DG-Agent` header, which is the other way an agent reaches
    # the graph and would otherwise be the way around the rule.
    if op.get("op") == "close":
        proj = project.find()
        tg = (TaskGraph.load(proj.tasks) if proj.has_tasks else None)
        why = cross.refuse_close(tg, op.get("vertex"), pending.owner())
        if why is not None:
            raise pending.ApplyError(why)
    fresh = bool(op.pop("new_area", False))
    pending.vet(eff, op, new_area=fresh)
    ops = pending.expand(eff, op)
    # The area guard lives in `stage_all` now, so the page's `new_area` has to
    # reach it — scoped to this request, never written into the op. `expand`
    # sits between the two and knows nothing about either, which is the reason
    # the permission is carried rather than passed. Audit `R-F2`.
    with pending.new_area_allowed(fresh):
        pending.stage_all(ops, against=eff)   # one write, each op stamped
    return ops


def fallout_payload(tid: str) -> dict:
    """What dropping `tid` leaves standing, as rows the panel can render.

    One row per affected task, each carrying the title and the reason it is
    affected, because a verdict asked about a bare id is a verdict nobody can
    give. The same `tasks.fallout` the CLI refuses on, so the two doors cannot
    disagree about who is affected.

    A fault is data rather than a 500, matching `/api/find`: an unknown id in
    a URL the page built is a bug worth showing, not a server error.
    """
    proj = project.find()
    if not proj.has_tasks:
        return {"error": "this project has no tasks.json"}
    tg = task_pending.preview(TaskGraph.load(proj.tasks))
    if tid not in tg.tasks:
        return {"error": f"unknown task {tid}"}
    out = tasks_mod.fallout(tg, tid)
    return {"task": tid, "fallout": [
        {"id": t, "title": tg.tasks[t].title, "status": tg.tasks[t].status,
         "why": why}
        for t, why in sorted(out.items())]}


def _task_note(tg: TaskGraph, op: dict) -> str | None:
    """The start-time warning for a task op, or None for anything else.

    The same helper `dg task start` prints from, for the same reason
    `_decide_note` exists: both doors onto starting work have to say the same
    thing about a prerequisite that was abandoned rather than finished.

    Read against the *pre-op* graph, tray included, which is where the
    prerequisite's status lives — the op being staged only moves `tid` itself.
    """
    if op.get("op") != "set_status" or op.get("status") != "DOING":
        return None
    tid = op.get("task")
    return (tasks_mod.starting_on_abandoned_work(tg, tid)
            if tid in tg.tasks else None)


def _decide_note(op: dict) -> str | None:
    """The decide-time warning for a close op, or None for anything else."""
    if op.get("op") != "close" or not op.get("vertex"):
        return None
    proj = project.find()
    tg = TaskGraph.load(proj.tasks) if proj.has_tasks else None
    return cross.deciding_ahead_of_evidence(tg, op["vertex"])


def stage_tasks(tg: TaskGraph, ops: list[dict], *,
                new_area: bool = False) -> list[dict]:
    """Vet a batch of task ops and stage them as **one** tray write.

    A drop and the cascade its verdicts imply are one judgement — the operator
    was asked about each affected task and answered — so a tray holding only
    the first half says the opposite of what they answered. `dg task drop`
    stages them together through `_tstage_all`; this is the same guarantee for
    the browser, which used to post the drop alone and nothing else.

    `vet_all` checks the whole group against the graph each op before it
    produces, before any of it is staged, so a refusal leaves the tray as it
    was rather than holding the first half of something that was given up on.
    """
    # The page's answer to "is this new area deliberate?" travels beside the op
    # and comes off it here, so it never reaches the tray — `apply` never
    # rechecks an area, and a permission on a staged record is one writer's
    # answer applied by another. Stripped in *this* function rather than in the
    # route, for the reason the guard it feeds is in `stage_all`: this is what
    # every task-staging caller goes through, and a strip a second door did not
    # run is not a strip.
    #
    # A loop, because the pop is the point and `any(...)` over a generator
    # short-circuits — the first truthy op was cleaned and every op behind it
    # kept the field. Audit `R-F2`.
    for op in ops:
        if isinstance(op, dict) and op.pop("new_area", False):
            new_area = True
    task_pending.vet_all(task_pending.preview(tg), ops, new_area=new_area)
    # `stage`'s twin — see there. Audit `R-F2`.
    with pending.new_area_allowed(new_area):
        pending.stage_all(ops, task_pending.path())
    return ops


def stage_task(tg: TaskGraph, op: dict, *, new_area: bool = False) -> list[dict]:
    """One task op, staged. The singular of `stage_tasks`, which is the path.

    Kept because most posts are one op, and delegating rather than duplicating
    means the vetting cannot differ between the two. There is a `vet` here and
    no `expand`, for the reason the task store documents: nothing about a task
    propagates. Reopening a decision marks its decided descendants
    `PROVISIONAL`; finishing a task changes nothing but the task, because
    blocked is derived and never stored.
    """
    return stage_tasks(tg, [op], new_area=new_area)


def find_payload(q: str, hops: int | None = None, *,
                 subgraph: bool = False) -> dict:
    """One query, answered for the browser exactly as the CLI answers it.

    With `subgraph`, the matches are a *seed* and the answer carries the slice
    they induce — the focus chip's question, run through `cross.induced`, which
    is the same call `dg find --subgraph` makes. The page could close over
    `derived` itself and save the round trip; it must not, because then "what
    is connected to D04" would have two implementations that agree until the
    day they do not.

    The same `cross.lenses` and the same `dgraph.query`, so a query typed into
    the page and the same query typed at a shell cannot disagree. The page
    needs *ids*, not rows — it already holds every record and only wants to
    know which to draw — so this returns id lists and the match evidence, and
    leaves rendering where rendering belongs.

    A fault is reported as data with a column rather than as a 500: a mistyped
    query is an ordinary thing to do in a search box, and the box wants to
    underline it, not show an error page.
    """
    from dgraph import query as _q

    lenses = cross.lenses(*_stores())
    if not q.strip():
        return {"query": "", "matched": None}   # null = "no filter", not "none"
    try:
        parsed = _q.parse(q)
        _q.vet(parsed, lenses)
        wanted = _q.scope(parsed, lenses)
    except _q.Fault as exc:
        return {"query": q, "fault": exc.reason, "column": exc.column}
    out: dict = {"query": str(parsed), "matched": {}, "scope": [
        l.kind for l in wanted]}
    for l in wanted:
        hits = _q.select(parsed, l)
        out["matched"][l.kind] = hits
        out.setdefault("why", {}).update({
            rid: [{"field": m.field, "snippet": m.text[:160]}
                  for m in _q.explain(parsed, l, rid)] for rid in hits})
    if subgraph:
        cut = cross.induced(*_stores(),
                            [r for rows in out["matched"].values() for r in rows],
                            depth=hops)
        # `bridged` travels with the ids because only this side can compute it:
        # it is a fact about the *path* the walk took, and the page sees only
        # the destination. Without it a decisions tab drawing a task-reached
        # decision has a node it cannot explain.
        out["subgraph"] = {"decisions": cut.decisions, "tasks": cut.tasks,
                           "bridged": cut.bridged}
    # A store the query is not about is absent from `matched`, which the page
    # reads as "this tab is not filtered" — different from an empty list, which
    # means "filtered, and nothing here matches".
    return out


def _clear(path, agent: str | None = None) -> int:
    """Empty one tray, and say how many ops went. `dg clear`, `dg task clear`.

    With `agent`, one writer's ops and nobody else's — `dg clear --agent`, and
    the browser's reject button for a tray several agents stage into. Counted
    inside `clear_agent` rather than here, because the narrowed clear re-reads
    the tray under its own lock and a count taken outside it would be of a tray
    that no longer existed by the time it was reported.
    """
    if agent is not None:
        return pending.clear_agent(agent, path)
    n = len(pending.load(path))
    pending.clear(path)
    return n


def _today() -> str:
    """The local date, as `dg confirm --against` files it.

    Local rather than UTC for the reason the page's `today()` is: near midnight
    a UTC date disagrees with the one the CLI would write for the same act.
    """
    from datetime import date
    return date.today().isoformat()


def _released_note(tg: TaskGraph, g: Graph | None, ops: list[dict]) -> list[str]:
    """What a removal set loose, as lines. `task_pending.releases`, worded once."""
    return [f"{t} becomes startable" for t in task_pending.releases(tg, g, ops)]


#: The four files a reading of this project is computed from: both stores and
#: both trays. Named here rather than derived from `project.IGNORE`, which is
#: about what git must not see and holds locks and temp files that change
#: without any reading changing with them.
#:
#: The **trays are in it and that is the point**. A supervisor watching a
#: fan-out sees nothing move in the stores under a confinement floor or under
#: `$DG_APPLY=never` — an agent there cannot apply at all — so a token that
#: watched only the stores would report a busy run as a quiet one.
WATCHED = ("store", "pending", "tasks", "task_pending")


def stat_payload() -> dict:
    """A cheap token that moves when any reading of this project would.

    **It answers one question and draws nothing.** The page polls this and uses
    it only to say *there is something to refresh* — the redraw is the reader's
    click, because the canvas holds a pan, a zoom and an open inspector that a
    poll must not move under them. That is the same argument that keeps the
    graph routes reading the store while the tray keeps its own panel.

    `(mtime_ns, size)` per file rather than a hash of the contents: this is
    polled every few seconds for as long as a browser is open, and the question
    is *has anything changed*, which `stat` answers without reading a byte. A
    file that is absent contributes `None` and is therefore distinguishable
    from an empty one — `dg task init` in another terminal moves the token.

    The staged counts travel beside it so the badge can say *how many* without
    a second request. They are the one thing a reader wants before deciding
    whether the refresh is worth taking.
    """
    proj = project.find()
    token = {}
    for name in WATCHED:
        p = getattr(proj, name)
        try:
            st = p.stat()
            token[name] = [st.st_mtime_ns, st.st_size]
        except OSError:
            token[name] = None
    staged = 0
    for tray in (None, task_pending.path()):
        try:
            staged += len(pending.load(tray) if tray is not None
                          else pending.load())
        except Exception:
            pass
    return {"token": token, "staged": staged}


def _stores() -> tuple[Graph | None, TaskGraph | None]:
    """Both stores, each `None` when the project does not have it.

    A project with only decisions and a project with only tasks are both
    ordinary, and the browser must render whichever half exists rather than
    500 on the half that does not.
    """
    proj = project.find()
    g = Graph.load(proj.store) if proj.has_decisions else None
    tg = TaskGraph.load(proj.tasks) if proj.has_tasks else None
    return g, tg


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    # -- helpers --
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    def _staging_as(self):
        """Who this request stages as: nobody, unless it says otherwise.

        An agent driving this API passes `X-DG-Agent`; the page never does. See
        `AGENT_HEADER`, and `pending.as_owner` for why the environment is not
        consulted here.
        """
        return pending.as_owner((self.headers.get(AGENT_HEADER) or "").strip()
                                or None)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _authed(self) -> bool:
        """Reject anything that did not come from the page this server served."""
        if secrets.compare_digest(self.headers.get(TOKEN_HEADER, ""), TOKEN):
            return True
        self._json({"error": "stale page — reload to pick up this run's token"}, 403)
        return False

    def _host_ok(self) -> bool:
        """Reject requests addressed to any name but this server's own.

        See ALLOWED_HOSTS: applied to every route, reads included, because the
        page at `/` is where the token lives.
        """
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip().lower()
        if host in ALLOWED_HOSTS:
            return True
        self._json({"error": "unrecognised Host header"}, 403)
        return False

    def _page(self) -> bytes:
        html = (STATIC / "app.html").read_text(encoding="utf-8")
        return html.replace(TOKEN_MARK, TOKEN).encode()

    # -- routes --
    #: Each do_* checks the Host, dispatches, and turns an unexpected exception
    #: — an unloadable store, chiefly — into a JSON 500 instead of a dropped
    #: connection and a traceback on the server's terminal.

    def do_GET(self) -> None:
        if not self._host_ok():
            return
        if urlparse(self.path).path in GUARDED_READS and not self._authed():
            return
        try:
            if self.path in ("/", "/index.html"):
                self._send(200, self._page(), "text/html; charset=utf-8")
            elif self.path == "/api/graph":
                self._json(graph_payload(Graph.load()))
            elif self.path == "/api/stat":
                # Deliberately not in `GUARDED_READS`: it reads no content and
                # runs no caller-supplied work, so it is the one route whose
                # cost a caller cannot choose. The token still travels, because
                # `api()` sends it on every request.
                self._json(stat_payload())
            elif self.path == "/api/pending":
                self._json(pending.load())
            elif self.path == "/api/tasks":
                g, tg = _stores()
                # `null`, not a 500 and not an empty graph: "this project does
                # not track work" and "this project has no work outstanding"
                # are different facts and the tab must say which.
                self._json(task_payload(tg, g) if tg is not None else None)
            elif self.path == "/api/joined":
                self._json(joined_payload(*_stores()))
            elif self.path == "/api/task-pending":
                self._json(pending.load(task_pending.path()))
            elif urlparse(self.path).path == "/api/task-fallout":
                # A **read**, so the panel can ask before it posts. The CLI
                # refuses a drop until every released and orphaned task has a
                # verdict; the button could not offer that question because it
                # could not see the answer. See `tasks.fallout`.
                self._json(fallout_payload(parse_qs(
                    urlparse(self.path).query).get("task", [""])[0]))
            elif urlparse(self.path).path == "/api/find":
                # The path alone, not a prefix: `startswith` answered
                # `/api/findXYZ` as a find, which is the only route here that
                # did not have to be spelled correctly.
                # `keep_blank_values`, and it is load-bearing: `parse_qs`
                # drops an empty value, which made `subgraph=` — the page's
                # spelling of *the whole cone* — arrive identical to no
                # `subgraph` at all, and the chip's `*` drew an unfiltered
                # graph. The two other readings here are unaffected; only this
                # route has a parameter whose empty form means something.
                fq = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                # Absent and empty mean different things: no `subgraph` at all
                # is the plain filter, `subgraph=` with no number is the whole
                # cone, and a number bounds it. A junk number is the caller's
                # mistake and travels as a fault, not a 500 — same bargain the
                # query box already has with a mistyped term.
                raw = fq.get("subgraph", [None])[0]
                try:
                    hops = int(raw) if raw not in (None, "") else None
                except ValueError:
                    self._json({"query": fq.get("q", [""])[0],
                                "fault": f"subgraph takes a hop count, "
                                         f"not `{raw}`"})
                    return
                try:
                    self._json(find_payload(fq.get("q", [""])[0], hops,
                                            subgraph=raw is not None))
                except ValueError as exc:
                    # A negative count. `cross.induced` refuses it, so this
                    # door and `dg find --hops` cannot disagree about the bound.
                    self._json({"query": fq.get("q", [""])[0],
                                "fault": f"subgraph {exc}"})
            elif urlparse(self.path).path == "/api/context":
                self._json(context_payload(parse_qs(
                    urlparse(self.path).query).get("id", [""])[0]))
            elif urlparse(self.path).path == "/api/path":
                q = parse_qs(urlparse(self.path).query)
                self._json(path_payload(q.get("from", [""])[0],
                                        q.get("to", [""])[0]))
            elif self.path == "/api/areas":
                self._json(areas_payload())
            elif self.path == "/api/check":
                self._json(check_payload())
            elif self.path == "/api/health":
                self._json(health())
            elif self.path == "/api/editor":
                self._json(editor_payload())
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        with self._staging_as():
            if not self._host_ok():
                return
            if not self._authed():
                return
            try:
                self._post()
            except Exception as exc:
                self._json({"error": str(exc)}, 500)

    def _post(self) -> None:
        if self.path == "/api/pending":
            g = Graph.load()
            op = self._body()
            try:
                ops = stage(g, op)
            except Exception as exc:
                return self._json({"error": str(exc)}, 400)
            # The same warning `dg decide` prints, from the same helper: both
            # doors onto settling a question have to say the same thing about
            # deciding ahead of the evidence.
            self._json({"staged": ops, "pending": pending.load(),
                        "notes": [n for n in [_decide_note(op)] if n]})
        elif self.path == "/api/task-pending":
            proj = project.find()
            if not proj.has_tasks:
                return self._json({"error": "this project has no tasks.json — "
                                            "`dg task init` starts one"}, 400)
            body = self._body()
            # A list is a group that has to land together — a drop and the
            # cascade its verdicts imply. One tray write, as `_tstage_all`
            # gives the CLI; half a cascade in the tray says the opposite of
            # what the operator answered.
            ops_in = body if isinstance(body, list) else [body]
            tg = TaskGraph.load(proj.tasks)
            # Read against the tray, and read *before* staging, which is what
            # `dg task start` does through `_teff`. A prerequisite dropped but
            # not yet applied has to be visible to both doors or to neither.
            eff = task_pending.preview(tg)
            try:
                ops = stage_tasks(tg, ops_in)
            except Exception as exc:
                return self._json({"error": str(exc)}, 400)
            # Same shape as `/api/pending` above: notes are staged-anyway
            # warnings the panel shows verbatim, never a refusal.
            self._json({"staged": ops,
                        "pending": pending.load(task_pending.path()),
                        "notes": [n for n in (_task_note(eff, o) for o in ops_in)
                                  if n]})
        elif self.path == "/api/edit":
            self._edit()
        elif self.path == "/api/repair":
            self._repair()
        elif self.path == "/api/confirm":
            self._confirm()
        elif self.path == "/api/read-evidence":
            self._read_evidence()
        elif self.path == "/api/dep":
            self._relate(decisions=True)
        elif self.path == "/api/task-dep":
            self._relate(decisions=False)
        elif self.path == "/api/fallout":
            self._fallout()
        elif self.path == "/api/add":
            self._add()
        elif self.path == "/api/add-task":
            self._add_task()
        elif self.path == "/api/compose":
            self._compose()
        elif self.path == "/api/expand":
            # preview propagation without staging
            g = Graph.load()
            try:
                self._json({"ops": pending.expand(pending.preview(g), self._body())})
            except Exception as exc:
                self._json({"error": str(exc)}, 400)
        elif self.path == "/api/apply":
            body = self._body()
            self._apply(body.get("agent") or None,
                        group=body.get("group") or None)
        else:
            self._json({"error": "not found"}, 404)

    def _apply(self, agent: str | None = None,
               group: str | None = None) -> None:
        """Apply both trays, exactly as `dg apply` does.

        Two independent batches: a task batch that will not apply must never
        stop a decision batch that would, so each is reported on its own and a
        refusal names which one. The write sequence itself is
        `dgraph.applying` — the same function the CLI calls, so the two hosts
        cannot drift on the order that makes it safe.

        `agent` is `dg apply --agent`: write what one writer staged and leave
        the rest. It is the accept half of a review the page can already do the
        reject half of, and a screen that can turn one proposal down but can
        only take **all** of them is one that pushes its user to `--all` and
        then to a reversal. `pending.discard` removes applied ops by value, so
        a narrowed batch leaves everybody else's staged with no extra care
        taken here.
        """
        proj = project.find()
        # Held across the read, exactly as `dg apply` does and for the same
        # reason: these two loads used to sit outside every lock, so a
        # `DELETE /api/pending/<ref>` — or a `dg drop` in the terminal the user
        # was told to keep open beside this — could unstage an op, report which
        # one, and watch it land. Audit W-F2. The span reaches `discard`, which
        # is inside `applying.apply_*`, so it wraps the whole loop below.
        with applying.trays(proj):
            return self._apply_held(proj, agent, group)

    def _apply_held(self, proj, agent: str | None = None,
                    group: str | None = None) -> None:
        """The body of `_apply`, with both trays already held."""
        ops = pending.load(proj.pending) if proj.has_decisions else []
        task_ops = (pending.load(task_pending.path())
                    if proj.has_tasks else [])
        if not ops and not task_ops:
            return self._json({"error": "nothing staged"}, 400)
        # The same rule the CLI takes, and before the same narrowing. A policy
        # that held at one door and not the other is the drift every shared
        # helper in `pending` exists to stop -- and the browser is the door an
        # agent reaches when the panel is open in front of it.
        blocked = pending.refuse_apply(len(ops) + len(task_ops))
        if blocked is not None:
            return self._json({"error": blocked}, 400)
        if agent is not None:
            # Judged against the **whole** tray, before narrowing: the roster a
            # refusal names has to be the one the reader is choosing from, and
            # one built from the selection could only ever contain the name
            # that was just asked for.
            why = pending.refuse_apply_for(agent, ops, task_ops)
            if why is not None:
                return self._json({"error": why}, 400)
            ops, _ = pending.mine(ops, pending.addressed(agent))
            task_ops, _ = pending.mine(task_ops, pending.addressed(agent))

        if group is not None:
            # `dg apply --group`'s twin: one **act**, which is the finest grain
            # there is now that the tray records which ops were staged
            # together. The panel could turn one proposal down per op and take
            # them only per writer; this is the accept half at the same grain
            # as the reject half. `G-F11`.
            found = next((o for o in list(ops) + list(task_ops)
                          if o.get("ref") == group), None)
            if found is None:
                return self._json(
                    {"error": f"nothing staged with id {group}"}, 400)
            keep = {o.get("ref") for o in
                    pending.group_of(list(ops) + list(task_ops), found)}
            ops = [o for o in ops if o.get("ref") in keep]
            task_ops = [o for o in task_ops if o.get("ref") in keep]

        out: dict = {"applied": 0, "applied_tasks": 0, "errors": [],
                     # What moved under the batch while it sat in the tray.
                     # Rendered as prose by `pending.describe`, so the browser
                     # and the terminal say the same thing about it.
                     "drift": []}
        graph = None
        # Both batches inside one lock span, exactly as `dg apply` does: the two
        # remain independent in whether they are refused, and the pair on disk
        # is never observable half-applied. Audit F27, and the reason this loop
        # and the CLI's must keep agreeing — see `dgraph/applying.py`.
        with applying.writing(proj):
            for label, batch, run in (
                ("decision", ops, applying.apply_decisions),
                ("task", task_ops, applying.apply_tasks),
            ):
                if not batch:
                    continue
                try:
                    r = run(batch)
                except pending.ApplyError as exc:
                    out["errors"].append(f"{label} batch refused: {exc}")
                    continue
                key = "applied" if label == "decision" else "applied_tasks"
                out[key] = r.applied
                out["drift"] += [pending.describe(d) for d in r.drift]
                if label == "decision":
                    graph = r.graph
        if graph is not None:
            out["graph"] = graph_payload(graph)
        if not out["errors"]:
            return self._json(out)
        # `error` is the one-string form every existing caller reads; `errors`
        # keeps them apart, since two independent batches can fail for two
        # unrelated reasons and a client showing one of them would mislead.
        out["error"] = "\n".join(out["errors"])
        # 400 when nothing was written — a refusal, and the ops are still
        # staged. 500 once some store *has* landed (one batch applied, another
        # refused): the caller must not read that as "nothing happened".
        wrote = out["applied"] or out["applied_tasks"]
        self._json(out, 500 if wrote else 400)

    def _add(self) -> None:
        """Open a question from the browser. `dg add`'s twin.

        Every rule and the op list itself are `pending.compose_add`, which the
        CLI calls too — so the edges that attach the vertex cannot be present
        at one door and missing at the other. This method is transport: read the body, hand it over, stage
        the group as one write.
        """
        g = Graph.load()
        eff = pending.preview(g)
        body = self._body()
        try:
            ops = pending.compose_add(
                eff, vid=(body.get("id") or "").strip(),
                title=(body.get("title") or "").strip(),
                area=(body.get("area") or "").strip(),
                status=(body.get("status") or "OPEN").strip(),
                after=[x for x in (body.get("after") or []) if x],
                note=(body.get("note") or "").strip() or None,
                stored=g)
            pending.vet_all(eff, ops)
            pending.stage_all(ops, against=eff)   # one write, each op stamped
        except pending.ApplyError as exc:
            return self._json({"error": str(exc)}, 400)
        # `ops`, not the tray: every other route reports what *this* act
        # staged, and the page counts it.
        self._json({"staged": ops, "pending": pending.load(), "notes": []})

    def _add_task(self) -> None:
        """Record a piece of work from the browser. `dg task add`'s twin.

        The same shape as `_add`, over the other store and its own composer.
        The one difference worth naming: a task may be born already pointing at
        a decision, so the decision store is read here — as its *effective*
        graph, the tray included, so that a question staged a minute ago is a
        legal `because`. That is the A3 lesson, and `dg task add` resolves the
        same field the same way.
        """
        proj = project.find()
        if not proj.has_tasks:
            return self._json({"error": "this project has no tasks.json — "
                                        "`dg task init` starts one"}, 400)
        g = pending.preview(Graph.load()) if proj.has_decisions else None
        tg = TaskGraph.load(proj.tasks)
        eff = task_pending.preview(tg)
        body = self._body()
        fresh = bool(body.get("new_area"))
        try:
            ops = task_pending.compose_add(
                eff, g, tid=(body.get("id") or "").strip(),
                title=(body.get("title") or "").strip(),
                area=(body.get("area") or "").strip(),
                new_area=fresh,
                after=[x for x in (body.get("after") or []) if x],
                discovered_during=[x for x in
                                   (body.get("discovered_during") or []) if x],
                because=_ids(body, "because"),
                evidence_for=(body.get("evidence_for") or "").strip() or None,
                note=(body.get("note") or "").strip() or None,
                stored=tg)
            staged = stage_tasks(eff, ops, new_area=fresh)
        except Exception as exc:
            return self._json({"error": str(exc)}, 400)
        # No notes: `dg task add` prints none either. The staged-anyway warnings
        # this app shows belong to acts that make a claim about *state* —
        # starting abandoned work, deciding ahead of evidence — and recording
        # that a question exists makes none.
        self._json({"staged": staged,
                    "pending": pending.load(task_pending.path()),
                    "notes": []})

    def _edit(self) -> None:
        """Revise a staged op in place. `dg edit N`'s twin.

        **Replaces rather than re-stages.** Re-staging moves the op to the end
        of the batch, and any derived `set_status` would then apply before the
        change it was derived from — so the whole of this route is
        `pending.replace_group`, addressed by the op's own id.

        It goes through the editor, because that is where the op's own fields
        are: `editor.render_op` re-renders a staged op into the same buffer
        `dg edit` opens, and there is no second parser. A derived op is refused
        here as it is there — the tray's ✕ is what removes one.
        """
        ref = self._body().get("ref")
        ops = pending.load()
        try:
            i = pending.resolve(ops, ref)
        except LookupError as exc:
            return self._json({"error": str(exc)}, 400)
        op = ops[i]
        kind = op.get("op")
        if kind not in editor.RENDERERS:
            return self._json(
                {"error": f"op {i} is {kind} — derived, not composed; "
                          f"remove it with the ✕ instead"}, 400)
        if not _editing.acquire(blocking=False):
            return self._json(
                {"error": "an editor is already open for this project"}, 409)
        try:
            # Rendered against the batch *without* this op: the others are
            # context the revision should see, this one is not.
            eff = pending.preview(Graph.load(), skip=i)
            new = editor.compose(eff, kind, vertex=op.get("vertex"),
                                 index=i, op=op, launcher=editor.launch_gui)
            pending.vet_all(eff, new)
            pending.replace_group(op.get("ref") or i, new, against=eff,
                                  supersede=editor.supersedes(kind, op))
        except editor.EditorAbort as exc:
            return self._json({"aborted": str(exc), "pending": pending.load()})
        except (editor.EditorError, pending.ApplyError) as exc:
            return self._json({"error": str(exc)}, 400)
        finally:
            _editing.release()
        self._json({"staged": new, "pending": pending.load()})

    def _repair(self) -> None:
        """Stage the PROVISIONAL marks a reopen would have derived. `dg repair`.

        `pending.repairs` is the whole of it and stages nothing else, which is
        what makes this the one honesty command that maps to a button. It
        exists because `expand` derives PROVISIONAL from a *reopen op*, and a
        merge, a rebase or a second clone can land a DECIDED vertex under a
        REOPENED premise without one ever having been staged here.
        """
        g = Graph.load()
        eff = pending.preview(g)
        ops = pending.repairs(eff)
        if not ops:
            return self._json({"staged": [], "pending": pending.load(),
                               "notes": ["nothing to repair — no decision "
                                         "rests on a premise under review "
                                         "without saying so"]})
        pending.stage_all(ops, against=eff)
        self._json({"staged": ops, "pending": pending.load(),
                    "notes": [f"{len(ops)} decision(s) marked PROVISIONAL"]})

    def _read_evidence(self) -> None:
        """Record that a late result was read against an answer, and it stands.

        The third exit. A result that lands after a decision is settled can
        refute the answer (reopen), turn out never to have been needed
        (unlink), or confirm it — and only the third was unreachable here, so
        of three exits the browser offered the one that files a reversal.

        Two things this route insists on, both from `dg confirm --against`:

        - **one reading per result.** Each is a separate finding and the note
          is about *that* result, so answering for several at once with one
          sentence is the box-tick this record exists not to be.
        - **the note is required.** Without it the entry records that somebody
          ran a command, not what they found.

        It stages a **task** op, because the reading is stored on the task. The
        response says so; the panel repeats it, or the tray gains a row the
        person who pressed a button on a decision cannot account for.
        """
        proj = project.find()
        if not proj.has_tasks:
            return self._json({"error": "this project has no tasks.json"}, 400)
        body = self._body()
        vid = (body.get("vertex") or "").strip()
        tid = (body.get("task") or "").strip()
        note = (body.get("note") or "").strip()
        if not note:
            return self._json(
                {"error": f"reading {tid} against {vid} needs what it showed"},
                400)
        g = pending.preview(Graph.load()) if proj.has_decisions else None
        tg = task_pending.preview(TaskGraph.load(proj.tasks))
        if g is None or vid not in g.vertices:
            return self._json({"error": f"unknown decision {vid}"}, 400)
        if tid not in {t["id"] for t in cross.late_evidence(tg, g, vid)}:
            return self._json(
                {"error": f"not evidence awaiting a reading against {vid}: "
                          f"{tid}"}, 400)
        op = {"op": "read_evidence", "task": tid, "against": vid,
              "note": note, "date": _today()}
        try:
            stage_tasks(tg, [op])
        except Exception as exc:
            return self._json({"error": str(exc)}, 400)
        self._json({"staged": [op],
                    "pending": pending.load(task_pending.path()),
                    "tray": "tasks",
                    "notes": [f"{vid} read against {tid} — a task op, so it is "
                              f"in the task tray"]})

    def _confirm(self) -> None:
        """Re-affirm a PROVISIONAL decision. `dg confirm`'s twin.

        A named act rather than a status control: `set_status` is derived
        everywhere else, and a browser that could write any status would be a
        second copy of the propagation rules. This route means *the premise
        moved and the answer still holds*, and stages exactly that.
        """
        g = Graph.load()
        eff = pending.preview(g)
        try:
            ops = pending.compose_confirm(
                eff, vid=(self._body().get("vertex") or "").strip())
        except pending.ApplyError as exc:
            return self._json({"error": str(exc)}, 400)
        pending.stage_all(ops, against=eff)
        released = [o["vertex"] for o in ops[1:]]
        self._json({"staged": ops, "pending": pending.load(),
                    "notes": ([f"{len(released)} vertex(es) were blocked on "
                               f"this and are released to OPEN: "
                               f"{', '.join(released)}"] if released else [])})

    #: Which composer each structural correction reaches, per store. One table,
    #: because the routes differ only in that and in what they report — and a
    #: `switch` per route is how one of them would come to skip a guard.
    #: `link`/`unlink` are decision-facing but write to the *task* store: the
    #: seam is a field on a task, and only one side of it may be edited.
    def _relate(self, *, decisions: bool) -> None:
        """Prerequisites, provenance, and the seam. `dg dep` and its family.

        Every rule lives in the store's own staging module, which the CLI calls
        too. What is here is transport plus one thing worth naming: a
        *removing* verb answers with what it set loose, so the panel can show
        it. `dg task undep`'s help has always promised it "releases this task if
        it waited only on that", and the browser is where that promise had no
        way to be read before the act rather than after.
        """
        body = self._body()
        verb = body.get("verb")
        if verb not in ("dep", "undep", "link", "unlink"):
            return self._json({"error": f"unknown verb {verb!r}"}, 400)
        try:
            if decisions:
                ops, said = self._decision_relation(verb, body)
            else:
                ops, said = self._task_relation(verb, body)
        except pending.ApplyError as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:
            return self._json({"error": str(exc)}, 500)
        path = None if decisions else task_pending.path()
        return self._json({"staged": ops, "pending": pending.load(path),
                           "notes": said, "tray": "decisions" if decisions
                                                 else "tasks"})

    def _decision_relation(self, verb, body):
        g = Graph.load()
        eff = pending.preview(g)
        vid = (body.get("id") or "").strip()
        after = [x for x in (body.get("after") or []) if x]
        if verb == "dep":
            ops, fresh, already = pending.compose_dep(eff, vid=vid, after=after)
            said = ([f"already rests on {', '.join(already)}"] if already else [])
            if not fresh:
                # Nothing staged is not the same as staged, and saying
                # otherwise sends the reader to `dg pending` for an op that is
                # not there — the same distinction `dg dep` makes.
                return [], said
        elif verb == "undep":
            ops = pending.compose_undep(eff, vid=vid, after=after)
            said = [f"{v.check} — {v.message}"
                    for v in pending.introduced(eff, ops)]
        else:
            raise pending.ApplyError(
                f"{verb} is a task verb — the seam is a field on a task, and "
                f"only that side of it may be edited")
        pending.stage_all(ops, against=eff)
        return ops, said

    def _task_relation(self, verb, body):
        proj = project.find()
        if not proj.has_tasks:
            raise pending.ApplyError("this project has no tasks.json — "
                                     "`dg task init` starts one")
        g = pending.preview(Graph.load()) if proj.has_decisions else None
        eff = task_pending.preview(TaskGraph.load(proj.tasks))
        tid = (body.get("id") or "").strip()
        after = [x for x in (body.get("after") or []) if x]
        during = [x for x in (body.get("discovered_during") or []) if x]
        if verb == "dep":
            ops, spoke = task_pending.compose_dep(
                eff, tid=tid, after=after, discovered_during=during)
            said = [f"already {task_pending.REL[k]['reads']} {', '.join(a)}"
                    for k, _, a in spoke if a]
        elif verb == "undep":
            ops, spoke = task_pending.compose_undep(
                eff, tid=tid, after=after, discovered_during=during)
            said = [f"{tid} no longer {task_pending.REL[k]['reads']} "
                    f"{', '.join(o)}" for k, o in spoke]
            said += _released_note(eff, g, ops)
        elif verb == "link":
            ops = task_pending.compose_link(
                eff, g, tid=tid,
                because=_ids(body, "because"),
                evidence_for=(body.get("evidence_for") or "").strip() or None)
            said = []
        else:
            ops, was = task_pending.compose_unlink(
                eff, tid=tid, because=_ids(body, "because"),
                evidence_for=bool(body.get("evidence_for")))
            said = [f"{tid} unlinked from {', '.join(was)}"]
            said += _released_note(eff, g, ops)
        said += [f"{v.check} — {v.message}"
                 for v in task_pending.introduced(eff, ops)]
        stage_tasks(eff, ops)
        return ops, said

    def _fallout(self) -> None:
        """What a proposed correction would set loose — **without staging it**.

        A read, so the panel can ask before it posts. `askFallout` is the
        pattern: the question a form is allowed to hold is one it can act on,
        and acting on it means knowing the answer first. `dg task drop` has had
        this since audit F2; the removing verbs have not, and a person clicking
        one in the browser learned what it released by reading the graph
        afterwards.
        """
        body = self._body()
        verb = body.get("verb")
        proj = project.find()
        try:
            if body.get("store") == "decisions":
                eff = pending.preview(Graph.load())
                ops = pending.compose_undep(
                    eff, vid=(body.get("id") or "").strip(),
                    after=[x for x in (body.get("after") or []) if x])
                return self._json({
                    "releases": [],
                    "findings": [f"{v.check} — {v.message}"
                                 for v in pending.introduced(eff, ops)]})
            if not proj.has_tasks:
                return self._json({"error": "this project has no tasks.json"}, 400)
            g = pending.preview(Graph.load()) if proj.has_decisions else None
            eff = task_pending.preview(TaskGraph.load(proj.tasks))
            tid = (body.get("id") or "").strip()
            if verb == "undep":
                ops, _ = task_pending.compose_undep(
                    eff, tid=tid,
                    after=[x for x in (body.get("after") or []) if x],
                    discovered_during=[x for x in
                                       (body.get("discovered_during") or []) if x])
            else:
                ops, _ = task_pending.compose_unlink(
                    eff, tid=tid, because=_ids(body, "because"),
                    evidence_for=bool(body.get("evidence_for")))
        except pending.ApplyError as exc:
            return self._json({"error": str(exc)}, 400)
        self._json({
            "releases": [f"{t} becomes startable"
                         for t in task_pending.releases(eff, g, ops)],
            "findings": [f"{v.check} — {v.message}"
                         for v in task_pending.introduced(eff, ops)]})

    def _compose(self) -> None:
        """Open the editor, block until it exits, stage what came back.

        The request is held for the whole editing session on purpose: it is the
        same blocking contract `dg decide --edit` has, and it means the browser
        learns the outcome without polling for a result that has nowhere else to
        go. `ThreadingHTTPServer` keeps the rest of the app responsive meanwhile.
        """
        body = self._body()
        kind = body.get("op")
        if kind not in editor.RENDERERS:
            return self._json({"error": f"cannot compose {kind!r}"}, 400)
        if not _editing.acquire(blocking=False):
            return self._json(
                {"error": "an editor is already open for this project — finish "
                          "it with C-c C-c, or cancel with C-c C-k"}, 409)
        try:
            g = Graph.load()
            # The buffer's context and the parser's target checks see the
            # staged ops too, matching the CLI's `_eff`.
            ops = editor.compose(
                pending.preview(g), kind,
                vertex=body.get("vertex"),
                seed=body.get("seed") or None,
                launcher=editor.launch_gui,
            )
            # Re-read the store: the editor session can last minutes, and
            # another tab or a terminal may have applied meanwhile. The ops
            # must expand against the graph as it is now, not as it was when
            # the buffer was written.
            fresh = Graph.load()
            staged: list[dict] = []
            for op in ops:
                staged += stage(fresh, op)
        except editor.EditorAbort as exc:
            return self._json({"aborted": str(exc), "pending": pending.load()})
        except (editor.EditorError, pending.ApplyError) as exc:
            return self._json({"error": str(exc)}, 400)
        finally:
            _editing.release()
        self._json({"staged": staged, "pending": pending.load()})

    def do_DELETE(self) -> None:
        with self._staging_as():
            if not self._host_ok():
                return
            if not self._authed():
                return
            try:
                # The trailing segment is an op id or an index — `pending.resolve`
                # takes either, and the id is what the page sends when the op has
                # one. Resolved under the tray lock, so a drop lands on the op the
                # page showed even if another writer has applied since. Audit F29.
                # The whole tray, not one op. `dg clear`'s twin, and it answers
                # with what it discarded: the tray is shared, so clearing can throw
                # away something a terminal staged a moment ago.
                # `urlparse`, not `==`: a narrowed clear carries `?agent=`,
                # and an exact match would fall through it to the 404 — the
                # same trap `/api/find` names, arriving from the other side.
                route = urlparse(self.path).path
                # `keep_blank_values`, and an empty name refused rather than
                # ignored. Without both, `?agent=` parses to nothing and a
                # narrowed clear silently becomes a clear of the whole tray —
                # a destructive widening produced by a page bug, which is the
                # one direction this route must not fail in.
                who = parse_qs(urlparse(self.path).query,
                               keep_blank_values=True).get("agent")
                agent = who[0] if who else None
                if agent == "":
                    return self._json({"error": "empty agent name"}, 400)
                if route == "/api/pending":
                    return self._json({"cleared": _clear(None, agent)})
                if route == "/api/task-pending":
                    return self._json({"cleared": _clear(task_pending.path(),
                                                         agent)})
                # `?group=1` is the way out `refuse_split` names, and the
                # page has to have one: a refusal whose remedy exists only as
                # a CLI flag is a dead end in a browser. `G-F11`.
                whole = parse_qs(urlparse(self.path).query).get("group")
                if self.path.startswith("/api/task-pending/"):
                    ref = urlparse(self.path).path.rsplit("/", 1)[1]
                    try:
                        (pending.drop_group if whole else pending.drop)(
                            ref, task_pending.path())
                    except LookupError as exc:
                        return self._json({"error": str(exc)}, 400)
                    except pending.ApplyError as exc:
                        # A group, and this took one member. A **400**, not the
                        # 500 an uncaught `ApplyError` becomes: it is a refusal
                        # the caller can act on, and the page needs the text to
                        # offer the act-wide drop.
                        return self._json({"error": str(exc), "group": True},
                                          400)
                    self._json(pending.load(task_pending.path()))
                elif self.path.startswith("/api/pending/"):
                    ref = urlparse(self.path).path.rsplit("/", 1)[1]
                    try:
                        (pending.drop_group if whole else pending.drop)(ref)
                    except LookupError as exc:
                        return self._json({"error": str(exc)}, 400)
                    except pending.ApplyError as exc:
                        return self._json({"error": str(exc), "group": True},
                                          400)
                    self._json(pending.load())
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as exc:
                self._json({"error": str(exc)}, 500)


# ---- running it detached -------------------------------------------------
#
# `dg serve` blocks, which is right for a terminal and useless to anything that
# wants the app *and* its own prompt back — a coding-agent session, chiefly.
# Detaching is here rather than in each host's adapter for the reason the whole
# plugin layer is thin: an adapter that forked a process would be a second
# implementation of the rule, in a language this repo does not test.

SERVE_NAME = ".dgraph-serve.json"
SERVE_LOG = ".dgraph-serve.log"


def _serve_file() -> Path:
    return project.find().root / SERVE_NAME


def probe(port: int, timeout: float = 1.5) -> dict | None:
    """Ask whatever is on this port to identify itself. `None` if it will not.

    Deliberately not "is the port open": something else may hold it, and a pid
    may have been recycled into an unrelated process. Only a reply from
    `/api/health` naming this tool counts.
    """
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(
            urllib.request.Request(f"http://127.0.0.1:{port}/api/health",
                                   headers={"Host": "127.0.0.1"}),
            timeout=timeout,
        ) as r:
            body = json.loads(r.read() or b"{}")
    except (OSError, urllib.error.URLError, ValueError):
        return None
    import dgraph
    return body if body.get("tool") == dgraph.TOOL else None


def status() -> dict:
    """What, if anything, this project has running.

    `state` is one of `running`, `stale` (a record whose port answers to nobody
    of ours) or `none`. A stale record is reported, never silently deleted: a
    caller that wanted to stop a server deserves to be told there was nothing
    to stop.
    """
    f = _serve_file()
    if not f.exists():
        return {"state": "none"}
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "stale", "reason": f"{SERVE_NAME} is unreadable"}
    live = probe(int(rec.get("port") or 0))
    if live is None:
        return {"state": "stale", **rec}
    # A record can outlive the run it describes and be inherited by a second
    # server on the same port; the live reply is what is believed.
    return {"state": "running", **rec, "pid": live["pid"],
            "url": f"http://127.0.0.1:{rec['port']}", "project": live["project"]}


def detach(port: int = 8765) -> dict:
    """Start a server in its own session and return once it answers.

    Two things make this safe to call from a slash command:

    - **stdout and stderr go to a file, never inherited.** A child holding the
      caller's pipe open is exactly what would hang the `!` block this exists
      to serve.
    - **It is idempotent.** Called when one is already up, it reports that one
      rather than fighting for the port, because a command run twice must not
      punish the second run.
    """
    import subprocess
    import sys
    import time

    have = status()
    if have["state"] == "running":
        return {**have, "already": True}

    root = project.find().root
    log = root / SERVE_LOG
    with open(log, "ab", buffering=0) as fh:
        proc = subprocess.Popen(
            [sys.executable, "-m", "dgraph.cli", "--project", str(root),
             "serve", "--port", str(port)],
            stdin=subprocess.DEVNULL, stdout=fh, stderr=fh,
            start_new_session=True, cwd=str(root),
        )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        live = probe(port, timeout=0.4)
        if live is not None:
            rec = {"pid": proc.pid, "port": port, "started": _now(),
                   "version": live["version"]}
            _serve_file().write_text(json.dumps(rec) + "\n", encoding="utf-8")
            return {"state": "running", **rec,
                    "url": f"http://127.0.0.1:{port}", "already": False}
        if proc.poll() is not None:
            break
        time.sleep(0.15)
    proc.kill()
    tail = ""
    try:
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
        tail = "\n".join(tail)
    except OSError:
        pass
    raise RuntimeError(
        f"the server did not come up on port {port}"
        + (f"\n{tail}" if tail else "")
        + f"\n(full output in {SERVE_LOG})")


def stop() -> dict:
    """Stop the server this project started, and nothing else.

    Refuses to signal a pid that does not identify as ours, which is the whole
    reason `/api/health` exists: a recycled pid is somebody else's process.
    """
    import signal
    st = status()
    if st["state"] != "running":
        if st["state"] == "stale":
            _serve_file().unlink(missing_ok=True)
            return {"state": "stale", "cleared": True}
        return {"state": "none"}
    try:
        os.kill(int(st["pid"]), signal.SIGTERM)
    except (OSError, ValueError) as exc:
        return {"state": "running", "error": str(exc)}
    _serve_file().unlink(missing_ok=True)
    return {"state": "stopped", "pid": st["pid"], "port": st["port"]}


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run(port: int = 8765) -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    ed = editor_payload()
    print(f"decision graph → http://127.0.0.1:{port}   (ctrl-c to stop)")
    if ed["available"]:
        print(f"compose in {ed['editor']}: click a decision, then “Compose in "
              f"{'emacs' if ed['emacs'] else 'editor'}”")
    else:
        print("no DISPLAY — the in-browser editor button is disabled; use "
              "`dg decide <id> --edit` from a terminal")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
