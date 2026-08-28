"""Where the graph lives.

The tool is installed once and run against any project. A project is simply a
directory containing `decisions.json`; the rendered view and the staging file
sit beside it.

Resolution order:
  1. an explicit path (``dg --project PATH``)
  2. ``$DG_PROJECT``
  3. the nearest ancestor of the cwd containing ``decisions.json``
  4. the cwd
"""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

STORE_NAME = "decisions.json"
VIEW_NAME = "decision-graph.md"
PENDING_NAME = ".dgraph-pending.json"
EDIT_NAME = ".dgraph-edit.org"

TASKS_NAME = "tasks.json"
TASK_VIEW_NAME = "tasks.md"
TASK_PENDING_NAME = ".dgraph-task-pending.json"

#: The id range this clone allocates from — see `dgraph/ranges.py`. Gitignored
#: like the trays, and for one more reason than they are: the watermark inside
#: it has to survive `git checkout`, or two branches in one worktree allocate
#: the same id from the same grant and nothing can see it.
RANGE_NAME = ".dgraph-range.json"

#: Somebody else's contribution, expressed as ops and waiting to be
#: adjudicated — see `dgraph/integrate.py`. **Not the tray**, and that is the
#: whole of why it is a second file: `pending.preview` is "what every
#: stage-time guard consults", so an unadjudicated op put in the tray makes
#: every read in this clone answer with it, and an agent doing unrelated work
#: composes against a title nobody accepted.
INCOMING_NAME = ".dgraph-incoming.json"

#: Every path pattern the tool writes that must not be committed. Not a
#: courtesy: three modules argue their own correctness from the assumption that
#: these are ignored. `write_atomic` leaves a `.dg-tmp` sibling behind when a
#: process is killed mid-write and says the suffix "is what the `.gitignore`
#: line matches, so… [it is] ignored instead of turning up untracked and being
#: swept into a commit by `git add -A`"; the commit gate tells the user that
#: `.dgraph-task-pending.json` is gitignored, and refuses commits on that
#: basis. None of it was true in a project set up from the quickstart, which
#: listed two of these lines out of the eleven files the tool can write.
#:
#: `.dgraph-*` covers both trays, the compose buffer, the detached server's run
#: record and log, and every `.lock` beside them, since all of them live in
#: that namespace. The two store locks are named individually rather than
#: swept up by `*.lock`, which would silently ignore a project's `uv.lock` or
#: `Cargo.lock`.
IGNORE = (
    ".dgraph-*",
    "*.dg-tmp",
    f"{STORE_NAME}.lock",
    f"{TASKS_NAME}.lock",
)

IGNORE_HEADER = "# dear-guide: staging, locks and temp files"

_override: Path | None = None


@dataclass(frozen=True)
class Project:
    root: Path

    @property
    def store(self) -> Path:
        return self.root / STORE_NAME

    @property
    def view(self) -> Path:
        return self.root / VIEW_NAME

    @property
    def pending(self) -> Path:
        return self.root / PENDING_NAME

    @property
    def edit(self) -> Path:
        """The editor buffer. A stable name, not a tempfile, so emacs can key
        `auto-mode-alist` off it the way it does for COMMIT_EDITMSG."""
        return self.root / EDIT_NAME

    @property
    def tasks(self) -> Path:
        return self.root / TASKS_NAME

    @property
    def task_view(self) -> Path:
        return self.root / TASK_VIEW_NAME

    @property
    def task_pending(self) -> Path:
        """The task staging area, deliberately its own file.

        `pending.preview` walks every op in a pending file and `_apply_one`
        raises on any op it does not know, so task ops sharing the decision
        staging file would break every decision command until it was cleared.
        """
        return self.root / TASK_PENDING_NAME

    @property
    def range(self) -> Path:
        """This clone's id grant. Absent in every single-writer project, which
        is the case `dgraph/ranges.py` treats as "behave as this tool always
        has" rather than as a fault."""
        return self.root / RANGE_NAME

    @property
    def incoming(self) -> Path:
        """An arriving contribution, quarantined until somebody answers it.

        One file for both stores, deliberately: a contribution is atomic across
        them. `because` and `evidence_for` hold a bare `D`-id in the other
        file and both cross-store link invariants are blocking, so half a
        contribution is not a smaller version of it — it is one that refuses
        for an inconsistency the whole does not have.
        """
        return self.root / INCOMING_NAME

    @property
    def has_decisions(self) -> bool:
        return self.store.exists()

    @property
    def has_tasks(self) -> bool:
        return self.tasks.exists()

    @property
    def exists(self) -> bool:
        """Whether this directory is a `dg` project at all.

        Either store is enough: a team may track work before it tracks
        decisions, and refusing `dg task` until somebody records a decision
        would be a strange gate. Callers that need one store specifically ask
        `has_decisions` / `has_tasks`.
        """
        return self.has_decisions or self.has_tasks


