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

#: The option's own name, and the attribute pytest derives from it.
OPTION = "--decision-graph"
_DEST = "decision_graph"

__all__ = [
    "decision_project",
    "decision_violations",
    "test_decision_graph_invariant",
    "test_decision_graph_advisory_warnings",
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
