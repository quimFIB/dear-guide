"""Setting up a fan-out: the answers, and what they turn into.

`agentic/README.md` is the procedure a person follows by hand. This is the same
procedure as data — the questions it asks, the ones it can answer from the
graph, and the two artefacts it writes:

    <out>/scout.md     the filled prompt, with no ⟨…⟩ left in it
    <out>/launch.sh    one line per agent, with the environment already right
    <out>/env.json     the environment itself, readable back

The third one is what makes the first two safe. They are generated from one
`Plan` and then diverge the moment somebody edits either — which the README
expects people to do — and a `scout.md` asserting `$DG_DECIDE=evidence` to an
agent running under `open` is worse than one that said nothing, because the
agent has no way to check. Writing the plan down means `dg-agent env --check
--plan` can assert they still agree, and `dg-agent run --plan` can compose the
environment from the same file the prompt was rendered from.

**Nothing here is interactive, and that is the point.** A wizard has to work in
three places: a person at a terminal, an agent inside Claude Code, and an agent
inside opencode. Only the first can drive a full-screen TUI. So the collecting
and the doing are split — `wizard.py` collects answers into a `Plan` and does
nothing else, the CLI's flags build the same `Plan`, and everything below runs
identically either way. The TUI is an adapter, exactly as the host hooks are.

## Most of the blanks answer themselves

`agentic/prompts/scout.md` has around twenty ⟨…⟩. Only three of them need a
person: what the fan-out is *for*, what the agents may read, and where findings
go. The rest are already in the graph or in the plan — the project's name, the
chain behind the work (`dg context <id> --full`, pasted verbatim), the areas,
the next free ids, which `$DG_DECIDE` is in force and what it means, the write
scope and its roots, the budget, and how long a field may be.

That ratio is the argument for the wizard existing. A template with twenty
blanks gets filled badly or not at all; one with three gets filled.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field, replace
from pathlib import Path

import json

from dgraph import agents, areas as _env_areas, env as _env, limits, project

#: Where `dg-agent setup` writes, relative to the project root. Not
#: `.dgraph-*`: those are scratch the `.gitignore` hides, and a filled prompt is
#: the opposite — the thing you want to read back, diff, and reuse next time.
OUT_DIR = "fanout"

#: The environment a run is launched under, written beside the prompt rather
#: than under `.dgraph-*` for the reason `OUT_DIR` gives about the filled
#: prompt: those are scratch the `.gitignore` hides, and this is the opposite —
#: the thing you want to read back, diff, and reuse next time. The archived
#: `.dgraph-fanout-env.sh` of an earlier run is what its absence produced.
ENV_NAME = "env.json"

#: Which `Plan` fields are the *environment*, and the variable each becomes.
#: Everything else in a plan — the focus ids, the host, how many agents, what
#: they may read — shapes the prompt or the launcher rather than the remit, and
#: a plan file that carried them would be a second copy of `scout.md`.
ENV_FIELDS = {"decide": "DG_DECIDE", "apply": "DG_APPLY", "write": "DG_WRITE",
              "area": "DG_AREA", "terse": "DG_TERSE",
              "budget": "DG_BUDGET", "exec_allow": "DG_EXEC_ALLOW",
              "confine": "DG_CONFINE", "floor": "DG_FLOOR"}


@dataclass(frozen=True)
class Host:
    """One runner a launch line can be generated for.

    `carries` is why this is a record rather than a string. A confinement
    backend expressed as the runner's own settings can only be applied by a
    spawn line speaking that runner's vocabulary — so *which* runners can apply
    it is a fact about the runner, and leaving it as an `== "claude"` in
    `render_launch` is what produced `P-F2`: an opencode plan declaring a floor
    that no line it generates could ever carry, with nothing to say so.

    A backend that wraps the command is absent from every entry on purpose.
    `dg-agent run` prepends those, host-neutrally, so no runner has to claim
    one and a runner that did would be claiming credit for a floor it is not
    applying.
    """

    #: The spawn, with `{prompt}` to fill.
    spawn: str
    #: Confinement backends this runner can carry itself.
    carries: tuple[str, ...] = ()


#: The hosts a launch line can be generated for. `mixed` is not among them
#: because a fan-out across two hosts is two launch files, and pretending
#: otherwise would produce a script that runs neither.
HOSTS = {
    "claude": Host('claude -p "$(cat {prompt})"', carries=("host",)),
    "opencode": Host('opencode run "$(cat {prompt})"'),
}

#: Where the agents live. `D52`.
#:
#: **Named for the mechanism, not for the weakness**, and `D34`'s falsifier is
#: why: *a host gains per-child environment for spawned subagents, or wraps its
#: own subagent spawn* — either makes this mode enforceable, at which point
#: `session` is still exactly the right word and simply gains guarantees.
#: `advisory` would have to be renamed, and a renamed mode breaks every script
#: and document that named it.
#:
#: **Not a `Host`, and that is the other half.** A `Host` is *one runner a
#: launch line can be generated for*; this mode has no launch line at all, since
#: the session calls its own subagent tool. A third `HOSTS` entry would be
#: `P-F2` again: a record claiming to carry something no line it generates
#: could.
MODES = ("process", "session")

#: What `--mode session` gives up, and on which host. `D52`'s declared list, in
#: one place so the prompt, the setup report and `agentic/RUNNING.md` cannot
#: come to disagree about it.
#:
#: The first four are permanent consequences of in-process spawning and hold
#: everywhere. The last is an opencode bug with an issue number, which is why it
#: is marked rather than merged into the others: it may simply go away, and a
#: reader deciding between hosts needs to know which losses are architecture and
#: which are a defect.
SESSION_LOSSES = (
    ("$DG_AGENT", None,
     "a subagent shares the session's environment, so without a "
     "`DG_AGENT=<name>` prefix on every call it reads as the supervisor (D32)"),
    ("the confinement floor", None,
     "no `dg-agent run` parent to prepend it (D33)"),
    ("the budget", None,
     "the same; `$DG_BUDGET` is advisory and `dg-agent expire` the only "
     "backstop (D33)"),
    ("$DG_TASK", None,
     "not lost but relocated: the session passed --roster, so it hands each "
     "agent its task in the spawn instructions instead (D61)"),
    ("a relayed verdict's claim to `person`", None,
     "the channel cannot prove a person wrote it with no floor (D40)"),
    ("`dg gate --write` and the commit gate", "opencode",
     "`tool.execute.before` does not fire for `task`-tool subagents "
     "(opencode#5894) — absent here, not weakened, and `commit` and `rm` are "
     "the two irreversible things"),
)


def capture_bin() -> Path | None:
    """The capture wrapper's directory, or `None` if this install has none.

    It lives in the dear-guide checkout rather than in the project being fanned
    out, so the generated launcher has to name an absolute path — `$PWD` is the
    project, and pointing there produced a line that silently found no wrapper
    and recorded nothing. A wheel install has no `agentic/` at all, which is why
    this can answer `None` and the caller leaves the capture out rather than
    emitting a path that does not exist.
    """
    here = Path(__file__).resolve().parent.parent / "agentic" / "bin"
    return here if (here / "dg").exists() else None


def templates() -> Path:
    """Where the prompt templates live.

    Package data, so an installed `dg` has them. `agentic/prompts/` in the repo
    are symlinks to these, which is why every path in the documentation still
    resolves and why there is exactly one copy to keep true.
    """
    return Path(__file__).resolve().parent / "prompts"


@dataclass
class Check:
    """One prerequisite, and how to satisfy it if it is not met.

    `bars` is the difference between *this cannot run* and *this can run and
    you should know*. It was a **comment** until `G-F7`: the check below the
    ready-tasks one said "a warning rather than a bar" and `--json` computed
    `ready` from `all(c.ok …)`, so the warning barred and the sentence saying
    otherwise was read by nobody. A distinction with two severities and one
    field is a distinction that is not made.
    """
    ok: bool
    label: str
    fix: str = ""
    #: `False` for a check that reports rather than refuses. Default `True`,
    #: which is the safe way round: a new prerequisite bars until somebody
    #: argues it should not.
    bars: bool = True


@dataclass
class Plan:
    """Every answer the wizard needs. Built by the TUI or by CLI flags."""

    #: Decision or task ids the fan-out is aimed at. Their chains are pasted
    #: into the prompt, which is the single most valuable thing in it: a fresh
    #: context knows the task and nothing about why it exists, and without the
    #: chain it cannot tell a constraint from an implementation detail.
    focus: list[str] = field(default_factory=list)
    #: Task ids to launch one agent each for, or empty for the loop this tool
    #: has always run: N interchangeable agents that read the frontier and take
    #: what they find.
    #:
    #: **Empty is not a lesser plan, and stays the default.** Self-selection is
    #: what makes a fan-out absorb a queue that moves while it runs -- an agent
    #: that finishes early takes the next thing rather than idling, and work
    #: made ready by another agent's finish gets picked up without anybody
    #: rewriting the launcher. A roster gives that up on purpose, and is worth
    #: it only when somebody has a reason to say *this* agent does *this*.
    #:
    #: `agents` is derived from its length rather than kept beside it, for the
    #: reason `dg-agent run` gives about `--budget` and `timeout`: two numbers
    #: in one generated file agree until somebody edits one.
    roster: list[str] = field(default_factory=list)
    #: `process` — one `dg-agent run` per agent, every rule enforced — or
    #: `session`, where the launching session spawns them itself and the rules
    #: in `SESSION_LOSSES` become advisory. `D34` split those: a session may
    #: supervise a fan-out without spawning it, and that gives up nothing.
    mode: str = "process"
    agents: int = 2
    host: str = "claude"
    decide: str = "evidence"
    #: `$DG_APPLY`. `own` by default here as in the tool, and unlike `decide`
    #: and `write` — which are tighter in a fan-out than the tool's own
    #: defaults — because an agent that cannot apply its own proposals cannot
    #: read them back as a graph either, and reviewing a frontier somebody
    #: chose is what a fan-out is *for*. It becomes `never` where nobody chose
    #: the frontier: see `PRESETS`.
    apply: str = "own"
    write: str = "launch"
    budget: int | None = 1800
    #: `$DG_TERSE`. On by default here while the tool's own default is off, for
    #: the same reason `decide` and `write` are tighter than theirs: a fan-out
    #: is where the failure this guards against actually happens. A dozen
    #: agents each writing their reasoning into an answer is what makes a graph
    #: unreadable in the browser, and the person deciding is the one who pays.
    terse: str = "on"
    #: `$DG_AREA`. `open` by default even here, where the other three are
    #: tighter than the tool's own defaults, because an area is the one thing a
    #: scout is *expected* to discover: a fan-out over an unexplored corner of a
    #: project finds corners nobody had named, and `strict` sends every one of
    #: them back to a person before the work that found it can be filed. The
    #: similarity guard is what makes `open` safe, and it is on either way.
    area: str = "open"
    #: `$DG_EXEC_ALLOW`. Program names, never command lines -- see `env`. Empty
    #: by default, which means every command escalates, because the alternative
    #: default is a guess about somebody else's project. `dg-agent setup`
    #: derives a proposal from the filesystem rather than leaving it blank.
    exec_allow: list[str] = field(default_factory=list)
    #: `$DG_CONFINE` and `$DG_FLOOR`. `require` here while the tool's own
    #: default is `off`, for the same reason `decide`, `write` and `terse` are
    #: tighter than theirs: a fan-out is where the failure these guard against
    #: actually happens. `dg-agent setup` drops it back to `off` and says so
    #: where no backend is usable, rather than writing a plan that cannot run.
    confine: str = "require"
    floor: str = "host"
    #: The three answers no graph can supply.
    brief: str = ""
    reads: list[tuple[str, str]] = field(default_factory=list)
    findings: str = "findings/<task-id>-<slug>.md"
    capture: bool = False
    out: str = OUT_DIR

    def resolved_out(self, proj: project.Project) -> Path:
        return proj.root / self.out


#: What the last `dg-agent setup` wrote, by relative path and digest.
#:
#: **The artefacts are meant to be edited, and were overwritten without a
#: word.** `agentic/README.md` expects a launcher to edit `scout.md`, and
#: `MARKERS` says in as many words that the proposed allowlist goes into
#: `env.json` "where it can be read back, **cut down**, and diffed next time".
#: Re-running `setup` — which is the right move when the graph has moved on,
#: and which this tool recommends — silently restored both. A launcher who had
#: removed `cargo` from the allowlist got it back and was told nothing. Audit
#: `G-F9`.
#:
#: A digest rather than a copy, because the question is only *was this
#: changed*, and a copy would be a second artefact to keep true.
#:
#: **The lock question, answered rather than left** (shapes 17, 18): nothing
#: holds this and nothing needs to. It is written by `dg-agent setup`, which a
#: person runs at a terminal one at a time, and read by the next run of the
#: same command. Two concurrent setups in one checkout would already be racing
#: on the three artefacts themselves, which no lock here would fix. It is in
#: `test_write_lock_matrix.UNGUARDED` with that reason beside the others.
#:
#: `.dgraph-*`, so the `.gitignore` `dg init` writes covers it the day it
#: first appears — it is scratch about a generated directory, and a digest
#: that travelled between clones would describe files this checkout never
#: wrote.
DIGEST_NAME = ".dgraph-fanout.json"


def _digest(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_written(proj: project.Project, written: list[Path]) -> None:
    """Remember what `setup` just generated, so the next run can tell an edit
    from its own output. Best-effort: a setup must not fail because scratch
    could not be written, and the cost of losing this is a warning that does
    not fire."""
    out = {}
    for p in written:
        try:
            out[str(p.relative_to(proj.root))] = _digest(
                p.read_text(encoding="utf-8"))
        except OSError:
            continue
    with __import__("contextlib").suppress(OSError):
        project.write_atomic(proj.root / DIGEST_NAME,
                             json.dumps(out, indent=2, ensure_ascii=False) + "\n")


def edited(proj: project.Project, plan: Plan) -> list[str]:
    """Which of this plan's artefacts a person has changed since they were
    generated — the files a re-run would destroy.

    Empty where the directory does not exist yet, where every file is exactly
    as `setup` left it, and where there is no record to compare against. That
    last case is the one worth stating: **a missing digest reads as "not
    edited"**, so a `fanout/` written before this existed is overwritten as it
    always was rather than refusing on a suspicion. Failing closed here would
    make every launcher's first run after upgrading a refusal they could not
    explain.
    """
    try:
        known = json.loads((proj.root / DIGEST_NAME)
                           .read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return []
    out = []
    for name in ("scout.md", "launch.sh", ENV_NAME):
        rel = f"{plan.out}/{name}"
        was = known.get(rel)
        if not was:
            continue
        try:
            now = _digest((proj.root / rel).read_text(encoding="utf-8"))
        except OSError:
            continue
        if now != was:
            out.append(rel)
    return out


def env_plan(plan: Plan) -> dict:
    """The `Plan`'s environment, as the object `env.json` holds.

    Only the remit. A plan file that also carried the focus ids, the host and
    the brief would be a second copy of `scout.md`, free to disagree with it —
    which is the failure this file is being written to close, arrived at from
    the other direction.

    The budget is seconds, not `30m`: it is the one field that is a *number*,
    and writing it as a span would mean the file round-tripped through a parser
    that can refuse. `plan_env` renders it back for the environment.

    **Derived from `ENV_FIELDS`, never listed.** The list version held eight of
    the nine and the missing one was `apply` — so `--preset scout` wrote a
    prompt telling the agent *`dg apply` is refused for you* and an `env.json`
    that set nothing, and the preset whose row reads "proposes only · nothing
    lands unapproved" landed its proposals unapproved. Every other mechanism
    around this file already reads the table: `plan_env` renders from it,
    `read_env_plan` accepts its keys, and the prompt guard maps it to what must
    be visible in `scout.md`. This was the one that spelled the fields out, and
    it is the one that fell behind. Audit `G-F1`.
    """
    return {k: (list(v) if isinstance(v, list) else v)
            for k, v in ((k, getattr(plan, k)) for k in ENV_FIELDS)}


def read_env_plan(path) -> dict:
    """`env.json`, shape-checked. Raises `ValueError` naming what is wrong.

    Checked rather than trusted, because this file's whole purpose is to be
    read back by something that then spawns agents under it: a plan with
    `"decide": "nevr"` in it would compose the widest policy for every child of
    the run, which is precisely the failure the file exists to catch. So a
    value that is not one of the choices is refused **here**, at the launcher,
    where it can still be fixed.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected an object, got {type(raw).__name__}")
    unknown = sorted(set(raw) - set(ENV_FIELDS))
    if unknown:
        raise ValueError(f"{path}: unknown key(s) {', '.join(unknown)} — this "
                         f"file holds the environment and nothing else "
                         f"({', '.join(ENV_FIELDS)})")
    from dgraph import confine as _confine
    for key, allowed in (("decide", _env.POLICIES), ("write", _env.WRITE_POLICIES),
                         ("apply", _env.APPLY_POLICIES),
                         ("area", _env.AREA_POLICIES),
                         ("confine", _confine.CONFINE_MODES),
                         ("floor", _confine.BACKENDS)):
        if key in raw and raw[key] not in allowed:
            raise ValueError(f"{path}: {key} is {raw[key]!r}, not one of "
                             f"{', '.join(allowed)}")
    if "terse" in raw:
        word = str(raw["terse"]).strip().lower()
        if word not in _env.TERSE_OFF and _env.terse_limit(word) is None:
            raise ValueError(f"{path}: terse is {raw['terse']!r}, not `on`, "
                             f"`off`, or a character count")
    if "exec_allow" in raw:
        # A list of names, checked here for the reason `decide` is: this file
        # is read back by the thing that spawns agents, and a name carrying
        # shell syntax would be dropped silently at parse time -- narrowing the
        # run without saying so. Refused at the launcher, where it is fixable.
        names = raw["exec_allow"]
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ValueError(f"{path}: exec_allow is {names!r} — a list of "
                             f"program names")
        # `n.split() != [n]` rather than a character test: one entry is one
        # name, and an entry with a space in it is a *subcommand*. Left in, it
        # would not be refused later either -- `env.exec_allow` splits on
        # whitespace, so `"git commit"` would quietly become two allowed
        # programs, `git` and `commit`, the second of which nobody listed.
        bad = [n for n in names
               if n.split() != [n] or (_env.NOT_A_NAME & set(n))]
        if bad:
            raise ValueError(
                f"{path}: exec_allow holds {', '.join(repr(b) for b in bad)} — "
                f"program names, not command lines — one name per entry. A "
                f"subcommand would silently become two allowed programs, and a "
                f"pipe is a rule nothing would ever match")
    if raw.get("budget") is not None:
        # The type *and* the range, because every other field here is
        # range-checked and this one was not: `isinstance(int)` let `0` and
        # every negative through, `plan_env` rendered `0` as `"0s"`, and
        # `dg-agent run` then raised `BadSpan` from the one line outside its
        # own try/except — a traceback where the surrounding code promises
        # "nothing was spawned and no name was claimed".
        #
        # `env.span` is the judge rather than a second `<= 0` written here, so
        # the file reader and `--budget` refuse the same values for the same
        # reason. Zero is refused rather than read as `infinite` for the reason
        # `env.INFINITE` gives: they are opposites, so it is not guessed at.
        if not isinstance(raw["budget"], int) or isinstance(raw["budget"], bool):
            raise ValueError(f"{path}: budget is {raw['budget']!r} — seconds, "
                             f"or null for no limit")
        try:
            _env.span(str(raw["budget"]))
        except _env.BadSpan as exc:
            raise ValueError(f"{path}: {exc}") from None
    return raw


