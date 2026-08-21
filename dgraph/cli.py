"""`dg` — read and edit the decision graph."""

from __future__ import annotations

import contextlib
import copy
import json
import os
import pathlib
import sys
from datetime import date as _date

import typer
from typer.core import TyperGroup
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from dgraph import applying
from dgraph import brief as _brief
from dgraph import check as _check
from dgraph import compact
from dgraph import cross, editor, pending, project, render, task_pending, task_render
from dgraph import model
from dgraph.model import SIMPLE_STATUSES, Graph
from dgraph.tasks import ID_RE as TASK_ID_RE
from dgraph.tasks import MISSING_EDGE, TaskGraph

# ---- how `--help` reads --------------------------------------------------
#
# Thirty commands in one flat list is a list nobody reaches the end of, and the
# question a reader arrives with is not "what is there?" but "what do I run to
# do this?" — so the headings below name intentions rather than kinds.
#
# `LAYOUT` is the whole help screen in one place: the panels, their order, and
# the order of the commands inside each. Nothing else decides it. In particular
# **it is not the order the functions happen to sit in this file**, which is
# where Rich takes it from by default — an accident of editing history is a
# poor table of contents, and moving a function would silently rearrange the
# documentation.

READ = "Reading the graph"
HONEST = "Keeping it honest"
RECORD = "Recording decisions"
STAGE = "The staging area — nothing is written until `apply`"
STORE = "Starting a graph, and moving one"
WORK = "Tracking the work — its own graph, and its own help screen"
WEB = "In a browser"

#: Panel, then the commands in it, both in the order `dg --help` shows them.
#: Within a panel the sequence is the order you would meet them: what a person
#: runs first, then the rarer neighbours, with anything destructive last.
#:
#: `"a/b"` is two names for one command, and reads as one line — two lines with
#: the same description would leave a reader counting commands that do not
#: exist, and looking for the difference between them.
LAYOUT = (
    (READ, ("show", "brief", "node", "why/context", "tree", "path", "areas")),
    (HONEST, ("check", "gate", "render")),
    (RECORD, ("add", "decide", "reopen", "confirm", "repair",
              "dep", "undep", "rm")),
    (STAGE, ("pending", "edit", "drop", "clear", "apply")),
    (STORE, ("init", "import", "import-md", "export")),
    (WORK, ("task",)),
    (WEB, ("serve",)),
)

#: The same for `dg task --help`, and deliberately parallel: the two stores are
#: peers, and a reader who has learned one help screen should not have to learn
#: the other. So a command that exists on both sides sits under the heading
#: that means the same thing — `render` is honesty about a generated view in
#: both, `export` is half of moving a store in both — and `test_cli.py` checks
#: that pairing rather than trusting it. `drop` is the one deliberate
#: exception, and is named there.
#:
#: Parallel in what the headings *mean*, not in how many commands sit under
#: them. The task side has no `reopen`, `confirm` or `repair` because a task
#: has no falsifier and nothing propagates out of one — a premise moving is
#: handled from the decision side — and no `check` because `dg check` judges
#: both stores. Those absences are the model being honest about what a task
#: is, so closing them for symmetry alone would be adding verbs that assert
#: something untrue about the record.
T_READ = "Reading the work"
T_HONEST = "Keeping it honest"
T_RECORD = "Recording work"
T_STAGE = "The staging area — nothing is written until `apply`"
T_STORE = "Starting a backlog, and moving one"

TASK_LAYOUT = (
    (T_READ, ("node", "tree")),
    (T_HONEST, ("render",)),
    (T_RECORD, ("add", "start", "done", "drop", "link", "unlink",
                "dep", "undep", "rm")),
    (T_STAGE, ("pending", "drop-op", "clear")),
    (T_STORE, ("init", "import", "export")),
)

#: What each panel is *for*, so the two screens can be checked against each
#: other rather than read side by side by somebody who remembers to.
ROLES = {READ: "read", HONEST: "honest", RECORD: "record", STAGE: "stage",
         STORE: "store", WORK: "work", WEB: "web",
         T_READ: "read", T_HONEST: "honest", T_RECORD: "record",
         T_STAGE: "stage", T_STORE: "store"}


def _ordered(layout) -> type[TyperGroup]:
    """A command group that lists its commands in `layout`'s order.

    Rich builds a panel per `rich_help_panel` in the order `list_commands`
    yields them, so this one list fixes both which panel comes first and how
    the commands inside it read.

    A command absent from the layout is still listed — at the end, in
    alphabetical order — rather than dropped. Losing a command from `--help`
    because somebody forgot to name it here would be a documentation bug that
    hides a feature, which is worse than an untidy tail. `test_cli.py` asserts
    the tail is empty, so the untidiness is caught rather than lived with.

    A layout entry naming several commands with `/` collapses them into one
    line, for aliases: Rich prints a row per name it is handed, so the way to
    print one row for `why` and `context` is to hand it a single command
    carrying both names. The first name wins the help text and, since the row
    is only a label, any of them still runs.
    """
    order = [name for _, names in layout for name in names]

    class Ordered(TyperGroup):
        def list_commands(self, ctx):
            known = [n for n in order
                     if all(a in self.commands for a in n.split("/"))]
            listed = {a for n in known for a in n.split("/")}
            return known + sorted(set(self.commands) - listed)

        def get_command(self, ctx, name):
            if "/" in name and name not in self.commands:
                cmd = super().get_command(ctx, name.split("/")[0])
                if cmd is not None:
                    cmd = copy.copy(cmd)
                    cmd.name = name
                return cmd
            return super().get_command(ctx, name)

    return Ordered


app = typer.Typer(
    cls=_ordered(LAYOUT),
    add_completion=False,
    help="Read and edit a decision graph. decisions.json is the source of "
         "truth; decision-graph.md is generated from it.",
)
con = Console()


def _version(show: bool) -> None:
    """`dg --version`, so an adapter can tell it is talking to an old `dg`.

    The plugin and the package install separately — a marketplace entry and a
    `pip install` — so skew is not hypothetical. Without a version surface an
    adapter's only symptom is a subcommand that does not exist yet, which looks
    exactly like a project with no graph.
    """
    if not show:
        return
    import dgraph
    con.print(dgraph.version())
    raise typer.Exit()


@app.callback()
def _root(
    project_dir: str = typer.Option(
        None, "--project", "-C", metavar="PATH",
        help="Project directory. Defaults to $DG_PROJECT, else the nearest "
             "ancestor holding decisions.json, else the cwd.",
    ),
    version: bool = typer.Option(
        False, "--version", callback=_version, is_eager=True,
        help="Print the installed version and exit.",
    ),
) -> None:
    project.use(project_dir)

STATUS_STYLE = {
    "DECIDED": "green",
    "OPEN": "bold red",
    "BLOCKED": "yellow",
    "REOPENED": "magenta",
    "PROVISIONAL": "cyan",
}


def _g() -> Graph:
    proj = project.find()
    if not proj.has_decisions:
        con.print(f"[red]no decisions.json under {proj.root}[/]\n"
                  f"[dim]run `dg init` there, or pass --project PATH[/]")
        raise typer.Exit(2)
    return Graph.load()


def _eff(g: Graph, skip: int | None = None) -> Graph:
    """The store plus the staged ops — what stage-time guards judge against.

    See `pending.preview`. If the staged batch itself no longer applies,
    staging more on top of it helps nobody, so this stops and names the fix
    instead of guessing from the store alone.
    """
    try:
        return pending.preview(g, skip=skip)
    except pending.ApplyError as exc:
        con.print(f"[red]the staged ops no longer apply cleanly, so this "
                  f"command cannot judge the graph they produce[/]\n{_x(exc)}\n"
                  f"[dim]`dg pending` to review; `dg drop <id>` or `dg edit <id>` "
                  f"to fix[/]")
        raise typer.Exit(1) from None


def _vet_all(g: Graph, ops: list[dict]) -> None:
    """`pending.vet_all`, with a refusal turned into a clean CLI exit."""
    try:
        pending.vet_all(g, ops)
    except pending.ApplyError as exc:
        con.print(f"[red]✗ nothing staged[/]\n{_x(exc)}")
        raise typer.Exit(1) from None


def _warn_stuck() -> None:
    """After staging: say now if `dg apply` would refuse the batch as it stands.

    A warning, never a refusal — mid-batch invalidity can be transitional (a
    child decided before its premise, with the premise's close still to come),
    and `apply` re-validates regardless. What must not happen is the user
    learning at apply time, several commands later, which op to blame.
    """
    try:
        pending.apply_all(Graph.load(), pending.load(), cross.guard_decisions())
    except pending.ApplyError as exc:
        con.print(f"[yellow]note: as staged, `dg apply` would currently refuse "
                  f"this batch[/]\n[dim]{_x(exc)}[/]\n"
                  f"[dim]staging what is missing, or `dg drop <id>`, clears it[/]")


def _style(status: str) -> str:
    return STATUS_STYLE.get(status.split(":")[0], "white")


def _x(text: object) -> str:
    """Escape text that is data, not markup.

    Titles, answers and violation strings all routinely contain square
    brackets, and rich reads `[no_orphans]` or `[draft]` as a style tag and
    silently drops it. Anything coming from the store or from a Violation goes
    through here; the literal `[green]`-style tags in f-strings do not.
    """
    return escape(str(text) if text is not None else "")


def _interactive() -> bool:
    """Whether there is a person on the other end. The seam tests replace.

    Everything that would block on a human — a prompt, a confirmation, an
    editor — asks this first, so that the one condition is decided in one place
    and a test can say "pretend there is a terminal" without owning stdin.
    """
    return sys.stdin.isatty()


def _status_legal(g: Graph, status: str, of: str | None = None) -> bool:
    """The legality rule from `Graph.validate`, applied before staging.

    Delegates rather than restating: this used to be its own copy of the rule
    and had already drifted from the validator's — see `model.status_fault`.
    """
    return model.status_fault(status, g.vertices, of=of) is None


def _tag(v) -> str:
    return f"[{_style(v.status)}]{v.status}[/]"


# ---- reading -------------------------------------------------------------


@app.command(rich_help_panel=READ)
def show(
    full: bool = typer.Option(False, "--full",
                              help="The table, with no title clipped."),
) -> None:
    """The frontier: everything still open or blocked, and anything provisional.

    One line each by default — id, status, title, and what the item waits on or
    releases. `--full` gives the table instead, which clips nothing and is the
    one to reach for when two titles read the same at the width above.
    """
    g = _g()
    att = _brief.attention(g)
    if full:
        _show_table(g, att)
    else:
        _show_listing(g, att)


def _width() -> int:
    """The line budget for a listing: the terminal, within reason.

    Rich reports 80 when there is no terminal, which is the right conservative
    default for the pipe this output usually goes down. A real terminal gets
    its real width up to `compact.MAX_WIDTH`, past which a line stops being one
    thing the eye takes in and the columns drift too far apart to associate.
    """
    return max(60, min(compact.MAX_WIDTH, con.width))


def _say(lines: list[str]) -> None:
    """Print listing lines with their markup but without Rich's wrapping.

    `soft_wrap=True` is the whole point: `con.print` folds at `$COLUMNS`, and a
    folded listing is one with the ids on the wrong lines — the same failure
    `brief` and `context` avoid by not using Rich at all. Here the colour is
    worth keeping, so the wrapping is turned off instead.
    """
    for line in lines:
        con.print(line, soft_wrap=True, highlight=False)


def _show_listing(g: Graph, att: list[dict]) -> None:
    """The frontier as one line per decision — the default, and the piped one."""
    rows = _brief.rows(g)
    ev = _brief.evidence_map(project.find())
    con.print(f"[bold]FRONTIER[/]  {len(rows)} not settled of "
              f"{len(g.vertices)}   " + "  ".join(
                  f"[{_style(k)}]{k} {n}[/]"
                  for k, n in sorted(_brief.counts(g).items())))
    entries: list[tuple[str, str, str, str]] = []
    areas: list[str] = []
    for r in rows:
        aside = []
        if r.waiting_on:
            aside.append("waits " + ", ".join(r.waiting_on))
        if ev.get(r.id):
            # An open decision with a spike still running is not decidable now.
            aside.append("evidence " + ", ".join(ev[r.id]))
        if r.unblocks:
            aside.append("unblocks " + ", ".join(r.unblocks))
        if not aside:
            aside.append("decidable now")
        # The base status, not the stored one: `BLOCKED:D04` says the blocker
        # twice, once here and once in `waits D04` above. Dropping it from the
        # column gives four characters back to every title in the listing.
        base = g.vertices[r.id].base_status
        entries.append((r.id, f"[{_style(base)}]{base}[/]", _x(r.title),
                        "[dim]" + _x(" · ".join(aside)) + "[/]"))
        areas.append(f"[dim]{_x(r.area)}[/]")
    _say(compact.listing(entries, width=_width(), markup=True, tails=areas))
    if not entries:
        con.print("  [dim]nothing open — every question here is settled[/]")

    # A PROVISIONAL vertex is settled, so it is not in the frontier — but its
    # answer rests on a premise under review, which is the thing most worth
    # knowing before building on it. It was missing from this view entirely.
    if att:
        con.print(f"\n[bold]RESTING ON A PREMISE UNDER REVIEW[/]  {len(att)}")
        _say(compact.listing(
            [(a["id"], f"[{_style('PROVISIONAL')}]PROVISIONAL[/]",
              _x(a["title"]),
              "[dim]" + _x(", ".join(a["because"]) if a["because"]
                           else f"premises settled again — "
                                f"`dg confirm {a['id']}`") + "[/]")
             for a in att],
            width=_width(), markup=True,
            tails=[f"[dim]{_x(a['area'])}[/]" for a in att]))
    con.print(compact.hint("dg show --full", "the table, nothing clipped"))


def _show_table(g: Graph, att: list[dict]) -> None:
    """The detailed frontier: every title in full, every relation its own column."""
    t = Table(title="Frontier", header_style="bold")
    for c in ("ID", "Decision", "Status", "Waiting on", "Unblocks", "Area"):
        t.add_column(c)
    for r in _brief.rows(g):
        t.add_row(r.id, _x(r.title), _tag(g.vertices[r.id]),
                  ", ".join(r.waiting_on) or "—",
                  ", ".join(r.unblocks) or "—", _x(r.area))
    con.print(t)

    if att:
        p = Table(title="Resting on a premise under review", header_style="bold")
        for c in ("ID", "Decision", "Because", "Area"):
            p.add_column(c)
        for a in att:
            why = (", ".join(a["because"]) if a["because"]
                   else f"premises settled again — `dg confirm {a['id']}`")
            p.add_row(a["id"], _x(a["title"]), _x(why), _x(a["area"]))
        con.print(p)

    con.print("  ".join(
        f"[{_style(k)}]{k} {n}[/]" for k, n in sorted(_brief.counts(g).items())
    ))


@app.command(rich_help_panel=READ)
def tree(root: str = typer.Argument(None, help="Vertex to root at")) -> None:
    """The DAG as a tree, from the roots or from one vertex."""
    g = _g()
    seen: set[str] = set()

    def add(parent: Tree, vid: str) -> None:
        v = g.vertices[vid]
        label = (f"[bold]{vid}[/] {_x(v.title)} "
                 f"[{_style(v.status)}]{v.status}[/]")
        if vid in seen:
            parent.add(label + " [dim](above)[/]")
            return
        seen.add(vid)
        node = parent.add(label)
        for c in g.children(vid):
            add(node, c)

    top = Tree("decision graph")
    for r in ([root] if root else g.roots()):
        if r not in g.vertices:
            con.print(f"[red]unknown vertex {r}[/]")
            raise typer.Exit(1)
        add(top, r)
    con.print(top)


