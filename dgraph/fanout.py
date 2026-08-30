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
ENV_FIELDS = {"decide": "DG_DECIDE", "write": "DG_WRITE",
              "area": "DG_AREA", "terse": "DG_TERSE",
              "budget": "DG_BUDGET", "exec_allow": "DG_EXEC_ALLOW"}

#: The hosts a launch line can be generated for. `mixed` is not among them
#: because a fan-out across two hosts is two launch files, and pretending
#: otherwise would produce a script that runs neither.
HOSTS = {
    "claude": 'claude -p "$(cat {prompt})"',
    "opencode": 'opencode run "$(cat {prompt})"',
}


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
    """One prerequisite, and how to satisfy it if it is not met."""
    ok: bool
    label: str
    fix: str = ""


@dataclass
class Plan:
    """Every answer the wizard needs. Built by the TUI or by CLI flags."""

    #: Decision or task ids the fan-out is aimed at. Their chains are pasted
    #: into the prompt, which is the single most valuable thing in it: a fresh
    #: context knows the task and nothing about why it exists, and without the
    #: chain it cannot tell a constraint from an implementation detail.
    focus: list[str] = field(default_factory=list)
    agents: int = 2
    host: str = "claude"
    decide: str = "evidence"
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
    #: The three answers no graph can supply.
    brief: str = ""
    reads: list[tuple[str, str]] = field(default_factory=list)
    findings: str = "findings/<task-id>-<slug>.md"
    capture: bool = False
    out: str = OUT_DIR

    def resolved_out(self, proj: project.Project) -> Path:
        return proj.root / self.out


def env_plan(plan: Plan) -> dict:
    """The `Plan`'s environment, as the object `env.json` holds.

    Only the remit. A plan file that also carried the focus ids, the host and
    the brief would be a second copy of `scout.md`, free to disagree with it —
    which is the failure this file is being written to close, arrived at from
    the other direction.

    The budget is seconds, not `30m`: it is the one field that is a *number*,
    and writing it as a span would mean the file round-tripped through a parser
    that can refuse. `plan_env` renders it back for the environment.
    """
    return {"decide": plan.decide, "write": plan.write, "area": plan.area,
            "terse": plan.terse, "budget": plan.budget,
            "exec_allow": list(plan.exec_allow)}


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
    for key, allowed in (("decide", _env.POLICIES), ("write", _env.WRITE_POLICIES),
                         ("area", _env.AREA_POLICIES)):
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
                     "dg task add, and settle what blocks the rest"))
    # A warning rather than a bar: a fan-out against a graph with nothing open
    # is legitimate if the agents are meant to propose questions, and refusing
    # it would refuse the one thing fan-out is best at.
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


def defaults(proj: project.Project | None = None) -> Plan:
    """A plan with everything the graph can answer already answered."""
    proj = proj or project.find()
    plan = replace(Plan(), exec_allow=propose_exec_allow(proj))
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
    if policy == "open":
        return ("Your writes are not scoped. Stay inside the project anyway "
                "unless you have been told otherwise.")
    roots = ", ".join(f"`{r}`" for r in limits.writable_roots(root))
    return (f"You may write freely under {roots}. A write anywhere else stops "
            f"and asks the person — that is the policy, not a broken tool, so "
            f"put the file somewhere in scope or say what you need and why. "
            f"Reading is never restricted.")


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
        "AREAS": _areas(proj),
        "DECIDE": plan.decide,
        "DECIDE_PROSE": _decide_prose(plan.decide),
        "TERSE": plan.terse,
        "TERSE_PROSE": _terse_prose(plan.terse),
        "READS": reads,
        "WRITE": plan.write,
        "WRITE_PROSE": _write_prose(plan.write, proj.root),
        "FINDINGS": plan.findings,
        "BUDGET_PROSE": _budget_prose(plan.budget),
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
    spawn = HOSTS[plan.host].format(prompt=shlex.quote(prompt))
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
    lines += [
        "for i in $(seq 1 %d); do" % plan.agents,
        f"  dg-agent run --plan {env_file} \\",
        f"    -- {spawn} &",
        "done",
        "",
        "dg-agent list      # who holds what, time left, and who has gone quiet",
        "wait",
        "# `dg-agent run` already parked what a child of *this* script dropped.",
        "# This is the backstop for what it cannot see: the script itself being",
        "# killed, the machine going down, the terminal closing.",
        "dg-agent expire",
        "dg pending         # the roster: who proposed what",
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
    return [scout, launch, spec]