def plan_env(spec: dict) -> dict[str, str]:
    """`env.json` as the assignments a child runs under.

    `null` budget is *absent* rather than `infinite`: unset is what the tool
    documents as no limit, and writing the word would make an unset variable
    and a deliberate `infinite` render differently in `dg-agent env` while
    meaning the same thing.
    """
    out = {}
    for key, name in ENV_FIELDS.items():
        if key not in spec:
            continue
        value = spec[key]
        if key == "budget":
            if value is None:
                continue
            value = _env.show_span(value)
        elif key == "exec_allow":
            # Absent rather than empty when there is nothing in it: unset and
            # `""` mean the same thing to `env.exec_allow`, and an empty
            # assignment in the child would make `dg-agent env` report a
            # variable somebody had chosen when nobody had.
            if not value:
                continue
            value = " ".join(value)
        out[name] = str(value)
    return out


def roster_fault(plan: Plan, proj: project.Project | None = None) -> str | None:
    """Why this roster names work no agent could start — or `None`.

    Separate from `plan_fault` because it needs the store and that one does
    not: `plan_fault` is about a *pair of settings* that contradict each other,
    answerable from the plan alone and therefore answerable anywhere. This is
    about the plan against the graph, so it is checked where the graph is open.

    Checked at setup rather than at launch, which is the same argument
    `read_env_plan` makes about a mistyped policy: the roster ends up in a
    generated `launch.sh`, and an id that resolves to nothing there costs a
    spawned agent, a claimed name and a run that discovers it has no work.
    Here, the launcher is standing at the terminal and the file is not written
    yet.

    **`DONE` is refused and `DOING` is not.** A finished task is a launcher
    reading a stale frontier, and there is no reading under which they meant
    it. `DOING` is a task some other agent may be holding right now — which is
    worth saying and not worth refusing, because a supervisor relaunching after
    a crash is the ordinary way that state arises.
    """
    if not plan.roster:
        return None
    proj = proj or project.find()
    if not proj.has_tasks:
        return (f"--roster names {', '.join(plan.roster)}, but this project "
                f"has no task store — a roster launches one agent per task")
    from dgraph.tasks import TaskGraph
    tg = TaskGraph.load(proj.tasks)
    missing = [t for t in plan.roster if t not in tg.tasks]
    if missing:
        return (f"--roster names {', '.join(missing)}, which "
                f"{'is' if len(missing) == 1 else 'are'} not in the task store")
    done = [t for t in plan.roster if tg.tasks[t].resolved]
    if done:
        return (f"--roster names {', '.join(f'{t} ({tg.tasks[t].status})' for t in done)} "
                f"— an agent launched for finished work has nothing to start")
    return None