@app.command(rich_help_panel=READ)
def node(vid: str) -> None:
    """Everything known about one decision."""
    g = _g()
    if vid not in g.vertices:
        con.print(f"[red]unknown vertex {vid}[/]")
        raise typer.Exit(1)
    v = g.vertices[vid]
    e = g.active_edge(vid)
    lines = [
        f"[bold]{_x(v.title)}[/]", "",
        f"status      {_tag(v)}",
        f"area        {_x(v.area)}",
        f"depends on  {', '.join(g.depends(vid)) or '—'}",
    ]
    if e and e.decided:
        lines += [
            f"opens       {', '.join(e.to) or 'TERMINAL'}",
            f"falsifier   {_x(e.falsifier) or '—'}",
            f"source      {_x(e.source)}   ({e.date})",
            "", "[bold]Answer[/]", _x(e.answer),
        ]
    else:
        lines += [f"opens       {', '.join(g.children(vid)) or '—'}"]
        if v.note:
            lines += ["", "[bold]Note[/]", _x(v.note)]
    # Derived from the task store, stored nowhere: `decisions.json` never names
    # a task. Absent from `decision-graph.md` on purpose — that file is guarded
    # by `stale_view`, so a task count in it would mean filing a chore makes the
    # decision view stale and denies the commit.
    doing = _tasks_implementing(vid)
    if doing:
        lines += [f"implemented by {', '.join(doing)}"]
    ev = _tasks_informing(vid)
    if ev:
        lines += [f"evidence from {', '.join(ev)}"]
    hist = g.history(vid)
    if hist:
        lines += ["", "[bold]Superseded[/]"]
        lines += [
            f"  “{_x(h.summary)}” → {_x(h.replaced_by) or '(undecided)'}"
            f"\n    {_x(h.why)}"
            for h in hist
        ]
    con.print(Panel("\n".join(lines), title=vid, border_style=_style(v.status)))


@app.command(rich_help_panel=READ)
def path(a: str, b: str) -> None:
    """The chain of evidence linking two decisions."""
    g = _g()
    p = g.path(a, b)
    if not p:
        con.print(f"[yellow]no decision path from {a} to {b}[/]")
        raise typer.Exit(1)
    for i, vid in enumerate(p):
        v = g.vertices[vid]
        con.print(f"[bold]{vid}[/] {_x(v.title)} "
                  f"[{_style(v.status)}]{v.status}[/]")
        if i < len(p) - 1:
            e = g.active_edge(vid)
            first = (e.answer or "").strip().split("\n")[0] if e else ""
            con.print(f"  [dim]│ {_x(first)}[/]")
            con.print("  [dim]▼[/]")


@app.command(rich_help_panel=READ)
def context(
    vid: str = typer.Argument(..., help="A decision id (D..) or a task id (T..)"),
    as_json: bool = typer.Option(False, "--json", help="The same as data."),
    full: bool = typer.Option(False, "--full",
                              help="Every answer, source and falsifier in full."),
) -> None:
    """Why this node is where it is: every premise it rests on.

    `dg why` and `dg context` are the same command: `why` is the question this
    answers and the one the tool is named for, `context` is what it prints.

    `dg node` shows one decision; this shows the reasoning behind it. By
    default that is the chain schematically — the shape on one line, then one
    line per premise saying what it answered. `--full` prints each answer, the
    evidence that reached it and the falsifier that would overturn it, which is
    the form to paste into a subagent's prompt.

    A task id pulls in the chain behind the decision it exists `because` of,
    which is the context a dispatched agent is otherwise missing.
    """
    from dgraph import context as _context
    proj = project.find()
    if not proj.exists:
        con.print(f"[red]no {project.STORE_NAME} under {proj.root}[/]\n"
                  f"[dim]run `dg init` there, or pass --project PATH[/]")
        raise typer.Exit(2)
    try:
        d = _context.data(proj, vid)
    except _context.UnknownNode as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    # plain print, never con.print: this is piped into a subagent's prompt and
    # rich soft-wraps at $COLUMNS, which would move the ids onto wrong lines.
    if as_json:
        print(json.dumps(d, indent=2, ensure_ascii=False))
        return
    print(_context.text(d) if full else _context.compact(d), end="")


#: `dg why D06` and `dg context D06` are the same command under two names.
#: Registered against the one callback rather than wrapped, so they cannot
#: drift in their options, their help, or what they print — the failure this
#: codebase spends most of its comments avoiding.
#:
#: `why` is the question `dgraph/context.py` opens by naming ("why is this
#: settled, and what would unsettle it?") and the one `dear-guide` is named
#: for. `context` stays because it is what the thing *is*, and because scripts
#: and docs already say it.
app.command(name="why", rich_help_panel=READ)(context)


@app.command(rich_help_panel=READ)
def areas() -> None:
    """Counts by area and status, in whichever stores this project has.

    Two tables rather than one, because the two stores share their areas and
    not their vocabularies: `OPEN` and `TODO` are not columns of the same
    table, and a row summing across them would be counting questions and work
    as if they were the same thing. Sharing the *areas* is the point — the same
    corner of a project, seen as what is undecided and as what is outstanding.

    A project with only one store gets only its table, and pays nothing for the
    other.
    """
    proj = project.find()
    g = _decisions_or_none()
    tg = TaskGraph.load(proj.tasks) if proj.has_tasks else None
    if g is None and tg is None:
        con.print(f"[red]no {project.STORE_NAME} under {proj.root}[/]\n"
                  f"[dim]run `dg init` there, or pass --project PATH[/]")
        raise typer.Exit(2)
    if g is not None:
        _area_counts("Decisions", g.areas, list(g.vertices.values()),
                     lambda v: v.base_status, _style)
    if tg is not None:
        _area_counts("Tasks", tg.areas, list(tg.tasks.values()),
                     lambda t: t.status,
                     lambda s: TASK_STYLE.get(s, "white"))


def _area_counts(title: str, areas, items: list, status_of, style) -> None:
    """One store's areas against its own statuses.

    Written once and called twice rather than duplicated per store: the two
    tables are the same question asked of different records, and the way they
    stop agreeing on what a count means is by being two pieces of code.
    """
    statuses = sorted({status_of(i) for i in items})
    t = Table(header_style="bold", title=title)
    t.add_column("Area")
    for st in statuses:
        t.add_column(st, justify="right", style=style(st))
    t.add_column("Total", justify="right")
    for a in areas:
        rows = [i for i in items if i.area == a]
        t.add_row(_x(a), *[str(sum(1 for i in rows if status_of(i) == st))
                           for st in statuses], str(len(rows)))
    con.print(t)


@app.command(rich_help_panel=READ)
def brief(
    as_json: bool = typer.Option(False, "--json",
                                 help="The same information as data."),
    limit: int = typer.Option(_brief.LIMIT, "--limit",
                              help="Frontier entries before summarising."),
) -> None:
    """What an agent needs to know about the graph right now.

    The payload a coding-agent host injects at the start of a session: the
    frontier with what each item waits on and releases, anything PROVISIONAL,
    the staging area, and the validity check. See `dgraph/brief.py` for why this
    is not `dg show`.
    """
    proj = project.find()
    if as_json:
        # plain print, never con.print — the `dg export` rule. Rich soft-wraps at
        # $COLUMNS and would corrupt the JSON for whatever is parsing it.
        print(json.dumps(_brief.data(proj), indent=2, ensure_ascii=False))
        return
    if not proj.exists:
        con.print(f"[red]no {project.STORE_NAME} under {proj.root}[/]\n"
                  f"[dim]run `dg init` there, or pass --project PATH[/]")
        raise typer.Exit(2)
    # Plain print for the same reason: this is read through a pipe, and a
    # soft-wrapped brief is a brief with the ids moved to the wrong lines.
    print(_brief.text(proj, limit=limit))


@app.command(rich_help_panel=HONEST)
def gate(
    command: str = typer.Option(None, "--command", metavar="CMD",
                                help="The shell command a host is about to run."),
    as_json: bool = typer.Option(False, "--json", help="Machine form."),
    triggers: bool = typer.Option(False, "--triggers",
                                  help="Print the substrings an adapter's fast "
                                       "path must let through, one per line."),
) -> None:
    """Judge a shell command before a host runs it: allow, ask, or deny.

    The commit gate, host-neutral. Both agent adapters call this and translate
    the answer, so the rule lives here rather than twice in two languages. Always
    exits 0 — the verdict is the output, and an adapter must be able to tell a
    refusal from a crash.

    `--triggers` prints `gate.TRIGGERS` instead of judging anything. An adapter
    skips this command entirely for a shell command containing none of them, so
    a third host — or a check on the two that exist — can read the list from the
    tool rather than copy it and let it go stale, which is how the removal
    verdict came to be unreachable from both.
    """
    from dgraph import gate as _gate
    if triggers:
        if as_json:
            print(json.dumps(list(_gate.TRIGGERS)))
        else:
            print("\n".join(_gate.TRIGGERS))
        return
    if command is None:
        con.print("[red]--command is required (or --triggers)[/]")
        raise typer.Exit(2)
    v = _gate.verdict(command)
    if as_json:
        print(json.dumps(v, ensure_ascii=False))
        return
    print(v["verdict"] if not v["reason"] else f"{v['verdict']}: {v['reason']}")


@app.command(rich_help_panel=HONEST)
def check() -> None:
    """Run every invariant, plus the markdown staleness check."""
    proj = project.find()
    if not proj.exists:
        # 2, not 1, matching `_g()`. "This project has no graph" is a different
        # thing from "this graph is broken", and an adapter keying on the exit
        # code has to be able to tell them apart.
        con.print(f"[red]no {project.STORE_NAME} or {project.TASKS_NAME} under "
                  f"{proj.root}[/]\n"
                  f"[dim]run `dg init` or `dg task init` there, or pass "
                  f"--project PATH[/]")
        raise typer.Exit(2)
    problems = _check.run(proj)
    for p in problems:
        con.print(f"[{'red' if p.blocking else 'yellow'}]"
                  f"{'✗' if p.blocking else '!'}[/] {_x(p)}")
    if any(p.blocking for p in problems):
        raise typer.Exit(1)
    # Counted per store that actually exists — a project may have either.
    sizes = []
    if proj.has_decisions:
        g = Graph.load(proj.store)
        sizes.append(f"{len(g.vertices)} vertices, {len(g.edges)} edges")
    if proj.has_tasks:
        tg = TaskGraph.load(proj.tasks)
        sizes.append(f"{len(tg.tasks)} tasks")
    tail = f", {len(problems)} warning(s)" if problems else ""
    con.print(f"[green]✓[/] {'; '.join(sizes)}, all invariants hold{tail}")


# ---- editing -------------------------------------------------------------


def _wants_editor(edit: bool | None) -> bool:
    """`--edit` wins; otherwise `$DG_EDIT` decides, and only at a terminal.

    `--no-edit` sets `edit` to False and must beat the environment, which is why
    this takes a tri-state rather than a bool.

    The terminal condition is for callers who are not people. An agent running
    `dg decide D04 -a … -s …` inherits whatever `$DG_EDIT` the user exported —
    the README tells them to — and would launch a blocking editor with nothing
    to draw on and nobody to type, hanging until something times out. An
    explicit `--edit` still works anywhere, so nothing a human asks for is
    refused.
    """
    if edit is not None:
        return edit
    if not _interactive():
        return False
    return os.environ.get("DG_EDIT", "").strip() not in ("", "0", "false", "no")


def _ask(prompt: str, flag: str, default: str | None = None) -> str:
    """Prompt for a value, unless there is nobody to prompt.

    With no terminal, click's own failure is a bare `Aborted.` — true, and
    useless to an agent, which needs to know which flag would have made the
    command work. A value with a default is optional, so it falls back silently.
    """
    if _interactive():
        if default is None:
            return typer.prompt(prompt)
        return typer.prompt(prompt, default=default, show_default=False)
    if default is not None:
        return default
    con.print(f"[red]missing {flag}[/]\n"
              f"[dim]{_x(prompt)}[/]\n"
              f"[dim]not a terminal, so it cannot be prompted for[/]")
    raise typer.Exit(2)


def _compose(g: Graph, kind: str, **kw) -> list[dict]:
    """Run the editor, turning its refusals into clean CLI exits."""
    proj = project.find()
    con.print(f"[dim]buffer: {proj.edit}[/]")
    if not editor.is_emacs(editor.resolve_editor()):
        premise = None
        if kw.get("vertex"):
            premise = next(iter(g.depends(kw["vertex"])), None)
        hint = f"run `dg node {premise}` for context on a premise" if premise \
            else "run `dg node <id>` for context"
        con.print(f"[dim]note: in-buffer navigation needs emacs — {hint}[/]")
    try:
        return editor.compose(g, kind, **kw)
    except editor.EditorAbort as exc:
        con.print(f"[yellow]aborted[/] {_x(exc)}")
        raise typer.Exit(1) from None
    except editor.EditorError as exc:
        con.print(f"[red]✗ nothing staged[/]\n{_x(exc)}")
        raise typer.Exit(1) from None


def _stage_close(g: Graph, op: dict) -> None:
    """Stage a close plus everything it implies, and say what was released.

    Shared by the prompt path and `--edit` so the propagation reporting cannot
    differ between them.
    """
    unknown = [x for x in op.get("to", []) if x not in g.vertices]
    if unknown:
        con.print(f"[red]unknown target(s): {', '.join(unknown)}[/]")
        raise typer.Exit(1)
    ops = pending.expand(g, op)
    # `against`: each op records what its premises looked like now, so an
    # apply after somebody else has moved one can say which. See `pending.stamp`.
    pending.stage_all(ops, against=g)
    released = [o["vertex"] for o in ops if o["op"] == "set_status"]
    if released:
        con.print(f"[cyan]{len(released)}[/] vertex(es) were "
                  f"BLOCKED:{op['vertex']} and are released to OPEN: "
                  f"{', '.join(released)}")
    con.print(f"[green]staged[/] {len(ops)} op(s) — review with `dg pending`, "
              f"then `dg apply`")
    _warn_stuck()


