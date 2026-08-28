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

from dgraph import agents, applying
from dgraph import brief as _brief
from dgraph import check as _check
from dgraph import compact
from dgraph import cross, editor, limits, pending, project, ranges, render, task_editor
from dgraph import integrate as integrate_mod
from dgraph import task_pending, task_render
from dgraph import query as _query
from dgraph.model import Graph
from dgraph.model import rival_note as model_rival_note
from dgraph.tasks import done_label
from dgraph.tasks import ID_RE as TASK_ID_RE
# Moved to `dgraph/tasks.py`, beside the two after-the-fact readings of the
# same question; imported under the CLI's old local name so every call site
# here is unchanged. The server needs it too — see `/api/task-fallout`.
from dgraph.tasks import fallout as _fallout
from dgraph.tasks import starting_on_abandoned_work as _start_note
from dgraph.tasks import TaskGraph

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
#: Its own panel rather than a corner of `STAGE`: nothing here stages anything.
#: These name the *writers* a shared tray has, which is a question that only
#: exists once more than one of them is running — and reads as noise filed under
#: a heading about ops waiting to be applied.
WRITERS = "Who is writing — names for a shared tray"
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
    (READ, ("show", "find", "brief", "node", "why/context", "tree", "path",
            "areas")),
    (HONEST, ("check", "gate", "render")),
    (RECORD, ("add", "decide", "reopen", "confirm", "repair", "amend",
              "dep", "undep", "rm")),
    (STAGE, ("pending", "edit", "drop", "clear", "apply")),
    (WRITERS, ("agent",)),
    (STORE, ("init", "range", "import", "import-md", "export", "integrate",
             "incoming")),
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
    (T_RECORD, ("add", "start", "park", "done", "drop", "amend", "link",
                "unlink", "dep", "undep", "rm")),
    (T_STAGE, ("pending", "drop-op", "clear")),
    (T_STORE, ("init", "import", "export")),
)


def _roles(*pairs: tuple[str, str]) -> dict[str, str]:
    """`{panel: role}`, refusing one heading with two meanings.

    Built from pairs rather than written as a dict literal because two of the
    headings below are the *same string* on both help screens — "Keeping it
    honest" and the staging line read identically for decisions and for work,
    which is deliberate and is why the two sides can share a role. A literal
    would have quietly kept whichever came last, so the twelve pairs declared
    would have been ten entries and a later disagreement would have resolved
    itself in silence. Here it is an error where the pairs are, which is the
    only place anybody would be looking.
    """
    out: dict[str, str] = {}
    for panel, role in pairs:
        if out.setdefault(panel, role) != role:
            raise ValueError(
                f"the heading {panel!r} is {out[panel]!r} on one help screen "
                f"and {role!r} on the other: one wording, two meanings")
    return out


