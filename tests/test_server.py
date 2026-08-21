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
                                 "why": "the sweep was wrong", "format": "org"}
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


# ---- the cross-graph guard (audit D2), server side ------------------------


def test_apply_refuses_a_batch_that_closes_a_cross_cycle(srv, store):
    """Host parity: the two doors must not disagree about what may be written.
    A decision batch that is invalid only because of what is in the task store
    is refused here exactly as `dg apply` refuses it."""
    import json as _json

    from dgraph import render, task_render
    from dgraph.model import Graph
    from dgraph.tasks import TaskGraph

    (store / "decisions.json").write_text(_json.dumps({
        "areas": ["Alpha"],
        "vertices": [
            {"id": "D01", "title": "First question", "area": "Alpha",
             "status": "OPEN"},
            {"id": "D02", "title": "Second question", "area": "Alpha",
             "status": "OPEN"},
        ],
        "edges": [],
    }), encoding="utf-8")
    (store / "tasks.json").write_text(_json.dumps({
        "areas": ["Alpha"],
        "tasks": [{"id": "T01", "title": "The spike", "area": "Alpha",
                   "status": "TODO", "because": "D02", "evidence_for": "D01"}],
        "edges": [],
    }), encoding="utf-8")
    render.write(Graph.load(store / "decisions.json"), store / "decision-graph.md")
    task_render.write(TaskGraph.load(store / "tasks.json"), store / "tasks.md")

    # D01 -> D02 closes it: D01 -> D02 -> T01 -> D01.
    code, _ = jreq(srv, "/api/pending", "POST",
                   {"op": "close", "vertex": "D01", "answer": "the answer",
                    "source": "a meeting", "falsifier": "new evidence",
                    "to": ["D02"]})
    assert code == 200
    code, body = jreq(srv, "/api/apply", "POST")
    assert code == 400 and "link_acyclic" in body["error"]
    assert Graph.load(store / "decisions.json").vertices["D01"].status == "OPEN"
    assert pending.load()          # still staged, nothing written


# ---- the task graph, and the join ----------------------------------------
#
# The decision routes above are mirrored one-for-one, so what is pinned here is
# what is *different*: a project may have one store and not the other, the
# readings that join them may not be reinvented outside `cross`, and the two
# staging trays stay independent all the way through apply.


@pytest.fixture
def dual(store, task_store, g):
    """One project with both stores, tasks pointing at real decisions."""
    from dgraph import render, task_render
    from dgraph.tasks import TaskGraph
    render.write(g, store / "decision-graph.md")
    tg = TaskGraph.load(task_store / "tasks.json")
    tg.tasks["T01"].because = "D01"      # DONE, premise settled
    tg.tasks["T03"].because = "D05"      # TODO, premise OPEN — gated
    tg.tasks["T04"].evidence_for = "D05"
    tg.save(task_store / "tasks.json")
    task_render.write(tg, task_store / "tasks.md")
    return task_store


def test_tasks_is_null_when_the_project_has_no_task_store(srv, store):
    """`null`, not a 500 and not an empty graph. "This project does not track
    work" and "this project has no work outstanding" are different facts, and
    the tab has to be able to say which."""
    code, body = jreq(srv, "/api/tasks")
    assert code == 200 and body is None


def test_tasks_carries_the_derived_readings_the_view_would_recompute(srv, dual):
    code, body = jreq(srv, "/api/tasks")
    assert code == 200
    d = body["derived"]["T03"]
    assert d["prerequisites"] == ["T02"]
    assert d["waiting_on"] == ["T02"]
    assert body["frontier"] == ["T02", "T03", "T04"]
    assert body["counts"]["DONE"] == 1


def test_readiness_in_the_payload_accounts_for_the_premise(srv, dual):
    """T03's prerequisites and its premise are two different obstacles, and the
    panel has to name the right one. The task store alone cannot: `cross` is
    the only module that may look at both."""
    _, body = jreq(srv, "/api/tasks")
    c = body["derived"]["T03"]["cross"]
    assert c["premise"] == "D05" and c["gated_by"] == "D05"
    assert c["premise_status"] == "OPEN" and c["ready"] is False


def test_the_joined_route_reports_both_link_directions(srv, dual):
    code, body = jreq(srv, "/api/joined")
    assert code == 200
    assert body["by_decision"]["D01"]["rests_on"] == ["T01"]
    assert body["by_decision"]["D05"]["evidence"] == ["T04"]
    assert body["by_decision"]["D05"]["pending_evidence"] == ["T04"]


def test_the_joined_route_is_empty_without_both_stores(srv, store):
    _, body = jreq(srv, "/api/joined")
    assert body == {"by_decision": {}}


def test_task_depth_ranks_by_longest_prerequisite_path(srv, dual):
    _, body = jreq(srv, "/api/tasks")
    d = body["derived"]
    assert (d["T01"]["depth"], d["T02"]["depth"], d["T03"]["depth"]) == (0, 1, 2)
    assert d["T04"]["depth"] == 0          # unconnected, which is ordinary