@app.command(rich_help_panel=RECORD)
def decide(
    vid: str,
    answer: str = typer.Option(None, "--answer", "-a"),
    source: str = typer.Option(None, "--source", "-s"),
    falsifier: str = typer.Option(None, "--falsifier", "-f"),
    opens: str = typer.Option(None, "--opens", "-o", help="comma-separated ids"),
    summary: str = typer.Option(None, "--summary"),
    edit: bool = typer.Option(None, "--edit/--no-edit", "-e",
                              help="Compose in $EDITOR (default: emacs). "
                                   "Set $DG_EDIT=1 to make this the default."),
) -> None:
    """Stage a decision. Prompts for anything not given as a flag."""
    g = _g()
    # Judged against the store *plus* the staged ops: a staged reopen makes the
    # vertex decidable again, a staged add makes it exist, and a staged close
    # makes a second close a duplicate — the store alone gets all three wrong.
    eff = _eff(g)
    if vid not in eff.vertices:
        con.print(f"[red]unknown vertex {vid}[/]")
        raise typer.Exit(1)
    v = eff.vertices[vid]
    e = eff.active_edge(vid)
    if e is not None and e.decided:
        # Caught here rather than at `apply`, which would refuse the same thing
        # (pending._apply_one) after the op was already staged, leaving the
        # staging area holding something that can never be applied. PROVISIONAL
        # reaches this branch too: it keeps the answer it was given.
        se = g.active_edge(vid) if vid in g.vertices else None
        if se is not None and se.decided:
            fix = ("`dg confirm` it if it still holds, or `dg reopen` it"
                   if v.base_status == "PROVISIONAL" else "`dg reopen` it first")
            con.print(f"[red]{vid} already has an answer, and is {v.status} — "
                      f"{fix}[/]")
        else:
            # Decided only in the staging area: the remedy is a staging verb,
            # not a reopen that would file a reversal of an unapplied answer.
            con.print(f"[red]a close for {vid} is already staged — "
                      f"`dg pending` to review it, `dg edit N` to revise it, "
                      f"or `dg drop <id>` to unstage it[/]")
        raise typer.Exit(1)
    if v.base_status == "BLOCKED" and v.blocker in eff.vertices \
            and not eff.vertices[v.blocker].settled:
        # A warning, not a refusal: sometimes the recorded blocker turns out
        # irrelevant, and closing the vertex is the only way to say so — there
        # is no unblock command, deliberately, since BLOCKED is a claim about
        # dependence and dropping it is itself a judgement worth making here.
        con.print(f"[yellow]note: {vid} is BLOCKED:{v.blocker} and "
                  f"{v.blocker} is not settled — deciding it anyway records "
                  f"that the block did not matter, and drops it[/]")

    if _wants_editor(edit):
        seed = {k: val for k, val in (
            ("answer", answer), ("source", source), ("falsifier", falsifier),
            ("summary", summary),
            ("to", [x.strip() for x in opens.split(",") if x.strip()] if opens else None),
        ) if val}
        ops = _compose(eff, "close", vertex=vid, seed=seed)
        _stage_close(eff, ops[0])
        return

    con.print(Panel(f"[bold]{_x(v.title)}[/]\n\n"
                    f"depends on {', '.join(eff.depends(vid)) or '—'}",
                    title=vid, border_style=_style(v.status)))
    answer = answer or _ask(
        "Answer (what was decided, and on what evidence)", "--answer/-a")
    source = source or _ask(
        "Source (a report/ path, a script, or 'discussion')", "--source/-s")
    existing = eff.children(vid)
    opens_l = (
        [x.strip() for x in opens.split(",") if x.strip()] if opens
        else [x.strip() for x in _ask(
            f"Opens which decisions? comma-separated, blank for terminal "
            f"(already linked: {', '.join(existing) or 'none'})",
            "--opens/-o", default="").split(",") if x.strip()]
    )
    if opens_l or existing:
        falsifier = falsifier or _ask(
            "Falsifier (what evidence would reopen this? 'ANALYTIC — …' if none)",
            "--falsifier/-f")
    op = {
        "op": "close", "vertex": vid, "answer": answer, "source": source,
        "falsifier": falsifier, "to": opens_l,
        "date": _date.today().isoformat(),
    }
    if summary:
        op["summary"] = summary
    _stage_close(eff, op)


@app.command(rich_help_panel=RECORD)
def repair() -> None:
    """Stage the PROVISIONAL a reopen would have derived, where one is missing.

    For a store broken from **outside** the tool. `dg reopen` marks every decided
    descendant PROVISIONAL as it stages, and that derivation is the only producer
    of the status — so a merge, a rebase, a partial checkout or a second clone
    can land a DECIDED vertex on a REOPENED premise with no op to derive the
    remedy from. `propagation` is a blocking violation, so until it is cleared
    every `dg apply` refuses and the commit gate denies every commit in the
    repository.

    `dg check` names two ways out and, until this existed, only one of them was
    real. *Settle the premise* — `dg decide <premise>` — does clear it, and means
    recording an answer nobody reached; that is a lie in the one artifact this
    tool exists to keep honest, and it is the wrong move whenever the premise is
    genuinely still under review. *Mark it PROVISIONAL* is the truthful one, and
    is what this stages.

    Deliberately narrow. It repairs `propagation` and nothing else, and only
    where the validator is reporting it right now — `set_status` stays an op the
    tool derives rather than one a caller may invent, which is the property that
    keeps a status from being writable by hand. `stale_block` and
    `block_is_a_premise` have derivable remedies too and are left alone until
    somebody decides they should not be; `no_dangling_refs` has none that is
    safe to derive at all.
    """
    g = _g()
    eff = _eff(g)
    ops = pending.repairs(eff)
    if not ops:
        # Said precisely, because "nothing to repair" reads as "the graph is
        # fine" and this command judges exactly one rule.
        con.print("[dim]nothing to repair — no decision rests on an unsettled "
                  "premise[/]\n[dim]`dg check` reports every other rule[/]")
        return
    for op in ops:
        v = eff.vertices[op["vertex"]]
        con.print(f"  [cyan]{op['vertex']}[/]  {_x(v.title)}\n"
                  f"      [dim]DECIDED → PROVISIONAL, resting on "
                  f"{op['derived_from']} "
                  f"({eff.vertices[op['derived_from']].status})[/]")
    pending.stage_all(ops, against=eff)
    con.print(f"[green]staged[/] {len(ops)} op(s) — review with `dg pending`, "
              f"then `dg apply`")
    _warn_stuck()


@app.command(rich_help_panel=RECORD)
def confirm(vid: str) -> None:
    """Re-affirm a PROVISIONAL decision: its premise moved, its answer holds.

    Without this, PROVISIONAL is a state with no exit. `set_status` is a derived
    op — `pending.expand` produces it and nothing else stages one — so the only
    route back to DECIDED was another `reopen` plus `decide`, which files a
    reversal that never happened. Reversals are the most valuable thing the graph
    holds and inventing one to escape a status would be a lie in the record.

    What it records is a real act: somebody re-read this decision under the new
    premise and found it still stands.
    """
    g = _g()
    eff = _eff(g)
    if vid not in eff.vertices:
        con.print(f"[red]unknown vertex {vid}[/]")
        raise typer.Exit(1)
    v = eff.vertices[vid]
    if v.base_status != "PROVISIONAL":
        con.print(f"[red]{vid} is {v.status}, not PROVISIONAL — "
                  f"there is nothing to re-affirm[/]")
        raise typer.Exit(1)
    unsettled = eff.provisional_because(vid)
    if unsettled:
        con.print(f"[red]{vid} still rests on {', '.join(unsettled)}[/]\n"
                  f"[dim]settle the premise first — until then PROVISIONAL is "
                  f"the accurate status[/]")
        raise typer.Exit(1)

    ops = pending.expand(eff, {"op": "set_status", "vertex": vid,
                               "status": "DECIDED"})
    pending.stage_all(ops, against=eff)
    released = [o["vertex"] for o in ops[1:]]
    if released:
        con.print(f"[cyan]{len(released)}[/] vertex(es) were BLOCKED:{vid} and "
                  f"are released to OPEN: {', '.join(released)}")
    con.print(f"[green]staged[/] {len(ops)} op(s) — {vid} back to DECIDED, "
              f"review with `dg pending`, then `dg apply`")
    _warn_stuck()


@app.command(rich_help_panel=RECORD)
def reopen(
    vid: str,
    why: str = typer.Option(None, "--why", "-w", help="what challenges it"),
    summary: str = typer.Option(None, "--summary"),
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="Stage without confirming the propagation."),
    edit: bool = typer.Option(None, "--edit/--no-edit", "-e",
                              help="Compose in $EDITOR (default: emacs)."),
) -> None:
    """Stage a reopen, showing what it drags into PROVISIONAL."""
    g = _g()
    # The effective graph, so a decision that exists only in the staging area
    # can be reopened, and — more importantly — so the propagation set counts
    # descendants whose close is staged but not applied.
    eff = _eff(g)
    if vid not in eff.vertices:
        con.print(f"[red]unknown vertex {vid}[/]")
        raise typer.Exit(1)
    e = eff.active_edge(vid)
    if e is None or not e.decided:
        con.print(f"[red]{vid} has no decision to reopen[/]")
        raise typer.Exit(1)

    if _wants_editor(edit):
        seed = {k: v for k, v in (("why", why), ("summary", summary)) if v}
        op = _compose(eff, "reopen", vertex=vid, seed=seed)[0]
    else:
        why = why or _ask("Why is this being reopened?", "--why/-w")
        op = {"op": "reopen", "vertex": vid, "why": why}
        if summary:
            op["summary"] = summary
    ops = pending.expand(eff, op)

    affected = [o["vertex"] for o in ops if o["op"] == "set_status"]
    # The blast radius in work terms, reported before the reopen is staged:
    # every unfinished task resting on this decision or on anything the reopen
    # just dragged into PROVISIONAL. No task tracker can compute this, because
    # none of them knows why a task exists.
    stalled = _tasks_resting_on([vid, *affected])
    con.print(Panel(
        f"[bold]{_x(eff.vertices[vid].title)}[/]\n\n"
        f"Its answer becomes superseded; its dependencies stay.\n\n"
        f"[cyan]{len(affected)}[/] decided descendant(s) rest on it and become "
        f"PROVISIONAL:\n  {', '.join(affected) or 'none'}"
        + (f"\n\n[cyan]{len(stalled)}[/] unfinished task(s) rest on a premise "
           f"under review:\n  {', '.join(stalled)}" if stalled else ""),
        title=f"reopen {vid}", border_style="magenta",
    ))
    if not yes:
        if not _interactive():
            # The panel above has already been printed, so the propagation is
            # reported either way — what is missing is somebody to accept it.
            con.print("[red]missing --yes[/]\n"
                      "[dim]not a terminal, so the propagation above cannot be "
                      "confirmed interactively[/]")
            raise typer.Exit(2)
        if not typer.confirm("Stage this?", default=True):
            raise typer.Exit()
    pending.stage_all(ops, against=eff)
    con.print(f"[green]staged[/] {len(ops)} op(s)")
    _warn_stuck()


@app.command(rich_help_panel=RECORD)
def add(
    vid: str = typer.Option(None, "--id"),
    title: str = typer.Option(None, "--title", "-t"),
    area: str = typer.Option(None, "--area"),
    after: str = typer.Option(None, "--after", help="parent that opens this"),
    status: str = typer.Option("OPEN", "--status"),
    note: str = typer.Option(None, "--note", "-n",
                             help="what is undecided, and why"),
    edit: bool = typer.Option(None, "--edit/--no-edit", "-e",
                              help="Compose in $EDITOR (default: emacs)."),
) -> None:
    """Stage a new decision vertex."""
    g = _g()
    # The effective graph: `--after` may name a vertex whose add is staged but
    # not applied, and an id already staged is as taken as an id in the store.
    eff = _eff(g)

    if _wants_editor(edit):
        seed = {k: v for k, v in (
            ("id", vid), ("title", title), ("area", area), ("status", status),
            ("note", note),
            ("after", [x.strip() for x in after.split(",") if x.strip()] if after else None),
        ) if v}
        ops = _compose(eff, "add_vertex", seed=seed)
        # The editor was the one staging route with no stage-time guard: the
        # flag path below runs `_status_legal` and the web app runs
        # `pending.vet`, and this ran neither, so a buffer could stage an op
        # that could never apply — in a tray every other writer shares. F30.
        _vet_all(eff, ops)
        pending.stage_all(ops, against=eff)
        con.print(f"[green]staged[/] {len(ops)} op(s) — add {ops[0]['id']}")
        _warn_stuck()
        return

    # Required only without --edit; typer cannot express that, so it is checked
    # here. Exit 2 keeps the shape of typer's own missing-option failure.
    missing = [flag for flag, val in (("--id", vid), ("--title", title),
                                      ("--area", area)) if not val]
    if missing:
        con.print(f"[red]missing option(s): {', '.join(missing)}[/]\n"
                  f"[dim]give them as flags, or use `dg add --edit`[/]")
        raise typer.Exit(2)
    if vid in eff.vertices:
        con.print(f"[red]{vid} already exists"
                  + (""
                     if vid in g.vertices
                     else " in the staging area — `dg pending` to review")
                  + "[/]")
        raise typer.Exit(1)
    if area not in eff.areas:
        con.print(f"[red]unknown area. one of: "
                  f"{', '.join(_x(a) for a in eff.areas)}[/]")
        raise typer.Exit(1)
    # `decide` validates its targets before staging; this does too, so that a
    # typo is reported by the command that contains it rather than by `apply`
    # two commands later.
    parents = [x.strip() for x in after.split(",") if x.strip()] if after else []
    unknown = [x for x in parents if x not in eff.vertices]
    if unknown:
        con.print(f"[red]unknown parent(s): {', '.join(unknown)}[/]")
        raise typer.Exit(1)
    if not _status_legal(eff, status, of=vid):
        con.print(f"[red]illegal status {_x(status)}[/]\n"
                  f"[dim]one of {', '.join(sorted(SIMPLE_STATUSES))}, "
                  f"or BLOCKED:<existing id>[/]")
        raise typer.Exit(1)
    # A block is a dependency, so its edge belongs in the tray beside the
    # vertex rather than only materialising at apply time — `dg pending` is
    # read by a person, and structure that appears from nowhere cannot be
    # reviewed. `_apply_one` adds it regardless, and `add_edge` unions, so
    # naming a blocker already given in --after changes nothing.
    if status.startswith("BLOCKED:"):
        blocker = status.split(":", 1)[1]      # `_status_legal` proved it exists
        if blocker not in parents:
            parents.append(blocker)
    op = {"op": "add_vertex", "id": vid, "title": title,
          "area": area, "status": status}
    if note:
        op["note"] = note
    # One write, and the site that most needed it: a vertex staged without its
    # edges is only a `no_orphans` warning, so unlike every other group here a
    # half-staged one could be applied and pass.
    pending.stage_all([op] + [{"op": "add_edge", "from": p, "to": [vid]}
                              for p in parents], against=eff)
    con.print(f"[green]staged[/] add {vid}")
    _warn_stuck()


def _archived(store: pathlib.Path) -> str | None:
    """Why this store is *not* safely removable from, or None if it is.

    Removal is the one act with no record of its own: `DROPPED` keeps the task
    and the reason, a superseded edge keeps the answer, but a removed vertex
    leaves nothing behind — that is what it means. Git is the archive, and this
    makes the archive real rather than assumed. Uncommitted changes to the
    store mean `git log -p` would not recover what is about to go.

    Any git trouble reads as "not a repository", the conservative direction:
    the point is refusing when there is no archive, so an unanswerable question
    about the archive is a refusal too.
    """
    from dgraph.gate import _git
    top = _git(store.parent, "rev-parse", "--show-toplevel")
    if top is None:
        return "this is not a git repository, so nothing would record what is removed"
    dirty = _git(store.parent, "status", "--porcelain", "--", str(store))
    if dirty is None:
        return "git could not report on the store, so the archive cannot be relied on"
    if dirty.strip():
        return f"{store.name} has uncommitted changes, so `git log -p` would not recover what is removed"
    return None


def _removal_mode(splice: bool, into: str | None) -> str:
    if splice and into:
        con.print("[red]--splice and --into say different things[/]\n"
                  "[dim]--splice joins what it sat between; --into moves its "
                  "edges onto another node[/]")
        raise typer.Exit(2)
    return "splice" if splice else "into" if into else "sever"


def _sanction(what: str, lines: list[str], yes: bool) -> None:
    """Show a removal's blast radius and get a human to agree to it.

    The second belt. `dg gate` answers `ask` on these commands so a host puts
    them to a person before they run at all; this is what the person then sees,
    and what an agent has to pass `--yes` to get past. Neither alone is enough:
    the gate can be switched off, and `--yes` is a flag a model can write.
    """
    con.print(f"[bold]{what}[/]")
    for line in lines:
        con.print(f"  {line}")
    if yes:
        return
    if not _interactive():
        con.print("[red]refused — removal is not reversible from inside this "
                  "tool[/]\n[dim]re-run with --yes once the above is what you "
                  "mean[/]")
        raise typer.Exit(2)
    if not typer.confirm("proceed?", default=False):
        con.print("[dim]nothing staged[/]")
        raise typer.Exit(1)


