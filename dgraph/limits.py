"""The two limits on an agent: where it may write, and how long it may run.

`$DG_DECIDE` in `cross.py` limits what an agent may *record*. These limit what
it may do to the machine and to the clock. All three are read the same way, are
declared by the launcher, and are never consulted for a caller with no
`$DG_AGENT` -- a supervisor is unaffected by every value, which is the shape the
ownership model already has.

## Why this module cannot enforce anything by itself

`$DG_DECIDE` works because **every decision goes through `dg`**. A write does
not. An agent writes with its host's own tools, and `dg` is not in that path at
all -- so a check that lived only here would be a rule nothing ever consulted.

The enforcement point that does exist is `dg gate`, and it is already
general: it takes a thing about to happen, answers `allow` / `warn` / `ask` /
`deny` with a reason, and **both host adapters relay that answer holding no
policy of their own** -- `hooks/precommit.py` under Claude Code and
`tool.execute.before` under opencode. Widening the gate from commands to writes
therefore widens it for every host at once, and a third host earns the same
behaviour by relaying the same verdict.

So the division is: the launcher declares, this module judges, the adapter
enforces. Nothing here is a security boundary, for the same reason nothing in
`cross.POLICIES` is one -- `$DG_AGENT` is self-declared, so an agent that unset
these *is* the supervisor. It is a rule the launcher sets so that an honest
mistake is caught.

## The write scope

    open      (default) anywhere. What the tool has always done, and what every
              project that has never heard of this gets.
    launch    the project root and the system temporary directory. Anywhere
              else is the person's call.

**There is no third value, and no `deny`.** A scope narrower than `launch` --
temp only, say -- would refuse a scout writing the findings it was launched to
produce, which is the ordinary case rather than an edge one. And an out-of-scope
write is answered `ask`, never `deny`: the rule is *consent*, not prohibition,
and a refusal the person cannot lift from where they are standing is
indistinguishable from a broken tool. `ask` is the verdict both adapters already
translate into "this is the user's call".

Reads are never judged. An agent that cannot read the repository it is reasoning
about is not constrained, it is blindfolded, and every interesting thing a
fan-out does starts by reading something outside its own directory.

## The budget

`$DG_BUDGET` is how long an agent may run before it hands its work back --
seconds, or a suffixed span (`30m`, `2h`), or `infinite` for no limit, which is
the default. It is recorded on the lease at `dg agent claim` so that a
supervisor reading `dg agent list` sees the same number the agent was given.

**What a budget buys is the hand-back, not the stopping.** A task left `DOING`
by an agent that died reads exactly like one being worked on -- the failure the
lease file exists to make visible -- and `dg task park --why` is the verb that
already fixes it. So expiry parks what the agent holds, with the reason naming
the budget. Whether the agent's process also stops is the launcher's business,
the way every other flag is; `timeout 1800 <however you spawn an agent>` is the
whole of it under any host.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

#: `$DG_WRITE`, and what its values mean. See the module docstring.
WRITE_POLICIES = ("open", "launch")
WRITE_ENV = "DG_WRITE"

#: `$DG_BUDGET`. The value is a span; `infinite` and unset both mean no limit.
BUDGET_ENV = "DG_BUDGET"

#: `$DG_SILENT_AFTER`: how long an agent may go without touching `dg` before
#: `dg agent list` says so. **Not a limit** -- nothing acts on it, and that is
#: the whole design. An elapsed budget is a fact about the clock; silence is a
#: suspicion, because an agent in a forty-minute build looks exactly like one
#: that died in the first minute of it. The two must never share a verb.
#:
#: The default is deliberately far above any plausible think-time, and it is
#: raised rather than lowered for a fan-out doing long compiles.
SILENT_ENV = "DG_SILENT_AFTER"
SILENT_DEFAULT = 900

#: What `infinite` may be spelled as. `0` is deliberately NOT among them: a
#: budget of zero seconds is a plausible typo for "no budget" and the two
#: readings are opposites, so it is refused rather than guessed at.
INFINITE = ("infinite", "inf", "none", "unlimited", "forever")

#: Suffixes a span may carry, in seconds. Bare digits are seconds.
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def write_policy(value: str | None = None) -> str:
    """What `$DG_WRITE` says, defaulting to today's behaviour.

    An unrecognised value is `open` rather than an error, for the reason
    `cross.policy` gives: this is read on the path of every judged write, and a
    typo in a launcher's environment must not make the tool unusable for the
    supervisor too. The CLI reports the typo where it is set instead.
    """
    val = (value if value is not None
           else os.environ.get(WRITE_ENV) or "").strip().lower()
    return val if val in WRITE_POLICIES else WRITE_POLICIES[0]


class BadSpan(ValueError):
    """A budget that could not be read. Raised rather than defaulted.

    Unlike `$DG_WRITE`, a misread budget is not a wider rule -- it is a
    *different number*, and both directions are wrong in a way nobody would
    notice until an agent was parked hours early or never. The launcher is
    told at claim time, which is the one moment it can still be fixed.
    """


def span(text: str | None) -> int | None:
    """A budget as seconds. `None` is no limit; raises `BadSpan` on nonsense.

    Accepts a bare count of seconds, a single suffixed span (`30m`, `2h`), or
    one of `INFINITE`. Deliberately not a duration mini-language: `1h30m` is
    not accepted, because parsing it is the beginning of a parser and `5400`
    is unambiguous.
    """
    raw = (text or "").strip().lower()
    if not raw or raw in INFINITE:
        return None
    unit = _UNITS.get(raw[-1], 1)
    digits = raw[:-1] if raw[-1] in _UNITS else raw
    if not digits.isdigit():
        raise BadSpan(
            f"{text!r} is not a budget. Give seconds (`1800`), a span "
            f"(`30m`, `2h`), or `infinite`")
    total = int(digits) * unit
    if total <= 0:
        raise BadSpan(
            f"{text!r} is a budget of nothing. Say `infinite` if that is what "
            f"is meant -- zero and unlimited are opposites, so this is not "
            f"guessed at")
    return total


def show_span(seconds: int | None) -> str:
    """A budget, for a person reading `dg agent list`. Never lossy."""
    if seconds is None:
        return "infinite"
    for suffix, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{seconds // size}{suffix}"
    return f"{seconds}s"


def approx_span(seconds: int) -> str:
    """A duration for a person to glance at. Lossy, deliberately.

    `show_span` round-trips through `span` and so cannot round: it renders 2401
    seconds as `2401s`, which is correct and unreadable, and an elapsed time
    nobody will ever re-parse is the case that wants the other trade. Used for
    the columns that measure *how long ago*; the budget itself keeps
    `show_span`, because that number was typed by a person and should come back
    the way they wrote it.
    """
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        hours, rest = divmod(seconds, 3600)
        return f"{hours}h" if rest < 60 else f"{hours}h{rest // 60}m"
    return f"{seconds // 86400}d"


def budget(value: str | None = None) -> int | None:
    """`$DG_BUDGET` as seconds, or `None` for no limit.

    A bad value here is *not* raised: by the time an agent is running, refusing
    every `dg` call over a malformed environment variable would take the graph
    away from the one caller still able to record what happened. `dg agent
    claim` validates it at the moment it is set, which is where the error
    belongs.
    """
    try:
        return span(value if value is not None else os.environ.get(BUDGET_ENV))
    except BadSpan:
        return None


def silent_after(value: str | None = None) -> int:
    """`$DG_SILENT_AFTER` as seconds, defaulting to `SILENT_DEFAULT`.

    A bad value is the default rather than an error, unlike `--budget`: nothing
    acts on this number, so misreading it costs a column that says the wrong
    thing rather than work parked at the wrong moment.
    """
    try:
        return span(value if value is not None
                    else os.environ.get(SILENT_ENV)) or SILENT_DEFAULT
    except BadSpan:
        return SILENT_DEFAULT


def _real(path) -> str:
    """Resolve without requiring existence -- a write's target may be new.

    `os.path.realpath` resolves what it can and leaves the rest, which is what
    a not-yet-created file needs. Symlinks are followed on purpose: `/tmp`
    resolving elsewhere, or a link out of the project into somewhere it was not
    launched from, are both exactly what the scope is being asked about.
    """
    return os.path.realpath(os.path.expanduser(str(path)))


def _within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        # Different drives, or a relative path that escaped resolution. Not
        # within, and not an error: the caller wants a verdict, not a crash.
        return False


def writable_roots(root: Path | None) -> list[str]:
    """Where `launch` lets an agent write: the project, and scratch.

    `root` is the project -- where the graph is -- rather than the agent's
    current directory, and that is the point. A `cd` is not a change of remit,
    and a scope anchored to the working directory would widen every time an
    agent walked somewhere else, which is the one direction a limit must not
    move on its own.
    """
    out = [_real(tempfile.gettempdir()), _real("/tmp")]
    if root is not None:
        out.append(_real(root))
    return sorted(set(out))


def refuse_write(path, owner: str | None, root: Path | None,
                 chosen: str | None = None) -> str | None:
    """Why this write is the person's call -- or `None` if the agent may make it.

    `owner` is `pending.owner()`: `None` is the supervisor and is never
    refused, exactly as in `cross.refuse_close`.
    """
    if owner is None:
        return None
    if write_policy(chosen) == "open":
        return None
    if not str(path).strip():
        return None
    target = _real(path)
    roots = writable_roots(root)
    if any(_within(target, r) for r in roots):
        return None
    where = ", ".join(roots)
    return (f"${WRITE_ENV}=launch: {owner} may write under {where}, and this "
            f"is outside all of them ({target}). Reading anywhere is fine; "
            f"writing here is the person's call. Approve it, or point the "
            f"write somewhere in scope")