#: What each panel is *for*, so the two screens can be checked against each
#: other rather than read side by side by somebody who remembers to.
ROLES = _roles(
    (READ, "read"), (HONEST, "honest"), (RECORD, "record"), (STAGE, "stage"),
    (STORE, "store"), (WORK, "work"), (WEB, "web"), (WRITERS, "writers"),
    (T_READ, "read"), (T_HONEST, "honest"), (T_RECORD, "record"),
    (T_STAGE, "stage"), (T_STORE, "store"),
)


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

    That label is for the *help*, and `list_commands` has a second audience:
    click reads it to build shell completions too. A shell offering
    `why/context` would complete a name nobody typed, while hiding `context`,
    which somebody might — so completion asks for the same list without the
    collapse, through `ctx.meta`, rather than each caller getting whichever
    form happens to suit the other.
    """
    order = [name for _, names in layout for name in names]

    #: Set on the context while click is completing, read by `list_commands`.
    PLAIN = "dgraph.plain_command_names"

    class Ordered(TyperGroup):
        def list_commands(self, ctx):
            known = [n for n in order
                     if all(a in self.commands for a in n.split("/"))]
            listed = {a for n in known for a in n.split("/")}
            tail = sorted(set(self.commands) - listed)
            if ctx is not None and ctx.meta.get(PLAIN):
                return [a for n in known for a in n.split("/")] + tail
            return known + tail

        def shell_complete(self, ctx, incomplete):
            """Completion is dispatch, not display.

            Click's own walk is kept — it is the thing that knows about hidden
            commands and about completing options after them — and only the
            names it walks are corrected.
            """
            ctx.meta[PLAIN] = True
            try:
                return super().shell_complete(ctx, incomplete)
            finally:
                ctx.meta.pop(PLAIN, None)

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
    # Before anything reads a store. `$DG_AGENT=unowned` is not a bad op, it is
    # a **bad configuration**, and the two want different refusals: raising at
    # the op leaves twelve `pending.stage_all` call sites to wrap and a
    # traceback wherever one was missed, and it would let a reading succeed
    # under an identity that every subsequent stage refuses. So the whole
    # session is refused here, once, the way an unloadable store is — and
    # `_with_refs` keeps its own raise for the library's other callers, the
    # server among them, which turn it into a 400 of their own.
    if pending.owner() == pending.UNOWNED:
        con.print(f"[red]✗ `{pending.UNOWNED}` is reserved — it is how this "
                  f"tool names ops nobody signed, so a writer cannot also be "
                  f"called that[/]\n"
                  f"[dim]set ${pending.AGENT_ENV} to something else, or unset "
                  f"it to work as the supervisor[/]")
        raise typer.Exit(2)
    # The heartbeat. Here because it is the one place every command passes
    # through, and after the refusal above so that a session being turned away
    # does not stamp itself alive. `touch` swallows everything and writes at
    # most once every `agents.TOUCH_EVERY` seconds — a liveness signal that
    # could fail a command, or add a locked write to every invocation, would
    # cost more than the fact is worth. A supervisor is not tracked.
    agents.touch(pending.owner())

STATUS_STYLE = {
    "DECIDED": "green",
    "OPEN": "bold red",
    "BLOCKED": "yellow",
    "REOPENED": "magenta",
    "PROVISIONAL": "cyan",
}


#: A store that will not load. Narrow on purpose: `apply_all` raises
#: `ApplyError` for a batch that does not fit, and these are the ways the file
#: underneath it fails instead — absent or unreadable (`OSError`), malformed
#: JSON or a duplicate id (`ValueError`), a field the schema does not have
#: (`TypeError`). Caught rather than allowed to propagate because a traceback
#: out of one batch takes the other one with it, which is the whole point of
#: `_apply_decisions` and `_apply_tasks` returning instead of exiting.
UNREADABLE = (OSError, ValueError, TypeError)


def _g() -> Graph:
    proj = project.find()
    if not proj.has_decisions:
        con.print(f"[red]no decisions.json under {proj.root}[/]\n"
                  f"[dim]run `dg init` there, or pass --project PATH[/]")
        raise typer.Exit(2)
    try:
        return Graph.load()
    except UNREADABLE as exc:
        # A store can be well-formed JSON and still refuse to load — the case
        # this exists for is a git merge that took both sides of a parallel
        # `dg add` and left one id twice, which conflicts in no file and is
        # reported by nothing until something tries to read it. `dg check` and
        # `dg brief` already say so plainly; these commands raised through, and
        # a traceback reads as *the tool broke* rather than *your store did* at
        # the one moment the difference decides what the reader does next.
        con.print(f"[red]{proj.store.name} could not be read[/]\n{_x(exc)}\n"
                  f"[dim]`dg check` reports it against both stores[/]")
        raise typer.Exit(1) from None


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


# ---- finding things ------------------------------------------------------
#
# The only reading that starts from a word rather than from the frontier or an
# id. Everything it knows how to ask lives in `dgraph/query.py`; what lives here
# is the part that module may not have — the cross-graph link — plus rendering.


def _lenses(*, archived: bool = True) -> tuple[list, Graph | None, TaskGraph | None]:
    """Every store this project has, as queryable surfaces.

    The cross-graph terms come from `cross.lenses`, which is the module allowed
    to say what the link means and is also what the browser calls — one
    implementation, so a query cannot mean two things depending on where it was
    typed.

    `archived` is `dg find --active` inverted: whether a decision's superseded
    edges are searched alongside its active one.
    """
    proj = project.find()
    g = _decisions_or_none() if proj.has_decisions else None
    tg = TaskGraph.load(proj.tasks) if proj.has_tasks else None
    return cross.lenses(g, tg, archived=archived), g, tg


def _fault(exc, source: str, *, as_json: bool = False) -> None:
    """Report an unanswerable query, pointing at the term that broke it.

    Under `--json` it is reported *as* json. Exit 2 already tells a script that
    it asked wrong, but a script that reads stdout should not have to parse a
    caret diagram to find out why — and the browser has taken the same view
    since `find_payload` was written.
    """
    if as_json:
        print(json.dumps({"query": source, "fault": exc.reason,
                          "column": exc.column}, indent=2, ensure_ascii=False))
        raise typer.Exit(2)
    con.print(f"[red]{_x(exc.reason)}[/]")
    if source:
        con.print(f"  [dim]{_x(source)}[/]")
        con.print("  [dim]" + " " * exc.column + "^[/]")
    raise typer.Exit(2)


@app.command(rich_help_panel=READ)
def find(
    query: str = typer.Argument(..., metavar="QUERY",
                                help="Terms, ANDed. `-` negates, `or` alternates."),
    decisions: bool = typer.Option(False, "--decisions", "-d",
                                   help="Search only the decision store."),
    tasks: bool = typer.Option(False, "--tasks", "-t",
                               help="Search only the task store."),
    as_json: bool = typer.Option(False, "--json", help="The same as data."),
    ids: bool = typer.Option(False, "--ids",
                             help="Bare ids, one per line, for a pipe."),
    full: bool = typer.Option(False, "--full",
                              help="The table, with no title clipped."),
    active: bool = typer.Option(False, "--active",
                                help="Active edges only: skip superseded answers."),
    limit: int = typer.Option(20, "--limit", "-n",
                              help="Rows per section before summarising."),
) -> None:
    """Find decisions and work by what they say.

    A bare word searches prose — titles, notes, answers, falsifiers, outcomes.
    `field:value` matches one stored field, `is:name` asks a derived question,
    and `under:`/`above:`/`because:` walk the graph. Terms are ANDed; `-`
    negates one and `or` alternates two.

    A decision's answers include the ones it used to have. `answer:`,
    `falsifier:` and `source:` read its superseded edges as well as its active
    one, and say `superseded answer:` when that is where the hit landed —
    a reversal's reasoning is often the only place a rejected approach is
    written down. `--active` narrows them to the answer that currently stands.

        dg find 'embedding -status:DECIDED'
        dg find 'is:ready area:Retrieval'
        dg find 'falsifier:"the corpus changes"'
        dg find 'under:D04 is:unsettled'
        dg find 'date:>=2026-01-01 is:decidable'
        dg find 'humdrum-data --active'   # only what still stands
        dg find 'is:decidable' --ids | xargs -n1 dg context

    Exit 1 means the query was fine and nothing matched. Exit 2 means it could
    not be answered as asked — a bad term, an unknown field, a predicate whose
    store this project does not have, an id that names no record, a query no
    single store can answer, or a flag contradicting the query.
    """
    proj = project.find()
    if not proj.exists:
        con.print(f"[red]no {project.STORE_NAME} under {proj.root}[/]\n"
                  f"[dim]run `dg init` there, or pass --project PATH[/]")
        raise typer.Exit(2)
    if decisions and tasks:
        con.print("[red]--decisions and --tasks are opposites[/]")
        raise typer.Exit(2)
    if limit < 1:
        # `--limit 0` used to print a count and a "… 2 more" line with no rows
        # above it, and `--limit -1` silently dropped the *last* row — Python's
        # slice semantics leaking through a flag. `--full` is how you ask for
        # everything, so there is nothing for a zero to mean.
        con.print("[red]--limit counts rows, so it starts at 1[/]\n"
                  "[dim]`--full` is how to ask for all of them[/]")
        raise typer.Exit(2)

    # Every store the project has, whatever the flags say. The flags narrow the
    # *answer*, not the vocabulary: vetting against a subset would report
    # `because` as an unknown field under `--decisions`, when the truth is that
    # the field is real and the flag disagrees with it — and those two need
    # different corrections.
    lenses, g, tg = _lenses(archived=not active)
    if not lenses:
        con.print(f"[red]nothing to search: no readable store under "
                  f"{proj.root}[/]")
        raise typer.Exit(2)

    try:
        q = _query.parse(query)
        _query.vet(q, lenses)
        wanted = _query.scope(q, lenses)
    except _query.Fault as exc:
        _fault(exc, query, as_json=as_json)
        return

    asked = {"decisions"} if decisions else {"tasks"} if tasks else None
    if asked is not None:
        narrowed = [l for l in wanted if l.kind in asked]
        if not narrowed:
            flag = "--decisions" if decisions else "--tasks"
            other = "tasks" if decisions else "decisions"
            if any(l.kind in asked for l in lenses):
                # The store exists; the query is simply about the other one.
                # Refused rather than resolved: honouring the flag would print
                # an empty section for a query that had a perfectly good
                # answer, and that failure is invisible. Honouring the field
                # would at least visibly ignore an argument somebody typed.
                con.print(f"[red]{flag} contradicts the query[/]\n"
                          f"[dim]it names something only the {other} store "
                          f"has — drop {flag}, or ask the other question[/]")
            else:
                con.print(f"[red]{flag}, but this project has no "
                          f"{list(asked)[0][:-1]} store[/]")
            raise typer.Exit(2)
        wanted = narrowed

    found = {l.kind: _query.select(q, l) for l in wanted}
    if as_json:
        # A store the query was not about is `null`; one that was, and matched
        # nothing, is `[]`. The key is always present — omitting it, as this
        # used to, left a consumer unable to tell "scoped away" from "no
        # matches" without re-parsing the query, and `data["decisions"]`
        # raising `KeyError` on a perfectly good answer. `find_payload` had
        # this distinction from the start; the two surfaces now agree.
        print(json.dumps({
            "query": str(q),
            "scope": [l.kind for l in wanted],
            **{l.kind: ([_find_row(q, l, r) for r in found[l.kind]]
                        if l.kind in found else None)
               for l in lenses},
        }, indent=2, ensure_ascii=False))
    elif ids:
        for l in wanted:
            for rid in found[l.kind]:
                print(rid)
    else:
        _find_report(q, wanted, found, full=full, limit=limit, g=g,
                     proj=proj)

    if not any(found.values()):
        if not (as_json or ids):
            _did_you_mean(q, wanted)
        raise typer.Exit(1)


def _find_row(q, l, rid: str) -> dict:
    status, title, area = l.row(rid)
    return {"id": rid, "status": status, "title": title, "area": area,
            "matched": [{"field": m.field, "snippet": compact.clip(m.text, 120),
                         "term": m.term} for m in _query.explain(q, l, rid)]}


#: How a store's rows are titled and coloured, so the two sections read as the
#: same view of different vocabularies rather than two commands' output.
_SECTION = {"decisions": ("DECISIONS", _style),
            "tasks": ("TASKS", lambda s: TASK_STYLE.get(s, "white"))}


def _find_aside(q, l, rid: str, g: Graph | None) -> str:
    """Why this row is here.

    The one place `find` departs from `dg show`'s row, and the point of the
    command: `dg show` puts waits/unblocks in the aside because you are
    triaging, `find` puts the field that matched there because you are
    identifying.

    A title hit needs no aside — the title is already on the line — so it falls
    back to what `dg show` would have said, which is why a search for a word
    that happens to be in the titles still reads as a frontier listing.
    """
    hits = [m for m in _query.explain(q, l, rid) if m.field != "title"]
    if hits:
        m = hits[0]
        rest = f" (+{len(hits) - 1})" if len(hits) > 1 else ""
        return f"{m.field}: {compact.gist(m.text)}{rest}"
    if l.kind == "decisions" and g is not None:
        waits, opens = g.waiting_on(rid), g.children(rid)
        if waits:
            return "waits " + ", ".join(waits)
        return "unblocks " + ", ".join(opens) if opens else ""
    return ""


def _find_report(q, lenses, found: dict, *, full: bool, limit: int,
                 g: Graph | None, proj) -> None:
    for l in lenses:
        rows = found[l.kind]
        heading, style = _SECTION[l.kind]
        con.print(f"\n[bold]{heading}[/]  {len(rows)} match of {len(l.ids)}")
        if not rows:
            con.print("  [dim]nothing matches here[/]")
            continue
        shown = rows if full else rows[:limit]
        asides = {rid: _find_aside(q, l, rid, g) for rid in shown}
        if full:
            t = Table(header_style="bold")
            for c in ("ID", "Title", "Status", "Matched", "Area"):
                t.add_column(c)
            for rid in shown:
                status, title, area = l.row(rid)
                t.add_row(rid, _x(title), f"[{style(status)}]{status}[/]",
                          _x(asides[rid]) or "—", _x(area))
            con.print(t)
        else:
            entries, tails = [], []
            for rid in shown:
                status, title, area = l.row(rid)
                # An empty aside stays the empty string, never empty markup:
                # `compact.listing` joins the tail onto whatever is truthy, and
                # `[dim][/]` is truthy, which leaves a row separated from its
                # area by a bullet and nothing else.
                aside = f"[dim]{_x(asides[rid])}[/]" if asides[rid] else ""
                entries.append((rid, f"[{style(status)}]{status}[/]",
                                _x(title), aside))
                tails.append(f"[dim]{_x(area)}[/]")
            # `prose_aside` only when an aside is actually prose. With every
            # row hit on its title the asides are short cross-references or
            # absent, and giving the aside its own column then spends the
            # title's width on nothing.
            prose = any(":" in asides[rid] for rid in shown)
            _say(compact.listing(entries, width=_width(), markup=True,
                                 tails=tails, prose_aside=prose))
            if len(rows) > len(shown):
                con.print(f"  [dim]… {len(rows) - len(shown)} more — "
                          f"`--limit {len(rows)}` or `--ids`[/]")
    _find_staged(proj)
    if not full:
        con.print(compact.hint("dg find … --full", "the table, nothing clipped"))


def _find_staged(proj) -> None:
    """Say when the tray also matches, without folding it into the rows.

    A staged op is a different kind of thing from a record — it has no id to
    follow up with — so a result set mixing them would be one where some rows
    answer `dg context` and some do not. The brief keeps the tray in its own
    section for the same reason.
    """
    staged = len(pending.load(proj.pending)) + len(
        pending.load(proj.task_pending))
    if staged:
        con.print(f"  [dim]{staged} op(s) staged and not applied — "
                  f"`dg pending` (not searched)[/]")


def _did_you_mean(q, lenses) -> None:
    """Rescue a typo without letting a guess into the result set.

    Exactness is what makes an empty result a *fact* — "nothing contains that
    string" is actionable in a way that "nothing matched, at whatever threshold
    was configured" is not. The cost is that `dg find embeddig` says nothing
    useful, so the suggestion is offered as a re-run to accept or ignore, and
    never as rows silently folded in.
    """
    import difflib
    bare = [t.value.raw for t in q.terms
            if t.kind == "prose" and not t.negated and not t.value.regex]
    if not bare:
        return
    vocab = set()
    for l in lenses:
        vocab |= _query.words(l)
    tips = []
    for word in bare:
        near = difflib.get_close_matches(word.lower(), vocab, n=2, cutoff=0.8)
        tips += [f"{word} → {n}" for n in near if n != word.lower()]
    if tips:
        con.print("  [dim]did you mean " + _x(", ".join(tips)) + "?[/]")


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
def node(
    vid: str,
    active: bool = typer.Option(
        False, "--active", "-a",
        help="The answer that stands, without the ones it replaced."),
) -> None:
    """Everything known about one decision.

    Including the edges a reversal replaced: each is printed with its own
    targets, falsifier, source and archived answer, because a reversal is an
    edge with a payload like any other. `--active` prints only the answer that
    currently stands, and says how many records it left out.
    """
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
    # Before the answer, not after it. `node` exists to show what a question
    # was answered with, so a second current answer is precisely the thing it
    # must not omit — and a reader who meets the caveat *below* the answer has
    # already read the answer as the answer.
    rivals = g.rival_answers(vid)
    if rivals:
        lines += ["", f"[red]{_x(model_rival_note(len(rivals)))}[/]"]
    if e and e.decided:
        lines += [
            f"opens       {', '.join(e.to) or 'TERMINAL'}",
            f"falsifier   {_x(e.falsifier) or '—'}",
            f"source      {_x(e.source)}   ({e.date})",
            "", "[bold]Answer[/]", _x(e.answer),
        ]
        for other in rivals:
            # Every one of them, with its own payload — the same rendering a
            # superseded edge gets, for the same reason: naming a count and
            # showing one answer still leaves the reader unable to see what
            # the other said.
            lines += [
                "", "[red]also current[/]",
                f"  opens       {', '.join(other.to) or 'TERMINAL'}",
                f"  falsifier   {_x(other.falsifier) or '—'}",
                f"  source      {_x(other.source)}   ({other.date})",
                f"  {_x(other.answer)}",
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
        lines += ["evidence from " + ev[0]] + [" " * 14 + e for e in ev[1:]]
    hist = g.history(vid)
    if hist and active:
        # Never silently. A reader who did not type the flag would see the same
        # panel and conclude the decision was never reversed, which is the one
        # thing this store exists to keep.
        lines += ["", f"[dim]{len(hist)} superseded "
                      f"{'edge' if len(hist) == 1 else 'edges'} not shown — "
                      f"drop --active to read {'it' if len(hist) == 1 else 'them'}[/]"]
    elif hist:
        lines += ["", "[bold]Superseded[/]"]
        for h in hist:
            lines += [
                "",
                f"  “{_x(h.summary)}” → {_x(h.replaced_by) or '(undecided)'}"
                f"   [dim]{h.date or ''}[/]",
                f"    why         {_x(h.why)}",
            ]
            # Only the fields this record actually holds. A reversal imported
            # from a markdown view, or made before the payload was archived,
            # has none of them — and a column of em-dashes would read as *it
            # opened nothing, on no evidence*, which is a claim, not a gap.
            if h.to:
                lines.append(f"    opened      {', '.join(h.to)}")
            if h.falsifier:
                lines.append(f"    falsifier   {_x(h.falsifier)}")
            if h.source:
                lines.append(f"    source      {_x(h.source)}")
            # `summary` is a label clipped from the answer, so when the two are
            # the same string the answer is already on the line above it.
            if h.answer and h.answer != h.summary:
                body = "\n".join("    " + ln for ln in h.answer.splitlines())
                lines += ["", "    [dim]archived answer[/]", _x(body)]
    # Kept apart from the reversals above, and this is the whole point of the
    # record. A rejected answer filed as history would read as *we believed
    # this, then changed our mind* — a claim about this project nobody made.
    # What it says instead is that somebody else answered this differently and
    # this store did not take it, which is worth exactly as much and is a
    # different sentence.
    turned_down = g.rejected(vid)
    if turned_down and not active:
        lines += ["", "[bold]Offered and not adopted[/]"]
        for h in turned_down:
            lines += [
                "",
                f"  from {_x(h.from_source)}   [dim]{h.date or ''}[/]",
                f"    answer      {_x(h.answer)}",
            ]
            if h.falsifier:
                lines.append(f"    falsifier   {_x(h.falsifier)}")
            if h.source:
                lines.append(f"    source      {_x(h.source)}")
            if h.to:
                lines.append(f"    would open  {', '.join(h.to)}")
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
    other. A store that exists and cannot be read is the case those two do not
    cover: this command's whole answer is the counts, so a missing table is a
    missing answer, said in the vocabulary of counts and exited on. Printing
    the other table and stopping at zero would hand a script half a total.
    """
    proj = project.find()
    g, unreadable = _decisions_state()
    tg = TaskGraph.load(proj.tasks) if proj.has_tasks else None
    if g is None and tg is None and unreadable is None:
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
    if unreadable is not None:
        # Last, so it is the line the reader ends on, and nonzero, so a caller
        # that only sees the exit code does not read a partial count as a total.
        con.print(f"[red]no decision counts: {project.STORE_NAME} could not be "
                  f"read[/]\n[dim]{_x(unreadable)}[/]\n"
                  f"[dim]`dg check` for what else it breaks[/]")
        raise typer.Exit(1)


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
    write: str = typer.Option(None, "--write", metavar="PATH",
                              help="A path a host is about to write to."),
    as_json: bool = typer.Option(False, "--json", help="Machine form."),
    triggers: bool = typer.Option(False, "--triggers",
                                  help="Print the substrings an adapter's fast "
                                       "path must let through, one per line."),
) -> None:
    """Judge a shell command or a write before a host does it.

    Host-neutral, and the only place either rule lives. Both agent adapters
    call this and translate the answer, so a rule written here is enforced
    under every host at once and a third host earns it by relaying the same
    verdict. Always exits 0 — the verdict is the output, and an adapter must be
    able to tell a refusal from a crash.

    `--command` is the commit gate: allow, warn, ask or deny.

    `--write` is the agent write scope, and answers only allow or ask. It reads
    `$DG_WRITE`, is never consulted for a caller with no `$DG_AGENT`, and never
    judges a read — see `dgraph/limits.py` for why each of those is the case.

    `--triggers` prints `gate.TRIGGERS` instead of judging anything. An adapter
    skips this command entirely for a shell command containing none of them, so
    a third host — or a check on the two that exist — can read the list from the
    tool rather than copy it and let it go stale, which is how the removal
    verdict came to be unreachable from both. It says nothing about `--write`,
    which has no fast path: every write is judged, because a path carries no
    substring that could tell an in-scope one from an out-of-scope one.
    """
    from dgraph import gate as _gate
    if triggers:
        if as_json:
            print(json.dumps(list(_gate.TRIGGERS)))
        else:
            print("\n".join(_gate.TRIGGERS))
        return
    if command is None and write is None:
        con.print("[red]--command or --write is required (or --triggers)[/]")
        raise typer.Exit(2)
    if command is not None and write is not None:
        # Refused rather than combined. `gate.combine` exists for two rules
        # reached by one command; these are two different questions about two
        # different things, and answering them with one verdict would attach a
        # write's `ask` to a commit or the reverse.
        con.print("[red]--command and --write are separate questions — "
                  "ask them one at a time[/]")
        raise typer.Exit(2)
    v = (_gate.write_verdict(write) if write is not None
         else _gate.verdict(command))
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
    except ranges.RangeError as exc:
        # The buffer prefills an id, so a used-up grant stops it before the
        # editor opens. Reported as a refusal rather than a traceback for the
        # reason `F-F2` gives about an unreadable store: *the tool broke* and
        # *this clone cannot allocate* send a reader to different places.
        con.print(f"[red]✗ nothing staged[/]\n{_x(exc)}")
        raise typer.Exit(1) from None
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
    # Said here rather than in `decide`, so `--edit` and the prompt path — both
    # of which end up here — cannot differ about it, and so the browser's
    # compose path can call the same helper.
    proj = project.find()
    note = cross.deciding_ahead_of_evidence(
        TaskGraph.load(proj.tasks) if proj.has_tasks else None, op["vertex"])
    if note:
        con.print(f"[yellow]note: {note}[/]")
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
    # Before anything is composed, and deliberately so. The skill's argument for
    # stage-time refusals is that the second agent to answer a settled question
    # is turned away BEFORE writing an answer, a source and a falsifier; a
    # policy that refused after all three were typed would be the same waste
    # with a different reason. See cross.POLICIES.
    why = cross.refuse_close(_maybe_tasks(), vid, pending.owner())
    if why is not None:
        con.print(f"[red]✗ nothing staged — {_x(why)}[/]")
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


def _late_evidence(eff, vid: str) -> list[dict]:
    """The finished evidence for `vid` that has never been read against it.

    `cross.late_evidence`, with this project's stores loaded and its task tray
    applied. The reading itself lives in `cross` because the browser needs the
    same list — a control offering a result that is not outstanding would be
    the two doors disagreeing about who is waiting.
    """
    proj = project.find()
    if not proj.has_tasks:
        return []
    return cross.late_evidence(_teff(TaskGraph.load(proj.tasks)), eff, vid)


