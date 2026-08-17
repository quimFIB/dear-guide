"""Local web app for the decision graph.

Bound to 127.0.0.1 only. It calls the same `pending` and `render` modules as the
CLI, so there is exactly one implementation of apply.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dgraph import pending, render
from dgraph.model import Graph

STATIC = Path(__file__).resolve().parent / "static"


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

    # -- routes --
    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, (STATIC / "app.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/graph":
            self._json(graph_payload(Graph.load()))
        elif self.path == "/api/pending":
            self._json(pending.load())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if self.path == "/api/pending":
            g = Graph.load()
            op = self._body()
            try:
                ops = pending.expand(g, op)
                for o in ops:
                    pending.stage(o)
            except Exception as exc:
                return self._json({"error": str(exc)}, 400)
            self._json({"staged": ops, "pending": pending.load()})
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

    def do_DELETE(self) -> None:
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
    print(f"decision graph → http://127.0.0.1:{port}   (ctrl-c to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
