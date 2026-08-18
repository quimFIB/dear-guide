"""`dg` — read and edit the decision graph."""

from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import date as _date

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from dgraph import brief as _brief
from dgraph import check as _check
from dgraph import cross, editor, pending, project, render, task_pending, task_render
from dgraph.model import SIMPLE_STATUSES, Graph
from dgraph.tasks import ID_RE as TASK_ID_RE
from dgraph.tasks import TaskGraph

app = typer.Typer(
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
    from importlib.metadata import PackageNotFoundError, version
    try:
        con.print(version("decision-graph-assistant"))
    except PackageNotFoundError:  # running from a checkout, not installed
        con.print("unknown")
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
                  f"[dim]`dg pending` to review; `dg drop N` or `dg edit N` "
                  f"to fix[/]")
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
                  f"[dim]staging what is missing, or `dg drop N`, clears it[/]")


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


def _status_legal(g: Graph, status: str) -> bool:
    """The legality rule from `Graph.validate`, applied before staging."""
    base, _, blocker = status.partition(":")
    if base == "BLOCKED":
        return bool(blocker) and blocker in g.vertices
    return base in SIMPLE_STATUSES and not blocker


def _tag(v) -> str:
    return f"[{_style(v.status)}]{v.status}[/]"


# ---- reading -------------------------------------------------------------


@app.command()
def show() -> None:
    """The frontier: everything still open or blocked, and anything provisional."""
    g = _g()
    t = Table(title="Frontier", header_style="bold")
    for c in ("ID", "Decision", "Status", "Waiting on", "Unblocks", "Area"):
        t.add_column(c)
    for r in _brief.rows(g):
        t.add_row(r.id, _x(r.title), _tag(g.vertices[r.id]),
                  ", ".join(r.waiting_on) or "—",
                  ", ".join(r.unblocks) or "—", _x(r.area))
    con.print(t)

    # A PROVISIONAL vertex is settled, so it is not in the frontier — but its
    # answer rests on a premise under review, which is the thing most worth
    # knowing before building on it. It was missing from this view entirely.
    att = _brief.attention(g)
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


@app.command()
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


@app.command()
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


@app.command()
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


@app.command()
def areas() -> None:
    """Counts by area and status."""
    g = _g()
    statuses = sorted({v.base_status for v in g.vertices.values()})
    t = Table(header_style="bold")
    t.add_column("Area")
    for s in statuses:
        t.add_column(s, justify="right", style=_style(s))
    t.add_column("Total", justify="right")
    for a in g.areas:
        vs = [v for v in g.vertices.values() if v.area == a]
        t.add_row(_x(a), *[str(sum(1 for v in vs if v.base_status == s))
                           for s in statuses], str(len(vs)))
    con.print(t)


@app.command()
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


@app.command()
def gate(
    command: str = typer.Option(..., "--command", metavar="CMD",
                                help="The shell command a host is about to run."),
    as_json: bool = typer.Option(False, "--json", help="Machine form."),
) -> None:
    """Judge a shell command before a host runs it: allow, ask, or deny.

    The commit gate, host-neutral. Both agent adapters call this and translate
    the answer, so the rule lives here rather than twice in two languages. Always
    exits 0 — the verdict is the output, and an adapter must be able to tell a
    refusal from a crash.
    """
    from dgraph import gate as _gate
    v = _gate.verdict(command)
    if as_json:
        print(json.dumps(v, ensure_ascii=False))
        return
    print(v["verdict"] if not v["reason"] else f"{v['verdict']}: {v['reason']}")


@app.command()
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
    for o in ops:
        pending.stage(o)
    released = [o["vertex"] for o in ops if o["op"] == "set_status"]
    if released:
        con.print(f"[cyan]{len(released)}[/] vertex(es) were "
                  f"BLOCKED:{op['vertex']} and are released to OPEN: "
                  f"{', '.join(released)}")
    con.print(f"[green]staged[/] {len(ops)} op(s) — review with `dg pending`, "
              f"then `dg apply`")
    _warn_stuck()


@app.command()
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
                      f"or `dg drop N` to unstage it[/]")
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