@app.command(rich_help_panel=RECORD)
def confirm(
    vid: str,
    against: str = typer.Option(None, "--against",
                                help="evidence tasks read against this answer"),
    note: str = typer.Option(None, "--note", "-n",
                             help="what the evidence showed"),
) -> None:
    """Re-affirm a decision: its premise moved or its evidence landed, and the
    answer holds.

    Two acts under one verb, told apart by what is standing in the way, and
    never both in one command — each stages into one tray, and a command that
    wrote to both would be a judgement the two `apply` paths could split.

    **Without `--against`** it re-affirms a PROVISIONAL status. Without that,
    PROVISIONAL is a state with no exit: `set_status` is a derived op —
    `pending.expand` produces it and nothing else stages one — so the only
    route back to DECIDED was another `reopen` plus `decide`, which files a
    reversal that never happened. Reversals are the most valuable thing the
    graph holds and inventing one to escape a status would be a lie in the
    record.

    **With `--against T01,T02`** it records that those results were read
    against this answer and it stands — the exit
    `cross.evidence_after_deciding` otherwise lacks. Evidence that lands after
    a decision is settled can refute the answer (`dg reopen`), turn out never
    to have been needed (`dg task unlink`), or confirm it, and only the third
    is common and only the third had no command. The note is required for the
    reason a drop's `--why` is: without it the entry records that somebody ran
    a command, not what they found.

    A reading is per task, so confirming against one of two late results leaves
    the finding naming the other. It is not permanent silence either: a
    *later* result post-dates the reading and the finding comes back.
    """
    g = _g()
    eff = _eff(g)
    if vid not in eff.vertices:
        con.print(f"[red]unknown vertex {vid}[/]")
        raise typer.Exit(1)
    v = eff.vertices[vid]

    # The reading path: asked for explicitly, or arrived at because the status
    # has nothing to re-affirm and the evidence does. The second case used to
    # be a bare refusal, which is what left the finding with no exit.
    late = _late_evidence(eff, vid) if v.settled else []
    if against or (v.base_status != "PROVISIONAL" and late):
        if not v.settled:
            con.print(f"[red]{vid} is {v.status} — an answer has to be "
                      f"standing before evidence can be read against it[/]")
            raise typer.Exit(1)
        named = _csv(against)
        if not named:
            # Refused rather than defaulted to "all of them", matching
            # `dg task drop`: each result is a separate reading and the note is
            # about that result, so answering for several at once with one
            # sentence is the box-tick this record exists not to be.
            if not _interactive():
                con.print(f"[red]{len(late)} result(s) to read against "
                          f"{vid}[/]")
                for t in late:
                    con.print(f"  [bold]{t['id']}[/]  finished {t['done']}\n"
                              f"      [dim]{_x(t['outcome'])}[/]")
                con.print(f"[dim]re-run naming them: --against "
                          f"{','.join(t['id'] for t in late)}[/]")
                raise typer.Exit(2)
            named = [t["id"] for t in late]
        by_id = {t["id"]: t for t in late}
        stray = [t for t in named if t not in by_id]
        if stray:
            con.print(f"[red]not evidence awaiting a reading against {vid}: "
                      f"{', '.join(sorted(set(stray)))}[/]\n"
                      f"[dim]`dg node {vid}` lists what this answer rests "
                      f"on[/]")
            raise typer.Exit(1)
        today = _date.today().isoformat()
        ops = []
        for tid in named:
            what = note or (
                _ask(f"{tid} — {by_id[tid]['outcome']}\n"
                     f"  what does it show about the answer?", "--note/-n")
                if _interactive() else None)
            if not what:
                con.print(f"[red]reading {tid} against {vid} needs what it "
                          f"showed: --note[/]")
                raise typer.Exit(1)
            ops.append({"op": "read_evidence", "task": tid, "against": vid,
                        "note": what, "date": today})
        _tstage_all(ops)
        con.print(f"[green]staged[/] {vid} read against "
                  f"{', '.join(named)} — a *task* op, so `dg task pending` "
                  f"to review")
        _twarn_stuck()
        return

    # Both guards and the expansion are `pending.compose_confirm`, shared with
    # `POST /api/confirm` — a re-affirmation that skipped either would stage an
    # op `apply` refuses, in a tray every other writer shares.
    try:
        ops = pending.compose_confirm(eff, vid=vid)
    except pending.ApplyError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
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
        # flag path below runs `pending.compose_add` and the web app runs
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
                  f"[dim]give them as flags, or use `dg add --edit`[/]"
                  + (_next_hint(lambda: editor.next_id(eff))
                     if not vid else ""))
        raise typer.Exit(2)
    # Every graph-facing rule, and the op list itself, is `pending.compose_add`
    # — shared with `POST /api/add`, because a second door onto opening a
    # question must not bring a second set of rules with it. What stays here is
    # the shape of a *command-line* failure: the flag list above, and turning a
    # refusal into a clean exit rather than a traceback.
    parents = [x.strip() for x in after.split(",") if x.strip()] if after else []
    try:
        ops = pending.compose_add(eff, vid=vid, title=title, area=area,
                                  status=status, after=parents, note=note,
                                  stored=g)
    except pending.ApplyError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    # One write, and the site that most needed it: a vertex staged without its
    # edges is only a `no_orphans` warning, so unlike every other group here a
    # half-staged one could be applied and pass.
    pending.stage_all(ops, against=eff)
    con.print(f"[green]staged[/] add {vid}")
    _warn_stuck()


#: What `dg amend` and `dg task amend` say when a title changes, once, at the
#: moment it changes. The store cannot reach a citation of the old wording —
#: not a commit message, not a line in `docs/`, not `dg why` output somebody
#: pasted into a review — and archiving the old title inside the store would
#: not reach them either, which is the argument for not archiving it. What is
#: left is telling whoever is making the change, here, where it can be acted on.
CITED = ("[dim]citations of the old title elsewhere — commits, docs, a pasted "
         "`dg why` — are not updated, and nothing can find them[/]")


def _amended(record, op: dict) -> list[str]:
    """One line per field this op changes, old and new. Both stores' twin."""
    return [f"{k:<7} {_x(getattr(record, k) or '—')} → {_x(op[k] or '—')}"
            for k in pending.FIELDS if k in op]


@app.command(rich_help_panel=RECORD)
def amend(
    vid: str,
    title: str = typer.Option(None, "--title", "-t"),
    area: str = typer.Option(None, "--area"),
    note: str = typer.Option(None, "--note", "-n",
                             help="what is undecided, and why"),
) -> None:
    """Correct how a decision is worded or filed: its title, area or note.

    The op every other repair already had. Until it existed, an agent finding a
    typo'd or since-clarified title had no legitimate move — it hand-edited
    `decisions.json`, which is the one route this architecture exists to make
    unnecessary, or it left the record wrong. Audit `F-F6`.

    **It cannot touch an answer**, and that is the line it is drawn along. A
    title and an area are not claims — a title is how a question is *referred
    to*, not something the question says — so nothing is superseded when one
    changes and nothing is archived. An answer and a falsifier are dated
    assertions, and rewriting one in place is the act this whole model refuses:
    `dg reopen` first, then decide again meaning it.
    """
    g = _g()
    eff = _eff(g)
    if vid not in eff.vertices:
        con.print(f"[red]unknown decision {vid}[/]")
        raise typer.Exit(1)
    op = {"op": "set_fields", "vertex": vid}
    op.update({k: v for k, v in (("title", title), ("area", area),
                                 ("note", note)) if v is not None})
    lines = _amended(eff.vertices[vid], op)
    try:
        # `pending.vet` holds every rule — a blank title, an unknown area, an
        # op that changes nothing — because the browser can post this op as
        # data and a rule only the CLI ran would not be a rule.
        pending.vet(eff, op)
    except pending.ApplyError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    pending.stage_all([op], against=eff)
    con.print(f"[green]staged[/] {vid}")
    for line in lines:
        con.print(f"  [dim]{line}[/]")
    if "title" in op:
        con.print(CITED)
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
    parents = [x.strip() for x in after.split(",") if x.strip()]
    try:
        ops, fresh, already = pending.compose_dep(eff, vid=vid, after=parents)
    except pending.ApplyError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    if already:
        con.print(f"[dim]already rests on {', '.join(already)}[/]")
    pending.stage_all(ops, against=eff)
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
    parents = [x.strip() for x in after.split(",") if x.strip()]
    try:
        ops, blocker = pending.compose_undep(eff, vid=vid, after=parents)
    except pending.ApplyError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    released = blocker is not None
    pending.stage_all(ops, against=eff)

    con.print(f"[green]staged[/] {vid} no longer rests on "
              f"{', '.join(parents)}")
    if released:
        con.print(f"[cyan]{vid} was BLOCKED:{blocker}[/] and is released to "
                  f"OPEN [dim]— the block asserted the dependency being "
                  f"removed[/]")

    # Reported, not repaired: each is a judgement the graph cannot make.
    # `pending.introduced` is the same difference the browser shows *before*
    # staging, which is what lets a person decline it there.
    for v in pending.introduced(eff, ops):
        con.print(f"[yellow]{v.check}[/] [dim]{_x(v.message)}[/]")
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
                  f"--because <D>`, or `dg task unlink <T> --because {vid}`[/]")
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
    if eff.rejected(vid):
        lines.append(f"[yellow]loses[/] {len(eff.rejected(vid))} answer(s) "
                     f"offered by another writer and not adopted")
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
    agent: str = typer.Option(None, "--agent", metavar="NAME",
                              help="Only what one writer staged. `unowned` "
                                   "names the ops nobody signed."),
) -> None:
    """What is staged but not yet applied.

    One line per op by default — its position, its short id, the op, what it is
    about, and the detail clipped. `--full` gives the table instead, which is
    the one to reach for when two answers read the same at the width above.

    `--agent` narrows it to one writer, and where several agents stage into one
    tray that is what makes their proposals reviewable one at a time rather
    than as an overlay. The roster line under the listing counts everybody
    either way, so a narrowed reading always says what it is not showing.
    """
    _tray(pending.load(), full, details=_PENDING_DETAIL, subject="vertex",
          heading="STAGED", title="Staged", column="Vertex",
          actions="`dg apply` to write, `dg drop <id>` to unstage",
          expand="dg pending --full", roster=_trays_roster(), agent=agent)
    _say_arriving()


def _say_arriving() -> None:
    """Name a waiting contribution wherever a tray is listed.

    Quarantine is what keeps an unadjudicated op out of every reading in this
    clone, and the cost of that is a batch a reader could miss entirely — the
    ops are real, they are about to land, and `dg pending` is where somebody
    looks to find out what is outstanding. So the batch still appears in one
    place; what does not happen is it appearing *as though it were yours*.
    """
    proj = project.find()
    n = integrate_mod.waiting(proj.root)
    if not n:
        return
    raw = integrate_mod.load_incoming(proj.root)
    open_now = len([f for f in raw.get("contested", [])
                    if not f.get("resolution")])
    con.print(f"[yellow]ARRIVING[/]  {n} op(s) from "
              f"{_x(raw.get('source', '?'))}, not staged"
              + (f" — {open_now} contested and unanswered" if open_now else "")
              + "\n[dim]`dg incoming` to read them. Commits are denied until "
                "this is settled.[/]")


def _tray(ops: list[dict], full: bool, *, details: dict, subject: str,
          heading: str, title: str, column: str, actions: str,
          expand: str, roster: dict | None = None,
          agent: str | None = None,
          narrow: str = "dg pending --agent <name>") -> None:
    """One staging tray, listed or tabulated. Both trays read through here.

    The two used to be a table apiece, near-identical and free to drift; they
    are peers, and one of them compact while the other is box-drawn is exactly
    the asymmetry the rest of this file spends its comments removing. So the
    shape lives here once and the callers pass only what genuinely differs:
    which store's key names the subject, and which command unstages one op.

    `agent` narrows the listing to one writer. The roster is printed **whether
    or not it matched**, and that is the point of printing it at all: a
    mistyped name would otherwise produce an empty listing that reads exactly
    like an empty tray, and the roster is the list the reader meant to type
    from. It is also what stops the narrowed heading lying — `STAGED 12 op(s)`
    is true of the selection and false of the tray, and the line underneath is
    where the other twelve are accounted for.
    """
    if agent:
        ops = [o for o in ops if pending.named(o) == agent]
    if not ops:
        con.print(f"[dim]nothing staged by {_x(agent)}[/]" if agent
                  else "[dim]nothing staged[/]")
        # `matched=False`: "showing scout-c" over a listing showing nothing
        # contradicts the line above it, and the name has already been said.
        _roster_line(roster, agent, narrow, matched=False)
        return
    (_tray_table if full else _tray_listing)(
        ops, details=details, subject=subject, heading=heading, title=title,
        column=column)
    _roster_line(roster, agent, narrow)
    con.print(f"[dim]{actions}[/]")
    if not full:
        con.print(compact.hint(expand, "the table, nothing clipped"))


def _clear_tray(clear_all, clear_one, agent: str | None, path, unit: str, *,
                other, other_cmd: str) -> None:
    """`dg clear` and `dg task clear`, whole or narrowed to one writer.

    A name that matched nothing is reported as a **failure**, not as a clear of
    zero ops. The two are indistinguishable in the tray afterwards and mean
    opposite things — one is "there was nothing of theirs", the other is "you
    typed a name nobody staged under, and the ops you meant to reject are still
    there" — and only the second is a reason to look again. Same reasoning as
    `_may_apply_for`, on the verb that does not write.

    **The clears are per store and rejecting a proposal is not.** `dg apply
    --agent` spans both trays because applying always did; these two never did,
    and unifying them here would quietly change what a bare `dg clear` means.
    So the narrowed form names what it could not reach instead — turning a
    proposal down and leaving half of it staged is exactly the quiet leftover
    the cross-tray roster exists to catch, and catching it one command earlier
    is cheaper than reading it off a roster afterwards.
    """
    if not agent:
        clear_all(path)
        con.print("[green]cleared[/]")
        return
    n = clear_one(agent, path)
    if not n:
        known = _trays_roster()
        listed = " · ".join(f"{_x(k)} {c}" for k, c in known.items())
        con.print(f"[red]nothing cleared — no {unit} staged by {_x(agent)}[/]\n"
                  f"[dim]staged by  {listed or 'nobody'}[/]")
        raise typer.Exit(1)
    con.print(f"[green]cleared[/] {n} {unit}(s) staged by {_x(agent)}")
    try:
        rest = len(pending.mine(pending.load(other),
                                pending.addressed(agent))[0])
    except (OSError, ValueError):
        return          # an unreadable neighbour costs the note and nothing else
    if rest:
        con.print(f"[dim]{rest} more staged by {_x(agent)} in the other tray — "
                  f"`{other_cmd} --agent {_x(agent)}`[/]")



