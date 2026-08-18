"""The web app's HTTP surface, including composing in an editor from the browser.

`POST /api/compose` is the only route that starts a process, so most of what is
pinned here is the boundary around it: who may call it, what happens when the
editor is cancelled or comes back incomplete, and that two browsers cannot fight
over the single compose buffer.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from dgraph import editor, pending, server


@pytest.fixture
def srv(store):
    """The real server on an ephemeral port, sharing this process's project."""
    from http.server import ThreadingHTTPServer

    s = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}"
    s.shutdown()
    s.server_close()


def req(base, path, method="GET", body=None, token=True, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data, method=method)
    if token:
        r.add_header(server.TOKEN_HEADER,
                    server.TOKEN if token is True else token)
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def jreq(*a, **kw):
    code, raw = req(*a, **kw)
    return code, json.loads(raw or b"{}")


CLOSE = {"op": "close", "vertex": "D05"}


def fill(**fields):
    """A launcher standing in for the editor: drop prose under named headings."""
    def launch(path):
        text = path.read_text(encoding="utf-8")
        for name, val in fields.items():
            head = f"** {name.capitalize()}\n"
            i = text.index(head) + len(head)
            text = text[:i] + val + "\n" + text[i:]
        path.write_text(text, encoding="utf-8")
        return 0
    return launch


# ---- the token -----------------------------------------------------------


def test_page_carries_this_runs_token(srv):
    body = req(srv, "/")[1].decode()
    assert server.TOKEN in body
    assert server.TOKEN_MARK not in body, "placeholder was served unsubstituted"


def test_reads_need_no_token(srv):
    assert jreq(srv, "/api/graph", token=False)[0] == 200
    assert jreq(srv, "/api/pending", token=False)[0] == 200


def test_staging_without_the_token_is_refused(srv):
    code, body = jreq(srv, "/api/pending", "POST", dict(CLOSE, answer="a",
                                                        source="s", to=[]),
                      token=False)
    assert code == 403
    assert pending.load() == [], "an unauthenticated POST changed the staging area"


def test_composing_without_the_token_is_refused(srv, monkeypatch):
    called = []
    monkeypatch.setattr(editor, "launch_gui", lambda p: called.append(p) or 0)
    assert jreq(srv, "/api/compose", "POST", CLOSE, token=False)[0] == 403
    assert not called, "the guard let a request start an editor"


def test_dropping_without_the_token_is_refused(srv):
    pending.stage(dict(CLOSE, answer="a", source="s", to=[]))
    assert jreq(srv, "/api/pending/0", "DELETE", token=False)[0] == 403
    assert len(pending.load()) == 1


def test_a_wrong_token_is_refused(srv, monkeypatch):
    # patched even though the call must be refused: if the guard ever regresses,
    # this should fail in milliseconds rather than open a real editor and hang.
    monkeypatch.setattr(editor, "launch_gui", lambda p: 0)
    assert jreq(srv, "/api/compose", "POST", CLOSE, token="not-it")[0] == 403


# ---- the Host boundary (audit B2a) ---------------------------------------


def test_a_foreign_host_header_is_refused_on_every_method(srv):
    """DNS rebinding: an attacker's hostname pointed at 127.0.0.1 reaches this
    socket carrying its own name — and would be same-origin with the page that
    embeds the token. Reads are refused too, because `/` is where the token
    lives."""
    import http.client
    host, port = srv.removeprefix("http://").split(":")
    for method, path in (("GET", "/"), ("GET", "/api/graph"),
                         ("POST", "/api/apply"), ("DELETE", "/api/pending/0")):
        c = http.client.HTTPConnection(host, int(port))
        c.request(method, path, headers={"Host": "evil.example",
                                         server.TOKEN_HEADER: server.TOKEN})
        assert c.getresponse().status == 403, (method, path)
        c.close()


# ---- stage-time vetting (audit B4) ---------------------------------------


def test_staging_an_unknown_op_is_refused(srv):
    code, body = jreq(srv, "/api/pending", "POST", {"op": "wibble"})
    assert code == 400 and "wibble" in body["error"]
    assert pending.load() == []


def test_staging_a_close_for_a_decided_vertex_is_refused(srv):
    """The CLI refuses this at stage time; the API used to stage it and let
    the whole batch die at apply."""
    code, body = jreq(srv, "/api/pending", "POST",
                      {"op": "close", "vertex": "D01", "answer": "a",
                       "source": "s", "falsifier": "f", "to": []})
    assert code == 400 and "reopen" in body["error"]
    assert pending.load() == []


