"""`dg` — read and edit the decision graph."""

from __future__ import annotations

import pathlib
import sys
from datetime import date as _date

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from dgraph import pending, project, render
from dgraph.model import Graph, Violation

app = typer.Typer(
    add_completion=False,
    help="Read and edit a decision graph. decisions.json is the source of "
         "truth; decision-graph.md is generated from it.",
)
con = Console()


@app.callback()
def _root(
    project_dir: str = typer.Option(
        None, "--project", "-C", metavar="PATH",
        help="Project directory. Defaults to $DG_PROJECT, else the nearest "
             "ancestor holding decisions.json, else the cwd.",
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


def _tag(v) -> str:
    return f"[{_style(v.status)}]{v.status}[/]"


# ---- reading -------------------------------------------------------------


@app.command()
def show() -> None:
    """The frontier: everything still open or blocked."""
    g = _g()
    t = Table(title="Frontier", header_style="bold")
    for c in ("ID", "Decision", "Status", "Waiting on", "Area"):
        t.add_column(c)
    for vid in g.frontier():
        v = g.vertices[vid]
        blockers = [p for p in g.depends(vid) if not g.vertices[p].settled]
        t.add_row(vid, v.title, _tag(v), ", ".join(blockers) or "—", v.area)
    con.print(t)
    counts: dict[str, int] = {}
    for v in g.vertices.values():
        counts[v.base_status] = counts.get(v.base_status, 0) + 1
    con.print("  ".join(
        f"[{_style(k)}]{k} {n}[/]" for k, n in sorted(counts.items())
    ))


@app.command()
def tree(root: str = typer.Argument(None, help="Vertex to root at")) -> None:
    """The DAG as a tree, from the roots or from one vertex."""
    g = _g()
    seen: set[str] = set()

    def add(parent: Tree, vid: str) -> None:
        v = g.vertices[vid]
        label = f"[bold]{vid}[/] {v.title} [{_style(v.status)}]{v.status}[/]"
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
        f"[bold]{v.title}[/]", "",
        f"status      {_tag(v)}",
        f"area        {v.area}",
        f"depends on  {', '.join(g.depends(vid)) or '—'}",
    ]
    if e and e.decided:
        lines += [
            f"opens       {', '.join(e.to) or 'TERMINAL'}",
            f"falsifier   {e.falsifier or '—'}",
            f"source      {e.source}   ({e.date})",
            "", "[bold]Answer[/]", e.answer or "",
        ]
    else:
        lines += [f"opens       {', '.join(g.children(vid)) or '—'}"]
        if v.note:
            lines += ["", "[bold]Note[/]", v.note]
    hist = g.history(vid)
    if hist:
        lines += ["", "[bold]Superseded[/]"]
        lines += [
            f"  “{h.summary}” → {h.replaced_by or '(undecided)'}\n    {h.why}"
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
        con.print(f"[bold]{vid}[/] {v.title} [{_style(v.status)}]{v.status}[/]")
        if i < len(p) - 1:
            e = g.active_edge(vid)
            first = (e.answer or "").strip().split("\n")[0] if e else ""
            con.print(f"  [dim]│ {first}[/]")
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
        t.add_row(a, *[str(sum(1 for v in vs if v.base_status == s))
                       for s in statuses], str(len(vs)))
    con.print(t)


@app.command()
def check() -> None:
    """Run every invariant, plus the markdown staleness check."""
    g = _g()
    problems = g.validate()
    view = project.find().view
    if not view.exists() or view.read_text(encoding="utf-8") != render.render(g):
        problems.append(Violation(
            "stale_view",
            "decision-graph.md does not match decisions.json — run `dg render`",
        ))
    for p in problems:
        con.print(f"[{'red' if p.blocking else 'yellow'}]"
                  f"{'✗' if p.blocking else '!'}[/] {p}")
    if any(p.blocking for p in problems):
        raise typer.Exit(1)
    if problems:
        con.print(f"[yellow]{len(problems)} warning(s)[/]")
        return
    con.print(f"[green]✓[/] {len(g.vertices)} vertices, {len(g.edges)} edges, "
              f"all invariants hold")


# ---- editing -------------------------------------------------------------


@app.command()
def decide(
    vid: str,
    answer: str = typer.Option(None, "--answer", "-a"),
    source: str = typer.Option(None, "--source", "-s"),
    falsifier: str = typer.Option(None, "--falsifier", "-f"),
    opens: str = typer.Option(None, "--opens", "-o", help="comma-separated ids"),
    summary: str = typer.Option(None, "--summary"),
) -> None:
    """Stage a decision. Prompts for anything not given as a flag."""
    g = _g()
    if vid not in g.vertices:
        con.print(f"[red]unknown vertex {vid}[/]")
        raise typer.Exit(1)
    v = g.vertices[vid]
    if v.base_status == "DECIDED":
        con.print(f"[red]{vid} is already DECIDED — `dg reopen` it first[/]")
        raise typer.Exit(1)

    con.print(Panel(f"[bold]{v.title}[/]\n\ndepends on {', '.join(g.depends(vid)) or '—'}",
                    title=vid, border_style=_style(v.status)))
    answer = answer or typer.prompt("Answer (what was decided, and on what evidence)")
    source = source or typer.prompt("Source (a report/ path, a script, or 'discussion')")
    existing = g.children(vid)
    opens_l = (
        [x.strip() for x in opens.split(",") if x.strip()] if opens
        else [x.strip() for x in typer.prompt(
            f"Opens which decisions? comma-separated, blank for terminal "
            f"(already linked: {', '.join(existing) or 'none'})",
            default="", show_default=False).split(",") if x.strip()]
    )
    if opens_l or existing:
        falsifier = falsifier or typer.prompt(
            "Falsifier (what evidence would reopen this? 'ANALYTIC — …' if none)"
        )
    unknown = [x for x in opens_l if x not in g.vertices]
    if unknown:
        con.print(f"[red]unknown target(s): {', '.join(unknown)}[/]")
        raise typer.Exit(1)

    op = {
        "op": "close", "vertex": vid, "answer": answer, "source": source,
        "falsifier": falsifier, "to": opens_l,
        "date": _date.today().isoformat(),
    }
    if summary:
        op["summary"] = summary
    pending.stage(op)
    con.print(f"[green]staged[/] close {vid} — review with `dg pending`, "
              f"then `dg apply`")


@app.command()
def reopen(
    vid: str,
    why: str = typer.Option(None, "--why", "-w", help="what challenges it"),
    summary: str = typer.Option(None, "--summary"),
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

    why = why or typer.prompt("Why is this being reopened?")
    op = {"op": "reopen", "vertex": vid, "why": why}
    if summary:
        op["summary"] = summary
    ops = pending.expand(g, op)

    affected = [o["vertex"] for o in ops if o["op"] == "set_status"]
    con.print(Panel(
        f"[bold]{g.vertices[vid].title}[/]\n\n"
        f"Its answer becomes superseded; its dependencies stay.\n\n"
        f"[cyan]{len(affected)}[/] decided descendant(s) rest on it and become "
        f"PROVISIONAL:\n  {', '.join(affected) or 'none'}",
        title=f"reopen {vid}", border_style="magenta",
    ))
    if not typer.confirm("Stage this?", default=True):
        raise typer.Exit()
    for o in ops:
        pending.stage(o)
    con.print(f"[green]staged[/] {len(ops)} op(s)")


@app.command()
def add(
    vid: str = typer.Option(..., "--id"),
    title: str = typer.Option(..., "--title", "-t"),
    area: str = typer.Option(..., "--area"),
    after: str = typer.Option(None, "--after", help="parent that opens this"),
    status: str = typer.Option("OPEN", "--status"),
) -> None:
    """Stage a new decision vertex."""
    g = _g()
    if area not in g.areas:
        con.print(f"[red]unknown area. one of: {', '.join(g.areas)}[/]")
        raise typer.Exit(1)
    pending.stage({"op": "add_vertex", "id": vid, "title": title,
                   "area": area, "status": status})
    if after:
        pending.stage({"op": "add_edge", "from": after, "to": [vid]})
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
            "close": lambda o: (o.get("answer") or "")[:70],
            "reopen": lambda o: o.get("why", ""),
            "set_status": lambda o: f"→ {o['status']}"
            + (f"  [dim](from {o['derived_from']})[/]" if o.get("derived_from") else ""),
            "add_vertex": lambda o: o.get("title", ""),
            "add_edge": lambda o: "→ " + ", ".join(o.get("to", [])),
        }[o["op"]](o)
        t.add_row(str(i), o["op"], o.get("vertex") or o.get("from") or o.get("id"), detail)
    con.print(t)
    con.print("[dim]`dg apply` to write, `dg drop N` to unstage[/]")


@app.command()
def drop(i: int) -> None:
    """Unstage one op."""
    pending.drop(i)
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
        con.print(f"[red]✗ aborted, nothing written[/]\n{exc}")
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
        con.print(f"[yellow]![/] {v}")
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
