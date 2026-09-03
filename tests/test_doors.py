"""Two doors onto the same act must stage the same thing.

The tool's strongest property is that the CLI and the web app share one apply,
one staging area and one set of rules, so they cannot disagree about what is
about to be written. Every test here pins that property for an operation the
browser gained when it stopped being a viewer with buttons, and the
shape is deliberate: **not** "the route stages something", which any half-built
form passes, but "the route stages *the op list the command stages*".

The op list is the unit rather than the resulting store, because the two differ
in exactly the way that hides a bug. A vertex staged without its edges applies
cleanly — `no_orphans` is a warning, not a refusal — so a form that dropped the
edges would produce a store that looks right and a graph that has lost its
structure. That was audit F28 on the task side and the same trap on this one.
"""

import json
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import bare, finished
from dgraph import pending, project, server, task_pending
from dgraph.cli import app
from dgraph.model import Graph
from dgraph.tasks import TaskGraph

runner = CliRunner()


@pytest.fixture
def srv():
    """The real server, sharing whatever project the test has set up."""
    from http.server import ThreadingHTTPServer

    s = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=s.serve_forever, daemon=True,
                     kwargs={"poll_interval": 0.01}).start()
    yield f"http://127.0.0.1:{s.server_port}"
    s.shutdown()
    s.server_close()


@pytest.fixture
def both(store, task_store):
    """A project holding both stores, with the seam written on one task.

    `store` and `task_store` share one `tmp_path`, so asking for both gives a
    directory with a decision graph and a task graph — the ordinary case, and
    the only one in which the seam can be written at all. `T01` carries a
    `because` so that there is something for `unlink` to remove; the rest are
    left bare, which is what most work looks like.
    """
    tg = TaskGraph.load(task_store / "tasks.json")
    tg.tasks["T01"].because = ["D01"]
    tg.save(task_store / "tasks.json")
    return task_store


def post(base, path, body):
    data = json.dumps(body).encode()
    r = urllib.request.Request(base + path, data=data, method="POST")
    r.add_header(server.TOKEN_HEADER, server.TOKEN)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def delete(base, path):
    r = urllib.request.Request(base + path, method="DELETE")
    r.add_header(server.TOKEN_HEADER, server.TOKEN)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def cli(root, *args):
    res = runner.invoke(app, ["--project", str(root), *args])
    assert res.exit_code == 0, res.output
    return res


def tray(path=None):
    return bare(pending.load(path))


# ---- opening a question --------------------------------------------------


#: The same intent, expressed once per door. Kept as one table so that adding a
#: field to the form and forgetting the flag is a test failure rather than a
#: divergence nobody looks for.
ADD_CASES = [
    pytest.param(
        {"id": "D07", "title": "A fresh question", "area": "Alpha"},
        ["add", "--id", "D07", "--title", "A fresh question", "--area", "Alpha"],
        id="bare"),
    pytest.param(
        {"id": "D07", "title": "With premises", "area": "Alpha",
         "after": ["D01", "D02"]},
        ["add", "--id", "D07", "--title", "With premises", "--area", "Alpha",
         "--after", "D01,D02"],
        id="after"),
    pytest.param(
        {"id": "D07", "title": "Waiting on another", "area": "Beta",
         "after": ["D05"]},
        ["add", "--id", "D07", "--title", "Waiting on another", "--area", "Beta",
         "--after", "D05"],
        id="waiting"),
    pytest.param(
        {"id": "D07", "title": "With a note", "area": "Alpha",
         "note": "Nobody has looked at this yet."},
        ["add", "--id", "D07", "--title", "With a note", "--area", "Alpha",
         "--note", "Nobody has looked at this yet."],
        id="note"),
]


@pytest.mark.parametrize("body,args", ADD_CASES)
def test_add_stages_the_same_ops_through_both_doors(srv, store, body, args):
    code, res = post(srv, "/api/add", body)
    assert code == 200, res
    from_web = tray()
    pending.clear()

    cli(store, *args)
    assert tray() == from_web
    # And it is the whole list, not just the vertex: the edges are the half a
    # form drops, and a store where they are missing still applies.
    assert res["staged"] == from_web


def test_a_blocked_status_stages_its_edge_from_the_browser(srv, store):
    """The paragraph this test used to carry was already the argument for
    `D68`: `waiting_on` and the frontier derive from edges, so an OPEN vertex
    resting on an unsettled premise reads correctly everywhere without a
    status saying so. The status is now refused at this door as at every
    other, with the remedy named."""
    code, res = post(srv, "/api/add", {
        "id": "D07", "title": "Blocked", "area": "Alpha", "status": "BLOCKED:D04"})
    assert code != 200 or "error" in res, res
    assert "--after" in str(res)


def test_the_browser_refuses_what_the_command_refuses(srv, store):
    """One rule set, so the refusals arrive at the same inputs."""
    for body, args, fragment in [
        ({"id": "D01", "title": "x", "area": "Alpha"},
         ["add", "--id", "D01", "--title", "x", "--area", "Alpha"],
         "already exists"),
        # An area that *resembles* one in use. A genuinely new one is legal
        # at both doors — areas accumulate — so the shared rule to pin is the
        # typo guard, not membership.
        ({"id": "D07", "title": "x", "area": "alpha"},
         ["add", "--id", "D07", "--title", "x", "--area", "alpha"],
         "close to areas already in use"),
        ({"id": "D07", "title": "x", "area": "Alpha", "after": ["D99"]},
         ["add", "--id", "D07", "--title", "x", "--area", "Alpha",
          "--after", "D99"],
         "unknown parent"),
        ({"id": "D07", "title": "x", "area": "Alpha", "status": "SORT-OF"},
         ["add", "--id", "D07", "--title", "x", "--area", "Alpha",
          "--status", "SORT-OF"],
         "illegal status"),
    ]:
        code, res = post(srv, "/api/add", body)
        assert code == 400, (body, res)
        assert fragment in res["error"]
        out = runner.invoke(app, ["--project", str(store), *args])
        assert out.exit_code == 1
        assert fragment in out.output
        assert tray() == [], "a refusal must leave the tray as it was"


def test_a_staged_id_is_as_taken_as_a_stored_one(srv, store):
    """The tray is shared, so the message has to tell the two apart."""
    post(srv, "/api/add", {"id": "D07", "title": "first", "area": "Alpha"})
    code, res = post(srv, "/api/add", {"id": "D07", "title": "again",
                                       "area": "Alpha"})
    assert code == 400
    assert "staging area" in res["error"]


def test_the_form_is_told_which_id_is_next(srv, store):
    """Prefilled from `editor.next_id`, which is what `dg add --edit` uses."""
    r = urllib.request.Request(srv + "/api/graph")
    r.add_header(server.TOKEN_HEADER, server.TOKEN)
    with urllib.request.urlopen(r, timeout=10) as resp:
        assert json.loads(resp.read())["next_id"] == "D07"


# ---- recording a piece of work -------------------------------------------


TASK_CASES = [
    pytest.param(
        {"id": "T07", "title": "Fresh work", "area": "Alpha"},
        ["task", "add", "--id", "T07", "--title", "Fresh work",
         "--area", "Alpha"],
        id="bare"),
    pytest.param(
        {"id": "T07", "title": "Ordered", "area": "Alpha", "after": ["T02"]},
        ["task", "add", "--id", "T07", "--title", "Ordered", "--area", "Alpha",
         "--after", "T02"],
        id="after"),
    pytest.param(
        {"id": "T07", "title": "Turned up", "area": "Beta",
         "discovered_during": ["T01"]},
        ["task", "add", "--id", "T07", "--title", "Turned up", "--area", "Beta",
         "--discovered-during", "T01"],
        id="prompted"),
    pytest.param(
        {"id": "T07", "title": "Both kinds", "area": "Alpha",
         "after": ["T02"], "discovered_during": ["T01"]},
        ["task", "add", "--id", "T07", "--title", "Both kinds",
         "--area", "Alpha", "--after", "T02", "--discovered-during", "T01"],
        id="both-kinds"),
]


@pytest.mark.parametrize("body,args", TASK_CASES)
def test_task_add_stages_the_same_ops_through_both_doors(srv, both, body, args):
    code, res = post(srv, "/api/add-task", body)
    assert code == 200, res
    from_web = tray(task_pending.path())
    pending.clear(task_pending.path())

    cli(both, *args)
    assert tray(task_pending.path()) == from_web
    assert res["staged"] == from_web


def test_the_two_edge_kinds_stay_distinct_through_the_browser(srv, both):
    """`precedes` orders the work; `prompted` only records where it came from.

    Collapsing them into one "prerequisite" control would assert the ordering
    that `prompted` exists to avoid asserting, and the tree drawn from it would
    hold work back for a reason nobody claimed.
    """
    code, res = post(srv, "/api/add-task", {
        "id": "T07", "title": "x", "area": "Alpha",
        "after": ["T02"], "discovered_during": ["T01"]})
    assert code == 200, res
    kinds = {(o["from"], o["kind"]) for o in bare(res["staged"])
             if o["op"] == "add_dep"}
    assert kinds == {("T02", "precedes"), ("T01", "prompted")}


def test_work_can_be_linked_to_a_decision_that_is_only_staged(srv, both):
    """The A3 lesson, through the browser: a question recorded a minute ago is
    a legal `because`, because the premise resolves against the tray."""
    post(srv, "/api/add", {"id": "D07", "title": "just staged", "area": "Alpha"})
    code, res = post(srv, "/api/add-task", {
        "id": "T07", "title": "measure it", "area": "Alpha", "because": "D07"})
    assert code == 200, res
    assert bare(res["staged"])[0]["because"] == ["D07"]


def test_the_browser_refuses_the_task_the_command_refuses(srv, both):
    for body, args, fragment in [
        ({"id": "T01", "title": "x", "area": "Alpha"},
         ["task", "add", "--id", "T01", "--title", "x", "--area", "Alpha"],
         "already exists"),
        ({"id": "D9", "title": "x", "area": "Alpha"},
         ["task", "add", "--id", "D9", "--title", "x", "--area", "Alpha"],
         "malformed id"),
        ({"id": "T07", "title": "x", "area": "alpha"},
         ["task", "add", "--id", "T07", "--title", "x", "--area", "alpha"],
         "close to areas already in use"),
        ({"id": "T07", "title": "x", "area": "Alpha", "after": ["T99"]},
         ["task", "add", "--id", "T07", "--title", "x", "--area", "Alpha",
          "--after", "T99"],
         "unknown task"),
        ({"id": "T07", "title": "x", "area": "Alpha", "because": "D99"},
         ["task", "add", "--id", "T07", "--title", "x", "--area", "Alpha",
          "--because", "D99"],
         "unknown decision"),
    ]:
        code, res = post(srv, "/api/add-task", body)
        assert code == 400, (body, res)
        assert fragment in res["error"]
        out = runner.invoke(app, ["--project", str(both), *args])
        assert out.exit_code == 1
        assert fragment in out.output
        assert tray(task_pending.path()) == [], "a refusal stages nothing"


