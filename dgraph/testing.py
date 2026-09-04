"""Ready-made pytest tests for a project's decision graph.

A project should not have to restate the tool's invariants. Put one file in
`tests/`:

    from dgraph.testing import *  # noqa: F401,F403

and every check the tool knows about becomes a test — across every store the
project has, since `check.CHECKS` names the task and decision rules alike.
Adding a check to the tool adds it to every project, with no list to keep in
sync — keeping one was how `stale_block` nearly went unchecked.

The project is located from pytest's rootdir, or from `$DG_PROJECT`, or with
``--decision-graph PATH`` if the plugin is registered.
"""

from __future__ import annotations

import warnings as _warnings
from pathlib import Path

import pytest

from dgraph import project as _project
from dgraph.check import CHECKS, run
from dgraph.domains import PROBES

#: The option's own name, and the attribute pytest derives from it.
OPTION = "--decision-graph"
_DEST = "decision_graph"
#: The opt-in for `dg probe`'s findings. Off by default, unlike every check
#: in `CHECKS`, because a probe may need a toolchain and reads the world:
#: `dg check` output is a function of the store (R2), and this is not.
PROBE_OPTION = "--decision-graph-probe"
_PROBE_DEST = "decision_graph_probe"
#: With the opt-in: only the probes these domains judge (`dg probe --domain`).
DOMAIN_OPTION = "--decision-graph-domain"
_DOMAIN_DEST = "decision_graph_domain"

__all__ = [
    "decision_project",
    "decision_violations",
    "probe_rows",
    "probe_findings",
    "probe_advisories",
    "test_decision_graph_invariant",
    "test_decision_graph_advisory_warnings",
    "test_decision_graph_probe",
    "test_decision_graph_probe_advisory_warnings",
]


def pytest_addoption(parser) -> None:
    """Register ``--decision-graph``.

    Only reached when pytest loads this module as a plugin, which the
    ``pytest11`` entry point arranges. A project that merely does
    ``from dgraph.testing import *`` still gets the fixtures and the tests —
    that import is what runs them — and gets the option too, because the entry
    point is part of the same installation.
    """
    parser.addoption(
        OPTION, action="store", default=None, metavar="PATH",
        help="the project whose development graph to check (default: "
             "$DG_PROJECT, else pytest's rootdir)",
    )
    parser.addoption(
        PROBE_OPTION, action="store_true", default=False,
        help="also evaluate every probe through the installed domains "
             "(`dg probe --all`); off by default because it may need a "
             "toolchain",
    )
    parser.addoption(
        DOMAIN_OPTION, action="append", default=[], metavar="PREFIX",
        help=f"with {PROBE_OPTION}: only probes this domain judges "
             "(`dg probe --domain PREFIX`); repeatable. Each domain runs "
             "under its own declared deadline",
    )


@pytest.fixture(scope="session")
def decision_project(pytestconfig):
    """Which project these tests judge.

    The explicit option wins, then `$DG_PROJECT` (via `project.find`), then
    rootdir. A repository holding more than one project needs the first of
    those: `$DG_PROJECT` is process-wide, so it cannot say "this test run, this
    project" without leaking into everything else the shell goes on to do.
    """
    chosen = pytestconfig.getoption(_DEST, default=None)
    start = Path(chosen).expanduser() if chosen else Path(pytestconfig.rootpath)
    proj = _project.Project(start.resolve()) if chosen else _project.find(start)
    if not proj.exists:
        where = f"{OPTION} {chosen}" if chosen else f"at or above {start}"
        pytest.skip(f"no {_project.STORE_NAME} or {_project.TASKS_NAME} "
                    f"{where}")
    return proj


@pytest.fixture(scope="session")
def decision_violations(decision_project):
    return run(decision_project)


@pytest.mark.parametrize("check", CHECKS)
def test_decision_graph_invariant(check, decision_violations):
    """One test per invariant, so a failure names the rule that broke."""
    hits = [str(v) for v in decision_violations
            if v.check == check and v.blocking]
    assert not hits, "\n".join(hits)


