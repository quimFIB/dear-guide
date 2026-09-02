"""The quick-start page must keep showing what it says it shows.

`quick-start-demo/index.html` is a reference of worked examples: seventeen
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
    transcript = _run_layer(slug, layer, tmp_path / "work")
    lines = transcript.splitlines()
    for rx, _note in build.RECIPES[slug][f"hl_{layer}"]:
        assert any(re.search(rx, ln) for ln in lines), \
            f"{slug} {layer}: no line matches /{rx}/\n\n{transcript}"


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
