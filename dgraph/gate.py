"""The commit gate: is this shell command about to record a contradiction?

A host asks by handing over the command it is about to run; the answer is a
verdict and a reason. Nothing here knows which host is asking.

That is the point. A host adapter is translation and nothing else, so the
interesting parts — recognising a commit inside an arbitrary shell command, and
deciding what to do about it — exist once, in the language this repo has tests
in. An adapter that re-implemented either would be a second implementation of
the rule in a place where a mistake is invisible, and a second host would make
it a third.

Four verdicts, matching what hosts can express:

  allow   say nothing, run the command
  warn    run the command, and say one thing about it on the way past
  ask     put it to the human — a judgement call about work in progress
  deny    refuse, and tell the model what to do instead

`warn` is the newest and the narrowest. It exists because a generated view that
has fallen behind its store is worth *mentioning* at the moment a commit records
it and is not worth refusing over — see `check._decisions` on why that severity
changed. Claude Code carries it as a top-level `systemMessage`, which shows the
text and then lets the call proceed through the user's own permission rules;
`allow` stays silent, deliberately, because an explicit allow would override
those rules for every git command in the session.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from dgraph import check as _check
from dgraph import limits, pending, project

# `&&`, `||`, `;`, `|`, `&` end a command; `shlex` with `punctuation_chars`
# hands each of them over as its own token.
SEPARATORS = frozenset({"&&", "||", ";", "|", "&", "(", ")"})
# Prefixes that wrap a command without being one.
WRAPPERS = frozenset({"sudo", "env", "time", "nohup", "command", "exec"})
# git's own options, before the subcommand, that take a separate value.
GIT_VALUE_OPTS = frozenset({"-C", "-c", "--git-dir", "--work-tree",
                            "--namespace", "--exec-path"})
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: Per-`git` timeout. Every call here is a metadata question about a local
#: repository — `rev-parse`, `diff --name-only`, `ls-files` — so seconds are
#: already generous and the old ten meant a handful of wedged calls could
#: outlast the *hook's* own budget, at which point the adapter gave up and the
#: commit went through ungated. See `hooks/precommit.py`.
GIT_TIMEOUT = 5

# `git commit`'s own options that take a *separate* value, so the token after
# them is that value and not a pathspec. `-m x` is the one that matters; every
# other entry is here so that it cannot be mistaken for a path either.
COMMIT_VALUE_OPTS = frozenset({
    "--message", "--reedit-message", "--reuse-message", "--file", "--template",
    "--author", "--date", "--cleanup", "--fixup", "--squash", "--trailer",
    "--pathspec-from-file",
})
#: Their short forms. `-S`/`-u` are deliberately absent: those take an attached
#: value or none, so a bare token after them really is a pathspec.
COMMIT_VALUE_SHORT = "mcCFt"
#: A pathspec this gate will not try to resolve. Magic (`:(glob)`, `:!x`) and
#: wildcards mean git's matching rules, not the filesystem's.
PATHSPEC_MAGIC = ("*", "?", "[", ":")


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

    `paths` are the pathspecs the commit carries, and they change what the
    commit *records*: `git commit -- decisions.json` ignores the index and
    commits the worktree version of that one file. A gate that only looked at
    the index would wave through exactly the store-without-its-view commit the
    pair check exists to refuse. `opaque` says a pathspec was present but could
    not be read as plain paths — a wildcard, git's pathspec magic, or
    `--pathspec-from-file` — in which case the index check stands and this is a
    known miss rather than a silent one.
    """
    cdir: str | None = None
    gitdir: bool = False
    paths: tuple[str, ...] = ()
    opaque: bool = False


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
        paths, opaque = _pathspecs(argv[i + 1:])
        return _Commit(cdir=os.path.join(*cdirs) if cdirs else None,
                       gitdir=gitdir, paths=paths, opaque=opaque)
    return None