@app.command(rich_help_panel=RECORD)
def dep(
    vid: str,
    after: str = typer.Option(..., "--after",
                              help="comma-separated premises this rests on"),
) -> None:
    """Record that this decision rests on others.

    `dg add --after` could only say this when the vertex was created, so a
    dependency between two questions that already exist had no way in — which
    is exactly the state `dg import-md` leaves behind, since it builds a whole
    graph of pre-existing vertices and reports the edges it could not infer.

    Adding a premise to an answered question is allowed, and is not the mirror
    of `dg undep` refusing to remove one. Recording that an answer *also*
    opened something is additive: the answer, its source and its falsifier all
    still stand. Removing a target says the answer never opened it, which
    contradicts what was written down. (Making a terminal answer non-terminal
    still needs a falsifier — `dg apply` says so.)
    """
    g = _g()
    eff = _eff(g)
    if vid not in eff.vertices:
        con.print(f"[red]unknown vertex {vid}[/]")
        raise typer.Exit(1)
    parents = [x.strip() for x in after.split(",") if x.strip()]
    unknown = [p for p in parents if p not in eff.vertices]
    if unknown:
        con.print(f"[red]unknown premise(s): {', '.join(unknown)}[/]")
        raise typer.Exit(1)
    if vid in parents:
        con.print(f"[red]{vid} cannot rest on itself[/]")
        raise typer.Exit(1)
    already = [p for p in parents if p in eff.depends(vid)]
    fresh = [p for p in parents if p not in already]
    if already:
        con.print(f"[dim]already rests on {', '.join(already)}[/]")
    pending.stage_all([{"op": "add_edge", "from": p, "to": [vid]}
                       for p in fresh], against=eff)
    # Nothing staged is not the same as staged, and saying otherwise sends the
    # reader to `dg pending` looking for an op that is not there.
    if not fresh:
        return
    con.print(f"[green]staged[/] {vid} rests on {', '.join(fresh)}")
    _warn_stuck()


@app.command(rich_help_panel=RECORD)
def undep(
    vid: str,
    after: str = typer.Option(..., "--after",
                              help="comma-separated premises to drop"),
) -> None:
    """Remove a dependency. The undo `dg add --after` never had.

    Until this existed, a dependency recorded against the wrong parent could
    only be repaired by editing `decisions.json` by hand — the exit every other
    guard in this tool works to make unnecessary. `dg task undep` has always
    had it; the decision store had the add half and no remove half.

    Only a **bare** edge, one whose source has not been decided. A decided
    edge's targets are part of its answer, so dropping one claims the answer no
    longer opens that question — reopen first, and decide again meaning it.
    """
    g = _g()
    eff = _eff(g)
    if vid not in eff.vertices:
        con.print(f"[red]unknown vertex {vid}[/]")
        raise typer.Exit(1)
    parents = [x.strip() for x in after.split(",") if x.strip()]
    held = eff.depends(vid)
    unknown = [p for p in parents if p not in held]
    if unknown:
        con.print(f"[red]{vid} does not rest on {', '.join(unknown)}[/]\n"
                  f"[dim]`dg node {vid}` lists its premises[/]")
        raise typer.Exit(1)
    decided = [p for p in parents
               if (e := eff.active_edge(p)) is not None and e.decided]
    if decided:
        con.print(f"[red]{', '.join(decided)} is decided, and its targets are "
                  f"part of that answer[/]\n"
                  f"[dim]`dg reopen {decided[0]}` first — that strips the "
                  f"payload and leaves the dependency editable — then remove "
                  f"the edge and decide again with the targets you mean[/]")
        raise typer.Exit(1)

    ops = [{"op": "remove_edge", "from": p, "to": [vid]} for p in parents]

    # The one repair with no judgement in it, so it is made rather than asked
    # about: `BLOCKED:P` asserts a dependency on P, and the edge carrying that
    # dependency is being removed, so the status is false the moment this
    # applies. `dg confirm` already releases blocked vertices to OPEN the same
    # way when their blocker settles. Staged in the same batch, because
    # `block_is_a_premise` is an error and `apply_all` would otherwise refuse
    # the whole thing with a message about an invariant the user did not break.
    #
    # In the same *write*, too, and that is not the same claim. This was two
    # `stage` calls for a while, with the comment above already asserting
    # otherwise: safe only because the intermediate happens to be refused by
    # `block_is_a_premise` — the coincidence F18 declined to keep relying on —
    # and paid for by whichever other writer applied in the window, whose whole
    # batch was refused over an invariant they had not broken. Audit F31.
    blocker = eff.vertices[vid].blocker
    released = (eff.vertices[vid].base_status == "BLOCKED"
                and blocker in parents)
    if released:
        ops.append({"op": "set_status", "vertex": vid, "status": "OPEN"})
    pending.stage_all(ops, against=eff)

    con.print(f"[green]staged[/] {vid} no longer rests on "
              f"{', '.join(parents)}")
    if released:
        con.print(f"[cyan]{vid} was BLOCKED:{blocker}[/] and is released to "
                  f"OPEN [dim]— the block asserted the dependency being "
                  f"removed[/]")

    # Reported, not repaired: each is a judgement the graph cannot make.
    after_eff = _eff(g)
    fresh = {(v.check, v.message) for v in after_eff.validate()} \
        - {(v.check, v.message) for v in eff.validate()}
    for check, message in sorted(fresh):
        con.print(f"[yellow]{check}[/] [dim]{_x(message)}[/]")
    _warn_stuck()


@app.command(rich_help_panel=RECORD)
def rm(
    vid: str,
    splice: bool = typer.Option(False, "--splice",
                                help="join its premises to what it opened"),
    into: str = typer.Option(None, "--into",
                             help="move its edges onto this vertex instead"),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirmation"),
) -> None:
    """Remove a decision. For a record made in error — nothing else.

    A question that turned out not to matter is `dg decide`d and made terminal;
    an answer that turned out wrong is `dg reopen`ed. Both keep the history,
    which is the most valuable thing this graph holds. Removal keeps nothing:
    it is for a vertex that should never have been written — a duplicate, an
    import artifact, a heading that was never a decision.

    By default it **severs**: the vertex goes and its edges go with it, leaving
    a state the validators already describe. `--splice` and `--into` instead
    **assert an edge nobody wrote**, joining what it sat between; both refuse
    to write into a decided answer, because attaching an answer to a question
    it never opened is the one claim this model must not manufacture.
    """
    mode = _removal_mode(splice, into)
    g = _g()
    eff = _eff(g)
    if vid not in eff.vertices:
        con.print(f"[red]unknown vertex {vid}[/]")
        raise typer.Exit(1)
    if into and into not in eff.vertices:
        con.print(f"[red]unknown vertex {into}[/]")
        raise typer.Exit(1)

    # Refused before anything is shown, because the repair is in the other
    # store and the two trays cannot apply as one batch. Left to `apply_all`,
    # this would be a `link_resolves` error about a file this command never
    # touched.
    holding = _tasks_naming(vid)
    if holding:
        con.print(f"[red]{len(holding)} task(s) name {vid}: "
                  f"{', '.join(holding)}[/]\n"
                  f"[dim]point them elsewhere first — `dg task link <T> "
                  f"--because <D>`, or `dg task unlink <T> --because`[/]")
        raise typer.Exit(1)

    blocked = _archived(project.find().store)
    if blocked:
        con.print(f"[red]refused — {blocked}[/]\n"
                  f"[dim]commit the store first; git is the only record of "
                  f"what a removal takes away[/]")
        raise typer.Exit(1)

    v = eff.vertices[vid]
    lines = [f"{_x(v.title)}  [{_x(v.area)}]  {v.status}"]
    parents, children = eff.depends(vid), eff.children(vid)
    lines.append(f"rests on   {', '.join(parents) or '—'}")
    lines.append(f"opens      {', '.join(children) or '—'}")
    if mode == "splice" and parents and children:
        lines.append(f"[yellow]asserts[/] "
                     + ", ".join(f"{p} → {c}" for p in parents
                                 for c in children))
    elif mode == "into":
        lines.append(f"[yellow]asserts[/] "
                     + ", ".join([f"{p} → {into}" for p in parents]
                                 + [f"{into} → {c}" for c in children]))
    if eff.history(vid):
        lines.append(f"[yellow]loses[/] {len(eff.history(vid))} superseded "
                     f"answer(s) — the record of how this changed")
    freed = sorted(o for o, x in eff.vertices.items() if x.blocker == vid)
    if freed:
        lines.append(f"releases   {', '.join(freed)} to OPEN")
    op = {"op": "remove_vertex", "vertex": vid, "mode": mode}
    if into:
        op["into"] = into
    # Vetted before the confirmation, not after: a `--splice` that would write
    # into a decided answer is refused by the op itself, and asking somebody to
    # approve a removal that cannot happen wastes the one decision this command
    # exists to put in front of them.
    try:
        pending.vet(eff, op)
    except pending.ApplyError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    _sanction(f"remove {vid} ({mode})", lines, yes)
    pending.stage(op, against=eff)
    con.print(f"[green]staged[/] remove {vid}")
    _warn_stuck()


def _tasks_naming(did: str) -> list[str]:
    """Tasks whose `because` or `evidence_for` points at this decision.

    Read through `cross`, like every other cross-graph reading. Silent where
    there is no task store, which is most projects.
    """
    proj = project.find()
    if not proj.has_tasks:
        return []
    try:
        tg = TaskGraph.load(proj.tasks)
        return sorted(set(cross.rests_on(tg, did)) | set(cross.evidence(tg, did)))
    except Exception:
        # A broken task store must not block a repair to the decision store;
        # `dg check` reports it separately. Erring toward allowing the removal
        # is safe here because `apply_all` still refuses a batch that would
        # leave a dangling link.
        return []


@app.command(name="pending", rich_help_panel=STAGE)
def pending_cmd(
    full: bool = typer.Option(False, "--full",
                              help="The table, with no detail clipped."),
) -> None:
    """What is staged but not yet applied.

    One line per op by default — its position, its short id, the op, what it is
    about, and the detail clipped. `--full` gives the table instead, which is
    the one to reach for when two answers read the same at the width above.
    """
    _tray(pending.load(), full, details=_PENDING_DETAIL, subject="vertex",
          heading="STAGED", title="Staged", column="Vertex",
          actions="`dg apply` to write, `dg drop <id>` to unstage",
          expand="dg pending --full")


def _tray(ops: list[dict], full: bool, *, details: dict, subject: str,
          heading: str, title: str, column: str, actions: str,
          expand: str) -> None:
    """One staging tray, listed or tabulated. Both trays read through here.

    The two used to be a table apiece, near-identical and free to drift; they
    are peers, and one of them compact while the other is box-drawn is exactly
    the asymmetry the rest of this file spends its comments removing. So the
    shape lives here once and the callers pass only what genuinely differs:
    which store's key names the subject, and which command unstages one op.
    """
    if not ops:
        con.print("[dim]nothing staged[/]")
        return
    (_tray_table if full else _tray_listing)(
        ops, details=details, subject=subject, heading=heading, title=title,
        column=column)
    con.print(f"[dim]{actions}[/]")
    if not full:
        con.print(compact.hint(expand, "the table, nothing clipped"))


def _tray_detail(o: dict, details: dict) -> str:
    """The detail cell for one staged op.

    `.get` with a fallback, never a direct index: this is what every stuck-tray
    message sends the reader to ("`dg pending` to review"), so it has to run on
    a tray that is stuck. An op kind nothing recognises — a hand-edit, a file
    from a newer `dg` — is exactly when somebody needs to see the row, and a
    traceback here leaves `dg clear` as the only exit. The same holds for a
    known kind missing a field, which is what a hand-edit also looks like.
    """
    return details.get(o.get("op"), _raw_op)(o)


def _tray_listing(ops: list[dict], *, details: dict, subject: str,
                  heading: str, title: str, column: str) -> None:
    """A tray as one line per op — the default, and the piped one.

    The position and the short id share the first column, and neither is ever
    clipped: they are the two ways to address an op, and a row you cannot
    `dg drop` is a row the review cannot act on. `id` beside `#`, not instead
    of it — the index is what a single writer reasons about and what the tool's
    own messages name, the id is what survives another writer's apply. See
    `pending.resolve`.

    The op kind and the subject share the second, padded so both read down.
    What is left goes to the detail, which is the part worth clipping: an
    answer or a title, whose first words say which op this is.
    """
    con.print(f"[bold]{heading}[/]  {len(ops)} op(s)")
    kinds = [_x(o.get("op") or "?") for o in ops]
    kw = max(len(compact.visible(k)) for k in kinds)
    iw = len(str(len(ops) - 1))
    _say(compact.listing(
        [(f"{i:>{iw}}  {o.get('ref') or '—'}",
          compact.pad(k, kw) + "  " + _op_subject(o, subject),
          _tray_detail(o, details), "")
         for i, (o, k) in enumerate(zip(ops, kinds))],
        width=_width(), markup=True))


def _tray_table(ops: list[dict], *, details: dict, subject: str,
                heading: str, title: str, column: str) -> None:
    """The detailed tray: every detail in full, every field its own column."""
    t = Table(header_style="bold", title=title)
    for c in ("#", "id", "Op", column, "Detail"):
        t.add_column(c)
    for i, o in enumerate(ops):
        t.add_row(str(i), o.get("ref") or "—", _x(o.get("op") or "?"),
                  _op_subject(o, subject), _tray_detail(o, details))
    con.print(t)


def _raw_op(o: dict) -> str:
    """The fallback detail: the op itself, so an unreadable row is still a row."""
    return _x(json.dumps(o, ensure_ascii=False))


def _op_subject(o: dict, first: str) -> str:
    """What a staged op is *about*, for the listing's subject column.

    `first` is the store's own key — `vertex` for a decision op, `task` for a
    task one — and the rest is shared: an edge op names its `from`, an add names
    the `id` it is about to create.

    One implementation because there are three callers now rather than two: both
    `pending` tables and `_op_summary` below, which has to pick the same subject
    the table showed or a drop confirmation would describe an op differently
    from the row that named it.
    """
    return o.get(first) or o.get("from") or o.get("id") or "—"


def _op_summary(o: dict, details: dict, subject: str) -> str:
    """One line naming a staged op — the table's three columns, run together.

    Printed by `dg drop` and `dg task drop-op` to say *what* was unstaged rather
    than only which index was given. Built from the same detail map the listing
    renders, so the two cannot drift: the row a person reviewed and the line
    confirming its removal are the same description.

    `.get` on the kind and on every field, like the tables: this is reached with
    whatever was in the tray, including a hand-edit or an op from a newer `dg`,
    and a traceback while unstaging would leave `dg clear` as the only exit.
    """
    detail = details.get(o.get("op"), _raw_op)(o)
    return (f"{_x(o.get('op') or '?')} {_op_subject(o, subject)}"
            + (f"  {detail}" if detail else ""))


_PENDING_DETAIL = {
    "close": lambda o: _x((o.get("answer") or "")[:70]),
    "reopen": lambda o: _x(o.get("why", "")),
    "set_status": lambda o: f"→ {_x(o.get('status', '?'))}"
    + (f"  [dim](from {_x(o['derived_from'])})[/]" if o.get("derived_from") else ""),
    "add_vertex": lambda o: _x(o.get("title", "")),
    "add_edge": lambda o: "→ " + ", ".join(o.get("to", [])),
    "remove_edge": lambda o: "✗ " + ", ".join(o.get("to", [])),
    "remove_vertex": lambda o: f"✗ removed ({_x(o.get('mode', 'sever'))}"
                              + (f" → {_x(o['into'])}" if o.get("into") else "")
                              + ")",
}