def in_repo(root: Path) -> bool:
    """Whether this directory is inside a git worktree, by inspection alone.

    No subprocess: `dg init` runs in the same breath as `git init` often
    enough, and shelling out to answer a question about the filesystem would
    make the common case pay for the rare one.
    """
    return any((d / ".git").exists() for d in (root, *root.parents))


def ensure_ignored(root: Path) -> list[str]:
    """Add the tool's scratch files to `root/.gitignore`. Returns what it added.

    Nothing outside a git worktree — a `.gitignore` in a directory with no
    repository is litter — and nothing that is already there. Failure is
    silent and returns nothing: an unwritable `.gitignore` is a reason to say
    less, not a reason for `dg init` to fail.
    """
    if not in_repo(root):
        return []
    path = root / ".gitignore"
    try:
        current = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError:
        return []
    have = {ln.strip() for ln in current.splitlines()}
    missing = [pat for pat in IGNORE if pat not in have]
    if not missing:
        return []
    block = ("" if not current or current.endswith("\n") else "\n") + \
            ("\n" if current else "") + IGNORE_HEADER + "\n" + \
            "\n".join(missing) + "\n"
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(block)
    except OSError:
        return []
    return missing


def use(path: str | Path | None) -> None:
    """Pin the project for this process (used by ``dg --project``)."""
    global _override
    _override = Path(path).expanduser().resolve() if path else None


def find(start: Path | None = None) -> Project:
    if _override is not None:
        return Project(_override)
    env = os.environ.get("DG_PROJECT")
    if env:
        return Project(Path(env).expanduser().resolve())
    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        if (d / STORE_NAME).exists() or (d / TASKS_NAME).exists():
            return Project(d)
    return Project(cur)


def write_atomic(path: Path, text: str) -> None:
    """Replace a file's contents, or leave the old ones entirely intact.

    Every store and every generated view goes through here. `dgraph/pending.py`
    opens on the premise that a tool able to corrupt the source of truth is
    worse than no tool, and `dgraph/applying.py` spends its docstring on the
    *order* of the four writes because that order is the correctness argument —
    but order only covers a failure *between* writes. A bare `write_text`
    truncates first and fills afterwards, so a process killed, a disk filled or
    a signal taken mid-write leaves a half-written `decisions.json`.

    That state has no exit from inside the tool: `dg check` reports
    `store_loads`, every `dg` command that could repair it refuses to load, and
    the commit gate denies every commit in the repository. The remedy is a
    hand-edit or `git checkout` — the two things this design exists to make
    unnecessary. So the write goes to a sibling temp file and `os.replace`
    swaps it in, which is atomic on every platform this runs on.

    The temp file is a sibling rather than a tempdir file because `os.replace`
    is only atomic within a filesystem, and `fsync` runs before the swap so a
    power loss cannot leave the rename pointing at unwritten blocks.

    Its name comes from `mkstemp` rather than from the pid, because `dg serve`
    is a `ThreadingHTTPServer`: two threads writing the same file would share
    one pid, and so one temp path — the first `os.replace` consumes it and the
    second fails on a file that is no longer there. A unique name per call
    makes concurrent writers merely race to be last, which is the ordinary
    meaning of two writes, rather than fail.

    The `.dg-tmp` suffix is what the `.gitignore` line matches, so the file a
    `kill -9` leaves behind is ignored instead of turning up untracked and
    being swept into a commit by `git add -A`.
    """
    # mkstemp opens 0600; a store that silently changed mode on its first
    # atomic write would be a surprise, so the target's own mode is carried
    # over — and a new file gets what a plain create would have given it.
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.",
                                suffix=".dg-tmp")
    tmp = Path(name)
    try:
        try:
            os.chmod(fd, stat.S_IMODE(path.stat().st_mode))
        except OSError:
            os.chmod(fd, 0o666 & ~_umask())
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Including KeyboardInterrupt and SystemExit: an interrupted write is
        # precisely the case this exists for, and a temp file left in the
        # user's repo is the small mess this helper would have made of the
        # large one it prevents.
        tmp.unlink(missing_ok=True)
        raise