def _roster_line(roster: dict | None, agent: str | None,
                 narrow: str = "dg pending --agent <name>",
                 matched: bool = True) -> None:
    """Who has work in the trays, and how much each of them has.

    Printed **only where somebody is named**, which is the rule the owner
    column in `_tray_listing` already follows and for the same reason: a
    project that never sets an identity is owed a reading identical to the one
    it had before ownership existed, and a line saying `unowned 3` to a single
    writer is noise dressed as information.

    It counts across both trays even though the listing above it shows one. The
    two are staged independently and applied as a pair, so an agent with no
    decision ops and five task ops is *present* in the review — and a reader
    who saw a per-tray roster would conclude it had gone home.
    """
    if not roster or list(roster) == [pending.UNOWNED]:
        return
    cells = " · ".join(f"{_x(n)} {c}" for n, c in roster.items())
    shown = f"  [dim]— showing[/] {_x(agent)}" if agent and matched else ""
    con.print(f"[dim]staged by[/]  {cells}{shown}")
    if not agent:
        con.print(compact.hint(narrow, "one writer alone"))


def _trays_roster() -> dict[str, int]:
    """The roster across both trays, for whichever listing is about to print it.

    Reads the *other* tray as well as its own, and forgives it entirely:
    `_staged_ops` reports an unreadable tray and owns an exit code, which is
    right for `apply` and wrong here, where the roster is a courtesy line under
    a listing that has already succeeded. `C-F22` is the same shape — one
    tray's failure must not reach the other's reading — so an unparseable
    neighbour costs this line its counts and nothing else.
    """
    proj = project.find()
    def read(path):
        try:
            return pending.load(path)
        except (OSError, ValueError):
            return None
    return pending.roster(read(proj.pending), read(proj.task_pending))


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
    # `by` only where somebody is named, and never a column of its own. A tray
    # nobody owns must read exactly as it did before ownership existed — that
    # is the whole claim a single writer is owed — and an "Owner" column of
    # dashes would break it for every project that will never set an identity.
    _say(compact.listing(
        [(f"{i:>{iw}}  {o.get('ref') or '—'}",
          compact.pad(k, kw) + "  " + _op_subject(o, subject),
          _tray_detail(o, details), _by(o))
         for i, (o, k) in enumerate(zip(ops, kinds))],
        width=_width(), markup=True))


def _by(o: dict) -> str:
    """Who staged this op, where anybody did. Empty otherwise — see the note in
    `_tray_listing` about a column of dashes."""
    return f"by {_x(o['by'])}" if o.get("by") else ""


