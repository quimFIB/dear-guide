"""The limits on an agent: where it may write, how long it may run, and how
much prose it may put in a record.

`$DG_DECIDE` in `cross.py` limits what an agent may *record*. These limit what
it may do to the machine, to the clock, and to the reader. All four are read the
same way, are declared by the launcher, and are never consulted for a caller
with no `$DG_AGENT` -- a supervisor is unaffected by every value, which is the
shape the ownership model already has.

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

## The synopsis

`$DG_TERSE` is how long a piece of prose an agent may put in a record before it
is told to put the development in a file and cite it.

**The graph is the synthesis, and the fields are labels on it.** A decision's
premises, the questions it opens, the work resting on it and the evidence
brought against it are all *edges*, and `dg context` computes the chain from
them on demand. An answer that also narrates the premise, the alternatives and
the reasoning is writing a second copy of what the structure already holds --
in the one place nothing can check it against the first. So the rule is not
mainly about length: the long field is the symptom, and the duplication is the
disease.

What is left after the duplication goes is genuinely worth keeping -- the
benchmark table, the three rejected options with their numbers -- and it goes in
a file, which every record already has a way to name. A decision cites it in
`--source`; a task names it in `--outcome`. There is deliberately no new field
for it: a second way to name a file is a second thing that can disagree with the
first, and `--source` has pointed at prose since `dg` had prose to point at.

    off      (default, and any unset or unreadable value) no limit. What the
             tool has always done.
    on       `TERSE_DEFAULT` characters.
    <n>      that many characters.

Judged per field, on the composed op, at `pending.stage_all` -- the one door
both trays and every caller go through. **Later than every other stage-time
guard in this tool, and necessarily so**: `cross.refuse_close` can refuse before
an answer is composed because it judges the *question*, while this judges the
prose itself and cannot exist before there is any. The refusal still leaves the
tray untouched, which is the property those guards were actually for.

`TERSE_FIELDS` is what it judges. `title` is not among them and cannot be: it is
the handle every other record and every reader refers to this one by, so "put it
in a file" is advice nobody can take -- a long title wants rewriting, which is a
judgement rather than a rule. `source` is not among them either, being the
citation rather than the prose.

## What this variable does NOT reach

**Only the stage-time refusal reads `$DG_TERSE`.** Two other places apply the
same idea and both read `TERSE_DEFAULT` unconditionally:

- `check._verbose`, which warns `verbose_field`;
- the `dg serve` panel, which folds a longer field behind *show all*
  (`FOLD_AT` in `static/app.html`, the same 400).

That is deliberate rather than an oversight. Both are read by a **supervisor**,
who by definition has no `$DG_AGENT` and therefore no launcher setting either --
a warning that went quiet exactly where nobody had configured anything would be
silent in every project it is for, which is every project that has not heard of
this. And the fold has to work for a graph written before the rule existed, an
imported one, and every record a person wrote by hand: none of those passes
through any policy.

The cost is that a **custom count** makes three numbers disagree. At `on` and at
`off` they all agree; at `DG_TERSE=800` an agent may stage a 700-character
answer that `dg check` warns about the moment it lands, in a panel that folds it
at 400. Nothing breaks -- the warning is advisory and the fold is not a limit --
but the three are then saying different things, which is why `on` is the value
the documentation recommends and the numeric form is for somebody who has a
reason.
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

#: `$DG_TERSE`: how much prose an agent may put in one field. See the module
#: docstring for why the rule is really about duplication rather than length.
TERSE_ENV = "DG_TERSE"

#: What `on` means, and what `dg check` warns above regardless of the
#: environment -- the check is read by supervisors, who never have this set.
#:
#: 400 characters is roughly three sentences. Chosen so that an answer written
#: the way the skill asks for one never trips it and a wall of text always
#: does: a bound tight enough to argue with is a bound that gets switched off.
TERSE_DEFAULT = 400

#: What `off` may be spelled as. Unlike `$DG_BUDGET`, `0` IS accepted here and
#: means off: a limit of zero characters would refuse every record that says
#: anything, so it has no coherent second reading to be confused with.
TERSE_OFF = ("off", "no", "none", "0", "unlimited", "infinite")

#: The op keys this judges, on either store. Both stores' prose in one tuple,
#: which is safe where `cross.py`'s barrier is not: these are string keys on a
#: dict, so nothing here learns what a task is.
#:
#: `title` is deliberately absent -- see the module docstring. So is `source`,
#: which is the citation the fix asks for and would be self-defeating to judge.
TERSE_FIELDS = ("answer", "falsifier", "note", "outcome", "why", "summary")

#: Where the development goes instead, per field. The refusal has to name the
#: door this particular record already has, because "put it in a file" without
#: one is advice with a missing step.
_TERSE_FIX = {
    "answer": "cite the file in `--source`",
    "falsifier": "cite the file in `--source`",
    "summary": "cite the file in `--source`",
    "outcome": "name the file in the `--outcome`",
    "why": "name the file in the reason",
    "note": "name the file in the note",
}


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


# ---- the synopsis --------------------------------------------------------


def terse_limit(value: str | None = None) -> int | None:
    """`$DG_TERSE` as a character count, or `None` for no limit.

    Unreadable is `None` rather than an error, for the reason `write_policy`
    gives: this is consulted on the path of every stage, and a typo in a
    launcher's environment must not make the tool unusable for the supervisor
    sharing the tray. Failing open costs a rule that was never a security
    boundary; failing closed would cost the graph.
    """
    raw = (value if value is not None
           else os.environ.get(TERSE_ENV) or "").strip().lower()
    if not raw or raw in TERSE_OFF:
        return None
    if raw in ("on", "yes", "true"):
        return TERSE_DEFAULT
    return int(raw) if raw.isdigit() else None


def overlong(record, limit: int | None = None) -> list[tuple[str, int]]:
    """Which of `record`'s prose fields run past `limit`, and by how much.

    `record` is any mapping -- a staged op, or a vertex/edge/task rendered as
    one. The single definition of "too long", so that the stage-time refusal
    and the `dg check` warning cannot come to differ about which field is at
    fault, the way four independent renderings of an answer once did.

    Length is characters, and a multi-line field is judged by its total rather
    than by its line count. Two bounds would be the beginning of a parser, and
    the total is what a reader's eye actually measures in the panel: six short
    bullets and one long paragraph read the same length, and both are the thing
    being asked about.
    """
    cap = TERSE_DEFAULT if limit is None else limit
    out = []
    for name in TERSE_FIELDS:
        text = record.get(name)
        if isinstance(text, str) and len(text.strip()) > cap:
            out.append((name, len(text.strip())))
    return out


def refuse_verbose(op: dict, owner: str | None,
                   chosen: str | None = None) -> str | None:
    """Why this op is too long to stage -- or `None` if it may be.

    `owner` is `pending.owner()`: `None` is the supervisor and is never
    refused, exactly as in `refuse_write` and `cross.refuse_close`.

    Only the first over-long field is reported. An agent that wrote three long
    fields wrote one long record, and a refusal listing all three invites
    trimming them one at a time until the count passes, which is the opposite
    of what the rule is asking for.
    """
    if owner is None:
        return None
    cap = terse_limit(chosen)
    if cap is None:
        return None
    found = overlong(op, cap)
    if not found:
        return None
    name, size = found[0]
    subject = op.get("vertex") or op.get("task") or "this record"
    return (f"${TERSE_ENV}={cap}: the {name} for {subject} is {size} "
            f"characters. The store holds the synopsis a person reads while "
            f"deciding — one or two sentences. Write the development to a file "
            f"and {_TERSE_FIX.get(name, 'name the file here')}; the chain, the "
            f"premises and the work resting on this are edges, and "
            f"`dg context {subject}` already says them")
