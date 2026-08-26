"""Names the tool hands out, and the one promise they make.

`$DG_AGENT` was a string somebody had to invent, and every value it went wrong
on was invented: a name colliding with `unowned`, a name carrying the roster's
own separator, a name that was two names. `dg agent claim` hands one out
instead.

The promise is **distinctness among the writers running now**, and it has to be
exact rather than likely. Drawing two words at random is the birthday problem,
and proof-duel's `tools/naming.py` measured it at 34 x 34: "288 sweeps drew only
247 distinct names". That module can shrug — a sweep name is a handle over a
timestamp that is already unique, and two sweeps never collide *as directories*.
A name here has nothing underneath it: two agents sharing one makes their ops
indistinguishable in the tray, so `--agent` writes the wrong batch and
`dg apply --mine` from one applies the other's drafts. `C-F16`, reached through
the door meant to make it harder.

So allocation reads what is taken and picks something else, and these tests are
about the two ways "taken" is bigger than the lease file.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading

import pytest
from typer.testing import CliRunner

from dgraph import agents, pending, project, task_pending
from dgraph.cli import app
from tests.conftest import FIXTURE, TASK_FIXTURE

runner = CliRunner()

D07 = {"op": "add_vertex", "id": "D07", "title": "seven", "area": "Alpha",
       "status": "OPEN"}
T07 = {"op": "add_task", "id": "T07", "title": "seven", "area": "Alpha",
       "status": "TODO"}


@pytest.fixture
def proj(tmp_path, monkeypatch):
    (tmp_path / "decisions.json").write_text(json.dumps(FIXTURE, indent=2),
                                             encoding="utf-8")
    (tmp_path / "tasks.json").write_text(json.dumps(TASK_FIXTURE, indent=2),
                                         encoding="utf-8")
    monkeypatch.setattr(project, "_override", tmp_path)
    monkeypatch.delenv("DG_AGENT", raising=False)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERM", "dumb")
    subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, capture_output=True)
    return project.Project(tmp_path)


@pytest.fixture
def run(proj):
    def go(*args):
        return runner.invoke(app, ["--project", str(proj.root), *args])
    return go


@pytest.fixture
def tiny(monkeypatch):
    """A pool of exactly two names, so exhaustion is reachable in a test.

    The lists are data and the allocator does not know their size, which is the
    property that lets this stand in for the real 7004 without simulating it.
    """
    monkeypatch.setattr(agents, "ADJECTIVES", ("agile",))
    monkeypatch.setattr(agents, "MARKS", ("azimuth", "bearing"))


# ---- the pool ------------------------------------------------------------


def test_the_pool_is_distinct_and_says_nothing_the_tool_reserves():
    seq = agents.sequence()

    assert len(set(seq)) == len(seq), "the pool repeats a name"
    assert pending.UNOWNED not in seq, (
        "the pool can hand out the one name staging refuses")
    assert all(re.fullmatch(r"[a-z]+-[a-z]+", n) for n in seq)


def test_a_name_cannot_forge_a_roster_entry():
    """The roster joins with ` · ` and the CLI splits nothing, so a name
    carrying the separator reads as two writers. A hand-set `$DG_AGENT` still
    can; nothing this tool hands out ever will."""
    assert not any(" " in n or "·" in n for n in agents.sequence())


def test_allocation_is_deterministic(proj):
    """No randomness anywhere, so a test can assert a name and a reader can
    predict one. `tools/naming.py` gives up variety for the same reason."""
    assert agents.claim(proj.root) == agents.sequence()[0]
    assert agents.claim(proj.root) == agents.sequence()[1]


# ---- what "taken" means --------------------------------------------------


def test_a_held_name_is_never_handed_out_twice(proj):
    seen = {agents.claim(proj.root) for _ in range(20)}

    assert len(seen) == 20


def test_two_launchers_at_once_get_two_names(proj):
    """The case the lock exists for. `project.held` takes an in-process lock
    first and it always succeeds, so this is deterministic rather than a race
    the test hopes to lose."""
    out, lock = [], threading.Lock()

    def go():
        name = agents.claim(proj.root)
        with lock:
            out.append(name)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(out)) == 8, f"two launchers got one name: {sorted(out)}"


@pytest.mark.parametrize("tray", ["decisions", "tasks"])
def test_a_name_staged_under_is_taken_even_with_no_lease(proj, tray):
    """The case the tray consultation is genuinely load-bearing: a lease file
    somebody deleted, or a `$DG_AGENT` set by hand before this existed. Either
    way the ops are real and the name is in use."""
    first = agents.sequence()[0]
    path = proj.pending if tray == "decisions" else task_pending.path()
    op = D07 if tray == "decisions" else T07
    pending.save([{**op, "by": first, "ref": "aaaa"}], path)

    assert agents.claim(proj.root) == agents.sequence()[1]


# ---- a claim never expires -----------------------------------------------


def test_emptying_the_trays_does_not_free_a_name(proj):
    """No expiry, and no boundary that reaps on the tool's own initiative.

    Every automatic rule wants "the trays are empty, so free everything", and
    every one has the same window: an agent claims, stages nothing yet, the
    trays go empty, its lease is swept, and the next claim hands the name to
    somebody else.
    """
    first = agents.claim(proj.root)
    pending.save([{**D07, "by": first, "ref": "aaaa"}], proj.pending)
    pending.clear(proj.pending)

    assert agents.claim(proj.root) != first
    assert first in agents.load(proj.root)


def test_release_frees_one_and_only_when_it_was_held(proj):
    first = agents.claim(proj.root)

    assert agents.release(first, proj.root) is True
    assert agents.release(first, proj.root) is False, "released twice"
    assert agents.claim(proj.root) == first


def test_release_does_not_hand_back_a_name_with_ops_staged(proj):
    """Releasing a name whose work is still staged is legitimate — the launcher
    is done, the review is not — but the *ops* still carry it, so handing it to
    a second writer would conflate them."""
    first = agents.claim(proj.root)
    pending.save([{**D07, "by": first, "ref": "aaaa"}], proj.pending)
    agents.release(first, proj.root)

    assert agents.claim(proj.root) != first


def test_prune_takes_the_idle_leases_and_leaves_the_rest(proj):
    busy, idle = agents.claim(proj.root), agents.claim(proj.root)
    pending.save([{**D07, "by": busy, "ref": "aaaa"}], proj.pending)

    assert agents.prune(proj.root) == [idle]
    assert list(agents.load(proj.root)) == [busy]


# ---- running out ---------------------------------------------------------


def test_exhaustion_raises_rather_than_inventing_a_name(proj, tiny):
    """An error, never a fallback. Handing back a name somebody holds, or
    numbering one outside the lists, is the silent conflation the ownership
    stamp exists to prevent — and the numbered one is worse for looking
    deliberate."""
    agents.claim(proj.root)
    agents.claim(proj.root)

    with pytest.raises(agents.Exhausted) as exc:
        agents.claim(proj.root)
    assert exc.value.total == 2 and exc.value.releasable == 2


def test_the_refusal_says_which_names_could_be_freed(proj, run, tiny):
    """Two messages, because the two situations have different exits: some
    leases are idle and `prune` frees them now, or every name has ops in a tray
    and the tray is what has to move first."""
    busy = agents.claim(proj.root)
    agents.claim(proj.root)
    pending.save([{**D07, "by": busy, "ref": "aaaa"}], proj.pending)

    some = run("agent", "claim")
    assert some.exit_code == 1
    assert "prune" in some.output and "1 have nothing staged" in some.output

    pending.save([{**D07, "by": busy, "ref": "aaaa"},
                  {**D07, "id": "D08", "by": agents.sequence()[1], "ref": "bbbb"}],
                 proj.pending)
    none = run("agent", "claim")
    assert none.exit_code == 1
    assert "dg apply" in none.output and "dg clear" in none.output


# ---- the door a launcher uses --------------------------------------------


def test_claim_prints_a_bare_name_and_nothing_else(proj, run):
    """`DG_AGENT=$(dg agent claim)` is the only sensible caller, so the whole
    of stdout has to be the name — no markup, no heading, and no wrap."""
    out = run("agent", "claim").output

    assert out.strip() == agents.sequence()[0]
    assert re.fullmatch(r"[a-z]+-[a-z]+\n?", out), repr(out)


def test_a_claimed_name_is_one_staging_accepts(proj, run, monkeypatch):
    """The round trip, and the point of the whole module: what `claim` hands
    out is a legal identity, so a launcher cannot produce the values that had
    to be refused."""
    name = run("agent", "claim").output.strip()
    monkeypatch.setenv("DG_AGENT", name)

    assert run("add", "--id", "D07", "--title", "seven",
               "--area", "Alpha").exit_code == 0
    assert [o.get("by") for o in pending.load(proj.pending)] == [name]


def test_list_marks_a_name_that_has_ops_but_no_lease(proj, run):
    """A hand-set `$DG_AGENT` looks exactly like this, and a roster of leases
    alone would report the tray unowned while somebody's drafts sat in it."""
    pending.save([{**D07, "by": "hand-set", "ref": "aaaa"}], proj.pending)

    out = run("agent", "list").output

    assert "hand-set" in out and "not claimed here" in out


def test_the_lease_file_is_ignored_by_git(proj, run):
    """`.dgraph-*` already covers it, and it has to: a lease is a claim about a
    writer running in *this* checkout, and one that travelled between clones
    would be a claim about somebody else's.

    Driven through `ensure_ignored` — what `dg init` calls — and then through
    git, rather than by reading `IGNORE` and agreeing with it. A pattern that
    matches in a list and not in a repository is the failure worth catching,
    and only git can report it. Its lock file goes the same way, so a
    `.dgraph-agents.json.lock` left by a killed process is not a commit either.
    """
    project.ensure_ignored(proj.root)
    run("agent", "claim")
    assert agents.path(proj.root).exists()
    (proj.root / (agents.AGENTS_NAME + ".lock")).write_text("")

    subprocess.run(["git", "add", "-A"], cwd=proj.root, capture_output=True)
    tracked = subprocess.run(["git", "ls-files"], cwd=proj.root,
                             capture_output=True, text=True).stdout

    assert agents.AGENTS_NAME not in tracked, tracked
