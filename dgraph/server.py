"""Local web app for the decision graph.

Bound to 127.0.0.1 only. It calls the same `pending`, `render` and `editor`
modules as the CLI, so there is exactly one implementation of apply and one
implementation of the compose buffer.

`POST /api/compose` launches an editor on the user's desktop and blocks until it
exits — the `git commit` model, driven from the browser instead of the terminal.
Two consequences shape this module:

- **Mutating routes require a token.** Any page in the user's browser can POST
  to a localhost server; it just cannot read the response cross-origin. That was
  tolerable while the API only moved data around, but a route that starts a
  process is worth guarding properly, so the token is minted per run, embedded
  in the served page, and demanded back on every non-GET. A cross-origin script
  cannot read it, and the custom header alone forces a preflight this server
  never answers.
- **One editor at a time.** There is a single buffer per project, the same
  property `COMMIT_EDITMSG` has, so a second request is refused rather than
  allowed to overwrite a buffer someone is typing in.
"""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dgraph import editor, pending, project, render
from dgraph.model import Graph

STATIC = Path(__file__).resolve().parent / "static"

#: Minted per run: restarting `dg serve` invalidates any page left open, which
#: is the conservative direction to fail in.
TOKEN = secrets.token_urlsafe(24)
TOKEN_HEADER = "X-DG-Token"
TOKEN_MARK = "__DG_TOKEN__"

#: Held for as long as an editor is open, which may be minutes.
_editing = threading.Lock()


def graph_payload(g: Graph) -> dict:
    d = g.to_dict()
    d["derived"] = {
        vid: {
            "depends": g.depends(vid),
            "depth": g.depth(vid),
            "children": g.children(vid),
            "history": [
                {"summary": h.summary, "replaced_by": h.replaced_by, "why": h.why}
                for h in g.history(vid)
            ],
        }
        for vid in g.vertices
    }
    d["frontier"] = g.frontier()
    return d


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
    but not applied, or the batch fails `apply` wholesale.
    """
    ops = pending.expand(pending.preview(g), op)
    for o in ops:
        pending.stage(o)
    return ops


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

    def _page(self) -> bytes:
        html = (STATIC / "app.html").read_text(encoding="utf-8")
        return html.replace(TOKEN_MARK, TOKEN).encode()

    # -- routes --
    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, self._page(), "text/html; charset=utf-8")
        elif self.path == "/api/graph":
            self._json(graph_payload(Graph.load()))
        elif self.path == "/api/pending":
            self._json(pending.load())
        elif self.path == "/api/editor":
            self._json(editor_payload())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if not self._authed():
            return
        if self.path == "/api/pending":
            g = Graph.load()
            op = self._body()
            try:
                ops = stage(g, op)
            except Exception as exc:
                return self._json({"error": str(exc)}, 400)
            self._json({"staged": ops, "pending": pending.load()})
        elif self.path == "/api/compose":
            self._compose()
        elif self.path == "/api/expand":
            # preview propagation without staging
            g = Graph.load()
            try:
                self._json({"ops": pending.expand(g, self._body())})
            except Exception as exc:
                self._json({"error": str(exc)}, 400)
        elif self.path == "/api/apply":
            g = Graph.load()
            ops = pending.load()
            if not ops:
                return self._json({"error": "nothing staged"}, 400)
            try:
                out = pending.apply_all(g, ops)
            except pending.ApplyError as exc:
                return self._json({"error": str(exc)}, 400)
            out.save()
            render.write(out)
            pending.clear()
            self._json({"applied": len(ops), "graph": graph_payload(out)})
        else:
            self._json({"error": "not found"}, 404)

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
            staged: list[dict] = []
            for op in ops:
                staged += stage(g, op)
        except editor.EditorAbort as exc:
            return self._json({"aborted": str(exc), "pending": pending.load()})
        except (editor.EditorError, pending.ApplyError) as exc:
            return self._json({"error": str(exc)}, 400)
        finally:
            _editing.release()
        self._json({"staged": staged, "pending": pending.load()})

    def do_DELETE(self) -> None:
        if not self._authed():
            return
        if self.path.startswith("/api/pending/"):
            try:
                pending.drop(int(self.path.rsplit("/", 1)[1]))
            except (ValueError, IndexError) as exc:
                return self._json({"error": str(exc)}, 400)
            self._json(pending.load())
        else:
            self._json({"error": "not found"}, 404)


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
