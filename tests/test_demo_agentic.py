"""The agentic demo must keep showing what it says it shows.

`demo-agentic/` is one day on a project, told in seven scenes, and each scene is a
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
    """One premise, a question per area, and the work that answers them.

    Spelled out rather than counted, because the shape is what produces the
    opening assignment. Three different jobs come out of these two files and a
    person wrote none of them: `T02` is DONE against an unsettled `D03`, so an
    answer is owed; `T01` has no prerequisites, so work is ready; `T03` is
    `because D03`, so work is blocked by a question rather than by a task.
    Change any one of those and scene 1 stops being about the graph handing out
    the work.
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

    # An answer is owed: T02 reported and D03 was never settled on it.
    assert tg.tasks["T02"].status == "DONE" and tg.tasks["T02"].evidence_for == "D03"
    assert tg.tasks["T02"].completions, "T02 has to carry a real outcome"
    # Work is ready: T01 has no prerequisites, and its result is owed to D02.
    assert tg.tasks["T01"].status == "TODO" and tg.tasks["T01"].evidence_for == "D02"
    assert "T01" not in tg.blocked_ids(), "T01 has to be startable on day one"
    # Work is blocked by a question, which is the other polarity of the seam.
    assert tg.tasks["T03"].because == ["D03"]


def test_d01_carries_the_falsifier_the_whole_demo_turns_on(tmp_path):
    """`D01`'s falsifier is the plot. Scenes 6 and 7 are unreadable without it."""
    from dgraph.model import Graph
    edge = Graph.load(DEMO / "decisions.json").active_edge("D01")
    assert edge is not None and edge.decided
    assert "GPU budget" in edge.falsifier      # the event B's reopen cites


def test_scene1_gets_three_jobs_out_of_the_graph(tmp_path):
    """The assignment is computed, not written. Three different kinds of
    outstanding thing come back from two commands: an answer that is owed, work
    that is ready, and work a *question* is blocking."""
    out = run(1, tmp_path)
    assert "[evidence_unharvested]" in out, "the answer nobody wrote down"
    assert "ready T01" in out, "the work nobody has started"
    assert "waits D03 (undecided)" in out, "work blocked by a question, not a task"


def test_scene2_makes_the_parallelism_out_of_the_work(tmp_path):
    """The scene's whole claim: a moment ago there was one ready task and three
    agents; B decomposed its own task and produced startable work for somebody
    else. If `ready T04` stops appearing, the demo no longer shows that."""
    out = run(2, tmp_path)
    assert "waits T04, T05" in out, "T01 no longer waits on its subtasks"
    assert "ready T04" in out, "the decomposition freed no work"


def test_scene3_moves_readiness_without_a_status_update(tmp_path):
    """`Blocked is derived, never stored` — asserted as the transition, because
    that is the part a reader has to see. T05 was waiting; one outcome recorded
    by a different agent, and it is ready."""
    out = run(3, tmp_path)
    assert "ready T05" in out
    assert "waits T05 · evidence for D02" in out, "T01's remaining wait"


def test_scene4_closes_the_loop_in_both_directions(tmp_path):
    """Work → evidence → answer, and answer → released work. The second is the
    one that surprises: `ready T03, T05` where T03 was blocked by a question."""
    out = run(4, tmp_path)
    assert "ready T03, T05" in out, "answering D03 did not release the release note"
    assert "[evidence_unharvested]" in out, "the graph did not ask B for D02"
    assert out.rstrip().endswith("running underneath it.") or "all invariants hold" in out


def test_scene5_names_the_agents_and_stops_publishing_their_drafts(tmp_path):
    """Both halves, and the second is only meaningful beside the first."""
    out = run(5, tmp_path)
    assert "applied 3 op(s)" in out, "the unnamed half no longer takes the others' work"
    assert "nothing staged" in out, "agent A's only signal, and it is the defect"
    assert "by A" in out and "by B" in out
    assert "op(s) left staged, by" in out


def test_scene6_puts_every_answer_under_review_at_once(tmp_path):
    """One fact, and the whole fan-out's output is provisional. The count is the
    point: both answers, computed rather than remembered."""
    out = run(6, tmp_path)
    assert "2 decided descendant(s) rest on it and become PROVISIONAL" in out
    assert "PROVISIONAL 2" in out
    assert "unfinished task(s) rest on a premise under review" in out
    # And the work does *not* stop: PROVISIONAL means there is an answer nobody
    # is vouching for, not that there is none. An earlier cut of this scene said
    # T03 was blocked again; the transcript said `ready T03`.
    assert "ready T03" in out, "the reopen wrongly blocked the work"
    assert "RESTING ON A PREMISE UNDER REVIEW" in out


def test_scene7_reports_the_two_it_cannot_prevent(tmp_path):
    """7a: a question left with no route to an answer, visible only at the seam.
    7b: drift, then a clean check over a contradiction, then the falsifier."""
    out = run(7, tmp_path)
    assert "[evidence_stalled]" in out
    assert "waits on evidence nobody is producing" in out
    # 7b — and `its answer changed` is the case, not a status move.
    assert "D01 moved since this batch was staged (its answer changed)" in out
    assert "all invariants hold" in out
    assert "its falsifier fired" in out


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