def _umask() -> int:
    """The process umask, read the only way POSIX offers — by setting it back."""
    current = os.umask(0)
    os.umask(current)
    return current


# ---- locking -------------------------------------------------------------
#
# Two writers, one file. `write_atomic` above makes each write all-or-nothing,
# which is not the same thing as making two writers safe: atomicity is not
# isolation. A read-modify-write — load the store, apply a batch, save it — can
# still lose the other writer's result entirely, and both trays and both stores
# are read-modify-written on every command.
#
# Lives here rather than in `pending.py`, where it started, because the tray is
# not the only thing that needs it: `dgraph/applying.py` holds the *store* the
# same way, and a second copy of a lock is how two locks come to disagree.

#: How long to wait for a live holder before giving up on the lock. Tray ops
#: are microseconds; an apply is a validate and a store write (the views are
#: built on demand, outside this lock), so callers that hold one across real
#: work pass their own.
LOCK_WAIT = 2.0

#: In-process locks, one per path. The file lock cannot separate two threads of
#: one process — they share a pid, so each reads the other's lock as "held by
#: something alive", which is true and useless. `dg serve` is a
#: `ThreadingHTTPServer` and two Apply clicks are two threads, so this is the
#: common case, not the exotic one.
_threads: dict[str, threading.Lock] = {}
_threads_guard = threading.Lock()


def _thread_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _threads_guard:
        return _threads.setdefault(key, threading.Lock())