def _pathspecs(argv: list[str]) -> tuple[tuple[str, ...], bool]:
    """The pathspecs in the arguments after `commit`, and whether any is opaque.

    Everything after a bare `--` is a path. Before it, a bare word is a path
    too — but only once the options that swallow the token after them have been
    accounted for, or `git commit -m fix` would read `fix` as a filename and
    every commit with a message would look like a partial one.
    """
    paths: list[str] = []
    opaque = False
    sep = False
    i = 0
    while i < len(argv):
        tok = argv[i]
        i += 1
        if sep:
            paths.append(tok)
            continue
        if tok == "--":
            sep = True
        elif tok.startswith("--"):
            name = tok.split("=", 1)[0]
            if name == "--pathspec-from-file":
                opaque = True       # the paths are in a file we do not read
            elif "=" not in tok and name in COMMIT_VALUE_OPTS:
                i += 1              # the next token is this option's value
        elif tok.startswith("-") and len(tok) > 1:
            # A short cluster: `-am msg` is `-a -m msg`, so the value is the
            # next token only when the value-taking letter ends the cluster.
            for k, ch in enumerate(tok[1:], start=1):
                if ch in COMMIT_VALUE_SHORT:
                    if k == len(tok) - 1:
                        i += 1
                    break
        else:
            paths.append(tok)
    if any(p.startswith(PATHSPEC_MAGIC) or any(m in p for m in "*?[")
           for p in paths):
        opaque = True
    return tuple(paths), opaque


#: `dg` subcommands that remove a node, and the only ones a host is asked
#: about. Everything else `dg` does is additive, or loses a claim without
#: inventing one, and the staging tray already makes those reviewable. These
#: are different: `--splice` and `--into` assert an edge nobody wrote, and even
#: a plain removal is the one act this tool keeps no record of.
REMOVALS = (("rm",), ("task", "rm"))

#: `dg`'s own options, before the subcommand, that take a separate value.
DG_VALUE_OPTS = frozenset({"--project", "-C"})


def _dg_removal(argv: list[str]) -> str | None:
    """The removal this one command runs, printable, or None.

    Known misses, documented rather than fixed, exactly as `is_commit`'s are:
    `bash -c "dg rm D02"`, a shell alias, and `python -m dgraph.cli rm`. The
    gate is a second belt here and not the only one — `dg rm` also refuses
    without `--yes` when nobody is at a terminal — so a miss costs a prompt,
    not the safeguard.
    """
    i = 0
    while i < len(argv) and (ASSIGNMENT.match(argv[i]) or argv[i] in WRAPPERS):
        i += 1
    argv = argv[i:]
    if not argv or os.path.basename(argv[0]) != "dg":
        return None
    words: list[str] = []
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok in DG_VALUE_OPTS:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        words.append(tok)
        i += 1
    for name in REMOVALS:
        if tuple(words[:len(name)]) == name:
            return "dg " + " ".join(name)
    return None


def _removals(command: str) -> list[str]:
    try:
        segments = _segments(command)
    except ValueError:
        return []
    return [r for r in (_dg_removal(seg) for seg in segments) if r]


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
                           capture_output=True, text=True,
                           timeout=GIT_TIMEOUT)
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


