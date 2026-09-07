"""The quick-start page must keep showing what it says it shows.

`quick-start-demo/index.html` is a reference of worked examples: twenty
recipes, each a real transcript of `dg` against a seed project, with the
lines that matter highlighted. Those highlighted lines are the claim each
recipe makes, and they are quotations from `dg` -- so a reworded message
leaves every recipe still running, still exiting zero, and quietly no longer
showing the thing the page points at.

So the assertions here are the highlights. `build.RECIPES` holds one regex
per highlighted line, per layer; each recipe is run for real from a fresh
copy of the seed, and its transcript is searched for every one of them. The
page and the test read the same table, so a line the page points at is a
line this test checks for, and nothing else about the prose is pinned.

The seed is checked too: it has to load, pass `dg check` clean, and hold
the states the recipes rely on -- a decidable question, one awaiting
evidence, one blocked on a premise, and a task in every status.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parent.parent / "quick-start-demo"
sys.path.insert(0, str(DEMO))
import build  # noqa: E402  (quick-start-demo/build.py)

RECIPES = sorted(build.RECIPES)


def _workdir(tmp_path):
    """A work directory whose path is exactly as long as `run.sh`'s default.

    Transcripts are captured at `COLUMNS=96` and carry the project path —
    `✓ created …/notelit/decisions.json`, the `DG_WRITE` cell of `dg-agent
    env` — so a longer path wraps and truncates lines that the shipped run
    did not, and the diff below would call every such line stale. Same
    length, masked to `PATH` afterwards: the two corpora then differ only in
    what the mask names. Unique per test, since xdist runs recipes in
    parallel and `claim_work` refuses a directory it did not make.
    """
    default = Path(os.environ.get("TMPDIR", "/tmp")) / "dg-quick-start"
    tag = tmp_path.name[-8:].replace("_", "-")
    name = ("dg-qs-" + tag)[:len(default.name)].ljust(len(default.name), "x")
    return default.parent / name


def _run_layer(slug, layer, workdir):
    env = {**os.environ, "DG_DEMO_DIR": str(workdir),
           # The recipes commit, and a developer's global git config may set
           # anything from a signing key to a hook. Identity comes from lib.sh.
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    for k in list(env):
        if k.startswith("DG_") and k != "DG_DEMO_DIR":
            del env[k]
    r = subprocess.run(["bash", str(DEMO / "recipes" / f"{slug}.sh"), layer],
                       capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


@pytest.mark.skipif(shutil.which("dg") is None, reason="dg is not installed")
@pytest.mark.parametrize("slug", RECIPES)
@pytest.mark.parametrize("layer", ["quick", "full"])
def test_recipe_still_shows_what_the_page_highlights(slug, layer, tmp_path):
    work = _workdir(tmp_path)
    try:
        transcript = _run_layer(slug, layer, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    lines = transcript.splitlines()
    for rx, _note in build.RECIPES[slug][f"hl_{layer}"]:
        assert any(re.search(rx, ln) for ln in lines), \
            f"{slug} {layer}: no line matches /{rx}/\n\n{transcript}"
    # And the transcript the page ships is the one the tool prints now. The
    # highlights above pin the lines the page points at; this pins the rest,
    # which is where three passes found the cookbook stale (`V-F8`, `J-F6`,
    # `L-F4`) while every highlight still matched. `D92`.
    shipped = (DEMO / "out" / f"{slug}.{layer}.txt").read_text(encoding="utf-8")
    want, got = _masked(shipped, work), _masked(transcript, work)
    if want != got:
        import difflib
        diff = "".join(difflib.unified_diff(
            want.splitlines(True), got.splitlines(True),
            f"out/{slug}.{layer}.txt", "fresh run", n=1))
        pytest.fail(f"{slug} {layer}: the shipped transcript is stale — "
                    f"`./run.sh {slug}` in quick-start-demo/ regenerates it\n{diff}")


#: What a fresh run legitimately differs in: op refs (four letters from
#: `pending.REF_ALPHABET`), dates, durations, commit hashes, and the paths of
#: this checkout and the work directory. Anything else is drift.
_NOISE = [
    (re.compile(r"\b[a-hj-km-np-z]{4}\b"), "REF"),
    (re.compile(r"\b\d{4}-\d\d-\d\d\b"), "DATE"),
    (re.compile(r"(?<![\w.])[+-]?\d+(?:\.\d+)?(?:ms|s|m|h)\b"), "SPAN"),
    (re.compile(r"\b[0-9a-f]{12}\b"), "HASH"),
]


def _masked(text, work):
    for path in (str(DEMO.parent), str(work), "/tmp/dg-quick-start"):
        text = text.replace(path, "PATH")
    for rx, sub in _NOISE:
        text = rx.sub(sub, text)
    return text


def test_seed_is_clean_and_holds_every_state():
    d = json.loads((DEMO / "seed" / "decisions.json").read_text())
    t = json.loads((DEMO / "seed" / "tasks.json").read_text())
    assert {v["status"] for v in d["vertices"]} == {"DECIDED", "OPEN"}
    assert any(not e.get("active") for e in d["edges"]), "the seed holds no reversal"
    assert {x["status"] for x in t["tasks"]} == {"TODO", "DOING", "DONE", "PARKED", "DROPPED"}
    assert {e["kind"] for e in t["edges"]} == {"precedes", "prompted"}
    assert any(x.get("because") for x in t["tasks"]) and any(x.get("evidence_for") for x in t["tasks"])


@pytest.mark.skipif(shutil.which("dg") is None, reason="dg is not installed")
def test_seed_frontier_has_all_three_kinds(tmp_path):
    for name in ("decisions.json", "tasks.json"):
        shutil.copy(DEMO / "seed" / name, tmp_path / name)
    env = {**os.environ, "DG_PROJECT": str(tmp_path), "COLUMNS": "120", "NO_COLOR": "1"}
    # The views are generated; a store without them warns, which is not the seed's fault.
    subprocess.run(["dg", "render"], check=True, capture_output=True, env=env)
    subprocess.run(["dg", "task", "render"], check=True, capture_output=True, env=env)
    check = subprocess.run(["dg", "check"], capture_output=True, text=True, env=env)
    assert check.returncode == 0 and "warning" not in check.stdout, check.stdout
    frontier = subprocess.run(["dg"], capture_output=True, text=True, env=env).stdout
    assert "decidable now" in frontier
    assert re.search(r"evidence T\d\d", frontier)
    assert re.search(r"waits D\d\d", frontier)


def test_page_is_built_from_the_transcripts_it_ships_with():
    """The committed page and the committed transcripts agree.

    `build.page()` is deterministic over out/, so a page rebuilt from the
    transcripts in the repo must be the one in the repo. Editing index.html
    by hand, or re-running the recipes without rebuilding, fails here.
    """
    if not (DEMO / "index.html").exists() or not any((DEMO / "out").glob("*/")):
        pytest.skip("the snapshots are not committed — `./quick-start-demo/run.sh` regenerates them")
    html_text, problems = build.page()
    assert not problems, problems
    assert html_text == (DEMO / "index.html").read_text()