def roster_warnings(plan: Plan, proj: project.Project | None = None) -> list[str]:
    """What is worth saying about a roster without refusing it.

    Said rather than refused, because each of these describes a launch somebody
    can legitimately mean. A blocked task is the case to read twice: `dg task
    start` does not refuse one, so the agent will begin work whose premise is
    not settled — which is sometimes exactly the point of a fan-out and is
    never something to discover afterwards.
    """
    if not plan.roster:
        return []
    proj = proj or project.find()
    if not proj.has_tasks:
        return []
    from dgraph.tasks import TaskGraph
    from dgraph.model import Graph
    from dgraph import cross
    tg = TaskGraph.load(proj.tasks)
    g = Graph.load(proj.store) if proj.has_decisions else None
    out = []
    for tid in plan.roster:
        if tid not in tg.tasks:
            continue
        t = tg.tasks[tid]
        if t.status == "DOING":
            who = agents.holdings(proj.root).get(tid)
            out.append(f"{tid} is already DOING"
                       + (f", held by {who}" if who else ""))
        if g is not None:
            gate = cross.gated_by(tg, g, tid)
            if gate is not None:
                out.append(f"{tid} rests on {gate}, which is not settled")
        waiting = tg.waiting_on(tid)
        if waiting:
            out.append(f"{tid} waits on {', '.join(waiting)}")
    return out


