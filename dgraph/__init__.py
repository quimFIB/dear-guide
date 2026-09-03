"""`dg` — a project's development graph.

Two stores, kept apart on purpose: `decisions.json` holds what the project has
settled, what each answer rests on and what evidence would reopen it;
`tasks.json` holds the work that follows from those decisions. They meet in one
module, `dgraph.cross`, and nowhere else.
"""

from __future__ import annotations

#: The distribution name, in one place. It has been renamed twice already, and
#: the symptom of a stale copy is `dg --version` reporting "unknown" — which an
#: adapter reads as "too old to have the command I want". Must equal the
#: `name` in `pyproject.toml`; a test asserts it does.
DIST = "dear-guide"
TOOL = "dg"


def version() -> str:
    """What this copy of the code is — during beta, the commit it came from.

    **A number nobody bumps is worse than no number.** `pyproject.toml` has
    said `0.1.0` across every change this tool has had, because bumping a
    version per change while the shape of the tool is still moving is a chore
    that gets skipped, and a version that is skipped reports a copy from March
    and a copy from today as the same thing. The commit does not need bumping:
    it is what the code *is*, and the question a user actually asks —
    *is my install current?* — is answered by comparing it against
    `git log -1` in the repository they installed from.

    So this returns the short commit, `-dirty` where the working tree has moved
    past it, and falls back to the packaged version where git cannot say (a
    wheel, a source tree with the history stripped).

    **What would end it:** a release published anywhere a user cannot run `git
    log` against — PyPI, or a distro package — at which point a hash names
    nothing the reader can compare and `pyproject.toml` has to start carrying a
    number that is actually maintained. Until then the number stays `0.1.0` and
    means only "beta"; nothing should read it as an ordering.
    """
    return commit() or _packaged()


def _packaged() -> str:
    """The version recorded at install time, or `"unknown"`."""
    from importlib.metadata import PackageNotFoundError, version as _v
    try:
        return _v(DIST)
    except PackageNotFoundError:
        return "unknown"


def commit() -> str | None:
    """The commit this code was installed from, short — or `None`.

    Read from git at the tree the package lives in, which for the editable
    install the README prescribes is the checkout itself: `__file__` sits
    inside the repository, so `git -C` on its directory answers about *this*
    code rather than about whatever directory the caller happens to stand in.

    **The repository found is checked, not assumed.** A non-editable install
    drops the package somewhere that may itself sit inside an unrelated
    repository — a vendored tree, a site-packages under a dotfiles repo — and
    that repo's HEAD is not this code's provenance. So the answer counts only
    if the toplevel git names holds the very file this is.

    `-dirty` where tracked files differ from HEAD, because an editable install
    *is* the working tree and uncommitted edits make the bare hash a lie.
    Untracked files are excluded: a scratch file beside the package changes
    nothing about what the installed code does, and would otherwise leave
    `--version` permanently dirty for anyone whose habit is a notes file.
    """
    global _COMMIT
    if _COMMIT is _UNASKED:
        _COMMIT = _ask_git()
    return _COMMIT


def _ask_git(here=None) -> str | None:
    """`commit()` without the cache. `here` is this file, and is a parameter
    only so the guard below can be tested against a tree that is not this
    one — which is the case that must not be got wrong, and the one that
    cannot be arranged by standing somewhere else."""
    import subprocess
    from pathlib import Path

    here = Path(here).resolve() if here else Path(__file__).resolve()

    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(("git", "-C", str(here.parent), *args),
                                 capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    top = git("rev-parse", "--show-toplevel")
    if not top or (Path(top) / "dgraph" / "__init__.py").resolve() != here:
        return None
    short = git("rev-parse", "--short", "HEAD")
    if not short:
        return None
    return f"{short}-dirty" if git("status", "--porcelain", "-uno") else short


#: Asked once per process. `None` is an answer — no git, or a tree that is not
#: this one — so "not asked yet" needs a value of its own, or a wheel install
#: pays for a subprocess on every call that will fail the way the last one did.
_UNASKED: object = object()
_COMMIT: str | None | object = _UNASKED