def test_staging_a_dangling_target_is_refused(srv):
    code, body = jreq(srv, "/api/pending", "POST",
                      dict(CLOSE, answer="a", source="s", falsifier="f",
                           to=["D99"]))
    assert code == 400 and "D99" in body["error"]
    assert pending.load() == []


def test_a_broken_store_is_a_json_error_not_a_dropped_connection(srv, store):
    (store / "decisions.json").write_text("{not json", encoding="utf-8")
    code, body = jreq(srv, "/api/graph", token=False)
    assert code == 500 and "error" in body


# ---- apply's write order (audit B1) --------------------------------------


def test_apply_recovers_when_the_view_cannot_be_written(srv, store):
    """Store first, pending cleared, view last: a view failure is the one
    recoverable case (`dg render`), and the applied ops must not stay staged —
    they are in the store now, and re-applying them is the A3 dead end."""
    from dgraph.model import Graph
    code, _ = jreq(srv, "/api/pending", "POST",
                   dict(CLOSE, answer="a", source="s", falsifier="f", to=[]))
    assert code == 200
    (store / "decision-graph.md").mkdir()          # write_text now fails
    code, body = jreq(srv, "/api/apply", "POST")
    assert code == 500 and "dg render" in body["error"]
    assert Graph.load(store / "decisions.json").vertices["D05"].status == "DECIDED"
    assert pending.load() == []


def test_stage_expands_against_the_staged_ops_too(srv, store):
    """Audit A3, server side. A reopen must mark a descendant whose close sits
    in the tray unapplied, or Apply refuses the whole batch — the same
    effective-graph rule the CLI stages by."""
    code, _ = jreq(srv, "/api/pending", "POST",
                   {"op": "close", "vertex": "D05", "answer": "a",
                    "source": "s", "falsifier": "f", "to": []})
    assert code == 200
    code, body = jreq(srv, "/api/pending", "POST",
                      {"op": "reopen", "vertex": "D01", "why": "shaken"})
    assert code == 200, body
    marked = {o["vertex"] for o in body["staged"]
              if o["op"] == "set_status" and o["status"] == "PROVISIONAL"}
    assert "D05" in marked
    code, body = jreq(srv, "/api/apply", "POST")
    assert code == 200, body


# ---- composing -----------------------------------------------------------


def test_compose_stages_what_the_editor_returned(srv, monkeypatch):
    monkeypatch.setattr(editor, "launch_gui",
                        fill(answer="Chose the small one.", source="report/x.md",
                             falsifier="the corpus changes"))
    code, body = jreq(srv, "/api/compose", "POST", CLOSE)
    assert code == 200, body
    op = body["staged"][0]
    assert op["op"] == "close" and op["vertex"] == "D05"
    assert op["answer"] == "Chose the small one."
    # the same propagation the CLI does: D06 was BLOCKED:D05
    assert {"op": "set_status", "vertex": "D06", "status": "OPEN",
            "derived_from": "D05"} in body["staged"]
    assert body["pending"] == pending.load()


def test_compose_seeds_the_buffer_from_the_form(srv, monkeypatch):
    seen = {}

    def launch(path):
        seen["text"] = path.read_text(encoding="utf-8")
        return 0

    monkeypatch.setattr(editor, "launch_gui", launch)
    jreq(srv, "/api/compose", "POST",
         dict(CLOSE, seed={"answer": "half a thought", "source": "discussion"}))
    assert "half a thought" in seen["text"], "work typed in the browser was lost"
    assert "discussion" in seen["text"]


def test_compose_uses_the_gui_launcher(srv, monkeypatch):
    """Not `launch`: the browser cannot drive a terminal editor."""
    monkeypatch.setattr(editor, "launch", lambda *a, **k: pytest.fail(
        "the web path used the terminal launcher"))
    monkeypatch.setattr(editor, "launch_gui", fill(
        answer="a", source="s", falsifier="f"))
    assert jreq(srv, "/api/compose", "POST", CLOSE)[0] == 200


def test_cancelling_in_the_editor_stages_nothing(srv, monkeypatch):
    monkeypatch.setattr(editor, "launch_gui",
                        lambda p: p.write_text("", encoding="utf-8") or 0)
    code, body = jreq(srv, "/api/compose", "POST", CLOSE)
    assert code == 200 and "aborted" in body
    assert pending.load() == []