def _git(root: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, text=True,
                           timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _staged(root: Path) -> set[Path]:
    """Absolute paths in git's index. Empty on any git trouble — a gate that
    guessed here would deny commits in directories that are not repositories."""
    top = _git(root, "rev-parse", "--show-toplevel")
    names = _git(root, "diff", "--cached", "--name-only", "-z")
    if top is None or names is None:
        return set()
    base = Path(top.strip())
    return {(base / n).resolve() for n in names.split("\0") if n}


def _dirty(root: Path) -> set[Path] | None:
    """Absolute paths that differ from HEAD — tracked changes and untracked
    files alike. `None` on any git trouble, which callers read as "cannot tell".

    Needed only by the pathspec branch below, and needed there because a
    pathspec commit is the normal way to commit part of a dirty tree: naming
    the store and not the view is a contradiction *only if the view has
    something to record*. Leaving out a view that already matches HEAD records
    nothing and must not be refused.
    """
    top = _git(root, "rev-parse", "--show-toplevel")
    tracked = _git(root, "diff", "--name-only", "HEAD", "-z")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    if top is None or tracked is None or untracked is None:
        return None
    base = Path(top.strip())
    return {(base / n).resolve()
            for n in (tracked + untracked).split("\0") if n}


def _records(commit: _Commit, root: Path, staged: set[Path]):
    """Whether a given file will be part of what this commit records.

    From the index, unless the commit carries pathspecs it can read — those
    override the index. An opaque pathspec (a wildcard, `--pathspec-from-file`)
    falls back to the index rather than being guessed at: that is the same
    answer the gate gave before pathspecs were parsed at all, so it is a known
    miss and never a new one.
    """
    def index(path: Path) -> bool:
        return path.resolve() in staged

    if not commit.paths or commit.opaque:
        return index
    # git resolves a pathspec against the shell's working directory — the same
    # directory the gate was invoked in, since a host hands over the command it
    # is about to run there. `-C` moves it, exactly as it moves git's.
    base = Path(commit.cdir) if commit.cdir else Path.cwd()
    spec = set()
    for raw in commit.paths:
        try:
            spec.add((base / raw).resolve())
        except OSError:
            return index                               # unresolvable
    here = root.resolve()
    if not any(s == here or here in s.parents or s in here.parents
               for s in spec):
        # Nothing the pathspec names is anywhere near this project, which most
        # likely means `base` is not where the command will actually run. Fall
        # back to the index rather than conclude from a computation whose
        # premise is wrong: that is the answer the gate gave before pathspecs
        # were parsed at all, so it can only be a known miss, never a new one.
        return index
    dirty = _dirty(root)

    def records(path: Path) -> bool:
        p = path.resolve()
        if any(p == s or s in p.parents for s in spec):
            return True
        # Not named — but a file with nothing to record is not being left out
        # of anything. Only a file that actually differs from HEAD counts as
        # excluded, or committing one half of a pair whose other half is
        # already up to date would be refused for no reason.
        return dirty is not None and p not in dirty

    return records


#: The substrings a command must contain before it can possibly earn anything
#: but `allow`. Both host adapters test this first and skip `dg gate` entirely
#: when none is present, which keeps a subprocess out of every unrelated shell
#: call. A fast path, never a policy.
#:
#: It is a **contract**, and that is why it lives here rather than as a literal
#: in each adapter: everything `_verdict` can refuse has to be reachable from
#: one of these words, or the adapter never asks and the verdict is unreachable
#: from that host. Not hypothetical — both adapters tested for `"commit"` alone,
#: which was the whole story until `REMOVALS` was added and was quietly not the
#: whole story afterwards, so `dg rm D02` (which this module answers `ask` on)
#: was waved through by Claude Code and opencode alike. Anything added to
#: `_verdict` from here on has to be reachable from this tuple, and
#: `tests/test_gate.py` fails if it is not.
#:
#: Deliberately loose. `rm` matches `rm -rf build` and `git commit -m "fix the
#: form"` too; a false positive costs one `dg gate` that answers allow, while a
#: false negative costs the safeguard. `dg gate --triggers` prints them, so an
#: adapter in a language this repo does not test can be checked against the
#: real list rather than against a comment.
TRIGGERS = ("commit", "rm")


def may_trigger(command: str) -> bool:
    """Whether `command` is worth handing to `verdict` at all.

    The predicate the adapters implement inline — they are stdlib-only and
    cannot import this module — kept here so there is something for them to be
    tested against. See `TRIGGERS`.
    """
    return any(t in command for t in TRIGGERS)


def _allow() -> dict:
    return {"verdict": "allow", "reason": ""}


def _warn(reason: str) -> dict:
    """Permit, and say one thing. See the module docstring."""
    return {"verdict": "warn", "reason": reason}


#: Which store a check belongs to, for the deny's opening sentence.
_SUBJECT = {
    frozenset({"decision"}): "The decision graph is not valid",
    frozenset({"task"}): "The task graph is not valid",
    frozenset({"link"}): "The link between the two graphs is not valid",
}


def _origin(v) -> str | None:
    """Which store a finding is about — read off the finding, not its name.

    This used to classify by prefix, and nine of the thirty-six check names
    never carried one: `released_by_drop`, `orphaned_by_drop`,
    `parked_holding_work` and the five `evidence_*` rules all fell through to
    "decision", so a corrupt `tasks.json` in a project with no decision store
    at all was refused with "The decision graph is not valid". `store_loads`
    could not be classified by any naming scheme, since all three validators
    emit it.

    `None` when nothing claimed it — which `_SUBJECT` renders as the plural
    subject rather than guessing a store.
    """
    return v.origin


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


def write_verdict(path: str, proj: project.Project | None = None) -> dict:
    """The same gate, asked about a write instead of a command.

    This is what makes the agent write scope host-neutral. The policy is
    `limits.refuse_write`; the two adapters already relay a verdict and hold no
    policy of their own, so widening the gate here widens it under Claude Code
    and opencode at once, and a third host earns it by relaying the same shape.

    Only ever `allow` or `ask`. The rule is consent rather than prohibition —
    see `limits` — so there is no write this can deny outright, and an adapter
    that only knows how to relay a refusal still passes the reason on.

    Never raises, for the reason `verdict` gives: an exception reads as "no
    verdict" and the write proceeds. Unlike `verdict` the fallback is `allow`
    rather than `deny`, and the asymmetry is deliberate. A commit the gate
    could not judge might contradict the record permanently; a write it could
    not judge is an ordinary file operation the person did not ask to be
    consulted about. Failing closed here would turn every unreadable project
    into a wall of prompts about writes nobody was worried about.
    """
    try:
        p = proj if proj is not None else project.find()
        root = getattr(p, "root", None)
    except Exception:
        root = None
    try:
        reason = limits.refuse_write(path, pending.owner(), root)
    except Exception:
        return _allow()
    return {"verdict": "ask", "reason": reason} if reason else _allow()


def exec_verdict(command: str) -> dict:
    """The same gate, asked whether an agent may run this at all.

    A sibling of `write_verdict` rather than a branch of `verdict`, and the
    distinction is the whole point. `verdict` answers *would this commit leave
    the record contradicting itself* — a question about the graph, reached only
    for commands `may_trigger` recognises. This answers *may this agent run
    this program*, which is a question about the remit and has nothing to say
    about `TRIGGERS`.

    **Nothing calls this yet, deliberately.** Both adapters send only what
    `may_trigger` matches, so wiring this in means widening what they send —
    every command rather than the two words — and until a broker exists to
    answer an `ask`, a headless run has nobody to ask and every command would
    be refused. The policy ships first; the switch is its own change.

    Only ever `allow` or `ask`, for the reason `write_verdict` gives: consent
    rather than prohibition. Never raises, and falls back to `allow` for the
    same reason it does there — a command this could not judge is not a record
    this could not vouch for.
    """
    try:
        reason = limits.refuse_exec(command, pending.owner())
    except Exception:
        return _allow()
    return {"verdict": "ask", "reason": reason} if reason else _allow()


#: The verdicts, weakest first. `combine` returns the strongest, which is the
#: only ordering that makes a compound command safe: a `deny` reached by one
#: half of `dg rm D01 && git commit` cannot be softened by the other half's
#: `ask`, and an `ask` cannot be softened by a `warn`.
RANK = ("allow", "warn", "ask", "deny")


def combine(answers: list[dict]) -> dict:
    """One answer for a command that trips more than one rule.

    The strongest verdict, carrying **every** reason. Both halves matter and
    the second is the one that used to be lost: `_verdict` returned the
    removal's `ask` before it ever looked for a commit, so `dg rm D01 && git
    commit` against an invalid store answered `ask` where the commit alone
    answers `deny`, and against a staged tray kept the right verdict but for
    the wrong reason — the operator was told about the removal and not about
    the ops the commit was about to drop.

    The `notes` branch of the commit path already combined this way; this is
    the same rule applied one level up.
    """
    answers = [a for a in answers if a["verdict"] != "allow" or a["reason"]]
    if not answers:
        return _allow()
    best = max(RANK.index(a["verdict"]) for a in answers)
    reasons = [a["reason"] for a in answers if a["reason"]]
    return {"verdict": RANK[best], "reason": "\n".join(dict.fromkeys(reasons))}


def _verdict(command: str, proj: project.Project | None = None) -> dict:
    if _off():
        return _allow()

    # A floor of `ask` and a note, rather than an answer: this is not a
    # question about whether the graph is valid — a removal is legal, and the
    # tool will refuse an invalid one on its own. It is a question about
    # whether a person meant it. `ask` is the only verdict that puts that to a
    # human, and it is the reason the removal commands exist behind it rather
    # than not at all.
    #
    # It used to `return` here, which answered the removal and never looked at
    # the rest of the command. Moving the check *below* the commit path would
    # only invert that — a removal alongside a clean commit would answer
    # `allow`, and the `ask` these commands exist behind would be unreachable,
    # which is the failure `TRIGGERS` was widened to fix. So neither half is
    # first: both run, and `combine` answers.
    removals = _removals(command)
    answers = []
    if removals:
        answers.append({
            "verdict": "ask",
            "reason": f"{', '.join(sorted(set(removals)))} removes a node from "
                      f"the graph. Nothing in this tool records what a removal "
                      f"takes away — `dg drop` and `dg reopen` keep their "
                      f"history, this keeps none — so git is the only way back. "
                      f"`--splice` and `--into` go further and assert an edge "
                      f"nobody wrote. Confirm this is meant.",
        })
    return combine(answers + [_commit_verdict(command, proj)])


def _commit_verdict(command: str, proj: project.Project | None = None) -> dict:
    commits = _commits(command)
    if not commits:
        return _allow()
    proj = proj or project.find()
    if not proj.exists:
        return _allow()
    if not any(_targets_this_repo(c, proj.root) for c in commits):
        return _allow()

    # **`deny`, where a staged tray gets `ask`**, and the asymmetry is the
    # point: one of them is your own unfinished thought and the other is
    # somebody else's finished one. A tray you can be asked about, because you
    # composed it and you know whether it was meant. A quarantined
    # contribution you cannot: committing over it drops work a second writer
    # did, and nothing in this repository would record that it existed.
    #
    # A file check rather than a flag on the ops, which is what quarantine buys
    # — and which also answers where "contested" lives. `dg integrate` writes
    # `.dgraph-incoming.json` and nothing else does.
    from dgraph import integrate as _integrate
    arriving = _integrate.waiting(proj.root)
    if arriving:
        return {
            "verdict": "deny",
            "reason": f"{arriving} op(s) from another writer are waiting in "
                      f"{project.INCOMING_NAME}, unadjudicated. Committing "
                      f"now records this store without them, and the file is "
                      f"gitignored — so the contribution would be gone with "
                      f"nothing saying it arrived. `dg incoming` shows what "
                      f"is contested.",
        }

    findings = _check.run(proj)
    problems = [v for v in findings if v.blocking]
    if problems:
        # No `dg render` entry here any more: the two view checks are warnings
        # now, so they can never appear among the blocking problems. They are
        # reported below instead, and only to a commit that records one.
        fixes = ["`dg check` names every rule that broke"]
        # Named by what actually broke: a task-store or link violation framed
        # as "the decision graph is not valid" sends the reader to the wrong
        # file, and the reader is often a model that will act on the sentence.
        origins = {_origin(v) for v in problems}
        subject = _SUBJECT.get(
            frozenset(origins), "The graphs this project keeps are not valid")
        return {
            "verdict": "deny",
            "reason": f"{subject}, so this commit would record a "
                      "contradiction:\n"
                      + "\n".join(f"  {v}" for v in problems)
                      + "\nFix it first: " + "; ".join(fixes) + ".",
        }

    # The generated views. **Said, not refused** — see `check._decisions` for
    # why that severity changed. What is left here is the half `dg check` cannot
    # see, and it is worth one sentence at exactly this moment because this is
    # the last point at which it is cheap to fix.
    #
    # Two ways a commit puts a view and its store out of step:
    #
    # - it records the store and not the view, or the view and not the store.
    #   Both directions matter, and the second is arguably the likelier: the
    #   generated file is the one people `git add` without thinking about what
    #   produced it, and the commit it makes is worse, since the readable
    #   artifact then names decisions the committed store has never heard of.
    # - the view in the worktree already lags the store, and this commit records
    #   that. `dg check` reports the lag; only here is it known to be going into
    #   history.
    #
    # Scoped to a commit that records one of the four files. A commit touching
    # only `src/` is not made better by being told about a generated file it has
    # nothing to do with, and that noise is most of what made the old blocking
    # version intolerable.
    #
    # "In the commit" is not always "in the index": `git commit -- <path>`
    # commits the worktree version of the named paths and ignores the index
    # entirely. `_records` answers per commit, from the pathspec when there is
    # one and from the index when there is not. Its known blind spot is a
    # `git add` earlier in the same command string, which now costs a missing
    # warning rather than a missing refusal (audit F19).
    stale = {v.check for v in findings if not v.blocking}
    notes: list[str] = []
    staged = _staged(proj.root)
    for commit in commits:
        if not _targets_this_repo(commit, proj.root):
            continue
        records = _records(commit, proj.root, staged)
        for store, view, cmd, lag in (
            (proj.store, proj.view, "dg render", "stale_view"),
            (proj.tasks, proj.task_view, "dg task render", "stale_task_view"),
        ):
            store_in, view_in = records(store), records(view)
            if not (store_in or view_in):
                continue
            if lag in stale:
                # The more fundamental of the two: rendering fixes this *and*
                # any split, so it is said instead of the split rather than
                # alongside it.
                #
                # **Missing and lagging are not the same sentence.** The views
                # are built on demand and nothing writes them as a side effect,
                # so a project that has never rendered is the ordinary state
                # rather than a corner — and "no longer matches" is false about
                # a file that has never existed, which is what the first commit
                # of every new project used to be told. `dg check` already tells
                # them apart; this says it in the same words.
                notes.append(
                    (f"{view.name} has never been generated, and this commit "
                     f"records {store.name} without it. `{cmd}` writes it; the "
                     f"view is optional, so committing as it stands is a "
                     f"choice rather than a mistake."
                     if not view.exists() else
                     f"{view.name} no longer matches {store.name}, and this "
                     f"commit records it. `{cmd}` rebuilds it; committing as it "
                     f"stands leaves the generated view behind until someone "
                     f"renders again.")
                )
                continue
            if store_in == view_in:
                continue
            # Named by which half is missing, because the remedy differs: a
            # store without its view needs the view rebuilt and added; a view
            # without its store needs the store added, or the view taken back
            # out.
            present, missing = (store, view) if store_in else (view, store)
            if commit.paths and not commit.opaque:
                how = "named in this commit"
                fix = (f"Run `{cmd}` and name {view.name} too."
                       if store_in else
                       f"Name {store.name} in the commit as well, or leave "
                       f"{view.name} out of it.")
            else:
                how = "staged"
                fix = (f"Run `{cmd}`, then `git add {view.name}`."
                       if store_in else
                       f"Run `git add {store.name}` to commit them together, "
                       f"or `git restore --staged {view.name}` to leave both "
                       f"out.")
            notes.append(
                f"{present.name} is {how} but {missing.name} is not, so the "
                f"commit records a store and a view that disagree. {fix}"
            )

    # Both trays. `.dgraph-task-pending.json` is gitignored like its sibling,
    # so unapplied task work is dropped by a commit just as silently, and an
    # unreadable tray is the case `ask` exists for with the count unreadable.
    trays = [(proj.pending, "decision", "dg clear")]
    if proj.has_tasks:
        trays.append((proj.task_pending, "task", "dg task clear"))
    waiting = []
    for path, kind, discard in trays:
        try:
            ops = pending.load(path)
        except Exception as exc:
            return {
                "verdict": "deny",
                "reason": f"{path.name} could not be read ({exc}), so it "
                          f"is impossible to tell whether {kind} op(s) are "
                          f"staged and about to be lost. Inspect the file, or "
                          f"discard it with `{discard}`, then retry.",
            }
        if ops:
            waiting.append((len(ops), kind, path.name, discard))
    # A staged tray outranks a lagging view: one loses work, the other loses
    # nothing. The note still travels with it rather than being dropped —
    # `dg apply` would settle both at once, and a person deciding about the
    # tray should be told the view is behind too.
    if waiting:
        return {
            "verdict": "ask",
            "reason": ", ".join(f"{n} {kind} op(s)" for n, kind, _, _ in waiting)
                      + " are staged and not applied. "
                      + " and ".join(name for _, _, name, _ in waiting)
                      + " is gitignored, so committing now drops them from the "
                        "record with no trace in the diff. `dg apply` writes "
                        "them; "
                      + " / ".join(f"`{d}`" for _, _, _, d in waiting)
                      + " discards them."
                      + ("\n" + "\n".join(notes) if notes else ""),
        }
    if notes:
        return _warn("\n".join(dict.fromkeys(notes)))
    return _allow()
