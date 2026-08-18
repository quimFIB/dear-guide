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
from dgraph import editor, pending, project, render
from dgraph.model import SIMPLE_STATUSES, Graph

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
    if not proj.exists:
        con.print(f"[red]no decisions.json under {proj.root}[/]\n"
                  f"[dim]run `dg init` there, or pass --project PATH[/]")
        raise typer.Exit(2)
    return Graph.load()


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
        con.print(f"[red]no {project.STORE_NAME} under {proj.root}[/]\n"
                  f"[dim]run `dg init` there, or pass --project PATH[/]")
        raise typer.Exit(2)
    problems = _check.run(proj)
    for p in problems:
        con.print(f"[{'red' if p.blocking else 'yellow'}]"
                  f"{'✗' if p.blocking else '!'}[/] {_x(p)}")
    if any(p.blocking for p in problems):
        raise typer.Exit(1)
    g = Graph.load(proj.store)
    tail = f", {len(problems)} warning(s)" if problems else ""
    con.print(f"[green]✓[/] {len(g.vertices)} vertices, {len(g.edges)} edges, "
              f"all invariants hold{tail}")


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
    if vid not in g.vertices:
        con.print(f"[red]unknown vertex {vid}[/]")
        raise typer.Exit(1)
    v = g.vertices[vid]
    e = g.active_edge(vid)
    if e is not None and e.decided:
        # Caught here rather than at `apply`, which would refuse the same thing
        # (pending._apply_one) after the op was already staged, leaving the
        # staging area holding something that can never be applied. PROVISIONAL
        # reaches this branch too: it keeps the answer it was given.
        fix = ("`dg confirm` it if it still holds, or `dg reopen` it"
               if v.base_status == "PROVISIONAL" else "`dg reopen` it first")
        con.print(f"[red]{vid} already has an answer, and is {v.status} — "
                  f"{fix}[/]")
        raise typer.Exit(1)

    if _wants_editor(edit):
        seed = {k: val for k, val in (
            ("answer", answer), ("source", source), ("falsifier", falsifier),
            ("summary", summary),
            ("to", [x.strip() for x in opens.split(",") if x.strip()] if opens else None),
        ) if val}
        ops = _compose(g, "close", vertex=vid, seed=seed)
        _stage_close(g, ops[0])
        return

    con.print(Panel(f"[bold]{_x(v.title)}[/]\n\n"
                    f"depends on {', '.join(g.depends(vid)) or '—'}",
                    title=vid, border_style=_style(v.status)))
    answer = answer or _ask(
        "Answer (what was decided, and on what evidence)", "--answer/-a")
    source = source or _ask(
        "Source (a report/ path, a script, or 'discussion')", "--source/-s")
    existing = g.children(vid)
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
    _stage_close(g, op)


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
    if vid not in g.vertices:
        con.print(f"[red]unknown vertex {vid}[/]")
        raise typer.Exit(1)
    v = g.vertices[vid]
    if v.base_status != "PROVISIONAL":
        con.print(f"[red]{vid} is {v.status}, not PROVISIONAL — "
                  f"there is nothing to re-affirm[/]")
        raise typer.Exit(1)
    unsettled = g.provisional_because(vid)
    if unsettled:
        con.print(f"[red]{vid} still rests on {', '.join(unsettled)}[/]\n"
                  f"[dim]settle the premise first — until then PROVISIONAL is "
                  f"the accurate status[/]")
        raise typer.Exit(1)

    ops = pending.expand(g, {"op": "set_status", "vertex": vid,
                             "status": "DECIDED"})
    for o in ops:
        pending.stage(o)
    released = [o["vertex"] for o in ops[1:]]
    if released:
        con.print(f"[cyan]{len(released)}[/] vertex(es) were BLOCKED:{vid} and "
                  f"are released to OPEN: {', '.join(released)}")
    con.print(f"[green]staged[/] {len(ops)} op(s) — {vid} back to DECIDED, "
              f"review with `dg pending`, then `dg apply`")


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
    if vid not in g.vertices:
        con.print(f"[red]unknown vertex {vid}[/]")
        raise typer.Exit(1)
    e = g.active_edge(vid)
    if e is None or not e.decided:
        con.print(f"[red]{vid} has no decision to reopen[/]")
        raise typer.Exit(1)

    if _wants_editor(edit):
        seed = {k: v for k, v in (("why", why), ("summary", summary)) if v}
        op = _compose(g, "reopen", vertex=vid, seed=seed)[0]
    else:
        why = why or _ask("Why is this being reopened?", "--why/-w")
        op = {"op": "reopen", "vertex": vid, "why": why}
        if summary:
            op["summary"] = summary
    ops = pending.expand(g, op)

    affected = [o["vertex"] for o in ops if o["op"] == "set_status"]
    con.print(Panel(
        f"[bold]{_x(g.vertices[vid].title)}[/]\n\n"
        f"Its answer becomes superseded; its dependencies stay.\n\n"
        f"[cyan]{len(affected)}[/] decided descendant(s) rest on it and become "
        f"PROVISIONAL:\n  {', '.join(affected) or 'none'}",
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

    if _wants_editor(edit):
        seed = {k: v for k, v in (
            ("id", vid), ("title", title), ("area", area), ("status", status),
            ("note", note),
            ("after", [x.strip() for x in after.split(",") if x.strip()] if after else None),
        ) if v}
        ops = _compose(g, "add_vertex", seed=seed)
        for o in ops:
            pending.stage(o)
        con.print(f"[green]staged[/] {len(ops)} op(s) — add {ops[0]['id']}")
        return

    # Required only without --edit; typer cannot express that, so it is checked
    # here. Exit 2 keeps the shape of typer's own missing-option failure.
    missing = [flag for flag, val in (("--id", vid), ("--title", title),
                                      ("--area", area)) if not val]
    if missing:
        con.print(f"[red]missing option(s): {', '.join(missing)}[/]\n"
                  f"[dim]give them as flags, or use `dg add --edit`[/]")
        raise typer.Exit(2)
    if area not in g.areas:
        con.print(f"[red]unknown area. one of: "
                  f"{', '.join(_x(a) for a in g.areas)}[/]")
        raise typer.Exit(1)
    # `decide` validates its targets before staging; this does too, so that a
    # typo is reported by the command that contains it rather than by `apply`
    # two commands later.
    parents = [x.strip() for x in after.split(",") if x.strip()] if after else []
    unknown = [x for x in parents if x not in g.vertices]
    if unknown:
        con.print(f"[red]unknown parent(s): {', '.join(unknown)}[/]")
        raise typer.Exit(1)
    if not _status_legal(g, status):
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
    new = _compose(g, kind, vertex=op.get("vertex"), index=i, op=op)
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
    """Validate the staged ops and write both files."""
    ops = pending.load()
    if not ops:
        con.print("[dim]nothing staged[/]")
        return
    g = _g()
    try:
        out = pending.apply_all(g, ops)
    except pending.ApplyError as exc:
        con.print(f"[red]✗ aborted, nothing written[/]\n{_x(exc)}")
        raise typer.Exit(1)
    if dry_run:
        con.print(f"[green]✓[/] {len(ops)} op(s) would apply cleanly")
        return
    out.save()
    render.write(out)
    pending.clear()
    con.print(f"[green]✓[/] applied {len(ops)} op(s) → decisions.json + "
              f"decision-graph.md")


@app.command(name="render")
def render_cmd() -> None:
    """Regenerate decision-graph.md from the store."""
    render.write(_g())
    con.print(f"[green]✓[/] wrote {project.find().view}")


@app.command()
def init(
    areas: str = typer.Option(
        "General", "--areas",
        help="comma-separated area names, used to group the rendered view",
    ),
) -> None:
    """Start an empty decision graph in this directory."""
    proj = project.find()
    if proj.exists:
        con.print(f"[red]{proj.store} already exists[/]")
        raise typer.Exit(1)
    g = Graph(areas=[a.strip() for a in areas.split(",") if a.strip()])
    g.save(proj.store)
    render.write(g, proj.view)
    con.print(f"[green]✓[/] created {proj.store} and {proj.view.name}")


@app.command(name="import-md")
def import_md(
    path: str = typer.Argument(..., help="a decision-graph.md in the legacy format"),
) -> None:
    """Bootstrap a store from a hand-written markdown decision document."""
    from dgraph.md_import import import_markdown
    proj = project.find()
    if proj.exists:
        con.print(f"[red]{proj.store} already exists — refusing to overwrite[/]")
        raise typer.Exit(1)
    g = import_markdown(pathlib.Path(path))
    problems = g.validate()
    for v in problems:
        con.print(f"[yellow]![/] {_x(v)}")
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