@app.command()
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
    for o in ops:
        pending.stage(o)
    released = [o["vertex"] for o in ops[1:]]
    if released:
        con.print(f"[cyan]{len(released)}[/] vertex(es) were BLOCKED:{vid} and "
                  f"are released to OPEN: {', '.join(released)}")
    con.print(f"[green]staged[/] {len(ops)} op(s) — {vid} back to DECIDED, "
              f"review with `dg pending`, then `dg apply`")
    _warn_stuck()


@app.command()
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
    for o in ops:
        pending.stage(o)
    con.print(f"[green]staged[/] {len(ops)} op(s)")
    _warn_stuck()


@app.command()
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
        for o in ops:
            pending.stage(o)
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
    if not _status_legal(eff, status):
        con.print(f"[red]illegal status {_x(status)}[/]\n"
                  f"[dim]one of {', '.join(sorted(SIMPLE_STATUSES))}, "
                  f"or BLOCKED:<existing id>[/]")
        raise typer.Exit(1)
    op = {"op": "add_vertex", "id": vid, "title": title,
          "area": area, "status": status}
    if note:
        op["note"] = note
    pending.stage(op)
    for parent in parents:
        pending.stage({"op": "add_edge", "from": parent, "to": [vid]})
    con.print(f"[green]staged[/] add {vid}")
    _warn_stuck()


@app.command(name="pending")
def pending_cmd() -> None:
    """What is staged but not yet applied."""
    ops = pending.load()
    if not ops:
        con.print("[dim]nothing staged[/]")
        return
    t = Table(header_style="bold", title="Staged")
    for c in ("#", "Op", "Vertex", "Detail"):
        t.add_column(c)
    for i, o in enumerate(ops):
        detail = {
            "close": lambda o: _x((o.get("answer") or "")[:70]),
            "reopen": lambda o: _x(o.get("why", "")),
            "set_status": lambda o: f"→ {o['status']}"
            + (f"  [dim](from {o['derived_from']})[/]" if o.get("derived_from") else ""),
            "add_vertex": lambda o: _x(o.get("title", "")),
            "add_edge": lambda o: "→ " + ", ".join(o.get("to", [])),
        }[o["op"]](o)
        t.add_row(str(i), o["op"], o.get("vertex") or o.get("from") or o.get("id"), detail)
    con.print(t)
    con.print("[dim]`dg apply` to write, `dg drop N` to unstage[/]")


@app.command(name="edit")
def edit_cmd(i: int) -> None:
    """Revise a staged op in the editor, in place.

    Replaces rather than re-stages: re-staging would move the op to the end of
    the batch, and any derived `set_status` would then apply before the change it
    was derived from.
    """
    ops = pending.load()
    if not 0 <= i < len(ops):
        con.print(f"[red]no staged op {i}[/]")
        raise typer.Exit(1)
    g = _g()
    op = ops[i]
    kind = op.get("op")
    if kind not in editor.RENDERERS:
        con.print(f"[red]op {i} is {kind} — derived, not composed[/]\n"
                  f"[dim]`dg drop {i}` to remove it[/]")
        raise typer.Exit(1)
    # Deliberately not re-expanded: the derived ops are already staged, and
    # propagation depends only on which vertex is settled — never on the answer
    # text or the target list — so revising either cannot invalidate them.
    # Rendered against the batch *without* op i: the other staged ops are
    # context this revision should see, but the op being replaced is not.
    new = _compose(_eff(g, skip=i), kind, vertex=op.get("vertex"), index=i, op=op)
    pending.replace(i, new[0])
    con.print(f"[green]updated[/] op {i} — review with `dg pending`")


@app.command()
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


@app.command()
def drop(i: int) -> None:
    """Unstage one op."""
    try:
        pending.drop(i)
    except IndexError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    con.print(f"[green]dropped[/] op {i}")


@app.command()
def clear() -> None:
    """Unstage everything."""
    pending.clear()
    con.print("[green]cleared[/]")


