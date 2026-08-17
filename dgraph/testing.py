"""Ready-made pytest tests for a project's decision graph.

A project should not have to restate the tool's invariants. Put one file in
`tests/`:

    from dgraph.testing import *  # noqa: F401,F403

and every check the tool knows about becomes a test. Adding a check to the tool
adds it to every project, with no list to keep in sync — keeping one was how
`stale_block` nearly went unchecked.

The project is located from pytest's rootdir, or from `$DG_PROJECT`, or with
``--decision-graph PATH`` if the plugin is registered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dgraph import project as _project
from dgraph.check import CHECKS, run

__all__ = [
    "decision_project",
    "decision_violations",
    "test_decision_graph_invariant",
    "test_decision_graph_has_no_warnings",
]


@pytest.fixture(scope="session")
def decision_project(pytestconfig):
    proj = _project.find(Path(pytestconfig.rootpath))
    if not proj.exists:
        pytest.skip(f"no decisions.json at or above {pytestconfig.rootpath}")
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


def test_decision_graph_has_no_warnings(decision_violations):
    """Non-blocking findings — an isolated vertex, say. Legal, but usually a slip."""
    warns = [str(v) for v in decision_violations if not v.blocking]
    assert not warns, "\n".join(warns)