def test_naming_itself_reads_as_unknown_while_the_task_does_not_exist(srv, both):
    """`check_relation` refuses self-reference, but an `add` cannot reach it:
    the task being created is not in the graph yet, so the earlier "unknown
    task" refusal answers first. Pinned because the two doors agree on *which*
    refusal, and because the self case is real for `dg task dep`, where the
    task does exist — see the interface audit's F4."""
    code, res = post(srv, "/api/add-task", {
        "id": "T07", "title": "x", "area": "Alpha", "after": ["T07"]})
    assert code == 400
    assert "unknown task(s): T07" in res["error"]
    out = runner.invoke(app, ["--project", str(both), "task", "add",
                              "--id", "T07", "--title", "x", "--area", "Alpha",
                              "--after", "T07"])
    assert out.exit_code == 1
    assert "unknown task(s): T07" in out.output


def test_a_typo_in_the_second_spec_stages_nothing_at_all(srv, both):
    """Both relation specs are checked before any op is built, so the new task
    does not end up in the tray alone with half its structure."""
    code, res = post(srv, "/api/add-task", {
        "id": "T07", "title": "x", "area": "Alpha",
        "after": ["T02"], "discovered_during": ["T99"]})
    assert code == 400
    assert tray(task_pending.path()) == []


def test_a_project_with_no_task_store_says_so(srv, store):
    code, res = post(srv, "/api/add-task",
                     {"id": "T07", "title": "x", "area": "Alpha"})
    assert code == 400
    assert "dg task init" in res["error"]


def test_linking_needs_a_decision_store_to_link_to(srv, task_store):
    """A tasks-only project: the field is refused, and the refusal says how to
    stop it being refused rather than only that it was."""
    code, res = post(srv, "/api/add-task", {
        "id": "T07", "title": "x", "area": "Alpha", "because": "D01"})
    assert code == 400
    assert "no decision graph" in res["error"]
    assert "dg init" in res["error"]


# ---- what the page is allowed to know ------------------------------------


def _page():
    return (server.STATIC / "app.html").read_text(encoding="utf-8")


def test_the_new_forms_reach_the_shared_routes():
    """A form that posted a hand-built op to `/api/pending` would work, and
    would be the second copy: the op list, and the edges in it, are the
    composer's job. Pin the routes so that shortcut is a test failure."""
    html = _page()
    assert '"/api/add"' in html and '"/api/add-task"' in html


def test_the_page_keeps_no_copy_of_the_rules():
    """The browser checks only what a form can check without asking — that the
    required boxes have something in them. Everything the *graph* decides
    (a legal id, a known area, a status that names an existing blocker) lives
    in the two composers, and a second implementation here is how the doors
    come to disagree about what is legal.

    Written as an absence, which is the only form this claim has: the page must
    not contain the vocabulary it would need to make these judgements itself.
    """
    form = _page().split("let NEWKIND=null;")[1].split("/* ---- the task panel")[0]
    for banned in ("SIMPLE_STATUSES", "REOPENED", "PROVISIONAL", "DECIDED",
                   "fullmatch", "/^T[0-9]", "/^D[0-9]"):
        assert banned not in form, f"the new-node form knows too much: {banned}"


def test_the_two_edge_kinds_are_labelled_as_different_claims():
    """The control offers `after` and `discovered during` separately, and says
    why. Collapsing them is the mistake `dg task tree`'s docstring argues
    against, and a form is where it would be most tempting."""
    form = _page().split("function newTaskForm")[1].split("async function stageNewTask")[0]
    assert "nAfter" in form and "nDuring" in form
    assert "asserts an order" in form


def test_the_joined_view_asks_which_store():
    """Two stores, and the tab has not answered the question. Guessing is how
    a piece of work gets recorded as a decision."""
    assert "function chooseStore" in _page()


# ---- the forms actually render -------------------------------------------