def plan_fault(plan: Plan) -> str | None:
    """Why this plan could never be launched as it stands — or `None`.

    Two combinations, both of them pairs no invocation can fix: a floor
    expressed as the runner's own settings under a runner that does not read
    them, and a write scope of `open` under a floor that seals it anyway. There
    is nothing to say at run time about either, because the pair is wrong rather
    than the command — so both are refused where they are *written*, which is
    the same place `--decide evidenced` is refused and for the same reason.

    Derived from `Host.carries` and `confine.configures_runner`, never from a
    host's name. The `== "claude"` this replaces is what let the pair be written
    in the first place.
    """
    from dgraph import confine as _confine
    # **The refusal `D52` names, and the only one the mode carries.** Everything
    # else `--mode session` gives up is *stated* — `SESSION_LOSSES`, said at
    # setup and again in the prompt — because `D34` decided the mode is declared
    # rather than enforced. This one cannot be a notice: `D33` established that
    # a session-spawned agent has no `dg-agent run` parent to prepend a floor,
    # so a plan asserting one is a prompt promising an agent a floor it has not
    # got, and there is no invocation later that could put it right.
    if plan.mode == "session" and plan.confine == "require":
        return ("--mode session and --confine require contradict: the floor is "
                "prepended by `dg-agent run`, and a subagent the session spawns "
                "has no such parent — so the plan would assert a floor its "
                "agents never get. Use --confine off and read what the mode "
                "gives up, or --mode process and keep the floor")
    if plan.confine == "off":
        return None
    # The second combination, found while writing the presets. A floor seals
    # `limits.writable_roots`, which never reads `$DG_WRITE` -- so `open` stops
    # the *gate* asking about a write outside the project while the kernel goes
    # on refusing it. The agent gets a bare `EACCES` carrying none of the prose
    # `limits.refuse_write` exists to attach, and the launcher believes it
    # granted something it did not. Refused where it is written, for the reason
    # below: the pair is wrong rather than the command.
    if plan.write == "open":
        return ("--write open and --confine require contradict: the floor "
                "seals the same roots the gate judges, so `open` only stops "
                "the gate asking and the write still fails — as an unexplained "
                "permission error rather than a question. Use --write launch, "
                "which the floor already enforces, or --confine off and mean it")
    try:
        if not _confine.configures_runner(plan.floor, None):
            return None
    except ValueError as exc:
        return str(exc)
    host = HOSTS.get(plan.host)
    if host is None or plan.floor in host.carries:
        return None
    portable = [b for b in _confine.BACKENDS if not _confine.configures_runner(b, None)]
    return (f"a {plan.floor!r} floor is the runner's own settings, and "
            f"{plan.host} does not read them — so no line generated here could "
            f"apply it and the run would be unconfined while saying otherwise. "
            f"Use --floor {' or '.join(portable) or 'a backend that wraps the '
            f'command'}, which wraps the command under any runner, or "
            f"--confine off and mean it")


def readiness(proj: project.Project | None = None) -> list[Check]:
    """What has to be true before a fan-out can start.

    Read-only and never raises: this is the first thing both modes show, and a
    wizard that fell over while reporting that something was missing would be
    reporting it the hard way.
    """
    proj = proj or project.find()
    out = [
        Check(proj.has_decisions, "decisions.json exists", "dg init"),
        Check(proj.has_tasks, "tasks.json exists", "dg task init"),
    ]
    try:
        free = len(agents.sequence()) - len(agents.load(proj.root))
    except Exception:
        free = 0
    out.append(Check(free > 0, f"{free} agent names free",
                     "dg-agent prune, or dg apply first"))
    try:
        from dgraph.tasks import TaskGraph
        from dgraph import cross
        from dgraph.model import Graph
        tg = TaskGraph.load(proj.tasks) if proj.has_tasks else None
        g = Graph.load(proj.store) if proj.has_decisions else None
        ready = ([t for t in sorted(tg.tasks) if cross.ready(tg, g, t)]
                 if tg is not None and g is not None else [])
    except Exception:
        ready = []
    out.append(Check(bool(ready),
                     f"{len(ready)} task(s) ready to claim"
                     + (f": {', '.join(ready[:5])}" if ready else ""),
                     "dg task add, and settle what blocks the rest",
                     bars=False))
    # A warning rather than a bar: a fan-out against a graph with nothing open
    # is legitimate if the agents are meant to propose questions, and refusing
    # it would refuse the one thing fan-out is best at.

    # **The step people skip, and the one nothing checked.** Every check above
    # is about the *graph*; this is the first about the *run*, which is why it
    # was missing rather than argued away. `dg gate` answers `ask` where it
    # cannot decide, and with no broker listening that `ask` reaches the host
    # as a permission prompt — in a headless `claude -p` there is nobody to
    # answer it, so every escalation for the whole run is a refusal nobody
    # chose. That is the failure the broker exists for, met by a launcher who
    # had just been shown four green ticks. Audit `G-F7`.
    #
    # A warning like the one above it, and for a comparable reason: a broker is
    # genuinely optional — its own docstring says a project that never starts
    # one behaves exactly as it did before — and a run whose allowlist covers
    # everything it needs will never escalate. Refusing that would refuse a
    # legitimate run; saying nothing is what produced the finding.
    try:
        from dgraph import broker as _broker
        up = _broker.listening(proj.root)
    except Exception:
        up = False
    out.append(Check(
        up,
        "a consent broker is listening" if up else
        "no consent broker — an escalation will be refused, not asked",
        "dg-agent broker (a terminal), or `--relay --plan fanout/env.json` to "
        "answer from a session",
        bars=False))
    return out


#: The loop itself. An agent that had to ask before `dg show` would be asking
#: about the commands a fan-out is made of, and the supervisor answering would
#: be approving its own procedure a hundred times over.
ALWAYS = ("dg", "dg-agent")

#: Programs that cannot change anything by themselves.
#:
#: Reads are never judged -- an agent that cannot read the repository it is
#: reasoning about is blindfolded rather than constrained -- and a `cat` that
#: had to be approved would contradict that in spirit while obeying it in
#: letter. Redirection does not widen these, because `cat a > b` is
#: composition and `limits.recognise` sends it to a person anyway.
#:
#: **`find` and `sed` are deliberately absent**, and their absence is not an
#: oversight to be fixed by whoever notices it. `find -delete` deletes and
#: `sed -i` rewrites in place -- and `sed -i` on the decision store is the
#: exact move `limits.protected_paths` exists to stop.
READERS = ("ls", "cat", "head", "tail", "wc", "grep", "rg", "diff",
           "file", "stat", "which", "pwd", "echo", "basename", "dirname")

#: What a marker file says about the programs a fan-out will need. A proposal
#: for a person to edit, never a decision: `dg-agent setup` writes it into
#: `env.json` where it can be read back, cut down, and diffed next time.
#:
#: Coarse on purpose. `cargo` here means all of cargo, and that concedes
#: arbitrary code execution -- running a build tool *is* running project code,
#: so `cargo build` is no safer than `cargo run` and a finer list would claim a
#: precision it could not keep. What answers that is a confinement floor, not a
#: longer allowlist.
MARKERS = {
    "Cargo.toml": ("cargo",),
    "pyproject.toml": ("python3", "pytest"),
    "setup.py": ("python3", "pytest"),
    "requirements.txt": ("python3", "pytest"),
    "package.json": ("npm", "node"),
    "Makefile": ("make",),
    "go.mod": ("go",),
    "dune-project": ("dune",),
    "_CoqProject": ("dune", "rocq", "coqc"),
    "CMakeLists.txt": ("cmake", "ctest"),
    # All of git, including the config that points its pager at a shell. Listed
    # because a fan-out that cannot read its own history is hobbled, and put
    # here rather than in `ALWAYS` so that it shows up in `env.json` as
    # something a launcher chose to keep.
    ".git": ("git",),
}