def _tray_table(ops: list[dict], *, details: dict, subject: str,
                heading: str, title: str, column: str) -> None:
    """The detailed tray: every detail in full, every field its own column.

    The owner column appears only when the tray has one, for the reason
    `_tray_listing` gives.
    """
    t = Table(header_style="bold", title=title)
    owned = any(o.get("by") for o in ops)
    for c in ("#", "id", "Op", column, "Detail", *(["By"] if owned else [])):
        t.add_column(c)
    for i, o in enumerate(ops):
        t.add_row(str(i), o.get("ref") or "—", _x(o.get("op") or "?"),
                  _op_subject(o, subject), _tray_detail(o, details),
                  *([_x(o.get("by") or "—")] if owned else []))
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

    Through `_x`, like every other field these views read out of the tray —
    which also settles the type, since it renders whatever it is given. Every
    caller is on the path a stuck tray sends the reader down, and what a
    hand-edit puts in an id field is not always a string: a number reached rich
    as an `int`, which it refuses to render, and took the whole listing with it
    — leaving `dg clear` as the way out of a tray that could have been read and
    fixed one op at a time.
    """
    return _x(o.get(first) or o.get("from") or o.get("id") or "—")


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


def _fields_detail(o: dict) -> str:
    """A `set_fields` op as the fields it writes, for either tray.

    One function in both tables, because it is one op in both stores and a
    second rendering is how two doors come to describe the same thing
    differently — which is the drift this file's comments spend most of their
    length preventing.
    """
    return ", ".join(f"{k} {_x(o[k] or '—')}"
                     for k in pending.FIELDS if k in o)


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
    "set_fields": _fields_detail,
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
                          supersede=editor.supersedes(kind, op))
    con.print(f"[green]updated[/] op {i} — {len(new)} op(s), review with "
              f"`dg pending`")


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
    `commands/serve.md` tells a user to work in the browser and a terminal at
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
def clear(
    agent: str = typer.Option(None, "--agent", metavar="NAME",
                              help="Unstage only what one writer staged."),
) -> None:
    """Unstage everything, or one writer's contribution with `--agent`.

    `--agent` is the reject verb for a shared tray: read one agent's proposal
    with `dg pending --agent a`, decide against it, and turn it down without
    touching the other three. Unlike `dg apply --agent` it is open to any
    caller — a bare `dg clear` already discards everybody's work whoever runs
    it, so gating the *narrower* form would only push a reader toward the
    blunter one.
    """
    _clear_tray(pending.clear, pending.clear_agent, agent, None, "op",
                other=task_pending.path(), other_cmd="dg task clear")


@app.command(rich_help_panel=STAGE)
def apply(
    dry_run: bool = typer.Option(False, "--dry-run", "-n"),
    all_: bool = typer.Option(False, "--all",
                              help="apply every staged op, whoever staged it"),
    mine: bool = typer.Option(False, "--mine",
                              help="apply only what this writer staged"),
    agent: str = typer.Option(None, "--agent", metavar="NAME",
                              help="apply what ONE writer staged, by the name "
                                   "`dg pending` lists"),
) -> None:
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
    ok = True
    # The trays are held **across the read**, not merely re-taken at the end.
    # These two `_staged_ops` calls used to sit outside every lock, so a
    # `dg drop` landing between them and `pending.discard` was a drop that
    # succeeded, named its op, and watched the op reach the store anyway —
    # both commands reporting success over one op. Audit W-F2, and the reason
    # this span starts here rather than three lines down.
    #
    # Outside `writing()`, because trays-then-stores is the one lock order every
    # writer in the tool uses; see `applying.trays`.
    with applying.trays(proj):
        ops = _staged_ops(proj.pending, "dg clear")
        task_ops = _staged_ops(proj.task_pending, "dg task clear")
        if ops is None and task_ops is None:
            raise typer.Exit(1)
        if not ops and not task_ops:
            con.print("[dim]nothing staged[/]")
            return
        chosen = [f for f, on in (("--all", all_), ("--mine", mine),
                                  ("--agent", bool(agent))) if on]
        if len(chosen) > 1:
            con.print(f"[red]{' and '.join(chosen)} ask for different "
                      f"scopes — pick one[/]")
            raise typer.Exit(2)
        if agent and not _may_apply_for(agent, ops, task_ops):
            raise typer.Exit(1)
        scoped = _scope(ops, task_ops, all_=all_, mine=mine, agent=agent)
        if scoped is None:
            raise typer.Exit(1)
        ops, task_ops, left = scoped
        if not ops and not task_ops and not (ops is None or task_ops is None):
            whose = f"staged by {_x(agent)}" if agent else "of yours staged"
            con.print(f"[dim]nothing {whose} — {left}[/]")
            return
        # One lock span across both batches, not one per batch. They stay
        # independent — either can be refused while the other is written, which
        # is the whole point of two `_apply_*` calls — but the *pair on disk* is
        # never observable half-applied by another writer, or by a reader that
        # takes the same lock. A dry run writes nothing and needs none. Audit
        # F27.
        with contextlib.nullcontext() if dry_run else applying.writing(proj):
            if ops:
                ok = _apply_decisions(ops, dry_run) and ok
            if task_ops:
                ok = _apply_tasks(task_ops, dry_run) and ok
        if left:
            con.print(f"[dim]{left}[/]")
    if not ok or ops is None or task_ops is None:
        # Something was refused, or one tray could not even be read. Exit
        # nonzero so nothing downstream reads this as "everything staged is now
        # written" — but only after both batches have had their turn.
        raise typer.Exit(1)


def _may_apply_for(agent: str, ops, task_ops) -> bool:
    """`pending.refuse_apply_for`, rendered for a terminal.

    The judgement is not here — it is shared with the browser, which returns
    the same sentence as a 400. What is here is the second line: a refusal a
    reader cannot act on is the shape `C-F17` is about, and the flags are what
    they act on.
    """
    why = pending.refuse_apply_for(agent, ops, task_ops)
    if why is None:
        return True
    con.print(f"[red]✗ nothing written — {_x(why)}[/]\n"
              f"[dim]`dg apply` writes your own; only a caller with no "
              f"$DG_AGENT applies for somebody else, and `dg pending` lists "
              f"the names[/]")
    return False


def _scope(ops, task_ops, *, all_: bool, mine: bool, agent: str | None = None):
    """Which of the staged ops this caller may apply, and what it is leaving.

    `(ops, task_ops, note)`, or `None` having refused. `note` is empty when
    nothing was left behind, which is every single-writer run.

    The three configurations, and the first is not a code path:

    - **nothing owned** — `mine` takes the lot, whoever is asking, because an
      unowned caller owns the unowned ops. A project that never sets
      `$DG_AGENT` therefore behaves exactly as it did before ownership existed,
      and cannot reach the refusal below.
    - **an owned caller** — applies its own and says what it left. Another
      agent's half-composed batch is not this one's to write, which is `C-F16`:
      a draft `close` applied by somebody else is a DECIDED answer whose only
      exit is a `reopen`, filing a reversal that never happened.
    - **an unowned caller, somebody else's work in the tray** — refused, and
      this is the case that must not be silent. The supervisor is usually right
      to apply everything, and `--all` says so in one word; but a `dg apply`
      that quietly swept up an agent's draft is the same failure wearing the
      supervisor's clothes.

    `--mine` is offered from an unowned caller too, where it means the unowned
    ops: a person tidying their own work out of a tray an agent is also using.

    `--agent NAME` is the fourth, and it is a *scope*, not a fourth authority:
    it supplies the identity the other three inherit from the environment, and
    the split, the note and the validation are the same ones. It exists for the
    review the other three cannot express — several agents proposing
    **alternatives** into one tray, where the supervisor means to write one of
    them and turn the rest down. Where agents propose complementary pieces of
    one elaboration the union is what you want, and `--all` already is it.
    `_may_apply_for` holds the authority question, which is separate.
    """
    # `None` is a tray that could not be read, and it stays `None` all the way
    # through: `apply` reports it separately and owns the exit code for the
    # pair, and a tray nobody can parse must not stop the other batch. Scoping
    # it as if it were empty would take a decision batch down with an
    # unreadable task tray, which is `C-F22` exactly.
    if all_ or (ops is None and task_ops is None):
        return ops, task_ops, ""
    # `--agent` supplies the identity the other two inherit, and everything
    # downstream is unchanged: one `mine` split, the same note about what was
    # left, the same refusal — except that a caller who *named* whose work to
    # take cannot be surprised by taking it, so the refusal below does not fire.
    me = pending.addressed(agent) if agent else pending.owner()
    keep, theirs = pending.mine(ops or [], me)
    tkeep, ttheirs = pending.mine(task_ops or [], me)
    if not theirs and not ttheirs:
        return ops, task_ops, ""
    keep = keep if ops is not None else None
    tkeep = tkeep if task_ops is not None else None
    # "unowned" rather than `None`: an op with no `by` was staged by somebody
    # who set no identity — the supervisor, or a door that stages as nobody —
    # and printing the Python value there tells a reader nothing they can act
    # on. Named this way in both directions, so `--mine` from an unowned caller
    # and a refusal naming unowned work use one word for one thing.
    who = ", ".join(sorted({str(o["by"]) if o.get("by") else "unowned"
                            for o in (*theirs, *ttheirs)}))
    n = len(theirs) + len(ttheirs)
    if not mine and not agent and pending.owner() is None:
        con.print(f"[red]✗ nothing written — {n} staged op(s) belong to "
                  f"{_x(who)}[/]\n"
                  f"[dim]`dg apply --all` writes theirs too, `dg apply --mine` "
                  f"only yours, `dg apply --agent <name>` one of them; "
                  f"`dg pending` shows who staged what[/]")
        return None
    return keep, tkeep, f"{n} op(s) left staged, by {_x(who)}"


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
    con.print(f"[green]✓[/] applied {r.applied} op(s) → {r.store}; the view is "
              f"regenerated on demand with `dg render`")
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
    con.print(f"[green]✓[/] applied {r.applied} op(s) → {r.store}; the view is "
              f"regenerated on demand with `dg task render`")
    return True


@app.command(name="render", rich_help_panel=HONEST)
def render_cmd() -> None:
    """Regenerate decision-graph.md from the store."""
    render.write(_g())
    con.print(f"[green]✓[/] wrote {project.find().view}")


# ---- agents --------------------------------------------------------------
#
# Naming moved in here because every value `$DG_AGENT` has gone wrong on was one
# a launcher invented. `dg` still cannot set its caller's environment, so the
# variable stays the way a name *reaches* an agent — what these commands remove
# is the need to make one up. See `dgraph/agents.py` for why a claim never
# expires and why running out is an error rather than a fallback.

agent_app = typer.Typer(
    add_completion=False,
    help="Names for the writers sharing one tray. `dg agent claim` hands out "
         "a free one; nothing ever reuses a name behind your back.",
)
app.add_typer(agent_app, name="agent", rich_help_panel=WRITERS)


@agent_app.command("claim")
def agent_claim(
    budget: str = typer.Option(
        None, "--budget", "-b",
        help="how long this agent may run: `1800`, `30m`, `2h`, or `infinite`"),
) -> None:
    """Take a free name and hold it. Prints the name, and nothing else.

    **Bare stdout on purpose**, because the only sensible caller is a
    substitution:

        DG_AGENT=$(dg agent claim) claude -p "..."

    A launcher's job, not an agent's: shell state does not survive between a
    coding agent's tool calls, so one that claimed a name for itself could not
    hold on to it.

    `--budget` records how long the agent may run. Nothing here stops it at
    that point — `dg` is not in the agent's process tree, and `timeout 1800
    <however you spawn one>` is the launcher's half. What the budget buys is
    the *hand-back*: `dg agent expire` parks whatever an out-of-time agent is
    holding, so work stops looking like it is being done by something that
    stopped. See `dg agent expire` and `agentic/README.md`.
    """
    try:
        seconds = limits.span(budget)
    except limits.BadSpan as exc:
        # Raised, where a bad `$DG_WRITE` is merely ignored. A misread budget
        # is not a wider rule, it is a different number, and this is the one
        # moment the launcher can still fix it.
        con.print(f"[red]✗ {_x(exc)}[/]")
        raise typer.Exit(1)
    try:
        name = agents.claim(budget=seconds)
    except agents.Exhausted as exc:
        # An error, never a fallback. Handing back a name somebody holds — or
        # inventing a numbered one outside the lists — is the silent conflation
        # the whole ownership stamp exists to prevent, and the numbered one is
        # worse for looking deliberate.
        con.print(f"[red]✗ no name to claim — {_x(exc)}[/]")
        if exc.releasable:
            # `releasable` counts leases with nothing staged, which is what
            # `prune` used to free. It now keeps back the ones still holding
            # DOING work, so the count is an upper bound and saying "frees
            # those now" would promise names that stay held. The subtraction
            # happens here because `Exhausted` is raised in a module that
            # cannot read the task store.
            free = exc.releasable - len(_still_working())
            if free > 0:
                con.print(f"[dim]{free} have nothing staged under them: "
                          f"`dg agent prune` frees those now[/]")
            else:
                con.print("[dim]every idle name is still holding work — "
                          "`dg agent expire`, or park what is DOING, then "
                          "`dg agent prune`[/]")
        else:
            con.print("[dim]every name has ops in a tray — `dg apply` or "
                      "`dg clear` first, then `dg agent prune`[/]")
        raise typer.Exit(1)
    # `print`, not `con.print`: rich wraps at the terminal width and would put a
    # newline inside a long name, and this string is going into a variable.
    print(name)


@agent_app.command("list")
def agent_list() -> None:
    """Every name held, when it was claimed, and what it still has staged."""
    leases = agents.load()
    staged = agents.in_trays(project.find())
    if not leases and not staged:
        con.print("[dim]no names claimed[/]")
        return
    t = Table(header_style="bold", title="Names held")
    for c in ("Name", "Since", "Staged", "Holding", "Budget", "Seen"):
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
            # holding work is the state `dg agent expire` exists for, and it is
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
        t.add_row(_x(name), since, str(staged.get(name, 0)),
                  ", ".join(rec.get("holding") or ()) or "[dim]—[/]", spend,
                  seen)
    con.print(t)
    if any(agents.over_budget()):
        con.print("[dim]`dg agent expire` hands back what the spent ones "
                  "hold[/]")
    if quiet_names:
        # No command is offered, deliberately. An elapsed budget is a fact and
        # `expire` acts on it; silence is a suspicion, and the only safe next
        # step is a person deciding whether that agent is thinking or gone.
        con.print(f"[dim]silent = no `dg` call and no file write for "
                  f"{limits.approx_span(limits.silent_after())}, while holding "
                  f"work. A long build looks the same as a dead agent — check "
                  f"before parking anything. ${limits.SILENT_ENV} sets the "
                  f"window[/]")
    con.print(f"[dim]{len(agents.sequence()) - len(set(leases) | set(staged))} "
              f"of {len(agents.sequence())} names free[/]")


@agent_app.command("release")
def agent_release(
    name: str = typer.Argument(..., help="the name to stop holding"),
    force: bool = typer.Option(
        False, "--force",
        help="release even while it holds DOING work, stranding those tasks"),
) -> None:
    """Stop holding one name, so it can be claimed again.

    Says nothing about the tray: releasing a name whose ops are still staged is
    legitimate — the launcher is done, the review is not — and `dg agent claim`
    still will not hand it out while those ops are there.

    It does say something about held **work**, and refuses. The same stranding
    `dg agent prune` avoids arrives through this door one name at a time; see
    that command for what it costs. `--force` overrides.
    """
    if not force:
        held = _still_working().get(name)
        if held:
            con.print(f"[red]✗ {_x(name)} still holds {', '.join(held)}[/]\n"
                      f"[dim]releasing it now strands that work — DOING, with "
                      f"no holder recorded and `dg task start` refusing it. "
                      f"`dg agent expire`, or `dg task park <id> --why …`, "
                      f"then release. `--force` releases anyway[/]")
            raise typer.Exit(1)
    if not agents.release(name):
        con.print(f"[red]nothing released — {_x(name)} is not held[/]\n"
                  f"[dim]`dg agent list` shows what is[/]")
        raise typer.Exit(1)
    con.print(f"[green]released[/] {_x(name)}")


@agent_app.command("prune")
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
    holders = {} if force else _still_working()
    gone = agents.prune(keep=holders)
    kept = sorted(holders)
    if not gone and not kept:
        con.print("[dim]nothing to prune — every name held has ops staged[/]")
        return
    if gone:
        con.print(f"[green]released[/] {len(gone)}: {_x(', '.join(sorted(gone)))}")
    for line in _stranding(kept, holders):
        con.print(line)
    if kept:
        con.print("[dim]`dg agent expire` parks what an out-of-time agent "
                  "holds, or `dg task park <id> --why …` by hand — then prune "
                  "again. `--force` releases them and strands the work[/]")


def _still_working() -> dict[str, list[str]]:
    """`{agent: [tids]}` for leases holding work that is genuinely still DOING.

    The guard behind `prune` and `release`, and the reason it is here rather
    than in `agents.py`: answering it means reading the task store, which that
    module deliberately knows nothing about.

    Soft on purpose. A project with no task store cannot strand a task, and
    `dg agent prune` has always worked without one — so a missing store is an
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
        tg = _teff(TaskGraph.load(proj.tasks))
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
    return [f"[yellow]kept[/] {_x(n)} — still holds "
            f"{', '.join(holders[n])}" for n in names]


@agent_app.command("expire")
def agent_expire() -> None:
    """Hand back what an out-of-budget agent is holding.

    A task left `DOING` by an agent that stopped reads exactly like one being
    worked on. That is the failure the lease file exists to make visible, and
    `dg task park --why` is the verb that already fixes it — so an expired
    budget parks, naming the budget as what stopped the work. Parked work is
    still outstanding, so nothing downstream is released: the next agent can
    pick it up, which is the whole point.

    **This does not stop a process.** `dg` is not in the agent's process tree
    and never was; `timeout 1800 <however you spawn one>` is the launcher's
    half. This is the half that makes the queue honest afterwards, and it is
    worth running even when the agent died on its own.

    Staged, like everything else — each park goes into the tray under the
    *agent's own name*, so `dg pending --agent <name>` shows it beside whatever
    that agent had already proposed and `dg apply --agent <name>` takes the
    batch. There is no `--apply` here on purpose: `dg apply` is the only door
    that writes, and a second one would be a second place the tray's ownership
    rules have to be right.
    """
    spent = agents.over_budget()
    if not spent:
        con.print("[dim]nothing expired — every budget has time on it[/]")
        return
    tg = _teff(_tg())
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
            con.print(f"[yellow]{_x(name)}[/] is {over} over budget and holds "
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
            _tstage_all([{"op": "set_status", "task": tid, "status": "PARKED",
                          "why": why, "date": _date.today().isoformat()}
                         for tid in held])
        staged += len(held)
        con.print(f"[green]staged[/] park of {', '.join(held)} "
                  f"[dim]as {_x(name)}, {over} over budget[/]")
    if staged:
        con.print(f"[dim]{staged} park(s) staged — `dg pending` to read them, "
                  f"`dg apply` to take them[/]")


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
    # Outstanding, so not `dim` like DROPPED; put down, so not urgent like
    # TODO. The colour says which half of the store it is in, the word says
    # what kind of outstanding it is — the decision store's own convention.
    "PARKED": "cyan",
    "DONE": "green",
    "DROPPED": "dim",
}


def _maybe_tasks():
    """The task store if this project has one, `None` otherwise.

    Unlike `_tg`, does not exit: the callers are cross-store *policies*, and a
    project tracking only decisions is an ordinary project rather than a
    misconfigured one.
    """
    proj = project.find()
    if not proj.has_tasks:
        return None
    try:
        return TaskGraph.load(proj.tasks)
    except UNREADABLE:
        return None


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


def _next_hint(offer) -> str:
    """`  next unused: D50`, or nothing it can say.

    Appended to the missing-`--id` refusals. `dg add` only prefills an id in
    the editor, so in a clone with a granted range the flag path would
    otherwise leave a writer to look the range up and count — and the id it
    then guesses is refused by a rule it cannot see from where it is standing.
    """
    try:
        return f"\n[dim]next unused: {offer()}[/]"
    except ranges.RangeError as exc:
        # Said rather than swallowed. A hint that simply vanishes leaves the
        # writer with "missing --id" and no id, and no way to tell a clone that
        # is out of ids from a message that never offered one.
        return f"\n[red]{_x(exc)}[/]"
    except Exception:
        # A hint must never break the refusal it decorates, and anything left
        # here is reported by the act that meets it.
        return ""


def _tcompose(kind: str, tg: TaskGraph, **kw) -> list[dict]:
    """Run the editor over a task template, turning refusals into CLI exits.

    `_compose`'s twin, and separate for the reason the modules behind them are:
    the two buffers must not become one. What they do share is this shape — the
    same buffer path printed, the same two exceptions caught, the same "nothing
    staged" promise — because a writer who has aborted one should recognise
    what happened when they abort the other.
    """
    proj = project.find()
    con.print(f"[dim]buffer: {proj.edit}[/]")
    if not editor.is_emacs(editor.resolve_editor()):
        con.print("[dim]note: in-buffer navigation needs emacs — "
                  "`dg task node <id>` for one piece of work in full[/]")
    go = (task_editor.compose_add if kind == "add_task"
          else task_editor.compose_done)
    try:
        # The *effective* decision graph, as the composers resolve it: the two
        # doors onto `Because` must accept the same ids, and the buffer's
        # frontier must be the one the writer is standing in front of.
        return go(tg, _decisions_eff_or_none(), **kw)
    except ranges.RangeError as exc:
        # `_compose`'s twin — see there. The buffer prefills an id, so a
        # used-up grant stops it before the editor opens.
        con.print(f"[red]✗ nothing staged[/]\n{_x(exc)}")
        raise typer.Exit(1) from None
    except editor.EditorAbort as exc:
        con.print(f"[yellow]aborted[/] {_x(exc)}")
        raise typer.Exit(1) from None
    except editor.EditorError as exc:
        con.print(f"[red]✗ nothing staged[/]\n{_x(exc)}")
        raise typer.Exit(1) from None


def _said_dialect(tg: TaskGraph, tid: str) -> None:
    """Say when composing in org changes how prose already stored will read.

    A task carries one `format` for its whole record, so an outcome written in
    the buffer makes the record org — including a note that was typed as a
    flag and has been markdown until now. The two dialects differ over
    `*asterisks*`, which is small and not nothing, and the writer is the only
    one who can tell whether it matters. Said, not refused.
    """
    t = tg.tasks.get(tid)
    if t is not None and t.format is None and (t.note or t.stops):
        con.print(f"[dim]{tid}'s prose was recorded as markdown and now reads "
                  f"as org — *asterisks* mean bold there, not italic[/]")


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
    con.print(f"[green]✓[/] created {proj.tasks}; run `dg task render` for "
              f"{proj.task_view.name}")
    _report_ignored(proj)


#: How each edge kind reads in a CLI message, phrased from the *new* task's
#: side — every flag below says "this task, relative to those". One table
#: because the two relations share no verb, and a message loose enough to cover
#: both would describe neither: `--after` makes this task wait, and
#: `--discovered-during` deliberately does not.
# The relation vocabulary lives in `task_pending`, beside the composers that
# apply its rules — the CLI is no longer its only caller. Only the wording
# survives here, for `_say_relation`; every check and every op-building step
# went with the composers, so a second copy cannot grow back by being handy.
_REL = task_pending.REL


def _say_relation(tid: str, kind: str, fresh: list[str],
                  already: list[str]) -> None:
    """What `task_pending.compose_dep` staged, said after the write."""
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
                                help="comma-separated decisions this work "
                                     "exists because of"),
    evidence_for: str = typer.Option(None, "--evidence-for",
                                     help="the decision this work will inform"),
    note: str = typer.Option(None, "--note", "-n"),
    edit: bool = typer.Option(None, "--edit/--no-edit", "-e",
                              help="Compose in $EDITOR (default: emacs)."),
) -> None:
    """Stage a new task."""
    tg = _teff(_tg())

    if _wants_editor(edit):
        seed = {k: v for k, v in (
            ("id", tid), ("title", title), ("area", area), ("note", note),
            ("because", because), ("evidence_for", evidence_for),
            ("after", _csv(after)),
            ("discovered_during", _csv(discovered_during)),
        ) if v}
        ops = _tcompose("add_task", tg, seed=seed)
        # Vetted before staging, exactly as `dg add --edit` is: the buffer is
        # the one route with no flag-path guard in front of it, and a tray
        # every other writer shares is the wrong place to discover that an op
        # can never apply. F30.
        _tstage_all(ops)
        con.print(f"[green]staged[/] {len(ops)} op(s) — add {ops[0]['id']}")
        _twarn_stuck()
        return

    missing = [f for f, val in (("--id", tid), ("--title", title),
                                ("--area", area)) if not val]
    if missing:
        con.print(f"[red]missing option(s): {', '.join(missing)}[/]\n"
                  f"[dim]give them as flags, or use `dg task add --edit`[/]"
                  + (_next_hint(lambda: task_editor.next_id(tg))
                     if not tid else ""))
        raise typer.Exit(2)
    # Every rule, and the op list itself, is `task_pending.compose_add` —
    # shared with `POST /api/add-task`. The task and its edges are one write:
    # a task landing without them is not a partial batch that something
    # refuses, it is a task that reads as startable, which is the whole of
    # audit F28. What stays here is the shape of a command-line failure, and
    # saying afterwards which relations were fresh and which were already held.
    parents, prompted = _csv(after) or [], _csv(discovered_during) or []
    try:
        ops = task_pending.compose_add(
            tg, _decisions_eff_or_none(), tid=tid, title=title, area=area,
            after=parents, discovered_during=prompted,
            because=_csv(because), evidence_for=evidence_for, note=note,
            stored=_tg())
    except pending.ApplyError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    said = [(kind, *task_pending.relation_ops(tg, tid, others, kind)[1:])
            for others, kind in ((parents, "precedes"),
                                 (prompted, "prompted")) if others]
    _tstage_all(ops)
    con.print(f"[green]staged[/] add {tid}")
    for kind, fresh, already in said:
        _say_relation(tid, kind, fresh, already)
    _twarn_stuck()


def _decisions_state() -> tuple[Graph | None, str | None]:
    """The decision store, and — where there isn't one — whether that is news.

    `(None, None)`: this project tracks only work. Ordinary, and every
    cross-graph view is written to expect it.

    `(None, reason)`: there *is* a store and it could not be read. Not
    ordinary, and not the same answer. The distinction is invisible to a view
    that only wants premises — it degrades either way — and decisive for a
    command whose whole output is one table per store, which has to say a table
    is missing rather than print the other one and call it the answer.
    """
    proj = project.find()
    if not proj.has_decisions:
        return None, None
    try:
        return Graph.load(proj.store), None
    except Exception as exc:
        return None, str(exc) or exc.__class__.__name__


def _decisions_or_none() -> Graph | None:
    """The decision store if this project has a readable one, else None.

    Task commands must work in a project that tracks only work, so every
    cross-graph view degrades to "no premise information" rather than failing.
    """
    g, unreadable = _decisions_state()
    if unreadable is not None:
        # Degrading silently would print "ready" for work whose premise is
        # undecided — a wrong answer, which is worse than an absent one.
        con.print(f"[dim]{project.STORE_NAME} could not be read, so premise "
                  f"information is missing here — `dg check`[/]")
    return g


def _decisions_eff_or_none() -> Graph | None:
    """`_decisions_or_none`, plus the decision ops already staged.

    What a task *buffer* has to read. The composers resolve `--because`
    against the effective decision graph so that a decision and the work it
    implies can be recorded in one batch — the A3 lesson — and a buffer
    resolving the same field against the store alone refuses what the flag
    accepts, and lists a frontier without the question just staged onto it.

    Where `_eff` stops the command, this degrades. A decision tray that no
    longer applies is not a reason `dg task done --edit` cannot write an
    outcome: the two stores stage independently, and this one is being read for
    context and one optional field. Said rather than refused, and never
    silently — a premise missing from the buffer must not be an absence with no
    explanation beside it.
    """
    g = _decisions_or_none()
    if g is None:
        return None
    try:
        return pending.preview(g)
    except pending.ApplyError as exc:
        con.print(f"[dim]the staged decision ops no longer apply cleanly, so "
                  f"this buffer shows {project.STORE_NAME} as it stands — "
                  f"`dg pending` to review[/]\n[dim]{_x(exc)}[/]")
        return g


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
    """Every task whose outcome bears on this decision, with its status.

    And with the outcome itself where there is one. An id and a status say
    that a measurement happened; only the outcome can be read against the
    answer, and reading it against the answer is the entire point of the link
    — most sharply where the work finished *after* the decision was settled,
    which is what `cross.evidence_after_deciding` reports in the store.
    """
    proj = project.find()
    if not proj.has_tasks:
        return []
    try:
        from dgraph import cross
        tg = TaskGraph.load(proj.tasks)
        out = []
        for t in cross.evidence(tg, did):
            task = tg.tasks[t]
            said = cross._one_line(task.outcome) if task.outcome else ""
            out.append(f"{t} ({task.status})" + (f" — {said}" if said else ""))
            # The reading, where there is one. Without it the panel shows a
            # result and no sign that anybody ever read it against the answer,
            # which is the difference between evidence and a loose measurement.
            for r in task.readings:
                if r.against == did:
                    out.append(f"  read {r.date} — "
                               f"{cross._one_line(r.note)}")
        return out
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


@task_app.command("amend", rich_help_panel=T_RECORD)
def task_amend(
    tid: str,
    title: str = typer.Option(None, "--title", "-t"),
    area: str = typer.Option(None, "--area"),
    note: str = typer.Option(None, "--note", "-n",
                             help="what this work involves"),
) -> None:
    """Correct how a task is worded or filed: its title, area or note.

    `dg amend`'s twin, and the same op — see there for what it will not touch
    and why. Here the line falls in the same place: a title and an area are how
    the work is referred to, while an outcome and a reason it stopped are dated
    records of what happened, and those are appended, never edited.

    To correct an outcome, `dg task start` and finish it again: the new one is
    recorded beside the old rather than over it, which is the whole of what
    `completions` is for.
    """
    tg = _teff(_tg())
    _require_task(tid, tg)
    op = {"op": "set_fields", "task": tid}
    op.update({k: v for k, v in (("title", title), ("area", area),
                                 ("note", note)) if v is not None})
    lines = _amended(tg.tasks[tid], op)
    # `_tstage` vets, and `task_pending.vet` holds every rule for the same
    # reason the decision store's does: the browser can post this op as data.
    _tstage(op)
    con.print(f"[green]staged[/] {tid}")
    for line in lines:
        con.print(f"  [dim]{line}[/]")
    if "title" in op:
        con.print(CITED)
    _twarn_stuck()


@task_app.command("link", rich_help_panel=T_RECORD)
def task_link(
    tid: str,
    because: str = typer.Option(
        None, "--because",
        help="comma-separated decisions to add to what this work exists "
             "because of"),
    evidence_for: str = typer.Option(None, "--evidence-for",
                                     help="the decision this work will inform"),
) -> None:
    """Point an existing task at a decision.

    The emergent case: work turns up a question nobody had written down, so you
    record the decision and then say which work raised it — after the fact, and
    often after the task is already done.

    A task rests on a set of premises, so `--because` appends to them rather
    than replacing them — `--because D01` then `--because D05` leaves the task
    resting on both.
    """
    tg = _teff(_tg())
    if not because and not evidence_for:
        con.print("[red]nothing to link[/]\n"
                  "[dim]pass --because or --evidence-for[/]")
        raise typer.Exit(2)
    try:
        ops = task_pending.compose_link(tg, _decisions_eff_or_none(), tid=tid,
                                        because=_csv(because),
                                        evidence_for=evidence_for)
    except pending.ApplyError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    _tstage_all(ops)
    con.print(f"[green]staged[/] {tid} linked")
    _twarn_stuck()


@task_app.command("unlink", rich_help_panel=T_RECORD)
def task_unlink(
    tid: str,
    because: str = typer.Option(None, "--because",
                                help="comma-separated premises to remove — a "
                                     "task rests on several, so name which"),
    evidence_for: bool = typer.Option(False, "--evidence-for",
                                      help="drop the single decision this work informs"),
) -> None:
    """Remove a task's link to a decision.

    The undo `dg task link` never had. A link recorded against the wrong
    decision, or one that stopped being true, is a correction the tool has to
    be able to make: hand-editing the store is the failure this command exists
    to prevent.
    """
    tg = _teff(_tg())
    if not because and not evidence_for:
        con.print("[red]nothing to unlink[/]\n"
                  "[dim]pass --because or --evidence-for[/]")
        raise typer.Exit(2)
    try:
        ops, was = task_pending.compose_unlink(
            tg, tid=tid, because=_csv(because), evidence_for=evidence_for)
    except pending.ApplyError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    _tstage_all(ops)
    con.print(f"[green]staged[/] {tid} unlinked from {', '.join(was)}")
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
    if not after and not discovered_during:
        con.print("[red]nothing to record[/]\n"
                  "[dim]pass --after or --discovered-during[/]")
        raise typer.Exit(2)
    try:
        ops, said = task_pending.compose_dep(
            tg, tid=tid, after=_csv(after) or [],
            discovered_during=_csv(discovered_during) or [])
    except pending.ApplyError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
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
    if not after and not discovered_during:
        con.print("[red]nothing to remove[/]\n"
                  "[dim]pass --after or --discovered-during[/]")
        raise typer.Exit(2)
    try:
        ops, said = task_pending.compose_undep(
            tg, tid=tid, after=_csv(after) or [],
            discovered_during=_csv(discovered_during) or [])
    except pending.ApplyError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    _tstage_all(ops)
    for kind, others in said:
        con.print(f"[green]staged[/] {tid} no longer {_REL[kind]['reads']} "
                  f"{', '.join(others)}")
    _twarn_stuck()


@task_app.command("start", rich_help_panel=T_RECORD)
def task_start(tid: str) -> None:
    """Stage a task moving to DOING, and say what it was waiting on.

    Starting is also what clears `released_by_drop`, so a prerequisite that was
    abandoned rather than finished has to be said *here* or it is never said to
    the person doing the work — the check is read by whoever runs it, and this
    is the moment nobody does.
    """
    tg = _teff(_tg())
    _require_task(tid, tg)
    _tstage({"op": "set_status", "task": tid, "status": "DOING"})
    con.print(f"[green]staged[/] {tid} → DOING")
    note = _start_note(tg, tid)
    if note:
        con.print(f"[yellow]note: {note}[/]")
    _twarn_stuck()


@task_app.command("park", rich_help_panel=T_RECORD)
def task_park(
    tid: str,
    why: str = typer.Option(None, "--why", "-w",
                            help="what stopped it — kept after it resumes"),
) -> None:
    """Stage a task as put down, and record what stopped it.

    The status between `DOING` and `DROPPED`, for work nobody is doing and
    nobody has given up on. Unlike dropping, it settles nothing downstream:
    parked work is still outstanding, so everything that waited on it goes on
    waiting, and there are no `--keep`/`--drop-too` verdicts to give — that
    machinery exists because abandoning work releases its dependants, and
    parking asserts the opposite.

    The reason is appended rather than assigned, and nothing ever clears it.
    Every other prose field here describes the current state and is cleared
    when that state stops holding; a park is a spell that *ended*, so the
    record of it outlives the status, and a task put down three times says so.

    Pick it up with `dg task start`.
    """
    tg = _teff(_tg())
    _require_task(tid, tg)
    t = tg.tasks[tid]
    if t.parked:
        con.print(f"[red]{tid} is already PARKED[/]\n"
                  f"[dim]`dg task start {tid}` first — parking twice over would "
                  f"record two spells where there was one[/]")
        raise typer.Exit(1)
    if t.resolved:
        # Not refused: work can be reopened, and parking is a legitimate place
        # to put it down on the way. Said out loud because the status it leaves
        # is not the one the caller was looking at.
        con.print(f"[yellow]note: {tid} was {t.status}; parking it makes it "
                  f"outstanding again[/]")
    why = why or _ask("Why is this being put down?", "--why/-w")
    _tstage({"op": "set_status", "task": tid, "status": "PARKED",
             "why": why, "date": _date.today().isoformat()})
    con.print(f"[green]staged[/] {tid} → PARKED")
    _twarn_stuck()


@task_app.command("done", rich_help_panel=T_RECORD)
def task_done(
    tid: str,
    outcome: str = typer.Option(None, "--outcome", "-o",
                                help="what the work produced: a path, a PR, a note"),
    edit: bool = typer.Option(None, "--edit/--no-edit", "-e",
                              help="Compose the outcome in $EDITOR."),
) -> None:
    """Stage a task as finished. Records what it produced.

    `--edit` composes the outcome in a buffer with the work's own context
    beside it — what it unblocks, and the decision it was for. An outcome is
    the one field of a task worth more room than a shell argument gives it.
    """
    tg = _teff(_tg())
    _require_task(tid, tg)
    waiting = tg.waiting_on(tid)
    if waiting:
        con.print(f"[yellow]note: {tid} still waits on "
                  f"{', '.join(waiting)}[/]")
    if _wants_editor(edit):
        seed = {"outcome": outcome} if outcome else None
        ops = _tcompose("set_status", tg, tid=tid, seed=seed)
        _tstage_all(ops)
        con.print(f"[green]staged[/] {tid} → DONE")
        _said_dialect(tg, tid)
        _twarn_stuck()
        return
    outcome = outcome or _ask(
        "Outcome (what did this produce? a path, a PR, a note)", "--outcome/-o")
    _tstage({"op": "set_status", "task": tid, "status": "DONE",
             "outcome": outcome, "done": _date.today().isoformat()})
    con.print(f"[green]staged[/] {tid} → DONE")
    _twarn_stuck()


def _csv(raw: str | None) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()] if raw else []


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

    # Required now, where it used to be optional here and required in the web
    # form. The reason is an archived record with a date rather than a field
    # that gets cleared, so a drop without one writes an empty entry into the
    # one part of this store that is kept forever — and the two doors onto the
    # same act should not disagree about what it takes to walk through them.
    why = why or _ask("Why is this not being done?", "--why/-w")
    today = _date.today().isoformat()
    op = {"op": "set_status", "task": tid, "status": "DROPPED",
          "why": why, "date": today}
    # The drop and its cascade are one write. They are one judgement — the
    # operator was asked about each cascaded task and answered — and a tray
    # holding only the first half says the opposite of what they answered.
    cascade = [{"op": "set_status", "task": t, "status": "DROPPED",
                "why": f"abandoned along with {tid}", "date": today}
               for t in sorted(doomed)]
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
    # One read for the whole listing rather than one per row.
    try:
        held = agents.holdings()
    except (OSError, ValueError):
        held = {}
    entries, areas = [], []
    for tid in frontier:
        task = tg.tasks[tid]
        waiting, gate = waiting_for(tid)
        aside = []
        # First, because it is the only thing here that names somebody rather
        # than describing the graph. `DOING` says a task is claimed; without
        # this it does not say by whom, and a fan-out cannot tell a stalled
        # agent from a slow one.
        #
        # Read from `.dgraph-agents.json` and NOT from the task: who has work is
        # a fact about a run, and the store is committed and kept forever. Silent
        # where nobody claimed it, so a project with no agents reads as it always
        # did.
        if held.get(tid):
            aside.append(f"held by {held[tid]}")
        if waiting:
            aside.append("waits " + ", ".join(waiting))
        if tg.unblocks(tid):
            aside.append("unblocks " + ", ".join(tg.unblocks(tid)))
        if task.because:
            aside.append(f"because {', '.join(task.because)}")
        if task.evidence_for:
            aside.append(f"evidence for {task.evidence_for}")
        if task.parked:
            # First in the aside, and unconditional: everything else here says
            # what the *graph* makes of this task, and this says somebody put
            # it down. A parked task with nothing else to report must not read
            # as "startable", which is what it did before there was a status
            # for this and people used DROPPED instead.
            reason = task.stopped_because
            aside.insert(0, "parked: " + compact.gist(reason) if reason
                         else "parked")
        elif not aside:
            aside.append("startable")
        # Yellow for the whole aside where a premise is undecided: the reason
        # this task is not startable is in there, and the eye needs sending to
        # the line before it reads which of the four bits says so.
        style = "yellow" if gate else "cyan" if task.parked else "dim"
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
        premise = ", ".join(task.because) or "—"
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
    if t.completions:
        # Every completion, not the live one: they are archived exactly as
        # `stops` are, so a removal takes the whole account of what this work
        # produced — including the results of earlier goes at it, which the
        # status no longer shows and nothing else records.
        lines.append(f"[yellow]loses[/] {len(t.completions)} completion(s), "
                     f"latest: {_x(t.completions[-1].outcome)}")
    if t.stops:
        # The one record here that a removal cannot supersede — `dg task drop`
        # keeps it, `dg task rm` does not, which is the difference between the
        # two and what this line exists to make visible.
        lines.append(f"[yellow]loses[/] {len(t.stops)} stop record(s), "
                     f"latest: {_x(t.stops[-1].why)}")
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
    # `parked` outranks `blocked`: a parked task with an unfinished
    # prerequisite is both, and the one a reader needs is the one somebody
    # chose. Blocked describes the graph; parked describes a judgement.
    state = ("parked" if t.parked
             else "waiting on a decision" if gate and t.unfinished
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
        lines.append(f"because     {', '.join(t.because)}"
                     + ("  [yellow](undecided)[/]" if gate else ""))
    if t.evidence_for:
        lines.append(f"informs     {t.evidence_for}")
    if t.done:
        lines.append(f"done        {t.done}")
    if t.completions:
        # Every completion, whatever the status is now, and drawn like the
        # stops below: the same record, kept for the same reason. The live one
        # is the last, and only while the status still claims one — `done_label`
        # decides that, so this panel cannot disagree with the store.
        lines += ["", "[bold]Outcome[/]"]
        label = done_label(t.status)
        last = len(t.completions) - 1
        for i, c in enumerate(t.completions):
            tag = f"  [dim]({label})[/]" if label and i == last else ""
            lines.append(f"  [dim]{c.date}[/]  {_x(c.outcome)}{tag}")
    if t.stops:
        # Kept whatever the status is now — work picked up again is the
        # ordinary case, and the list of what kept stopping it is the record
        # this store did not have. The live entry is the last one, and only
        # while the status still claims a reason; `stopped_because` decides
        # that, so this cannot disagree with the store's own reading.
        lines += ["", "[bold]Stopped[/]"]
        live = t.stopped_because
        for i, k in enumerate(t.stops):
            now = live is not None and i == len(t.stops) - 1
            tag = ("  [cyan](still parked)[/]" if now and t.parked
                   else "  [dim](abandoned here)[/]" if now else "")
            lines.append(f"  [dim]{k.date}[/]  {_x(k.why)}{tag}")
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

    Anything the walk cannot reach from a root is named rather than dropped.
    A cycle is the only way that happens, `task_acyclic` refuses to apply one,
    and a hand-edited store is where it comes from — but the tasks inside a
    cycle are exactly the ones no root leads to, so drawing what is left and
    saying nothing reports a whole region of the graph as absent.
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
        # A cycle with no root outside it leaves nothing to draw from; one
        # beside other work leaves the rest of the graph drawable and the cycle
        # invisible. The second is the likelier shape and the easier to miss,
        # so the reach is checked after the walk rather than guessed before it.
        if tg.tasks and not roots:
            con.print("[yellow]every task has something before it — the graph "
                      "has a cycle; `dg check` names it[/]")
            roots = sorted(tg.tasks)
        for r in roots:
            add(top, r)
        stranded = sorted(set(tg.tasks) - seen)
        if stranded:
            con.print(f"[yellow]no root reaches {', '.join(stranded)} — they "
                      f"are in a cycle; `dg check` names it[/]")
            # One entry per cycle, not one per task in it: the first draws
            # the ring, and the rest of the ring is `seen` by the time the
            # loop reaches it.
            for r in stranded:
                if r not in seen:
                    add(top, r)
    con.print(top)


@task_app.command("pending", rich_help_panel=T_STAGE)
def task_pending_cmd(
    full: bool = typer.Option(False, "--full",
                              help="The table, with no detail clipped."),
    agent: str = typer.Option(None, "--agent", metavar="NAME",
                              help="Only what one writer staged. `unowned` "
                                   "names the ops nobody signed."),
) -> None:
    """What is staged for the task store but not yet applied.

    One line per op by default, the table under `--full` — `dg pending`'s twin,
    through the same renderer, because the two trays are reviewed the same way.
    `--agent` narrows it, and its roster counts **both** trays: the two are
    staged apart and applied together, so a writer present in one is present in
    the review.
    """
    _tray(pending.load(task_pending.path()), full, details=_TASK_DETAIL,
          subject="task", heading="STAGED TASKS", title="Staged tasks",
          column="Task",
          actions="`dg apply` to write, `dg task drop-op <id>` to unstage",
          expand="dg task pending --full", roster=_trays_roster(), agent=agent,
          narrow="dg task pending --agent <name>")
    _say_arriving()


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
    "set_fields": _fields_detail,
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
def task_clear(
    agent: str = typer.Option(None, "--agent", metavar="NAME",
                              help="Unstage only what one writer staged."),
) -> None:
    """Unstage every task op, or one writer's with `--agent`. See `dg clear`."""
    _clear_tray(pending.clear, pending.clear_agent, agent,
                task_pending.path(), "task op",
                other=project.find().pending, other_cmd="dg clear")


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
    _adopt(tg, proj.tasks, proj.task_view, force,
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
    con.print(f"[green]✓[/] created {proj.store}; run `dg render` for "
              f"{proj.view.name}")
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


def _adopt(graph, store: pathlib.Path, view: pathlib.Path,
           force: bool, counted: str, source: str) -> None:
    """Validate a prepared graph, refuse it if invalid, then make it the store.

    Shared by every bootstrap door — `dg import`, `dg task import`,
    `dg import-md` — because the rule they must agree on is the one that
    matters: **a bootstrap never writes a store `dg apply` would refuse.** That
    would plant, on day one, the contradiction the tool exists to prevent. One
    implementation, so a new door cannot arrive without it.

    Like `init`, the store is written but the generated view is left to
    `dg render` / `dg task render`, built on demand.
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
    render_cmd_name = "task render" if view.name == project.TASK_VIEW_NAME \
        else "render"
    con.print(f"[green]✓[/] imported {counted} → {store}; run `dg "
              f"{render_cmd_name}` for {view.name}")
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


