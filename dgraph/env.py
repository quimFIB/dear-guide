"""The environment an agent runs under: one table, one parser per variable.

Everything a launcher can say about an agent it says in an environment
variable, and until this module existed each one was read where it was
enforced -- five modules, seven reads, no shared definition and no shared
parser. That was survivable while nothing reported them and fatal once
something had to: a report assembled from seven private `os.environ.get` calls
is an eighth reading of the same question, free to disagree with all seven.

## Why a typo is the thing this exists for

Three of these **fail open**, and the reasoning is sound: they are read on the
path of every judged write, and a typo in a launcher's environment must not
make the tool unusable for the supervisor sharing the tray. `$DG_DECIDE=nevr`
is therefore `open` -- the widest policy -- and looks identical to a policy
somebody chose.

That is only defensible if something says so somewhere, and for a long time
nothing did: the three docstrings each promised "the CLI reports the typo where
it is set instead", and no such report existed. `dg-agent env` is that report,
and `readings` below is what it is built from -- so the promise and the parser
are now the same code rather than a sentence about it.

**Failing open is a property of a variable, not a mood**, which is why it is a
field of `Var` rather than a convention each parser keeps. `$DG_BUDGET` is the
one that raises, and the asymmetry is argued rather than accidental: a misread
budget is not a wider rule, it is a different number, and both directions are
wrong in a way nobody notices until an agent is parked hours early or never.

## The seam

**`dg-agent` writes this environment; `dg` reads it.** Both do it through here.
`dg` must keep reading it, because that is where the rules are enforced -- on
the path of every stage, every close, every judged write and every op's
ownership stamp. What `dg-agent` owns is deciding what the variables *say*, and
saying what they currently do.

`limits` and `cross` import their parsers back from here under the names they
have always had, so every existing call site is unchanged and there is still
exactly one definition of what `evidence` means.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dgraph import project

# ---- the names -----------------------------------------------------------

#: Who is staging. Unset is the supervisor, which is not a lesser identity --
#: it is the one caller `dg apply` lets take a whole tray.
AGENT_ENV = "DG_AGENT"

#: How this tool names ops nobody signed. A writer cannot also be called that,
#: and `dg`'s root callback refuses a whole session over it -- so it is a bad
#: *value* here rather than a bad op.
UNOWNED = "unowned"

#: What an agent may close. See `cross.py` for what each policy means.
POLICY_ENV = "DG_DECIDE"
POLICIES = ("open", "evidence", "never")

#: Where an agent may write without asking. See `limits.py`.
WRITE_ENV = "DG_WRITE"
WRITE_POLICIES = ("open", "launch")

#: What an agent may run without asking. Space-separated **program names** --
#: names, not command lines and not subcommands. `dg-agent run` composes it at
#: launch from the plan, for the reason every other value here is composed
#: there: a list read when a command is judged would be live, and
#: `fanout/env.json` sits inside the project an agent may write to.
#:
#: Unset is the **empty** list, so every command escalates. That is the one
#: direction this may fail in: an agent that drops the variable makes its own
#: run stricter, never wider, which is the opposite of `$DG_DECIDE` and is why
#: `fails_open` is False for it.
EXEC_ENV = "DG_EXEC_ALLOW"

#: What makes a token something other than a program name. A token carrying one
#: of these was written as a *command line*, and judging command lines belongs
#: to the recogniser rather than to this list -- so it is dropped and reported
#: rather than matched against, which would let `git|sh` in as a name.
NOT_A_NAME = frozenset(";&|<>$`()[]{}*?!\\\"'\n\t")

#: Whether an agent may file under an area nobody has used yet. See
#: `pending.refuse_area`.
#:
#: `open` lets it, subject to the similarity guard, which is the rule that
#: replaced area *membership*; `strict` refuses a genuinely new area and sends
#: it back to a person as a proposal. Read exactly as `$DG_DECIDE` is: only for
#: a caller with `$DG_AGENT` set, so a supervisor is never refused.
AREA_ENV = "DG_AREA"
AREA_POLICIES = ("open", "strict")

#: How long before an agent's work is handed back. A span, or `infinite`.
BUDGET_ENV = "DG_BUDGET"

#: How long a record's prose field may be. See `limits.py`.
TERSE_ENV = "DG_TERSE"

#: When a quiet agent is reported. **Not a limit** -- nothing acts on it.
SILENT_ENV = "DG_SILENT_AFTER"
SILENT_DEFAULT = 900

#: Which graph all of the above apply to.
PROJECT_ENV = "DG_PROJECT"

#: What `infinite` may be spelled as. `0` is deliberately NOT among them: a
#: budget of zero seconds is a plausible typo for "no budget" and the two
#: readings are opposites, so it is refused rather than guessed at.
INFINITE = ("infinite", "inf", "none", "unlimited", "forever")

#: Suffixes a span may carry, in seconds. Bare digits are seconds.
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

#: What `$DG_TERSE=on` means, and what `dg check` warns above regardless of the
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


# ---- spans ---------------------------------------------------------------


class BadSpan(ValueError):
    """A budget that could not be read. Raised rather than defaulted.

    Unlike `$DG_WRITE`, a misread budget is not a wider rule -- it is a
    *different number*, and both directions are wrong in a way nobody would
    notice until an agent was parked hours early or never. The launcher is
    told at claim time, and `dg-agent run` refuses before it spawns anything,
    which are the two moments it can still be fixed.
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
    """A budget, for a person reading `dg-agent list`. Never lossy."""
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


