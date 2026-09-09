"""The pack that emptied FINDINGS.md's *still standing* list into the graph
(T121–T128, under D98, D35, D103, D100, D106, D107, D108). One claim each.
"""

import json
import re
import shutil
import socket
import subprocess
import threading
from dataclasses import fields, replace

import pytest
from typer.testing import CliRunner

from dgraph import broker, cli, ids, integrate, mdbuffer, pending, project, server, task_pending
from dgraph.cli import app
from dgraph.model import Edge, Graph, Vertex
from dgraph.render import write
from dgraph.tasks import Reading, Task, TaskGraph

runner = CliRunner()


def dg(root, *args, input=None):
    import os
    os.environ["COLUMNS"] = "200"
    return runner.invoke(app, ["--project", str(root), *args], input=input)


def flat(out):
    return " ".join(out.split())


# ---- T121 · the first load says when the store did not load ---------------

FIRST_LOAD = r"""
const src = require("fs").readFileSync(process.argv[2], "utf8");
const js = src.slice(src.indexOf("<script>") + 8, src.lastIndexOf("</script>"));
const cut = (from, to) => js.slice(js.indexOf(from), js.indexOf(to));
const block = cut("async function firstLoad", "firstLoad();");
if (!js.trimEnd().endsWith("firstLoad();")) throw new Error("the foot does not call firstLoad()");
const SAID = [];
const said = (html, cls) => SAID.push([html, cls]);
const esc = s => s;
let badges = 0;
const refreshBadge = () => { badges++; };
const fit = () => { throw new Error("fit() ran after a failed boot"); };
const draw = () => { throw new Error("draw() ran after a failed boot"); };
let POLL = null; const POLL_EVERY = 1000;
const pollStat = () => {};
const setInterval = () => 42;
const boot = async () => { throw new Error("decisions.json could not be read: bad json"); };
eval(block + `
firstLoad().then(() => {
  if (SAID.length !== 1) throw new Error("said " + SAID.length + " times");
  if (!SAID[0][0].includes("decisions.json could not be read")) throw new Error("said: " + SAID[0][0]);
  if (SAID[0][1] !== "bad") throw new Error("class " + SAID[0][1]);
  if (POLL !== 42) throw new Error("the poll did not start");
  if (badges !== 1) throw new Error("badge not refreshed");
  console.log("ok");
}).catch(e => { console.error(e.message); process.exit(1); });
`);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_store_that_does_not_load_is_said_on_first_load(tmp_path):
    """`boot().then(...)` at the foot had no catch: a project whose store
    fails to load drew nothing and said nothing. `refresh()` had the catch;
    the first load is the same function, with it."""
    harness = tmp_path / "first.js"
    harness.write_text(FIRST_LOAD, encoding="utf-8")
    r = subprocess.run(["node", str(harness), str(server.STATIC / "app.html")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == "ok"


# ---- T122 · Relay.serve listens before the final path exists --------------

def test_the_relay_path_appears_only_once_it_is_listening(tmp_path, monkeypatch):
    """The file is what every reader takes for *the relay is up*; it used to
    exist between bind and listen, where a connect is refused."""
    r = broker.Relay(tmp_path, wait=1)
    final = r.path()
    seen = {}
    real_listen = socket.socket.listen

    def listen(self, *a):
        seen["final_at_listen"] = final.exists()
        return real_listen(self, *a)
    monkeypatch.setattr(socket.socket, "listen", listen)
    stop = threading.Event()
    t = threading.Thread(target=r.serve, args=(stop,), daemon=True)
    t.start()
    try:
        for _ in range(200):
            if final.exists():
                break
            threading.Event().wait(0.01)
        assert final.exists()
        assert seen["final_at_listen"] is False
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(1)
        c.connect(str(final))          # the moment it exists, it accepts
        c.close()
    finally:
        stop.set()
        t.join(2)
    assert not final.exists() and not final.with_name(final.name + ".bind").exists()


# ---- T123 · reopen asks before the buffer, and only with something to confirm

@pytest.fixture
def launcher(monkeypatch):
    calls = []

    def launch(path):
        calls.append(path)
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("** Why\n", "** Why\nnew evidence\n", 1),
                        encoding="utf-8")
        return 0
    from dgraph import editor
    monkeypatch.setattr(editor, "launch", launch)
    monkeypatch.setenv("TERM", "dumb")
    return calls


def test_reopen_with_descendants_refuses_before_the_editor_opens(store, g, launcher):
    write(g)
    r = dg(store, "reopen", "D01", "--edit")        # D02, D04 rest on D01
    assert r.exit_code == 2, r.output
    assert "missing --yes" in r.output and "D02" in r.output
    assert launcher == [], "the editor opened before the refusal"
    assert pending.load() == []


def test_reopen_with_nothing_to_propagate_needs_no_yes(store, g, launcher):
    write(g)
    r = dg(store, "reopen", "D03", "--edit")        # terminal: nothing rests on it
    assert r.exit_code == 0, r.output
    assert len(launcher) == 1
    assert [o["op"] for o in pending.load()] == ["reopen"]
    r = dg(store, "clear")
    r = dg(store, "reopen", "D03", "--no-edit", "-w", "new evidence")
    assert r.exit_code == 0, r.output
    assert [o["op"] for o in pending.load()] == ["reopen"]


def test_reopen_with_descendants_and_yes_still_stages_the_propagation(store, g, launcher):
    write(g)
    r = dg(store, "reopen", "D01", "--edit", "--yes")
    assert r.exit_code == 0, r.output
    assert sorted(o["op"] for o in pending.load()) == ["reopen"] + ["set_status"] * 3   # D02, D03, D04


# ---- T124 · mdbuffer's docstring -------------------------------------------

def test_mdbuffer_docstring_describes_the_hint_it_emits():
    doc = mdbuffer.__doc__
    line = [ln for ln in doc.splitlines() if ln.strip().startswith("# comment lines")][0]
    assert "<!--" not in line and "> hint" in line
    assert "<!--" not in [ln for ln in doc.splitlines() if "->" in ln][0]


# ---- T126 · every string option refuses a blank ----------------------------

def test_every_string_option_refuses_a_blank_or_is_whitelisted():
    unguarded = [(c, o) for c, o, d in cli._string_options(app)
                 if d.callback is not cli._not_blank and (c, o) not in cli.BLANK_ALLOWED]
    assert unguarded == []
    known = {(c, o) for c, o, _ in cli._string_options(app)}
    stale = [k for k in cli.BLANK_ALLOWED if k not in known]
    assert stale == [], stale
    assert all(cli.BLANK_ALLOWED.values())


@pytest.mark.parametrize("args", [
    ("dep", "D04", "--after", ""),
    ("confirm", "D01", "--against", ""),
    ("task", "dep", "T02", "--after", ""),
    ("task", "drop", "T02", "--why", "x", "--keep", ""),
    ("probe", "--domain", ""),
])
def test_a_blank_selecting_value_is_refused_on_every_door(store, task_store, g, args):
    write(g)
    r = dg(store, *args)
    assert r.exit_code != 0
    assert "refused rather than widened" in flat(r.output), r.output
    assert pending.load() == [] and pending.load(task_pending.path()) == []


def test_a_whitelisted_blank_still_means_what_it_meant(store, g):
    """`--note ""` clears the note by flag, the buffer's *may be emptied*."""
    write(g)
    r = dg(store, "amend", "D05", "--note", "")
    assert r.exit_code == 0, r.output
    assert pending.load()[0].get("note") == ""


