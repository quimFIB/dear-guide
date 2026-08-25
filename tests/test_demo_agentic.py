"""The agentic demo must keep showing what it says it shows.

`demo-agentic/` is five claims about what happens when two writers meet, and
every one of them is a quotation from `dg`. That makes it fragile in a way
`demo/` is not: `demo/`'s prose describes a *store*, which changes when someone
edits a JSON file, while this demo's prose describes *messages*, which change
when someone rewords an exception. The reword is the likely event and it leaves
every scene still running, still exiting zero, and quietly no longer
demonstrating the thing its README quotes.

So the assertions here are the quotations. Each scene is run for real -- two
clones, a push, a pull, an apply -- and its transcript is searched for the lines
the README prints. Rewording `pending.already()` fails this test, which is the
point: the message and the paragraph explaining it are one artefact, and this is
the only thing holding them together.

Two things are deliberately *not* asserted:

- **The narration.** A demo whose sentences are pinned is a demo nobody edits;
  `tests/test_demo.py` gives the same reason.
- **Exit status alone.** Every scene exits zero even when it demonstrates
  nothing, because a refused `dg apply` is a successful demo of a refusal. Only
  the transcript can tell the difference.
"""

import shutil
import subprocess

import pytest

DEMO = pytest.importorskip("pathlib").Path(__file__).resolve().parent.parent / "demo-agentic"