def test_staging_a_task_op_needs_the_token(srv, dual):
    code, _ = jreq(srv, "/api/task-pending", "POST",
                   {"op": "set_status", "task": "T02", "status": "DOING"},
                   token=False)
    assert code == 403


def test_a_task_op_staged_in_the_browser_is_the_tray_the_cli_reads(srv, dual):
    """One tray, two front ends. If they were separate files, `dg apply` in a
    terminal would silently drop what the browser staged."""
    from dgraph import task_pending
    code, body = jreq(srv, "/api/task-pending", "POST",
                      {"op": "set_status", "task": "T02", "status": "DOING"})
    assert code == 200 and body["staged"]
    assert pending.load(task_pending.path()) == body["pending"]


def test_a_task_op_is_vetted_before_it_is_staged(srv, dual):
    """The CLI's stage-time guard, server side: an op for a task that does not
    exist is refused here rather than staged as a batch-poisoning op that
    `apply` rejects later."""
    code, body = jreq(srv, "/api/task-pending", "POST",
                      {"op": "set_status", "task": "T99", "status": "DOING"})
    assert code == 400 and "T99" in body["error"]


def test_staging_a_task_op_without_a_task_store_says_so(srv, store):
    code, body = jreq(srv, "/api/task-pending", "POST",
                      {"op": "set_status", "task": "T02", "status": "DOING"})
    assert code == 400 and "tasks.json" in body["error"]


def test_one_apply_writes_both_stores(srv, dual):
    from dgraph import task_pending
    from dgraph.model import Graph
    from dgraph.tasks import TaskGraph
    jreq(srv, "/api/pending", "POST",
         dict(CLOSE, answer="a", source="s", falsifier="f", to=[]))
    jreq(srv, "/api/task-pending", "POST",
         {"op": "set_status", "task": "T02", "status": "DOING"})
    code, body = jreq(srv, "/api/apply", "POST")
    assert code == 200, body
    # 2 decision ops: closing D05 also unstacks D06, which was BLOCKED:D05.
    assert (body["applied"], body["applied_tasks"]) == (2, 1)
    assert Graph.load(dual / "decisions.json").vertices["D05"].status == "DECIDED"
    assert TaskGraph.load(dual / "tasks.json").tasks["T02"].status == "DOING"
    assert pending.load() == [] and pending.load(task_pending.path()) == []
    assert "DOING" in (dual / "tasks.md").read_text()


def test_a_refused_task_batch_does_not_stop_a_decision_batch(srv, dual):
    """Two independent batches, exactly as `dg apply` treats them. A task op
    that will not apply must not take a clean decision batch down with it."""
    from dgraph import task_pending
    from dgraph.model import Graph
    from dgraph.tasks import TaskGraph
    jreq(srv, "/api/pending", "POST",
         dict(CLOSE, answer="a", source="s", falsifier="f", to=[]))
    # Staged past the vet, by writing the tray directly: T02 done with no
    # outcome is exactly what `apply` refuses.
    pending.stage({"op": "set_status", "task": "T02", "status": "DONE"},
                  task_pending.path())
    code, body = jreq(srv, "/api/apply", "POST")
    assert code == 500 and body["applied"] == 2 and body["applied_tasks"] == 0
    assert "task batch refused" in body["error"]
    assert Graph.load(dual / "decisions.json").vertices["D05"].status == "DECIDED"
    assert TaskGraph.load(dual / "tasks.json").tasks["T02"].status == "TODO"
    # The refused ops stay staged; the applied ones do not.
    assert pending.load() == [] and pending.load(task_pending.path())


def test_dropping_a_task_op_touches_only_the_task_tray(srv, dual):
    from dgraph import task_pending
    jreq(srv, "/api/pending", "POST",
         dict(CLOSE, answer="a", source="s", falsifier="f", to=[]))
    jreq(srv, "/api/task-pending", "POST",
         {"op": "set_status", "task": "T02", "status": "DOING"})
    before = len(pending.load())
    code, body = jreq(srv, "/api/task-pending/0", "DELETE")
    assert code == 200 and body == []
    assert before and len(pending.load()) == before


# ---- health, and the detached run ----------------------------------------


def test_health_identifies_the_tool_and_the_project(srv, store):
    """`--status` and `--stop` believe this and nothing else. A pidfile cannot
    tell a live server from a recycled pid, so the port has to answer for
    itself."""
    import os

    import dgraph
    code, body = jreq(srv, "/api/health")
    assert code == 200
    assert body["tool"] == "dg" and body["version"] == dgraph.version()
    assert body["pid"] == os.getpid() and body["project"] == str(store)


def test_health_needs_no_token_but_still_checks_the_host(srv, store):
    code, _ = jreq(srv, "/api/health", token=False)
    assert code == 200
    r = urllib.request.Request(srv + "/api/health")
    r.add_header("Host", "attacker.example")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    assert code == 403


