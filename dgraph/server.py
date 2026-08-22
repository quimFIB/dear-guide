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

from dgraph import applying, cross, editor, pending, project, render, task_pending
from dgraph.model import Graph
from dgraph.tasks import TaskGraph, stop_label

STATIC = Path(__file__).resolve().parent / "static"

#: Minted per run: restarting `dg serve` invalidates any page left open, which
#: is the conservative direction to fail in.
TOKEN = secrets.token_urlsafe(24)
TOKEN_HEADER = "X-DG-Token"
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
    d["derived"] = {
        vid: {
            "depends": g.depends(vid),
            "depth": g.depth(vid),
            "children": g.children(vid),
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
                for h in g.history(vid)
            ],
        }
        for vid in g.vertices
    }
    d["frontier"] = g.frontier()
    return d


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


def task_payload(tg: TaskGraph, g: Graph | None) -> dict:
    """The task graph as the browser needs it.

    Mirrors `graph_payload`: the store, plus a `derived` block per task holding
    what the view would otherwise recompute. Every cross-graph reading comes
    from `cross`, so this builder joins nothing itself — the rule
    `tests/test_cross.py` enforces and the reason `gated_by` is not simply
    `because`.
    """
    d = tg.to_dict()
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
                {"gated_by": None, "ready": tg.ready(tid), "premise": None,
                 "evidence_for": None, "premise_status": None,
                 "premise_title": None}
                if g is None else _task_cross(tg, g, tid)
            ),
        }
        for tid in tg.tasks
    }
    d["frontier"] = tg.frontier()
    d["counts"] = tg.counts()
    return d


def _task_cross(tg: TaskGraph, g: Graph, tid: str) -> dict:
    """One task's link to the decision store, resolved for display."""
    link = cross.task_link(tg, g, tid)
    did = link["premise"]
    return {
        "gated_by": link["gated_by"],
        "ready": link["ready"],
        "premise": link["because"],
        "dangling": link["dangling"],
        "evidence_for": link["evidence_for"],
        "premise_status": g.vertices[did].status if did else None,
        "premise_title": g.vertices[did].title if did else None,
    }


def joined_payload(g: Graph | None, tg: TaskGraph | None) -> dict:
    """What the joined view draws: which tasks hang off which decision.

    The one reading no single-store view can produce, and the reason
    `dgraph/cross.py` exists. Empty on either side when that store is absent.
    """
    if g is None or tg is None:
        return {"by_decision": {}}
    return {
        "by_decision": {
            vid: {"rests_on": cross.rests_on(tg, vid),
                  "evidence": cross.evidence(tg, vid),
                  "pending_evidence": cross.pending_evidence(tg, vid)}
            for vid in g.vertices
        }
    }


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
    pending.vet(eff, op)
    ops = pending.expand(eff, op)
    pending.stage_all(ops, against=eff)   # one write, each op stamped
    return ops


def stage_task(tg: TaskGraph, op: dict) -> list[dict]:
    """Vet a task op and stage it. The one task-staging path.

    The twin of `stage`, and shorter for a reason the task store documents:
    nothing about a task propagates. Reopening a decision marks its decided
    descendants `PROVISIONAL`; finishing a task changes nothing but the task,
    because blocked is derived and never stored. So there is a `vet` here and
    no `expand`.
    """
    eff = task_pending.preview(tg)
    task_pending.vet(eff, op)
    pending.stage(op, task_pending.path())
    return [op]


def find_payload(q: str) -> dict:
    """One query, answered for the browser exactly as the CLI answers it.

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
    # A store the query is not about is absent from `matched`, which the page
    # reads as "this tab is not filtered" — different from an empty list, which
    # means "filtered, and nothing here matches".
    return out


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
            elif urlparse(self.path).path == "/api/find":
                # The path alone, not a prefix: `startswith` answered
                # `/api/findXYZ` as a find, which is the only route here that
                # did not have to be spelled correctly.
                self._json(find_payload(parse_qs(
                    urlparse(self.path).query).get("q", [""])[0]))
            elif self.path == "/api/health":
                self._json(health())
            elif self.path == "/api/editor":
                self._json(editor_payload())
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
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
            self._json({"staged": ops, "pending": pending.load()})
        elif self.path == "/api/task-pending":
            proj = project.find()
            if not proj.has_tasks:
                return self._json({"error": "this project has no tasks.json — "
                                            "`dg task init` starts one"}, 400)
            op = self._body()
            try:
                ops = stage_task(TaskGraph.load(proj.tasks), op)
            except Exception as exc:
                return self._json({"error": str(exc)}, 400)
            self._json({"staged": ops,
                        "pending": pending.load(task_pending.path())})
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
            self._apply()
        else:
            self._json({"error": "not found"}, 404)

    def _apply(self) -> None:
        """Apply both trays, exactly as `dg apply` does.

        Two independent batches: a task batch that will not apply must never
        stop a decision batch that would, so each is reported on its own and a
        refusal names which one. The write sequence itself is
        `dgraph.applying` — the same function the CLI calls, so the two hosts
        cannot drift on the order that makes it safe.
        """
        proj = project.find()
        ops = pending.load(proj.pending) if proj.has_decisions else []
        task_ops = (pending.load(task_pending.path())
                    if proj.has_tasks else [])
        if not ops and not task_ops:
            return self._json({"error": "nothing staged"}, 400)

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
                if r.view_error:
                    out["errors"].append(
                        f"applied {r.applied} {label} op(s) to {r.store}, but "
                        f"{r.view} could not be written ({r.view_error}) — run "
                        f"`dg render` to regenerate it")
        if graph is not None:
            out["graph"] = graph_payload(graph)
        if not out["errors"]:
            return self._json(out)
        # `error` is the one-string form every existing caller reads; `errors`
        # keeps them apart, since two independent batches can fail for two
        # unrelated reasons and a client showing one of them would mislead.
        out["error"] = "\n".join(out["errors"])
        # 400 when nothing was written — a refusal, and the ops are still
        # staged. 500 once something *has* landed: the store moved and the
        # view did not, which `dg render` fixes and which the caller must not
        # read as "nothing happened".
        wrote = out["applied"] or out["applied_tasks"]
        self._json(out, 500 if wrote else 400)

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
        if not self._host_ok():
            return
        if not self._authed():
            return
        try:
            # The trailing segment is an op id or an index — `pending.resolve`
            # takes either, and the id is what the page sends when the op has
            # one. Resolved under the tray lock, so a drop lands on the op the
            # page showed even if another writer has applied since. Audit F29.
            if self.path.startswith("/api/task-pending/"):
                try:
                    pending.drop(self.path.rsplit("/", 1)[1],
                                 task_pending.path())
                except LookupError as exc:
                    return self._json({"error": str(exc)}, 400)
                self._json(pending.load(task_pending.path()))
            elif self.path.startswith("/api/pending/"):
                try:
                    pending.drop(self.path.rsplit("/", 1)[1])
                except LookupError as exc:
                    return self._json({"error": str(exc)}, 400)
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