# ---- the parsers ---------------------------------------------------------


def _one_of(value: str | None, env: str, allowed: tuple[str, ...]) -> str:
    """A policy word, defaulting to the widest. The fail-open shape."""
    val = (value if value is not None
           else os.environ.get(env) or "").strip().lower()
    return val if val in allowed else allowed[0]


def policy(value: str | None = None) -> str:
    """What `$DG_DECIDE` says, defaulting to today's behaviour.

    An unrecognised value is `open` rather than an error: this is read on the
    path of every `decide`, and a typo in a launcher's environment should not
    make the tool unusable for the supervisor too. `dg-agent env` reports the
    typo where it is set instead.
    """
    return _one_of(value, POLICY_ENV, POLICIES)


def write_policy(value: str | None = None) -> str:
    """What `$DG_WRITE` says, defaulting to today's behaviour.

    Fails open, for the reason `policy` gives, and reported by the same
    command.
    """
    return _one_of(value, WRITE_ENV, WRITE_POLICIES)


def area_policy(value: str | None = None) -> str:
    """What `$DG_AREA` says, defaulting to today's behaviour.

    Fails open, like its two neighbours and for the same reason: it is read on
    the path of every stage, and a typo in a launcher must not take the graph
    away from the supervisor sharing the tray.
    """
    return _one_of(value, AREA_ENV, AREA_POLICIES)


def terse_limit(value: str | None = None) -> int | None:
    """`$DG_TERSE` as a character count, or `None` for no limit.

    Unreadable is `None` rather than an error, for the reason `write_policy`
    gives: this is consulted on the path of every stage, and failing open costs
    a rule that was never a security boundary, where failing closed would cost
    the graph. `dg-agent env` reports it.
    """
    raw = (value if value is not None
           else os.environ.get(TERSE_ENV) or "").strip().lower()
    if not raw or raw in TERSE_OFF:
        return None
    if raw in ("on", "yes", "true"):
        return TERSE_DEFAULT
    return int(raw) if raw.isdigit() else None


def exec_allow(value: str | None = None) -> tuple[str, ...]:
    """`$DG_EXEC_ALLOW` as the program names an agent may run unasked.

    Order is kept and duplicates are dropped, because this is read by a person
    in `dg-agent env` as often as it is matched against.

    A token that is not a program name is **dropped**, not raised: this is
    consulted on the path of every command, and the drop narrows rather than
    widens, so there is nothing here that failing quietly could cost. What it
    would cost is silence, and `_read_exec_allow` is what reports it.
    """
    raw = (value if value is not None else os.environ.get(EXEC_ENV) or "")
    return tuple(dict.fromkeys(
        tok for tok in raw.split() if tok and not (NOT_A_NAME & set(tok))))


def _show_exec_allow(value) -> str:
    names = tuple(value or ())
    if not names:
        return "nothing — every command asks"
    shown = ", ".join(names[:4])
    return shown if len(names) <= 4 else f"{shown}, +{len(names) - 4} more"


