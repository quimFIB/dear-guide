"""The org -> markdown converter, and the property that makes a mixed store safe.

`tests/fixtures/org_cases.json` is shared with the JS half (see
`test_app_md_matches_the_corpus`), so a rule stated once is checked in both
languages.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from dgraph.orgmd import anchor, to_markdown

FIXTURE = Path(__file__).parent / "fixtures" / "org_cases.json"
CORPUS = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = CORPUS["cases"]
APP = Path(__file__).resolve().parents[1] / "dgraph" / "static" / "app.html"


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_org_converts_to_the_expected_markdown(case):
    assert to_markdown(case["org"], fmt=case.get("format")) == case["markdown"]


@pytest.mark.parametrize("text", CORPUS["markdown_is_a_no_op"])
def test_markdown_passes_through_untouched(text):
    """The load-bearing property.

    The store holds org (from `dg decide --edit`) *and* markdown (from the web
    app's textarea, `md_import.py`, and any future agent plugin). A converter
    that rewrote markdown would corrupt half the store on every render, so every
    rule is restricted to syntax markdown does not have.
    """
    assert to_markdown(text) == text


def test_conversion_is_idempotent():
    """`render` runs on every apply; converting twice must not drift."""
    for case in CASES:
        fmt = case.get("format")
        once = to_markdown(case["org"], fmt=fmt)
        assert to_markdown(once, fmt=fmt) == once, case["name"]


def test_emphasis_is_left_alone_without_provenance():
    """Org `*bold*` and markdown `*italic*` are the same syntax, so without a
    provenance tag no rewrite can be right for both — untagged prose keeps its
    bytes. Pinned so nobody 'fixes' it with a heuristic that breaks paths."""
    assert to_markdown("*bold*") == "*bold*"
    assert to_markdown("/italic/") == "/italic/"
    assert to_markdown("see report/x.md and and/or") == "see report/x.md and and/or"


def test_org_emphasis_converts_only_under_provenance():
    """The transparency the tag buys: org-composed prose renders with org's
    meaning everywhere, and markdown stays markdown."""
    assert to_markdown("*bold*", fmt="org") == "**bold**"
    assert to_markdown("/italic/", fmt="org") == "_italic_"
    # org's own boundaries: none of these are emphasis in org either
    assert to_markdown("2*3*4 and/or report/x.md", fmt="org") == \
        "2*3*4 and/or report/x.md"
    # an unknown tag is inert, never a guess
    assert to_markdown("*bold*", fmt="wiki") == "*bold*"


def test_empty_and_none_survive():
    assert to_markdown(None) is None
    assert to_markdown("") == ""


def test_anchor_is_lowercase_and_stable():
    assert anchor("D04") == '<a id="d04"></a>'


# ---- the JS half of the same corpus --------------------------------------


START = "/* --- prose rendering: shared with dgraph/orgmd.py --- */"
END = "/* --- end prose rendering --- */"


def _extract_md() -> str:
    """Lift the page's real rendering block out of app.html for node to run.

    Extracting between sentinels rather than duplicating the functions here: if
    the page changes, this test follows it. A copy would drift, which is the
    exact failure the shared corpus exists to prevent.
    """
    src = APP.read_text(encoding="utf-8")
    i, j = src.index(START), src.index(END)
    return src[i + len(START):j]


def test_the_extraction_sentinels_still_exist():
    """If someone removes the markers, the JS half would silently stop running."""
    src = APP.read_text(encoding="utf-8")
    assert src.count(START) == 1 and src.count(END) == 1
    assert "function md(s, fmt)" in _extract_md()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_app_md_matches_the_corpus():
    """Org knowledge lives in Python and JS; this is the anti-drift device."""
    script = _extract_md() + r"""
const cases = %s;
let bad = [];
for (const c of cases) {
  const got = md(c.org, c.format);
  if (got !== c.html) bad.push(`${c.name}\n  want ${c.html}\n  got  ${got}`);
}
if (bad.length) { console.log(bad.join("\n")); process.exit(1); }
""" % json.dumps(CASES)
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_app_html_script_parses():
    """`app.html` has no build step, so a syntax error ships silently. This is the
    only thing standing between a stray comma and a blank web app."""
    src = APP.read_text(encoding="utf-8")
    js = src[src.index("<script>") + len("<script>"):src.rindex("</script>")]
    r = subprocess.run(["node", "--check", "-"], input=js,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