# ---- T127 · one corpus for the TASKS line ----------------------------------

def test_the_tasks_line_and_dg_task_read_one_corpus(store, task_store, g):
    write(g)
    pending.stage({"op": "add_task", "id": "T09", "title": "nine", "area": "Alpha"},
                  task_pending.path())
    pending.stage({"op": "add_task", "id": "T10", "title": "ten", "area": "Alpha"},
                  task_pending.path())
    pending.stage({"op": "add_dep", "from": "T09", "to": ["T10"], "kind": "precedes"},
                  task_pending.path())
    line = [ln for ln in dg(store, "brief").output.splitlines() if ln.startswith("TASKS")][0]
    n = int(re.search(r"TASKS\s+(\d+):", line).group(1))
    ready = int(re.search(r"\((\d+) ready", line).group(1))
    blocked = int(re.search(r"(\d+) blocked", line).group(1))
    listing = dg(store, "task").output
    assert n == int(re.search(r"of (\d+)", listing).group(1))
    assert ready == len(re.search(r"^ready (.*)$", listing, re.M).group(1).split(", "))
    assert blocked >= 1 and ready <= n
    payload = json.loads(dg(store, "brief", "--json").output)
    assert sum(payload["tasks"]["counts"].values()) == n


# ---- T128 · one table of id-bearing fields ---------------------------------

def test_the_readers_derive_from_the_one_table():
    assert integrate._ID_KEYS == ids.RENUMBERED
    assert pending.REFERENCES == ids.LIVE
    assert set(ids.RENUMBERED) >= set(ids.LIVE) | {"id", "against"}
    assert "derived_from" in ids.RENUMBERED and "against" not in ids.LIVE


def test_rewrite_renumbers_every_field_including_lists_and_derived_from():
    m = {"D01": "D09", "T01": "T09"}
    op = {"op": "set_status", "vertex": "D01", "derived_from": "D01",
          "because": ["D01", "D02"], "to": ["D01"], "against": "D01",
          "task": "T01", "title": "keep"}
    integrate._rewrite(op, m)
    assert op == {"op": "set_status", "vertex": "D09", "derived_from": "D09",
                  "because": ["D09", "D02"], "to": ["D09"], "against": "D09",
                  "task": "T09", "title": "keep"}


def test_every_id_bearing_field_of_both_models_is_in_the_table():
    idish = {"id", "src", "to", "because", "evidence_for", "against",
             "vertex", "task", "from", "into", "derived_from"}
    for model in (Vertex, Edge, Task, Reading):
        for f in fields(model):
            if f.name in idish:
                key = ids.ALIASES.get(f.name, f.name)
                assert key in ids.ID_FIELDS, (model.__name__, f.name)


def test_rm_names_the_readings_that_keep_the_removed_id(store, task_store, g):
    write(g)
    tg = TaskGraph.load(task_store / "tasks.json")
    tg.tasks["T01"].readings.append(Reading(date="2026-05-01", note="held", against="D03"))
    tg.save(task_store / "tasks.json")
    subprocess.run(["git", "init", "-q", "."], cwd=store, check=True)
    subprocess.run(["git", "add", "-A"], cwd=store, check=True)
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a",
                    "commit", "-qm", "s"], cwd=store, check=True)
    r = dg(store, "rm", "D03", "--yes")
    assert r.exit_code == 0, r.output
    assert "keeps 1 reading(s) naming D03: T01 (2026-05-01)" in flat(r.output), r.output