def _at_ref(ref: str, name: str):
    """One store as it was at a git ref, or None where that ref had none.

    `git show` rather than a checkout: integration reads three graphs and two
    of them are not the working tree, so touching the working tree to see them
    would be a side effect nobody asked for.
    """
    import subprocess
    root = project.find().root
    res = subprocess.run(["git", "-C", str(root), "show", f"{ref}:./{name}"],
                         capture_output=True, text=True)
    return res.stdout if res.returncode == 0 else None


def _merge_base(ref: str) -> str:
    import subprocess
    root = project.find().root
    res = subprocess.run(["git", "-C", str(root), "merge-base", "HEAD", ref],
                         capture_output=True, text=True)
    if res.returncode != 0 or not res.stdout.strip():
        con.print(f"[red]no common history with {_x(ref)}[/]\n"
                  f"[dim]a contribution is derived from what its writer "
                  f"started from — without a base, a record missing from the "
                  f"arriving store cannot be told from one it never saw, and "
                  f"guessing is how a deletion gets silently reverted.\n"
                  f"`--base <ref>` names it if this repository cannot.[/]")
        raise typer.Exit(1)
    return res.stdout.strip()


@app.command(rich_help_panel=STORE)
def integrate(
    ref: str = typer.Argument(..., help="the branch, worktree or commit "
                                        "whose contribution is arriving"),
    base: str = typer.Option(None, "--base",
                             help="what they started from "
                                  "(default: the merge base with HEAD)"),
) -> None:
    """Bring somebody else's work in, as ops you can read before they land.

    Not a merge. A git text-merge of `decisions.json` fails loudly but in a
    file with no semantics, and the naive improvement — a union keyed by id —
    fails *silently*, which is worse: a removal loses to any side that still
    names the record, two answers to one question become an arbitrary pick,
    and a park is erased by a completion. Replayed as ops, each of those is a
    refusal or a line in a report, before anything is written.

    The ops are quarantined in `.dgraph-incoming.json`, **not** staged. The
    tray is what every stage-time guard consults, so an unadjudicated op put
    there would have this clone answering `dg node` with a title nobody
    accepted.
    """
    proj = project.find()
    if not project.in_repo(proj.root):
        con.print("[red]integration needs git[/]\n[dim]the base a "
                  "contribution is derived from comes from `git merge-base`[/]")
        raise typer.Exit(1)
    if integrate_mod.waiting(proj.root):
        con.print("[red]a contribution is already waiting[/]\n"
                  "[dim]adjudicate it first — one at a time, because the "
                  "second would be judged against a graph nobody has agreed "
                  "to yet[/]")
        raise typer.Exit(1)

    base_ref = base or _merge_base(ref)
    ours_g = Graph.load(proj.store) if proj.has_decisions else None
    ours_tg = TaskGraph.load(proj.tasks) if proj.has_tasks else None
    theirs_g, base_g = _pair(ref, base_ref, project.STORE_NAME, Graph)
    theirs_tg, base_tg = _pair(ref, base_ref, project.TASKS_NAME, TaskGraph)
    if theirs_g is None and theirs_tg is None:
        con.print(f"[red]{_x(ref)} holds no store this project has[/]")
        raise typer.Exit(1)

    rep = integrate_mod.plan(
        ours_g, ours_tg, base_g, base_tg, theirs_g, theirs_tg,
        # Wired explicitly, and it has to be: put an arriving contribution
        # through `apply_all` without the cross guards and both link
        # invariants are silent — the dangling reference lands, the cycle
        # lands, `dg check` finds them afterwards, and the commit gate then
        # denies every commit in the repository with a hand-edit as the only
        # exit. `guard_pair` rather than the two one-sided guards, because
        # each half has to be judged against what the other half will hold.
        guard=cross.guard_pair(),
        # The allocator for class M's renames, passed in for the reason the
        # guard is: this clone's id range is `ranges`' business and this
        # module must not learn where a free id comes from. A renamed record
        # lands **in the receiving clone's range**, or the rename
        # reintroduces the collision it just resolved.
        next_free=_free_id)
    _integration_report(rep, ref, base_ref)

    if rep.derived == 0:
        con.print("[dim]nothing to integrate — the contribution is already "
                  "in this store[/]")
        return
    integrate_mod.save_incoming(rep, source=ref, base=base_ref[:12],
                                root=proj.root)
    con.print(f"[dim]quarantined in {project.INCOMING_NAME} — nothing is "
              f"staged and nothing is written[/]")