#: The page has no build step, so `node --check` (in `test_orgmd.py`) catches a
#: stray comma and nothing else. This runs the two form builders against a DOM
#: stub, which is the difference between "the file parses" and "the form the
#: post function reads is the form that was drawn". Every field below is one
#: `stageNewDecision`/`stageNewTask` reads by id: a rename on one side only is
#: silent otherwise — the value would simply arrive empty.
FORM_HARNESS = r"""
const src = require("fs").readFileSync(process.argv[2], "utf8");
const js = src.slice(src.indexOf("<script>") + 8, src.lastIndexOf("</script>"));
// Two slices: the new-node forms, then the structure form. The task panel
// sits between them and drags in half the app, so it is stepped over rather
// than stubbed — these are the blocks under test, not the page.
const cut = (from, to) => js.slice(js.indexOf(from), js.indexOf(to));
const block = cut("let NEWKIND=null;", "/* ---- the task panel")
            + cut("const REL_FORMS = {", "/* ---- what a drop leaves standing");
const G = JSON.parse(process.argv[3]), T = JSON.parse(process.argv[4]);
let tab = "decisions", sel = null, tsel = null;
const held = {};
const el = id => (held[id] = held[id] || {id, value:"", options:[],
  selectedOptions:[], style:{}, onclick:null, onchange:null, innerHTML:""});
// `querySelectorAll` because the forms bind their own controls after drawing
// them. Returning nothing is right: what is under test is the HTML a form
// produces and the body a reader builds back out of it, not the binding.
const side = {innerHTML:"", querySelectorAll: () => []};
const $ = s => (s === "#side" ? side : el(s.slice(1)));
const esc = x => String(x == null ? "" : x);
const draw = () => {}, fit = () => {}, boot = async () => {};
const say = () => {};
// A recorder, not a stub returning nothing: two assertions below are about
// *what was posted*, and a stub that forgets its argument cannot fail them.
const POSTED = [];
const api = async (url, opts) => {
  POSTED.push({url, body: JSON.parse(opts.body)});
  return url === "/api/fallout"
    ? {releases: ["T99 becomes startable"], findings: []}
    : {staged: [{}], notes: []};
};
const taskPanel = () => {}, panel = () => {};
const DRIVER = `
const want = (html, ids, what) => ids.forEach(i => {
  if (!html.includes(\`id="\${i}"\`)) throw new Error(what + " has no " + i);
});
newDecisionForm();
want(side.innerHTML, ["nId","nTitle","nArea","nStatus","nAfter",
                      "nNote","nGo","nNo"], "the decision form");
if (!side.innerHTML.includes(\`value="\${G.next_id}"\`))
  throw new Error("the id was not prefilled with " + G.next_id);
G.areas.forEach(a => {
  if (!side.innerHTML.includes(\`<option value="\${a}">\`))
    throw new Error("area missing from the form: " + a);
});
tab = "tasks";
newTaskForm();
want(side.innerHTML, ["nId","nTitle","nArea","nAfter","nDuring","nBecause",
                      "nEvidence","nNote","nGo","nNo"], "the task form");
if (!side.innerHTML.includes(\`value="\${T.next_id}"\`))
  throw new Error("the task id was not prefilled with " + T.next_id);

// The structure form, over both stores and all four verbs. Each is drawn and
// then read back through \`relateBody\`, which is the same reader the fallout
// preview uses — so a field the form draws and the body ignores is a failure
// here rather than a correction that silently does nothing.
const taskPanel = () => {}, panel = () => {};
[["tasks", process.argv[5], ["dep","undep","link","unlink"]],
 ["decisions", process.argv[6], ["dep","undep"]]].forEach(([store, id, verbs]) => {
  verbs.forEach(verb => {
    RELATING = {store, id, verb};
    structureForm();
    if (!side.innerHTML.includes('id="rGo"'))
      throw new Error(\`\${store}/\${verb} drew no stage button\`);
    const body = relateBody();
    if (body.verb !== verb || body.id !== id)
      throw new Error(\`\${store}/\${verb} read back as \` + JSON.stringify(body));
    const removing = verb === "undep" || verb === "unlink";
    if (removing !== side.innerHTML.includes("Check, then stage"))
      throw new Error(\`\${store}/\${verb} disagrees about whether it removes\`);
  });
});

// \`because\` is a set on the way out. The stub select reports itself single by
// default, so this is the one place the multiple branch is driven.
RELATING = {store: "tasks", id: process.argv[5], verb: "link"};
structureForm();
held.r_because.multiple = true;
held.r_because.selectedOptions = [{value: "D04"}, {value: "D05"}];
if (relateBody().because !== "D04,D05")
  throw new Error("a multi-select premise read back as "
                  + JSON.stringify(relateBody().because));

// **The act confirmed is the act staged.** \`relateFallout\` replaces \`#side\`
// with its confirmation, so a \`postRelate\` that read the form again read a
// form that no longer existed and posted an empty body — which the server
// refused as "nothing to unlink". Every removing verb failed this way whenever
// there was fallout to confirm, which is the only time the path runs.
RELATING = {store: "tasks", id: process.argv[5], verb: "unlink"};
structureForm();
held.r_because.multiple = true;
held.r_because.selectedOptions = [{value: "D04"}];
POSTED.length = 0;
relateFallout().then(() => {
  side.innerHTML = "";                     // as the confirmation did
  Object.keys(held).forEach(k => { if (k.startsWith("r_")) delete held[k]; });
  return held.rYes.onclick();
}).then(() => {
  const staged = POSTED.filter(p => p.url !== "/api/fallout");
  if (!staged.length) throw new Error("confirming staged nothing");
  if (staged[0].body.because !== "D04")
    throw new Error("the act staged is not the act confirmed: "
                    + JSON.stringify(staged[0].body));
  console.log("ok");
}).catch(e => { console.error(e.message); process.exit(1); });

// The correction form, over both stores, and read back through \`amendOp\` —
// the same pairing the structure form gets above, and for the same reason: a
// field the form draws and the op ignores is a correction that silently does
// nothing.
[["decisions", "vertex", G.areas, G.vertices[0]],
 ["tasks", "task", T.areas, T.tasks[0]]].forEach(([store, key, areas, rec]) => {
  side.innerHTML = amendForm(areas, rec);
  want(side.innerHTML, ["amTitle","amArea","amNote","doAmend"],
       store + "'s correction form");
  // Placeholders, never values: \`captureDraft\` reads a non-empty input as
  // unconfirmed work, so a prefilled form would make every node somebody
  // merely looked at into a draft.
  if (!side.innerHTML.includes(\`placeholder="\${rec.title}"\`))
    throw new Error(store + ": the title is not offered as a placeholder");
  areas.forEach(a => {
    if (!side.innerHTML.includes(\`<option value="\${a}">\`))
      throw new Error(store + ": area missing from the form: " + a);
  });
  const untouched = amendOp(key, rec.id);
  if (Object.keys(untouched).length !== 2)
    throw new Error(store + ": an untouched form built " +
                    JSON.stringify(untouched));
  held.amTitle.value = "  Reworded  ";
  held.amArea.value = areas[0];
  const op = amendOp(key, rec.id);
  if (op.op !== "set_fields" || op[key] !== rec.id || op.title !== "Reworded"
      || op.area !== areas[0] || "note" in op)
    throw new Error(store + ": read back as " + JSON.stringify(op));
  held.amTitle.value = ""; held.amArea.value = "";
});
console.log("ok");
`;
// `block + DRIVER`, not `eval(block); DRIVER;` — which is what this said, and
// a bare expression statement evaluates a string and discards it. Every
// assertion below the definition ran nowhere: the harness exited 0 whatever
// the forms drew, so this test had been asserting that `app.html`'s two blocks
// *parse* and nothing else. Found by breaking a field id on purpose and
// watching it pass.
eval(block + DRIVER);
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_forms_draw_every_field_their_post_function_reads(both, tmp_path):
    """Rendered against the real payloads, so an area the store has and the
    form does not is a failure here rather than a select nobody can use.

    Covers the new-node forms and the structure form — the latter over both
    stores and all four verbs, since it is one builder driven by a table and a
    missing table entry is otherwise a blank panel nobody notices."""
    g, tg = Graph.load(), TaskGraph.load(project.find().tasks)
    harness = tmp_path / "form.js"
    harness.write_text(FORM_HARNESS, encoding="utf-8")
    r = subprocess.run(
        ["node", str(harness), str(server.STATIC / "app.html"),
         json.dumps(server.graph_payload(g)),
         json.dumps(server.task_payload(tg, g)), "T03", "D06"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ---- correcting the structure --------------------------------------------


#: `(route, body, cli args, tray)` per structural correction. The decision
#: side and the task side are in one table on purpose: they are the same act
#: over two stores, and a table per store is how one of them would quietly lose
#: a case.
REL_CASES = [
    pytest.param(
        "/api/dep", {"verb": "dep", "id": "D05", "after": ["D03"]},
        ["dep", "D05", "--after", "D03"], None, id="dep"),
    pytest.param(
        "/api/dep", {"verb": "undep", "id": "D06", "after": ["D05"]},
        ["undep", "D06", "--after", "D05"], None, id="undep"),
    # A **decided** premise, which both doors refused until `D57` settled that
    # an edge's targets belong to the graph. Here rather than among the
    # refusals below, and that move is the point: the two doors have to agree
    # it is allowed exactly as they had to agree it was not.
    pytest.param(
        "/api/dep", {"verb": "undep", "id": "D04", "after": ["D02"]},
        ["undep", "D04", "--after", "D02"], None, id="undep-decided"),
    pytest.param(
        "/api/task-dep", {"verb": "dep", "id": "T04", "after": ["T02"]},
        ["task", "dep", "T04", "--after", "T02"], "task", id="task-dep"),
    pytest.param(
        "/api/task-dep",
        {"verb": "dep", "id": "T04", "discovered_during": ["T01"]},
        ["task", "dep", "T04", "--discovered-during", "T01"], "task",
        id="task-dep-prompted"),
    pytest.param(
        "/api/task-dep", {"verb": "undep", "id": "T03", "after": ["T02"]},
        ["task", "undep", "T03", "--after", "T02"], "task", id="task-undep"),
    pytest.param(
        "/api/task-dep", {"verb": "link", "id": "T04", "because": "D01"},
        ["task", "link", "T04", "--because", "D01"], "task", id="link"),
    pytest.param(
        "/api/task-dep", {"verb": "unlink", "id": "T01", "because": "D01"},
        ["task", "unlink", "T01", "--because", "D01"], "task", id="unlink"),
]


# `tray` rather than `store` for the argname: a parametrized argument shadows
# any fixture of the same name **across the whole fixture closure**, so calling
# it `store` silently robbed `both` of its decision graph and left the three
# decision-facing cases posting against a project that had none.
@pytest.mark.parametrize("route,body,args,tray_of", REL_CASES)
def test_structure_stages_the_same_ops_through_both_doors(srv, both, route,
                                                          body, args, tray_of):
    """Six commands, two stores, one composer each. The op list is the unit for
    the reason it is throughout this file: `undep` carries a `set_status` in the
    *same* list when it releases a block, and a door that staged the removal
    without it would produce a batch `apply` refuses over an invariant the user
    did not break — audit F31."""
    path = task_pending.path() if tray_of else None
    code, res = post(srv, route, body)
    assert code == 200, res
    from_web = tray(path)
    pending.clear(path)

    cli(both, *args)
    assert tray(path) == from_web
    assert res["staged"] == from_web


@pytest.mark.parametrize("route,body,args,tray_of,fragment", [
    ("/api/dep", {"verb": "dep", "id": "D01", "after": ["D01"]},
     ["dep", "D01", "--after", "D01"], None, "cannot rest on itself"),
    ("/api/dep", {"verb": "undep", "id": "D01", "after": ["D05"]},
     ["undep", "D01", "--after", "D05"], None, "does not rest on"),
    ("/api/task-dep", {"verb": "undep", "id": "T03", "after": ["T01"]},
     ["task", "undep", "T03", "--after", "T01"], "task", "not a prerequisite"),
    ("/api/task-dep", {"verb": "link", "id": "T04", "because": "D99"},
     ["task", "link", "T04", "--because", "D99"], "task", "unknown decision"),
])
def test_structure_refuses_the_same_thing_through_both_doors(
        srv, both, route, body, args, tray_of, fragment):
    """What both doors refuse, they refuse identically — same reason, same
    words. A decided premise used to be on this list and is not any more:
    `D57` made it ordinary, so it moved to `REL_CASES`, where the doors are
    asserted to *stage* it identically instead."""
    path = task_pending.path() if tray_of else None
    code, res = post(srv, route, body)
    assert code == 400, res
    assert fragment in res["error"]
    out = runner.invoke(app, ["--project", str(both), *args])
    assert out.exit_code == 1, out.output
    assert fragment in out.output
    assert tray(path) == [], "a refusal stages nothing"


def test_removing_a_block_releases_it_in_the_same_op_list(srv, store):
    """F31's release travelled in the same list as the `remove_edge`. Under
    `D68` there is no release to travel: the wait was the edge."""
    code, res = post(srv, "/api/dep",
                     {"verb": "undep", "id": "D06", "after": ["D05"]})
    assert code == 200, res
    assert bare(res["staged"]) == [{"op": "remove_edge", "from": "D05", "to": ["D06"]}]
    assert not any("released" in n for n in res["notes"])


def test_the_seam_may_only_be_edited_from_the_task_side(srv, both):
    """`because` is a field on a task, and the decision store never names work.
    A `link` posted to the decision route is refused rather than quietly
    handled, so the asymmetry stays visible."""
    code, res = post(srv, "/api/dep", {"verb": "link", "id": "D01"})
    assert code == 400
    assert "task verb" in res["error"]


def test_nothing_staged_is_reported_as_nothing_staged(srv, store):
    """An edge already in the store is not a failure and not a write. Saying
    "staged" would send the reader to `dg pending` for an op that is not
    there — the distinction `dg dep` makes, kept at the other door."""
    code, res = post(srv, "/api/dep", {"verb": "dep", "id": "D03",
                                       "after": ["D01"]})
    assert code == 200, res
    assert res["staged"] == []
    assert any("already rests on D01" in n for n in res["notes"])


# ---- what a removal sets loose -------------------------------------------


def test_the_fallout_of_a_removal_can_be_read_before_it_is_staged(srv, both):
    """The `askFallout` promise, for the removing verbs: a person can see what
    stops waiting *before* deciding, which is the part a confirmation dialog
    cannot do. A read — nothing is staged by asking."""
    code, res = post(srv, "/api/fallout", {
        "store": "tasks", "verb": "undep", "id": "T03", "after": ["T02"]})
    assert code == 200, res
    assert res["releases"] == ["T03 becomes startable"]
    assert tray(task_pending.path()) == [], "asking must stage nothing"


def test_the_fallout_reads_the_tray(srv, both):
    """A correction staged a moment ago has to be visible to the next one, or
    the preview describes a graph nobody is looking at."""
    post(srv, "/api/task-dep", {"verb": "dep", "id": "T03", "after": ["T04"]})
    code, res = post(srv, "/api/fallout", {
        "store": "tasks", "verb": "undep", "id": "T03", "after": ["T02"]})
    assert code == 200, res
    # T03 now also waits on T04, which is DOING, so dropping T02 frees nothing.
    assert res["releases"] == []


def test_the_fallout_names_the_findings_a_removal_would_introduce(srv, store):
    """Introduced, not inherited: a store already invalid for an unrelated
    reason must not have that blamed on this act."""
    code, res = post(srv, "/api/fallout", {
        "store": "decisions", "verb": "undep", "id": "D06", "after": ["D05"]})
    assert code == 200, res
    assert res["releases"] == []
    assert any("no_orphans" in f for f in res["findings"])


def test_the_page_asks_before_it_removes():
    """Pinned in the file that holds it: both removing verbs go through the
    preview, and the preview is what the post reads from — one body builder,
    so a reassurance about one act cannot precede a different one."""
    html = _page()
    assert "async function relateFallout" in html
    assert "removing?relateFallout():postRelate()" in html
    assert html.count("function relateBody") == 1


def test_the_replaced_argument_says_which_leg_survived():
    """The comment at the old `app.html:809` was wrong and is gone. What
    replaced it has to argue the new position rather than leave the next reader
    to re-derive why a form may hold this — and has to keep arguing the one
    exclusion that stands."""
    html = _page()
    assert "Only the first of those survived" in html
    assert "erases a record instead of superseding it" in html
    assert "each one wants a sentence of explanation the form has nowhere" \
        not in html


# ---- the way out of PROVISIONAL ------------------------------------------


@pytest.fixture
def reviewed(store, g):
    """D02 PROVISIONAL under a REOPENED D01, then D01 settled again.

    The shape `dg confirm` exists for: a premise went under review, its
    conclusions were marked provisional, and the premise then settled the same
    way it was heading. Nothing about D02's answer changed, so there is nothing
    to supersede — and until this existed the only route back was a reversal
    that never happened.
    """
    from dataclasses import replace as dc_replace
    graph = Graph.load()
    graph.vertices["D02"] = dc_replace(graph.vertices["D02"],
                                       status="PROVISIONAL")
    graph.save()
    return graph


def test_a_provisional_decision_can_be_re_affirmed_from_the_browser(srv,
                                                                    reviewed):
    """The exit the browser did not have. It is the interface that *creates*
    PROVISIONAL — it has reopen — so being unable to clear it made this the one
    status the tool could reach and not leave."""
    code, res = post(srv, "/api/confirm", {"vertex": "D02"})
    assert code == 200, res
    assert bare(res["staged"]) == [
        {"op": "set_status", "vertex": "D02", "status": "DECIDED"}]


def test_confirm_stages_the_same_ops_through_both_doors(srv, reviewed, store):
    code, res = post(srv, "/api/confirm", {"vertex": "D02"})
    assert code == 200, res
    from_web = tray()
    pending.clear()
    cli(store, "confirm", "D02")
    assert tray() == from_web
    assert res["staged"] == from_web


@pytest.mark.parametrize("vid,fragment", [
    ("D01", "not PROVISIONAL"),
    ("D99", "unknown vertex"),
])
def test_confirm_refuses_the_same_thing_through_both_doors(srv, reviewed,
                                                           store, vid,
                                                           fragment):
    code, res = post(srv, "/api/confirm", {"vertex": vid})
    assert code == 400, res
    assert fragment in res["error"]
    out = runner.invoke(app, ["--project", str(store), "confirm", vid])
    assert out.exit_code == 1, out.output
    assert fragment in out.output
    assert tray() == []


def test_a_premise_still_under_review_refuses_at_both_doors(srv, store, g):
    """While the premise is unsettled, PROVISIONAL is the *accurate* status,
    and re-affirming would claim a conclusion the graph cannot support."""
    from dataclasses import replace as dc_replace
    graph = Graph.load()
    graph.vertices["D01"] = dc_replace(graph.vertices["D01"],
                                       status="REOPENED")
    graph.vertices["D02"] = dc_replace(graph.vertices["D02"],
                                       status="PROVISIONAL")
    graph.save()
    code, res = post(srv, "/api/confirm", {"vertex": "D02"})
    assert code == 400, res
    assert "still rests on D01" in res["error"]
    out = runner.invoke(app, ["--project", str(store), "confirm", "D02"])
    assert out.exit_code == 1
    assert "still rests on D01" in out.output


def test_a_hand_written_status_is_refused_where_it_is_staged(srv, store, g):
    """The interface audit's F2, closed at the *shared* floor rather than in
    one route. A bare `set_status` posted to the generic staging sink used to
    go into a tray every writer shares and come back as somebody else's refusal
    at apply time, naming a CLI command instead of the box that was ticked.

    `pending.vet` now asks `compose_confirm`'s questions at the moment the op
    arrives, so the two doors refuse in the same breath.
    """
    from dataclasses import replace as dc_replace
    graph = Graph.load()
    graph.vertices["D01"] = dc_replace(graph.vertices["D01"],
                                       status="REOPENED")
    graph.vertices["D02"] = dc_replace(graph.vertices["D02"],
                                       status="PROVISIONAL")
    graph.save()
    code, res = post(srv, "/api/pending",
                     {"op": "set_status", "vertex": "D02", "status": "DECIDED"})
    assert code == 400, res
    assert "still rests on D01" in res["error"]
    assert tray() == []


def test_no_status_but_a_re_affirmation_may_be_written_directly(srv, store):
    """`set_status` is derived: `expand` and `repairs` produce it and stamp
    `derived_from`. An unstamped one is either a re-affirmation or a status
    somebody wrote by hand, and no door of this tool offers the second."""
    code, res = post(srv, "/api/pending",
                     {"op": "set_status", "vertex": "D05", "status": "OPEN"})
    assert code == 400, res
    assert "a status is not set directly" in res["error"]


def test_settling_a_premise_releases_what_was_blocked_on_it(srv, reviewed):
    """Expanded, not bare: settling a vertex releases everything BLOCKED on it,
    and leaving that to the caller is how a block goes stale and `apply`
    refuses the whole batch."""
    code, res = post(srv, "/api/confirm", {"vertex": "D02"})
    assert code == 200, res
    # D06 is BLOCKED:D05, not on D02 — so nothing is released here, and the
    # op list is exactly the one act. The releasing case is `expand`'s, pinned
    # in test_dgraph.py; what matters at this door is that it goes through it.
    assert len(res["staged"]) == 1


def test_the_panel_offers_both_exits_on_a_decision_under_review():
    """It used to offer one, and that one files a reversal that never
    happened. Pinned in the file that holds it."""
    html = _page()
    panel = html.split("function panel()")[1].split("function say(")[0]
    assert 'id="doConfirm"' in panel
    assert 'st==="PROVISIONAL"' in panel
    # And it says which premise is in the way rather than offering a button
    # that refuses.
    assert "provisional_because" in panel


def test_the_page_posts_an_act_not_a_status():
    """No general status control **for decisions**: `set_status` is derived in
    that store, so a browser that could write any status would be a second copy
    of the propagation rules.

    Scoped to the decision half deliberately. A *task* status is not derived —
    started, parked, done and dropped are facts about the work, and the task
    panel posts them directly, which is correct and is why the two stores have
    different verbs at this door.
    """
    html = _page()
    assert '"/api/confirm"' in html
    decisions = html.split("function panel()")[1].split(
        "/* ---- composing in an editor")[0]
    # The op as it would be *written*, not the word: the comment above
    # `stageConfirm` names `set_status` to explain why it does not post one.
    assert 'op:"set_status"' not in decisions


# ---- evidence that landed after the answer -------------------------------


@pytest.fixture
def late(both):
    """A settled D01 whose evidence T01 finished afterwards.

    The shape `cross.evidence_after_deciding` reports and the browser could not
    show: an answer and a measurement that may contradict it, in the store
    together. The answer is backdated rather than the task post-dated, because
    the finding measures the task's `done` against the *edge's* date.
    """
    from dataclasses import replace as dc_replace
    tg = TaskGraph.load(both / "tasks.json")
    tg.tasks["T01"].evidence_for = "D01"
    finished(tg.tasks["T01"], "2026-06-01", "recall 0.91, below target")
    tg.save(both / "tasks.json")
    graph = Graph.load()
    edges = []
    for e in graph.edges:
        edges.append(dc_replace(e, date="2026-01-05")
                     if e.src == "D01" and e.answer else e)
    graph.edges = edges
    graph.save()
    return both


def test_the_late_result_is_visible_in_the_browser(srv, late):
    """`dg check` has reported this since the finding existed; the page showed
    only its benign opposite, so the same store read clean here."""
    r = urllib.request.Request(srv + "/api/joined")
    r.add_header(server.TOKEN_HEADER, server.TOKEN)
    with urllib.request.urlopen(r, timeout=10) as resp:
        joined = json.loads(resp.read())
    rows = joined["by_decision"]["D01"]["late_evidence"]
    assert [t["id"] for t in rows] == ["T01"]
    # The outcome, not just the id: the panel has to show what landed, or the
    # reader cannot judge it without leaving.
    assert rows[0]["outcome"] == "recall 0.91, below target"


def test_the_third_exit_is_reachable_and_clears_the_finding(srv, late):
    code, res = post(srv, "/api/read-evidence", {
        "vertex": "D01", "task": "T01", "note": "the target moved; it stands"})
    assert code == 200, res
    assert bare(res["staged"]) == [{
        "op": "read_evidence", "task": "T01", "against": "D01",
        "note": "the target moved; it stands",
        "date": server._today()}]
    # It is a *task* op staged from a decision panel, and the response says so.
    assert res["tray"] == "tasks"
    assert any("task tray" in n for n in res["notes"])


#: The three things a result that lands after an answer can mean, and the
#: command for each. Before this item the browser could reach only the first —
#: which `dg confirm`'s docstring calls the uncommon one, so the usual outcome
#: was the unreachable one.
EXITS = [
    pytest.param(["reopen", "D01", "--why", "it does not hold", "--yes"],
                 id="it-refutes-the-answer"),
    pytest.param(["task", "unlink", "T01", "--evidence-for"],
                 id="the-answer-never-needed-it"),
    pytest.param(["confirm", "D01", "--against", "T01", "--note", "stands"],
                 id="it-confirms-the-answer"),
]


@pytest.mark.parametrize("args", EXITS)
def test_every_exit_clears_the_finding(srv, late, both, args):
    """One fresh store per exit, rather than one store walked through all
    three: restoring the shape between them was its own source of wrong
    answers, and each exit is an independent claim anyway."""
    from dgraph import cross

    def still_found():
        return bool(cross.late_evidence(
            TaskGraph.load(both / "tasks.json"), Graph.load(), "D01"))

    assert still_found(), "the fixture did not produce the finding"
    cli(both, *args)
    cli(both, "apply")
    assert not still_found()


def test_a_later_result_brings_the_finding_back(srv, late, both):
    """A reading is per result *and* per date: it says this measurement was
    read on this day. Work that finishes afterwards has not been read, so the
    finding returns rather than staying silenced."""
    cli(both, "confirm", "D01", "--against", "T01", "--note", "stands")
    cli(both, "apply")
    from dgraph import cross
    tg = TaskGraph.load(both / "tasks.json")
    assert not cross.late_evidence(tg, Graph.load(), "D01")

    # A second completion, which is what a later result is: the first stays.
    finished(tg.tasks["T01"], "2026-12-01", "re-run, and it does not hold")
    tg.save(both / "tasks.json")
    assert cross.late_evidence(TaskGraph.load(both / "tasks.json"),
                               Graph.load(), "D01")


def test_reading_something_that_is_not_outstanding_is_refused(srv, late):
    code, res = post(srv, "/api/read-evidence", {
        "vertex": "D01", "task": "T02", "note": "x"})
    assert code == 400
    assert "not evidence awaiting a reading" in res["error"]


def test_the_note_is_required_at_both_doors(srv, late, both):
    """Without it the entry records that somebody ran a command, not what they
    found."""
    code, res = post(srv, "/api/read-evidence", {"vertex": "D01",
                                                 "task": "T01"})
    assert code == 400
    assert "needs what it showed" in res["error"]
    out = runner.invoke(app, ["--project", str(both), "confirm", "D01",
                              "--against", "T01"])
    assert out.exit_code == 1
    assert "needs what it showed" in out.output


def test_the_panel_shows_the_late_result_and_offers_the_reading():
    html = _page()
    assert "function lateEvidence" in html
    assert '"/api/read-evidence"' in html
    # Per result, not per decision: the note is about *that* measurement.
    assert "rows.forEach" in html.split("function lateEvidence")[1]


# ---- is the store sound? -------------------------------------------------


@pytest.fixture
def unsound(store, g):
    """A DECIDED vertex under a REOPENED premise, with no reopen ever staged.

    What a merge, a rebase, a partial checkout or a second clone leaves behind:
    `expand` derives PROVISIONAL from a reopen *op*, so a reopen that reached
    the store by any other route leaves its conclusions claiming more than the
    graph supports. It is exactly what `dg repair` exists for.
    """
    from dataclasses import replace as dc_replace
    graph = Graph.load()
    graph.vertices["D01"] = dc_replace(graph.vertices["D01"],
                                       status="REOPENED")
    graph.save()
    return store


def get(base, path):
    r = urllib.request.Request(base + path)
    r.add_header(server.TOKEN_HEADER, server.TOKEN)
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read())


def test_the_browser_can_ask_whether_the_store_is_sound(srv, unsound, store):
    """It could not, so an invalid store looked normal until an unrelated
    Apply was refused for a reason unrelated to what was staged."""
    body = get(srv, "/api/check")
    names = {f["check"] for f in body["stored"]}
    assert "propagation" in names
    # The same findings `dg check` prints, and the same count.
    out = runner.invoke(app, ["--project", str(store), "check"])
    for f in body["stored"]:
        assert f["check"] in out.output


def test_findings_reach_the_page_with_their_remedies_intact(srv, unsound):
    """Printed verbatim. The strings already carry the fix, and a second
    wording in the page is the two doors disagreeing about one store."""
    body = get(srv, "/api/check")
    prop = next(f for f in body["stored"] if f["check"] == "propagation")
    assert "`dg repair`" in prop["message"]
    assert prop["severity"] == "error"
    assert prop["origin"] == "decision"


def test_a_clean_store_says_nothing_at_all(srv, store, g):
    """Quiet when clean — the chip is hidden, so its appearing is the signal.

    `dg render` first, because the generated view counts: a `decision-graph.md`
    that has fallen behind its store is a real finding, and the fixture writes
    only the store.
    """
    cli(store, "render")
    body = get(srv, "/api/check")
    assert body["stored"] == []
    assert body["staged"] == []
    assert body["repairs"] == 0


def test_the_staged_list_is_empty_when_the_tray_is(srv, unsound):
    """With nothing staged the two lists are identical by construction, and
    showing both would make one unsound store look twice as unsound."""
    body = get(srv, "/api/check")
    assert body["stored"] and body["staged"] == []


def test_the_staged_list_answers_what_apply_would_judge(srv, unsound):
    """A different question from "is the record sound": what this batch would
    leave behind, which is what a person needs before staging more."""
    before = get(srv, "/api/check")
    code, res = post(srv, "/api/repair", {})
    assert code == 200, res
    after = get(srv, "/api/check")
    assert after["stored"] == before["stored"], "the record has not moved"
    assert len(after["staged"]) < len(before["stored"]), \
        "the staged batch should leave fewer findings than the store has"


def test_the_staged_list_is_what_check_staged_prints(srv, store, g):
    """One reading through both doors: the page's staged list carries the
    link checks and a tray that will not preview, which judging each
    previewed store on its own could not show."""
    cli(store, "render")
    (store / ".dgraph-pending.json").write_text(json.dumps([
        {"op": "close", "vertex": "D77", "answer": "a", "source": "s",
         "falsifier": "f", "opens": [], "date": "2026-09-03"}]))
    body = get(srv, "/api/check")
    assert body["stored"] == []
    assert [f["check"] for f in body["staged"]] == ["tray_applies"]
    assert body["staged"][0]["origin"] == "decision"
    out = runner.invoke(app, ["--project", str(store), "check", "--staged"])
    assert out.exit_code == 1 and "[tray_applies]" in out.output


def test_repair_stages_the_same_ops_through_both_doors(srv, unsound, store):
    code, res = post(srv, "/api/repair", {})
    assert code == 200, res
    from_web = tray()
    pending.clear()
    cli(store, "repair")
    assert tray() == from_web


def test_repair_with_nothing_to_repair_says_so(srv, store, g):
    """Nothing staged is not the same as staged, here as everywhere else."""
    code, res = post(srv, "/api/repair", {})
    assert code == 200, res
    assert res["staged"] == []
    assert any("nothing to repair" in n for n in res["notes"])


def test_the_chip_is_hidden_until_there_is_something_to_say():
    html = _page()
    assert 'id="soundBtn"' in html and 'style="display:none"' in html
    sound = html.split("async function soundness")[1].split("function findingRows")[0]
    assert 'chip.style.display="none"' in sound


def test_the_page_reprints_findings_rather_than_rewording_them():
    html = _page()
    rows = html.split("function findingRows")[1].split("function soundPanel")[0]
    assert "esc(f.message)" in rows
    assert "esc(f.check)" in rows


# ---- what the agent is told ----------------------------------------------


#: Commands the skill deliberately does not carry, and why. An exception list
#: rather than a silence, because "not in the skill" and "not in the skill *on
#: purpose*" are different facts and only one of them is a bug.
SKILL_EXCEPTIONS = {
    # Reached through `/dg:serve`, which starts it detached and hands over the
    # URL — a slash command rather than a table row, because what an agent does
    # with it is give it to a person.
    "serve",
    # A hook runs it, on every Bash call. An agent never types it, and one that
    # did would be judging its own commit.
    "gate",
}


def test_the_skill_carries_every_command_or_says_why_not():
    """Prose gaps close and reopen. The interface audit found eight at once —
    `dg init` among them, which left an agent asked to start tracking with no
    sentence saying how to create the file the whole skill operates on —
    because nothing was watching.

    `tests/test_cli.py` already checks the two help screens against each other;
    this is the same shape against the third surface.
    """
    from dgraph import cli

    def names(layout):
        # `"a/b"` is two names for **one** command — `cli.LAYOUT`'s own
        # convention, and `dg --help` prints it as one line. Mentioning either
        # tells the agent the command exists, so they are kept together and
        # satisfied together rather than demanded separately.
        return [tuple(name.split("/")) for _, cmds in layout for name in cmds]

    skill = (Path(__file__).resolve().parents[1]
             / "skills" / "dear-guide" / "SKILL.md").read_text(encoding="utf-8")
    commands = (Path(__file__).resolve().parents[1] / "commands")
    slash = "\n".join(p.read_text(encoding="utf-8")
                      for p in commands.glob("*.md"))

    missing = []
    for layout, prefix in ((cli.LAYOUT, "dg "), (cli.TASK_LAYOUT, "dg task ")):
        for aliases in names(layout):
            if set(aliases) & SKILL_EXCEPTIONS or aliases == ("task",):
                continue
            if not any(prefix + a in skill or prefix + a in slash
                       for a in aliases):
                missing.append(prefix + "/".join(aliases))
    assert not missing, (
        f"the agent is never told about: {', '.join(missing)} — add a row to "
        f"SKILL.md, or a reason to SKILL_EXCEPTIONS")


def test_the_exceptions_are_real_commands():
    """An exception list that outlives its command is a silence with a note
    attached, which is worse than no note."""
    from dgraph import cli
    known = {n for _, cmds in cli.LAYOUT for name in cmds
             for n in name.split("/")}
    assert SKILL_EXCEPTIONS <= known


def test_the_skill_tells_an_agent_to_start_work_it_picks_up():
    """`DOING` was a status only a human ever wrote: the skill's worked example
    went add → done, so `dg brief`'s reading of work in progress was empty in
    exactly the sessions where an agent was doing the work."""
    skill = (Path(__file__).resolve().parents[1]
             / "skills" / "dear-guide" / "SKILL.md").read_text(encoding="utf-8")
    assert "dg task start" in skill
    # And says what it buys, not only that it exists — an instruction with no
    # reason attached is the one that gets dropped under context pressure.
    reason = skill.split("**Mark work in flight when you pick it up**")[1][:900]
    assert "dg brief" in reason


def test_the_skill_says_how_to_start_a_graph():
    skill = (Path(__file__).resolve().parents[1]
             / "skills" / "dear-guide" / "SKILL.md").read_text(encoding="utf-8")
    assert "dg init" in skill and "dg task init" in skill
    # The model fact the skill states about the graphs and never connected to
    # the commands that make them.
    assert "either works without the other" in skill


# ---- the editor buffer says only what it can do ---------------------------


#: The four buffers `dg` opens, and whether each names a vertex the elisp can
#: walk from. `dg decide` and `dg reopen` do; `dg add` is composing one that
#: does not exist yet, and the two task buffers belong to a store
#: `dgraph.el` cannot read at all — `dgraph-readonly-commands` is `("export")`
#: and the guard tests the first argument, so `dg task export` is refused as
#: `dg task`.
BUFFERS = [
    pytest.param("close", True, id="dg-decide"),
    pytest.param("reopen", True, id="dg-reopen"),
    pytest.param("add_vertex", False, id="dg-add"),
    pytest.param("add_task", False, id="dg-task-add"),
    pytest.param("task_done", False, id="dg-task-done"),
]


#: The two key shapes a header can carry, matching `dgraph--advertised-keys`.
_KEY_RE = re.compile(r"C-c (?:C-[a-z]|d [a-z])")


def _keys(*fragments):
    """The keys named in a header fragment, read off `editor.py` itself.

    Derived rather than restated. Before this the vocabulary lived in three
    places — `dgraph-prefix-keys`, the header constants, and a row of string
    literals in this file — and only the first two were checked against each
    other, so the copy here could say anything.
    """
    from dgraph import editor
    return {k for f in fragments for k in _KEY_RE.findall(getattr(editor, f))}


WALK_KEYS = _keys("_KEYS_WALK")
VISIT_KEYS = _keys("_KEYS_VISIT")
#: What may legitimately sit in org's own `C-c C-<letter>` namespace.
MODE_CTRL_KEYS = _keys("_HEADER", "_KEYS_ALWAYS")


def _buffer(kind, root):
    from dgraph import editor as ed, task_editor as ted
    g, tg = Graph.load(), TaskGraph.load(root / "tasks.json")
    return {
        "close": lambda: ed.render_close(g, "D01"),
        "reopen": lambda: ed.render_reopen(g, "D01"),
        "add_vertex": lambda: ed.render_add(g),
        "add_task": lambda: ted.render_add(tg, g),
        "task_done": lambda: ted.render_done(tg, g, "T01"),
    }[kind]()


@pytest.mark.skipif(shutil.which("emacs") is None, reason="emacs not installed")
@pytest.mark.parametrize("kind,walkable", BUFFERS)
def test_a_buffer_advertises_only_keys_that_work_in_it(both, kind, walkable):
    """Interface audit F8. Three of the four buffer kinds printed
    `C-c C-p parent · C-c C-a ancestors` in their own header and both errored —
    the header being the only documentation those keys have.

    The assertion is deliberately wider than the bug: **every** `C-c` key the
    header names is looked up and, where it is a walk key, actually run. A new
    buffer kind either offers the keys or stops naming them; it cannot do
    neither, and it cannot do both.
    """
    buf = both / ".dgraph-edit.org"
    buf.write_text(_buffer(kind, both), encoding="utf-8")
    r = subprocess.run(
        ["emacs", "-batch",
         "-l", str(Path(__file__).resolve().parents[1]
                   / "dgraph" / "elisp" / "dgraph.el"),
         "-l", str(Path(__file__).resolve().parent
                   / "fixtures" / "advertised_keys.el"),
         str(buf)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    advertised = set(_KEY_RE.findall(r.stdout.strip().splitlines()[-1]))

    # The two gates are independent, and the expected sets come from
    # `editor.py` rather than being spelled out again here — the vocabulary was
    # written down three times before this (the keymap, the header, and a row
    # of literals in this file), and the third copy agreed with nothing.
    assert (WALK_KEYS <= advertised) is walkable, advertised
    # `both` has a decision store, so every kind offers `visit` — including the
    # two that are not walkable, which is the whole point of the second gate:
    # a buffer with no premise to walk to is where looking one up is worth
    # most, and it was the buffer that did not offer it.
    assert VISIT_KEYS <= advertised, advertised
    if not walkable:
        assert not WALK_KEYS & advertised, advertised

    # The navigation keys live under `C-c d` so that org keeps its own
    # `C-c C-<letter>` namespace — `C-c C-v` alone is org-babel's whole prefix
    # map. Only the keys the header itself names may appear there: the two
    # commit-buffer keys, argued for in `dgraph-edit-mode-map`, and `C-c C-o`,
    # which is org's own `org-open-at-point` reached through the `dg:` link
    # type and therefore works in the task buffers too.
    assert {k for k in advertised if k.startswith("C-c C-")} == MODE_CTRL_KEYS


@pytest.mark.skipif(shutil.which("emacs") is None, reason="emacs not installed")
def test_a_tasks_only_project_offers_no_navigation_at_all(task_store):
    """`visit` needs a decision store; a project tracking only work has none.

    The other half of the gate above. `dgraph.el` reaches decisions through
    `dg export`, which fails here, so advertising the key would bind it to an
    error — interface audit F8's shape, arrived at from the opposite side.
    """
    from dgraph.tasks import TaskGraph
    from dgraph import task_editor as ted

    buf = task_store / ".dgraph-edit.org"
    buf.write_text(ted.render_add(TaskGraph.load(task_store / "tasks.json"),
                                  None), encoding="utf-8")
    r = subprocess.run(
        ["emacs", "-batch",
         "-l", str(Path(__file__).resolve().parents[1]
                   / "dgraph" / "elisp" / "dgraph.el"),
         "-l", str(Path(__file__).resolve().parent
                   / "fixtures" / "advertised_keys.el"),
         str(buf)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    advertised = set(_KEY_RE.findall(r.stdout.strip().splitlines()[-1]))
    assert not (WALK_KEYS | VISIT_KEYS) & advertised, advertised
    assert advertised == MODE_CTRL_KEYS, advertised


@pytest.mark.skipif(shutil.which("emacs") is None, reason="emacs not installed")
def test_the_error_names_the_buffer_rather_than_denying_it_is_one(both):
    """"not composing a decision" was false in a `dg add` buffer — which is
    composing one — and unhelpful in a task buffer, which is composing
    something this file has no way to show."""
    buf = both / ".dgraph-edit.org"
    buf.write_text(_buffer("add_vertex", both), encoding="utf-8")
    r = subprocess.run(
        ["emacs", "-batch",
         "-l", str(Path(__file__).resolve().parents[1]
                   / "dgraph" / "elisp" / "dgraph.el"),
         "--eval", f'(progn (find-file "{buf}")'
                   f' (condition-case e (dgraph-parent)'
                   f'   (error (princ (error-message-string e)))))'],
        capture_output=True, text=True)
    assert "add_vertex" in r.stdout
    assert "no premises to walk" in r.stdout
    assert "not composing a decision" not in r.stdout


# ---- the trays --------------------------------------------------------


def test_a_tray_can_be_emptied_and_says_how_much_went(srv, store, g):
    """Eight staged ops abandoned used to mean eight clicks. The count comes
    back because the tray is shared: clearing can discard something a terminal
    staged a minute ago, and the number is the only warning that says so."""
    post(srv, "/api/add", {"id": "D07", "title": "a", "area": "Alpha"})
    post(srv, "/api/add", {"id": "D08", "title": "b", "area": "Alpha"})
    assert len(tray()) == 2
    code, res = delete(srv, "/api/pending")
    assert code == 200, res
    assert res["cleared"] == 2
    assert tray() == []


def test_each_tray_is_cleared_on_its_own(srv, both):
    """Two stores, two batches, all the way through — `dg apply` treats them
    independently and so does this."""
    post(srv, "/api/add", {"id": "D07", "title": "a", "area": "Alpha"})
    post(srv, "/api/add-task", {"id": "T07", "title": "b", "area": "Alpha"})
    delete(srv, "/api/task-pending")
    assert tray(task_pending.path()) == []
    assert len(tray()) == 1


def test_clear_stages_the_same_outcome_as_the_command(srv, store, g):
    post(srv, "/api/add", {"id": "D07", "title": "a", "area": "Alpha"})
    delete(srv, "/api/pending")
    from_web = tray()
    post(srv, "/api/add", {"id": "D07", "title": "a", "area": "Alpha"})
    cli(store, "clear")
    assert tray() == from_web == []


def test_a_derived_op_is_not_revisable(srv, store, g):
    """`render_op` refuses one and so does this: a derived op has no buffer,
    and the ✕ is what removes it."""
    from dataclasses import replace as dc_replace
    graph = Graph.load()
    graph.vertices["D01"] = dc_replace(graph.vertices["D01"],
                                       status="REOPENED")
    graph.save()
    post(srv, "/api/repair", {})
    ref = pending.load()[0]["ref"]
    code, res = post(srv, "/api/edit", {"ref": ref})
    assert code == 400
    assert "derived, not composed" in res["error"]


def test_revising_an_unknown_op_is_refused(srv, store, g):
    code, res = post(srv, "/api/edit", {"ref": "nope"})
    assert code == 400


def test_the_tray_offers_a_clear_and_a_revise():
    html = _page()
    trayfn = html.split("function tray()")[1].split("function confirmClear")[0]
    assert "data-clear" in trayfn
    # The count in the button, not behind it.
    assert "clear ${n}" in html
    assert "data-edit" in html.split("function opRow")[1]


def test_clearing_asks_in_the_panel_not_through_a_modal():
    """A browser modal blocks every subsequent event, and this question is
    answerable with the count in front of you."""
    html = _page()
    assert "function confirmClear" in html
    assert "confirm(" not in html.split("function confirmClear")[1][:800]


# ---- the readings the page did not have ----------------------------------


def test_the_chain_comes_from_the_function_the_cli_uses(srv, store, g):
    """Three readings is the size at which a page starts deriving its own
    answers, and a browser computing a premise chain differently from
    `dg context` is exactly the drift this tool exists to catch."""
    from dgraph import context as ctx
    body = get(srv, "/api/context?id=D05")
    assert [r["id"] for r in body["chain"]] == [
        p.id for p in ctx.chain(Graph.load(), "D05")]


def test_the_chain_carries_the_reading_and_not_only_the_rows(srv, store, g):
    """The thing `dg context` exists to say is whether anything in the chain is
    still under review — an answer resting on one is a bet, not a conclusion.
    A list of premises with that left out is the neighbourhood again."""
    from dataclasses import replace as dc_replace
    graph = Graph.load()
    graph.vertices["D02"] = dc_replace(graph.vertices["D02"],
                                       status="REOPENED")
    graph.save()
    body = get(srv, "/api/context?id=D05")
    assert "D02" in body["shaky"]


def test_a_chain_for_something_that_is_not_there(srv, store, g):
    assert "unknown decision" in get(srv, "/api/context?id=D99")["error"]


def test_the_path_is_the_one_the_command_prints(srv, store, g):
    body = get(srv, "/api/path?from=D01&to=D05")
    assert [n["id"] for n in body["path"]] == Graph.load().path("D01", "D05")
    # The step, not the whole answer: what carried the reasoning to the next
    # node. The last node has none, because nothing follows it.
    assert body["path"][-1]["because"] is None
    assert body["path"][0]["because"]


def test_no_path_says_so_rather_than_returning_nothing(srv, store, g):
    body = get(srv, "/api/path?from=D05&to=D01")
    assert body["path"] == []
    assert "no decision path" in body["error"]


def test_areas_are_two_blocks_never_one_table(srv, both):
    """The stores share their areas and not their vocabularies: a row summing
    OPEN with TODO counts questions and work as if they were the same thing."""
    body = get(srv, "/api/areas")
    assert set(body["decisions"]["counts"]) <= set(body["decisions"]["areas"])
    assert set(body["tasks"]["counts"]) <= set(body["tasks"]["areas"])
    dec = {s for c in body["decisions"]["counts"].values() for s in c}
    tsk = {s for c in body["tasks"]["counts"].values() for s in c}
    assert not (dec & tsk), "the two vocabularies must not meet"


def test_areas_totals_match_the_stores(srv, both):
    body = get(srv, "/api/areas")
    total = sum(n for c in body["decisions"]["counts"].values()
                for n in c.values())
    assert total == len(Graph.load().vertices)


def test_a_store_that_is_absent_is_null_not_empty(srv, store, g):
    """"this project does not track work" and "this project has no work" are
    different facts, and the page has to be able to say which."""
    body = get(srv, "/api/areas")
    assert body["tasks"] is None
    assert body["decisions"] is not None


def test_the_page_asks_the_server_for_all_three(srv):
    html = _page()
    for route in ('"/api/context?id="', '`/api/path?from=', '"/api/areas"'):
        assert route in html, route


#: Every `data-<hook>` the page interpolates a value into. An attribute is only
#: a control once something on the other side reads it — by selector
#: (`[data-x]`) or off the element it already holds (`dataset.x`) — and
#: `data-chain` had neither: `why…` was drawn beside every premise, `showChain`
#: sat in the file as dead code, and the link did what an unbound `href="#"`
#: does. Nothing failed; the reading was simply never reachable.
def _rendered_hooks(html):
    return {m.group(1) for m in re.finditer(r'data-([a-z]+)="\$\{', html)}


def test_every_hook_the_page_draws_is_read_somewhere():
    """The route being reachable is not the reading being reachable. Each of
    the three readings has a route test above; this asks the other half."""
    html = _page()
    assert "chain" in _rendered_hooks(html), "the premise chain lost its door"
    for hook in _rendered_hooks(html):
        assert f"[data-{hook}]" in html or f"dataset.{hook}" in html, (
            f"the page draws data-{hook} and nothing ever reads it — "
            f"the control is decoration")


def test_boot_publishes_the_graph_and_its_layout_together():
    """`boot()` runs again after Apply and after an `add`, over a canvas whose
    nodes are already drawn and already bound to `select`. While it is fetching,
    every one of those boxes is still clickable — so a payload published before
    the layout for it exists is a window in which a click reads coordinates
    that belong to the previous graph, or to nothing.

    That window was six awaits wide and threw `xyOf` on a click landing inside
    it. Stated as the property rather than the symptom: nothing reaches the
    globals until the fetches are done, and no `await` separates publishing
    them from laying them out.
    """
    body = _page().split("async function boot(){")[1].split("\n}")[0]
    for name in ("G", "T", "JOIN", "PEND", "TPEND"):
        assert f"{name} = await" not in body and f"{name}=await" not in body, (
            f"boot publishes {name} mid-flight — the canvas is clickable "
            f"while the rest of the payloads are still in the air")
    published, laid_out = body.index("G = g;"), body.index("layout();")
    assert published < laid_out < body.index("draw();")
    assert "await" not in body[published:laid_out], (
        "an await between publishing the graph and laying it out reopens "
        "the window")


AREAS_HARNESS = r"""
const src = require("fs").readFileSync(process.argv[2], "utf8");
const js = src.slice(src.indexOf("<script>") + 8, src.lastIndexOf("</script>"));
const cut = (from, to) => js.slice(js.indexOf(from), js.indexOf(to));
// The page's own `esc`, not a stub of it — that substitution is the whole
// reason this reading could throw with every other test passing. `const`
// inside `eval` binds only inside it, so both are re-bound as globals.
let esc, showAreas;
eval(cut("const esc=s=>String(", "const isRule=").replace("const esc=", "esc="));
eval(cut("async function showAreas(){", "function panel(){")
       .replace("async function showAreas(){", "showAreas = async function(){"));