@app.command()
def apply(dry_run: bool = typer.Option(False, "--dry-run", "-n")) -> None:
    """Validate everything staged and write it.

    One verb for both stores, but two independent batches: each is validated
    and written on its own, so a task batch that will not apply can never stop
    a decision batch that would.
    """
    ops = pending.load()
    task_ops = pending.load(task_pending.path())
    if not ops and not task_ops:
        con.print("[dim]nothing staged[/]")
        return
    if ops:
        _apply_decisions(ops, dry_run)
    if task_ops:
        _apply_tasks(task_ops, dry_run)


def _apply_decisions(ops: list[dict], dry_run: bool) -> None:
    g = _g()
    try:
        # `cross.guard_decisions()` judges the result against the work resting
        # on it: a decision batch can close a cycle through the task store
        # (`D02 opens D01`, with a task between them), and neither store's own
        # validator can see it.
        out = pending.apply_all(g, ops, cross.guard_decisions())
    except pending.ApplyError as exc:
        con.print(f"[red]✗ aborted, nothing written[/]\n{_x(exc)}\n"
                  f"[dim]`dg pending` to review; `dg drop N` to unstage[/]")
        raise typer.Exit(1)
    if dry_run:
        con.print(f"[green]✓[/] {len(ops)} decision op(s) would apply cleanly")
        return
    # Render first: it is pure, so a rendering bug aborts before anything is
    # written. Then the store, then clear pending — the ops are in the store
    # now, and keeping them staged would re-apply them — and the view last,
    # where a failure is the one recoverable case.
    view_text = render.render(out)
    out.save()
    pending.clear()
    proj = project.find()
    try:
        proj.view.write_text(view_text, encoding="utf-8")
    except OSError as exc:
        con.print(f"[yellow]applied {len(ops)} op(s) → decisions.json, but "
                  f"{proj.view.name} could not be written[/]\n{_x(exc)}\n"
                  f"[dim]`dg render` regenerates it once the cause is fixed[/]")
        raise typer.Exit(1)
    con.print(f"[green]✓[/] applied {len(ops)} op(s) → decisions.json + "
              f"decision-graph.md")


def _apply_tasks(ops: list[dict], dry_run: bool) -> None:
    tg = _tg()
    try:
        out = task_pending.apply_all(tg, ops, cross.guard_tasks())
    except pending.ApplyError as exc:
        con.print(f"[red]✗ task ops aborted, nothing written[/]\n{_x(exc)}\n"
                  f"[dim]`dg task pending` to review; `dg task drop-op N` to "
                  f"unstage[/]")
        raise typer.Exit(1)
    if dry_run:
        con.print(f"[green]✓[/] {len(ops)} task op(s) would apply cleanly")
        return
    proj = project.find()
    view_text = task_render.render(out)
    out.save(proj.tasks)
    pending.clear(task_pending.path())
    try:
        proj.task_view.write_text(view_text, encoding="utf-8")
    except OSError as exc:
        con.print(f"[yellow]applied {len(ops)} op(s) → {proj.tasks.name}, but "
                  f"{proj.task_view.name} could not be written[/]\n{_x(exc)}\n"
                  f"[dim]`dg task render` regenerates it[/]")
        raise typer.Exit(1)
    con.print(f"[green]✓[/] applied {len(ops)} op(s) → {proj.tasks.name} + "
              f"{proj.task_view.name}")


@app.command(name="render")
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
    add_completion=False,
    help="Track the work a project has to do. tasks.json is the source of "
         "truth; tasks.md is generated from it.",
)
app.add_typer(task_app, name="task")

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
        con.print(f"[red]the staged task ops no longer apply cleanly[/]\n{_x(exc)}\n"
                  f"[dim]`dg task pending` to review; `dg task drop N` to fix[/]")
        raise typer.Exit(1) from None


def _tstage(op: dict) -> None:
    """Vet an op against the effective task graph, then stage it."""
    tg = _teff(_tg())
    try:
        task_pending.vet(tg, op)
    except pending.ApplyError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    pending.stage(op, task_pending.path())


@task_app.command("init")
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