def _pair(ref: str, base_ref: str, name: str, builder):
    """`(theirs, base)` for one store, or `(None, None)` where it has none."""
    theirs_raw = _at_ref(ref, name)
    if theirs_raw is None:
        return None, None
    base_raw = _at_ref(base_ref, name)
    try:
        theirs = builder.from_dict(json.loads(theirs_raw))
        # A store the base did not have is a store this contribution created,
        # so every record in it is an addition — which an empty graph says
        # exactly, with no special case anywhere downstream.
        base = (builder.from_dict(json.loads(base_raw)) if base_raw
                else builder(areas=list(theirs.areas)))
    except (ValueError, KeyError) as exc:
        con.print(f"[red]{name} at {_x(ref)} could not be read[/]\n{_x(exc)}")
        raise typer.Exit(1) from None
    return theirs, base


def _free_id(prefix: str, taken) -> str:
    """An id nothing here holds, inside this clone's grant if it has one.

    `taken` carries the ids earlier renames in the same contribution have just
    claimed, so two arriving records that both collide do not both land on the
    same replacement.
    """
    numbers = [int(i[1:]) for i in taken if i[1:].isdigit()]
    return f"{prefix}{ranges.next_number(prefix, numbers):02d}"


def _integration_report(rep, ref: str, base_ref: str) -> None:
    """One report, once, after everything has been collected.

    Fail-fast would make a twelve-op contribution with three conflicts four
    round-trips, in composition order rather than importance order — and the
    invariant failure, the thing that might make somebody reject the whole
    contribution, arrives only after three unrelated questions have been
    answered. That inverts the point of having a seam.
    """
    con.print(f"[bold]{rep.derived} op(s) from {_x(ref)}[/] "
              f"[dim]against {_x(base_ref[:12])}[/] — "
              f"{rep.clean} clean, {len(rep.contested)} contested, "
              f"{len(rep.blocking)} blocking")
    if rep.renamed:
        con.print("\n[cyan]renamed[/] [dim]— the id was taken here, so the "
                  "arriving record got a free one. Mechanical, and never a "
                  "question: it is done inside the contribution, where an "
                  "edge still knows which vertex it meant.[/]")
        for line in rep.renamed:
            con.print(f"  {line}")
    if rep.contested:
        con.print("\n[yellow]contested[/] [dim]— it applies, but this graph "
                  "says otherwise. Only a person can say which is right.[/]")
        for f in rep.contested:
            con.print(f"  [dim]{f.store[0]}{f.at}[/]  {_x(f.message)}")
            if f.refusal:
                # The refusal under the disagreement that caused it, indented
                # as a consequence rather than listed as a second conflict.
                con.print(f"      [dim]{_x(f.refusal)}[/]")
    if rep.inapplicable:
        con.print("\n[yellow]inapplicable[/] [dim]— cannot apply here at "
                  "all, and what each one took down with it[/]")
        for f in rep.inapplicable:
            more = (f"  [dim](+{len(f.grouped)} op(s) on the same record)[/]"
                    if f.grouped else "")
            con.print(f"  [dim]{f.store[0]}{f.at}[/]  {_x(f.message)}{more}")
    if rep.unexpressible:
        con.print("\n[yellow]not expressible as an op[/] [dim]— reported "
                  "rather than invented; no merge driver here has invariant "
                  "knowledge of its own[/]")
        for line in rep.unexpressible:
            con.print(f"  {_x(line)}")
    if rep.blocking:
        con.print("\n[red]blocking[/] [dim]— the graph these produce is one "
                  "the store may not hold[/]")
        for line in rep.blocking:
            con.print(f"  {_x(line)}")
    if rep.warnings:
        # Advisory *and* arrival-order-sensitive, said as one sentence,
        # because the second half is what stops somebody automating on them:
        # several of these fire in one integration order and not the other,
        # and a signal that depends on who integrated first is not a signal to
        # act on.
        con.print("\n[dim]warnings this contribution introduces — advisory, "
                  "and several of them depend on which side integrated "
                  "first. Nothing should key off them.[/]")
        for line in rep.warnings:
            con.print(f"  [dim]{_x(line)}[/]")
    if rep.touched:
        # What was integrated, not only what was found wrong. A clean
        # `dg check` afterwards is not evidence that the work arrived.
        con.print(f"\n[dim]touched {len(rep.touched)} record(s): "
                  f"{_x(', '.join(rep.touched))}[/]")
    if rep.ok and rep.derived:
        con.print("[green]nothing contested[/] [dim]— every op applies and "
                  "the result is valid[/]")


