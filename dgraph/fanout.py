"""Setting up a fan-out: the answers, and what they turn into.

`agentic/README.md` is the procedure a person follows by hand. This is the same
procedure as data — the questions it asks, the ones it can answer from the
graph, and the two artefacts it writes:

    <out>/scout.md     the filled prompt, with no ⟨…⟩ left in it
    <out>/launch.sh    one line per agent, with the environment already right

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

from dgraph import agents, limits, project

#: Where `dg agent setup` writes, relative to the project root. Not
#: `.dgraph-*`: those are scratch the `.gitignore` hides, and a filled prompt is
#: the opposite — the thing you want to read back, diff, and reuse next time.
OUT_DIR = "fanout"

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
    #: The three answers no graph can supply.
    brief: str = ""
    reads: list[tuple[str, str]] = field(default_factory=list)
    findings: str = "findings/<task-id>-<slug>.md"
    capture: bool = False
    out: str = OUT_DIR

    def resolved_out(self, proj: project.Project) -> Path:
        return proj.root / self.out


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
                     "dg agent prune, or dg apply first"))
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


def defaults(proj: project.Project | None = None) -> Plan:
    """A plan with everything the graph can answer already answered."""
    proj = proj or project.find()
    plan = Plan()
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
    try:
        from dgraph.model import Graph
        g = Graph.load(proj.store)
        found = sorted({v.area for v in g.vertices.values() if v.area})
        return ", ".join(f"`{a}`" for a in found) if found else "none yet"
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
    """One line per agent, with the environment already right."""
    proj = proj or project.find()
    prompt = f"{plan.out}/scout.md"
    spawn = HOSTS[plan.host].format(prompt=shlex.quote(prompt))
    claim = "dg agent claim"
    if plan.budget is not None:
        claim += f" --budget {limits.show_span(plan.budget)}"
    lines = [
        "#!/usr/bin/env bash",
        "# Generated by `dg agent setup`. Re-run it to regenerate.",
        "#",
        "# Read `agentic/RUNNING.md` before changing this: the assignment is",
        "# per command and must never be exported — an exported $DG_AGENT makes",
        "# the launcher an agent, and its own policy then refuses it.",
        "set -euo pipefail",
        "",
        f'cd "$(dirname "$0")/.."',
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
                "# The capture: every `dg` call of the run, with both trays as",
                "# they stood after it. Optional, not part of the procedure.",
                "#",
                "# An absolute path on purpose: the wrapper lives in the",
                "# dear-guide checkout, not in this project, so $PWD would find",
                "# nothing and record nothing while looking like it worked.",
                f'export PATH={shlex.quote(str(bin_dir))}:"$PATH"',
                f'command -v dg | grep -q {shlex.quote(str(bin_dir))} || '
                '{ echo "capture wrapper not first on PATH" >&2; exit 1; }',
                "",
            ]
    lines += ["for i in $(seq 1 %d); do" % plan.agents]
    env = [f"DG_AGENT=$({claim})", f"DG_DECIDE={plan.decide}",
           f"DG_WRITE={plan.write}", f"DG_TERSE={plan.terse}"]
    if plan.budget is not None:
        env.append(f"DG_BUDGET={limits.show_span(plan.budget)}")
    lines += [f"  {' '.join(env)} \\"]
    if plan.budget is not None:
        lines += [f"    timeout {plan.budget} {spawn} &"]
    else:
        lines += [f"    {spawn} &"]
    lines += [
        "done",
        "",
        "dg agent list      # who holds what, time left, and who has gone quiet",
        "wait",
        "dg agent expire    # hand back what any out-of-time agent still holds",
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
    project.write_atomic(scout, render_scout(plan, proj))
    project.write_atomic(launch, render_launch(plan, proj))
    launch.chmod(0o755)
    return [scout, launch]