#: A slide, however many classes it carries. Matching `class="slide"` exactly is
#: what these checks did first, and the annex slides — which add
#: `slide--annex` — were then invisible to every one of them: the counter check
#: compared 12 against 10 and the coverage check found no annexes at all.
_SLIDE = re.compile(r'<section class="[^"]*\bslide\b[^"]*" data-title="([^"]+)"')


def test_the_deck_covers_every_scene_the_demo_has():
    """The failure this file was written after: the deck kept describing five
    scenes while the demo had six, and nothing said so."""
    titles = _SLIDE.findall(_BODY)
    scenes = sorted(int(t.split()[1]) for t in titles if t.startswith("Scene "))
    on_disk = sorted(int(p.stem) for p in (DEMO / "scenes").glob("[0-9].sh"))
    assert scenes == on_disk, f"deck has {scenes}, demo has {on_disk}"


def test_the_deck_covers_the_annex_too():
    """The first version of the check above globbed `[0-9].sh`, so `a1.sh` and
    `a2.sh` were invisible to it: the annex could be missing from the deck, or
    rot in it, and the suite would stay green while reporting that the deck was
    covered. A guard that is silent about half its subject is worse than none,
    because the guard is why nobody looks."""
    titles = _SLIDE.findall(_BODY)
    annexes = sorted(int(t.split()[1]) for t in titles if t.startswith("Annex "))
    on_disk = sorted(int(p.stem[1:]) for p in (DEMO / "scenes").glob("a[0-9].sh"))
    assert annexes == on_disk, f"deck has annexes {annexes}, demo has {on_disk}"


def test_the_deck_marks_the_annex_as_outside_the_day():
    """A reader landing on slide 11 has to know they have left the story. The
    marker is a class rather than a sentence so it cannot be edited away in a
    prose pass without this failing."""
    for m in re.finditer(r'<section class="([^"]*)" data-title="Annex [0-9]"',
                         _BODY):
        assert "slide--annex" in m.group(1), "an annex slide reads as a scene"
    assert "not part of the day" in _BODY


def test_the_slide_counter_matches_the_slides():
    """Hand-written, and read by nobody until it is wrong."""
    said = re.search(r'id="count">\s*1\s*/\s*(\d+)\s*<', _BODY).group(1)
    assert int(said) == len(_SLIDE.findall(_BODY))


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
    for m in re.finditer(r'<section class="[^"]*\bslide\b[^"]*" '
                         r'data-title="([^"]+)"(.*?)</section>', _BODY, re.S):
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


# ---- the annex -----------------------------------------------------------
#
# Outside the day and outside `demo.sh all`, which is exactly why it needs
# pinning: nothing in the main run exercises these, so they can rot without any
# transcript changing. The claims are the same shape as the scenes' — a
# quotation from `dg`, asserted rather than described.


def _annex(name, tmp_path):
    return run(name, tmp_path)


def test_annex1_refuses_both_id_collisions_identically(tmp_path):
    """The annex's whole point: the refusal is the same both times and the right
    answer is opposite — a fresh id where the questions differ, a `dg drop`
    where they are the same question in two wordings. If these ever start
    reading differently, the paragraph explaining why an agent cannot tell them
    apart is wrong."""
    out = _annex("a1", tmp_path)
    assert out.count("D04 already exists, and is not what this op would have "
                     "created") == 2
    assert "50-99" in out, "the grant that makes the collision rare"


def test_annex2_cannot_be_merged_by_git_and_is_refused_at_composition(tmp_path):
    """a2a is what a union genuinely handles; a2b is what it must never be
    allowed to do silently."""
    out = _annex("a2", tmp_path)
    assert "CONFLICT (content): Merge conflict in decisions.json" in out
    assert "all invariants hold" in out, "a2a must resolve to a store dg vouches for"
    assert "D50 already has an answer, and is DECIDED" in out
    assert "every premise under this is settled" in out
    # ...and it opens on the day the seven scenes built rather than on a bare
    # fixture. Asserted on the source, because the replay is deliberately silent
    # and so leaves nothing in the transcript to match.
    assert 'beat_the_day_so_far "$A_DIR"' in \
        (DEMO / "scenes" / "a2.sh").read_text(encoding="utf-8")


def test_the_annex_is_reachable_and_is_not_part_of_the_day():
    """Both halves. An annex nobody can run is worse than no annex, and an annex
    inside `all` is not an annex — it is scene 8, which would make the day stop
    being one story."""
    driver = (DEMO / "demo.sh").read_text(encoding="utf-8")
    assert "annex) scenes=(a1 a2)" in driver, "no way to run it"
    assert "a1|a2)" in driver, "no way to run one of them"
    assert "all) scenes=(1 2 3 4 5 6 7) ;;" in driver, "the annex leaked into the day"
    # And the day says it exists, or nobody will find it.
    assert "demo.sh annex" in driver