def test_decision_graph_advisory_warnings(decision_violations):
    """Non-blocking findings, surfaced through pytest's warning summary.

    Deliberately never a failure: an isolated vertex is exactly what the first
    vertex of a new graph looks like (see `Violation` in `dgraph/model.py`),
    and a CI that goes red on the documented-normal state of work in progress
    teaches people to delete the test. Blocking violations still fail above,
    one test per rule.
    """
    warns = [str(v) for v in decision_violations if not v.blocking]
    if warns:
        _warnings.warn(
            "decision graph advisory finding(s):\n" + "\n".join(warns),
            UserWarning, stacklevel=1,
        )


@pytest.fixture(scope="session")
def probe_rows(pytestconfig, decision_project):
    """`dg probe --all` over the project, judged — only when opted in.

    Skipped rather than empty when the flag is off, so a green run never
    reads as *every probe holds* on a machine that evaluated nothing.
    """
    if not pytestconfig.getoption(_PROBE_DEST, default=False):
        pytest.skip(f"probes are evaluated only under {PROBE_OPTION}")
    from dgraph import pending as _pending
    from dgraph import probing as _probing
    from dgraph.model import Graph as _Graph
    from dgraph.tasks import TaskGraph as _TaskGraph
    proj = decision_project
    g = _Graph.load(proj.store) if proj.has_decisions else None
    tg = _TaskGraph.load(proj.tasks) if proj.has_tasks else None
    rows = _probing.rows(g, tg,
                         _pending.load(proj.pending) if g is not None else [],
                         _pending.load(proj.task_pending) if tg is not None else [])
    only = list(pytestconfig.getoption(_DOMAIN_DEST, default=[]) or [])
    if only:
        rows = _probing.select(rows, g, _probing.Scope(domain=only))
    return _probing.judge(rows, proj.root)   # each domain's own deadline (D85)


@pytest.fixture(scope="session")
def probe_findings(probe_rows):
    """The rows as findings — what `test_decision_graph_probe` fails on."""
    from dgraph import probing as _probing
    return _probing.findings(probe_rows)


@pytest.fixture(scope="session")
def probe_advisories(probe_rows):
    """The non-blocking findings worth a reader's eye: what every domain but
    the base case said, or could not say. `prose` presents and never judges,
    so its `unjudged` is the reading itself and not a finding; a `holds`, a
    timeout, a prefix nobody installed are."""
    from dgraph import probing as _probing
    rows = [r for r in probe_rows if r.prefixes != {"prose"}]
    return [v for v in _probing.findings(rows) if not v.blocking]


@pytest.mark.parametrize("finding", PROBES)
def test_decision_graph_probe(finding, probe_findings):
    """One test per probe finding, `test_decision_graph_invariant`'s twin
    over `PROBES` (proposal C6): a fired probe fails, the rest are findings
    a reader looks at — in the warning summary, through the advisory twin
    below. `domain_unavailable` and `probe_unjudged` never fail — a missing
    toolchain is not evidence about the thing the probe is about."""
    hits = [str(v) for v in probe_findings
            if v.check == finding and v.blocking]
    assert not hits, "\n".join(hits)


def test_decision_graph_probe_advisory_warnings(probe_advisories):
    """The non-blocking half of `dg probe`, through pytest's warning summary
    — `test_decision_graph_advisory_warnings`' twin over `PROBES`. A prefix
    this machine cannot judge is a footer line on `dg probe`, and under the
    opt-in it was nothing at all: six green tests on a run that evaluated
    none of what it was asked (audit `J-F5`). Never a failure, for the
    reason the twin gives; a `holds` and an `unjudged` are findings a
    reader looks at, and this is where they are put."""
    warns = [str(v) for v in probe_advisories]
    if warns:
        _warnings.warn(
            "decision graph probe finding(s):\n" + "\n".join(warns),
            UserWarning, stacklevel=1,
        )