def run(scene, tmp_path):
    """One scene, in its own work directory, with its transcript captured."""
    env = {**__import__("os").environ,
           "DG_DEMO_DIR": str(tmp_path / "work"),
           # The scenes commit, and a developer's global git config may set
           # anything from a signing key to a default branch. `git_commit`
           # passes identity per-commit; this covers the rest.
           "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
           "GIT_CONFIG_SYSTEM": "/dev/null"}
    (tmp_path / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n")
    proc = subprocess.run(["bash", str(DEMO / "scenes" / f"{scene}.sh")],
                          capture_output=True, text=True, env=env, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


@pytest.fixture(scope="module", autouse=True)
def _dg_installed():
    if shutil.which("dg") is None:
        pytest.skip("dg is not on PATH")


def test_the_store_is_the_shape_every_scene_assumes(tmp_path):
    """One premise, one child per agent — the smallest shape that can collide.

    Spelled out rather than counted, because the shape is load-bearing: a
    second premise, or a third child, and half the scenes stop being about two
    agents meeting over one thing.
    """
    from dgraph.model import Graph
    from dgraph.tasks import TaskGraph

    g = Graph.load(DEMO / "decisions.json")
    tg = TaskGraph.load(DEMO / "tasks.json")

    assert set(g.vertices) == {"D01", "D02", "D03"}
    assert g.vertices["D01"].base_status == "DECIDED"
    assert {v: g.vertices[v].base_status for v in ("D02", "D03")} == {
        "D02": "OPEN", "D03": "OPEN"}
    assert sorted(g.depends("D02")) == sorted(g.depends("D03")) == ["D01"]

    # Both children carry the evidence link, and T02 is already DONE: that is
    # what makes the opening `dg check` hand agent A its assignment.
    assert {t.evidence_for for t in tg.tasks.values()} == {"D02", "D03"}
    assert tg.tasks["T02"].status == "DONE"


def test_d01_carries_the_falsifier_the_whole_demo_turns_on(tmp_path):
    """`D01`'s falsifier is the plot. Scene 3 is unreadable without it."""
    from dgraph.model import Graph
    edge = Graph.load(DEMO / "decisions.json").active_edge("D01")
    assert edge is not None and edge.decided
    assert "GPU budget" in edge.falsifier      # the event B's reopen cites


def test_scene1_shows_one_tray_with_two_authors(tmp_path):
    """B's reopen reasons over A's unapplied op, B's apply publishes it, and A
    is told nothing until it tries to write."""
    out = run(1, tmp_path)
    # B's reopen describes A's staged work as an existing consequence.
    assert "1 decided descendant(s) rest on it and become PROVISIONAL" in out
    # Three ops, two authors, one tray.
    assert "STAGED  3 op(s)" in out
    assert "applied 3 op(s)" in out
    # A's only signal, and the refusal that finally corrects it.
    assert "nothing staged" in out
    assert "already has an answer, and is PROVISIONAL" in out


def test_scene2_shows_the_protocol_that_avoids_it(tmp_path):
    """One op in the tray, and it is not B's. The whole supported path."""
    out = run(2, tmp_path)
    assert "STAGED  1 op(s)" in out
    assert "applied" not in out, "scene 2 must not write anything"


def test_scene3_reports_the_drift_and_then_certifies_the_contradiction(tmp_path):
    """The demo's central claim, in three parts.

    The drift line is printed; the batch lands anyway; and `dg check` then calls
    the result sound. If a future invariant *did* catch this, the third
    assertion fails — which is the right failure, because the README would then
    be wrong and somebody has to rewrite the scene rather than quietly keep it.
    """
    out = run(3, tmp_path)

    # It opens by handing agent A its assignment.
    assert "[evidence_unharvested]" in out

    # The one warning anyone gets, and it does not stop the write.
    assert "D01 moved since this batch was staged (its answer changed)" in out
    assert "op 0 (close D03) " in out.replace("\n", " ")

    # ...and the graph that results is certified clean, with the opening
    # warning gone: A harvested it.
    assert "3 vertices, 3 edges; 2 tasks, all invariants hold" in out
    clean = out.index("3 vertices, 3 edges; 2 tasks, all invariants hold")
    assert "warning(s)" not in out[clean:clean + 80]

    # Both contradicting answers, in one `dg why`, with the verdict under them.
    assert "compile in as a generated header" in out
    assert "A trained net, 40 MB" in out
    assert "every premise under this is settled" in out


def test_scene3_ends_by_using_the_falsifier(tmp_path):
    """The exit is a command, and it is only available because the falsifier
    was written before there was any reason to write it."""
    out = run(3, tmp_path)
    assert "reopen D03" in out
    assert "its falsifier fired" in out


def test_scene4_says_three_different_things(tmp_path):
    """A refusal, and two collisions that must not read alike.

    The distinction between 4b and 4c is the whole argument in
    `pending.already()`: an agent that reads "identical to the op staged" as
    "my work failed" puts two vertices behind one question.
    """
    out = run(4, tmp_path)

    # 4a — the structural case, refused, naming the premise and both exits.
    assert "aborted, nothing written" in out
    assert "[propagation] D02 is DECIDED but rests on D01 (REOPENED)" in out

    # 4b — a genuine clash of ids.
    assert "already exists, and is not what this op would have created" in out

    # 4c — the same id, the same intent, somebody else's write.
    assert "identical to the op staged" in out
    assert "Nothing of yours was lost" in out


def test_scene5_conflicts_in_git_and_is_resolved_by_check(tmp_path):
    """Isolation moves the race into git, and `dg check` is the merge test."""
    out = run(5, tmp_path)
    assert "CONFLICT (content): Merge conflict in decisions.json" in out
    # The generated view conflicts too, and is never resolved by hand.
    assert "CONFLICT (content): Merge conflict in decision-graph.md" in out
    # Both agents' questions survive the union, and the result validates.
    assert "5 vertices" in out and "all invariants hold" in out


def test_the_demo_refuses_a_work_directory_it_did_not_make(tmp_path):
    """The one destructive thing here is guarded.

    `demo/demo.sh` never removes a directory; these scenes must, to rebuild the
    clones. So the work directory is claimed before anything is deleted, and a
    `$DG_DEMO_DIR` with somebody else's files in it stops the demo instead.
    """
    occupied = tmp_path / "not-ours"
    occupied.mkdir()
    (occupied / "important.txt").write_text("mine\n")

    env = {**__import__("os").environ, "DG_DEMO_DIR": str(occupied)}
    proc = subprocess.run(["bash", str(DEMO / "scenes" / "1.sh")],
                          capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode != 0
    assert "refusing to use" in proc.stderr
    assert (occupied / "important.txt").exists()


def test_demo_json_is_hand_editable():
    """Read as an example of the format, like `demo/`'s — so it looks like one."""
    import json
    for name in ("decisions.json", "tasks.json"):
        raw = (DEMO / name).read_text(encoding="utf-8")
        json.loads(raw)
        assert "\t" not in raw
        assert raw.endswith("\n")
