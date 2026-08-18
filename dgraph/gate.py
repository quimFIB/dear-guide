"""The commit gate: is this shell command about to record a contradiction?

A host asks by handing over the command it is about to run; the answer is a
verdict and a reason. Nothing here knows which host is asking.

That is the point. A host adapter is translation and nothing else, so the
interesting parts — recognising a commit inside an arbitrary shell command, and
deciding what to do about it — exist once, in the language this repo has tests
in. An adapter that re-implemented either would be a second implementation of
the rule in a place where a mistake is invisible, and a second host would make
it a third.

Three verdicts, matching what hosts can express:

  allow   say nothing, run the command
  ask     put it to the human — a judgement call about work in progress
  deny    refuse, and tell the model what to do instead
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from dgraph import check as _check
from dgraph import pending, project

# `&&`, `||`, `;`, `|`, `&` end a command; `shlex` with `punctuation_chars`
# hands each of them over as its own token.
SEPARATORS = frozenset({"&&", "||", ";", "|", "&", "(", ")"})
# Prefixes that wrap a command without being one.
WRAPPERS = frozenset({"sudo", "env", "time", "nohup", "command", "exec"})
# git's own options, before the subcommand, that take a separate value.
GIT_VALUE_OPTS = frozenset({"-C", "-c", "--git-dir", "--work-tree",
                            "--namespace", "--exec-path"})
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _off() -> bool:
    return os.environ.get("DG_HOOK_OFF", "").strip() not in ("", "0", "false", "no")


def _segments(command: str) -> list[list[str]]:
    """The command split into the individual commands it runs.

    Lines first, because an agent's `git add -A\\ngit commit -m …` is one string
    to the host and two commands to the shell, and a gate that only looked at the
    first token would wave the second one through.

    On unbalanced quotes this raises `ValueError`, and the caller allows: the
    shell will not run the command either, so there is nothing to gate.
    """
    out: list[list[str]] = []
    for line in command.splitlines():
        lex = shlex.shlex(line, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        seg: list[str] = []
        for tok in lex:
            if tok in SEPARATORS:
                if seg:
                    out.append(seg)
                seg = []
            else:
                seg.append(tok)
        if seg:
            out.append(seg)
    return out


@dataclass(frozen=True)
class _Commit:
    """One commit found in a command: where git was pointed, if anywhere.

    `cdir` is the composed `-C` value (git chains successive `-C`s the same
    way). `gitdir` flags `--git-dir`, which points at a `.git` directory
    rather than a worktree — comparing repositories through it is a different
    computation, so the gate treats it as "unknown target" and keeps gating.
    """
    cdir: str | None = None
    gitdir: bool = False


def _parse_commit(argv: list[str]) -> _Commit | None:
    """The `_Commit` if this one command actually commits, else None."""
    i = 0
    while i < len(argv) and (ASSIGNMENT.match(argv[i]) or argv[i] in WRAPPERS):
        i += 1
    argv = argv[i:]
    if not argv or os.path.basename(argv[0]) != "git":
        return None

    cdirs: list[str] = []
    gitdir = False
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok in GIT_VALUE_OPTS:
            if tok == "-C" and i + 1 < len(argv):
                cdirs.append(argv[i + 1])
            if tok in ("--git-dir", "--work-tree"):
                gitdir = True
            i += 2
            continue
        if tok.startswith(("--git-dir=", "--work-tree=")):
            gitdir = True
            i += 1
            continue
        if tok.startswith("-"):
            i += 1
            continue
        if tok != "commit":
            return None
        # `--dry-run` writes nothing. Deliberately not `-n`, which for
        # `git commit` means `--no-verify` — precisely the commits somebody is
        # trying to slip past a hook, and the last ones to wave through.
        if "--dry-run" in argv[i:]:
            return None
        return _Commit(cdir=os.path.join(*cdirs) if cdirs else None,
                       gitdir=gitdir)
    return None


def _commits(command: str) -> list[_Commit]:
    try:
        segments = _segments(command)
    except ValueError:
        return []
    return [c for c in (_parse_commit(seg) for seg in segments) if c is not None]


def is_commit(command: str) -> bool:
    """Whether this shell command commits. Known misses, documented not fixed:
    `bash -c "git commit …"`, git aliases (`git ci`), and `gh` merge paths."""
    return bool(_commits(command))


def _toplevel(path: Path) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse",
                            "--show-toplevel"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _targets_this_repo(commit: _Commit, root: Path) -> bool:
    """Whether the commit lands in the same repository as the project.

    A `git -C /elsewhere commit` records nothing about this graph, and gating
    it against this project's state is a false positive. Every doubt resolves
    to True — no `-C` (the cwd's repo, the case the gate has always covered),
    a `--git-dir`/`--work-tree` override, an unresolvable path, no git —
    because the gate's conservative direction is to keep gating.
    """
    if commit.cdir is None or commit.gitdir:
        return True
    ours = _toplevel(root)
    theirs = _toplevel(Path(commit.cdir))
    if ours is None or theirs is None:
        return True
    return ours == theirs


def _staged(root: Path) -> set[Path]:
    """Absolute paths in git's index. Empty on any git trouble — a gate that
    guessed here would deny commits in directories that are not repositories."""
    def git(*args: str) -> str | None:
        try:
            r = subprocess.run(["git", "-C", str(root), *args],
                               capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout if r.returncode == 0 else None

    top = git("rev-parse", "--show-toplevel")
    names = git("diff", "--cached", "--name-only", "-z")
    if top is None or names is None:
        return set()
    base = Path(top.strip())
    return {(base / n).resolve() for n in names.split("\0") if n}


def _allow() -> dict:
    return {"verdict": "allow", "reason": ""}


def verdict(command: str, proj: project.Project | None = None) -> dict:
    """The gate. Never raises, never blocks on anything, always answers.

    "Never raises" is load-bearing: both host adapters read a crash as "no
    verdict" and run the command, so an exception here is a gate that fails
    open on exactly the state it cannot vouch for. Anything unexpected
    therefore becomes a deny that says what could not be read.
    """
    try:
        return _verdict(command, proj)
    except Exception as exc:
        return {
            "verdict": "deny",
            "reason": f"dg gate could not judge this commit ({exc!r}). "
                      f"`dg check` and `dg pending` show the state it failed "
                      f"to read; fix that first, then retry.",
        }


def _verdict(command: str, proj: project.Project | None = None) -> dict:
    if _off():
        return _allow()
    commits = _commits(command)
    if not commits:
        return _allow()
    proj = proj or project.find()
    if not proj.exists:
        return _allow()
    if not any(_targets_this_repo(c, proj.root) for c in commits):
        return _allow()

    problems = [v for v in _check.run(proj) if v.blocking]
    if problems:
        fixes = ["`dg check` names every rule that broke"]
        if any(v.check == "stale_view" for v in problems):
            fixes.insert(0, f"`dg render` regenerates {proj.view.name}")
        return {
            "verdict": "deny",
            "reason": "The decision graph is not valid, so this commit would "
                      "record a contradiction:\n"
                      + "\n".join(f"  {v}" for v in problems)
                      + "\nFix it first: " + "; ".join(fixes) + ".",
        }

    # `dg check` compares the *worktree* to the store, so it cannot see this:
    # `git add decisions.json && git commit` passes every invariant and still
    # produces a commit whose store and view disagree — the exact drift this tool
    # exists to prevent. Not fixed automatically: rendering from here would mean
    # writing and staging a file mid-command, which is more magic than a decision
    # log deserves.
    staged = _staged(proj.root)
    if proj.store.resolve() in staged and proj.view.resolve() not in staged:
        return {
            "verdict": "deny",
            "reason": f"{proj.store.name} is staged but {proj.view.name} is not, "
                      f"so the commit would record a store and a view that "
                      f"disagree. Run `dg render`, then "
                      f"`git add {proj.view.name}`.",
        }

    try:
        ops = pending.load(proj.pending)
    except Exception as exc:
        # An unreadable staging file may hold staged decision work, and this
        # commit would drop it with no trace in the diff — the one situation
        # the `ask` verdict below exists for, unreadable. Fail closed.
        return {
            "verdict": "deny",
            "reason": f"{proj.pending.name} could not be read ({exc}), so it "
                      f"is impossible to tell whether decision op(s) are "
                      f"staged and about to be lost. Inspect the file, or "
                      f"discard it with `dg clear`, then retry.",
        }
    if ops:
        return {
            "verdict": "ask",
            "reason": f"{len(ops)} decision op(s) are staged and not applied. "
                      f"{proj.pending.name} is gitignored, so committing now "
                      f"drops them from the record with no trace in the diff. "
                      f"`dg apply` writes them; `dg clear` discards them.",
        }
    return _allow()