def propose_exec_allow(proj: project.Project | None = None) -> list[str]:
    """What this project looks like it needs, for a person to edit.

    The first setup field answered by the **filesystem** rather than by the
    graph, and the reason a per-run list beats a committed one: a list written
    once goes stale in a repository nobody re-reads it in, and a derived one is
    recomputed every run and cannot.

    Order is `ALWAYS`, then readers, then whatever the markers found, so the
    line reads as the loop, the eyes, and the work.
    """
    proj = proj or project.find()
    found: list[str] = [*ALWAYS, *READERS]
    for marker, programs in MARKERS.items():
        if (proj.root / marker).exists():
            found.extend(programs)
    return list(dict.fromkeys(found))



# ---- the curated remits -----------------------------------------------------

#: The policy answers a preset does **not** vary, and why each is a constant
#: rather than a dial. Applied by every preset, so choosing one answers the
#: whole policy block rather than part of it — a preset that left three of
#: seven unanswered would leave the person exactly where they started.
#:
#: - `write` — `launch` throughout. `open` cannot be combined with a floor:
#:   `confine.policy` builds the sealed roots from `limits.writable_roots`,
#:   which never reads `$DG_WRITE`, so `open` silently stops the *gate* asking
#:   while the kernel goes on refusing. `plan_fault` refuses that pair now, and
#:   no preset may emit it.
#: - `area` — `open` throughout. A scout finding a corner nobody had named is a
#:   finding, and the similarity guard is what makes that safe. Nothing about
#:   trusting an agent's judgement changes it.
#: - `terse` — `on` throughout. It is about whoever reads the panel while
#:   deciding, not about the agent: a trusted agent writing 800-character
#:   answers makes the graph just as unreadable.
#: - `confine` — `require` throughout, degrading to `off` only where no backend
#:   is usable, which `_with_available_floor` decides from the machine. "I trust
#:   their judgement" and "I want a shell redirection to escape the gate" are
#:   different claims, and merging them into one word is how a floor gets turned
#:   off by somebody who only meant the first.
#:
#: The budget is deliberately absent: it varies with the size of the work rather
#: than with the remit, and the wizard asks it either way.
PRESET_CONSTANTS = {"write": "launch", "area": "open", "terse": "on"}

#: What an agent may run unasked, as a rule rather than a list -- the list is
#: per-project and comes from the marker files, so a preset that held one would
#: hold a guess about somebody else's repository.
#:
#: `readers` is `ALWAYS + READERS`: the loop, and the programs that cannot
#: change anything. Emphatically not the empty list, which reads as the
#: strictest option and is in fact unusable -- every command escalating means a
#: person approving `cat`, which spends the supervisor's attention on nothing
#: and teaches them to approve without looking.
EXEC_SCOPES = ("readers", "project")


@dataclass(frozen=True)
class Preset:
    """One curated remit: the policy block, answered in a word.

    The `row` is not decoration. A card reading `Maintainer` that quietly
    widened `$DG_DECIDE` would be the `DG_DECIDE=nevr` failure wearing a
    friendlier name -- a rule removed in the direction of more permission by
    something nobody could read. So the label is a handle and the row is the
    truth, printed beside it everywhere the name appears: the wizard's cards,
    `dg-agent presets`, and the table in `agentic/README.md`.
    """

    name: str
    row: str
    decide: str
    exec_scope: str
    #: `$DG_APPLY`. The third field to vary, and the one that decides whether
    #: the tray is an approval queue or only an isolation between writers.
    apply: str = "own"

    def __post_init__(self) -> None:
        if self.decide not in _env.POLICIES:
            raise ValueError(f"{self.name}: decide is {self.decide!r}")
        if self.exec_scope not in EXEC_SCOPES:
            raise ValueError(f"{self.name}: exec_scope is {self.exec_scope!r}")
        if self.apply not in _env.APPLY_POLICIES:
            raise ValueError(f"{self.name}: apply is {self.apply!r}")


#: Three points on a dial the tool already had. They invent no policy: the
#: middle one is exactly what `Plan()` has always defaulted to, so naming the
#: presets changed nobody's run.
#:
#: Two fields vary, and that is the honest count. The value of a preset is not
#: that it moves seven knobs -- it is that one answer settles all seven, five of
#: them by holding a constant somebody would otherwise have to derive from
#: `agentic/README.md` before they could start.
PRESETS = {
    "scout": Preset(
        "scout", "proposes only · nothing lands unapproved",
        decide="never", exec_scope="readers", apply="never"),
    "contributor": Preset(
        "contributor", "settles what evidence backs",
        decide="evidence", exec_scope="project"),
    "maintainer": Preset(
        "maintainer", "settles anything",
        decide="open", exec_scope="project"),
}

#: What `dg-agent setup` starts on, and what the wizard preselects. The same
#: remit `Plan()` already had.
DEFAULT_PRESET = "contributor"


def preset_exec_allow(scope: str, proj: project.Project | None = None) -> list[str]:
    """The programs a scope names, for this project."""
    if scope == "readers":
        return [*ALWAYS, *READERS]
    return propose_exec_allow(proj)


def apply_preset(plan: Plan, name: str,
                 proj: project.Project | None = None) -> Plan:
    """`plan` with one preset's whole policy block applied.

    Expanded here and never stored. `env.json` goes on holding the resolved
    values and no preset name, for the reason it holds no focus ids either: a
    name recorded beside the values it produced is free to disagree with them
    the moment somebody edits one, and the file exists to close exactly that
    gap.
    """
    if name not in PRESETS:
        raise ValueError(f"{name!r} is not one of "
                         f"{', '.join(PRESETS)} -- see `dg-agent presets`")
    p = PRESETS[name]
    return _with_available_floor(replace(
        plan, decide=p.decide, apply=p.apply,
        exec_allow=preset_exec_allow(p.exec_scope, proj),
        confine="require", **PRESET_CONSTANTS))


def preset_of(plan: Plan, proj: project.Project | None = None) -> str | None:
    """Which preset this plan is still exactly, or `None` once it was edited.

    Used to preselect the wizard's card and to say `(edited)` when nothing
    matches. Deliberately an equality test against a freshly applied preset
    rather than a stored field, which is the same argument `apply_preset`
    makes: the plan is the truth, and a name is only ever a summary of it.
    """
    return next((name for name in PRESETS
                 if apply_preset(plan, name, proj) == _with_available_floor(plan)),
                None)


def preset_rows(proj: project.Project | None = None) -> list[tuple[str, str, str, str]]:
    """`(name, row, decide, programs, apply)` for each preset, in order.

    One renderer's worth of data, so the wizard's cards, `dg-agent presets` and
    the guide's table cannot come to disagree about what a name means.
    """
    return [(p.name, p.row, p.decide,
             " ".join(preset_exec_allow(p.exec_scope, proj)), p.apply)
            for p in PRESETS.values()]



def _with_available_floor(plan: Plan) -> Plan:
    """`plan`, with a floor this machine can actually provide.

    Proposed, then checked. A plan that asked for a floor no backend here can
    provide is a plan that refuses to launch, and the wizard's job is to hand
    back something that runs — so it drops to `off` and the reason travels in
    `dg-agent env`, where a launcher reads it before the first agent starts.

    Shared by `defaults` and `apply_preset` rather than repeated: a preset that
    asserted `confine=require` on a machine with no backend would write a plan
    whose prompt promises an agent a floor it has not got.
    """
    from dgraph import confine as _confine
    # A session-hosted run has no floor to have, whatever this machine offers:
    # the backend is applied by the spawn line, and there is no spawn line.
    # Answering `require` here would write the plan `plan_fault` then refuses.
    if plan.mode == "session":
        return replace(plan, confine="off")
    if _confine.available(plan.floor)[0]:
        return plan
    other = next((b for b in _confine.BACKENDS
                  if _confine.available(b)[0]), None)
    return replace(plan, floor=other or plan.floor,
                   confine="require" if other else "off")