def test_an_incomplete_buffer_stages_nothing(srv, monkeypatch):
    monkeypatch.setattr(editor, "launch_gui", fill(answer="Chose it."))
    code, body = jreq(srv, "/api/compose", "POST", CLOSE)
    assert code == 400
    assert "Source" in body["error"]
    assert pending.load() == []


def test_compose_refuses_a_derived_op(srv):
    code, body = jreq(srv, "/api/compose", "POST",
                      {"op": "set_status", "vertex": "D05", "status": "OPEN"})
    assert code == 400 and "set_status" in body["error"]


def test_compose_reopen_round_trips(srv, monkeypatch):
    monkeypatch.setattr(editor, "launch_gui", fill(why="the sweep was wrong"))
    code, body = jreq(srv, "/api/compose", "POST",
                      {"op": "reopen", "vertex": "D01"})
    assert code == 200, body
    assert body["staged"][0] == {"op": "reopen", "vertex": "D01",
                                 "why": "the sweep was wrong"}
    # reopening D01 drags its decided descendants into PROVISIONAL
    assert {o["vertex"] for o in body["staged"] if o["op"] == "set_status"} == {
        "D02", "D03", "D04"}


def test_a_second_editor_is_refused_while_one_is_open(srv, monkeypatch):
    """One buffer per project, so the second request must not overwrite the
    first's file while someone is typing in it."""
    opened, release = threading.Event(), threading.Event()

    def launch(path):
        opened.set()
        release.wait(10)
        return 1  # abort; this test is about the lock, not the payload

    monkeypatch.setattr(editor, "launch_gui", launch)
    first: list = []
    t = threading.Thread(target=lambda: first.append(
        jreq(srv, "/api/compose", "POST", CLOSE)), daemon=True)
    t.start()
    assert opened.wait(10), "the first request never reached the editor"

    code, body = jreq(srv, "/api/compose", "POST", CLOSE)
    assert code == 409
    assert "already open" in body["error"]

    release.set()
    t.join(10)
    assert first and first[0][0] == 200  # the abort, reported cleanly


def test_the_lock_is_released_after_a_failure(srv, monkeypatch):
    monkeypatch.setattr(editor, "launch_gui", fill(answer="only this"))
    assert jreq(srv, "/api/compose", "POST", CLOSE)[0] == 400
    monkeypatch.setattr(editor, "launch_gui",
                        fill(answer="a", source="s", falsifier="f"))
    assert jreq(srv, "/api/compose", "POST", CLOSE)[0] == 200, \
        "a failed compose left the editor lock held"


# ---- what the browser is told --------------------------------------------


def test_editor_route_describes_the_editor(srv, monkeypatch):
    monkeypatch.setenv("DG_GUI_EDITOR", "emacs")
    monkeypatch.setenv("DISPLAY", ":0")
    body = jreq(srv, "/api/editor")[1]
    assert body == {"editor": "emacs", "emacs": True, "available": True,
                    "buffer": str(store_edit())}


def test_editor_route_reports_no_display(srv, monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DG_EDIT_CMD", raising=False)
    assert jreq(srv, "/api/editor")[1]["available"] is False


def store_edit():
    from dgraph import project
    return project.find().edit


def test_the_page_and_the_server_agree_on_the_token_contract():
    """Two files, one protocol: the placeholder the server substitutes and the
    header name the page sends back. A rename on either side is silent — the app
    would simply stop being able to stage anything — so pin both."""
    html = (server.STATIC / "app.html").read_text(encoding="utf-8")
    assert f'content="{server.TOKEN_MARK}"' in html
    assert server.TOKEN_HEADER in html


def test_the_page_makes_every_request_through_one_helper():
    """`api()` is what attaches the token, so a bare `fetch` for a mutating route
    is a request the server will refuse — silently, since the tray's delete
    button ignored the response. That is exactly how the token guard broke the
    ✕ button. One call site, so it cannot happen again."""
    html = (server.STATIC / "app.html").read_text(encoding="utf-8")
    sites = [ln.strip() for ln in html.splitlines() if "fetch(" in ln]
    assert len(sites) == 1, f"expected one fetch call site, got: {sites}"
    assert "const r = await fetch(path, opts)" in sites[0]