def budget(value: str | None = None) -> int | None:
    """`$DG_BUDGET` as seconds, or `None` for no limit.

    A bad value here is *not* raised: by the time an agent is running, refusing
    every `dg` call over a malformed environment variable would take the graph
    away from the one caller still able to record what happened. `dg-agent
    claim` and `dg-agent run` validate it at the moment it is set, which is
    where the error belongs.
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


# ---- the table -----------------------------------------------------------


@dataclass(frozen=True)
class Var:
    """One environment variable, and everything anybody needs to know about it.

    `fails_open` is the field this table exists for. It is a property of the
    variable rather than a habit each parser keeps, and it is what tells a
    report whether a value it could not read was quietly widened or refused.
    """

    #: The variable's name, e.g. `DG_DECIDE`.
    name: str
    #: One line: what this decides. Shown by `dg-agent env`.
    what: str
    #: `raw -> effective`, where `raw` is the string in the environment.
    parse: Callable[[str | None], object]
    #: `effective -> str`, for a person reading a column.
    show: Callable[[object], str]
    #: What unset means, in the same vocabulary as `show`.
    unset: str
    #: True where an unreadable value is silently widened rather than refused.
    fails_open: bool
    #: The legal words, where there is a fixed set of them. Empty otherwise.
    choices: tuple[str, ...] = ()
    #: Which binary composes this. `dg` never sets any of them; a person or a
    #: launcher does, and after the split `dg-agent run` is the launcher.
    settable: bool = True


def _agent_show(value) -> str:
    return value or "—"


def _project_show(value) -> str:
    return str(value)


#: Every variable an agent's remit is expressed in, in the order a person reads
#: them: who you are, what you may decide, where you may write, how long you
#: have, how much you may say, when your silence is reported, and which graph
#: it all applies to.
#:
#: A second family -- `$DG_EDIT`, `$DG_EDITOR`, `$DG_EDIT_CMD`,
#: `$DG_GUI_EDITOR`, `$DG_HOOK_OFF` -- is deliberately absent. Those are the
#: person's own tooling rather than an agent's remit, they are read by `dg` and
#: composed by nobody, and putting them in this table would make `dg-agent env`
#: a report about the terminal instead of about the launch.
VARS: tuple[Var, ...] = (
    Var(AGENT_ENV, "who is staging; unset is the supervisor",
        lambda raw: (raw or "").strip() or None, _agent_show, "—",
        fails_open=False),
    Var(POLICY_ENV, "what an agent may close",
        policy, str, POLICIES[0], fails_open=True, choices=POLICIES),
    Var(WRITE_ENV, "where it may write without asking",
        write_policy, str, WRITE_POLICIES[0], fails_open=True,
        choices=WRITE_POLICIES),
    Var(EXEC_ENV, "what it may run without asking",
        exec_allow, _show_exec_allow, "nothing — every command asks",
        fails_open=False),
    Var(AREA_ENV, "whether it may file under a new area",
        area_policy, str, AREA_POLICIES[0], fails_open=True,
        choices=AREA_POLICIES),
    Var(BUDGET_ENV, "how long before work is handed back",
        budget, show_span, "infinite", fails_open=False),
    Var(TERSE_ENV, "how long a field may be",
        terse_limit, lambda n: "no limit" if n is None else f"{n} chars",
        "no limit", fails_open=True),
    Var(SILENT_ENV, "when a quiet agent is reported",
        silent_after, show_span, show_span(SILENT_DEFAULT), fails_open=True),
    Var(PROJECT_ENV, "which graph all of the above apply to",
        lambda raw: Path(raw).expanduser().resolve() if raw else None,
        _project_show, "nearest ancestor", fails_open=False, settable=False),
)

BY_NAME = {v.name: v for v in VARS}


@dataclass(frozen=True)
class Reading:
    """One variable as it actually stands: what was set, and what is in force.

    `ok` is False for **set and not understood**, and for nothing else. Unset
    is a legitimate choice and the documented default for every one of them, so
    a report that flagged an unset variable would flag every project that has
    never heard of this.
    """

    var: Var
    #: Exactly what is in the environment, or `None` if it is not set there.
    raw: str | None
    #: The parsed value, whatever type the variable's parser returns.
    value: object
    #: Whether the raw string was understood. Vacuously true when unset.
    ok: bool
    #: What is worth saying about this reading beyond the value. Empty when
    #: there is nothing to say.
    note: str = ""

    @property
    def set(self) -> bool:
        return self.raw is not None and self.raw.strip() != ""

    @property
    def effective(self) -> str:
        return self.var.show(self.value)

    @property
    def complaint(self) -> str:
        """The one line naming the typo and what it cost. Empty if there is
        none. Phrased at the launcher, because the launcher is the only place
        it can be fixed -- an agent reading this cannot change its own
        environment."""
        if self.ok:
            return ""
        return (f"${self.var.name}={self.raw} {self.note} "
                f"Set it where the agent is launched.")


def _read_exec_allow(raw: str | None) -> tuple[object, bool, str]:
    """The names, and a complaint naming any token that was not one.

    Not understood here means *some token was dropped*, never the whole value:
    a list with one bad entry still has the good ones, and refusing it wholesale
    would turn a typo into an empty allowlist that asks about everything.
    """
    names = exec_allow(raw)
    dropped = [tok for tok in (raw or "").split() if tok not in names]
    if dropped:
        return (names, False,
                f"— dropped {', '.join(repr(d) for d in dropped)}: this list "
                f"holds program names, not command lines, and a name carrying "
                f"shell syntax would never have matched one.")
    return names, True, ""


def _read_agent(raw: str | None) -> tuple[object, bool, str]:
    name = (raw or "").strip() or None
    if name == UNOWNED:
        return (None, False,
                f"is reserved — it is how this tool names ops nobody signed, "
                f"so a writer cannot also be called that, and every `dg` call "
                f"in this session is refused.")
    return name, True, ("agent — refusals apply" if name
                        else "supervisor — no refusal applies")


def _read_choice(var: Var, raw: str | None) -> tuple[object, bool, str]:
    value = var.parse(raw)
    if raw is not None and raw.strip() and raw.strip().lower() not in var.choices:
        return (value, False,
                f"is not one of {', '.join(var.choices)} — running as "
                f"`{value}`, the widest.")
    return value, True, ""


def _read_budget(raw: str | None) -> tuple[object, bool, str]:
    if raw is not None and raw.strip():
        try:
            return span(raw), True, ""
        except BadSpan as exc:
            # The one that does not widen. A misread budget is a different
            # number, so there is nothing to report as "fell back to" -- it is
            # simply unread, and the lease is what the agent is actually
            # running against.
            return None, False, f"could not be read — {exc}."
    return None, True, ""


def _read_terse(raw: str | None) -> tuple[object, bool, str]:
    value = terse_limit(raw)
    if raw is None or not raw.strip():
        return value, True, ""
    word = raw.strip().lower()
    if word in TERSE_OFF or word in ("on", "yes", "true") or word.isdigit():
        return value, True, ""
    return (value, False,
            "is not `on`, `off`, or a character count — running with no "
            "limit at all.")


def _read_silent(raw: str | None) -> tuple[object, bool, str]:
    if raw is None or not raw.strip():
        return SILENT_DEFAULT, True, ""
    try:
        return span(raw) or SILENT_DEFAULT, True, ""
    except BadSpan:
        return (SILENT_DEFAULT, False,
                f"is not a span — reporting silence after "
                f"{show_span(SILENT_DEFAULT)}, the default.")


def _read_project(raw: str | None) -> tuple[object, bool, str]:
    """`$DG_PROJECT` resolved to the graph it actually found.

    The failure this catches is a **stale env file**, which looks exactly like
    a fresh one: an archived `.dgraph-fanout-env.sh` whose `DG_PROJECT` still
    pointed at a project root the graph had since moved out of ran every agent
    against no store at all, and nothing said so.
    """
    found = project.find()
    if raw is None or not raw.strip():
        return found.root, True, "nearest ancestor"
    named = Path(raw).expanduser().resolve()
    if not (named / project.STORE_NAME).exists() and \
            not (named / project.TASKS_NAME).exists():
        return (named, False,
                f"holds neither {project.STORE_NAME} nor "
                f"{project.TASKS_NAME} — every agent launched under it works "
                f"against no graph.")
    return named, True, ""


#: How each variable is read *for a report*, where the parsers above are read
#: for a *decision*. The two differ in exactly one way and it is the whole
#: point of this module: a parser answers "what is in force", and these also
#: answer "was that chosen, or fallen into".
_READERS: dict[str, Callable[[str | None], tuple[object, bool, str]]] = {
    AGENT_ENV: _read_agent,
    EXEC_ENV: _read_exec_allow,
    POLICY_ENV: lambda raw: _read_choice(BY_NAME[POLICY_ENV], raw),
    WRITE_ENV: lambda raw: _read_choice(BY_NAME[WRITE_ENV], raw),
    AREA_ENV: lambda raw: _read_choice(BY_NAME[AREA_ENV], raw),
    BUDGET_ENV: _read_budget,
    TERSE_ENV: _read_terse,
    SILENT_ENV: _read_silent,
    PROJECT_ENV: _read_project,
}


def reading(name: str, environ: dict | None = None) -> Reading:
    """One variable, as it stands in `environ` (the process's own by default)."""
    var = BY_NAME[name]
    raw = (os.environ if environ is None else environ).get(name)
    value, ok, note = _READERS[name](raw)
    return Reading(var=var, raw=raw, value=value, ok=ok, note=note)


def readings(environ: dict | None = None) -> list[Reading]:
    """Every variable, in table order. What `dg-agent env` renders."""
    return [reading(v.name, environ) for v in VARS]


def faults(environ: dict | None = None) -> list[Reading]:
    """Only the readings that are set and not understood.

    What `dg-agent env --check` exits non-zero over, and the whole of it: an
    unset variable is a documented default, never a finding.
    """
    return [r for r in readings(environ) if not r.ok]