@app.command(name="edit", rich_help_panel=STAGE)
def edit_cmd(ref: str = typer.Argument(..., metavar="ID_OR_INDEX",
                                       help="the op's id, or its position")) -> None:
    """Revise a staged op in the editor, in place.

    Replaces rather than re-stages: re-staging would move the op to the end of
    the batch, and any derived `set_status` would then apply before the change it
    was derived from.
    """
    ops = pending.load()
    try:
        i = pending.resolve(ops, ref)
    except LookupError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    g = _g()
    op = ops[i]
    kind = op.get("op")
    if kind not in editor.RENDERERS:
        con.print(f"[red]op {i} is {kind} — derived, not composed[/]\n"
                  f"[dim]`dg drop {op.get('ref') or i}` to remove it[/]")
        raise typer.Exit(1)
    # Deliberately not re-expanded: the derived ops are already staged, and
    # propagation depends only on which vertex is settled — never on the answer
    # text or the target list — so revising either cannot invalidate them.
    # Rendered against the batch *without* op i: the other staged ops are
    # context this revision should see, but the op being replaced is not.
    eff = _eff(g, skip=i)
    new = _compose(eff, kind, vertex=op.get("vertex"), index=i, op=op)
    # The whole group, not `new[0]`. An `add_vertex` comes back as the vertex
    # plus one `add_edge` per parent named in the buffer, and taking only the
    # first discarded every structural change the edit made — silently, under a
    # message saying it had worked. Audit F26.
    #
    # The edges the previous version staged go with it, or the tray would hold
    # both readings of what the vertex rests on and apply their union. Matched
    # under the lock by what they attach, never by index: see `replace_group`.
    # Addressed by the op's own id, not by the index it had when the tray was
    # read: an editor session lasts minutes, and another writer applying in that
    # window moves every position past what it removed. The id is resolved
    # against the tray `replace_group` is about to write. Audit F29.
    _vet_all(eff, new)
    pending.replace_group(op.get("ref") or i, new, against=eff,
                          supersede=_supersedes(kind, op))
    con.print(f"[green]updated[/] op {i} — {len(new)} op(s), review with "
              f"`dg pending`")


def _supersedes(kind: str, op: dict):
    """What a revision of `op` takes out of the tray, or None for "nothing".

    Only an `add_vertex` supersedes anything: `close` and `reopen` each parse
    back to exactly one op, so a revision of either is a swap and nothing else.
    A vertex's parents are edges, staged as separate ops, and re-stating them in
    the buffer has to retract the old ones.

    An `add_edge` naming other vertices as well keeps them. Nothing in the tool
    stages one — every producer writes a single target — but `_apply_one` unions
    targets and the web API takes an op as data, so dropping such an op whole
    would lose an attachment the edit never mentioned.
    """
    if kind != "add_vertex":
        return None
    vid = op.get("id")

    def supersede(other: dict) -> dict | None:
        if other.get("op") != "add_edge" or vid not in (other.get("to") or []):
            return other
        rest = [t for t in other["to"] if t != vid]
        return {**other, "to": rest} if rest else None

    return supersede


@app.command(rich_help_panel=STORE)
def export(
    vid: str = typer.Argument(None, help="Scope to one decision"),
) -> None:
    """Dump the graph as JSON. Read-only; what `dgraph.el` reads for navigation."""
    from dgraph.server import graph_payload
    g = _g()
    payload = graph_payload(g)
    if vid:
        if vid not in g.vertices:
            con.print(f"[red]unknown vertex {vid}[/]")
            raise typer.Exit(1)
        payload = {
            "areas": payload["areas"],
            "vertices": [v for v in payload["vertices"] if v["id"] == vid],
            "edges": [e for e in payload["edges"]
                      if e["from"] == vid or vid in e.get("to", [])],
            "derived": {vid: payload["derived"][vid]},
            "frontier": payload["frontier"],
            "ancestors": sorted(g.ancestors(vid)),
        }
    # plain print, never con.print: rich soft-wraps at $COLUMNS and would corrupt
    # the JSON for whatever is parsing it.
    print(json.dumps(payload, ensure_ascii=False))


@app.command(rich_help_panel=STAGE)
def drop(ref: str = typer.Argument(..., metavar="ID_OR_INDEX",
                                   help="the op's id, or its position")) -> None:
    """Unstage one op, by its id or its position, and say which one it was.

    **Quote the id when anything else may be writing.** The tray is shared —
    `commands/dg-serve.md` tells a user to work in the browser and a terminal at
    once — and `pending.discard` takes applied ops out by value from wherever
    they sit, which is what keeps a concurrent writer's work staged. Every index
    past them then shifts, so a position read off an earlier `dg pending` can
    address a different op by the time it is used. An id does not move: it is
    resolved against the tray this command is about to write. Audit F29.

    The position still works, because it is what a single writer sees and
    reasons about. The two can never be confused — an id is letters only.
    """
    try:
        gone = pending.drop(ref)
    except LookupError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    con.print(f"[green]dropped[/] {_x(ref)}: "
              f"{_op_summary(gone, _PENDING_DETAIL, 'vertex')}")


@app.command(rich_help_panel=STAGE)
def clear() -> None:
    """Unstage everything."""
    pending.clear()
    con.print("[green]cleared[/]")


@app.command(rich_help_panel=STAGE)
def apply(dry_run: bool = typer.Option(False, "--dry-run", "-n")) -> None:
    """Validate everything staged and write it.

    One verb for both stores, but two independent batches: each is validated
    and written on its own, so a batch that cannot be applied can never stop one
    that can — in **either** direction, and for every reason a batch fails. A
    refusal used to exit here and take the other batch with it; so, later and
    less visibly, did a *missing store*, because the two helpers loaded it
    through `_g()`/`_tg()`, which exit the process. Both are gone: each helper
    reports its own store's absence and returns, and this function owns the one
    exit code for the pair.
    """
    proj = project.find()
    ops = _staged_ops(proj.pending, "dg clear")
    task_ops = _staged_ops(proj.task_pending, "dg task clear")
    if ops is None and task_ops is None:
        raise typer.Exit(1)
    if not ops and not task_ops:
        con.print("[dim]nothing staged[/]")
        return
    ok = True
    # One lock span across both batches, not one per batch. They stay
    # independent — either can be refused while the other is written, which is
    # the whole point of two `_apply_*` calls — but the *pair on disk* is never
    # observable half-applied by another writer, or by a reader that takes the
    # same lock. A dry run writes nothing and needs none. Audit F27.
    with contextlib.nullcontext() if dry_run else applying.writing(proj):
        if ops:
            ok = _apply_decisions(ops, dry_run) and ok
        if task_ops:
            ok = _apply_tasks(task_ops, dry_run) and ok
    if not ok or ops is None or task_ops is None:
        # Something was refused, or one tray could not even be read. Exit
        # nonzero so nothing downstream reads this as "everything staged is now
        # written" — but only after both batches have had their turn.
        raise typer.Exit(1)


def _staged_ops(path: pathlib.Path, discard: str) -> list[dict] | None:
    """One staging tray, or `None` if it cannot be read.

    Read separately and reported separately, because the two batches are
    independent: a task tray nobody can parse must not stop a decision batch
    that would apply cleanly — which is exactly what an unguarded load did.
    """
    try:
        return pending.load(path)
    except Exception as exc:
        con.print(f"[red]{path.name} could not be read[/]\n{_x(exc)}\n"
                  f"[dim]inspect it by hand, or discard it with `{discard}`[/]")
        return None


#: A store that will not load. Narrow on purpose: `apply_all` raises
#: `ApplyError` for a batch that does not fit, and these are the ways the file
#: underneath it fails instead — absent or unreadable (`OSError`), malformed
#: JSON or a duplicate id (`ValueError`), a field the schema does not have
#: (`TypeError`). Caught rather than allowed to propagate because a traceback
#: out of one batch takes the other one with it, which is the whole point of
#: `_apply_decisions` and `_apply_tasks` returning instead of exiting.
UNREADABLE = (OSError, ValueError, TypeError)


def _report_drift(r: applying.Result) -> None:
    """Say what moved under this batch while it was staged.

    Before the ✓, because it is context for the result rather than a footnote
    to it. Never a refusal — the invariants already refuse the case that
    matters, so what reaches here is a batch that applies cleanly and rests on
    something that changed quietly. See `pending.drift`.
    """
    for d in r.drift:
        con.print(f"[yellow]·[/] {_x(pending.describe(d))}")
    if r.drift:
        con.print(f"[dim]`dg node <id>` shows what it says now; nothing was "
                  f"applied on the old reading[/]")


def _apply_decisions(ops: list[dict], dry_run: bool) -> bool:
    """Write the decision batch. Reports, and says whether it worked.

    Returns rather than exiting so the task batch still gets its turn: the two
    are independent, and `apply` owns the exit code for the pair. That is why
    the store is *not* loaded through `_g()` here: it exits the process, and an
    exit from inside this function is exactly the failure it exists to prevent
    — a decision store that is absent used to take an applicable task batch
    down with it, which is the property `apply`'s docstring claims in both
    directions. Both trays are gitignored, so a `git checkout` of a branch
    without `decisions.json` reaches this with work staged in the other one.

    Nothing is passed to `applying` either: it re-reads under the lock on the
    write path regardless, and reading it here first would be the second read
    the parameter exists to avoid.
    """
    proj = project.find()
    if not proj.has_decisions:
        con.print(f"[red]✗ {len(ops)} decision op(s) not applied — no "
                  f"{project.STORE_NAME} under {proj.root}[/]\n"
                  f"[dim]`dg init` there, or `dg clear` to discard them[/]")
        return False
    try:
        r = applying.apply_decisions(ops, dry_run)
    except pending.Collision as exc:
        # Not a red ✗, and not "nothing written": the work *is* written — the
        # other writer wrote it — and only this apply did nothing. The headline
        # is what a model acts on, so a refusal that reads as loss is how one
        # comes to re-stage under a fresh id and put two vertices behind one
        # question. Still nonzero, because the rest of the batch was refused
        # with the duplicate and has not landed.
        con.print(f"[yellow]· refused — another writer got there first[/]\n"
                  f"{_x(exc)}\n"
                  f"[dim]`dg pending` to review; `dg drop <id>` on the duplicate, "
                  f"then `dg apply` again for the rest of the batch[/]")
        return False
    except pending.ApplyError as exc:
        con.print(f"[red]✗ aborted, nothing written[/]\n{_x(exc)}\n"
                  f"[dim]`dg pending` to review; `dg drop <id>` to unstage[/]")
        return False
    except UNREADABLE as exc:
        con.print(f"[red]✗ nothing written — {project.STORE_NAME} could not "
                  f"be read[/]\n{_x(exc)}\n"
                  f"[dim]`dg check` says what is wrong with it; the staged ops "
                  f"are untouched[/]")
        return False
    _report_drift(r)
    if r.dry_run:
        con.print(f"[green]✓[/] {r.applied} decision op(s) would apply cleanly")
        return True
    if r.view_error:
        con.print(f"[yellow]applied {r.applied} op(s) → {r.store}, but "
                  f"{r.view} could not be written[/]\n{_x(r.view_error)}\n"
                  f"[dim]`dg render` regenerates it once the cause is fixed[/]")
        return False
    con.print(f"[green]✓[/] applied {r.applied} op(s) → {r.store} + {r.view}")
    return True


def _apply_tasks(ops: list[dict], dry_run: bool) -> bool:
    """The same for the task batch, and `_tg()` is skipped for the same reason
    `_g()` is. See `_apply_decisions`."""
    proj = project.find()
    if not proj.has_tasks:
        con.print(f"[red]✗ {len(ops)} task op(s) not applied — no "
                  f"{project.TASKS_NAME} under {proj.root}[/]\n"
                  f"[dim]`dg task init` there, or `dg task clear` to discard "
                  f"them[/]")
        return False
    try:
        r = applying.apply_tasks(ops, dry_run)
    except pending.Collision as exc:
        con.print(f"[yellow]· task ops refused — another writer got there "
                  f"first[/]\n{_x(exc)}\n"
                  f"[dim]`dg task pending` to review; `dg task drop-op <id>` on "
                  f"the duplicate, then `dg apply` again[/]")
        return False
    except pending.ApplyError as exc:
        con.print(f"[red]✗ task ops aborted, nothing written[/]\n{_x(exc)}\n"
                  f"[dim]`dg task pending` to review; `dg task drop-op <id>` to "
                  f"unstage[/]")
        return False
    except UNREADABLE as exc:
        con.print(f"[red]✗ nothing written — {project.TASKS_NAME} could not "
                  f"be read[/]\n{_x(exc)}\n"
                  f"[dim]`dg check` says what is wrong with it; the staged ops "
                  f"are untouched[/]")
        return False
    if r.dry_run:
        con.print(f"[green]✓[/] {r.applied} task op(s) would apply cleanly")
        return True
    if r.view_error:
        con.print(f"[yellow]applied {r.applied} op(s) → {r.store}, but "
                  f"{r.view} could not be written[/]\n{_x(r.view_error)}\n"
                  f"[dim]`dg task render` regenerates it[/]")
        return False
    con.print(f"[green]✓[/] applied {r.applied} op(s) → {r.store} + {r.view}")
    return True


@app.command(name="render", rich_help_panel=HONEST)
def render_cmd() -> None:
    """Regenerate decision-graph.md from the store."""
    render.write(_g())
    con.print(f"[green]✓[/] wrote {project.find().view}")


# ---- tasks ---------------------------------------------------------------
#
# A separate store, a separate staging file and a separate view, so that work
# can never be mistaken for a decision — the barrier is structural rather than a
# convention somebody has to remember.

task_app = typer.Typer(
    cls=_ordered(TASK_LAYOUT),
    add_completion=False,
    help="Track the work a project has to do. tasks.json is the source of "
         "truth; tasks.md is generated from it.",
)
app.add_typer(task_app, name="task", rich_help_panel=WORK)

TASK_STYLE = {
    "TODO": "bold red",
    "DOING": "yellow",
    "DONE": "green",
    "DROPPED": "dim",
}


def _tg() -> TaskGraph:
    proj = project.find()
    if not proj.has_tasks:
        con.print(f"[red]no {project.TASKS_NAME} under {proj.root}[/]\n"
                  f"[dim]run `dg task init` there, or pass --project PATH[/]")
        raise typer.Exit(2)
    return TaskGraph.load(proj.tasks)


def _teff(tg: TaskGraph, skip: int | None = None) -> TaskGraph:
    """The task store plus its staged ops — what task guards judge against."""
    try:
        return task_pending.preview(tg, skip=skip)
    except pending.ApplyError as exc:
        # `dg task drop-op <id>`, not `dg task drop <id>`: the latter exists and
        # abandons a task called N, which is not what a stuck tray needs.
        con.print(f"[red]the staged task ops no longer apply cleanly[/]\n{_x(exc)}\n"
                  f"[dim]`dg task pending` to review; `dg task drop-op <id>` to "
                  f"fix[/]")
        raise typer.Exit(1) from None


def _tstage(op: dict) -> None:
    """Vet an op against the effective task graph, then stage it. The singular
    of `_tstage_all`, for a command whose whole change really is one op."""
    _tstage_all([op])