const R = JSON.parse(process.argv[3]);
const side = {innerHTML:""};
const $ = s => side;
let sel = null, tsel = null, NEWKIND = null, RELATING = null;
const draw = () => {}, say = () => {};
const api = async () => R;
showAreas().then(() => {
  const h = side.innerHTML;
  ["decisions", "work"].forEach(b => {
    if (!h.includes(">" + b + "<")) throw new Error("no " + b + " block");
  });
  Object.entries(R).forEach(([, store]) => store && store.areas.forEach(a => {
    if (!h.includes(">" + a + "<")) throw new Error("area missing: " + a);
    Object.values(store.counts[a] || {}).forEach(n => {
      if (!h.includes(">" + n + "<")) throw new Error("count missing: " + n);
    });
  }));
  console.log(h.replace(/<[^>]+>/g, " "));
}).catch(e => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_areas_reading_renders_its_counts(both, tmp_path):
    """Rendered with the page's **own** `esc`, which is the part that broke.

    `FORM_HARNESS` stubs `esc` as a coercion, so the areas table passed every
    test there and threw in a browser: the real one calls `.replace`, and the
    cells it is handed are counts, not strings. A harness that stubs the very
    function under suspicion cannot see that, so this one cuts the real
    `esc` out of the page and runs the table against a real `/api/areas`
    payload — two blocks, both stores' counts, nothing thrown.
    """
    harness = tmp_path / "areas.js"
    harness.write_text(AREAS_HARNESS, encoding="utf-8")
    r = subprocess.run(
        ["node", str(harness), str(server.STATIC / "app.html"),
         json.dumps(server.areas_payload())],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    # The harness raises on a missing area or count; this is the other half of
    # the claim the reading makes — two blocks, never one table.
    assert "decisions" in r.stdout and "work" in r.stdout


# ---- correcting the wording (audit F-F6) ---------------------------------
#
# `set_fields` is the op that had no door at all until this finding: no command
# retitled an applied record, so the browser could not either, and the only
# route was editing the JSON. Both doors reach it now, and both go through
# `pending.vet_fields` — the browser posts the op as data, so a rule the CLI
# ran and `vet` did not would not be a rule.


def test_amend_stages_the_same_op_through_both_doors(srv, store):
    body = {"op": "set_fields", "vertex": "D05", "title": "Which shard count?"}
    code, res = post(srv, "/api/pending", body)
    assert code == 200, res
    from_web = tray()
    pending.clear()

    cli(store, "amend", "D05", "--title", "Which shard count?")
    assert tray() == from_web


def test_the_browser_is_refused_what_the_command_is_refused(srv, store):
    """Same three refusals, same wording, from `pending.vet_fields`."""
    for body, says in (
            ({"op": "set_fields", "vertex": "D05"}, "nothing to change"),
            ({"op": "set_fields", "vertex": "D05", "title": " "},
             "needs a title"),
            ({"op": "set_fields", "vertex": "D05", "area": "alpha"},
             "close to areas already in use"),
            ({"op": "set_fields", "vertex": "D05", "answer": "not this one"},
             "not amended in answer"),
    ):
        code, res = post(srv, "/api/pending", body)
        assert code == 400 and says in res["error"], (body, res)
        assert tray() == []


def test_the_page_offers_the_correction_on_both_stores():
    """One form in the file, drawn on both panels. Two would be two sets of
    rules a page away from disagreeing, which is what `_fields_detail` and
    `vet_fields` exist to prevent on the other two surfaces."""
    src = (server.STATIC / "app.html").read_text(encoding="utf-8")
    assert src.count("function amendForm(") == 1
    assert src.count("amendForm(") == 3          # the definition and both panels
    assert 'amendOp("vertex"' in src and 'amendOp("task"' in src
    # Prefilled as placeholders, never as values: `captureDraft` treats a
    # non-empty input as unconfirmed work, so a form carrying the current title
    # would make every node somebody merely looked at into a draft.
    assert 'id="amTitle" placeholder=' in src
    assert 'id="amTitle" value=' not in src


# ---- an exhausted grant, at the door that cannot refuse (F-F3) ------------


def test_a_used_up_grant_does_not_take_the_payload_down_with_it(srv, store):
    """The page is still worth drawing. Every reading it offers still holds and
    only the one form that prefills an id cannot be filled, so the fault
    travels beside the id rather than as a 500 — the same distinction `dg add`
    makes in a terminal between *this graph cannot be read* and *this clone
    cannot allocate*."""
    from dgraph import ranges, server as srv_mod
    ranges.save({"D": ranges.Grant(50, 51, 51)}, store)
    try:
        d = srv_mod.graph_payload(Graph.load(store / "decisions.json"))
        assert d["next_id"] is None
        assert "used up" in d["next_id_fault"]
        assert d["vertices"] and d["frontier"] is not None   # still a graph
    finally:
        ranges.save({}, store)


def test_the_page_says_why_the_id_could_not_be_prefilled():
    """A blank id field with no explanation is the failure this replaces: the
    form looks broken rather than the clone looking out of ids."""
    src = (server.STATIC / "app.html").read_text(encoding="utf-8")
    assert src.count("function idFault(") == 1
    assert src.count("${idFault(") == 2          # both new-record forms
    assert "next_id_fault" in src


def test_a_declined_answer_reaches_the_browser_as_its_own_kind(srv, store, g):
    """Never folded in with the superseded ones. A reversal says the project
    changed its mind; this says somebody else answered the same question and
    it was not taken. Drawn as a reversal it would claim the first about a
    record where only the second happened."""
    out = pending.apply_all(g, [
        {"op": "reject", "vertex": "D01", "answer": "the other way",
         "source": "bench/b.md", "from_source": "worker-a"}])
    out.save(store / "decisions.json")
    payload = server.graph_payload(Graph.load(store / "decisions.json"))
    node = payload["derived"]["D01"]
    assert node["declined"] and node["declined"][0]["from_source"] == "worker-a"
    assert not [h for h in node["history"] if h.get("answer") == "the other way"]

    src = (server.STATIC / "app.html").read_text(encoding="utf-8")
    assert src.count("function declinedCard(") == 1
    assert "d.declined" in src


# ---- `$DG_TERSE`, at the one door ----------------------------------------
#
# The rule is judged in `pending.stage_all` rather than in either store's
# `vet`, so what has to be pinned is that it really is one door: a launcher's
# rule that the browser, or the task store, did not consult is not a rule.


TERSE_LONG = "x " * 300


def test_the_cli_refuses_a_verbose_decision_and_stages_nothing(store, monkeypatch):
    monkeypatch.setenv("DG_AGENT", "brisk-beacon")
    monkeypatch.setenv("DG_TERSE", "on")
    r = runner.invoke(app, ["--project", str(store), "decide", "D05",
                            "--answer", TERSE_LONG, "--source", "bench/x.md",
                            "--falsifier", "it moves"])
    assert r.exit_code == 1
    assert "nothing staged" in r.output
    # The property the stage-time guards were actually for: the tray is shared,
    # so a refusal that left half a batch in it would wedge every other writer.
    assert pending.load() == []


def test_the_same_rule_reaches_the_task_store(both, monkeypatch):
    """One door, both trays. `dg task done` never passes through
    `pending.vet`, so a guard written into that would have missed it."""
    monkeypatch.setenv("DG_AGENT", "brisk-beacon")
    monkeypatch.setenv("DG_TERSE", "on")
    cli(both, "task", "start", "T02")
    before = pending.load(task_pending.path())
    r = runner.invoke(app, ["--project", str(both), "task", "done", "T02",
                            "--outcome", TERSE_LONG])
    assert r.exit_code == 1 and "nothing staged" in r.output
    # Unchanged, not empty: the claim staged above is still there, which is the
    # point — a refusal must not take anything else out of a shared tray.
    assert pending.load(task_pending.path()) == before


def test_the_same_rule_reaches_the_browser(store, monkeypatch):
    """The API is the other way an agent reaches the graph. A policy the CLI
    held alone would be the way around itself — the argument `server.stage`
    already makes about `$DG_DECIDE`."""
    monkeypatch.setenv("DG_AGENT", "brisk-beacon")
    monkeypatch.setenv("DG_TERSE", "on")
    with pytest.raises(pending.ApplyError):
        server.stage(Graph.load(), {"op": "close", "vertex": "D05",
                                    "answer": TERSE_LONG, "source": "x.md",
                                    "falsifier": "it moves", "to": [],
                                    "date": "2026-08-28"})
    assert pending.load() == []


def test_a_supervisor_at_the_same_door_is_not_refused(store, monkeypatch):
    """The value that makes this a launcher's rule rather than a house style."""
    monkeypatch.delenv("DG_AGENT", raising=False)
    monkeypatch.setenv("DG_TERSE", "on")
    r = runner.invoke(app, ["--project", str(store), "decide", "D05",
                            "--answer", TERSE_LONG, "--source", "bench/x.md",
                            "--falsifier", "it moves"])
    assert r.exit_code == 0, r.output
    assert pending.load()


# ---- R-F7 · the tray says what an area is, and whether it is new -----------
#
# `opRow` had no `add_vertex` branch and fell through to `JSON.stringify(o)`,
# so the browser's failure and the CLI's were one fact told two ways: no
# surface that reviews staged work said what a record is filed under. That
# matters because `refuse_area` is the only other thing standing between a
# fan-out and a fragmented vocabulary, and it decides from two strings — it
# cannot see a sub-area at all. A person can, given the fact.
#
# Run in node against the real `opRow`, not against the payload: a renderer
# that was never exercised with an `add_vertex` is `B-F1`'s shape, and a test
# at the HTTP layer would not have seen it.

OPROW_HARNESS = r"""
const src = require("fs").readFileSync(process.argv[2], "utf8");
const js = src.slice(src.indexOf("<script>") + 8, src.lastIndexOf("</script>"));
// The real blocks, cut at their own comment banners: `knownAreas` where the
// page actually declares it, then `fieldsOf` and `opRow`. Stubbing
// `knownAreas` would be shape 14 — the whole question is whether the row knows
// an area is new, and a stub answers it for free.
//
// **Cutting the page is why this harness once passed against a broken page.**
// An earlier version pulled a second `knownAreas` declared beside `opRow`,
// which in the browser was shadowing the real one 1150 lines above and
// breaking every add form and side panel. One cut cannot see two declarations
// in different cuts, so `test_no_top_level_function_is_declared_twice_in_the_page`
// reads the whole script and is what actually guards that.
const cut = (from, to) => js.slice(js.indexOf(from), js.indexOf(to));
const block = cut("/* Every area either store knows.", "function newDecisionForm(){")
            + cut("/* What a `set_fields` op writes,",
                  "/* Rows carry the index they had");
const G = JSON.parse(process.argv[3]), T = JSON.parse(process.argv[4]);
const esc = x => String(x == null ? "" : x);
const ED = null;
eval(block);
const out = JSON.parse(process.argv[5]).map((o, i) => opRow(o, i, "d"));
console.log(JSON.stringify(out));
"""


def _oprow(tmp_path, ops, areas_declared=("corpus",)):
    """Render staged ops through the page's own `opRow`."""
    harness = tmp_path / "oprow.js"
    harness.write_text(OPROW_HARNESS)
    g = {"areas": list(areas_declared), "vertices": [], "edges": [],
         "derived": {}}
    res = subprocess.run(
        ["node", str(harness), str(server.STATIC / "app.html"),
         json.dumps(g), json.dumps(None), json.dumps(ops)],
        capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_browser_names_the_area_and_marks_a_new_one(tmp_path):
    """The row states the fact. A known area is shown; an unknown one is
    marked, because that is the one somebody might want to look at."""
    rows = _oprow(tmp_path, [
        {"op": "add_vertex", "id": "D09", "title": "Theirs",
         "area": "corpus-design", "ref": "abcd", "saw": {}, "by": "scout"},
        {"op": "add_vertex", "id": "D10", "title": "Mine", "area": "corpus"},
    ])
    assert "corpus-design — new" in rows[0]
    assert "corpus-design" in rows[0] and "fresh-area" in rows[0]
    assert "corpus" in rows[1] and "new" not in rows[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_browser_no_longer_dumps_the_op_as_json(tmp_path):
    """The failure this closed, asserted as itself.

    The fallthrough put `saw`, `ref` and `by` into a cell built for a sentence,
    which is how the area came to be *technically* visible and unreadable. A
    branch that regressed to the dump would put them back.
    """
    row = _oprow(tmp_path, [
        {"op": "add_vertex", "id": "D09", "title": "Theirs", "area": "corpus",
         "ref": "abcd", "saw": {"D01": "x"}, "by": "agile-azimuth"}])[0]
    for bookkeeping in ('"saw"', '"ref"', '"by"', '"op"'):
        assert bookkeeping not in row, f"the op is still dumped: {row}"
    assert "Theirs" in row


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_browser_reads_set_fields_off_the_op(tmp_path):
    """`set_fields` had no branch either, and the fix must not hard-code the
    field list — a copy of `pending.FIELDS` in the page is shape 8, and the
    first draft of it guessed a field `set_fields` cannot write."""
    row = _oprow(tmp_path, [
        {"op": "set_fields", "vertex": "D01", "area": "corpora",
         "ref": "abcd", "by": "scout"}])[0]
    assert "area corpora" in row
    assert '"ref"' not in row and "scout" not in row


def test_no_top_level_function_is_declared_twice_in_the_page():
    """The page is one classic `<script>`, so a duplicate declaration wins
    silently and breaks every caller of the one it shadowed.

    That happened: a second `knownAreas()` returning a `Set` was added beside
    the tray listing, 1150 lines below the one returning an Array, and it took
    over — `areaField`'s `known.map(...)` and `amendForm`'s `areas.map(...)`
    then threw on every add form and every side panel. No test saw it, because
    the harnesses `eval` one cut of the file at a time and the two declarations
    are in different cuts. This reads the whole script, which is the only way
    the collision is visible at all.
    """
    src = (server.STATIC / "app.html").read_text(encoding="utf-8")
    js = src[src.index("<script>") + 8:src.rindex("</script>")]
    names = re.findall(r"^function ([A-Za-z_$][\w$]*)\s*\(", js, re.M)
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert not dupes, f"declared more than once at top level: {dupes}"


def test_a_batched_new_area_is_stripped_from_every_op(tmp_path, monkeypatch):
    """`new_area` is a carrier and must never reach the tray, on any op.

    The first version of this read `any(o.pop(...) for o in ops)`, and `any`
    stops at the first truthy value — so the op that granted the permission was
    cleaned and every op behind it kept the field, which `_with_refs` spreads
    straight into the staged record. Latent today (no shipped view batches two
    `add_task`s carrying it) and a broken invariant either way: `apply` never
    rechecks an area, so a permission on a record in a shared tray is one
    writer's answer applied by another.
    """
    monkeypatch.setattr(project, "_override", tmp_path)
    (tmp_path / "tasks.json").write_text(json.dumps(
        {"areas": ["Alpha"], "tasks": [], "edges": []}))
    from dgraph.tasks import TaskGraph
    ops = [{"op": "add_task", "id": f"T0{i}", "title": "x",
            "area": "Provenance", "status": "TODO", "new_area": True}
           for i in (1, 2, 3)]
    server.stage_tasks(TaskGraph.load(tmp_path / "tasks.json"), ops)

    for op in ops:
        assert "new_area" not in op, f"not popped: {op}"
    for staged in pending.load(task_pending.path()):
        assert "new_area" not in staged, f"reached the tray: {staged}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_browser_reads_a_reprobe_and_a_bind_off_the_op(tmp_path):
    """The three ops the domains scaffold added fell through to the JSON
    dump — `R-F7`'s shape, one pass later (audit `N-F5`)."""
    rows = _oprow(tmp_path, [
        {"op": "reprobe", "vertex": "D05",
         "probe": {"kind": "prose.rule", "args": {"n": 1}},
         "date": "2026-02-02", "ref": "abcd", "by": "scout"},
        {"op": "bind", "vertex": "D05",
         "binds": [{"kind": "rocq.constant", "ref": "X"}], "ref": "abce"},
        {"op": "unbind", "task": "T02",
         "binds": [{"kind": "rocq.file", "ref": "a.v"}], "ref": "abcf"},
    ])
    assert "prose.rule" in rows[0] and '{"n":1}' in rows[0]
    assert "+ rocq.constant:X" in rows[1]
    assert "✗ rocq.file:a.v" in rows[2]
    for row in rows:
        for bookkeeping in ('"ref"', '"by"', '"op"'):
            assert bookkeeping not in row, f"the op is still dumped: {row}"


def test_the_panels_draw_the_four_new_fields():
    """The node panels name every field `dg node` prints: rule, done when,
    binds, and the probe with its date (audit `N-F5`). A field × surface
    matrix, read off the source; the browser check is recorded under
    *checked and sound*."""
    html = (server.STATIC / "app.html").read_text(encoding="utf-8")
    panel = html.split("function panel(){")[1].split("\nfunction ")[0]
    task = html.split("function taskPanel(){")[1].split("\nfunction ")[0]
    edge = html.split("function edgeCard(")[1].split("\nfunction ")[0]
    for field in ("v.rule", "v.binds", "v.probes"):
        assert field in panel, field
    for field in ("x.done_when", "x.binds", "x.probes"):
        assert field in task, field
    assert "e.probe" in edge
