"""Collecting a fan-out's answers, and choosing how to ask for them.

Three collectors, one result. Each fills a `fanout.Plan` and does nothing else
— what a plan turns into is `fanout.py`, tested once, reached identically by
all of them:

    wizard_tui.run     a full-screen form. Needs `textual`, an extra.
    ask                a question at a time. Needs nothing the tool does not
                       already have, so interactive setup always works.
    CLI flags          `cli._setup_from_flags`, and the only one an agent
                       inside Claude Code or opencode can drive.

`collect` picks between the first two. **The order is: no terminal at all
first.** A command that starts prompting with nothing on the other end gets
EOF, and click turns that into a bare `Aborted.` — true, and useless to an
agent, which needed to be told that flags exist. So an absent terminal is
answered rather than attempted, which is the same rule `cli._ask` follows.

The plain collector is not a degraded mode. It asks the same eleven questions
in the same order and produces the same bytes, which a test asserts across all
three paths; what the TUI adds is seeing them together, because `never` with a
45-minute budget is a different run from `evidence` with fifteen and a
question-at-a-time flow cannot show you that.
"""

from __future__ import annotations

from dataclasses import replace

from rich.console import Console
from rich.prompt import Confirm, Prompt

from dgraph import cross, fanout, limits

con = Console()


class NoTerminal(RuntimeError):
    """Nobody to ask. Carries what to do instead, not just that it failed."""


def _para(label: str, hint: str, default: str) -> str:
    """One free-text answer, with its guidance printed above it.

    The hint is not optional decoration. "What is this fan-out for?" answered
    without being told that the agents get the sentence verbatim produces a
    note to self rather than a brief.
    """
    con.print(f"\n[bold]{label}[/]")
    con.print(f"[dim]{hint}[/]")
    return Prompt.ask("", default=default, show_default=bool(default)).strip()


def _reads(existing: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """`path: what it is`, until a blank line.

    A loop rather than one comma-separated answer: the second half of each
    entry is prose, and prose in a delimited list is prose somebody truncates
    to avoid the delimiter.
    """
    con.print("\n[bold]What may the agents read?[/]")
    con.print("[dim]One per line, as `path: what it is`. They will not find "
              "these on their own, and one that guesses reads the wrong thing "
              "confidently. Blank line when done.[/]")
    out = list(existing)
    for path, what in out:
        con.print(f"  [dim]· {path} — {what}[/]")
    while True:
        line = Prompt.ask("", default="", show_default=False).strip()
        if not line:
            return out
        path, _, what = line.partition(":")
        out.append((path.strip(), what.strip() or "(not described)"))


def ask(plan: fanout.Plan, proj) -> fanout.Plan | None:
    """The plain collector: a question at a time, with `rich` and nothing else.

    Returns `None` if the person declines at the summary — the last chance to
    back out before anything is written, and the reason the summary exists at
    all rather than the answers simply being applied.
    """
    con.print(f"\n[bold]Setting up a fan-out in {proj.root.name}[/]")
    con.print("[dim]Enter accepts the default in brackets. Everything else — "
              "the chain, the areas, what each policy means — is filled from "
              "the graph.[/]")

    brief = _para(
        "What is this fan-out for?",
        "One paragraph. The area being worked and what a good session looks "
        "like; the agents get this verbatim. \"The Search frontier\" beats "
        "\"help with the project\".", plan.brief)

    focus = _para(
        "Which ids is it aimed at?",
        "Comma-separated. Each one's full chain is pasted into the prompt — "
        "the part a fresh context cannot reconstruct for itself.",
        ",".join(plan.focus))

    reads = _reads(plan.reads)

    findings = _para("Where does an agent put what it produces?",
                     "One file per task, named in its `--outcome`.",
                     plan.findings)

    con.print("\n[bold]How many agents, and on which host?[/]")
    agents = Prompt.ask("  agents", default=str(plan.agents))
    host = Prompt.ask("  host", choices=sorted(fanout.HOSTS),
                      default=plan.host)

    con.print("\n[bold]What may an agent settle on its own?[/]  [dim]$DG_DECIDE[/]")
    con.print("[dim]evidence: only a question a finished --evidence-for task "
              "backs · never: nothing · open: anything[/]")
    decide = Prompt.ask("", choices=list(cross.POLICIES), default=plan.decide)

    con.print("\n[bold]Where may an agent write?[/]  [dim]$DG_WRITE[/]")
    con.print("[dim]launch: this project and /tmp, anywhere else asks the "
              "person · open: unrestricted. Reads are never judged.[/]")
    write = Prompt.ask("", choices=list(limits.WRITE_POLICIES),
                       default=plan.write)

    con.print("\n[bold]How long before its work is handed back?[/]")
    con.print("[dim]Nothing kills the agent — `timeout` in the launcher does "
              "that. This is what `dg agent expire` measures against.[/]")
    while True:
        raw = Prompt.ask("", default=limits.show_span(plan.budget))
        try:
            budget = limits.span(raw)
            break
        except limits.BadSpan as exc:
            con.print(f"[yellow]  {exc}[/]")

    capture = Confirm.ask("\nRecord the run (.dgraph-capture/)?",
                          default=plan.capture)

    try:
        n = max(1, int(agents))
    except ValueError:
        n = plan.agents

    out = replace(plan, brief=brief,
                  focus=[s.strip() for s in focus.split(",") if s.strip()],
                  reads=reads, findings=findings or plan.findings, agents=n,
                  host=host, decide=decide, write=write, budget=budget,
                  capture=capture)

    con.print(f"\n[bold]{out.agents} agent(s) on {out.host}[/] · "
              f"$DG_DECIDE={out.decide} · $DG_WRITE={out.write} · "
              f"budget {limits.show_span(out.budget)}"
              + (" · captured" if out.capture else ""))
    con.print(f"[dim]focus {', '.join(out.focus) or '(none)'} · "
              f"findings at {out.findings}[/]")
    return out if Confirm.ask("Write the files?", default=True) else None


def collect(plan: fanout.Plan, proj, *, interactive: bool,
            prefer_tui: bool = True) -> fanout.Plan | None:
    """Ask, whichever way this terminal allows. `None` if the person backed out.

    `interactive` is the caller's `_interactive()` — passed rather than read so
    the one "is there a person there" decision stays in a single place and a
    test can say otherwise without owning stdin.
    """
    if not interactive:
        raise NoTerminal(
            "no terminal to ask on. Give the answers as flags — "
            "`dg agent setup --json` prints the defaults and the three it "
            "still has to ask for")
    if prefer_tui:
        try:
            from dgraph import wizard_tui
        except ImportError:
            # Not worth a word. The plain collector asks the same questions and
            # produces the same files; announcing a missing optional dependency
            # before every setup would be noise about a difference that does
            # not change the result.
            pass
        else:
            return wizard_tui.run(plan, proj)
    return ask(plan, proj)