def _tstage_all(ops: list[dict]) -> None:
    """Vet a group against the effective task graph, then stage it as one write.

    `pending.stage_all`'s argument, applied to the store that did not have it.
    Four task commands build groups — `add --after`, `dep`, `undep`, `drop
    --drop-too` — and each used to stage one op per iteration, one lock and one
    load-modify-save apiece. Between two of those the tray holds half a group.

    On the decision side that was safe by coincidence: a half-applied
    propagation leaves a graph `validate` calls invalid, so an apply landing
    mid-group was refused. **Here there is no such coincidence.** This store has
    no orphan check, no stored blocked-ness and no cross-task completion rule,
    so half a group validates clean and is written — `dg task add --after T01`
    could land the task alone, and it reads as startable to everything that asks.
    Audit F28.

    The whole group is vetted before any of it is staged (`vet_all`), so a
    refusal leaves the tray as it was rather than holding the first half of
    something the command then gave up on.
    """
    if not ops:
        return
    try:
        task_pending.vet_all(_teff(_tg()), ops)
    except pending.ApplyError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    pending.stage_all(ops, task_pending.path())


def _twarn_stuck() -> None:
    """After staging: say now if `dg apply` would refuse the task batch.

    `_warn_stuck` for the other store. A warning, never a refusal — a batch can
    be legitimately transitional mid-way (a task finished before the
    prerequisite whose own completion is still to come) — but the user must not
    learn several commands later which op to blame. Called once per command
    rather than once per op, since one command may stage several.
    """
    try:
        # `staged_decisions=True`: this runs before either batch has, and
        # `dg apply` writes decisions first, so a task op naming a decision
        # that is staged alongside it will apply cleanly. Warning about it
        # here would be a message that the very next command disproves.
        task_pending.apply_all(TaskGraph.load(), pending.load(task_pending.path()),
                               cross.guard_tasks(staged_decisions=True))
    except pending.ApplyError as exc:
        con.print(f"[yellow]note: as staged, `dg apply` would currently refuse "
                  f"this task batch[/]\n[dim]{_x(exc)}[/]\n"
                  f"[dim]staging what is missing, or `dg task drop-op <id>`, "
                  f"clears it[/]")


@task_app.command("init", rich_help_panel=T_STORE)
def task_init(
    areas: str = typer.Option(
        "General", "--areas",
        help="comma-separated area names, used to group the rendered view",
    ),
) -> None:
    """Start an empty task graph in this directory."""
    proj = project.find()
    if proj.has_tasks:
        con.print(f"[red]{proj.tasks} already exists[/]")
        raise typer.Exit(1)
    tg = TaskGraph(areas=[a.strip() for a in areas.split(",") if a.strip()])
    tg.save(proj.tasks)
    task_render.write(tg, proj.task_view)
    con.print(f"[green]✓[/] created {proj.tasks} and {proj.task_view.name}")
    _report_ignored(proj)


#: How each edge kind reads in a CLI message, phrased from the *new* task's
#: side — every flag below says "this task, relative to those". One table
#: because the two relations share no verb, and a message loose enough to cover
#: both would describe neither: `--after` makes this task wait, and
#: `--discovered-during` deliberately does not.
_REL = {
    "precedes": {"flag": "--after", "reads": "after",
                 "self": "cannot come after itself"},
    "prompted": {"flag": "--discovered-during", "reads": "discovered during",
                 "self": "cannot be discovered during itself"},
}


def _held(tg: TaskGraph, tid: str, kind: str) -> list[str]:
    """What already relates to `tid` this way — the reverse of the stored edge."""
    return (tg.prerequisites(tid) if kind == "precedes"
            else tg.discovered_during(tid))


def _relation_targets(tg: TaskGraph, tid: str, raw: str, kind: str) -> list[str]:
    """Parse and check one relation spec, staging nothing. Exits on anything bad.

    Separate from the staging below so a command taking two specs can check
    both before writing either: `dg task add --after X --discovered-during ???`
    must not leave the new task staged and the batch half built.
    """
    others = [x.strip() for x in raw.split(",") if x.strip()]
    unknown = [o for o in others if o not in tg.tasks]
    if unknown:
        con.print(f"[red]unknown task(s): {', '.join(unknown)}[/]")
        raise typer.Exit(1)
    if tid in others:
        con.print(f"[red]{tid} {_REL[kind]['self']}[/]")
        raise typer.Exit(1)
    return others


def _relation_ops(tg: TaskGraph, tid: str, others: list[str],
                  kind: str) -> tuple[list[dict], list[str], list[str]]:
    """The ops for one kind of edge, and what was fresh versus already held.

    Computing the ops is split from staging and from saying so, the way
    `_relation_targets` already split *checking* from staging — and for the same
    reason one step further on: a command taking two relation specs has to be
    able to build both groups before writing either, or the tray holds half of
    what was asked for. See `_tstage_all`.
    """
    already = [o for o in others if o in _held(tg, tid, kind)]
    fresh = [o for o in others if o not in already]
    return ([{"op": "add_dep", "from": o, "to": [tid], "kind": kind}
             for o in fresh], fresh, already)


def _say_relation(tid: str, kind: str, fresh: list[str],
                  already: list[str]) -> None:
    """What `_relation_ops` staged, said after the write."""
    rel = _REL[kind]
    if already:
        con.print(f"[dim]already {rel['reads']} {', '.join(already)}[/]")
    # See `dep`: claiming to have staged nothing sends the reader to
    # `dg task pending` looking for an op that is not there.
    if fresh:
        con.print(f"[green]staged[/] {tid} {rel['reads']} {', '.join(fresh)}")


@task_app.command("add", rich_help_panel=T_RECORD)
def task_add(
    tid: str = typer.Option(None, "--id"),
    title: str = typer.Option(None, "--title", "-t"),
    area: str = typer.Option(None, "--area"),
    after: str = typer.Option(None, "--after",
                              help="comma-separated tasks that must come first"),
    discovered_during: str = typer.Option(
        None, "--discovered-during",
        help="comma-separated tasks whose doing turned this one up"),
    because: str = typer.Option(None, "--because",
                                help="the decision this work exists because of"),
    evidence_for: str = typer.Option(None, "--evidence-for",
                                     help="the decision this work will inform"),
    note: str = typer.Option(None, "--note", "-n"),
) -> None:
    """Stage a new task."""
    tg = _teff(_tg())
    missing = [f for f, val in (("--id", tid), ("--title", title),
                                ("--area", area)) if not val]
    if missing:
        con.print(f"[red]missing option(s): {', '.join(missing)}[/]")
        raise typer.Exit(2)
    # Checked here rather than at apply, so a typo is reported by the command
    # that contains it. The decision side only catches this at validate time.
    if not TASK_ID_RE.fullmatch(tid):
        con.print(f"[red]malformed id {_x(tid)} — expected something like T07[/]\n"
                  f"[dim]decisions are D-ids and live in a different store[/]")
        raise typer.Exit(1)
    if tid in tg.tasks:
        con.print(f"[red]{tid} already exists"
                  + ("" if tid in _tg().tasks else " in the staging area") + "[/]")
        raise typer.Exit(1)
    if area not in tg.areas:
        con.print(f"[red]unknown area. one of: "
                  f"{', '.join(_x(a) for a in tg.areas)}[/]")
        raise typer.Exit(1)
    op = {"op": "add_task", "id": tid, "title": title, "area": area}
    if note:
        op["note"] = note
    if because:
        op["because"] = _resolve_premise(because)
    if evidence_for:
        op["evidence_for"] = _resolve_premise(evidence_for)
    # Both relation specs are checked before anything is staged, so a typo in
    # the second one does not leave the new task sitting in the tray alone.
    rels = [(_relation_targets(tg, tid, raw, kind), kind)
            for raw, kind in ((after, "precedes"),
                              (discovered_during, "prompted")) if raw]
    # The task and its edges are one write. A task landing without them is not
    # a partial batch that something refuses — it is a task that reads as
    # startable, which is the whole of audit F28.
    ops, said = [op], []
    for others, kind in rels:
        more, fresh, already = _relation_ops(tg, tid, others, kind)
        ops += more
        said.append((kind, fresh, already))
    _tstage_all(ops)
    con.print(f"[green]staged[/] add {tid}")
    for kind, fresh, already in said:
        _say_relation(tid, kind, fresh, already)
    _twarn_stuck()


def _decisions_or_none() -> Graph | None:
    """The decision store if this project has a readable one, else None.

    Task commands must work in a project that tracks only work, so every
    cross-graph view degrades to "no premise information" rather than failing.
    """
    proj = project.find()
    if not proj.has_decisions:
        return None
    try:
        return Graph.load(proj.store)
    except Exception:
        # Degrading silently would print "ready" for work whose premise is
        # undecided — a wrong answer, which is worse than an absent one.
        con.print(f"[dim]{project.STORE_NAME} could not be read, so premise "
                  f"information is missing here — `dg check`[/]")
        return None


def _gated_by(tg: TaskGraph, g: Graph | None, tid: str) -> str | None:
    if g is None:
        return None
    from dgraph import cross
    return cross.gated_by(tg, g, tid)


def _tasks_implementing(did: str) -> list[str]:
    """Every task that exists because of this decision, with its status."""
    proj = project.find()
    if not proj.has_tasks:
        return []
    try:
        from dgraph import cross
        tg = TaskGraph.load(proj.tasks)
        return [f"{t} ({tg.tasks[t].status})" for t in cross.rests_on(tg, did)]
    except Exception:
        return []


def _tasks_informing(did: str) -> list[str]:
    """Every task whose outcome bears on this decision, with its status."""
    proj = project.find()
    if not proj.has_tasks:
        return []
    try:
        from dgraph import cross
        tg = TaskGraph.load(proj.tasks)
        return [f"{t} ({tg.tasks[t].status})" for t in cross.evidence(tg, did)]
    except Exception:
        return []


def _tasks_resting_on(dids: list[str]) -> list[str]:
    """Unfinished work whose premise is one of these decisions.

    Silent in a project with no task store, which is most of them — the
    decision commands must cost nothing extra where there are no tasks.
    """
    proj = project.find()
    if not proj.has_tasks:
        return []
    try:
        from dgraph import cross
        return cross.blast_radius(TaskGraph.load(proj.tasks), dids)
    except Exception:
        # A broken task store must never stop a decision being reopened;
        # `dg check` reports it separately.
        return []


def _resolve_premise(did: str) -> str:
    """Check a `--because` against the *effective* decision graph.

    Resolved against the store plus its staged ops, so recording a decision and
    the work it implies in one batch is possible — the A3 lesson. The converse
    never holds: a decision command must not consult tasks.
    """
    proj = project.find()
    if not proj.has_decisions:
        con.print(f"[red]--because {_x(did)} names a decision, but there is no "
                  f"{project.STORE_NAME} here[/]\n"
                  f"[dim]`dg init` to start a decision graph, or drop "
                  f"--because[/]")
        raise typer.Exit(1)
    if did not in _eff(Graph.load(proj.store)).vertices:
        con.print(f"[red]unknown decision {_x(did)}[/]\n"
                  f"[dim]`dg show` lists what is on the frontier[/]")
        raise typer.Exit(1)
    return did


@task_app.command("link", rich_help_panel=T_RECORD)
def task_link(
    tid: str,
    because: str = typer.Option(None, "--because",
                                help="the decision this work exists because of"),
    evidence_for: str = typer.Option(None, "--evidence-for",
                                     help="the decision this work will inform"),
) -> None:
    """Point an existing task at a decision.

    The emergent case: work turns up a question nobody had written down, so you
    record the decision and then say which work raised it — after the fact, and
    often after the task is already done.
    """
    _require_task(tid)
    if not because and not evidence_for:
        con.print("[red]nothing to link[/]\n"
                  "[dim]pass --because or --evidence-for[/]")
        raise typer.Exit(2)
    op = {"op": "set_link", "task": tid}
    if because:
        op["because"] = _resolve_premise(because)
    if evidence_for:
        op["evidence_for"] = _resolve_premise(evidence_for)
    _tstage(op)
    con.print(f"[green]staged[/] {tid} linked")
    _twarn_stuck()


@task_app.command("unlink", rich_help_panel=T_RECORD)
def task_unlink(
    tid: str,
    because: bool = typer.Option(False, "--because",
                                 help="drop the decision this work rests on"),
    evidence_for: bool = typer.Option(False, "--evidence-for",
                                      help="drop the decision this work informs"),
) -> None:
    """Remove a task's link to a decision.

    The undo `dg task link` never had. A link recorded against the wrong
    decision, or one that stopped being true, is a correction the tool has to
    be able to make: hand-editing the store is the failure this command exists
    to prevent.
    """
    tg = _teff(_tg())
    _require_task(tid, tg)
    if not because and not evidence_for:
        con.print("[red]nothing to unlink[/]\n"
                  "[dim]pass --because or --evidence-for[/]")
        raise typer.Exit(2)
    wanted = ([f for f, on in (("because", because),
                               ("evidence_for", evidence_for)) if on])
    missing = [f for f in wanted if getattr(tg.tasks[tid], f) is None]
    if missing:
        con.print(f"[red]{tid} has no {' or '.join('--' + f.replace('_', '-') for f in missing)} "
                  f"to remove[/]")
        raise typer.Exit(1)
    _tstage({"op": "set_link", "task": tid, "clear": wanted})
    con.print(f"[green]staged[/] {tid} unlinked from "
              f"{', '.join(getattr(tg.tasks[tid], f) for f in wanted)}")
    _twarn_stuck()


@task_app.command("dep", rich_help_panel=T_RECORD)
def task_dep(
    tid: str,
    after: str = typer.Option(None, "--after",
                              help="comma-separated tasks that must come first"),
    discovered_during: str = typer.Option(
        None, "--discovered-during",
        help="comma-separated tasks whose doing turned this one up"),
) -> None:
    """Record how this task relates to other tasks.

    Two relations, and they make different claims. `--after` is a prerequisite:
    it makes this task wait. `--discovered-during` is provenance: it records
    which work turned this one up, makes it wait on nothing, and frequently
    runs the other way from the ordering — a cleanup noticed mid-task usually
    has to land before that task can be finished.

    `dg task add` could only say either when the task was created, so a relation
    discovered later had no way in at all.
    """
    tg = _teff(_tg())
    _require_task(tid, tg)
    if not after and not discovered_during:
        con.print("[red]nothing to record[/]\n"
                  "[dim]pass --after or --discovered-during[/]")
        raise typer.Exit(2)
    rels = [(_relation_targets(tg, tid, raw, kind), kind)
            for raw, kind in ((after, "precedes"),
                              (discovered_during, "prompted")) if raw]
    # Both kinds in one write: `--after X --discovered-during Y` is one
    # statement about how this task relates to the others, and half of it is a
    # different statement. See `_tstage_all`.
    ops, said = [], []
    for others, kind in rels:
        more, fresh, already = _relation_ops(tg, tid, others, kind)
        ops += more
        said.append((kind, fresh, already))
    _tstage_all(ops)
    for kind, fresh, already in said:
        _say_relation(tid, kind, fresh, already)
    _twarn_stuck()