def defaults(proj: project.Project | None = None) -> Plan:
    """A plan with everything the graph can answer already answered."""
    proj = proj or project.find()
    plan = _with_available_floor(
        replace(Plan(), exec_allow=propose_exec_allow(proj)))
    try:
        from dgraph.tasks import TaskGraph
        from dgraph import cross
        from dgraph.model import Graph
        tg = TaskGraph.load(proj.tasks) if proj.has_tasks else None
        g = Graph.load(proj.store) if proj.has_decisions else None
        if tg is not None and g is not None:
            ready = [t for t in sorted(tg.tasks) if cross.ready(tg, g, t)]
            plan = replace(plan, focus=ready[:3])
    except Exception:
        pass
    return plan


def _chain(proj: project.Project, ids: list[str]) -> str:
    """`dg context <id> --full` for each focus id, pasted.

    Verbatim and not summarised. The chain is the one part of a prompt that a
    fresh context cannot reconstruct, and a paraphrase of a premise is a
    premise nobody can check.
    """
    from dgraph import context as _context
    out = []
    for vid in ids:
        try:
            out.append(_context.text(_context.data(proj, vid)).rstrip())
        except Exception as exc:
            out.append(f"[{vid}: could not be read — {exc!r}]")
    return "\n\n".join(out) if out else "[no focus ids given]"


def _assigned_prose(plan: Plan) -> str:
    """What "The loop" opens with, given a roster or the absence of one.

    **The default keeps its exact words.** `scout.md` has said *nothing is
    assigned to you* since the first fan-out, and `agentic/README.md` argues
    for it: self-selection is what lets a run absorb a queue that moves while
    it runs. A roster is the exception, so the exception is what reads
    differently — an unrostered plan renders the sentence it always did, to the
    byte, and a diff of two generated prompts shows the roster and nothing
    else.

    It does not end the loop. An agent given a task still reads the frontier
    when that task is done, for the reason the section below gives in as many
    words: one agent finishing makes work startable for another that was never
    told it existed.
    """
    if not plan.roster:
        return ("Nothing is assigned to you. Read the frontier, take "
                "something, finish it, read\nagain:")
    if plan.mode == "session":
        # No launcher and no `dg-agent run`, so nothing sets `$DG_TASK`. The
        # session that spawned this agent passed the roster and is the only
        # party that can hand the assignment over -- in the spawn
        # instructions, which is where the agent is told to look. `D61`.
        return (
            "**Your task was named by the session that spawned you**, in its "
            "instructions to\nyou — this fan-out was launched with a roster, "
            "one agent per task, and there is\nno `$DG_TASK` here to read it "
            "from. Start there:\n"
            "\n"
            "```sh\n"
            "dg task start <the task you were given>\n"
            "```\n"
            "\n"
            "If you were not given one, say so and read the frontier instead. "
            "That is a starting\npoint and not a fence. Nothing stops you "
            "taking more, and you should: when your\ntask is done, read the "
            "frontier and carry on as below. The roster says where each\n"
            "agent *begins*, so that two agents do not open the same work at "
            "the same moment\n— it does not say where any of them stops:")
    return (
        "**`$DG_TASK` is yours.** This fan-out was launched with a roster "
        "— one agent per\ntask — and the launcher named yours. Start "
        "there:\n"
        "\n"
        "```sh\n"
        'dg task start "$DG_TASK"\n'
        "```\n"
        "\n"
        "That is a starting point and not a fence. Nothing stops you "
        "taking more, and\nyou should: when your task is done, read the "
        "frontier and carry on as below.\nThe roster says where each "
        "agent *begins*, so that two agents do not open the\nsame work at "
        "the same moment — it does not say where any of them stops:")


def _mode_prose(plan: Plan) -> str:
    """What the agent must be told about where it is running. `D52`.

    Empty under `process`, which is the point: the enforced mode has nothing to
    say, so a generated prompt reads identically to the one this tool has always
    produced and a diff of two shows the mode and nothing else.

    Under `session` this is the site of the three that **changes behaviour**
    rather than informing a human. `D32` established that a subagent shares its
    session's environment, so `$DG_AGENT` is carried only by prefixing each
    call — and a mechanism that depends on the agent cooperating works only if
    the agent is asked. The rest of the list is here because an agent that knows
    its budget is advisory is one that can park before it stops, which is the
    difference between work the next agent resumes and work somebody redoes.
    """
    # Empty string and no newline, so the token's own line closes up: a
    # generated `process` prompt is byte-identical to the one this tool wrote
    # before the mode existed, which is what makes a diff of two prompts show
    # the mode and nothing else.
    if plan.mode != "session":
        return ""
    # Filtered by host, like the setup report and for a better reason: this one
    # is read by an *agent*, and telling it about a defect in a runner it is not
    # running under is noise in the one document it must act on.
    rows = []
    for what, host, why in SESSION_LOSSES:
        if host and host != plan.host:
            continue
        rows.append(f"- **{what}** — {why}")
    return (
        "## Where you are running, and what it costs\n"
        "\n"
        "You were spawned **inside a session**, not as a child of `dg-agent "
        "run`. That is a\nchoice somebody made deliberately, and it means the "
        "rules below are advisory here\nrather than enforced:\n"
        "\n"
        + "\n".join(rows) + "\n"
        "\n"
        "**The first one is yours to keep.** Nothing sets `$DG_AGENT` for you, "
        "so prefix it\nyourself on every `dg` call — `DG_AGENT=<your name> dg "
        "…` — or every op you\nstage is filed as the supervisor's, and `dg "
        "apply` will let you take a tray that\nis not yours.\n"
        "\n"
        "**And park before you stop.** Your budget stops nothing here, so the "
        "one thing that\nsays where you got to is you saying it.\n\n")


def _decide_prose(policy: str) -> str:
    return {
        "evidence": ("You may close a question only where a **finished** "
                     "`--evidence-for` task backs it — the case where the "
                     "falsifier writes itself. Everything else is refused at "
                     "stage time, before you have composed an answer."),
        "never": ("Every answer goes back to a person. Record what you "
                  "measured with `dg task done --outcome` and move on."),
        "open": ("Nothing is enforced, so the rule above is a request rather "
                 "than a refusal. Hold to it anyway."),
    }[policy]


def _apply_prose(policy: str) -> str:
    """What `$DG_APPLY` means to the agent reading it.

    Both halves are worth saying plainly, because the two are opposite claims
    about the same tray and the prompt used to assert the second unconditionally
    — "nothing you stage is written until somebody applies it", which was simply
    untrue for an agent that could apply its own.
    """
    return {
        "own": ("`dg apply` writes **your own** staged ops and leaves every "
                "other writer's alone, so what you stage and apply is in the "
                "store. The tray keeps writers apart; it is not somebody "
                "approving you. Apply your own work — leaving it staged means "
                "it exists only in a gitignored file."),
        "never": ("You **stage only**. `dg apply` is refused for you and the "
                  "ops stay exactly where they are, for a caller with no "
                  "`$DG_AGENT` to read and apply. That refusal is the policy "
                  "and not a broken tool: here the tray *is* an approval "
                  "queue, so propose freely and leave it staged."),
    }[policy]


def _terse_prose(value: str) -> str:
    cap = limits.terse_limit(value)
    if cap is None:
        return ("No limit is set, so this is a request rather than a refusal. "
                "Hold to it anyway — the person reviewing your proposals is "
                "reading them in a panel.")
    return (f"A field longer than **{cap} characters** is refused at stage "
            f"time, before the tray is touched. That is the policy and not a "
            f"broken tool: write the development to a file and cite it.")