def test_probe_rejects_a_port_that_is_not_ours(srv, store):
    """The whole reason `/api/health` exists. Something else on the port must
    not be mistaken for this server and signalled."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Other(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"tool": "something-else"}')

    s = ThreadingHTTPServer(("127.0.0.1", 0), Other)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        assert server.probe(s.server_port) is None
    finally:
        s.shutdown(); s.server_close()


def test_probe_survives_a_port_with_nothing_on_it(store):
    assert server.probe(1, timeout=0.3) is None


def test_status_reports_a_record_whose_port_answers_to_nobody_as_stale(store):
    """A stale record is reported, never silently deleted: somebody who asked
    to stop a server deserves to be told there was nothing to stop."""
    (store / server.SERVE_NAME).write_text(
        json.dumps({"pid": 999999, "port": 1}), encoding="utf-8")
    assert server.status()["state"] == "stale"


def test_stop_refuses_to_signal_anything_that_is_not_ours(store, monkeypatch):
    """The failure this guards is killing whatever inherited a recycled pid."""
    killed = []
    monkeypatch.setattr("os.kill", lambda *a: killed.append(a))
    (store / server.SERVE_NAME).write_text(
        json.dumps({"pid": 999999, "port": 1}), encoding="utf-8")
    out = server.stop()
    assert killed == [] and out["cleared"] is True
    assert not (store / server.SERVE_NAME).exists()


def test_status_is_none_with_no_record(store):
    assert server.status() == {"state": "none"}


def test_detach_starts_a_server_and_is_idempotent(store, g):
    """The property a slash command needs: it returns, and running it twice
    does not fight for the port."""
    from dgraph import render
    render.write(g, store / "decision-graph.md")
    first = server.detach(port=0 or _free_port())
    try:
        assert first["already"] is False
        assert server.probe(first["port"])["project"] == str(store)
        again = server.detach(port=first["port"])
        assert again["already"] is True and again["pid"] == first["pid"]
        assert server.status()["state"] == "running"
    finally:
        server.stop()
    assert server.status()["state"] == "none"
    assert server.probe(first["port"], timeout=0.5) is None


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_delete_route_takes_an_op_id(srv, store):
    """The browser sends the id when the op has one. Audit F29: the tray is
    shared, and a terminal applying between the page's last refresh and the
    click shifts every position past what it removed."""
    from dgraph import pending

    for n in (7, 8, 9):
        pending.stage({"op": "add_vertex", "id": f"D{n:02d}", "title": f"n{n}",
                       "area": "Alpha", "status": "OPEN"})
    ops = pending.load()
    code, left = jreq(srv, "/api/pending/" + ops[2]["ref"], "DELETE")
    assert code == 200 and [o["id"] for o in left] == ["D07", "D08"]

    # an index still resolves, and an id nothing carries is a 400 that says so
    code, left = jreq(srv, "/api/pending/0", "DELETE")
    assert code == 200 and [o["id"] for o in left] == ["D08"]
    code, bad = jreq(srv, "/api/pending/zzzz", "DELETE")
    assert code == 400 and "zzzz" in bad["error"]
    assert len(pending.load()) == 1


# ---- /api/find: the page and the CLI answer with one engine ---------------


def test_find_returns_ids_not_rows(srv, store):
    """The page already holds every record and only needs to know which to
    draw, so the endpoint returns ids and the match evidence and leaves
    rendering where rendering belongs."""
    code, d = jreq(srv, "/api/find?q=is:unsettled")
    assert code == 200
    assert d["matched"]["decisions"] == ["D05", "D06"]


def test_find_reports_a_fault_as_data_not_a_500(srv, store):
    """A mistyped query is an ordinary thing to do in a search box; the box
    wants to underline it, not show an error page."""
    code, d = jreq(srv, "/api/find?q=falsifer:x")
    assert code == 200 and "falsifer" in d["fault"] and "column" in d


def test_an_empty_query_is_not_a_filter(srv, store):
    """`null` rather than an empty match set: "not filtered" and "filtered, and
    nothing matches" must not blank the page the same way."""
    code, d = jreq(srv, "/api/find?q=")
    assert code == 200 and d["matched"] is None


def test_a_store_the_query_is_not_about_is_absent(srv, store):
    """Absence reads as "this tab is not filtered". An empty list would blank
    the tasks tab whenever somebody searched a decision-only field."""
    code, d = jreq(srv, "/api/find?q=falsifier:evidence")
    assert "tasks" not in d["matched"] and d["matched"]["decisions"] == ["D01"]


def test_find_says_which_field_matched(srv, store):
    code, d = jreq(srv, "/api/find?q=falsifier:evidence")
    assert d["why"]["D01"][0]["field"] == "falsifier"


def test_the_page_and_the_cli_agree(srv, store):
    """One engine, so a query typed into the page and the same query typed at a
    shell cannot mean different things."""
    from typer.testing import CliRunner

    from dgraph.cli import app
    _, d = jreq(srv, "/api/find?q=is:unsettled")
    out = CliRunner().invoke(
        app, ["--project", str(store), "find", "is:unsettled", "--ids"])
    assert d["matched"]["decisions"] == out.stdout.split()