@task_app.command("undep", rich_help_panel=T_RECORD)
def task_undep(
    tid: str,
    after: str = typer.Option(None, "--after",
                              help="comma-separated prerequisites to drop"),
    discovered_during: str = typer.Option(
        None, "--discovered-during",
        help="comma-separated origins to drop"),
) -> None:
    """Remove a relation. Releases this task if it waited only on that.

    Naming the kind is required rather than inferred from the pair, because
    both kinds can hold between the same two tasks: guessing would delete the
    ordering when the correction was to the provenance.
    """
    tg = _teff(_tg())
    _require_task(tid, tg)
    if not after and not discovered_during:
        con.print("[red]nothing to remove[/]\n"
                  "[dim]pass --after or --discovered-during[/]")
        raise typer.Exit(2)
    # Every spec is checked, then every op is staged in one write, then the
    # whole thing is reported — so a bad second spec cannot leave the first
    # one's removals staged alone. See `_tstage_all`.
    ops, said = [], []
    for raw, kind in ((after, "precedes"), (discovered_during, "prompted")):
        if not raw:
            continue
        held = _held(tg, tid, kind)
        others = [x.strip() for x in raw.split(",") if x.strip()]
        unknown = [o for o in others if o not in held]
        if unknown:
            con.print("[red]"
                      + MISSING_EDGE[kind].format(
                          src=", ".join(unknown), other=tid)
                      + "[/]\n"
                      f"[dim]`dg task node {tid}` lists both relations[/]")
            raise typer.Exit(1)
        ops += [{"op": "remove_dep", "from": o, "to": [tid], "kind": kind}
                for o in others]
        said.append((kind, others))
    _tstage_all(ops)
    for kind, others in said:
        con.print(f"[green]staged[/] {tid} no longer {_REL[kind]['reads']} "
                  f"{', '.join(others)}")
    _twarn_stuck()


@task_app.command("start", rich_help_panel=T_RECORD)
def task_start(tid: str) -> None:
    """Stage a task moving to DOING."""
    _require_task(tid)
    _tstage({"op": "set_status", "task": tid, "status": "DOING"})
    con.print(f"[green]staged[/] {tid} → DOING")
    _twarn_stuck()


@task_app.command("done", rich_help_panel=T_RECORD)
def task_done(
    tid: str,
    outcome: str = typer.Option(None, "--outcome", "-o",
                                help="what the work produced: a path, a PR, a note"),
) -> None:
    """Stage a task as finished. Records what it produced."""
    tg = _teff(_tg())
    _require_task(tid, tg)
    waiting = tg.waiting_on(tid)
    if waiting:
        con.print(f"[yellow]note: {tid} still waits on "
                  f"{', '.join(waiting)}[/]")
    outcome = outcome or _ask(
        "Outcome (what did this produce? a path, a PR, a note)", "--outcome/-o")
    _tstage({"op": "set_status", "task": tid, "status": "DONE",
             "outcome": outcome, "done": _date.today().isoformat()})
    con.print(f"[green]staged[/] {tid} → DONE")
    _twarn_stuck()


def _csv(raw: str | None) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()] if raw else []


def _fallout(tg: TaskGraph, tid: str) -> dict[str, str]:
    """What dropping `tid` would leave standing, and why each one is affected.

    Two different consequences, deliberately reported together because they
    need the same judgement from the same person at the same moment:

    *released* — work that waited only on `tid`, which `RESOLVED` will make
    startable. That may be wrong: a prerequisite that produced something this
    work consumes does not release it, it undermines it.

    *orphaned* — work `tid` turned up, which nothing else explains. Dropping
    `tid` changes nothing about it at all, which is precisely why it needs
    saying: provenance gates nothing, so the silence is total.

    Only counts an origin as gone when every *other* origin is already DROPPED
    — one surviving origin still says why the work exists.
    """
    out = {}
    for t in tg.unblocks(tid):
        if tg.waiting_on(t) == [tid]:
            out[t] = f"released — waited only on {tid}"
    for t in tg.prompted(tid):
        if tg.tasks[t].unfinished and all(
                o == tid or tg.tasks[o].status == "DROPPED"
                for o in tg.discovered_during(t)):
            out[t] = f"orphaned — discovered during {tid}"
    return out


@task_app.command("drop", rich_help_panel=T_RECORD)
def task_drop(
    tid: str,
    why: str = typer.Option(None, "--why", "-w", help="why it is not being done"),
    keep: str = typer.Option(None, "--keep",
                             help="affected tasks that are still worth doing"),
    drop_too: str = typer.Option(None, "--drop-too",
                                 help="affected tasks to abandon along with it"),
) -> None:
    """Stage a task as abandoned, and settle what that leaves behind.

    Dropping is the one status change that acts on other work: it releases what
    waited on this task and orphans what this task turned up. Each of those
    needs a verdict, and the moment to give one is now, while the reason for
    dropping is still in mind. `dg check` asks the same questions afterwards,
    which is the backstop, not the intended path.

    A task named in `--drop-too` is staged as dropped but its *own* fallout is
    not walked from here — one command, one judgement. The check picks up
    anything the cascade releases.

    `--keep` settles the cascade, not the backlog. `dg check` goes on asking
    about kept work until it starts, loses the dead prerequisite, or records
    what it now stands on — because "still worth doing" said once in a terminal
    is not something the store can read afterwards, and the two questions are
    genuinely different: this one is about the drop, that one is about a task
    sitting in the backlog with nothing explaining it.
    """
    tg = _teff(_tg())
    _require_task(tid, tg)
    fallout = _fallout(tg, tid)
    kept, doomed = _csv(keep), _csv(drop_too)

    stray = [t for t in kept + doomed if t not in fallout]
    if stray:
        con.print(f"[red]not affected by dropping {tid}: "
                  f"{', '.join(sorted(set(stray)))}[/]\n"
                  f"[dim]--keep and --drop-too name only the work this drop "
                  f"leaves standing[/]")
        raise typer.Exit(1)
    both = sorted(set(kept) & set(doomed))
    if both:
        con.print(f"[red]{', '.join(both)} named as both kept and dropped[/]")
        raise typer.Exit(1)

    undecided = [t for t in sorted(fallout) if t not in kept and t not in doomed]
    if undecided:
        if not _interactive():
            # Refused rather than defaulted, because both defaults are wrong
            # somewhere: assuming "keep" is how a task whose whole purpose died
            # with `tid` stays in the backlog, and assuming "drop" throws away
            # work that was only ever incidentally connected.
            con.print(f"[red]{len(undecided)} task(s) need a verdict before "
                      f"{tid} can be dropped[/]")
            for t in undecided:
                con.print(f"  [bold]{t}[/]  {_x(tg.tasks[t].title)}\n"
                          f"      [dim]{fallout[t]}[/]")
            con.print(f"[dim]re-run naming each one: --keep A,B and/or "
                      f"--drop-too C,D[/]")
            raise typer.Exit(2)
        for t in undecided:
            con.print(f"[bold]{t}[/]  {_x(tg.tasks[t].title)}\n"
                      f"    [dim]{fallout[t]}[/]")
            (kept if typer.confirm("  still worth doing?", default=True)
             else doomed).append(t)

    op = {"op": "set_status", "task": tid, "status": "DROPPED"}
    if why:
        op["why"] = why
    # The drop and its cascade are one write. They are one judgement — the
    # operator was asked about each cascaded task and answered — and a tray
    # holding only the first half says the opposite of what they answered.
    cascade = [{"op": "set_status", "task": t, "status": "DROPPED",
                "why": f"abandoned along with {tid}"} for t in sorted(doomed)]
    _tstage_all([op] + cascade)
    con.print(f"[green]staged[/] {tid} → DROPPED")
    for c in cascade:
        con.print(f"[green]staged[/] {c['task']} → DROPPED")
    if kept:
        con.print(f"[cyan]kept[/]: {', '.join(sorted(kept))} "
                  f"[dim]— still worth doing without {tid}[/]")
        con.print(f"[dim]`dg check` asks again until each one starts, drops "
                  f"the dead prerequisite, or records what it stands on[/]")
    _twarn_stuck()


def _require_task(tid: str, tg: TaskGraph | None = None) -> None:
    tg = tg if tg is not None else _teff(_tg())
    if tid not in tg.tasks:
        con.print(f"[red]unknown task {tid}[/]")
        raise typer.Exit(1)


@task_app.callback(invoke_without_command=True)
def task_show(
    ctx: typer.Context,
    full: bool = typer.Option(False, "--full",
                              help="The table, with no title clipped."),
) -> None:
    """What is outstanding, and what can be picked up now.

    One line each by default. `--full` gives the table instead, which clips
    nothing.
    """
    if ctx.invoked_subcommand is not None:
        return
    tg = _tg()
    g = _decisions_or_none()

    def waiting_for(tid: str) -> tuple[list[str], str | None]:
        """What holds this task up, from both stores.

        Two waiting-on lists, kept apart in the data and joined only here: a
        mixed list of T- and D-ids would eventually be fed to a task lookup and
        crash, which is the class of bug audit item A1 fixed.
        """
        gate = _gated_by(tg, g, tid)
        waiting = list(tg.waiting_on(tid))
        if gate:
            waiting.append(f"{gate} (undecided)")
        return waiting, gate

    if full:
        _task_table(tg, waiting_for)
    else:
        _task_listing(tg, waiting_for)

    ready = [tid for tid in sorted(tg.tasks)
             if tg.ready(tid) and not _gated_by(tg, g, tid)]
    con.print(f"[green]ready[/] {', '.join(ready) or '—'}")
    if not full:
        con.print(compact.hint("dg task --full", "the table, nothing clipped"))


def _task_listing(tg: TaskGraph, waiting_for) -> None:
    """Outstanding work as one line per task — the default, and the piped one."""
    frontier = tg.frontier()
    con.print(f"[bold]TASKS[/]  {len(frontier)} outstanding of "
              f"{len(tg.tasks)}   " + "  ".join(
                  f"[{TASK_STYLE.get(k, 'white')}]{k} {n}[/]"
                  for k, n in sorted(tg.counts().items())))
    entries, areas = [], []
    for tid in frontier:
        task = tg.tasks[tid]
        waiting, gate = waiting_for(tid)
        aside = []
        if waiting:
            aside.append("waits " + ", ".join(waiting))
        if tg.unblocks(tid):
            aside.append("unblocks " + ", ".join(tg.unblocks(tid)))
        if task.because:
            aside.append(f"because {task.because}")
        if task.evidence_for:
            aside.append(f"evidence for {task.evidence_for}")
        if not aside:
            aside.append("startable")
        # Yellow for the whole aside where a premise is undecided: the reason
        # this task is not startable is in there, and the eye needs sending to
        # the line before it reads which of the four bits says so.
        style = "yellow" if gate else "dim"
        entries.append((tid, f"[{TASK_STYLE.get(task.status, 'white')}]"
                             f"{task.status}[/]", _x(task.title),
                        f"[{style}]" + _x(" · ".join(aside)) + "[/]"))
        areas.append(f"[dim]{_x(task.area)}[/]")
    _say(compact.listing(entries, width=_width(), markup=True, tails=areas))
    if not entries:
        con.print("  [dim]nothing outstanding[/]")


def _task_table(tg: TaskGraph, waiting_for) -> None:
    """Outstanding work in full: every title whole, every relation its own column."""
    t = Table(title="Tasks", header_style="bold")
    for c in ("ID", "Task", "Status", "Waiting on", "Because", "Area"):
        t.add_column(c)
    for tid in tg.frontier():
        task = tg.tasks[tid]
        waiting, gate = waiting_for(tid)
        premise = task.because or "—"
        if gate:
            premise = f"[yellow]{premise}[/]"
        t.add_row(tid, _x(task.title),
                  f"[{TASK_STYLE.get(task.status, 'white')}]{task.status}[/]",
                  ", ".join(waiting) or "—", premise, _x(task.area))
    con.print(t)


@task_app.command("rm", rich_help_panel=T_RECORD)
def task_rm(
    tid: str,
    splice: bool = typer.Option(False, "--splice",
                                help="join what came before it to what follows"),
    into: str = typer.Option(None, "--into",
                             help="move its edges onto this task instead"),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirmation"),
) -> None:
    """Remove a task. For a record made in error — nothing else.

    Work that will not be done is `dg task drop`, which keeps the task, the
    reason, and a verdict on everything the drop leaves standing. Removal keeps
    none of that. Use it for a task filed twice, or one that turned out to be a
    restatement of another — `--into` folds it into the one it duplicates.

    Reconnection happens **per edge kind**: prerequisites join to
    prerequisites, provenance to provenance. Splicing across them would assert
    an ordering nobody claimed.
    """
    mode = _removal_mode(splice, into)
    tg = _teff(_tg())
    _require_task(tid, tg)
    if into and into not in tg.tasks:
        con.print(f"[red]unknown task {into}[/]")
        raise typer.Exit(1)
    blocked = _archived(project.find().tasks)
    if blocked:
        con.print(f"[red]refused — {blocked}[/]\n"
                  f"[dim]commit the store first; git is the only record of "
                  f"what a removal takes away[/]")
        raise typer.Exit(1)

    t = tg.tasks[tid]
    lines = [f"{_x(t.title)}  [{_x(t.area)}]  {t.status}"]
    for label, before, after in (
            ("after", tg.prerequisites(tid), tg.unblocks(tid)),
            ("found doing", tg.discovered_during(tid), tg.prompted(tid))):
        if before or after:
            lines.append(f"{label:<12}{', '.join(before) or '—'}  "
                         f"→ {', '.join(after) or '—'}")
    if t.status == "DONE" and t.outcome:
        lines.append(f"[yellow]loses[/] the outcome: {_x(t.outcome)}")
    if t.why:
        lines.append(f"[yellow]loses[/] why it was dropped: {_x(t.why)}")
    op = {"op": "remove_task", "task": tid, "mode": mode}
    if into:
        op["into"] = into
    try:
        task_pending.vet(tg, op)          # before the confirmation; see `rm`
    except pending.ApplyError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    _sanction(f"remove {tid} ({mode})", lines, yes)
    _tstage(op)
    con.print(f"[green]staged[/] remove {tid}")
    _twarn_stuck()


@task_app.command("node", rich_help_panel=T_READ)
def task_node(tid: str) -> None:
    """Everything known about one task."""
    tg = _tg()
    if tid not in tg.tasks:
        con.print(f"[red]unknown task {tid}[/]")
        raise typer.Exit(1)
    t = tg.tasks[tid]
    # Readiness joined across both stores, like `dg task` and unlike the
    # generated view: a task whose premise is undecided is not startable, and
    # the detail view saying "ready" was the tool contradicting itself.
    g = _decisions_or_none()
    gate = _gated_by(tg, g, tid)
    state = ("waiting on a decision" if gate and t.unfinished
             else "ready" if tg.ready(tid)
             else "blocked" if tg.blocked(tid) else t.status.lower())
    lines = [
        f"[bold]{_x(t.title)}[/]", "",
        f"status      [{TASK_STYLE.get(t.status, 'white')}]{t.status}[/]  "
        f"[dim]({state})[/]",
        f"area        {_x(t.area)}",
        f"waiting on  {', '.join(tg.waiting_on(tid)) or '—'}",
        f"unblocks    {', '.join(tg.unblocks(tid)) or '—'}",
    ]
    # Printed only when they hold. Provenance is genuinely absent for most
    # work, and an "— " on every panel would train the eye to skip the line
    # on the tasks where it says something.
    if tg.discovered_during(tid):
        lines.append(f"found doing {', '.join(tg.discovered_during(tid))}")
    if tg.prompted(tid):
        lines.append(f"turned up   {', '.join(tg.prompted(tid))}")
    if t.because:
        lines.append(f"because     {t.because}"
                     + ("  [yellow](undecided)[/]" if gate else ""))
    if t.evidence_for:
        lines.append(f"informs     {t.evidence_for}")
    if t.done:
        lines.append(f"done        {t.done}")
    if t.outcome:
        lines += ["", "[bold]Outcome[/]", _x(t.outcome)]
    if t.why and t.status == "DROPPED":
        lines += ["", "[bold]Not being done[/]", _x(t.why)]
    if t.note:
        lines += ["", "[bold]Note[/]", _x(t.note)]
    con.print(Panel("\n".join(lines), title=tid,
                    border_style=TASK_STYLE.get(t.status, "white")))