def _write_prose(policy: str, root: Path) -> str:
    """Where writes may go, and the two files inside that scope that are not
    an agent's to write.

    **The stores are named, and that is `P-F5`.** This used to promise writing
    "freely" under the roots, which stopped being true when
    `limits.protected_paths` landed: `decisions.json` and `tasks.json` are
    inside every one of those roots and are refused by a different rule, with a
    different fix. A prompt that promises a scope the gate will not give is
    worse than one that said nothing, because the agent has no way to check.
    """
    stores = ", ".join(f"`{Path(p).name}`" for p in limits.protected_paths(root))
    record = (f" The one exception is the record itself — {stores} — which "
              f"nothing writes with an editor. Every change to it goes through "
              f"`dg` and lands in the tray; applying the tray is the "
              f"supervisor's call." if stores else "")
    if policy == "open":
        return ("Your writes are not scoped. Stay inside the project anyway "
                "unless you have been told otherwise." + record)
    roots = ", ".join(f"`{r}`" for r in limits.writable_roots(root))
    return (f"You may write freely under {roots}. A write anywhere else stops "
            f"and asks the person — that is the policy, not a broken tool, so "
            f"put the file somewhere in scope or say what you need and why. "
            f"Reading is never restricted." + record)


def _exec_prose(names: list[str]) -> str:
    """What may be run without asking, and what an unlisted command does.

    Stated rather than discovered. This is usually the *first* rule an agent
    meets — it fires before a single write — and the prompt said nothing about
    it at all, so a scout met a refusal naming a variable it had never been
    told existed, with no move but to guess. `P-F5`.
    """
    if not names:
        return ("**Nothing is on your exec allowlist**, so every command you "
                "run is put to the person first. That is a launcher that has "
                "not filled `$DG_EXEC_ALLOW` in rather than a rule about you — "
                "say so early, because until it is set you cannot even run "
                "`dg`.")
    shown = ", ".join(f"`{n}`" for n in names)
    return (f"You may run {shown} without asking. **Anything else stops and "
            f"goes to the person**, and so does any command line that runs "
            f"more than one program — a pipe, a redirect, `&&`, a subshell — "
            f"because which program *that* runs cannot be read off the front "
            f"of it. Run the parts separately, or ask for the line as written "
            f"and it will be remembered for the rest of your run. This is the "
            f"policy, not a broken tool.")


def _confine_prose(mode: str, floor: str) -> str:
    """Whether a kernel-level floor sits under all of the above.

    Worth a paragraph because of what a refusal *looks like* when it comes from
    there: `Device or resource busy` or `Read-only file system`, from the tool
    the agent happened to be using, with no rule named. An agent that has been
    told the floor exists reads that as a boundary; one that has not reads it
    as a broken machine and retries.
    """
    if mode == "off":
        return ("No kernel-level floor is in place, so the rules above are "
                "enforced by `dg` and the gate. Hold to them anyway.")
    return (f"A confinement floor (`{floor}`) sits under all of the above, so "
            f"the boundaries are enforced by the kernel and not only by the "
            f"tooling. A refusal from it does **not** name a rule — you will "
            f"see `Read-only file system` or `Device or resource busy` from "
            f"whatever tool you were using. That is this policy, not a broken "
            f"machine and not something to retry: check the scope above, and "
            f"if the write was legitimate, say what you need and why.")


def _area_prose(policy: str) -> str:
    if policy == "strict":
        return ("**`$DG_AREA=strict`**: you may file only under an area "
                "already in use. A genuinely new one is refused at stage time "
                "and goes back to a person as a proposal — say what it should "
                "be called and why.")
    return ("**`$DG_AREA=open`**: you may file under a new area, though one "
            "that closely resembles an area already in use is refused so that "
            "two names for one thing do not appear. Prefer an area already in "
            "use; the list above is what there is.")


def _budget_prose(seconds: int | None) -> str:
    if seconds is None:
        return ("No budget was set, so nothing will hand your work back for "
                "you.")
    return (f"You have **{limits.show_span(seconds)}**. Decide what to finish "
            f"rather than being cut off mid-way, and touch the graph before "
            f"any long silent stretch so a supervisor can tell you from an "
            f"agent that died.")


#: The comment blocks in the template tell whoever fills it in by hand what
#: goes in each token. Once filled they are instructions for work already done,
#: and an agent that reads them goes looking for blanks that are not there.
_COMMENT = ("<!--", "-->")


def _strip_comments(text: str) -> str:
    out, rest = [], text
    while _COMMENT[0] in rest:
        head, _, rest = rest.partition(_COMMENT[0])
        out.append(head)
        _, _, rest = rest.partition(_COMMENT[1])
        rest = rest.lstrip("\n") if head.endswith("\n") else rest
    out.append(rest)
    return "".join(out)


def _areas(proj: project.Project) -> str:
    """The areas in use, with how much is filed under each, across both stores.

    The union rather than one store's list, because that is what the guard an
    agent will meet reads: an area known only to `tasks.json` is an area a
    decision may be filed under, and a prompt that named only the decision
    store's would send a scout to invent a synonym for one that exists.

    Counts, because "in use" is the question a scout actually has. An area
    holding one record and an area holding thirty read identically as a bare
    name, and the second is where a proposal belongs.
    """
    try:
        from dgraph.model import Graph
        from dgraph.tasks import TaskGraph
        g = Graph.load(proj.store) if proj.has_decisions else None
        tg = TaskGraph.load(proj.tasks) if proj.has_tasks else None
        d = _env_areas.counts(g.areas, g.vertices.values()) if g else {}
        t = _env_areas.counts(tg.areas, tg.tasks.values()) if tg else {}
        rows = []
        for a in list(d) + [a for a in t if a not in d]:
            held = [f"{d[a]} decision(s)" for _ in (1,) if d.get(a)]
            held += [f"{t[a]} task(s)" for _ in (1,) if t.get(a)]
            rows.append(f"`{a}`" + (f" ({', '.join(held)})" if held else
                                    " (registered, nothing in it yet)"))
        return ", ".join(rows) if rows else "none yet"
    except Exception:
        return "none yet"


def render_scout(plan: Plan, proj: project.Project | None = None) -> str:
    """The filled prompt: the template, with every token substituted.

    Substitution rather than generation, and that is the whole design. The
    guidance an agent needs — the loop, the rule, the two things easy to get
    wrong — is prose that belongs in one file a person can read and edit. If
    this composed its own prompt instead, that prose would exist twice and the
    two copies would disagree within a month, which is the failure this repo
    has already recorded twice over `gate.TRIGGERS`.

    No ⟨…⟩ survives, which a test asserts: a blank left in a generated prompt
    is an instruction to the agent to go looking for something nobody filled.
    """
    proj = proj or project.find()
    body = _strip_comments((templates() / "scout.md")
                           .read_text(encoding="utf-8")).lstrip("\n")
    reads = "\n".join(f"- `{p}` — {what}" for p, what in plan.reads) or \
        "- (nothing named — ask the person before you go looking)"
    fills = {
        "PROJECT": proj.root.name,
        "BRIEF": plan.brief or "(not stated — ask before you start)",
        "CHAIN": _chain(proj, plan.focus),
        "ASSIGNED": _assigned_prose(plan),
        "MODE_PROSE": _mode_prose(plan),
        "AREAS": _areas(proj),
        "DECIDE": plan.decide,
        "DECIDE_PROSE": _decide_prose(plan.decide),
        "APPLY": plan.apply,
        "APPLY_PROSE": _apply_prose(plan.apply),
        "TERSE": plan.terse,
        "TERSE_PROSE": _terse_prose(plan.terse),
        "READS": reads,
        "WRITE": plan.write,
        "WRITE_PROSE": _write_prose(plan.write, proj.root),
        "FINDINGS": plan.findings,
        "BUDGET_PROSE": _budget_prose(plan.budget),
        "AREA_PROSE": _area_prose(plan.area),
        "EXEC_PROSE": _exec_prose(list(plan.exec_allow)),
        "CONFINE_PROSE": _confine_prose(plan.confine, plan.floor),
    }
    for token, value in fills.items():
        body = body.replace(f"⟨{token}⟩", value)
    return body


