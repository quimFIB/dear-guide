"""The cookbook's demo domain, `grep` (`quick-start-demo/grep-domain/`).

Nothing ships it, but it is the domain an author copies, so the two rules it
exists to show are pinned here rather than left to the page: the store names
data and never a command (R4), and a domain reads under `root` and nowhere
else. Audit J-F4 found the first broken by the argument order — a `pattern`
beginning with `-` reached `grep` as an option, so `--help` fired with the
usage text as its sentence and `-f/etc/hostname` read a file outside root.
"""

import sys
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parent.parent / "quick-start-demo" / "grep-domain"
sys.path.insert(0, str(DEMO))
from dg_grep import GREP  # noqa: E402

from dgraph.domains import Domain, Item  # noqa: E402


@pytest.fixture
def root(tmp_path):
    (tmp_path / "bench").mkdir()
    (tmp_path / "bench" / "search.md").write_text("p95: 340 ms  trigram\n")
    return tmp_path


def _judge(root, **args):
    return GREP.evaluate([Item("D", "grep.matches", args, None)], root,
                         deadline=0)["D"]


def test_it_is_a_domain_that_judges(root):
    assert isinstance(GREP, Domain) and GREP.name == "grep"
    assert _judge(root, file="bench/search.md", pattern="^p95: [0-9]{3}").verdict == "fired"
    assert _judge(root, file="bench/search.md", pattern="^p95: [0-9]{1,2} ms").verdict == "holds"


def test_a_pattern_is_a_pattern_even_when_it_starts_with_a_dash(root):
    """J-F4: the store carries data; a leading dash must not turn it into
    an option grep interprets."""
    r = _judge(root, file="bench/search.md", pattern="--help")
    assert r.verdict == "holds" and "Usage" not in r.sentence, r
    (root / "bench" / "search.md").write_text("-f is a line\n")
    r = _judge(root, file="bench/search.md", pattern="-f is")
    assert r.verdict == "fired" and r.sentence.endswith("-f is a line"), r


def test_a_pattern_cannot_name_a_file_outside_root(root, tmp_path_factory):
    """J-F4, the half that reads the world: `-f FILE` takes patterns from
    FILE, wherever it is."""
    outside = tmp_path_factory.mktemp("elsewhere") / "patterns"
    outside.write_text("trigram\n")
    r = _judge(root, file="bench/search.md", pattern=f"-f{outside}")
    assert r.verdict == "holds", r


def test_a_file_outside_root_is_not_read_however_it_is_spelled(root, tmp_path_factory):
    """The rule the domain got right: `..`, an absolute path and a symlink
    out of root all resolve outside it and are `unjudged`, not read."""
    outside = tmp_path_factory.mktemp("elsewhere") / "secret.md"
    outside.write_text("p95: 1 ms\n")
    (root / "bench" / "link.md").symlink_to(outside)
    for f in ("../elsewhere0/secret.md", str(outside), "bench/link.md",
              "bench/../../secret.md"):
        r = _judge(root, file=f, pattern="p95")
        assert r.verdict == "unjudged" and "not read" in r.sentence, (f, r)
    assert _judge(root, file="bench/missing.md", pattern="p95").verdict == "unjudged"
