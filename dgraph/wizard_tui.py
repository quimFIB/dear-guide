"""The fan-out setup wizard, as a full-screen terminal UI.

One of three collectors. `wizard.py` beside this holds the plain one, which
needs no dependency beyond what the tool already has, and decides which of them
runs; `fanout.py` turns what any of them collects into files.

**This module collects answers and does nothing else.** Every question it asks
maps to one field of `fanout.Plan`, and the moment it has one it hands it back;
what a plan turns into lives in `fanout.py`, where it is tested and where the
non-interactive path reaches it too. An adapter that also decided something
would be a second implementation of the setup, in the one file this repo cannot
easily test — the same argument `hooks/precommit.py` makes about the gate.

That split is why the wizard can be optional. `textual` is an extra, the CLI
works without it, and an agent inside Claude Code or opencode — which cannot
drive a full-screen app at all — reaches the identical result through flags.

Imported lazily by `wizard.collect`, so the import cost and the dependency both
fall on the one path that needs them. A missing `textual` is not an error
anywhere: the plain collector takes over and asks the same questions.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (Button, Checkbox, Footer, Header, Input, Label,
                             RadioButton, RadioSet, Static, TextArea)

from dgraph import confine as _confine, cross, env, fanout, limits

#: The budgets offered as buttons. A free-text field is there too — these are
#: the ones worth one keystroke, chosen to bracket what a scout actually needs
#: rather than to enumerate anything.
BUDGETS = ("15m", "30m", "1h", "2h", "infinite")


class Wizard(App):
    """One screen, everything visible. Deliberately not a sequence of pages.

    A page-at-a-time wizard hides what it already asked, and the interesting
    thing about these answers is how they read *together*: `never` with a
    45-minute budget is a different run from `evidence` with fifteen. Scrolling
    beats paging when the whole form fits in a screen and a half.
    """

    CSS = """
    Screen { layout: vertical; }
    #body { padding: 1 2; }
    .q { margin-top: 1; text-style: bold; }
    .hint { color: $text-muted; margin-bottom: 1; }
    Input, TextArea { margin-bottom: 1; }
    TextArea { height: 5; }
    #actions { height: auto; padding: 1 2; }
    Button { margin-right: 2; }
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Write the files"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, plan: fanout.Plan, proj):
        super().__init__()
        self.plan = plan
        self.proj = proj
        self.result: fanout.Plan | None = None
        #: The remit cards, in the order they are shown, so `RadioSet.Changed`
        #: can name one from its index. The labels carry the row beside the
        #: name and are not a key.
        self._presets = list(fanout.PRESETS)
        #: Which card the incoming plan already *is*, or `None` once it has
        #: been edited away from all of them. Computed rather than stored, for
        #: the reason `fanout.preset_of` gives.
        self._preset = fanout.preset_of(plan, proj)

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="body"):
            yield Static(f"Fan-out in [b]{self.proj.root.name}[/]", classes="q")
            yield Static("Everything below is written into fanout/scout.md and "
                         "fanout/launch.sh. The graph fills the rest.",
                         classes="hint")

            yield Label("What may these agents settle?", classes="q")
            yield Static("One answer fills the whole policy block below. It "
                         "prefills rather than locks — every field it sets is "
                         "still yours to change, and `customise` leaves them "
                         "at the tool's own defaults.", classes="hint")
            with RadioSet(id="preset"):
                for name, row, _, _, _ in fanout.preset_rows(self.proj):
                    yield RadioButton(f"{name}  —  {row}",
                                      value=(name == self._preset))
                yield RadioButton("customise  —  the tool's own defaults",
                                  value=(self._preset is None))

            yield Label("What is this fan-out for?", classes="q")
            yield Static("One paragraph: the area of the graph being worked, "
                         "and what a good session produces. The agents get "
                         "this verbatim.", classes="hint")
            yield TextArea(self.plan.brief, id="brief")

            yield Label("Which ids is it aimed at?", classes="q")
            yield Static("Comma-separated. Each one's full chain is pasted "
                         "into the prompt — the part a fresh context cannot "
                         "reconstruct.", classes="hint")
            yield Input(",".join(self.plan.focus), id="focus")

            yield Label("What may the agents read?", classes="q")
            yield Static("One per line, as PATH: what it is. They will not "
                         "find these on their own.", classes="hint")
            yield TextArea("\n".join(f"{p}: {w}" for p, w in self.plan.reads),
                           id="reads")

            yield Label("Where does an agent put what it produces?", classes="q")
            yield Input(self.plan.findings, id="findings")

            yield Label("How many agents, and on which host?", classes="q")
            yield Input(str(self.plan.agents), id="agents", type="integer")
            with RadioSet(id="host"):
                for h in fanout.HOSTS:
                    yield RadioButton(h, value=(h == self.plan.host))

            yield Label("What may an agent settle on its own?  $DG_DECIDE",
                        classes="q")
            yield Static("evidence: only a question a finished --evidence-for "
                         "task backs · never: nothing · open: anything",
                         classes="hint")
            with RadioSet(id="decide"):
                for p in cross.POLICIES:
                    yield RadioButton(p, value=(p == self.plan.decide))

            yield Label("May an agent write the store itself?  $DG_APPLY",
                        classes="q")
            yield Static("own: it applies its own staged ops, so an added "
                         "question lands without you · never: it only stages, "
                         "and a caller with no $DG_AGENT applies.",
                         classes="hint")
            with RadioSet(id="apply"):
                for p_ in env.APPLY_POLICIES:
                    yield RadioButton(p_, value=(p_ == self.plan.apply))

            yield Label("Where may an agent write?  $DG_WRITE", classes="q")
            yield Static("launch: this project and /tmp; anywhere else asks "
                         "the person. Reads are never restricted.",
                         classes="hint")
            with RadioSet(id="write"):
                for p in limits.WRITE_POLICIES:
                    yield RadioButton(p, value=(p == self.plan.write))

            yield Label("May an agent file under a new area?  $DG_AREA",
                        classes="q")
            yield Static("open: any area — one resembling an area in use is "
                         "refused, and --new-area overrides · strict: only "
                         "areas already in use. `open` is usually right: a "
                         "scout finding a corner nobody had named is a "
                         "finding.", classes="hint")
            with RadioSet(id="area"):
                for p in env.AREA_POLICIES:
                    yield RadioButton(p, value=(p == self.plan.area))

            yield Label("How long before its work is handed back?", classes="q")
            yield Static("`dg-agent run` stops the child at this and parks "
                         "what it was holding; `dg-agent expire` is the "
                         "backstop for what that cannot see.",
                         classes="hint")
            yield Input(limits.show_span(self.plan.budget), id="budget")

            yield Label("How long may a field be?  $DG_TERSE", classes="q")
            yield Static("The store holds the synopsis a person reads while "
                         "deciding; the development goes in a file the record "
                         "cites. `on`, a character count, or `off`.",
                         classes="hint")
            yield Input(self.plan.terse, id="terse")

            yield Label("What may an agent run without asking?  "
                        "$DG_EXEC_ALLOW", classes="q")
            yield Static("Program names, space-separated — names, not command "
                         "lines. Anything else stops and goes to the broker, "
                         "and so does any line running more than one program. "
                         "Derived from this project's marker files.",
                         classes="hint")
            yield Input(" ".join(self.plan.exec_allow), id="exec_allow")

            yield Label("Is a confinement floor required?  $DG_CONFINE",
                        classes="q")
            yield Static("require: the boundaries above are enforced by the "
                         "kernel too, so a shell redirection no gate sees is "
                         "refused as well · off: the gate and the broker are "
                         "all that judge. A run that asks for a floor it "
                         "cannot get refuses to start.", classes="hint")
            with RadioSet(id="confine"):
                for p in _confine.CONFINE_MODES:
                    yield RadioButton(p, value=(p == self.plan.confine))

            yield Label("…and which backend provides it?  $DG_FLOOR",
                        classes="q")
            yield Static(" · ".join(
                f"{b}: {'usable here' if _confine.available(b)[0] else _confine.available(b)[1]}"
                for b in _confine.BACKENDS), classes="hint")
            with RadioSet(id="floor"):
                for p in _confine.BACKENDS:
                    yield RadioButton(p, value=(p == self.plan.floor))

            yield Checkbox("Record the run (.dgraph-capture/)", self.plan.capture,
                           id="capture")
        with Horizontal(id="actions"):
            yield Button("Write files", variant="primary", id="save")
            yield Button("Cancel", id="cancel")
        yield Footer()

    # ---- collecting ------------------------------------------------------

    def _chosen(self, rid: str, fallback: str) -> str:
        """Which button of a set is on.

        Read off the buttons, not off `RadioSet.pressed_button`. The set keeps
        that pointer up to date from the messages its buttons send, and
        `_prefill` moves them with those messages suppressed — deliberately, for
        the reason given there — so the pointer can lag behind what is on
        screen. What is on screen is what the person answered.
        """
        on = next((b for b in self.query_one(f"#{rid}", RadioSet).query(RadioButton)
                   if b.value), None)
        return str(on.label) if on is not None else fallback

    def _plan(self) -> fanout.Plan:
        """Every widget, read once, into a `Plan`. No validation beyond what
        cannot be expressed in the widget: the radio sets can only hold real
        values, and the budget and the field limit are the two free-text
        fields that can be wrong."""
        from dataclasses import replace

        reads = []
        for line in self.query_one("#reads", TextArea).text.splitlines():
            if not line.strip():
                continue
            path, _, what = line.partition(":")
            reads.append((path.strip(), what.strip() or "(not described)"))
        try:
            budget = limits.span(self.query_one("#budget", Input).value)
        except limits.BadSpan:
            budget = self.plan.budget
        try:
            n = max(1, int(self.query_one("#agents", Input).value or 1))
        except ValueError:
            n = self.plan.agents
        # The second free-text field that can be wrong, and answered the same
        # way as the budget: keep what the plan already had rather than
        # silently widening the rule the person came here to set.
        terse = (self.query_one("#terse", Input).value or "").strip().lower()
        if terse not in limits.TERSE_OFF and limits.terse_limit(terse) is None:
            terse = self.plan.terse
        return replace(
            self.plan,
            brief=self.query_one("#brief", TextArea).text.strip(),
            focus=[s.strip() for s
                   in self.query_one("#focus", Input).value.split(",")
                   if s.strip()],
            reads=reads,
            findings=(self.query_one("#findings", Input).value.strip()
                      or self.plan.findings),
            agents=n,
            host=self._chosen("host", self.plan.host),
            decide=self._chosen("decide", self.plan.decide),
            apply=self._chosen("apply", self.plan.apply),
            write=self._chosen("write", self.plan.write),
            area=self._chosen("area", self.plan.area),
            budget=budget,
            terse=terse,
            capture=self.query_one("#capture", Checkbox).value,
            exec_allow=list(dict.fromkeys(
                (self.query_one("#exec_allow", Input).value or "").split())),
            confine=self._chosen("confine", self.plan.confine),
            floor=self._chosen("floor", self.plan.floor),
        )

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """Prefill the policy block from a chosen remit.

        Only this set; the other seven are answers in their own right and
        setting them below fires this again, which the guard drops.

        It writes into the widgets rather than into `self.plan` so that what
        the person sees is what will be collected — a preset applied to the
        plan alone would leave the form showing the old values and `_plan`
        reading them straight back out.
        """
        if event.radio_set.id != "preset":
            return
        if event.index >= len(self._presets):        # customise
            self._preset = None
            return
        self._preset = self._presets[event.index]
        self._prefill(self._preset)

    def _prefill(self, preset: str) -> None:
        """Write one preset's policy block into the widgets.

        Into the widgets rather than into `self.plan`, so that what the person
        sees is what will be collected — a preset applied to the plan alone
        would leave the form showing the old values and `_plan` reading them
        straight back out.

        **The sets are moved with their own messages suppressed.** A `RadioSet`
        posts its `Changed` from inside `prevent(RadioButton.Changed)`, and a
        message carries that suppression into its handler — so a button pressed
        from here reaches no set, and one asked to switch *off* is switched
        straight back on by the set defending its invariant. Both leave a set
        displaying one answer and reporting another. Suppressing deliberately
        and setting every button of the set is the honest version: nothing is
        half-delivered, and `_chosen` reads the buttons rather than the set's
        own idea of which is pressed.
        """
        out = fanout.apply_preset(self.plan, preset, self.proj)
        with self.prevent(RadioButton.Changed, RadioSet.Changed):
            for rid, value in (("decide", out.decide), ("apply", out.apply),
                           ("write", out.write),
                               ("area", out.area), ("confine", out.confine),
                               ("floor", out.floor)):
                for button in self.query_one(f"#{rid}", RadioSet).query(RadioButton):
                    button.value = str(button.label) == value
        self.query_one("#terse", Input).value = out.terse
        self.query_one("#exec_allow", Input).value = " ".join(out.exec_allow)

    def action_save(self) -> None:
        self.result = self._plan()
        self.exit(self.result)

    def action_cancel(self) -> None:
        self.result = None
        self.exit(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        (self.action_save if event.button.id == "save"
         else self.action_cancel)()


def run(plan: fanout.Plan, proj) -> fanout.Plan | None:
    """Collect answers. `None` if the person cancelled."""
    return Wizard(plan, proj).run()