def render_launch(plan: Plan, proj: project.Project | None = None) -> str:
    """One line per agent, run under an environment nothing here spells twice.

    **No `DG_` assignment and no `timeout` survive in this file**, and both
    absences are the point.

    The assignments went because a bare `DG_DECIDE=evidence` in a shell line is
    a value nothing validates: mistype it and `cross.policy` answers `open` —
    the widest policy — silently, in the direction of more permission.
    `dg-agent run` validates before it spawns, and `dg-agent env --check
    --plan` catches it before the loop even starts.

    The `timeout` went because it and `--budget` were two independent numbers
    saying one thing. They agree in generated output and stop agreeing the
    moment somebody edits this file, which the README expects. Now the budget
    *is* the timeout, and the process that enforces it is the child's parent,
    so it can also hand the work back.
    """
    proj = proj or project.find()
    prompt = f"{plan.out}/scout.md"
    spawn = HOSTS[plan.host].spawn.format(prompt=shlex.quote(prompt))
    # The half of the floor `dg-agent run` cannot apply. A backend that wraps
    # the command is host-neutral and is prepended there; one that configures
    # the runner speaks the runner's own vocabulary, so it can only be carried
    # by the line that knows which runner it is calling — this one.
    #
    # A literal string on argv rather than a file: settings are read at launch,
    # and a launcher's own settings are the ones a runner honours, so this is
    # the version that is obviously out of an agent's reach rather than out of
    # reach by a second rule.
    carried = False
    if plan.confine != "off" and plan.floor in HOSTS[plan.host].carries:
        from dgraph import confine as _confine
        arg = _confine.settings_arg(_confine.render(plan.floor, proj.root))
        if arg:
            spawn = spawn.replace("claude -p",
                                  f"claude --settings {shlex.quote(arg)} -p", 1)
            carried = True
    env_file = shlex.quote(f"{plan.out}/{ENV_NAME}")
    lines = [
        "#!/usr/bin/env bash",
        "# Generated by `dg-agent setup`. Re-run it to regenerate.",
        "#",
        f"# The remit is in {plan.out}/{ENV_NAME}, which is also what",
        "# fanout/scout.md was rendered from — so the prompt's claims about",
        "# what each agent may do and what this actually sets cannot drift.",
        "# Change a policy there, not here.",
        "set -euo pipefail",
        "",
        'cd "$(dirname "$0")/.."',
        "",
        "# Before any agent starts: every value in the plan is one this `dg`",
        "# understands, and nothing exported in this shell contradicts it.",
        f"dg-agent env --check --plan {env_file}",
        "",
        "# ...and whether anybody is there to answer an escalation. A warning,",
        "# never a refusal: a run with no broker is legal and behaves exactly as",
        "# it did before one existed -- the gate returns its own verdict, and an",
        "# `ask` becomes a refusal. What is not legal is finding that out from",
        "# an agent that stopped. `agentic/QUICKSTART.md` calls this the step",
        "# people skip, and until now only prose said so. `D53`.",
        "dg-agent broker --check || true",
        "",
    ]
    if plan.capture:
        bin_dir = capture_bin()
        if bin_dir is None:
            lines += [
                "# Capture was asked for, and this dear-guide install has no",
                "# agentic/bin/dg — a wheel install ships no wrapper. Run from",
                "# a checkout, or drop --capture.",
                'echo "capture requested but no wrapper in this install" >&2',
                "exit 1",
                "",
            ]
        else:
            lines += [
                "# The capture: every `dg` and `dg-agent` call of the run, with",
                "# both trays as they stood after it. Optional, not part of the",
                "# procedure.",
                "#",
                "# An absolute path on purpose: the wrappers live in the",
                "# dear-guide checkout, not in this project, so $PWD would find",
                "# nothing and record nothing while looking like it worked.",
                f'export PATH={shlex.quote(str(bin_dir))}:"$PATH"',
                f'command -v dg | grep -q {shlex.quote(str(bin_dir))} || '
                '{ echo "capture wrapper not first on PATH" >&2; exit 1; }',
                # Its own assertion, because after the split most of what a
                # capture of a *fan-out* is for — the claims, the setup, the
                # launch, the parks — goes through the other name.
                f'command -v dg-agent | grep -q {shlex.quote(str(bin_dir))} || '
                '{ echo "dg-agent capture wrapper not first on PATH" >&2; exit 1; }',
                "",
            ]
    if plan.mode == "session":
        # **No `dg-agent run`, because there is nothing for it to parent.** The
        # session spawns the agents with its own tool, so what this file can
        # usefully be is the two things that still apply and a statement of what
        # does not — rather than a script that would look like the other mode's
        # and enforce none of it.
        return "\n".join([
            "#!/usr/bin/env bash",
            "# Generated by `dg-agent setup --mode session`.",
            "#",
            "# **There is no launcher for this mode.** The session that set the",
            "# fan-out up spawns the agents itself, with its own subagent tool,",
            "# reading fanout/scout.md. What that costs is in the prompt and in",
            "# `agentic/RUNNING.md`; the short of it is that every rule below",
            "# becomes advisory, because `dg-agent run` is what enforced them",
            "# and it is not in the picture.",
            "set -euo pipefail",
            "",
            'cd "$(dirname "$0")/.."',
            "",
            "# Still worth running, and the only two things here that are:",
            "# the remit is one this `dg` understands...",
            f"dg-agent env --check --plan {env_file}",
            "# ...and somebody is there to answer an escalation.",
            "dg-agent broker --check || true",
            "",
            "echo 'mode: session — spawn the agents from the session, then'",
            "echo '`dg pending` to read what they proposed.'",
            "",
        ])
    run = f"dg-agent run{' --floor-applied' if carried else ''}"
    if plan.roster:
        # One agent per named task, and the ids are the loop rather than a
        # count: `seq` would put the roster's length in the file twice, once
        # as a number and once as the list it came from.
        lines += [
            "# One agent per task, named. `$DG_TASK` reaches the agent through",
            "# `dg-agent run`, which sets it for the child only -- the same",
            "# rule as `$DG_AGENT`, and for the same reason: an exported",
            "# assignment would make this launcher an agent holding work.",
            "for t in %s; do" % " ".join(shlex.quote(t) for t in plan.roster),
            f"  {run} --plan {env_file} --task \"$t\" \\",
            f"    -- {spawn} &",
            "done",
            "",
        ]
    else:
        lines += [
            "for i in $(seq 1 %d); do" % plan.agents,
            f"  {run} --plan {env_file} \\",
            f"    -- {spawn} &",
            "done",
            "",
        ]
    lines += [
        "dg-agent list      # who holds what, time left, and who has gone quiet",
        "wait",
        "# `dg-agent run` already parked what a child of *this* script dropped.",
        "# This is the backstop for what it cannot see: the script itself being",
        "# killed, the machine going down, the terminal closing.",
        "dg-agent expire",
        "dg pending         # the tray: who proposed what",
        "",
    ]
    return "\n".join(lines)


def write(plan: Plan, proj: project.Project | None = None) -> list[Path]:
    """Write both artefacts and return what was written."""
    proj = proj or project.find()
    out = plan.resolved_out(proj)
    out.mkdir(parents=True, exist_ok=True)
    scout, launch = out / "scout.md", out / "launch.sh"
    spec = out / ENV_NAME
    project.write_atomic(scout, render_scout(plan, proj))
    project.write_atomic(launch, render_launch(plan, proj))
    # Last, and with a trailing newline, because this one is read by a machine
    # and diffed by a person: it is the file both of the others were generated
    # from, and the only one anything checks them against.
    project.write_atomic(spec, json.dumps(env_plan(plan), indent=2,
                                          ensure_ascii=False) + "\n")
    launch.chmod(0o755)
    written = [scout, launch, spec]
    # Last, and after `chmod`, so what is recorded is the file as it will be
    # read back. See `DIGEST_NAME`.
    record_written(proj, written)
    return written