@task_app.command("add")
def task_add(
    tid: str = typer.Option(None, "--id"),
    title: str = typer.Option(None, "--title", "-t"),
    area: str = typer.Option(None, "--area"),
    after: str = typer.Option(None, "--after",
                              help="comma-separated tasks that must come first"),
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
    parents = [x.strip() for x in after.split(",") if x.strip()] if after else []
    unknown = [p for p in parents if p not in tg.tasks]
    if unknown:
        con.print(f"[red]unknown prerequisite(s): {', '.join(unknown)}[/]")
        raise typer.Exit(1)

    op = {"op": "add_task", "id": tid, "title": title, "area": area}
    if note:
        op["note"] = note
    if because:
        op["because"] = _resolve_premise(because)
    if evidence_for:
        op["evidence_for"] = _resolve_premise(evidence_for)
    _tstage(op)
    for p in parents:
        _tstage({"op": "add_dep", "from": p, "to": [tid]})
    con.print(f"[green]staged[/] add {tid}")


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


@task_app.command("link")
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


@task_app.command("start")
def task_start(tid: str) -> None:
    """Stage a task moving to DOING."""
    _require_task(tid)
    _tstage({"op": "set_status", "task": tid, "status": "DOING"})
    con.print(f"[green]staged[/] {tid} → DOING")


@task_app.command("done")
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


@task_app.command("drop")
def task_drop(
    tid: str,
    why: str = typer.Option(None, "--why", "-w", help="why it is not being done"),
) -> None:
    """Stage a task as abandoned. Releases anything waiting on it."""
    tg = _teff(_tg())
    _require_task(tid, tg)
    released = [t for t in tg.unblocks(tid) if tg.waiting_on(t) == [tid]]
    op = {"op": "set_status", "task": tid, "status": "DROPPED"}
    if why:
        op["note"] = why
    _tstage(op)
    if released:
        con.print(f"[cyan]{len(released)}[/] task(s) waited only on {tid} and "
                  f"are released: {', '.join(released)}")
    con.print(f"[green]staged[/] {tid} → DROPPED")


def _require_task(tid: str, tg: TaskGraph | None = None) -> None:
    tg = tg if tg is not None else _teff(_tg())
    if tid not in tg.tasks:
        con.print(f"[red]unknown task {tid}[/]")
        raise typer.Exit(1)


@task_app.callback(invoke_without_command=True)
def task_show(ctx: typer.Context) -> None:
    """What is outstanding, and what can be picked up now."""
    if ctx.invoked_subcommand is not None:
        return
    tg = _tg()
    g = _decisions_or_none()
    t = Table(title="Tasks", header_style="bold")
    for c in ("ID", "Task", "Status", "Waiting on", "Because", "Area"):
        t.add_column(c)
    for tid in tg.frontier():
        task = tg.tasks[tid]
        # Two waiting-on lists, kept apart in the data and joined only here:
        # a mixed list of T- and D-ids would eventually be fed to a task lookup
        # and crash, which is the class of bug audit item A1 fixed.
        waiting = list(tg.waiting_on(tid))
        gate = _gated_by(tg, g, tid)
        if gate:
            waiting.append(f"{gate} (undecided)")
        premise = task.because or "—"
        if gate:
            premise = f"[yellow]{premise}[/]"
        t.add_row(tid, _x(task.title),
                  f"[{TASK_STYLE.get(task.status, 'white')}]{task.status}[/]",
                  ", ".join(waiting) or "—", premise, _x(task.area))
    con.print(t)
    ready = [tid for tid in sorted(tg.tasks)
             if tg.ready(tid) and not _gated_by(tg, g, tid)]
    con.print(f"[green]ready[/] {', '.join(ready) or '—'}")
    con.print("  ".join(
        f"[{TASK_STYLE.get(k, 'white')}]{k} {n}[/]"
        for k, n in sorted(tg.counts().items())
    ))


@task_app.command("node")
def task_node(tid: str) -> None:
    """Everything known about one task."""
    tg = _tg()
    if tid not in tg.tasks:
        con.print(f"[red]unknown task {tid}[/]")
        raise typer.Exit(1)
    t = tg.tasks[tid]
    state = ("ready" if tg.ready(tid)
             else "blocked" if tg.blocked(tid) else t.status.lower())
    lines = [
        f"[bold]{_x(t.title)}[/]", "",
        f"status      [{TASK_STYLE.get(t.status, 'white')}]{t.status}[/]  "
        f"[dim]({state})[/]",
        f"area        {_x(t.area)}",
        f"waiting on  {', '.join(tg.waiting_on(tid)) or '—'}",
        f"unblocks    {', '.join(tg.unblocks(tid)) or '—'}",
    ]
    if t.done:
        lines.append(f"done        {t.done}")
    if t.outcome:
        lines += ["", "[bold]Outcome[/]", _x(t.outcome)]
    if t.note:
        lines += ["", "[bold]Note[/]", _x(t.note)]
    con.print(Panel("\n".join(lines), title=tid,
                    border_style=TASK_STYLE.get(t.status, "white")))


@task_app.command("pending")
def task_pending_cmd() -> None:
    """What is staged for the task store but not yet applied."""
    ops = pending.load(task_pending.path())
    if not ops:
        con.print("[dim]nothing staged[/]")
        return
    t = Table(header_style="bold", title="Staged tasks")
    for c in ("#", "Op", "Task", "Detail"):
        t.add_column(c)
    for i, o in enumerate(ops):
        detail = {
            "add_task": lambda o: _x(o.get("title", "")),
            "add_dep": lambda o: "→ " + ", ".join(o.get("to", [])),
            "set_status": lambda o: f"→ {o['status']}",
        }.get(o["op"], lambda o: _x(json.dumps(o)))(o)
        t.add_row(str(i), o["op"], o.get("task") or o.get("from") or o.get("id"),
                  detail)
    con.print(t)
    con.print("[dim]`dg apply` to write, `dg task drop-op N` to unstage[/]")


@task_app.command("drop-op")
def task_drop_op(i: int) -> None:
    """Unstage one staged task op."""
    try:
        pending.drop(i, task_pending.path())
    except IndexError as exc:
        con.print(f"[red]{_x(exc)}[/]")
        raise typer.Exit(1) from None
    con.print(f"[green]dropped[/] task op {i}")


@task_app.command("clear")
def task_clear() -> None:
    """Unstage every task op."""
    pending.clear(task_pending.path())
    con.print("[green]cleared[/]")


@task_app.command("render")
def task_render_cmd() -> None:
    """Regenerate tasks.md from the store."""
    task_render.write(_tg())
    con.print(f"[green]✓[/] wrote {project.find().task_view}")


@app.command()
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


@app.command(name="import-md")
def import_md(
    path: str = typer.Argument(..., help="a decision-graph.md in the legacy format"),
    force: bool = typer.Option(False, "--force",
                               help="Write the store even if the imported graph "
                                    "breaks invariants."),
) -> None:
    """Bootstrap a store from a hand-written markdown decision document."""
    from dgraph.md_import import import_markdown
    proj = project.find()
    if proj.has_decisions:
        con.print(f"[red]{proj.store} already exists — refusing to overwrite[/]")
        raise typer.Exit(1)
    try:
        g = import_markdown(pathlib.Path(path))
    except (OSError, ValueError) as exc:
        con.print(f"[red]✗ not imported[/]\n{_x(exc)}")
        raise typer.Exit(1) from None
    problems = g.validate()
    for v in problems:
        con.print(f"[{'red' if v.blocking else 'yellow'}]"
                  f"{'✗' if v.blocking else '!'}[/] {_x(v)}")
    blocking = [v for v in problems if v.blocking]
    if blocking and not force:
        # A bootstrap that writes a store `dg apply` would refuse plants the
        # contradiction this tool exists to prevent, on day one.
        con.print(f"[red]✗ not imported: the result breaks {len(blocking)} "
                  f"invariant(s)[/]\n"
                  f"[dim]fix the source document, or `--force` to write it "
                  f"anyway and repair with `dg` afterwards[/]")
        raise typer.Exit(1)
    g.save(proj.store)
    render.write(g, proj.view)
    con.print(f"[green]✓[/] imported {len(g.vertices)} vertices, "
              f"{len(g.edges)} edges → {proj.store}")


@app.command()
def serve(port: int = typer.Option(8765, "--port", "-p")) -> None:
    """Launch the local web app."""
    from dgraph.server import run
    run(port)


def main() -> None:
    if len(sys.argv) == 1:
        sys.argv.append("show")
    app()


if __name__ == "__main__":
    main()
