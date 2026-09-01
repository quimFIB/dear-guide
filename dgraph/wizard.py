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

The plain collector is not a degraded mode. It asks the same twelve questions
in the same order and produces the same bytes, which a test asserts across all
three paths; what the TUI adds is seeing them together, because `never` with a
45-minute budget is a different run from `evidence` with fifteen and a
question-at-a-time flow cannot show you that.
"""

from __future__ import annotations

from dataclasses import replace

from rich.console import Console
from rich.prompt import Confirm, Prompt

from dgraph import confine as _confine, cross, env, fanout, limits

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


def _remit(plan: fanout.Plan, proj) -> fanout.Plan:
    """The first question, and the one that answers six of the others.

    Asked before anything else because the policy block is what a newcomer
    cannot answer -- every one of those fields needs a section of
    `agentic/README.md` behind it -- and because a preset chosen after the fact
    would silently overwrite answers the person had already given.

    **It prefills; it does not lock.** Every field it sets is asked again below
    with the preset's value as the default, which is the whole difference
    between a curated starting point and a mode. `customise` is the same form
    with the tool's own defaults, and is not a fourth remit.
    """
    con.print("\n[bold]What may these agents settle?[/]")
    con.print("[dim]One answer fills the whole policy block; every field is "
              "asked again below, with these as the defaults.[/]")
    for name, row, _, _, _ in fanout.preset_rows(proj):
        con.print(f"  [bold]{name}[/]  [dim]{row}[/]")
    con.print("  [bold]customise[/]  [dim]the tool's own defaults, "
              "unchanged[/]")
    choice = Prompt.ask("", choices=[*fanout.PRESETS, "customise"],
                        default=fanout.preset_of(plan, proj)
                        or fanout.DEFAULT_PRESET)
    if choice == "customise":
        return plan
    out = fanout.apply_preset(plan, choice, proj)
    con.print(f"[dim]  $DG_DECIDE={out.decide} · $DG_APPLY={out.apply} · "
              f"$DG_WRITE={out.write} · "
              f"$DG_AREA={out.area} · $DG_TERSE={out.terse} · "
              f"{len(out.exec_allow)} program(s) unasked · "
              f"floor {out.floor if out.confine != 'off' else 'off'}[/]")
    return out


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

    plan = _remit(plan, proj)

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

    con.print("\n[bold]May an agent write the store itself?[/]  [dim]$DG_APPLY[/]")
    con.print("[dim]own: it applies its own staged ops — an added question "
              "lands without you · never: it only stages, and a caller with no "
              "$DG_AGENT applies. `never` makes the tray an approval queue, "
              "which is what it is not by default.[/]")
    apply_ = Prompt.ask("", choices=list(env.APPLY_POLICIES), default=plan.apply)

    con.print("\n[bold]Where may an agent write?[/]  [dim]$DG_WRITE[/]")
    con.print("[dim]launch: this project and /tmp, anywhere else asks the "
              "person · open: unrestricted. Reads are never judged.[/]")
    write = Prompt.ask("", choices=list(limits.WRITE_POLICIES),
                       default=plan.write)

    con.print("\n[bold]May an agent file under a new area?[/]  [dim]$DG_AREA[/]")
    con.print("[dim]open: any area — a name resembling one in use is refused, "
              "and `--new-area` overrides · strict: only areas already in use, "
              "and a new one goes back to a person. `open` is usually right: a "
              "scout finding a corner nobody had named is a finding.[/]")
    area = Prompt.ask("", choices=list(env.AREA_POLICIES), default=plan.area)

    con.print("\n[bold]How long before its work is handed back?[/]")
    con.print("[dim]`dg-agent run` stops the child at this and parks what it "
              "was holding; `dg-agent expire` is the backstop for what that "
              "cannot see.[/]")
    while True:
        raw = Prompt.ask("", default=limits.show_span(plan.budget))
        try:
            budget = limits.span(raw)
            break
        except limits.BadSpan as exc:
            con.print(f"[yellow]  {exc}[/]")

    con.print("\n[bold]How long may a field be?[/]  [dim]$DG_TERSE[/]")
    con.print("[dim]The store holds the synopsis a person reads while "
              "deciding; the development goes in a file the record cites. "
              "`on`, a character count, or `off`.[/]")
    while True:
        terse = Prompt.ask("", default=plan.terse).strip().lower()
        if terse in limits.TERSE_OFF or limits.terse_limit(terse) is not None:
            break
        con.print("[yellow]  `on`, `off`, or a character count[/]")

    con.print("\n[bold]What may an agent run without asking?[/]  "
              "[dim]$DG_EXEC_ALLOW[/]")
    con.print("[dim]Program names, space-separated — names, not command lines. "
              "Anything else stops and goes to `dg-agent broker`, and so does "
              "any line running more than one program. Derived from this "
              "project's marker files; edit or empty it.[/]")
    exec_allow = _names("", " ".join(plan.exec_allow))

    con.print("\n[bold]Is a confinement floor required?[/]  [dim]$DG_CONFINE · "
              "$DG_FLOOR[/]")
    con.print("[dim]require: the boundaries above are enforced by the kernel "
              "too, so a shell redirection no gate sees is refused as well · "
              "off: the gate and the broker are all that judge. A run that "
              "asks for a floor it cannot get refuses to start.[/]")
    confine = Prompt.ask("  confine", choices=list(_confine.CONFINE_MODES),
                         default=plan.confine)
    floor = plan.floor
    if confine != "off":
        floor = Prompt.ask("  backend", choices=list(_confine.BACKENDS),
                           default=plan.floor)
        ok, why = _confine.available(floor)
        if not ok:
            con.print(f"[yellow]  {floor} will not confine anything here — "
                      f"{why}[/]")

    capture = Confirm.ask("\nRecord the run (.dgraph-capture/)?",
                          default=plan.capture)

    try:
        n = max(1, int(agents))
    except ValueError:
        n = plan.agents

    out = replace(plan, apply=apply_, brief=brief,
                  focus=[s.strip() for s in focus.split(",") if s.strip()],
                  reads=reads, findings=findings or plan.findings, agents=n,
                  host=host, decide=decide, write=write, area=area,
                  budget=budget, terse=terse, capture=capture,
                  exec_allow=exec_allow, confine=confine, floor=floor)

    con.print(f"\n[bold]{out.agents} agent(s) on {out.host}[/] · "
              f"$DG_DECIDE={out.decide} · $DG_APPLY={out.apply} · "
              f"$DG_WRITE={out.write} · "
              f"$DG_AREA={out.area} · $DG_TERSE={out.terse} · "
              f"budget {limits.show_span(out.budget)} · "
              f"{len(out.exec_allow) or 'no'} program(s) unasked · "
              f"floor {out.floor if out.confine != 'off' else 'off'}"
              + (" · captured" if out.capture else ""))
    con.print(f"[dim]focus {', '.join(out.focus) or '(none)'} · "
              f"findings at {out.findings}[/]")
    return out if Confirm.ask("Write the files?", default=True) else None


def _names(label: str, default: str) -> list[str]:
    """A space-separated list of program names, asked once and kept in order.

    Not validated here beyond splitting: `fanout.read_env_plan` and
    `_compose` both refuse a token carrying shell syntax, at the two moments a
    launcher can still fix it, and a third rule written here would be a third
    thing to keep true.
    """
    raw = Prompt.ask(label, default=default)
    return list(dict.fromkeys(raw.split()))


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
            "`dg-agent setup --json` prints the defaults and the three it "
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