def alive(pid: int) -> bool:
    """Whether a process exists. A pid we may not signal counts as alive."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError, ValueError):
        pass  # not ours to signal, but something is there
    return True


def holder(lock: Path) -> int | None:
    """The pid stamped in a lock file, or None if it does not say.

    Public because `dgraph/editor.py` shares it, exactly as it shares `alive`:
    the buffer lock and this one both have to answer *is this still mine?*
    before unlinking, and two implementations of that is how two locks come to
    disagree. See `_release` below, and audit `C-F12` / `M-F5`.
    """
    try:
        return int(lock.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _take(lock: Path, wait: float) -> int | None:
    """Acquire `lock`, or return None having decided not to wait any longer.

    Three cases, and the difference between them is the whole point:

    - **the holder is gone** — a crashed process leaves its lock behind, and a
      lock nobody can ever take is worse than the race it was guarding. Its pid
      says so, so it is taken immediately rather than after a timeout.
    - **the holder is alive** — then it is not crashed, it is slow, and waiting
      is what a lock is for. Past `wait` we give up and proceed *unlocked*
      rather than stealing: stealing from a live holder is what produced two
      simultaneous holders, since the victim's release would then delete the
      thief's lock file and admit a third.
    - **the lock does not say who holds it** — either a holder that has created
      the file and not yet written its pid (microseconds) or a corrupt leftover
      (forever). Waiting tells them apart, so this waits like the live case and
      then takes it like the dead one.
    """
    deadline = time.monotonic() + wait
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            pid = holder(lock)
            if pid is not None and not alive(pid):
                with contextlib.suppress(OSError):
                    lock.unlink()
                continue
            if time.monotonic() >= deadline:
                if pid is None:
                    with contextlib.suppress(OSError):
                        lock.unlink()   # never said who it was; take it
                    continue
                return None             # live and wedged: proceed unlocked
            time.sleep(0.01)
            continue
        except OSError:
            return None                 # cannot lock here at all
        with contextlib.suppress(OSError):
            os.write(fd, str(os.getpid()).encode())
        return fd


def _release(lock: Path, fd: int | None) -> None:
    """Drop a lock we hold — and only one we still hold.

    The pid is checked before the unlink because this process may have been
    stolen from (its lock was unreadable, or it was declared dead while merely
    unschedulable). Deleting the file in that state would remove *someone
    else's* lock and let a third writer in, which is the failure this whole
    block exists to prevent.
    """
    if fd is None:
        return
    with contextlib.suppress(OSError):
        os.close(fd)
    if holder(lock) == os.getpid():
        with contextlib.suppress(OSError):
            lock.unlink()


#: Which projects this thread already holds the store pair for, so `stores`
#: below can nest. Per-thread because the lock it guards is: `dg serve` is a
#: `ThreadingHTTPServer` and two Apply clicks are two threads, which must not
#: see each other's depth.
_pairs = threading.local()


def _pair_depth() -> dict[str, int]:
    if not hasattr(_pairs, "depth"):
        _pairs.depth = {}
    return _pairs.depth


@contextlib.contextmanager
def stores(proj, *, wait: float = LOCK_WAIT):
    """Hold a project's stores — **both** of them — for the duration of a block.

    The unit a caller usually wants, because the two stores are only meaningful
    together: a task names a decision, and the pair can be invalid while each
    half is fine. Two callers need that:

    - **an apply**, which writes one store having judged it against the other,
      and which `dg apply` runs twice in a row — once per batch. Held around the
      pair rather than around each batch, or the two stores are momentarily out
      of step with each other and nothing said the state in between was legal.
      See `dgraph/applying.py`; it was audit F27.
    - **`check.run`**, which reads both and judges the link between them. A
      reader that takes no lock can read one store from before a write and the
      other from after, and then report a `link_resolves` that never existed —
      which the commit gate turns into a denial for every agent in the
      repository.

    Always in the same order, so two holders cannot deadlock against each other.
    Stores the project does not have are not locked, because a caller of *this*
    function is reading and writing a pair that exists — and a lock file beside
    a file that does not exist would be litter.

    **That is a statement about this function's callers, not about the file.**
    There is one race on a store that is not there: two bootstraps creating it,
    where the loser's whole import is gone with nothing said. `dg init`,
    `dg task init` and `cli._adopt` therefore take `held` on the store
    directly, spanning their "already exists" check as well as the write, and
    must not be moved onto this function to tidy them up. Audit `R-F1`.

    **Nests.** An inner `stores()` for a project this thread already holds is a
    no-op, so `applying.apply_decisions` can be called on its own *or* inside a
    `writing()` that spans both batches without the second acquisition
    deadlocking on the first — `held`'s in-process lock is not re-entrant, and
    the file lock would be worse: the inner release would delete the outer
    holder's lock file and let a third writer in.
    """
    key = str(proj.root)
    depth = _pair_depth()
    if depth.get(key):
        depth[key] += 1
        try:
            yield
        finally:
            depth[key] -= 1
        return
    depth[key] = 1
    try:
        with contextlib.ExitStack() as stack:
            for path, present in ((proj.store, proj.has_decisions),
                                  (proj.tasks, proj.has_tasks)):
                if present:
                    stack.enter_context(held(path, wait=wait))
            yield
    finally:
        depth[key] -= 1


#: Which paths this thread already holds, so `held` below can nest. Per-thread
#: for the reason `_pairs` is, and separate from it because the unit differs: a
#: pair is keyed by project, a lock by the file it names.
_singles = threading.local()


def _single_depth() -> dict[str, int]:
    if not hasattr(_singles, "depth"):
        _singles.depth = {}
    return _singles.depth


@contextlib.contextmanager
def held(path: Path, *, wait: float = LOCK_WAIT):
    """Hold `path` for a read-modify-write, so two writers cannot lose one.

    Never raises, and never refuses to run the body. A lock that cannot be
    created — a read-only directory, a filesystem without `O_EXCL`, a holder
    that is alive but wedged — degrades to the unsynchronised behaviour that
    was there before it, because refusing to write would be a worse failure
    than the race it guards.

    The in-process lock is taken first and always succeeds, so two threads are
    separated even when the file lock is unavailable.

    **Nests**, exactly as `stores` does and for the same reason. Every act this
    tool has to make atomic is a *route* that owns a lock its own callees also
    take: `applying.trays` holds the tray across an apply whose `pending.discard`
    takes it again, and `integrate.adopt` holds the quarantine file while calling
    `pending.stage_all`, which takes the trays. Without nesting the first is a
    deadlock against itself — the in-process lock is a plain `threading.Lock` —
    and the file lock would be worse: the inner release would delete the outer
    holder's lock file and admit a third writer, which is precisely what
    `_release` checks the pid to avoid doing to somebody else. Audit W-F2.

    The depth is per thread because the lock it guards is: `dg serve` is a
    `ThreadingHTTPServer`, and two Apply clicks are two threads that must not
    see each other's depth.
    """
    key, depth = str(path), _single_depth()
    if depth.get(key):
        depth[key] += 1
        try:
            yield
        finally:
            depth[key] -= 1
        return
    depth[key] = 1
    try:
        with _one_holder(path, wait):
            yield
    finally:
        depth[key] -= 1


@contextlib.contextmanager
def _one_holder(path: Path, wait: float):
    """The acquisition itself, once. Split from `held` only so the nesting
    bookkeeping above reads as bookkeeping."""
    lock = path.with_name(path.name + ".lock")
    with _thread_lock(path):
        fd = _take(lock, wait)
        try:
            yield
        finally:
            _release(lock, fd)
