"""Names for the writers sharing one tray.

`$DG_AGENT` is a string somebody has to invent, and every value it has ever gone
wrong on was invented by a launcher: a name that collided with `unowned`, a name
carrying the roster's own separator, a name that was two names. This module
hands one out instead, so the only names in circulation are ones the tool chose.

**It does not replace `$DG_AGENT`.** `dg` cannot set its caller's environment, so
the variable is still how a name reaches an agent — what changes is that nobody
has to make a value up:

    DG_AGENT=$(dg agent claim) claude -p "..."

That makes this a command for *launchers*, and the distinction matters for a
coding agent in particular: shell state does not survive between its tool calls,
so an agent that claimed a name for itself could not hold on to it. Whatever
spawns the agent claims the name and hands it down.

## Uniqueness is checked, never hoped for

The obvious scheme — draw two words at random — is the birthday problem, and
proof-duel's `tools/naming.py` has the measurement: at 34 x 34 it found "288
sweeps drew only 247 distinct names". That module could shrug, because a sweep
name is a handle over a timestamp that is already unique and two sweeps never
collide *as directories*. An agent name has nothing underneath it. Two agents
sharing one means their ops are indistinguishable in the tray, `--agent` writes
the wrong batch, and `dg apply --mine` from one applies the other's
half-composed drafts — `C-F16`, the failure the whole ownership stamp exists to
prevent, walked back in through the door meant to make it easier to use.

So allocation **reads what is taken and picks something else**. There is no
retry loop and no probability anywhere: the first free name in a fixed order
wins, which also makes it deterministic and therefore testable. The size of the
lists below stops being a correctness parameter and becomes a comfort one.

## A claim never expires

Taken means *leased, or present in either tray* — the trays as well, so a name
survives a lease file somebody deleted, and so a project that set `$DG_AGENT` by
hand before this existed cannot be handed a name it is already using.

Nothing here reaps. Every automatic rule wants a boundary like "the trays are
empty, so free everything", and every such boundary has the same window: an
agent claims a name, stages nothing yet, the trays go empty, the sweep drops its
lease, and the next claim hands the same name to somebody else. Closing that
window costs more machinery than the names are worth, so freeing one is an
explicit act — `dg agent release`, or `dg agent prune` for everything with no
ops left in either tray.

Running out is therefore possible, and is an **error**: `claim` refuses and says
what to empty. Handing back a name that is already in use, or falling back to a
numbered one, would be the silent conflation this module exists to prevent.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from dgraph import pending, project

#: Where the leases live. `.dgraph-*` is already in the `.gitignore` `dg init`
#: writes, so this file is covered the day it first appears — it is scratch, it
#: is per-checkout, and a lease that travelled between clones would be a claim
#: about a writer that is not running here.
AGENTS_NAME = ".dgraph-agents.json"

#: Ordinary adjectives, deliberately dull — proof-duel's word for its own list,
#: and the reasoning carries: "a name is a handle, not a joke that wears out on
#: the fortieth sweep". Here it wears out on the fortieth *review*.
ADJECTIVES = (
    "agile", "amber", "apt", "blithe", "bold", "brave", "bright", "brisk",
    "calm", "canny", "civil", "clean", "clear", "clever", "cool", "crisp",
    "deft", "dense", "direct", "eager", "even", "exact", "fair", "fine",
    "firm", "fleet", "fluent", "frank", "fresh", "glad", "grand", "humble",
    "keen", "lean", "level", "light", "lithe", "loyal", "lucid", "merry",
    "mild", "modest", "neat", "nimble", "noble", "novel", "open", "patient",
    "placid", "plain", "prime", "prompt", "proud", "pure", "quick", "quiet",
    "rapid", "rare", "ready", "rich", "ripe", "robust", "sage", "sane",
    "serene", "sharp", "silent", "simple", "sleek", "slim", "smart", "smooth",
    "snug", "sober", "soft", "solid", "sound", "spare", "stable", "stark",
    "steady", "stern", "still", "stout", "strict", "sturdy", "subtle", "sure",
    "swift", "tame", "tense", "terse", "tidy", "tough", "true", "vast",
    "vital", "vivid", "warm", "wary", "wise", "witty", "wry",
)

#: The other half: instruments and features of navigation and mapmaking. A
#: guide's vocabulary, for a tool called `dear-guide`, and **things rather than
#: people** — proof-duel names its sweeps after logicians because that is the
#: field it measures, whereas a writer here is a role for an afternoon and not
#: a tribute. Nothing to spell wrong, nobody to have opinions about.
MARKS = (
    "azimuth", "bearing", "beacon", "cairn", "chart", "compass", "contour",
    "course", "datum", "dial", "ephemeris", "equator", "fathom", "gnomon",
    "gradient", "harbour", "heading", "horizon", "isobar", "isotherm",
    "keel", "knot", "landfall", "lantern", "latitude", "ledger", "legend",
    "longitude", "lodestar", "log", "mainstay", "meridian", "milestone",
    "mooring", "needle", "octant", "parallel", "passage", "pennant", "pilot",
    "plumb", "pole", "quadrant", "reckoning", "relief", "rhumb", "ridge",
    "rudder", "sextant", "shoal", "signal", "sounding", "spar", "stadia",
    "station", "summit", "survey", "tack", "theodolite", "tide", "traverse",
    "trig", "vane", "vector", "waypoint", "wake", "watch", "waterline",
)


def sequence() -> list[str]:
    """Every name this tool can hand out, in the order it hands them out.

    The **mark** varies fastest, so the first names differ in the half that is
    easier to tell apart at a glance: `agile-azimuth`, `agile-bearing`,
    `agile-beacon`. Two adjectives are harder to distinguish in a roster than
    two nouns, and the roster is where these are read.

    Total, ordered and pure: `taken` decides which of these is free, and this
    decides nothing at all. That split is what makes allocation deterministic.
    """
    return [f"{adj}-{mark}" for adj in ADJECTIVES for mark in MARKS]


def path(root: Path | None = None) -> Path:
    return (root or project.find().root) / AGENTS_NAME


def load(root: Path | None = None) -> dict[str, dict]:
    """The leases. `{}` where none have been claimed.

    A file that will not parse raises, deliberately. Everywhere else in this
    tool an unreadable *tray* degrades to "no counts" because the reading it
    feeds is a courtesy; here it feeds an allocation, and a claim made against
    a lease file nobody could read is a claim that cannot promise the one thing
    a claim is for.
    """
    p = path(root)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save(leases: dict[str, dict], root: Path | None = None) -> None:
    p = path(root)
    if leases:
        project.write_atomic(
            p, json.dumps(leases, indent=2, ensure_ascii=False) + "\n")
    elif p.exists():
        p.unlink()


def in_trays(proj) -> dict[str, int]:
    """Every name with ops in a tray right now, and how many.

    Read without the tray locks, which is safe because `project.write_atomic`
    means there are no torn reads, and sufficient because a name that reaches a
    tray got there from a lease this function is only double-checking. The one
    case it is genuinely load-bearing is a lease file that was deleted while
    work was still staged under it.
    """
    out: dict[str, int] = {}
    for p in (proj.pending, proj.task_pending):
        try:
            ops = pending.load(p)
        except (OSError, ValueError):
            continue
        for op in ops:
            if op.get("by"):
                out[op["by"]] = out.get(op["by"], 0) + 1
    return out


class Exhausted(RuntimeError):
    """Every name is held. Raised rather than worked around.

    The alternatives are both silent conflation: hand back a name somebody is
    using, or invent one outside the lists — and a numbered fallback is the
    worse of the two, because it looks deliberate. `dg agent claim` turns this
    into a refusal that says which names are free-able and how.
    """

    def __init__(self, total: int, releasable: int):
        self.total, self.releasable = total, releasable
        super().__init__(f"all {total} names are held")


def claim(root: Path | None = None, *, today: str | None = None,
          budget: int | None = None, now: str | None = None) -> str:
    """Take the first free name, record the lease, and return it.

    `budget` is seconds the agent may run before its work is handed back, or
    `None` for no limit. It is recorded here rather than left in the launcher's
    environment so that a supervisor reading `dg agent list` sees the same
    number the agent was given, and so `dg agent expire` has something to
    measure against. See `limits`.

    Under the lease file's own lock: two launchers starting agents at the same
    moment is the case this exists for, and an unlocked read-modify-write here
    would hand them both the same name — the exact failure, arrived at by the
    machinery meant to prevent it.

    The lock is this file's alone and no tray lock is taken inside it, so it
    cannot participate in a lock-order cycle with `applying.trays`.
    """
    proj = project.find() if root is None else project.Project(root)
    with project.held(path(proj.root)):
        leases = load(proj.root)
        staged = in_trays(proj)
        taken = set(leases) | set(staged)
        for name in sequence():
            if name not in taken:
                rec = {"since": today or _today()}
                if budget is not None:
                    # `started` is a timestamp and `since` a date, and both are
                    # kept: the date is what a person reads, the timestamp is
                    # what expiry subtracts. Deriving one from the other would
                    # mean either a lease that cannot be read at a glance or a
                    # budget with a day's resolution.
                    rec["budget"] = int(budget)
                    rec["started"] = now or _now()
                leases[name] = rec
                save(leases, proj.root)
                return name
        raise Exhausted(len(sequence()),
                        len([n for n in leases if n not in staged]))


def holdings(root: Path | None = None) -> dict[str, str]:
    """`{task: agent}` for work an agent is holding RIGHT NOW.

    **This is why the lease file exists in the plural.** `DOING` says a task is
    claimed and not by whom, which a fan-out needs -- a stalled agent has to be
    distinguishable from a slow one, and `dg task park --why "the agent died"`
    has to have something to name.

    It is here and not on the `Task` because it is a fact about a RUN and not
    about the work. The task store is kept forever and is committed; agent names
    are recycled the moment they are released, so a name written into
    `tasks.json` today identifies nothing in six months, and "who finished this"
    is noise a reader has to scroll past. `Stop` and `Completion` carried `by`
    for exactly one commit before that argument won.

    So it lives in scratch, beside the names themselves, under the `.dgraph-*`
    the .gitignore already covers -- true while the run is on, gone with it.
    """
    return {t: name for name, rec in load(root).items()
            for t in (rec.get("holding") or ())}


def hold(name: str, tid: str, root: Path | None = None) -> None:
    """Record that `name` has started `tid`. A person holds nothing."""
    if not name:
        return
    with project.held(path(root)):
        leases = load(root)
        # A name with no lease can still hold work: `$DG_AGENT` is
        # self-declared, so an agent that never called `claim` is an ordinary
        # caller with a name. Record it rather than dropping the fact.
        rec = leases.setdefault(name, {"since": _today()})
        held = [t for t in (rec.get("holding") or ()) if t != tid]
        rec["holding"] = held + [tid]
        save(leases, root)


def drop_hold(tid: str, root: Path | None = None) -> None:
    """Forget who had `tid`, whoever it was. Called when work leaves DOING."""
    with project.held(path(root)):
        leases = load(root)
        changed = False
        for rec in leases.values():
            held = rec.get("holding") or []
            if tid in held:
                rec["holding"] = [t for t in held if t != tid]
                if not rec["holding"]:
                    del rec["holding"]
                changed = True
        if changed:
            save(leases, root)


def release(name: str, root: Path | None = None) -> bool:
    """Drop one lease. `False` if nothing was holding it.

    Says nothing about the tray. Releasing a name whose ops are still staged is
    legitimate — the launcher is done, the review is not — and the ops keep the
    name they carry, so nothing is orphaned. It only means the name can be
    handed out again, which `claim` will not do while those ops are there.
    """
    proj = project.find() if root is None else project.Project(root)
    with project.held(path(proj.root)):
        leases = load(proj.root)
        if name not in leases:
            return False
        del leases[name]
        save(leases, proj.root)
        return True


def prune(root: Path | None = None, keep: Iterable[str] = ()) -> list[str]:
    """Drop every lease with no ops left in either tray, and name what went.

    The deliberate act that automatic reaping was not allowed to be. Safe by
    construction rather than by timing: a name with nothing staged under it can
    still be *in use* by an agent that has not staged yet, so this is offered to
    a person who knows the round is over, and never run on their behalf.

    `keep` is names to leave alone whatever else is true of them. It exists
    because "nothing staged" is not the same as "finished": an agent that
    claimed a task and has not staged yet is idle by this measure and very much
    in use, and dropping its lease STRANDS the task -- still `DOING`, its holder
    no longer recorded anywhere, and `dg task start` refusing it as taken. Only
    a hand-written park recovers it, by somebody who noticed.

    The set is the caller's to compute, not this module's, and deliberately so:
    knowing whether a held task is still `DOING` means reading the task store,
    and `agents.py` does not know what a task is. `cli.agent_prune` has the
    graph loaded already and passes the answer in. Defaulting to empty keeps
    every existing caller on the old behaviour.
    """
    keep = set(keep)
    proj = project.find() if root is None else project.Project(root)
    with project.held(path(proj.root)):
        leases = load(proj.root)
        staged = in_trays(proj)
        gone = [n for n in leases if n not in staged and n not in keep]
        for n in gone:
            del leases[n]
        save(leases, proj.root)
        return gone


def _today() -> str:
    """The local date, matching how `dg confirm --against` files one."""
    from datetime import date
    return date.today().isoformat()


def _now() -> str:
    """A timestamp for a budget to be measured from. Naive local, like `_today`.

    The lease is scratch, per-checkout and gone with the run, so it never
    crosses a machine and has nothing to reconcile against another clock.
    """
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def remaining(rec: dict, now: str | None = None) -> int | None:
    """Seconds left on this lease's budget. Negative is over; `None` no limit.

    A lease with a budget but no `started` is treated as no limit rather than
    as instantly spent. That combination means a file written by an older
    version or edited by hand, and reading it as "expired" would park an
    agent's work on the strength of a field that was never set.
    """
    if rec.get("budget") is None or not rec.get("started"):
        return None
    from datetime import datetime
    try:
        began = datetime.fromisoformat(rec["started"])
    except (TypeError, ValueError):
        return None
    at = datetime.fromisoformat(now) if now else datetime.now()
    return int(rec["budget"]) - int((at - began).total_seconds())


#: How stale a `last_seen` must be before `touch` writes a new one. Every `dg`
#: call goes through `touch`, so an unconditional write would be a locked
#: read-modify-write per invocation to record something nothing reads at that
#: resolution. Coarser than this and the silence column starts rounding up.
TOUCH_EVERY = 30


def touch(name: str | None, root: Path | None = None, *,
          now: str | None = None, every: int = TOUCH_EVERY) -> None:
    """Record that `name` was alive just now. A person is not tracked.

    Called from the CLI's root callback, so **every** `dg` invocation by an
    agent is a heartbeat -- and, because both host adapters run `dg gate` on
    the agent's own tool calls, so is every file write it makes. That second
    path is what makes the signal worth having: an agent calls `dg` a handful
    of times an hour and writes files constantly.

    Never raises. A heartbeat that could break a command would be a liveness
    signal that costs liveness, and this runs before every single one.

    Only stamps a lease that already exists. A hand-set `$DG_AGENT` that never
    claimed gets no lease here -- `hold` is what creates one for it, and until
    then there is nothing to be silent about.
    """
    if not name:
        return
    try:
        leases = load(root)
        rec = leases.get(name)
        if rec is None:
            return
        stamp = now or _now()
        # Read unlocked and return early: the common case is a fresh stamp and
        # no write at all, and taking the lease lock to discover that would put
        # a lock acquisition on the front of every `dg` command.
        if quiet_for(rec, stamp) is not None and quiet_for(rec, stamp) < every:
            return
        with project.held(path(root)):
            leases = load(root)
            if name not in leases:
                return
            leases[name]["last_seen"] = stamp
            save(leases, root)
    except Exception:
        return


def quiet_for(rec: dict, now: str | None = None) -> int | None:
    """Seconds since this lease was last seen alive; `None` if never stamped."""
    if not rec.get("last_seen"):
        return None
    from datetime import datetime
    try:
        seen = datetime.fromisoformat(rec["last_seen"])
    except (TypeError, ValueError):
        return None
    at = datetime.fromisoformat(now) if now else datetime.now()
    return max(0, int((at - seen).total_seconds()))


def silent(root: Path | None = None, now: str | None = None,
           after: int | None = None) -> list[dict]:
    """Agents holding work that have not been seen for a while.

    **Only those holding work**, which is the whole of what keeps this quiet.
    An agent that has gone silent holding nothing has cost nobody anything, and
    reporting it would put a column of noise in front of every supervisor for
    the sake of a fact with no consequence.

    Nothing acts on this. It is read by `dg agent list` and by a person, who
    can tell a forty-minute build from a corpse in ways this cannot -- see
    `limits.SILENT_ENV`.
    """
    from dgraph import limits
    after = limits.silent_after() if after is None else after
    out = []
    for name, rec in sorted(load(root).items()):
        held = list(rec.get("holding") or ())
        quiet = quiet_for(rec, now)
        if held and quiet is not None and quiet >= after:
            out.append({"agent": name, "quiet": quiet, "holding": held})
    return out


def over_budget(root: Path | None = None, now: str | None = None) -> list[dict]:
    """Agents past their budget, with what each is still holding.

    Holding nothing is included on purpose. An agent that spent its budget
    without ever claiming a task has nothing to park, but a supervisor still
    wants to know it is out of time -- the difference between "finished early"
    and "died before it started" is exactly what a fan-out cannot otherwise
    see.
    """
    out = []
    for name, rec in sorted(load(root).items()):
        left = remaining(rec, now)
        if left is not None and left < 0:
            out.append({"agent": name, "over": -left,
                        "budget": int(rec["budget"]),
                        "holding": list(rec.get("holding") or ())})
    return out