@app.command(rich_help_panel=STORE)
def incoming(
    take: str = typer.Option(None, "--take", metavar="REF",
                             help="settle one contested op in favour of the "
                                  "arriving answer"),
    keep: str = typer.Option(None, "--keep", metavar="REF",
                             help="...or in favour of this store's"),
    split: str = typer.Option(None, "--split", metavar="REF",
                              help="...or neither: the two answers are to "
                                   "different questions worded as one"),
    as_id: str = typer.Option(None, "--as", metavar="ID",
                              help="the id for the question --split opens"),
    title: str = typer.Option(None, "--title",
                              help="its wording (default: this one's)"),
    area: str = typer.Option(None, "--area",
                             help="its area (default: this one's)"),
    adopt: bool = typer.Option(False, "--adopt",
                               help="move it into the trays, to review and "
                                    "apply like your own work"),
    discard: bool = typer.Option(False, "--discard",
                                 help="refuse the whole contribution"),
) -> None:
    """What another writer's contribution holds, before any of it is yours.

    `dg integrate` puts it here rather than in the tray, so that nothing in
    this clone answers a question with an op nobody has accepted. Adoption is
    the moment it becomes yours: the ops move into the two trays and from
    there they are reviewed and applied exactly like work you composed.

    **Adoption is all or nothing**, because a contribution is. Dropping the
    contested half and keeping the rest reads as though a person had chosen
    the remainder, and an op left behind takes its dependants with it — an
    `add_edge` whose vertex was held back can never apply, and it would sit in
    a tray every writer here shares.
    """
    proj = project.find()
    # One `dg incoming` is one act on the quarantine file: it reads it, may
    # answer part of it, prints what it then holds, and may adopt or discard the
    # whole thing. Held across all of that, so a second writer cannot answer a
    # conflict between this command's read and its write — which used to leave
    # both of them told they had settled it. The routes below take the same lock
    # and it nests, so they cost nothing here. Audit W-F1.
    with integrate_mod.held(proj.root):
        return _incoming(proj, take, keep, split, as_id, title, area,
                         adopt, discard)


def _incoming(proj, take, keep, split, as_id, title, area, adopt, discard):
    """The body of `dg incoming`, with the quarantine file already held."""
    raw = integrate_mod.load_incoming(proj.root)
    if not raw:
        con.print("[dim]nothing arriving[/]")
        return
    d_ops, t_ops = raw.get("decisions", []), raw.get("tasks", [])
    if discard and adopt:
        con.print("[red]--adopt and --discard ask for opposite things[/]")
        raise typer.Exit(2)

    if split:
        if not as_id:
            con.print("[red]--split needs an id for the question it opens: "
                      "--as D51[/]\n[dim]`dg range` says which are yours[/]")
            raise typer.Exit(2)
        g = _g()
        was = next((f for f in raw.get("contested", [])
                    if f.get("ref") == split), None)
        vid = ((was or {}).get("op") or {}).get("vertex")
        here = g.vertices.get(vid) if vid else None
        # The refusals a new vertex would meet at `apply`, met here instead:
        # a split that turns out illegal several commands later is a seam
        # answered against a graph that could not hold the answer.
        bad = ranges.fault("D", as_id)
        if as_id in g.vertices or bad:
            con.print(f"[red]{_x(bad or f'{as_id} already exists')}[/]")
            raise typer.Exit(1)
        try:
            said = integrate_mod.split(
                proj.root, split, as_id,
                title=title or (here.title if here else as_id),
                area=area or (here.area if here else (g.areas or ["General"])[0]))
        except (LookupError, ValueError) as exc:
            con.print(f"[red]{_x(exc)}[/]")
            raise typer.Exit(1) from None
        raw = integrate_mod.load_incoming(proj.root)
        con.print(f"[green]settled[/] {_x(said)}")

    for ref, choice in ((take, "take"), (keep, "keep")):
        if not ref:
            continue
        try:
            # The route, not `answer_one` plus a write: the load has to be
            # inside the lock, and it happens in there. `Answered` is the
            # second writer's refusal and is a `LookupError`, so this `except`
            # already catches it. Audit W-F1.
            said = integrate_mod.answer(proj.root, ref, choice)
        except LookupError as exc:
            con.print(f"[red]{_x(exc)}[/]")
            raise typer.Exit(1) from None
        raw = integrate_mod.load_incoming(proj.root)
        con.print(f"[green]settled[/] {_x(said)}")
    if take or keep or split:
        left = [f for f in raw.get("contested", []) if not f.get("resolution")]
        con.print(f"[dim]{len(left)} contested op(s) still open[/]"
                  if left else
                  "[dim]every conflict answered — `dg incoming --adopt`[/]")
        if not adopt:
            return

    if discard:
        integrate_mod.clear_incoming(proj.root)
        con.print(f"[yellow]refused[/] {len(d_ops) + len(t_ops)} op(s) from "
                  f"{_x(raw.get('source', '?'))}\n"
                  f"[dim]nothing here records that it arrived — the file was "
                  f"gitignored. Say so wherever the contribution came from.[/]")
        return

    con.print(f"[bold]{len(d_ops) + len(t_ops)} op(s) from "
              f"{_x(raw.get('source', '?'))}[/] "
              f"[dim]against {_x(raw.get('base', '?'))}[/]")
    open_now = [f for f in raw.get("contested", []) if not f.get("resolution")]
    for f in raw.get("contested", []):
        mark = {"take": "[green]take[/]", "keep": "[green]keep[/]",
                "split": "[green]split[/]"}.get(f.get("resolution"),
                                                "[yellow]open[/]")
        con.print(f"  {mark}  [dim]{_x(f.get('ref') or '?')}[/]  "
                  f"{_x(f.get('message') or '')}")
        if f.get("refusal") and not f.get("resolution"):
            con.print(f"        [dim]{_x(f['refusal'])}[/]")
    held = []
    for label, key in (("blocking", "blocking"),
                       ("not expressible as an op", "unexpressible")):
        for line in raw.get(key, []):
            held.append((label, line))
    for label, line in held:
        con.print(f"  [yellow]{label}[/]  {_x(line)}")
    for half, table in ((d_ops, _PENDING_DETAIL), (t_ops, _TASK_DETAIL)):
        for op in half:
            con.print(f"  [dim]{_x(op.get('iref') or '·'):>4}[/]  "
                      f"{_x(op.get('op', '?'))}  {_tray_detail(op, table)}")

    if not adopt:
        con.print("\n[dim]`dg incoming --take <ref>` for the arriving "
                  "answer, `--keep <ref>` for this store's, or "
                  "`--split <ref> --as <id>` where the two answers turn out "
                  "to be to different questions; then `--adopt`. "
                  "`--discard` refuses the whole contribution.[/]")
        return
    if open_now or held:
        # Refused rather than forced, and there is no `--force`. These are the
        # questions only a person can answer, and a flag that adopted
        # everything would answer them by not asking.
        con.print("\n[red]not adopted[/] [dim]— every line above needs an "
                  "answer first. `--discard` refuses the whole "
                  "contribution.[/]")
        raise typer.Exit(1)

    def stage(d_ops, t_ops):
        if d_ops:
            _vet_all(_eff(_g()), d_ops)
            pending.stage_all(d_ops)
        if t_ops:
            _tstage_all(t_ops)

    # Staging and clearing as one act, so two adopters cannot each stage the
    # whole contribution and each delete the only record that it arrived.
    # `stage` is passed in because it runs this store's stage-time vetting and
    # `integrate` must not learn what either store means. Audit W-F1.
    landed = integrate_mod.adopt(proj.root, stage)
    if landed is None:
        con.print("[dim]nothing arriving[/]")
        return
    d_ops, t_ops, notes = landed
    con.print(f"[green]adopted[/] {len(d_ops) + len(t_ops)} op(s) "
              f"[dim]— `dg pending` to review, `dg apply` to write[/]")
    for line in notes:
        con.print(f"  [yellow]note[/] {_x(line)}")


@app.command(name="range", rich_help_panel=STORE)
def range_cmd(
    set_: str = typer.Option(None, "--set", metavar="LO-HI",
                             help="grant this clone a range, e.g. 50-99"),
    clear: bool = typer.Option(False, "--clear",
                               help="give the grant up and allocate from the "
                                    "whole sequence again"),
) -> None:
    """This clone's id range, and what it has issued out of it.

    Two clones of one graph both compute the next id as `max(stored) + 1`, so
    on a shared base they do not *sometimes* collide — they collide by
    construction, for every record either of them adds. A grant per clone is
    what makes that rare, which is what keeps an integration report readable
    enough to be read.

    Set it once per contribution, not per command: a worker that never passes a
    flag cannot get it wrong. **Every clone needs one, `main` included** — a
    checkout that has just integrated `D50`–`D57` computes `D58` next, which is
    inside the range the contributor is still holding.

    With no grant, allocation is exactly what it has always been. Nothing here
    fires in a single-writer project, and `--clear` puts one back to that.
    """
    proj = project.find()
    if set_ and clear:
        con.print("[red]--set and --clear ask for opposite things[/]")
        raise typer.Exit(2)

    if clear:
        ranges.save({}, proj.root)
        con.print("[green]grant given up[/] [dim]— ids come from the whole "
                  "sequence again[/]")
        return

    if set_:
        lo, hi = _range_bounds(set_)
        # One grant for both stores, never one per store: a contribution is to
        # the project, not to one of its halves, and a worker with a `D` range
        # and no `T` range collides on every task it adds while its decisions
        # are safe.
        ranges.save({p: ranges.Grant(lo, hi) for p in ranges.PREFIXES},
                    proj.root)
        con.print(f"[green]granted[/] D{lo}-D{hi} and T{lo}-T{hi} "
                  f"[dim]({hi - lo + 1} ids each)[/]")

    try:
        grants = ranges.load(proj.root)
    except ranges.RangeError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    if not grants:
        con.print("[dim]no grant — this clone allocates from the whole "
                  "sequence, which is right for one writer.\n"
                  "`dg range --set 50-99` before contributing alongside "
                  "somebody else.[/]")
        return

    for prefix in ranges.PREFIXES:
        g = grants.get(prefix)
        if g is None:
            continue
        line = f"{prefix}  [bold]{g.lo}-{g.hi}[/]  [dim]{g.size} ids[/]"
        if g.issued is not None:
            line += f"  issued to [bold]{prefix}{g.issued:02d}[/]"
        con.print(line)
        # The next id, from the store this clone actually has — the grant alone
        # cannot say it, because an id inside the range may already be in the
        # store from an integration.
        loader = ((_g, editor.next_id) if prefix == "D"
                  else (_tg, task_editor.next_id))
        if (proj.has_decisions if prefix == "D" else proj.has_tasks):
            try:
                con.print(f"   next  {loader[1](loader[0]())}")
            except ranges.RangeError as exc:
                con.print(f"   [red]{_x(exc)}[/]")


def _range_bounds(text: str) -> tuple[int, int]:
    """`50-99` as a pair, refusing anything that would not be a range.

    Its own function so the refusals sit together and read as one rule: a
    grant a caller got wrong should be reported by the act that contains it,
    not by the first `dg add` that meets it several commands later.
    """
    lo_s, _, hi_s = text.partition("-")
    if not hi_s or not lo_s.strip().isdigit() or not hi_s.strip().isdigit():
        con.print(f"[red]{_x(text)} is not a range — write it as LO-HI, "
                  f"like 50-99[/]")
        raise typer.Exit(2)
    lo, hi = int(lo_s), int(hi_s)
    if lo < 1:
        con.print("[red]a range starts at 1 or above — id 0 is not one this "
                  "tool writes[/]")
        raise typer.Exit(2)
    if hi < lo:
        con.print(f"[red]{lo}-{hi} is empty — the high end comes second[/]")
        raise typer.Exit(2)
    return lo, hi


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
    _adopt(g, proj.store, proj.view, force,
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
    _adopt(g, proj.store, proj.view, force,
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