@task_app.command("tree", rich_help_panel=T_READ)
def task_tree(root: str = typer.Argument(None, help="Task to root at")) -> None:
    """The work as a tree, from what can start first.

    The spine is `precedes` — what must be resolved before what — so reading
    down a branch is reading an order of work. `prompted` children hang off the
    task whose doing turned them up and say so, because the two edges are
    different claims: one asserts an ordering, the other only records where the
    work came from, and drawing them alike would assert the ordering that
    kind exists to avoid asserting.

    Roots are the tasks nothing precedes and nothing prompted. A task reached
    twice is drawn once and marked, as in `dg tree`.
    """
    tg = _tg()
    seen: set[str] = set()

    def add(parent: Tree, tid: str, note: str = "") -> None:
        t = tg.tasks[tid]
        label = (f"[bold]{tid}[/] {_x(t.title)} "
                 f"[{TASK_STYLE.get(t.status, 'white')}]{t.status}[/]{note}")
        if tid in seen:
            parent.add(label + " [dim](above)[/]")
            return
        seen.add(tid)
        node = parent.add(label)
        for c in tg.unblocks(tid):
            add(node, c)
        for c in tg.prompted(tid):
            add(node, c, " [dim]— turned up doing it[/]")

    top = Tree("task graph")
    if root:
        if root not in tg.tasks:
            con.print(f"[red]unknown task {_x(root)}[/]")
            raise typer.Exit(1)
        add(top, root)
    else:
        roots = [t for t in sorted(tg.tasks)
                 if not tg.prerequisites(t) and not tg.discovered_during(t)]
        # Only reachable from a hand-edited store — `task_acyclic` refuses to
        # apply a cycle — but a tree that silently draws nothing is the worst
        # way to report one, so every task becomes a root and the reason is
        # said out loud.
        if tg.tasks and not roots:
            con.print("[yellow]every task has something before it — the "
                      "ordering has a cycle; `dg check` names it[/]")
            roots = sorted(tg.tasks)
        for r in roots:
            add(top, r)
    con.print(top)


@task_app.command("pending", rich_help_panel=T_STAGE)
def task_pending_cmd(
    full: bool = typer.Option(False, "--full",
                              help="The table, with no detail clipped."),
) -> None:
    """What is staged for the task store but not yet applied.

    One line per op by default, the table under `--full` — `dg pending`'s twin,
    through the same renderer, because the two trays are reviewed the same way.
    """
    _tray(pending.load(task_pending.path()), full, details=_TASK_DETAIL,
          subject="task", heading="STAGED TASKS", title="Staged tasks",
          column="Task",
          actions="`dg apply` to write, `dg task drop-op <id>` to unstage",
          expand="dg task pending --full")


_TASK_DETAIL = {
    "add_task": lambda o: _x(o.get("title", "")),
    # The kind is named, not implied: the whole point of storing it is that a
    # reader never has to know which relation an absence stands for, and a
    # review screen is where that matters most. Not bracketed — square brackets
    # are console markup here, and rich ate the first version of this line.
    "add_dep": lambda o: f"{_x(o.get('kind', '?'))} → {', '.join(o.get('to', []))}",
    "remove_dep": lambda o: f"✗ {_x(o.get('kind', '?'))} → {', '.join(o.get('to', []))}",
    "set_link": lambda o: ", ".join(
        [f"{f} {_x(o[f])}" for f in ("because", "evidence_for") if o.get(f)]
        + [f"clear {_x(f)}" for f in o.get("clear", ())]),
    "set_status": lambda o: f"→ {_x(o.get('status', '?'))}",
    "remove_task": lambda o: f"✗ removed ({_x(o.get('mode', 'sever'))}"
                            + (f" → {_x(o['into'])}" if o.get("into") else "")
                            + ")",
}


@task_app.command("drop-op", rich_help_panel=T_STAGE)
def task_drop_op(
    ref: str = typer.Argument(..., metavar="ID_OR_INDEX",
                              help="the op's id, or its position"),
) -> None:
    """Unstage one staged task op, by id or position. See `dg drop`."""
    try:
        gone = pending.drop(ref, task_pending.path())
    except LookupError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    con.print(f"[green]dropped[/] task op {_x(ref)}: "
              f"{_op_summary(gone, _TASK_DETAIL, 'task')}")


@task_app.command("clear", rich_help_panel=T_STAGE)
def task_clear() -> None:
    """Unstage every task op."""
    pending.clear(task_pending.path())
    con.print("[green]cleared[/]")


@task_app.command("export", rich_help_panel=T_STORE)
def task_export(
    tid: str = typer.Argument(None, help="Scope to one task"),
) -> None:
    """Dump the task graph as JSON. `dg export`'s twin, for the other store.

    Round-trips: what this prints is accepted by `dg task import`, which drops
    the derived blocks and rebuilds them. Scoping to one task narrows the store
    to that task and its edges, so the result is a fragment — importable, but
    only into an empty project and usually not valid on its own.
    """
    from dgraph.server import task_payload
    tg = _tg()
    payload = task_payload(tg, _decisions_or_none())
    if tid:
        if tid not in tg.tasks:
            con.print(f"[red]unknown task {tid}[/]")
            raise typer.Exit(1)
        payload = {
            "areas": payload["areas"],
            "tasks": [t for t in payload["tasks"] if t["id"] == tid],
            "edges": [e for e in payload["edges"]
                      if e["from"] == tid or tid in e.get("to", [])],
            "derived": {tid: payload["derived"][tid]},
            "frontier": payload["frontier"],
        }
    # plain print, never con.print: rich soft-wraps at $COLUMNS and would
    # corrupt the JSON for whatever is parsing it. The `dg export` rule.
    print(json.dumps(payload, ensure_ascii=False))


@task_app.command("import", rich_help_panel=T_STORE)
def task_import(
    path: str = typer.Argument(..., help="a tasks.json prepared elsewhere"),
    force: bool = typer.Option(False, "--force",
                               help="Write the store even if the graph breaks "
                                    "invariants."),
) -> None:
    """Adopt a `tasks.json` written by hand or generated elsewhere.

    `dg import`'s twin, and it exists because the two stores are peers: a
    project may start from a prepared backlog exactly as it may start from a
    prepared decision graph, and one store having a checked front door while
    the other does not is the asymmetry this tool keeps refusing to have.

    Links to decisions (`because`, `evidence_for`) are carried through as
    written and checked here only for shape. Whether they name a decision that
    exists is a cross-store question, which is `dg check`'s.
    """
    from dgraph import json_import
    proj = project.find()
    _refuse_overwrite(proj.has_tasks, proj.tasks)
    try:
        tg, recomputed = json_import.read(pathlib.Path(path), "tasks")
    except (OSError, json_import.ShapeError) as exc:
        con.print(f"[red]✗ not imported[/]\n{_x(exc)}")
        raise typer.Exit(1) from None
    _say_recomputed(recomputed)
    _adopt(tg, proj.tasks, proj.task_view, task_render.write, force,
           f"{len(tg.tasks)} tasks, {len(tg.edges)} edges", "the file")


@task_app.command("render", rich_help_panel=T_HONEST)
def task_render_cmd() -> None:
    """Regenerate tasks.md from the store."""
    task_render.write(_tg())
    con.print(f"[green]✓[/] wrote {project.find().task_view}")


@app.command(rich_help_panel=STORE)
def init(
    areas: str = typer.Option(
        "General", "--areas",
        help="comma-separated area names, used to group the rendered view",
    ),
) -> None:
    """Start an empty decision graph in this directory."""
    proj = project.find()
    if proj.has_decisions:
        con.print(f"[red]{proj.store} already exists[/]")
        raise typer.Exit(1)
    g = Graph(areas=[a.strip() for a in areas.split(",") if a.strip()])
    g.save(proj.store)
    render.write(g, proj.view)
    con.print(f"[green]✓[/] created {proj.store} and {proj.view.name}")
    _report_ignored(proj)


def _report_ignored(proj: project.Project) -> None:
    """Say what `init` added to `.gitignore`, if anything.

    Done here rather than left to the reader: the staging trays, the locks and
    the temp files are all scratch, and the commit gate's own advice ("it is
    gitignored, so committing now drops them from the record") is only true if
    they actually are.
    """
    added = project.ensure_ignored(proj.root)
    if added:
        con.print(f"[dim]added to .gitignore: {', '.join(added)}[/]")


def _adopt(graph, store: pathlib.Path, view: pathlib.Path, write_view,
           force: bool, counted: str, source: str) -> None:
    """Validate a prepared graph, refuse it if invalid, then make it the store.

    Shared by every bootstrap door — `dg import`, `dg task import`,
    `dg import-md` — because the rule they must agree on is the one that
    matters: **a bootstrap never writes a store `dg apply` would refuse.** That
    would plant, on day one, the contradiction the tool exists to prevent. One
    implementation, so a new door cannot arrive without it.
    """
    problems = graph.validate()
    for v in problems:
        con.print(f"[{'red' if v.blocking else 'yellow'}]"
                  f"{'✗' if v.blocking else '!'}[/] {_x(v)}")
    blocking = [v for v in problems if v.blocking]
    if blocking and not force:
        con.print(f"[red]✗ not imported: the result breaks {len(blocking)} "
                  f"invariant(s)[/]\n"
                  f"[dim]fix {source}, or `--force` to write it anyway and "
                  f"repair with `dg` afterwards[/]")
        raise typer.Exit(1)
    graph.save(store)
    write_view(graph, view)
    con.print(f"[green]✓[/] imported {counted} → {store}")
    # Same as `init`: a store has just appeared, and the trays, locks and temp
    # files beside it are scratch. The commit gate's advice ("it is gitignored,
    # so committing now drops it") is only true if they actually are.
    _report_ignored(project.find())


def _say_recomputed(blocks: list[str]) -> None:
    """Name the derived blocks an export payload carried and this import threw.

    Said out loud rather than passed over: everything else this importer meets
    that is not in the schema is refused, and a reader who is not told which
    rule applied here has to go and find out whether their data survived.
    """
    if blocks:
        con.print(f"[dim]recomputed from the edges, not read: "
                  f"{', '.join(blocks)}[/]")


def _refuse_overwrite(exists: bool, store: pathlib.Path) -> None:
    """A bootstrap never lands on top of a store that is already there.

    Not `--force`-able, deliberately: `--force` means "I accept a graph that
    breaks invariants", and letting it also mean "discard the store I have"
    puts an irreversible act behind a flag reached for routinely.
    """
    if exists:
        con.print(f"[red]{store} already exists — refusing to overwrite[/]\n"
                  f"[dim]import into an empty directory and merge by hand, or "
                  f"move the existing store aside first[/]")
        raise typer.Exit(1)


@app.command(name="import", rich_help_panel=STORE)
def import_json(
    path: str = typer.Argument(..., help="a decisions.json prepared elsewhere"),
    force: bool = typer.Option(False, "--force",
                               help="Write the store even if the graph breaks "
                                    "invariants."),
) -> None:
    """Adopt a `decisions.json` written by hand or generated elsewhere.

    `decisions.json` is the input format, so this command is not a conversion —
    it is the check. It says which record and which field is wrong before
    anything is written, and refuses a graph `dg apply` would refuse, rather
    than leaving you to find out at the next `dg check`.

    See the Model section of the README for the schema; `dg export` shows a
    real one. To start from a prose document instead, have an agent read it and
    write the store, or drive `dg add` and `dg decide` from it.
    """
    from dgraph import json_import
    proj = project.find()
    _refuse_overwrite(proj.has_decisions, proj.store)
    try:
        g, recomputed = json_import.read(pathlib.Path(path), "decisions")
    except (OSError, json_import.ShapeError) as exc:
        con.print(f"[red]✗ not imported[/]\n{_x(exc)}")
        raise typer.Exit(1) from None
    _say_recomputed(recomputed)
    _adopt(g, proj.store, proj.view, render.write, force,
           f"{len(g.vertices)} vertices, {len(g.edges)} edges", "the file")


@app.command(name="import-md", rich_help_panel=STORE)
def import_md(
    path: str = typer.Argument(..., metavar="DECISION_GRAPH_MD",
                               help="a decision-graph.md that `dg render` wrote"),
    force: bool = typer.Option(False, "--force",
                               help="Write the store even if the imported graph "
                                    "breaks invariants."),
) -> None:
    """Rebuild a store from a `decision-graph.md` this tool generated.

    A migration, not a markdown parser. It reads exactly the dialect
    `dg render` emits — `### D01 — Title` sections with `- **Status:**`,
    `- **Depends on:**` and a `**Resolves to → …**` block — and reconciles the
    two directions such a document records the dependency relation in. Anything
    else is refused, naming the section that did not parse.

    **To start from a document of your own, this is not the command.** Write
    `decisions.json` and adopt it with `dg import`, or have an agent read your
    document and drive `dg add` / `dg decide` from it.
    """
    from dgraph.md_import import import_markdown
    proj = project.find()
    _refuse_overwrite(proj.has_decisions, proj.store)
    try:
        g = import_markdown(pathlib.Path(path))
    except (OSError, ValueError) as exc:
        con.print(f"[red]✗ not imported[/]\n{_x(exc)}")
        raise typer.Exit(1) from None
    _adopt(g, proj.store, proj.view, render.write, force,
           f"{len(g.vertices)} vertices, {len(g.edges)} edges",
           "the source document")


@app.command(rich_help_panel=WEB)
def serve(
    port: int = typer.Option(8765, "--port", "-p"),
    detach: bool = typer.Option(False, "--detach", "-d",
                                help="Start it in the background and return."),
    stop: bool = typer.Option(False, "--stop", help="Stop the detached server."),
    status: bool = typer.Option(False, "--status",
                                help="Say whether one is running."),
) -> None:
    """Browse and edit both graphs in a local web app.

    Blocks by default, which is right for a terminal. `--detach` starts it in
    its own session, prints the URL and returns — so a coding-agent session can
    open the app without giving up its own prompt. It is idempotent: run twice,
    the second run reports the first one's URL.
    """
    from dgraph import server
    if sum([detach, stop, status]) > 1:
        con.print("[red]--detach, --stop and --status are three different "
                  "questions; ask one[/]")
        raise typer.Exit(2)

    if status:
        st = server.status()
        if st["state"] == "running":
            con.print(f"[green]running[/] · pid {st['pid']} · {st['url']}")
        elif st["state"] == "stale":
            con.print(f"[yellow]not running[/] — {server.SERVE_NAME} describes "
                      f"a server that no longer answers\n"
                      f"[dim]`dg serve --stop` clears the record[/]")
            raise typer.Exit(1)
        else:
            con.print("[dim]not running[/]")
            raise typer.Exit(1)
        return

    if stop:
        st = server.stop()
        if st["state"] == "stopped":
            con.print(f"[green]stopped[/] pid {st['pid']} (port {st['port']})")
        elif st.get("error"):
            con.print(f"[red]could not stop pid {st['pid']}[/]\n{_x(st['error'])}")
            raise typer.Exit(1)
        elif st.get("cleared"):
            con.print(f"[yellow]nothing was running[/] — cleared a stale "
                      f"{server.SERVE_NAME}")
        else:
            con.print("[dim]nothing to stop[/]")
        return

    if detach:
        try:
            st = server.detach(port)
        except RuntimeError as exc:
            con.print(f"[red]{_x(exc)}[/]")
            raise typer.Exit(1) from None
        con.print(f"[green]{'already running' if st['already'] else 'started'}[/]"
                  f" · pid {st['pid']} · {st['url']}\n"
                  f"[dim]`dg serve --stop` when you are done[/]")
        return

    server.run(port=port)


def main() -> None:
    if len(sys.argv) == 1:
        sys.argv.append("show")
    app()


if __name__ == "__main__":
    main()
