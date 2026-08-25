"""The agentic demo must keep showing what it says it shows.

`demo-agentic/` is one day on a project, told in six scenes, and each scene is a
claim about what happens when several writers meet. Every one of them is a
quotation from `dg`. That makes it fragile in a way
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


def test_scene1_turns_a_fired_falsifier_into_a_plan(tmp_path):
    """The opening move is the graph's, not the maintainer's: the falsifier
    written in March names the sponsor's cluster, and `dg show` is what turns
    "this is too big" into three questions with an order between them."""
    out = run(1, tmp_path)
    assert "reopen D01" in out
    assert "D01  REOPENED" in out
    # The order the fan-out has to respect, and nobody wrote it down as a rule.
    assert "decidable now" in out
    assert "waits D01" in out


def test_scene2_names_the_agents_and_stops_publishing_their_drafts(tmp_path):
    """Both halves, and the second is only meaningful beside the first.

    Unnamed, one agent's apply takes three ops and the others are told `nothing
    staged`. Named, the same commands leave every other agent's work its own.
    """
    out = run(2, tmp_path)
    assert "applied 3 op(s)" in out, "the unnamed half no longer loses the drafts"
    assert "nothing staged" in out, "agent A's only signal, and it is the defect"
    # ...and the same morning, with identities.
    # The column, not the exact spacing: `compact.listing` pads the aside to
    # the widest row, so pinning the run of spaces would fail on a reworded
    # answer rather than on a lost stamp.
    assert "by A" in out and "by B" in out
    assert "applied 1 op(s)" in out
    assert "op(s) left staged, by" in out


def test_scene3_orders_publication_by_the_edge(tmp_path):
    """Three agents compose at once and one is told to wait, by a refusal that
    names the premise and both exits. Then the same op applies unchanged."""
    out = run(3, tmp_path)
    assert "STAGED  3 op(s)" in out
    assert "[propagation] D02 is DECIDED but rests on D01 (REOPENED)" in out
    assert "aborted, nothing written" in out
    # The premise lands, and B's refused op then applies with nothing changed.
    assert out.count("applied 1 op(s)") >= 2


def test_scene4_reports_the_drift_and_then_certifies_the_contradiction(tmp_path):
    """The one no lock reaches. The drift line is the whole warning; `dg check`
    then calls the result clean, because it is."""
    out = run(4, tmp_path)
    assert "D01 moved since this batch was staged (REOPENED → DECIDED)" in out
    assert "all invariants hold" in out
    # Both readings, in one command's output, four lines apart.
    assert "compile in as a generated header" in out
    assert "A trained net, 40 MB" in out
    assert "every premise under this is settled" in out


def test_scene4_ends_by_using_the_falsifier(tmp_path):
    """The exit is a command rather than a judgement call, and that is only true
    because the falsifier was written before there was any reason to."""
    out = run(4, tmp_path)
    assert "its falsifier fired" in out
    assert "D03  REOPENED" in out


def test_scene5_refuses_both_collisions_the_same_way(tmp_path):
    """Two agents, one id, twice — and the refusal is identical while the right
    answer is opposite. That is the scene's whole claim, and it is a claim about
    what the tool deliberately does *not* decide."""
    out = run(5, tmp_path)
    assert out.count("D04 already exists, and is not what this op would have "
                     "created") == 2
    assert "50-99" in out, "the grant that makes the collision rare"


def test_scene6_cannot_be_merged_by_git_and_is_refused_at_composition(tmp_path):
    """6a is what a text merge does with two additions; 6b is what it must never
    be allowed to do with two answers."""
    out = run(6, tmp_path)
    assert "CONFLICT (content): Merge conflict in decisions.json" in out
    assert "all invariants hold" in out, "6a resolves to a store dg will vouch for"
    # 6b: refused before anything is staged, let alone merged.
    assert "D50 already has an answer, and is DECIDED" in out
    # ...and the day it closes on is the day the first five scenes built.
    assert "A trained net, 40 MB" in out
    assert "D03  REOPENED" in out


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


# ---- the deck ------------------------------------------------------------
#
# `slides.html` is the demo's other surface and nothing checked it, which is how
# it came to be a five-scene deck describing a six-scene demo. `B-F1` is the
# precedent and the warning: three controls were *drawn*, bound to nothing, and
# every test passed. These are structural rather than visual — they cannot say
# the deck looks right — but each one pins a way it can silently stop working.

import re

SLIDES = (DEMO / "slides.html").read_text(encoding="utf-8")
_CSS, _BODY = SLIDES.split("</style>", 1)


def test_the_deck_covers_every_scene_the_demo_has():
    """The failure this file was written after: the deck kept describing five
    scenes while the demo had six, and nothing said so."""
    titles = re.findall(r'<section class="slide" data-title="([^"]+)"', _BODY)
    scenes = sorted(int(t.split()[1]) for t in titles if t.startswith("Scene "))
    on_disk = sorted(int(p.stem) for p in (DEMO / "scenes").glob("[0-9].sh"))
    assert scenes == on_disk, f"deck has {scenes}, demo has {on_disk}"


def test_the_slide_counter_matches_the_slides():
    """Hand-written, and read by nobody until it is wrong."""
    said = re.search(r'id="count">\s*1\s*/\s*(\d+)\s*<', _BODY).group(1)
    assert int(said) == len(re.findall(r'<section class="slide"', _BODY))


def test_every_class_the_deck_uses_has_a_rule():
    """A class name with no rule renders as unstyled text, which on a terminal
    card means a refusal that is no longer red. Caught one — `warn`, which the
    stylesheet spells `dr`."""
    used = {c for g in re.findall(r'class="([^"]+)"', _BODY) for c in g.split()}
    missing = sorted(c for c in used if f".{c}" not in _CSS
                     and not c.startswith("is-"))
    assert not missing, f"no CSS rule for: {missing}"


def test_no_slide_skips_a_step():
    """The stepper reveals `data-step` 1..max and stops when `on >= max`, so a
    gap is a keypress that does nothing and a reader who thinks the deck hung."""
    for m in re.finditer(r'<section class="slide" data-title="([^"]+)"(.*?)</section>',
                         _BODY, re.S):
        steps = {int(n) for n in re.findall(r'data-step="(\d+)"', m.group(2))}
        if steps:
            assert steps == set(range(1, max(steps) + 1)), \
                f"{m.group(1)} has steps {sorted(steps)}"


def test_the_third_agent_lane_is_only_used_where_it_exists():
    """`.step--c` occupies the middle column, which is the spine in a two-lane
    slide. Used without `.lanes--3` it silently lands on top of the divider."""
    for m in re.finditer(r'<div class="lanes([^"]*)">(.*?)\n    </div>', _BODY, re.S):
        if "step--c" in m.group(2) or "lanehead--c" in m.group(2):
            assert "lanes--3" in m.group(1), "agent C's lane outside .lanes--3"
