"""`dg-agent` — the launcher: names, budgets, and the environment they compose.

`dg` is for the graphs. Everything about spawning agents, naming them,
budgeting them and composing their environment is here, on the other side of
one sentence:

> **`dg-agent` writes the environment. `dg` reads it.**

`dg` must keep reading it, because that is where the rules are enforced — on
the path of every stage (`$DG_TERSE`), every close (`$DG_DECIDE`), every judged
write (`$DG_WRITE`), every new area (`$DG_AREA`) and every op's ownership stamp
(`$DG_AGENT`). None of that moved. What moved is everything that *decides what
those variables say* and everything that *spawns something to run under them*.

That is not an interface invented for the split. `agentic/README.md` has always
said the whole contract with the host is environment variables, and two
binaries either side of a documented contract express that better than one
binary that is both.

## Why the move was worth making

Because the boundary finally owns the thing that was missing. Three of the
variables **fail open** — `$DG_DECIDE=nevr` is `open`, the widest policy, and
looks exactly like a policy somebody chose — and each of the three docstrings
justified that by promising the CLI would report the typo where it is set.
Nothing did. `dg-agent env` is that report, and `dg-agent run` is the place a
bad value can fail *closed* without contradicting the argument for failing
open: the fail-open case is a supervisor sharing a tray on the path of every
call, and this is a launcher composing one child, once.

## One package, two entry points

Not two distributions. `dg-agent expire` stages parks into the task tray, so it
needs `task_pending` and the tray lock; the parsers in `dgraph/env.py` are
shared by construction, which is the one bug this whole split is about arrived
at from the other direction; and two packages that must agree on the meaning of
`DG_DECIDE=evidence` is a coupling nobody can see in a lockfile.

## What stayed with `dg`

The two graphs, both trays, `apply`, `gate`, both host adapters, the heartbeat,
and the `--agent NAME` scoping on `pending` / `apply` / `clear`. Those flags
scope a *tray*, and the tray is graph machinery that happens to be shared: the
name in them is a label, not a lease.

**`dg agent` is gone outright — not aliased, and not left as a stub.** A
forwarding shim would let every generated `launch.sh` in the wild keep working,
which sounds kind and means nobody migrates; a stub that named its replacement
would keep a whole subcommand alive in `dg --help` to say one sentence. So the
split is a split: `dg --help` names no agent command, `dg-agent --help` names no
graph command, and a stale launcher fails the way any typo'd command fails.
Everything a person could type it into — the slash command's allowlist, the
generated launcher, both prompt templates, the guides — says `dg-agent`.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import time as _time
import sys
from dataclasses import asdict, replace
from datetime import date as _date

import typer
from rich.markup import escape
from rich.table import Table

from dgraph import agents, cli, cross, env, fanout, limits, pending, project
from dgraph import task_pending  # noqa: F401  (kept beside the tray it writes)
from dgraph.tasks import TaskGraph

NAMES = "Names for a shared tray"
ENVIRONMENT = "The environment an agent runs under"
FANOUT = "Setting up a fan-out, and taking it apart again"

#: The whole help screen in one place, exactly as `dg`'s `LAYOUT` is: the
#: panels, their order, and the order of the commands inside each. Read down
#: it and you have the life of one agent — it is given a name, told what it
#: may do, run under that, and handed back what it was holding.
LAYOUT = (
    (NAMES, ("claim", "list", "release", "prune")),
    (ENVIRONMENT, ("env", "run", "broker", "consent")),
    (FANOUT, ("presets", "setup", "expire")),
)

app = typer.Typer(
    cls=cli._ordered(LAYOUT),
    add_completion=False,
    help="Launch agents against a development graph: hand out names, compose "
         "the environment that says what each may do, and hand back what one "
         "was holding when it stopped. `dg` reads what this sets.",
)


@app.callback()
def _root(
    project_dir: str = typer.Option(
        None, "--project", "-C", metavar="PATH",
        help="Project directory. Defaults to $DG_PROJECT, else the nearest "
             "ancestor holding decisions.json, else the cwd.",
    ),
) -> None:
    """The same session-level refusal `dg` makes, for the same reason.

    A launcher with `$DG_AGENT` exported is the failure `render_launch` has
    always warned about in a comment, and `unowned` is the one name staging
    reserves. Refusing the session here rather than at the op keeps the two
    binaries' answer to "who are you" identical — and this is the binary that
    can actually fix it, since it is the one composing the environment.

    No heartbeat is stamped. `dg`'s root callback touches the lease because
    every graph call passes through it and liveness is what those calls
    evidence; a launcher calling `dg-agent list` is not the agent, and
    stamping it alive here would make a dead agent look busy every time
    somebody checked on it.
    """
    project.use(project_dir)
    if pending.owner() == pending.UNOWNED:
        cli.con.print(f"[red]✗ `{pending.UNOWNED}` is reserved — it is how this "
                      f"tool names ops nobody signed, so a writer cannot also "
                      f"be called that[/]\n"
                      f"[dim]set ${pending.AGENT_ENV} to something else, or "
                      f"unset it to work as the supervisor[/]")
        raise typer.Exit(2)


@app.command("claim")
def agent_claim(
    budget: str = typer.Option(
        None, "--budget", "-b",
        help="how long this agent may run: `1800`, `30m`, `2h`, or `infinite`"),
) -> None:
    """Take a free name and hold it. Prints the name, and nothing else.

    **Bare stdout on purpose**, because the only sensible caller is a
    substitution:

        DG_AGENT=$(dg-agent claim) claude -p "..."

    A launcher's job, not an agent's: shell state does not survive between a
    coding agent's tool calls, so one that claimed a name for itself could not
    hold on to it.

    `--budget` records how long the agent may run, on the lease, so a
    supervisor reading `dg-agent list` sees the same number the agent was
    given. **Nothing here stops it at that point** — this command hands out a
    name and returns, and whatever spawns the agent is somewhere else.
    `dg-agent run` is that somewhere else: it claims and spawns in one act, so
    it is the child's parent and the budget is real. Reach for `claim` when you
    are spawning the agent yourself and want only the name.

    Either way what the budget buys is the *hand-back*: `dg-agent expire` parks
    whatever an out-of-time agent is holding, so work stops looking like it is
    being done by something that stopped. See `dg-agent expire` and
    `agentic/README.md`.
    """
    try:
        seconds = limits.span(budget)
    except limits.BadSpan as exc:
        # Raised, where a bad `$DG_WRITE` is merely ignored. A misread budget
        # is not a wider rule, it is a different number, and this is the one
        # moment the launcher can still fix it.
        cli.con.print(f"[red]✗ {cli._x(exc)}[/]")
        raise typer.Exit(1)
    try:
        name = agents.claim(budget=seconds)
    except agents.Exhausted as exc:
        # An error, never a fallback. Handing back a name somebody holds — or
        # inventing a numbered one outside the lists — is the silent conflation
        # the whole ownership stamp exists to prevent, and the numbered one is
        # worse for looking deliberate.
        cli.con.print(f"[red]✗ no name to claim — {cli._x(exc)}[/]")
        if exc.releasable:
            # `releasable` counts leases with nothing staged, which is what
            # `prune` used to free. It now keeps back the ones still holding
            # DOING work, so the count is an upper bound and saying "frees
            # those now" would promise names that stay held. The subtraction
            # happens here because `Exhausted` is raised in a module that
            # cannot read the task store.
            free = exc.releasable - len(_still_working())
            if free > 0:
                cli.con.print(f"[dim]{free} have nothing staged under them: "
                          f"`dg-agent prune` frees those now[/]")
            else:
                cli.con.print("[dim]every idle name is still holding work — "
                          "`dg-agent expire`, or park what is DOING, then "
                          "`dg-agent prune`[/]")
        else:
            cli.con.print("[dim]every name has ops in a tray — `dg apply` or "
                      "`dg clear` first, then `dg-agent prune`[/]")
        raise typer.Exit(1)
    # `print`, not `cli.con.print`: rich wraps at the terminal width and would put a
    # newline inside a long name, and this string is going into a variable.
    print(name)


@app.command("list")
def agent_list() -> None:
    """Every name held, when it was claimed, and what it still has staged."""
    from dgraph import broker as _broker
    proj = project.find()
    leases = agents.load()
    staged = agents.in_trays(proj)
    if not leases and not staged:
        cli.con.print("[dim]no names claimed[/]")
        return
    # Only where a broker is running, because the column can only ever be empty
    # otherwise and an always-blank column reads as "nobody is waiting" rather
    # than "nothing could tell you". `P-F7`.
    blocked = _broker.waiting(proj.root)
    columns = ["Name", "Since", "Staged", "Holding", "Budget", "Seen"]
    if _broker.listening(proj.root):
        columns.append("Waiting")
    t = Table(header_style="bold", title="Names held")
    for c in columns:
        t.add_column(c)
    quiet_names = {r["agent"] for r in agents.silent()}
    # Names with ops but no lease are listed too, and marked. That is what a
    # hand-set `$DG_AGENT` looks like, and a roster of leases alone would say
    # the tray was unowned while somebody's drafts sat in it.
    for name in sorted(set(leases) | set(staged)):
        rec = leases.get(name, {})
        since = rec.get("since", "[dim]not claimed here[/]")
        left = agents.remaining(rec)
        if rec.get("budget") is None:
            spend = "[dim]—[/]"
        elif left is not None and left < 0:
            # The one cell worth a colour: an agent past its budget and still
            # holding work is the state `dg-agent expire` exists for, and it is
            # invisible in every other reading of a run.
            spend = f"[red]SPENT +{limits.approx_span(-left)}[/]"
        else:
            spend = (f"{limits.show_span(rec['budget'])}"
                     f" ({limits.approx_span(left)} left)")
        quiet = agents.quiet_for(rec)
        if quiet is None:
            seen = "[dim]—[/]"
        elif name in quiet_names:
            # Yellow, never red: this is the one column that can be wrong. An
            # agent in a long build is silent in exactly the same way a dead
            # one is, and the colour should not claim otherwise.
            seen = f"[yellow]silent {limits.approx_span(quiet)}[/]"
        else:
            seen = limits.approx_span(quiet)
        row = [cli._x(name), since, str(staged.get(name, 0)),
               ", ".join(rec.get("holding") or ()) or "[dim]—[/]", spend, seen]
        if "Waiting" in columns:
            # `Seen` cannot say this and has not been able to since the
            # heartbeat landed: an agent blocked on a person stamps its lease
            # exactly as a working one does, so `3s` means *alive* and nothing
            # more. This is the cell that says which.
            w = blocked.get(name)
            # `queued` and blocked-on-a-person are both blocked and read
            # differently: the first is not answerable yet, so a supervisor
            # counting questions must not count it as one. `G-F4`.
            row.append("[dim]—[/]" if not w else
                       f"[yellow]{w.get('kind', 'consent')} "
                       f"{limits.approx_span(max(0, int(_time.time() - w.get('since', 0))))}"
                       f"{' [dim](queued)[/]' if w.get('queued') else ''}"
                       f"[/]")
        t.add_row(*row)
    cli.con.print(t)
    if any(agents.over_budget()):
        cli.con.print("[dim]`dg-agent expire` hands back what the spent ones "
                  "hold[/]")
    if quiet_names:
        # No command is offered, deliberately. An elapsed budget is a fact and
        # `expire` acts on it; silence is a suspicion, and the only safe next
        # step is a person deciding whether that agent is thinking or gone.
        cli.con.print(f"[dim]silent = no `dg` call and no file write for "
                  f"{limits.approx_span(limits.silent_after())}, while holding "
                  f"work. A long build looks the same as a dead agent — check "
                  f"before parking anything. ${limits.SILENT_ENV} sets the "
                  f"window[/]")
    if blocked:
        # No command offered, like `silent` above and for the opposite reason:
        # there is nothing wrong. An agent waiting on consent is an agent doing
        # what the design asks, and what unblocks it is the person at
        # `dg-agent broker` answering.
        queued = sum(1 for w in blocked.values() if w.get("queued"))
        cli.con.print(f"[dim]waiting = blocked on a consent decision, not "
                  f"stalled — `dg-agent broker` is where it is answered. Its "
                  f"heartbeat keeps stamping, so `Seen` cannot tell you "
                  f"this[/]"
                  + (f"\n[dim]{queued} of them {'is' if queued == 1 else 'are'} "
                     f"[/][yellow]queued[/]"
                     f"[dim] behind another agent's question — the broker asks "
                     f"one at a time, so answering the one in front releases "
                     f"them[/]" if queued else ""))
    cli.con.print(f"[dim]{len(agents.sequence()) - len(set(leases) | set(staged))} "
              f"of {len(agents.sequence())} names free[/]")


@app.command("release")
def agent_release(
    name: str = typer.Argument(..., help="the name to stop holding"),
    force: bool = typer.Option(
        False, "--force",
        help="release even while it holds DOING work, stranding those tasks"),
) -> None:
    """Stop holding one name, so it can be claimed again.

    Says nothing about the tray: releasing a name whose ops are still staged is
    legitimate — the launcher is done, the review is not — and `dg-agent claim`
    still will not hand it out while those ops are there.

    It does say something about held **work**, and refuses. The same stranding
    `dg-agent prune` avoids arrives through this door one name at a time; see
    that command for what it costs. `--force` overrides.
    """
    if not force:
        held = _still_working().get(name)
        if held:
            cli.con.print(f"[red]✗ {cli._x(name)} still holds {', '.join(held)}[/]\n"
                      f"[dim]releasing it now strands that work — DOING, with "
                      f"no holder recorded and `dg task start` refusing it. "
                      f"`dg-agent expire`, or `dg task park <id> --why …`, "
                      f"then release. `--force` releases anyway[/]")
            raise typer.Exit(1)
    if not agents.release(name):
        cli.con.print(f"[red]nothing released — {cli._x(name)} is not held[/]\n"
                  f"[dim]`dg-agent list` shows what is[/]")
        raise typer.Exit(1)
    cli.con.print(f"[green]released[/] {cli._x(name)}")


@app.command("prune")
def agent_prune(
    force: bool = typer.Option(
        False, "--force",
        help="release names still holding DOING work, stranding those tasks"),
) -> None:
    """Release every name with no ops left in either tray.

    Deliberate, and never run on your behalf. A name with nothing staged can
    still belong to an agent that has not staged *yet*, so this is safe when a
    person knows the round is over and unsafe as a rule a timer applies.

    **A name still holding DOING work is kept back**, because "nothing staged"
    and "finished" are not the same thing and the gap between them is the first
    minute of every agent's life. Dropping such a lease strands the task: it
    stays `DOING`, its holder stops being recorded anywhere, and `dg task start`
    refuses it as taken — so no other agent can pick it up and only a
    hand-written park recovers it. Nothing detects that afterwards either,
    because a task `DOING` with no holder is exactly what an ordinary solo
    `dg task start` leaves behind.

    `--force` releases them anyway. It is here because a rule that cannot be
    overridden is a rule people route around, and because the stranding is
    sometimes what you want — a run whose tasks you are about to drop.
    """
    # Passed as a callable, not as a set: `agents.prune` evaluates it inside
    # the lease lock, so an agent claiming work between the judgement and the
    # delete it authorises cannot be stranded by it. See that function; audit
    # `M-F6`. `holders` below is what it actually saw, for the message.
    holders: dict[str, list[str]] = {}

    def keep():
        holders.update({} if force else _still_working())
        return holders

    gone = agents.prune(keep=keep)
    kept = sorted(holders)
    if not gone and not kept:
        cli.con.print("[dim]nothing to prune — every name held has ops staged[/]")
        return
    if gone:
        cli.con.print(f"[green]released[/] {len(gone)}: {cli._x(', '.join(sorted(gone)))}")
    for line in _stranding(kept, holders):
        cli.con.print(line)
    if kept:
        cli.con.print("[dim]`dg-agent expire` parks what an out-of-time agent "
                  "holds, or `dg task park <id> --why …` by hand — then prune "
                  "again. `--force` releases them and strands the work[/]")


@app.command("presets")
def agent_presets(
    as_json: bool = typer.Option(False, "--json", help="the same, machine form"),
) -> None:
    """The curated remits, and what each one sets.

    The same rows the wizard's cards carry and the same table
    `agentic/README.md` prints, rendered from `fanout.preset_rows` so the three
    cannot come to disagree about what a name means. A preset that had to be
    looked up in prose to be trusted would be a name hiding a policy, which is
    the failure it exists to prevent.

    What a preset does *not* touch is as much the point: the budget, which
    varies with the size of the work rather than with the remit, and the three
    answers no graph can supply.
    """
    proj = project.find()
    rows = fanout.preset_rows(proj)
    if as_json:
        print(json.dumps({
            "default": fanout.DEFAULT_PRESET,
            "constant": {**fanout.PRESET_CONSTANTS, "confine": "require"},
            "presets": [{"name": n, "row": r, "decide": d,
                         "exec_allow": progs.split(), "apply": ap}
                        for n, r, d, progs, ap in rows],
        }, ensure_ascii=False))
        return

    table = Table(box=None, pad_edge=False)
    for col in ("", "what it settles", "$DG_DECIDE", "$DG_APPLY",
                "may run unasked"):
        table.add_column(col)
    for name, row, decide, progs, ap in rows:
        table.add_row(
            f"[bold]{name}[/]" + ("  [dim](default)[/]"
                                  if name == fanout.DEFAULT_PRESET else ""),
            row, decide,
            ap + ("  [dim]you apply[/]" if ap == "never"
                  else "  [dim]applies its own[/]"),
            f"{len(progs.split())} programs"
            + ("  [dim]readers only[/]"
               if fanout.PRESETS[name].exec_scope == "readers"
               else "  [dim]+ build tools[/]"))
    cli.con.print(table)
    const = ", ".join(f"$DG_{k.upper()}={v}"
                      for k, v in fanout.PRESET_CONSTANTS.items())
    cli.con.print(f"\n[dim]The same in all three: {const}, and a confinement "
              f"floor where one is available. None of them sets a budget — "
              f"that follows the size of the work, not the remit.[/]")
    cli.con.print("[dim]`dg-agent setup --preset <name>`, or pick one in the "
              "wizard; any other flag still overrides one field.[/]")


@app.command("setup")
def agent_setup(
    preset: str = typer.Option(None, "--preset",
                               help="a curated remit — scout | contributor | "
                                    "maintainer. Fills the whole policy block; "
                                    "any other flag still overrides one field. "
                                    "`dg-agent presets` prints what each sets"),
    focus: str = typer.Option(None, "--focus",
                              help="comma-separated ids the fan-out is for; "
                                   "their chains are pasted into the prompt"),
    n: int = typer.Option(None, "--agents", "-n", help="how many to launch"),
    host: str = typer.Option(None, "--host", help="claude | opencode"),
    decide: str = typer.Option(None, "--decide", help="open | evidence | never"),
    apply_policy: str = typer.Option(
        None, "--apply",
        help="own | never — whether an agent writes the store itself, or only "
             "stages for a caller with no $DG_AGENT to apply"),
    write_scope: str = typer.Option(None, "--write", help="open | launch"),
    area: str = typer.Option(None, "--area-policy",
                             help="open | strict — whether a scout may file "
                                  "under an area nobody has used yet"),
    budget: str = typer.Option(None, "--budget",
                               help="`30m`, `1800`, or `infinite`"),
    terse: str = typer.Option(None, "--terse",
                              help="`on`, a character count, or `off` — how "
                                   "long a field may be before the "
                                   "development belongs in a file"),
    exec_allow: str = typer.Option(
        None, "--exec-allow", metavar="NAMES",
        help="program names an agent may run unasked, space-separated; "
             "replaces what the project's marker files proposed"),
    confine: str = typer.Option(
        None, "--confine", help="require | off — whether a confinement floor "
                                "is required below the tool layer"),
    floor: str = typer.Option(
        None, "--floor", help="host | bwrap — which backend provides it"),
    brief: str = typer.Option(None, "--brief",
                              help="one paragraph: what a good session produces"),
    read: list[str] = typer.Option(None, "--read", metavar="PATH:WHAT",
                                   help="a file the agents may read, and what "
                                        "it is; repeatable"),
    findings: str = typer.Option(None, "--findings",
                                 help="where an agent puts what it produces"),
    capture: bool = typer.Option(False, "--capture",
                                 help="record every `dg` call of the run"),
    out: str = typer.Option(None, "--out", help=f"output dir (default {fanout.OUT_DIR}/)"),
    force: bool = typer.Option(
        False, "--force",
        help="regenerate over artefacts you have edited by hand, losing those "
             "edits — refused without this"),
    plain: bool = typer.Option(False, "--plain",
                               help="ask a question at a time, never the "
                                    "full-screen form"),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="print both artefacts, write nothing"),
    as_json: bool = typer.Option(False, "--json",
                                 help="readiness and defaults, machine form"),
) -> None:
    """Set up a fan-out: check what is ready, then write the prompt and launcher.

    Three ways in, one result. **With no arguments and a terminal** it asks:
    a single-screen form where `textual` is installed, a question at a time
    otherwise — the second needs nothing the tool does not already have, so
    interactive setup always works and `--plain` picks it deliberately.
    **With flags** it is entirely non-interactive, which is how an agent inside
    Claude Code or opencode uses it, since neither can drive a full-screen app.
    **With `--json`** it reports readiness and the defaults it would use, so an
    agent can put the three questions the graph cannot answer to the person and
    then call back with flags.

    Everything else the template asks for is filled from the graph: the
    project, the chain behind each focus id pasted verbatim from
    `dg context --full`, which policies are in force and what each means, the
    write roots, the budget. Only three answers are yours — what the fan-out is
    for, what the agents may read, and where findings go.

    Writes `fanout/scout.md` and `fanout/launch.sh`. Neither is scratch: a
    filled prompt is the thing you want to read back and reuse, so it is not
    hidden under `.dgraph-*`.
    """
    proj = project.find()
    checks = fanout.readiness(proj)
    plan = fanout.defaults(proj)

    if as_json:
        print(json.dumps({
            # Barring checks only. The two advisory ones — nothing ready to
            # claim, and no broker listening — describe runs that are unusual
            # and legitimate, and an agent reading `ready: false` for either
            # would refuse to set up a fan-out somebody meant. `G-F7`.
            "ready": all(c.ok for c in checks if c.bars),
            "checks": [{"ok": c.ok, "label": c.label, "fix": c.fix,
                        "bars": c.bars} for c in checks],
            # Every field, from the plan itself. Listed by hand, this reported
            # eight of eleven — and the three it left out were the three an
            # agent calling back with flags could not have known about
            # (`P-F6`). `asdict` is what makes a new field visible here by
            # arriving, rather than by somebody remembering.
            "defaults": {k: v for k, v in asdict(plan).items()
                         if k not in ("brief", "reads", "capture")},
            # The curated remits, so an agent driving this with flags can
            # offer the person a word instead of six policy questions it would
            # have to explain first. Rendered from `fanout.preset_rows`, like
            # every other place a preset is shown.
            "presets": [{"name": name, "row": row, "decide": dec,
                         "exec_allow": progs.split(), "apply": ap}
                        for name, row, dec, progs, ap
                        in fanout.preset_rows(proj)],
            "default_preset": fanout.DEFAULT_PRESET,
            "asks": ["--brief", "--read PATH:WHAT", "--findings"],
        }, ensure_ascii=False))
        return

    given = {"preset": preset,
             "focus": focus, "agents": n, "host": host, "decide": decide,
             "apply": apply_policy,
             "write": write_scope, "area": area, "budget": budget,
             "terse": terse, "brief": brief, "read": read,
             "exec_allow": exec_allow, "confine": confine, "floor": floor,
             "findings": findings, "out": out}
    interactive = not any(v not in (None, (), []) for v in given.values())

    for c in checks:
        cli.con.print(f"[{'green' if c.ok else 'yellow'}]"
                  f"{'✓' if c.ok else '!'}[/] {cli._x(c.label)}"
                  + (f"  [dim]{c.fix}[/]" if not c.ok and c.fix else ""))
    if not all(c.ok for c in checks[:2]):
        cli.con.print("[red]✗ this project has no graph to fan out against[/]")
        raise typer.Exit(2)

    if interactive:
        plan = _setup_interactively(plan, proj, plain)
        if plan is None:
            cli.con.print("[dim]cancelled — nothing written[/]")
            raise typer.Exit(1)
    else:
        try:
            plan = _setup_from_flags(plan, given)
        except ValueError as exc:
            cli.con.print(f"[red]✗ {cli._x(exc)}[/]")
            raise typer.Exit(2) from None
        plan = replace(plan, capture=capture)

    # Before anything is written, for the reason `_setup_from_flags` refuses a
    # mistyped policy: a plan that cannot be launched confined is a plan whose
    # prompt will assert a floor to an agent that has none, and there is no
    # invocation later that could put it right.
    fault = fanout.plan_fault(plan)
    if fault:
        cli.con.print(f"[red]✗ {cli._x(fault)}[/]\n"
                      f"[dim]nothing was written[/]")
        raise typer.Exit(2)

    if dry_run:
        cli.con.print("[dim]— fanout/scout.md —[/]")
        print(fanout.render_scout(plan, proj))
        cli.con.print("[dim]— fanout/launch.sh —[/]")
        print(fanout.render_launch(plan, proj))
        cli.con.print("[dim]--dry-run: nothing written[/]")
        return
    # **Refused rather than overwritten.** Both artefacts a launcher is told to
    # edit — `scout.md`, and `env.json`'s allowlist, which `MARKERS` says is a
    # proposal to cut down — were regenerated in silence by a re-run, and
    # re-running is what this tool recommends when the graph has moved on. A
    # launcher who had removed `cargo` got it back and was told nothing.
    #
    # At the door, before anything is written, like every other refusal here:
    # a partial regeneration is worse than none, since the three files are read
    # against each other. Audit `G-F9`.
    changed = fanout.edited(proj, plan)
    if changed and not force:
        cli.con.print(f"[red]✗ nothing written[/] — you have edited "
                      f"{'these' if len(changed) > 1 else 'this'} since "
                      f"`dg-agent setup` generated "
                      f"{'them' if len(changed) > 1 else 'it'}:")
        for rel in changed:
            cli.con.print(f"    {cli._x(rel)}")
        cli.con.print("[dim]regenerating would lose those edits. Move them "
                      "aside and re-run, or `--force` and mean it. The run "
                      "you already have still launches: `./"
                      f"{plan.out}/launch.sh`[/]")
        raise typer.Exit(1)
    written = fanout.write(plan, proj)
    for p in written:
        cli.con.print(f"[green]wrote[/] {p.relative_to(proj.root)}")
    # **The broker comes before the launcher, and this line used to say
    # otherwise.** Naming `./launch.sh` as the next step is what taught people
    # to skip the step `agentic/QUICKSTART.md` calls the one people skip: with
    # nothing listening, `dg gate` answers `ask`, the host turns that into a
    # permission prompt, and a headless run has nobody to answer it. Said here
    # as well as in `readiness()` because this is the last line on the screen
    # and the checks scroll off. Audit `G-F7`.
    from dgraph import broker as _broker
    try:
        up = _broker.listening(proj.root)
    except Exception:
        up = False
    nxt = (f"Then `./{written[1].relative_to(proj.root)}`" if up else
           f"Then start a broker — `dg-agent broker`, or `--relay` to answer "
           f"from a session — and `./{written[1].relative_to(proj.root)}`")
    cli.con.print(f"[dim]read {written[0].relative_to(proj.root)} before launching "
              f"— the three answers only you can give are near the top. "
              f"{nxt}[/]")


def _setup_from_flags(plan: fanout.Plan, given: dict) -> fanout.Plan:
    """CLI flags to a `Plan`, refusing a value that is not one of the choices.

    Refused rather than defaulted: a launcher that typed `--decide evidenced`
    means to constrain its agents, and quietly running them unconstrained is
    the failure the flag exists to prevent. `$DG_WRITE` defaults a bad value
    because it is read on every judged write; this is read once, out loud.
    """
    #: Where a flag is not spelled the way its `Plan` field is. `--area-policy`
    #: rather than `--area` because `dg-agent setup --area corpus` would read as
    #: "fan out over the corpus area", which is a focus and not a policy.
    FLAG = {"area": "area-policy"}

    # First, so every other flag reads as an override of it rather than as
    # something the preset might undo. `--preset scout --decide open` is a
    # launcher saying "that remit, except for this", and the order is what
    # makes that sentence true.
    if given.get("preset") is not None:
        try:
            plan = fanout.apply_preset(plan, given["preset"])
        except ValueError as exc:
            raise ValueError(str(exc)) from None

    def one_of(name, value, allowed):
        if value is None:
            return getattr(plan, name)
        if value not in allowed:
            raise ValueError(f"--{FLAG.get(name, name)} must be one of "
                             f"{', '.join(sorted(allowed))}, not {value!r}")
        return value

    reads = []
    for item in (given["read"] or ()):
        path, _, what = item.partition(":")
        if not path:
            raise ValueError(f"--read wants PATH:WHAT, got {item!r}")
        reads.append((path, what.strip() or "(not described)"))

    budget = plan.budget
    if given["budget"] is not None:
        budget = limits.span(given["budget"])

    # Refused rather than defaulted, like `--decide`, and unlike the
    # environment variable this ends up in. `limits.terse_limit` fails open
    # because it is read on the path of every stage; a typo *here* is read
    # once, out loud, by somebody who meant to constrain their agents.
    terse = plan.terse
    if given["terse"] is not None:
        terse = given["terse"].strip().lower()
        if terse not in limits.TERSE_OFF and limits.terse_limit(terse) is None:
            raise ValueError(
                f"--terse wants `on`, `off`, or a character count, not "
                f"{given['terse']!r}")

    # Replaces the proposal rather than adding to it. A launcher spelling the
    # list out has read what the markers found and decided otherwise, and a
    # flag that quietly kept the rest would make `--exec-allow dg` mean
    # something wider than it says.
    exec_names = plan.exec_allow
    if given.get("exec_allow") is not None:
        exec_names = list(dict.fromkeys(given["exec_allow"].split()))
        bad = [n for n in exec_names if env.NOT_A_NAME & set(n)]
        if bad:
            raise ValueError(
                f"--exec-allow takes program names, not command lines — "
                f"{', '.join(repr(b) for b in bad)} carries shell syntax and "
                f"would never match one")

    # Refused rather than defaulted, like every other word flag here. A floor
    # is the one value where a silent fallback means an unconfined run that
    # says it is confined, which is the whole of `P-F2`.
    from dgraph import confine as _confine

    return replace(
        plan,
        confine=one_of("confine", given.get("confine"), set(_confine.CONFINE_MODES)),
        floor=one_of("floor", given.get("floor"), set(_confine.BACKENDS)),
        focus=[s.strip() for s in given["focus"].split(",") if s.strip()]
              if given["focus"] else plan.focus,
        agents=given["agents"] or plan.agents,
        host=one_of("host", given["host"], set(fanout.HOSTS)),
        decide=one_of("decide", given["decide"], set(cross.POLICIES)),
        apply=one_of("apply", given["apply"], set(env.APPLY_POLICIES)),
        write=one_of("write", given["write"], set(limits.WRITE_POLICIES)),
        area=one_of("area", given["area"], set(env.AREA_POLICIES)),
        budget=budget,
        terse=terse,
        exec_allow=exec_names,
        brief=given["brief"] or plan.brief,
        reads=reads or plan.reads,
        findings=given["findings"] or plan.findings,
        out=given["out"] or plan.out,
    )


#: What a missing `textual` says. A constant so a test can render it: `[tui]`
#: is rich markup unless escaped, and the first version of this message came
#: out as `pip install 'dear-guide'` — an install hint that silently dropped
#: the extra it was telling you to install.
TUI_HINT = ("[dim]a single-screen form is available with `pip install "
            + escape("'dear-guide[tui]'") + "`[/]")


def _has_tui() -> bool:
    try:
        import textual  # noqa: F401
    except ImportError:
        return False
    return True


def _setup_interactively(plan: fanout.Plan, proj,
                         plain: bool) -> fanout.Plan | None:
    """Ask, however this terminal allows. `wizard.collect` decides between the
    full-screen form and the question-at-a-time one.

    A missing `textual` costs nothing here: the plain collector asks the same
    questions and writes the same files, so the extra is mentioned once, as an
    upgrade, and never as a refusal. It used to be a refusal, which made
    interactive setup unavailable to anyone who had not installed an optional
    dependency — for a command whose whole job is being the easy way in.
    """
    from dgraph import wizard
    try:
        got = wizard.collect(plan, proj, interactive=cli._interactive(),
                             prefer_tui=not plain)
    except wizard.NoTerminal as exc:
        cli.con.print(f"[yellow]![/] {cli._x(exc)}")
        raise typer.Exit(2) from None
    if not plain and not _has_tui():
        cli.con.print(TUI_HINT)
    return got


def _still_working() -> dict[str, list[str]]:
    """`{agent: [tids]}` for leases holding work that is genuinely still DOING.

    The guard behind `prune` and `release`, and the reason it is here rather
    than in `agents.py`: answering it means reading the task store, which that
    module deliberately knows nothing about.

    Soft on purpose. A project with no task store cannot strand a task, and
    `dg-agent prune` has always worked without one — so a missing store is an
    empty answer rather than the exit `_tg` would raise. The lease's `holding`
    list is filtered against real statuses because `drop_hold` clears it only
    when work leaves DOING through this CLI; a task parked by somebody else
    leaves an entry behind, and keeping a name alive over a stale one would
    make `prune` useless exactly when a run is over.
    """
    proj = project.find()
    if not proj.has_tasks:
        return {}
    try:
        tg = cli._teff(TaskGraph.load(proj.tasks))
    except (OSError, ValueError, typer.Exit):
        # A store that will not load, or a tray that no longer applies. Both
        # are real problems with their own messages elsewhere; neither is a
        # reason for this guard to refuse, and treating them as "nothing is
        # held" only restores the behaviour prune had before the guard.
        return {}
    out: dict[str, list[str]] = {}
    for name, rec in agents.load().items():
        live = [t for t in (rec.get("holding") or ())
                if t in tg.tasks and tg.tasks[t].status == "DOING"]
        if live:
            out[name] = live
    return out


def _stranding(names: list[str], holders: dict[str, list[str]]) -> list[str]:
    """The lines explaining what was kept back, and how to release it anyway."""
    return [f"[yellow]kept[/] {cli._x(n)} — still holds "
            f"{', '.join(holders[n])}" for n in names]


@app.command("expire")
def agent_expire() -> None:
    """Hand back what an out-of-budget agent is holding.

    A task left `DOING` by an agent that stopped reads exactly like one being
    worked on. That is the failure the lease file exists to make visible, and
    `dg task park --why` is the verb that already fixes it — so an expired
    budget parks, naming the budget as what stopped the work. Parked work is
    still outstanding, so nothing downstream is released: the next agent can
    pick it up, which is the whole point.

    **This does not stop a process, and it is still the backstop.** `dg-agent
    run` parks what a child of *its own* dropped, when the information is
    freshest — but it cannot see itself being killed, the machine going down or
    the terminal closing, and an agent spawned some other way has no parent
    watching it at all. This is the half that makes the queue honest
    afterwards, and it is worth running even when you saw the agent die.

    Staged, like everything else — each park goes into the tray under the
    *agent's own name*, so `dg pending --agent <name>` shows it beside whatever
    that agent had already proposed and `dg apply --agent <name>` takes the
    batch. There is no `--apply` here on purpose: `dg apply` is the only door
    that writes, and a second one would be a second place the tray's ownership
    rules have to be right.
    """
    spent = agents.over_budget()
    if not spent:
        cli.con.print("[dim]nothing expired — every budget has time on it[/]")
        return
    tg = cli._teff(cli._tg())
    staged = 0
    for rec in spent:
        name, over = rec["agent"], limits.approx_span(rec["over"])
        held = [t for t in rec["holding"]
                if t in tg.tasks and tg.tasks[t].status == "DOING"]
        idle = [t for t in rec["holding"] if t not in held]
        if not held:
            # Said, not skipped silently. An agent that spent its budget
            # holding nothing is the "died before it started" case, and it is
            # the one a roster of parked tasks would never show.
            cli.con.print(f"[yellow]{cli._x(name)}[/] is {over} over budget and holds "
                      f"nothing" + (f" (already left {', '.join(idle)})"
                                    if idle else ""))
            continue
        why = (f"budget spent: {name} was given "
               f"{limits.show_span(rec['budget'])} and is {over} past it")
        # One write per agent, not one per task. A hand-back is a group: a tray
        # read between two of these would show half of it, and `_tstage_all`
        # exists for exactly that. It cannot be one write across *all* agents,
        # because `as_owner` stamps a whole call and each batch carries its own
        # agent's name.
        with pending.as_owner(name):
            cli._tstage_all([{"op": "set_status", "task": tid, "status": "PARKED",
                          "why": why, "date": _date.today().isoformat()}
                         for tid in held])
        staged += len(held)
        cli.con.print(f"[green]staged[/] park of {', '.join(held)} "
                  f"[dim]as {cli._x(name)}, {over} over budget[/]")
    if staged:
        cli.con.print(f"[dim]{staged} park(s) staged — `dg pending` to read them, "
                  f"`dg apply` to take them[/]")




# ---- the environment -----------------------------------------------------
#
# The half of the split that is new. Everything above moved; everything below
# is what the move was for — a place where the seven variables an agent's remit
# is written in can be seen, checked, and handed to a child.


def _effect(r: env.Reading, proj: project.Project) -> str:
    """The `read as` column: what this value actually does, in a person's words.

    A value is not a behaviour. `launch` is a word, and what a launcher needs to
    see is the two directories it resolved to; `evidence` is a word, and what it
    means is which closes are about to be refused. The whole defect this command
    exists for is that `open` looks identical whether it was chosen or fallen
    into, so the column that says what is in force has to say it in the
    vocabulary of the refusal, not of the variable.
    """
    name, value = r.var.name, r.value
    if name == env.AGENT_ENV:
        return "agent — refusals apply" if value else "supervisor — no refusal applies"
    if name == env.POLICY_ENV:
        return {"open": "may close any question",
                "evidence": "closes only what finished evidence backs",
                "never": "every answer goes back to a person"}[value]
    if name == env.APPLY_ENV:
        return {"own": "writes its own staged ops itself",
                "never": "stages only — a caller with no $DG_AGENT applies"}[value]
    if name == env.WRITE_ENV:
        if value == "open":
            return "anywhere"
        return ", ".join(limits.writable_roots(proj.root))
    if name == env.CONFINE_ENV:
        if value == "off":
            return "no floor — the gate and the broker are all that judge"
        from dgraph import confine
        ok, why = confine.available(env.reading(env.FLOOR_ENV).value)
        return "a floor is required" if ok else f"REQUIRED but {why}"
    if name == env.FLOOR_ENV:
        from dgraph import confine
        ok, why = confine.available(value)
        return f"{value} — usable here" if ok else f"{value} — {why}"
    if name == env.EXEC_ENV:
        # The vocabulary of the refusal, like every branch here: what a reader
        # needs is how many commands are about to escalate, not the tuple.
        n = len(value or ())
        if not n:
            return "every command asks"
        return f"{n} program{'' if n == 1 else 's'} run unasked; the rest ask"
    if name == env.AREA_ENV:
        return ("any area; a new one is checked against the ones in use"
                if value == "open" else "only areas already in use")
    if name == env.BUDGET_ENV:
        return _budget_effect(r)
    if name == env.TERSE_ENV:
        return ("no limit on a field"
                if value is None else f"a field over {value} chars is refused")
    if name == env.SILENT_ENV:
        return ("default" if not r.set
                else f"quiet for {env.show_span(value)} is reported")
    return r.note or str(value)


def _budget_effect(r: env.Reading) -> str:
    """What is left, read off the **lease** rather than off the variable.

    `agents.claim` records the budget on the lease so that a supervisor reading
    `dg-agent list` sees the same number the agent was given. That makes the
    lease the authority and `$DG_BUDGET` a copy — so a disagreement between
    them is itself a finding, and this is the one place both are to hand.

    Not a `--check` failure, deliberately: the variable parsed, and what the
    agent is actually running against is the lease. Said out loud, and left to
    a person.
    """
    name = pending.owner()
    rec = agents.load().get(name or "", {})
    left = agents.remaining(rec) if rec else None
    if left is None:
        return "infinite" if r.value is None else env.show_span(r.value)
    lease = rec.get("budget")
    said = f"{env.approx_span(max(left, 0))} left on this lease"
    if r.set and r.value != lease:
        said += (f" — but the lease says {env.show_span(lease)}, not "
                 f"{env.show_span(r.value)}")
    return said


def _plan_conflicts(spec: dict, readings: list[env.Reading]) -> list[str]:
    """Where the ambient environment contradicts the plan a prompt was made from.

    The failure this catches is drift between two artefacts generated from one
    answer. `fanout/scout.md` asserts each policy to the agent in the second
    person — "You are running under `$DG_DECIDE=evidence`" — and the launcher
    sets it separately; edit one afterwards and the prompt goes on asserting a
    policy nobody is enforcing, to an agent with no way to check. One file feeds
    both, and this asserts they still agree.

    Read in the *launcher's* shell, where none of these is normally set — an
    unset variable is therefore silent, and what is reported is a launcher that
    exported something the plan contradicts.

    **Compared on the parsed value, never on the two strings.** `plan_env`
    renders a budget through `show_span`, so a plan holding `1800` reaches here
    as `30m` — and `agentic/README.md` lists `1800` and `30m` side by side as
    spellings of one remit. A string compare called those a conflict, and
    `launch.sh` runs this under `set -euo pipefail` before the first agent, so
    a launcher that had followed the documentation had its whole fan-out
    refused over two spellings of the same number. `$DG_TERSE` had it too:
    `400` against `on`, both `TERSE_DEFAULT`.

    `Var.parse` is what normalises them, which is the field's first real use —
    it is the table's answer to "what does this string mean", and asking it
    here means the report and the enforcement cannot disagree about when two
    remits are the same one.
    """
    want = fanout.plan_env(spec)
    out = []
    for r in readings:
        if not r.set or r.var.name not in want:
            continue
        if r.value != r.var.parse(want[r.var.name]):
            out.append(f"${r.var.name} is {r.raw.strip()} in this shell and "
                       f"{want[r.var.name]} in the plan the prompt was "
                       f"rendered from")
    return out


@app.command("env")
def agent_env(
    check: bool = typer.Option(
        False, "--check",
        help="exit non-zero if a variable is set and not understood"),
    plan_path: str = typer.Option(
        None, "--plan", metavar="PATH",
        help=f"a {fanout.ENV_NAME} written by `dg-agent setup`: validate its "
             f"values, and report where this shell contradicts it"),
    export: bool = typer.Option(
        False, "--export",
        help="print assignments for `eval`, for a host that cannot take a "
             "wrapper process"),
    as_json: bool = typer.Option(False, "--json", help="the same, machine form"),
) -> None:
    """What every `$DG_*` says, what it means, and where one was mistyped.

    **Three things `env | grep DG_` cannot do.** It cannot name a fallback as a
    fallback — and that is the whole defect, because `$DG_DECIDE=nevr` runs as
    `open`, the widest policy, and looks exactly like a policy somebody chose.
    It cannot show the budget against the *lease*, which is the number the agent
    is actually running against. And it cannot resolve `$DG_PROJECT` to the
    graph it found, which is how a stale environment file — pointing at a root
    the graph has since moved out of — ran a whole fan-out against no store at
    all while looking perfectly correct.

    `--check` is the promise three docstrings in this tool have made for as long
    as they have existed: those variables fail open *because* something reports
    the typo where it is set. Only **set and not understood** is a finding.
    Unset is a legitimate choice and the documented default for all of them, so
    flagging it would flag every project that has never heard of this.

    A lease that disagrees with `$DG_BUDGET` is shown but is not a `--check`
    failure: the variable parsed, and the lease is what the hand-back reads.
    """
    proj = project.find()
    readings = env.readings()
    spec = None
    if plan_path is not None:
        try:
            spec = fanout.read_env_plan(plan_path)
        except (OSError, ValueError) as exc:
            cli.con.print(f"[red]✗ {cli._x(exc)}[/]")
            raise typer.Exit(2) from None

    if export:
        # `$DG_AGENT` is deliberately absent, and this is the one place the
        # rule can be *enforced* rather than commented: an exported name makes
        # the launcher an agent, and its own policy then refuses it. A name is
        # per command — `dg-agent run` puts it in one child's environment, or
        # `DG_AGENT=$(dg-agent claim) …` does it by hand.
        want = (fanout.plan_env(spec) if spec is not None
                else {r.var.name: r.raw.strip() for r in readings
                      if r.set and r.var.settable
                      and r.var.name != env.AGENT_ENV})
        for name, value in want.items():
            print(f"export {name}={shlex.quote(value)}")
        print(f"# {env.AGENT_ENV} is deliberately not exported: it is per "
              f"command. `dg-agent run --` sets it for one child.")
        return

    faults = [r for r in readings if not r.ok]
    conflicts = _plan_conflicts(spec, readings) if spec is not None else []
    # A third kind of finding, and the one no reading can express: every value
    # here can be perfectly legible and the floor still not exist. `require`
    # with no usable backend is a run that reports itself confined and is not.
    from dgraph import confine as _confine
    unfloored = _confine.preflight(**({} if spec is None else {
        "mode_": spec.get("confine"), "backend_": spec.get("floor")}))
    if unfloored:
        conflicts = [*conflicts, unfloored]

    if as_json:
        print(json.dumps({
            "ok": not faults and not conflicts,
            "project": str(proj.root),
            "variables": [
                {"name": r.var.name, "what": r.var.what, "set": r.raw,
                 "effective": r.effective, "reads_as": _effect(r, proj),
                 "ok": r.ok, "fails_open": r.var.fails_open,
                 "complaint": r.complaint or None}
                for r in readings],
            "plan": spec,
            "conflicts": conflicts,
        }, ensure_ascii=False))
        if check and (faults or conflicts):
            raise typer.Exit(1)
        return

    t = Table(header_style="bold", box=None, pad_edge=False)
    for c, just in (("variable", "left"), ("set to", "left"),
                    ("effective", "left"), ("read as", "left")):
        t.add_column(c, justify=just)
    for r in readings:
        mark = "" if r.ok else "[red]✗[/] "
        t.add_row(cli._x(r.var.name),
                  cli._x(r.raw.strip()) if r.set else "[dim]—[/]",
                  mark + cli._x(r.effective),
                  cli._x(_effect(r, proj)))
    cli.con.print(t)

    for r in faults:
        cli.con.print(f"[red]✗[/] {cli._x(r.complaint)}")
    for line in conflicts:
        cli.con.print(f"[red]✗[/] {cli._x(line)}\n"
                      f"[dim]the prompt asserts the plan's value to the agent "
                      f"in the second person, so the two must agree — "
                      f"re-run `dg-agent setup`, or unset it here[/]")
    if not faults and not conflicts:
        cli.con.print("[dim]every variable set here was understood[/]")
    if (faults or conflicts) and check:
        raise typer.Exit(1)


# ---- running one child ---------------------------------------------------


def _compose(spec: dict | None, given: dict) -> dict[str, str]:
    """The child's `$DG_*`, from a plan and the flags that override it.

    **Every value is refused rather than defaulted**, which is the opposite of
    what the same variables do when `dg` reads them, and the split is what makes
    that a structural distinction rather than a judgement about which code path
    you are on. Failing open is right on the path of every judged write, where a
    typo must not take the graph away from the supervisor sharing the tray.
    Here there is exactly one child, once, and the launcher is standing at the
    terminal: a value it cannot read is a value it can still fix.
    """
    out: dict[str, str] = {}
    if spec is not None:
        out.update(fanout.plan_env(spec))
    words = {"decide": (env.POLICY_ENV, env.POLICIES),
             "apply": (env.APPLY_ENV, env.APPLY_POLICIES),
             "write": (env.WRITE_ENV, env.WRITE_POLICIES),
             "area": (env.AREA_ENV, env.AREA_POLICIES)}
    for flag, (name, allowed) in words.items():
        value = given.get(flag)
        if value is None:
            continue
        if value not in allowed:
            raise ValueError(f"--{flag} must be one of {', '.join(allowed)}, "
                             f"not {value!r}")
        out[name] = value
    if given.get("terse") is not None:
        terse = given["terse"].strip().lower()
        if terse not in env.TERSE_OFF and env.terse_limit(terse) is None:
            raise ValueError(f"--terse wants `on`, `off`, or a character "
                             f"count, not {given['terse']!r}")
        out[env.TERSE_ENV] = terse
    if given.get("exec_allow") is not None:
        # Raises, like `--budget` and unlike the three word flags, and for the
        # same reason: a dropped name is not a wider rule, it is a different
        # list. `env.exec_allow` drops silently on the judged path because a
        # drop there narrows and the report catches it; here the launcher is
        # standing at the terminal and can fix the typo before anything spawns.
        names = given["exec_allow"].split()
        bad = [n for n in names if env.NOT_A_NAME & set(n)]
        if bad:
            raise ValueError(
                f"--exec-allow takes program names, not command lines — "
                f"{', '.join(repr(b) for b in bad)} carries shell syntax and "
                f"would never match one")
        # An explicit empty list is a *choice* — every command asks — so it
        # overrides a plan that listed some, where a `continue` would silently
        # keep the plan's.
        out[env.EXEC_ENV] = " ".join(dict.fromkeys(names))
        if not names:
            out.pop(env.EXEC_ENV)
    if given.get("budget") is not None:
        # Raises, where `$DG_WRITE` is merely ignored. A misread budget is not
        # a wider rule, it is a different number, and both directions are
        # wrong in a way nobody notices until an agent is parked hours early
        # or never.
        seconds = env.span(given["budget"])
        out[env.BUDGET_ENV] = env.show_span(seconds)
    return out


def _floor_prefix(composed: dict[str, str]) -> list[str]:
    """The argv a confining backend wraps the child in, or nothing.

    Only the host-neutral half. A backend whose floor is expressed as the
    runner's own settings has nothing to prepend, and its half is carried by
    the spawn line — which is where anything host-specific belongs, and is why
    this command can stay ignorant of what it is launching.
    """
    from dgraph import confine as _confine
    if _confine.mode(composed.get(_confine.CONFINE_ENV)) == "off":
        return []
    chosen = _confine.backend(composed.get(_confine.FLOOR_ENV))
    root = project.find().root
    return _confine.render(chosen, root).prefix


def _floor_unapplied(composed: dict[str, str], applied: bool) -> str | None:
    """Why this run would be unconfined despite declaring a floor — or `None`.

    **The preflight's blind spot, and the whole of `P-F2`.**
    `confine.preflight` asks whether a backend is *available*; this asks whether
    the carrier it renders is *applied*. Both questions have to be asked here,
    because a backend can be perfectly usable on this machine and still be
    carried by nobody: `host` renders settings, only a spawn line can carry
    them, and this command composes no spawn line.

    `applied` is `--floor-applied`, asserted by whoever built the spawn line —
    a *hand-off*, never a reading of the command. Sniffing the argv for
    `--settings` would be reading a command line this tool did not compose,
    which is the parsing `limits.recognise` refuses one layer down, and it would
    pass for a `--settings` naming a file whose contents nothing checked.

    The token is only about the half this command cannot apply. A backend whose
    floor is an argv prefix is applied right here, so asserting it changes
    nothing — an assertion that could excuse an applicable floor would be a way
    out of one.
    """
    from dgraph import confine as _confine
    try:
        if _confine.mode(composed.get(_confine.CONFINE_ENV)) == "off":
            return None
        chosen = _confine.backend(composed.get(_confine.FLOOR_ENV))
        if not _confine.configures_runner(chosen, project.find().root):
            return None
    except ValueError as exc:
        return str(exc)
    if applied:
        return None
    return (f"${_confine.CONFINE_ENV}=require and ${_confine.FLOOR_ENV}="
            f"{chosen}, whose floor is the runner's own settings — and this "
            f"command can only prepend argv, so nothing here would apply it. "
            f"Launch through the generated `{fanout.OUT_DIR}/launch.sh`, which "
            f"carries the settings and says so with `--floor-applied`; or "
            f"choose ${_confine.FLOOR_ENV}=bwrap, which wraps the command; or "
            f"say ${_confine.CONFINE_ENV}=off and mean it")


def _hold_back(name: str, why: str) -> list[str]:
    """Park whatever `name` still holds as DOING. The `expire` mechanism, run
    when the information is freshest.

    Staged under that agent's own name, exactly as `dg-agent expire` stages it,
    so `dg pending --agent <name>` shows the park beside whatever that agent had
    already proposed. There is no apply here for the reason there is none there:
    `dg apply` is the only door that writes.
    """
    held = _still_working().get(name) or []
    if not held:
        return []
    with pending.as_owner(name):
        cli._tstage_all([{"op": "set_status", "task": tid, "status": "PARKED",
                          "why": why, "date": _date.today().isoformat()}
                         for tid in held])
    return held


@app.command("consent")
def agent_consent(
    allow: bool = typer.Option(False, "--allow", help="let this one through"),
    deny: bool = typer.Option(False, "--deny", help="refuse it"),
    scope: bool = typer.Option(
        False, "--scope",
        help="allow, and stop asking for the rest of that scope — a directory "
             "for a write, the identical command for an exec. Only on the "
             "`scoped` rung, which is what publishes one"),
    why: str = typer.Option(None, "--why", help="the reason, recorded in the log"),
    as_json: bool = typer.Option(False, "--json", help="the request, machine form"),
) -> None:
    """Read the consent request a relaying broker is blocked on, and answer it.

    The supervisor's half of `dg-agent broker --relay`. With no flags it prints
    what is waiting and what answering it would mean; with `--allow`, `--deny`
    or `--scope` it writes the verdict and the blocked agent moves.

    **This is transport, not a decision you may hand off.** The verdict is
    recorded as answered by a person, because the rung that published it means
    a person — so running this from a session means *you* saying yes, with the
    session relaying it. A model answering here would put a lie in the one
    artefact a supervisor reads afterwards; that is a rung of its own, `auto`,
    and it is not this one.

    **Answer inside the caller's deadline.** Both host adapters give the gate
    100 seconds and it answers `deny` before that elapses, so a verdict written
    two minutes later is one the agent never receives — the log says
    `delivered: false` rather than pretending otherwise.
    """
    from dgraph import broker as _broker
    proj = project.find()
    req = _broker.pending_ask(proj.root)
    if req is None:
        if as_json:
            print(json.dumps({"pending": None}))
            return
        cli.con.print("[dim]nothing is waiting on a relayed answer[/]")
        # Blocked and *not* relayed is a different state and worth naming: a
        # terminal broker publishes a waiting agent too, and somebody reading
        # this would otherwise conclude nothing is stuck.
        others = _broker.waiting(proj.root)
        if others:
            cli.con.print(f"[yellow]![/] {len(others)} agent(s) are blocked on "
                      f"a broker that is not relaying — answer it where it "
                      f"is running, or restart it with `--relay`")
        raise typer.Exit(1)

    level = req.get("level")
    waited = int(_time.time() - float(req.get("since") or _time.time()))
    # Whether the agent is still listening. A verdict for one that has gone is
    # refused rather than taken — see below, and `broker.gone_for`.
    gone = _broker.gone_for(req)
    if as_json:
        print(json.dumps({"pending": req, "waited": waited,
                          "gone_for": gone}, ensure_ascii=False))
        return

    chosen = [f for f, on in (("--allow", allow), ("--deny", deny),
                              ("--scope", scope)) if on]
    if not chosen:
        holding = req.get("holding") or {}
        where = ", ".join(x for x in (holding.get("task"),
                                      holding.get("evidence_for")) if x)
        verb = "run" if req.get("kind") == "exec" else "write"
        cli.con.print(f"[bold]{cli._x(req.get('agent'))}[/] wants to {verb}:")
        cli.con.print(f"  {cli._x(req.get('target'))}")
        if where:
            cli.con.print(f"  [dim]({cli._x(where)})[/]")
        if (req.get("gate") or {}).get("reason"):
            cli.con.print(f"  [dim]{cli._x(req['gate']['reason'])}[/]")
        if gone is None:
            cli.con.print(f"  [dim]waiting {waited}s[/]")
        else:
            # Said where the wait used to be, because it is the same fact
            # turned over: this is not somebody waiting on you. `G-F2`.
            cli.con.print(f"  [red]gave up {int(gone)}s ago[/] [dim]— its "
                          f"deadline was {int(req['deadline'])}s and it has "
                          f"stopped listening[/]")
        # The one thing an answerer has to know before answering: which rung
        # published this, and therefore what the log will say produced the
        # verdict. On `user` that word is `person`, and it is only true if a
        # person gave it.
        if level == "auto":
            cli.con.print("  [yellow]rung `auto` — this answer is recorded as "
                      "`auto`, not as a person[/]")
        else:
            cli.con.print(f"  [dim]rung `{cli._x(level)}` — this answer is "
                      f"recorded as `person`, so it must be a person's[/]")
        cli.con.print("\n[dim]`dg-agent consent --allow` · `--deny` "
                  + (f"· `--scope` grants {cli._x(req.get('scope'))} "
                     if level == "scoped" and req.get("scope") else "")
                  + "· `--why \"…\"` records a reason[/]")
        return
    if len(chosen) > 1:
        cli.con.print(f"[red]✗ {', '.join(chosen)} — answer one way[/]")
        raise typer.Exit(2)
    # Refused, not warned. An `allow` for a caller that has gone is a verdict
    # nobody receives, and recording it would put in the consent log the very
    # ambiguity `delivered` exists to resolve — a supervisor reading it
    # afterwards cannot tell a permission that was enforced from one that was
    # merely typed. The id is stable over `(agent, kind, target)`, so a retry
    # re-attaches and the same question comes back answerable. `G-F2`.
    if gone is not None:
        cli.con.print(
            f"[red]✗ {cli._x(req.get('agent'))} gave up {int(gone)}s ago[/] "
            f"[dim]— its deadline was {int(req['deadline'])}s and it has "
            f"stopped listening. Nothing was recorded.[/]\n"
            f"[dim]It will ask again if it retries; the id is stable, so you "
            f"will get the same question.[/]")
        raise typer.Exit(1)
    if scope and level != "scoped":
        cli.con.print(f"[red]✗ --scope needs the `scoped` rung; this request "
                  f"came in under `{cli._x(level)}`[/]\n"
                  f"[dim]`--allow` lets this one through[/]")
        raise typer.Exit(2)

    grant = None
    if scope:
        grant = _broker.Grant(kind=req.get("kind"), value=req.get("scope"))
    reason = why or ("declined by the supervisor" if deny else
                     f"scope granted by the supervisor: {req.get('scope')}"
                     if scope else "allowed once by the supervisor")
    refused = _broker.send_answer(proj.root, req.get("id"), allow=not deny,
                                  why=reason, grant=grant)
    if refused:
        cli.con.print(f"[red]✗ {cli._x(refused)}[/]")
        raise typer.Exit(1)
    cli.con.print(f"[green]{'denied' if deny else 'allowed'}[/] "
              f"{cli._x(req.get('target'))}"
              + (f" [dim]· scope {cli._x(req.get('scope'))}[/]" if scope else ""))


@app.command("broker")
def agent_broker(
    write: str = typer.Option(None, "--write-rung", help="off | auto | scoped | user"),
    exec_: str = typer.Option(None, "--exec-rung", help="off | auto | scoped | user"),
    relay: bool = typer.Option(
        False, "--relay",
        help="publish each request for `dg-agent consent` to answer, instead "
             "of prompting on this terminal — for a supervisor who is a "
             "Claude Code or opencode session rather than a tty"),
    wait: float = typer.Option(
        None, "--relay-wait", metavar="SECONDS",
        help="how long a relayed request waits before it is denied; the "
             "default sits just above the deadline both host adapters pass"),
) -> None:
    """Answer the consent requests a fan-out's agents block on.

    `dg gate` is a pure function, so where it cannot allow something it says
    `ask` — and in a headless run there is nobody to ask, which turns consent
    into a refusal nobody chose. This is the process standing there.

    Run it in its own terminal, beside the fan-out. It listens on a socket in
    the project, answers one request at a time because a person answers one at
    a time, and exits on Ctrl-C. **Nothing needs it**: with no broker listening
    the gate returns the verdict it always did, so a project that never starts
    one behaves exactly as it did before.

    The two rungs are read *here* rather than by the gate, and that placement
    is the point: the gate runs inside the agent's own process, so a rung read
    there is one the agent could widen.

    **`auto` is refused unless something is attached to decide with.** It is a
    rung of the model — it decides here instead of at a person — and with no
    policy behind it it would answer `deny` to everything while this printed
    `auto` and published nobody as waiting. So `unattachable` refuses it at the
    door. `--relay` *is* such a policy, so the two together are accepted: the
    question goes out for `dg-agent consent` exactly as on the other rungs, and
    the difference is the record — a verdict given on `auto` is logged `auto`,
    not `person`, however human the hand that gave it. Choose `scoped` if what
    you want is your own answer recorded as yours.
    """

    from dgraph import broker as _broker
    try:
        proj = project.find()
    except Exception as exc:
        cli.con.print(f"[red]✗ no graph here — {cli._x(exc)}[/]")
        raise typer.Exit(2) from None
    given = {"DG_CONSENT_WRITE": write, "DG_CONSENT_EXEC": exec_}
    for name, value in given.items():
        if value is not None:
            os.environ[name] = value
    try:
        rungs = {k: _broker.rung(k) for k in _broker.LADDERS}
    except ValueError as exc:
        cli.con.print(f"[red]✗ {cli._x(exc)}[/]\n[dim]nothing is listening; "
                      f"fix the rung and start again[/]")
        raise typer.Exit(2) from None

    # Both refusals sit before anything is bound, so nothing has started that
    # would have to be torn down — and so the launcher, which is about to run
    # `launch.sh` in the next terminal, is stopped by a sentence rather than by
    # agents that quietly get denied.
    #
    # `auto` first, because it is the one that would otherwise *look* like it
    # worked: nothing is attached to decide with, so the rung answers `deny` to
    # everything while the banner below still prints `auto`.
    # The policy that will actually be attached, which is what this check was
    # written to take: with `--relay` the `auto` rung has a relay to decide
    # with, so the door opens by itself — exactly as its docstring promised the
    # day one existed.
    channel = (_broker.Relay(proj.root, wait=wait or _broker.RELAY_WAIT)
               if relay else None)
    auto_policy = channel.auto if channel else None
    unattachable = _broker.unattachable(rungs, auto=auto_policy)
    if unattachable:
        cli.con.print(f"[red]✗ {cli._x(unattachable)}[/]\n[dim]nothing is "
                      f"listening; fix the rung and start again[/]")
        raise typer.Exit(2)

    # Third door check, and the same shape as the two around it: refused
    # before anything binds, because a relay whose channel an agent can write
    # is a broker that hands out its own permissions.
    if relay:
        short = _broker.unwaitable(wait)
        if short:
            cli.con.print(f"[red]✗ {cli._x(short)}[/]\n[dim]nothing is "
                          f"listening; fix the wait and start again[/]")
            raise typer.Exit(2)
        unsafe = _broker.unrelayable(proj.root)
        if unsafe:
            cli.con.print(f"[red]✗ {cli._x(unsafe)}[/]\n[dim]nothing is "
                      f"listening; fix the channel and start again[/]")
            raise typer.Exit(2)

    # A deep checkout gets a sentence rather than a traceback out of
    # `socket.bind`. `P-F9`.
    unbindable = _broker.unbindable(proj.root)
    if unbindable:
        cli.con.print(f"[red]✗ {cli._x(unbindable)}[/]")
        raise typer.Exit(2)

    # `rungs` is what was just read and reported; passing it keeps the
    # class's "read once at construction" to one read. Re-deriving it in
    # `__post_init__` worked only because the loop above had already put
    # the values in `os.environ`, which is a second source for one rule.
    # The front end is the only thing `--relay` changes. Everything else —
    # the rungs, the memory of grants, the log, the serial answering — is the
    # same broker, because a relay is a way of *asking* and not a policy.
    front = channel.prompt if channel else _broker.terminal_prompt
    b = _broker.Broker(root=proj.root, prompt=front, auto=auto_policy,
                       rungs=rungs)
    # The relay's own listener, beside the broker's. Started before `serve`
    # blocks, stopped with it.
    relay_stop = threading.Event()
    if channel is not None:
        threading.Thread(target=channel.serve, args=(relay_stop,),
                         daemon=True).start()
    cli.con.print(
        f"[green]listening[/] on {cli._x(_broker.SOCKET_NAME)} "
        f"[dim]write={rungs['write']} exec={rungs['exec']} · "
        f"answers go to {cli._x(_broker.LOG_NAME)} · Ctrl-C to stop[/]")
    if relay:
        cli.con.print(
            f"[dim]relaying on {cli._x(_broker.RELAY_SOCK_NAME)}: each request "
            f"waits {int(wait or _broker.RELAY_WAIT)}s for `dg-agent consent`, "
            f"and is handed over a connection rather than left in a file. "
            f"Nobody answering is a deny — an unreachable decider is not "
            f"consent.[/]")
        # Which rung a request came in on decides what its answer is *called*,
        # and that is the whole of `D36`. Printed at the door so a launcher
        # knows before the first request which of the two they have set up.
        for kind, level in sorted(rungs.items()):
            if level in ("user", "scoped"):
                cli.con.print(f"[dim]  {kind}: `{level}` — answers are "
                          f"recorded [b]person[/b], so a person must give "
                          f"them[/]")
            elif level == "auto":
                cli.con.print(f"[yellow]  {kind}: `auto` — whatever answers is "
                          f"recorded [b]auto[/b], not as a person[/]")
    try:
        b.serve()
    except KeyboardInterrupt:
        cli.con.print("\n[dim]stopped — agents now get the gate's own verdict, "
                      "which in a headless run is a refusal[/]")
    finally:
        relay_stop.set()


@app.command("run", context_settings={"allow_interspersed_args": False})
def agent_run(
    command: list[str] = typer.Argument(
        None, metavar="-- COMMAND …",
        help="what to spawn, after `--`; anything the host takes"),
    plan_path: str = typer.Option(
        None, "--plan", metavar="PATH",
        help=f"a {fanout.ENV_NAME} written by `dg-agent setup`"),
    decide: str = typer.Option(None, "--decide", help="open | evidence | never"),
    apply_policy: str = typer.Option(
        None, "--apply", help="own | never — whether it writes the store "
                              "itself, or only stages"),
    write_scope: str = typer.Option(None, "--write", help="open | launch"),
    area: str = typer.Option(None, "--area-policy", help="open | strict"),
    terse: str = typer.Option(None, "--terse",
                              help="`on`, a character count, or `off`"),
    budget: str = typer.Option(None, "--budget",
                               help="`30m`, `1800`, or `infinite`"),
    exec_allow: str = typer.Option(
        None, "--exec-allow", metavar="NAMES",
        help="program names the agent may run unasked, space-separated"),
    name: str = typer.Option(
        None, "--agent", metavar="NAME",
        help="run under a name already claimed, instead of claiming one"),
    floor_applied: bool = typer.Option(
        False, "--floor-applied",
        help="the spawn line already carries this backend's settings; "
             "`dg-agent setup` writes this on the lines it generates"),
) -> None:
    """Claim a name, compose the environment, and run one agent under it.

    `dg` cannot set its caller's environment and neither can this — so it
    composes one and hands it to a **child**, which is what turns three
    separate rules into one command:

    - **`$DG_AGENT` is set for the child only.** That is the rule
      `render_launch` has always written into `launch.sh` as a comment: an
      exported name makes the launcher an agent, and its own policy then
      refuses it. A behaviour beats a comment somebody has to obey.
    - **Every value is validated before anything is spawned**, so a typo is
      caught before an agent starts rather than after a wave of them have
      filed proposals under the widest policy there is.
    - **One number, not two.** `--budget 30m` and `timeout 1800` were two
      independent values in one generated line, agreeing only until somebody
      edited the file the README expects them to edit. Here the budget is the
      timeout.

    **What the budget buys, and what it does not.** This process is the child's
    parent, so it can stop it and — more useful — do the hand-back by itself:
    a child killed for time, or one that died holding work, has whatever it
    holds parked under its own name, with the reason naming what stopped it.
    A child that exited clean has nothing parked, because a park filed over a
    finished session records a stop that never happened.

    That covers the child timing out and the child crashing. It does **not**
    cover this process being killed — a `kill -9` on the group, the machine
    going down, the terminal closing. `dg-agent expire` therefore stays exactly
    as it is and remains the backstop the procedure tells you to run. This
    narrows the window; nothing closes it.

    **The name is not released.** An agent that staged a proposal is holding
    something a person has to read, and auto-release would recycle a name whose
    tray still matters.
    """
    if not command:
        cli.con.print("[red]✗ nothing to run — put the command after `--`[/]\n"
                      "[dim]`dg-agent run --plan fanout/env.json -- claude -p "
                      "\"$(cat fanout/scout.md)\"`[/]")
        raise typer.Exit(2)
    spec = None
    if plan_path is not None:
        try:
            spec = fanout.read_env_plan(plan_path)
        except (OSError, ValueError) as exc:
            cli.con.print(f"[red]✗ {cli._x(exc)}[/]")
            raise typer.Exit(2) from None
    try:
        composed = _compose(spec, {"decide": decide, "apply": apply_policy,
                                   "write": write_scope,
                                   "area": area, "terse": terse,
                                   "budget": budget, "exec_allow": exec_allow})
    except (ValueError, env.BadSpan) as exc:
        cli.con.print(f"[red]✗ {cli._x(exc)}[/]\n"
                      f"[dim]nothing was spawned and no name was claimed[/]")
        raise typer.Exit(2) from None

    # Before the name is claimed and before anything is spawned, which is the
    # same rule `_compose` follows: a run that believes it is confined and is
    # not is the one outcome a floor exists to rule out, and the runner's own
    # sandbox does not refuse — it warns on a stderr nobody reads and proceeds.
    from dgraph import confine as _confine
    unfloored = _confine.preflight(mode_=composed.get(_confine.CONFINE_ENV),
                                   backend_=composed.get(_confine.FLOOR_ENV))
    unfloored = unfloored or _floor_unapplied(composed, floor_applied)
    if unfloored:
        cli.con.print(f"[red]✗ {cli._x(unfloored)}[/]\n"
                      f"[dim]nothing was spawned and no name was claimed[/]")
        raise typer.Exit(2)

    try:
        wrap = _floor_prefix(composed)
    except ValueError as exc:
        cli.con.print(f"[red]✗ {cli._x(exc)}[/]")
        raise typer.Exit(2) from None

    seconds = env.span(composed.get(env.BUDGET_ENV))
    if name is None:
        try:
            name = agents.claim(budget=seconds)
        except agents.Exhausted as exc:
            cli.con.print(f"[red]✗ no name to claim — {cli._x(exc)}[/]\n"
                          f"[dim]`dg-agent prune` frees what is finished[/]")
            raise typer.Exit(1) from None

    child = dict(os.environ, **composed)
    child[env.AGENT_ENV] = name
    cli.con.print(f"[green]running[/] as {cli._x(name)} "
                  f"[dim]{' '.join(f'{k}={v}' for k, v in sorted(composed.items()))}"
                  f"{'' if seconds is None else f' · {env.show_span(seconds)}'}[/]")

    # `start_new_session` so the whole tree can be stopped, not just the shell
    # the host wrapped it in: a host that spawns a model runner leaves the
    # runner behind when only the direct child is signalled, and a budget that
    # stopped the wrapper while the work carried on would be the advisory
    # budget this command exists to replace.
    try:
        proc = subprocess.Popen([*wrap, *command], env=child,
                                start_new_session=True)
    except OSError as exc:
        cli.con.print(f"[red]✗ could not run {cli._x(command[0])} — "
                      f"{cli._x(exc)}[/]\n[dim]{cli._x(name)} stays claimed; "
                      f"`dg-agent release {name}` if it is not wanted[/]")
        raise typer.Exit(127) from None

    timed_out = False
    try:
        code = proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _stop(proc)
        code = proc.returncode if proc.returncode is not None else -1
    except KeyboardInterrupt:
        _stop(proc)
        code = 130

    why = None
    if timed_out:
        why = (f"budget spent: {name} was given {env.show_span(seconds)} and "
               f"was stopped at it")
    elif code != 0:
        why = f"{name} exited {code} while holding this"
    if why is not None:
        parked = _hold_back(name, why)
        if parked:
            cli.con.print(f"[green]staged[/] park of {', '.join(parked)} "
                          f"[dim]as {cli._x(name)} — `dg pending --agent "
                          f"{cli._x(name)}` to read it[/]")
    if timed_out:
        cli.con.print(f"[yellow]{cli._x(name)} was stopped at its budget[/]")
    elif code != 0:
        cli.con.print(f"[yellow]{cli._x(name)} exited {code}[/]")
    cli.con.print(f"[dim]{cli._x(name)} stays claimed — it may be holding a "
                  f"proposal somebody has to read. `dg-agent list`, then "
                  f"`dg-agent release {cli._x(name)}`[/]")
    raise typer.Exit(_exit_code(code, timed_out))


#: What `timeout(1)` exits when it stops a command, and what this exits for the
#: same reason -- a budget that ended the run is not a run that succeeded, and a
#: launcher checking `$?` in a loop should see the same number it saw when the
#: line was `timeout 1800 …`.
TIMED_OUT = 124


def _exit_code(code: int, timed_out: bool) -> int:
    """The child's exit status, as a shell can read it.

    A signalled child reports a *negative* number through `Popen`, which is not
    an exit status at all: returned as-is it comes back as 0 through typer, so a
    run stopped at its budget would look like a run that finished. `TIMED_OUT`
    for the budget, and the shell's `128 + n` for any other signal.
    """
    if timed_out:
        return TIMED_OUT
    if code < 0:
        return 128 - code
    return code


def _stop(proc: subprocess.Popen) -> None:
    """Stop the child's whole process group, politely and then not.

    The group rather than the process, for the reason `start_new_session` is
    set. `SIGTERM` first because a host that keeps a session file wants the
    chance to close it, and `SIGKILL` after a short grace because a budget that
    can be ignored is the advisory budget this replaced.
    """
    import signal
    for sig, grace in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def main() -> None:
    if len(sys.argv) == 1:
        # The roster, for the same reason `dg` alone shows the graph: the
        # question somebody arrives with is "who is running", and a help screen
        # answers a question nobody asked.
        sys.argv.append("list")
    app()


if __name__ == "__main__":
    main()
