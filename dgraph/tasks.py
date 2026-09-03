"""The task graph: work, what it waits on, and the invariants over them.

`tasks.json` is the store; `tasks.md` is a rendered view of it. The schema is a
plain graph, like the decision store, but a simpler one:

  task    a unit of work, with an explicit status
  edge    a relation between two tasks, of one of two kinds:

            precedes  `from` must be resolved before anything in `to` can start
            prompted  doing `from` turned `to` up

Beyond that kind an edge carries no payload. There is nothing to record about
"T01 comes before T02" beyond the fact itself, and what T01 *produced* is its
own `outcome` — one fact about one task, not a fact about each edge leaving it.

This module is deliberately **not** a copy of `dgraph/model.py`, because tasks
are not decisions and the differences are the whole point of keeping two stores:

- **Blocked is derived, never stored.** A task is ready when its prerequisites
  are resolved. The decision graph keeps status explicit because a decision can
  have consequences and still be under review — out-degree says nothing about
  it. Task readiness genuinely *is* a function of dependencies, so storing it
  would only create something that can go stale. The decision store came
  round to the same view for *waiting* (`D68`): its `BLOCKED:<id>` status was
  a stored copy of an edge, and the rule that kept it honest, `stale_block`,
  went with it.
- **Supersession, in two places, and both are lists.** A decision that is
  overturned keeps its old answer forever, because how a project changed its
  mind is worth more than the conclusion. Here that applies to the two
  questions whose answers can stop being current and still be worth having —
  *why is this work not being done* and *what did it produce* — so every
  stoppage appends to `Task.stops` and every completion appends to
  `Task.completions`, and nothing ever clears either. Work put down three
  times says so; work finished twice keeps both results. Which entry is
  **live** is never stored: `stopped_because`, `done` and `outcome` all derive
  it from the status, which is what stops a record outliving the claim it
  used to support. Every other field this store keeps is current-state.

  `completions` was the exception until audit `F-F5`, and it is worth saying
  why the exception was not visible: the pair was two scalars that `_apply_one`
  assigned, so a second `dg task done` overwrote a result and its date with
  every invariant still holding. Nothing about the record's *shape* changed,
  which is exactly what an invariant can see.
- **Two ways to stop, and they differ downstream, not in the record.** `PARKED`
  is work nobody is doing; `DROPPED` is work nobody is going to do. Both write
  the same `Stop`. What separates them is whether the work that waited on this
  is now free to proceed — abandoning says yes and releases it, parking says no
  and holds it. A store with one stopping status has to answer that question
  the same way twice, and neither answer is right for both cases.
- **No falsifier, no answer.** A task is finished when it is done, and what it
  produced is an `outcome` — a path, a PR, a note. That is a record, not a
  claim about the world, so nothing can falsify it. A *later* outcome does not
  falsify an earlier one either: both happened, and which one the work stands
  on now is the status's business.
- **No orphan check.** An unconnected task is ordinary; an unconnected decision
  is a smell.

Nothing here imports `dgraph.model`, and nothing in `dgraph.model` imports this.
The two stores meet in exactly one place, `dgraph/cross.py`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from dgraph import areas as _areas
from dgraph import project
from dgraph.violation import Violation, cycle_from

STATUSES = ("TODO", "DOING", "PARKED", "DONE", "DROPPED")

#: Work that is still outstanding — what "the backlog" means. `PARKED` belongs
#: here and not below: parked work is put down, not finished with, so it still
#: holds up everything that waits on it. That is the whole difference from
#: `DROPPED`, which releases its dependants because abandoning work *is* the
#: judgement that it was not needed. Parking asserts the opposite.
UNFINISHED = frozenset({"TODO", "DOING", "PARKED"})

#: Work that will not be done again, so it no longer blocks anything. A dropped
#: prerequisite releases its dependants: abandoning it *is* the decision that it
#: was not needed.
RESOLVED = frozenset({"DONE", "DROPPED"})

ID_RE = re.compile(r"T\d+")

#: What an edge between two tasks asserts. Fixed at two, and `validate` refuses
#: any other: an open-ended kind field invites a private vocabulary, and the
#: writer is usually an agent with no way to learn the house one.
#:
#: `prompted` is a relation, so it lives in the edge list rather than in a field
#: on `Task` — the rule the decision store opens with, that dependency is the
#: graph structure and never a stored field. `because` and `evidence_for` are
#: fields only because they cross into a store this module cannot see, where
#: there is no shared edge list to put them in. Within a store there is one.
#:
#: The kinds are independent relations and both may hold between the same pair,
#: including in opposite directions — the ordinary case being work that turned
#: up a cleanup which has to land before that work can be redone. Each kind is
#: therefore walked as its own subgraph; see `validate`.
KINDS = ("precedes", "prompted")

#: How the absence of an edge of each kind reads, for whoever is told there is
#: nothing to remove. Here rather than in the two callers that say it — the
#: staging layer at apply time and the CLI at check time — because the two
#: relations share no verb, and one message loose enough to cover both ("is not
#: related to") would tell nobody which correction they actually failed to make.
#: How a removal reconnects what the task sat between. Same vocabulary as the
#: decision store's, and deliberately the same word for the same act: a person
#: who has learned `dg rm --splice` should not have to learn it twice.
REMOVAL_MODES = ("sever", "splice", "into")

#: What to call the live stop, by the status that claims it. One table, read by
#: every renderer: `task_render` writes it into `tasks.md`, `context` prints it
#: in both its forms, and the server sends it to the browser panel so `app.html`
#: renders the served word rather than choosing a fourth. Three renderers
#: picking the label independently is how the PARKED reason came to be printed
#: by one of them and dropped by the other two.
STOP_LABEL = {"PARKED": "Put down", "DROPPED": "Not being done"}


#: What to call the live completion, beside `STOP_LABEL` and for the same
#: reason. Four renderers draw the completion list — `task_render`, `cli`,
#: `context` and `app.html` — and a word each of them chooses for itself is
#: how the PARKED reason came to be printed by one and dropped by the others.
DONE_LABEL = "the result"


def done_label(status: str) -> str | None:
    """The word for the live completion, or None where the status claims none.

    `stop_label`'s twin. The status is what decides, exactly as it does in
    `Task.done` — a completion under restarted work is a record, not a claim.
    """
    return DONE_LABEL if status == "DONE" else None


def stop_label(status: str) -> str | None:
    """The label for the live stop, or `None` where the status claims none.

    Follows `Task.stopped_because`: the status is what decides whether a stop
    is live, so the same statuses that produce a reason produce a label.
    """
    return STOP_LABEL.get(status)

MISSING_EDGE = {
    "precedes": "{src} is not a prerequisite of {other}",
    "prompted": "{other} was not discovered during {src}",
}


@dataclass
class Stop:
    """One time this work stopped, and why. Written by `park` and by `drop`.

    The store's only archived record, and deliberately one record rather than
    two: a park and a drop answer the same question — why is this not being
    done — and which of them it was is already in the status. Keeping a
    separate live field for the current reason was the same fact stored twice,
    in the arrangement where the copies can disagree.

    Two fields and no more: what stopped it, and when. What *restarted* it is a
    relation between tasks, which is what the `prompted` edge already says.

    **And not who.** Attribution was briefly added here for a fan-out that
    wanted to know which agent abandoned what, and taken straight back out: this
    record is kept forever, agent names are recycled, and in six months "who
    parked this" is noise that a name no longer even identifies. Who is holding
    work is a fact about a RUN, and `dgraph/agents.py` keeps it in scratch that
    evaporates. See there.
    """

    why: str
    date: str


@dataclass
class Completion:
    """One time this work was finished, and what it produced.

    `Stop`'s twin, and for the same reason: a record kept forever is appended,
    never assigned. A task can be finished, restarted and finished again — a
    measurement redone with a different method, a fix shipped twice — and each
    time the store holds a dated account of what came out. Overwriting the
    first one destroys the only copy of a result, silently, and no invariant
    can notice because nothing about the record's *shape* changed.

    Two fields and no more: when it finished, and what it produced -- not who
    produced it, for the reason `Stop` gives. Which completion is *live* is not
    stored, for the reason `stopped_because` gives:
    the status decides, so `Task.done` and `Task.outcome` derive it, and a
    completion that a later status contradicts cannot rot because it never
    claimed to be current.
    """

    date: str
    outcome: str


@dataclass
class Reading:
    """One time this work was read against the answer it was evidence for.

    The exit `evidence_after_deciding` had no other way to offer. Evidence that
    lands after a decision is settled has three possible readings — it refutes
    the answer, it was never needed, or **it confirms the answer** — and only
    the first two had a command. The third is the common one, and without this
    the warning could not end: `reopen` asserts a doubt that is not there,
    `unlink` deletes the measurement from the record, and doing nothing leaves
    a permanent warning that trains the eye past every other one.

    Three fields. `against` is a `D`-id in the *other* store, held for the
    reason `evidence_for` is held here and interpreted no more than that one
    is: it makes the record true on its own, and it stops a reading following
    the task if the link is later moved to a different question.

    **Not the field the store refuses to keep.** The rule (`released_by_drop`,
    below) bans a stored field whose only job is to silence a check, and its
    reason is that such a field goes stale. This one cannot: it is a dated
    record of a past act, like `Stop`, not a claim about the present. A
    *later* measurement post-dates the reading and the finding returns on its
    own — which is the behaviour a boolean "acknowledged" could never have, and
    the test that tells the two apart.
    """

    date: str
    note: str
    against: str


@dataclass
class Task:
    id: str
    title: str
    area: str
    status: str = "TODO"
    note: str | None = None      # prose: what this involves, why it is parked
    #: Every time this work was finished, oldest first, and never cleared.
    #: `done` and `outcome` read the last entry and are derived, not stored —
    #: see those properties for why. Held as a list because the pair used to be
    #: two scalars, and a second `dg task done` overwrote both with `dg check`
    #: reporting clean: the one archival record in either store kept somewhere
    #: a later write could erase it.
    completions: list[Completion] = field(default_factory=list)
    #: Why the work is not being done, written by `dg task drop`. Its own
    #: field rather than the note, because overwriting the only description of
    #: what the work *was* is the opposite of keeping a record: the decision
    #: store keeps a superseded answer forever for the same reason.
    #: Every time this work stopped, oldest first, and never cleared — the
    #: exception the module docstring argues for. While the status is PARKED or
    #: DROPPED the last entry is the live reason, and `stopped_because` is how
    #: to read it; otherwise the list is the record of what kept stopping this
    #: work, which is the thing worth having. There is no companion field for
    #: "the current reason": that was the same fact in two places, and the
    #: copies could disagree.
    stops: list[Stop] = field(default_factory=list)
    #: The dialect of every piece of prose this record holds — the note, the
    #: outcome and the reason it was dropped, all converted through this one
    #: field by `task_render`. "org", else markdown. See `task_pending.PROSE`.
    format: str | None = None
    #: The decisions this work exists because of — a `D`-id in the *other*
    #: store, held as a list because a task may rest on more than one premise
    #: at once. Held here and nowhere else: `decisions.json` never names a task,
    #: so a change to it always means a decision changed. Nothing in this
    #: module resolves it; that is `dgraph/cross.py`'s job, and this module
    #: cannot even see the decision store.
    because: list[str] = field(default_factory=list)
    #: The decision this work will *bear on* — the spike whose result feeds a
    #: question, or the chore that turned out to raise one. Opposite polarity
    #: to `because`: that one makes the task wait on the decision, this one
    #: makes the decision wait on the task. Deliberately not called `settles`:
    #: a task produces evidence, a person decides.
    evidence_for: str | None = None
    #: Every time this work was read against the answer it informs, oldest
    #: first, and never cleared — the same archival treatment `stops` gets, for
    #: the same reason: it records an act that happened, and an act that
    #: happened stays happened. `cross.evidence_after_deciding` reads the last
    #: one as its baseline; nothing here interprets `against`.
    readings: list[Reading] = field(default_factory=list)

    def read_against(self, did: str) -> str | None:
        """The date this work was last read against `did`, if it ever was.

        A plain `max` over strings, which is what ISO dates are for. Filtered
        by `against` rather than taking the last entry: a task whose link was
        moved must not carry the old question's readings into the new one.
        """
        dates = [r.date for r in self.readings if r.against == did]
        return max(dates) if dates else None

    @property
    def unfinished(self) -> bool:
        return self.status in UNFINISHED

    @property
    def resolved(self) -> bool:
        return self.status in RESOLVED

    @property
    def parked(self) -> bool:
        return self.status == "PARKED"

    @property
    def done(self) -> str | None:
        """The date of the live completion, or None where none is claimed.

        Derived for the reason `stopped_because` is, and the sentence there
        applies word for word: a record that outlived its status cannot exist
        if the status is what decides whether the record is live. Restarting
        finished work leaves its completion in the list and stops it being
        read, which is why nothing clears anything any more.
        """
        return self.completions[-1].date if self._live else None

    @property
    def outcome(self) -> str | None:
        """What the live completion produced. `done`'s other half."""
        return self.completions[-1].outcome if self._live else None

    @property
    def _live(self) -> bool:
        return self.status == "DONE" and bool(self.completions)

    @property
    def stopped_because(self) -> str | None:
        """The live reason, or None where the status makes no such claim.

        Derived rather than stored, which is what retires the whole class of
        violation the old `why` field needed: prose that outlived its status
        cannot exist if the status is what decides whether prose is live.
        """
        if self.status not in ("PARKED", "DROPPED") or not self.stops:
            return None
        return self.stops[-1].why


def fallout(tg: TaskGraph, tid: str) -> dict[str, str]:
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

    **Here rather than in `cli.py`**, beside `dropped_prerequisites` and
    `abandoned_origins`, which are the same reading asked after the fact. It
    lived in the CLI, so the web `Drop it` button could not ask the question at
    all: it posted one op and said nothing, where `dg task drop` refuses until
    every released and orphaned task has a verdict. Two doors onto one act must
    refuse the same thing, and a door can only refuse what it can see.
    """
    out = {}
    for t in tg.unblocks(tid):
        # Work that has already stopped is not released by anything, so it is
        # not asked about. Without the filter a DROPPED dependant was demanded
        # a verdict neither answer could give — `--drop-too` is refused by
        # `task_pending` as a second drop, and `--keep` prints "still worth
        # doing" about abandoned work — and a PARKED one was asked whether the
        # drop had made it startable, which is not a question about a task
        # that is not being done.
        #
        # `unfinished` alone would be the wrong filter here even though it is
        # what the orphaned branch below uses: PARKED is unfinished. The two
        # branches ask different questions — this one is about startability,
        # which only live work has, and that one is about provenance, which
        # parked work still needs.
        #
        # What the parked dependant gets instead, since silence here must not
        # mean silence everywhere: `released_by_drop` fires over PARKED as well
        # as TODO, and `starting_on_abandoned_work` says it again to whoever
        # picks the task up. Do not widen this filter to cover them — it would
        # put the question back where it cannot be answered.
        dep = tg.tasks[t]
        if dep.unfinished and not dep.parked and tg.waiting_on(t) == [tid]:
            out[t] = f"released — waited only on {tid}"
    for t in tg.prompted(tid):
        if tg.tasks[t].unfinished and all(
                o == tid or tg.tasks[o].status == "DROPPED"
                for o in tg.discovered_during(t)):
            out[t] = f"orphaned — discovered during {tid}"
    return out


def starting_on_abandoned_work(tg: TaskGraph, tid: str) -> str | None:
    """The warning for picking `tid` up when what it waited on was abandoned.

    `released_by_drop` says this to whoever reads the store; this says it to
    whoever is about to do the work, at the moment they commit to doing it —
    the same split `cross.deciding_ahead_of_evidence` makes against
    `evidence_after_deciding`, and for the same reason: the check is only read
    by someone who runs it, and starting work is precisely the moment nobody
    does.

    It is also the only cover the DOING case has. Starting a task is what
    *clears* the check — deliberately, since acknowledging it any other way
    would need a stored field — so once the work has begun the store is silent
    by design, and the one thing that must not be silent is the transition
    itself.

    A note, never a refusal: proceeding without an abandoned prerequisite is
    often exactly right, and which of the two an edge was is knowledge the
    store does not have.
    """
    gone = tg.dropped_prerequisites(tid)
    if not gone:
        return None
    return (f"{tid} waited on {', '.join(gone)}, which was abandoned rather "
            f"than finished — check the work is still possible without what "
            f"it would have produced")


def matches(t: Task, op: dict) -> bool:
    """Whether `t` is the task an `add_task` op would have created.

    Only the fields that op writes, so a later `dg task start` on the same task
    does not make a landed op read as a clash. What is being asked is "did my op
    land", not "is the task untouched since".

    **Here rather than beside its decision-store twin** (`pending._same_vertex`),
    and the asymmetry has a cause: two of these fields cross into a store this
    module cannot see, and `tests/test_cross.py` allows them to be named only
    where they are declared and serialised — which is here. `task_pending` calls
    this and never mentions them.

    They are compared rather than skipped, and that direction matters. Two
    writers creating the same title under different premises have *not* created
    the same task, and a comparison that ignored the link would tell the loser
    its work landed when something else did.
    """
    return (t.title == op["title"] and t.area == op["area"]
            and t.status == op.get("status", "TODO")
            and t.note == op.get("note")
            and _fold_because(t.because) == _fold_because(op.get("because"))
            and t.evidence_for == op.get("evidence_for"))


@dataclass
class TaskEdge:
    """A relation between tasks. `kind` says which; there is no other payload.

    `kind` is required, always written, and never defaulted. An omitted field
    would mean every reader — a person scanning `tasks.json`, an agent editing
    it, a diff — has to already know which kind the absence stands for, and a
    store that can be read without knowing the schema is worth the bytes.
    """

    src: str
    to: list[str]
    kind: str


def _fold_because(raw) -> list[str]:
    """Normalise a stored `because` to a list, folding a pre-list scalar.

    `because` is now a list on every `Task`, but a store written before the
    change holds a scalar string. `_task` folds it here so the rest of the tool
    never has to know the old shape.
    """
    if raw is None:
        return []
    return list(raw) if isinstance(raw, (list, tuple)) else [raw]


def _task(raw: dict) -> Task:
    """One stored task. Only `parks` needs building; everything else is scalar.

    A malformed park entry is refused at load rather than in `validate`, for
    the reason a duplicate id is: `Park(**p)` on a dict missing `why` raises
    somewhere unhelpful, and a park silently dropped here would be invisible by
    the time anything could report it — which is the one thing an archived
    record must never be.
    """
    fields = dict(raw)
    if "why" in fields:
        # A store written before stops existed. Refused rather than folded in,
        # for the reason `_edge` refuses an absent `kind`: the repair is
        # mechanical, but the date is not something this function knows, and
        # inventing one would put a fabricated fact into the one record here
        # that is kept forever. Git knows when the drop landed.
        raise ValueError(
            f"{raw.get('id', '?')}: `why` is no longer a field — a reason now "
            f"lives in `stops` with the date it was written. Replace it with "
            f'"stops": [{{"why": …, "date": "YYYY-MM-DD"}}]; `git log -p` has '
            f"the date"
        )
    stops = fields.pop("stops", [])
    try:
        fields["stops"] = [Stop(**k) for k in stops]
    except TypeError as exc:
        raise ValueError(
            f"{raw.get('id', '?')}: malformed stops entry — each needs a "
            f"`why` and a `date`, and nothing else ({exc})"
        ) from None
    completions = fields.pop("completions", [])
    legacy = [fields.pop(f, None) for f in ("done", "outcome")]
    if any(v is not None for v in legacy) and not completions:
        # A store written while a completion was two scalars. Folded rather
        # than refused, unlike the `why` above, because nothing has to be
        # invented: both halves are already in the record, and the date is one
        # of them. A half-written pair folds too, with the missing half empty,
        # so that `validate` can go on reporting it — refusing the load would
        # make the one command that names the fault unable to run.
        completions = [{"date": legacy[0] or "", "outcome": legacy[1] or ""}]
    try:
        fields["completions"] = [Completion(**c) for c in completions]
    except TypeError as exc:
        raise ValueError(
            f"{raw.get('id', '?')}: malformed completions entry — each needs "
            f"a `date` and an `outcome`, and nothing else ({exc})"
        ) from None
    readings = fields.pop("readings", [])
    try:
        fields["readings"] = [Reading(**k) for k in readings]
    except TypeError as exc:
        raise ValueError(
            f"{raw.get('id', '?')}: malformed readings entry — each needs a "
            f"`date`, a `note` and an `against`, and nothing else ({exc})"
        ) from None
    # `because` became a list. A store written before then held a scalar, so it
    # is folded rather than refused, the same way the two-scalar completion was:
    # nothing has to be invented.
    fields["because"] = _fold_because(fields.pop("because", None))
    return Task(**fields)


def _edge(i: int, e: dict) -> TaskEdge:
    """One stored edge, refusing an absent `kind` rather than assuming one.

    Refused at load for the reason a duplicate id is (see `TaskGraph.load`): a
    default applied quietly is invisible by the time anything could report it,
    and the store would then read one way and behave another. A store written
    before kinds existed holds prerequisites and nothing else, so the repair is
    mechanical and the message says exactly what it is.
    """
    if "kind" not in e:
        raise ValueError(
            f"edge {i} ({e.get('from')} -> {', '.join(e.get('to', []))}) has no "
            f'"kind" — every edge names one of {", ".join(KINDS)}. A store '
            f'written before edge kinds existed holds only prerequisites: add '
            f'"kind": "precedes" to each edge.'
        )
    return TaskEdge(src=e["from"], to=list(e.get("to", [])), kind=e["kind"])


@dataclass
class TaskGraph:
    areas: list[str] = field(default_factory=list)
    tasks: dict[str, Task] = field(default_factory=dict)
    edges: list[TaskEdge] = field(default_factory=list)

    # ---- construction ----------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> TaskGraph:
        raw = json.loads((path or project.find().tasks).read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> TaskGraph:
        """Build a task graph from the parsed store. `Graph.from_dict`'s twin,
        and split out for the same reason: one construction path, so a document
        `dg task import` accepts is one `TaskGraph.load` would have accepted."""
        # Refused here rather than in validate(), for the reason the decision
        # store gives: building the dict collapses duplicates silently, so by
        # the time validate() runs the discarded task is already invisible.
        counts = Counter(t["id"] for t in raw["tasks"])
        dupes = sorted(i for i, n in counts.items() if n > 1)
        if dupes:
            raise ValueError(
                f"duplicate task id(s): {', '.join(dupes)} — one entry per id; "
                f"merge or renumber them by hand"
            )
        return cls(
            areas=raw.get("areas", []),
            tasks={t["id"]: _task(t) for t in raw["tasks"]},
            edges=[
                _edge(i, e) for i, e in enumerate(raw.get("edges", []))
            ],
        )

    def to_dict(self) -> dict:
        # `pending._register`'s twin reading — see `model.Graph.to_dict`.
        order = _areas.order(self.areas)
        rows = sorted(self.tasks.values(), key=lambda t: (order(t.area), t.id))
        return {
            "areas": self.areas,
            "tasks": [
                {
                    k: val
                    for k, val in (
                        ("id", t.id), ("title", t.title), ("area", t.area),
                        ("status", t.status), ("note", t.note),
                        # `t.completions`, not `t.done`/`t.outcome`: those are
                        # status-gated, so serializing through them would drop
                        # the record of finished work the moment it restarted.
                        ("completions", [{"date": c.date, "outcome": c.outcome}
                                         for c in t.completions] or None),
                        # Absent rather than `[]` when there is none, matching
                        # every other field here: the store stays readable, and
                        # work that never stopped says so by silence.
                        ("stops", [{"why": k.why, "date": k.date}
                                   for k in t.stops] or None),
                        ("format", t.format), ("because", t.because or None),
                        ("evidence_for", t.evidence_for),
                        ("readings", [{"date": r.date, "note": r.note,
                                       "against": r.against}
                                      for r in t.readings] or None),
                    )
                    if val is not None
                }
                for t in rows
            ],
            "edges": [
                {"from": e.src, "to": sorted(e.to), "kind": e.kind}
                for e in sorted(self.edges, key=lambda e: (e.src, e.kind))
                if e.to
            ],
        }

    def save(self, path: Path | None = None) -> None:
        text = json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"
        project.write_atomic(path or project.find().tasks, text)

    # ---- queries ---------------------------------------------------------

    def _adjacency(self) -> tuple[dict, dict]:
        """`(src, kind) -> heads` and `(head, kind) -> srcs`, in one pass.

        The same two relations `_out` and `_in` compute, for every task at
        once. Built inside the call that wants it and dropped with it — never
        cached on the graph, for the reason `Graph._reverse` gives: a caller
        holding one across a staged op is holding a stale answer, and no write
        path should have to remember a structure it cannot see.
        """
        out: dict[tuple[str, str], set[str]] = {}
        into: dict[tuple[str, str], set[str]] = {}
        for e in self.edges:
            for h in e.to:
                if h in self.tasks:
                    out.setdefault((e.src, e.kind), set()).add(h)
                if e.src in self.tasks:
                    into.setdefault((h, e.kind), set()).add(e.src)
        return out, into

    def _out(self, tid: str, kind: str, _adj=None) -> list[str]:
        """The heads of every `kind` edge leaving `tid`.

        Unions every matching edge rather than demanding one, because a task
        edge carries no payload beyond its kind: two `precedes` edges out of
        `T01` are two sets of successors and mean exactly their union. The
        decision graph needs `one_active_edge` because two answers to one
        question is a contradiction; here there is nothing to contradict. The
        grouping is by `(src, kind)` and not by `src` — the kinds are separate
        relations, and unioning across them is what would lose the distinction.
        """
        if _adj is not None:
            return sorted(_adj[0].get((tid, kind), ()))
        return sorted({t for e in self.edges
                       if e.src == tid and e.kind == kind
                       for t in e.to if t in self.tasks})

    def _in(self, tid: str, kind: str, _adj=None) -> list[str]:
        """The tails of every `kind` edge arriving at `tid`.

        Skips a source naming no task, exactly as `Graph.depends` does: a
        traversal helper that trusts ids crashes the validator that was about to
        report the dangling reference.
        """
        if _adj is not None:
            return sorted(_adj[1].get((tid, kind), ()))
        return sorted({e.src for e in self.edges
                       if e.kind == kind and tid in e.to and e.src in self.tasks})

    def unblocks(self, tid: str, _adj=None) -> list[str]:
        """The tasks this one is a prerequisite for."""
        return self._out(tid, "precedes", _adj)

    def prerequisites(self, tid: str, _adj=None) -> list[str]:
        """Derived, never stored: what must be resolved before this can start."""
        return self._in(tid, "precedes", _adj)

    def prompted(self, tid: str, _adj=None) -> list[str]:
        """The work that doing this one turned up. Provenance, not dependency.

        Deliberately absent from `waiting_on` and `ready`, which is the whole
        reason it is a separate kind: a chore noticed while doing `tid` is
        usually startable at once, and frequently has to land *before* `tid`
        can be finished. Treating it as an ordering would assert the opposite.

        What it is for is the question the edge list cannot otherwise answer —
        when `tid` is abandoned, which work existed only because of it.
        """
        return self._out(tid, "prompted", _adj)

    def discovered_during(self, tid: str, _adj=None) -> list[str]:
        """The work whose doing turned this one up. The reverse of `prompted`."""
        return self._in(tid, "prompted", _adj)

    # ---- what abandoning work leaves behind ------------------------------
    #
    # `RESOLVED` covers DONE and DROPPED alike, so `waiting_on` cannot tell a
    # prerequisite that finished from one that was given up on. That is the
    # right default — the alternative deadlocks everything downstream of any
    # abandoned task forever — but it makes the release silent, and these two
    # are what `validate` asks in order to break the silence.

    def dropped_prerequisites(self, tid: str, _adj=None) -> list[str]:
        """The prerequisites of `tid` that were abandoned rather than finished.

        A released dependant is not always a startable one: a prerequisite that
        *produced* something this work consumes does not release it, it
        undermines it. The store cannot tell those apart — which of the two an
        edge was usually only becomes clear when you try to proceed without it
        — so it reports the fact and leaves the judgement where the knowledge is.
        """
        return [p for p in self.prerequisites(tid, _adj)
                if self.tasks[p].status == "DROPPED"]

    def abandoned_origins(self, tid: str, _adj=None) -> list[str]:
        """The origins of `tid`, but only when *every* one was abandoned.

        Partial abandonment is not a silence worth breaking: one surviving
        origin still explains why the work exists. Empty for work with no
        origin recorded, which is the ordinary case for anything planned.
        """
        origins = self.discovered_during(tid, _adj)
        return origins if origins and all(
            self.tasks[o].status == "DROPPED" for o in origins) else []

    def waiting_on(self, tid: str, _adj=None) -> list[str]:
        """The prerequisites that are not resolved yet."""
        return [p for p in self.prerequisites(tid, _adj)
                if not self.tasks[p].resolved]

    def ready(self, tid: str, _adj=None) -> bool:
        """Startable now: not yet begun, and nothing outstanding before it."""
        return self.tasks[tid].status == "TODO" and not self.waiting_on(tid, _adj)

    def blocked(self, tid: str, _adj=None) -> bool:
        return self.tasks[tid].unfinished and bool(self.waiting_on(tid, _adj))

    def blocked_ids(self) -> list[str]:
        """Everything blocked, so that no surface counts it its own way.

        `is:blocked`, the `dg task` table, the web panel and `dg brief` all
        answer "who is blocked?" and must answer it identically. The brief
        once derived the count as frontier-minus-ready, which called every
        DOING and PARKED task blocked; the fix is not better arithmetic there
        but one definition here that every surface reads.
        """
        adj = self._adjacency()
        return sorted(t.id for t in self.tasks.values()
                      if self.blocked(t.id, adj))

    def frontier(self) -> list[str]:
        """Everything still outstanding, ready or not."""
        return sorted(t.id for t in self.tasks.values() if t.unfinished)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.tasks.values():
            out[t.status] = out.get(t.status, 0) + 1
        return out

    # ---- invariants ------------------------------------------------------

    def validate(self) -> list[Violation]:
        v: list[Violation] = []

        def add(c, m, severity="error"):
            v.append(Violation(c, m, severity))

        ids = set(self.tasks)
        # One grouping of the edges for the whole pass. Every rule below asks
        # about prerequisites or successors of each task in turn, and each of
        # those was a fresh scan of the edge list.
        adj = self._adjacency()

        for tid, t in self.tasks.items():
            if not ID_RE.fullmatch(tid):
                add("task_ids_wellformed",
                    f"malformed id {tid!r} — expected something like T07")
            if t.status not in STATUSES:
                add("task_status_legal",
                    f"{tid}: illegal status {t.status!r} — one of "
                    f"{', '.join(STATUSES)}")

        for e in self.edges:
            # Checked before the id checks below, which are kind-agnostic and
            # so keep working on an edge whose kind is nonsense — one bad field
            # should not suppress every other finding about the same edge.
            if e.kind not in KINDS:
                add("task_edge_kind",
                    f"{e.src}: unknown edge kind {e.kind!r} — one of "
                    f"{', '.join(KINDS)}")
            if e.src not in ids:
                add("task_no_dangling_refs", f"edge from unknown task {e.src}")
                continue
            for t in e.to:
                if t not in ids:
                    add("task_no_dangling_refs", f"edge {e.src} -> unknown task {t}")
                if t == e.src:
                    add("task_no_dangling_refs", f"{e.src}: edge to itself")

        for tid, t in self.tasks.items():
            # There is no "carries a reason it should not" rule any more, and
            # its absence is the point of folding `why` into `stops`. That rule
            # existed because a live field outlived the status that made it
            # true; a stop describes a stoppage that *happened*, so no later
            # status can contradict it, and work stopped twice and restarted
            # twice is the ordinary case rather than drift. What is checked is
            # the other direction — a status claiming a reason that is not
            # there. Two names for one shape, because the remedies are two
            # different commands and `dgraph/testing.py` gives a project one
            # test per rule.
            if t.status == "PARKED" and not t.stops:
                add("task_park_complete",
                    f"{tid} is PARKED with nothing saying why — the reason it "
                    f"stopped has nowhere to live. `dg task park {tid} --why "
                    f"…`, or set the status back")
            if t.status == "DROPPED" and not t.stops:
                add("task_drop_complete",
                    f"{tid} is DROPPED with nothing saying why. Abandoning "
                    f"work is a judgement about it, and the reason is the part "
                    f"worth keeping: `dg task drop {tid} --why …`")
            for k in t.stops:
                if not k.why or not k.date:
                    add("task_park_complete",
                        f"{tid}: a stops entry is missing its "
                        f"{'why' if not k.why else 'date'}")
            # A reading with no note is the box-tick the whole design refuses:
            # the note is what a later reader has instead of the conversation,
            # and without it the entry says only that somebody ran a command.
            for r in t.readings:
                missing = next((f for f in ("date", "note", "against")
                                if not getattr(r, f)), None)
                if missing:
                    add("task_reading_complete",
                        f"{tid}: a readings entry is missing its {missing}")
                elif r.against != t.evidence_for:
                    # Kept rather than deleted when the link moves — it is an
                    # archived record — but said out loud, because a reading
                    # against a question this work no longer informs is inert
                    # and a reader could take it for cover it does not give.
                    add("task_reading_stale",
                        f"{tid} was read against {r.against} on {r.date}, but "
                        f"it is "
                        + (f"evidence for {t.evidence_for} now"
                           if t.evidence_for else "evidence for nothing now")
                        + f" — the record stands, but it covers nothing",
                        "warning")
            for c in t.completions:
                if not c.date or not c.outcome:
                    add("task_done_complete",
                        f"{tid}: a completions entry is missing its "
                        f"{'date' if not c.date else 'outcome'}")
            # There is no "carries completion data it should not" rule any
            # more, and its absence is the point of folding the pair into a
            # list. That rule existed because two live scalars outlived the
            # status that made them true, and a status change had to clear them
            # to stop them rotting. A completion describes work that *finished*,
            # so no later status can contradict it — work finished, restarted
            # and finished again is the ordinary case rather than drift, and it
            # is the case the old shape destroyed. What is checked instead is
            # the other direction: a status claiming a completion that is not
            # there, below, and an entry missing a half of itself, above.
            if t.status != "DONE":
                continue
            if not t.done:
                add("task_done_complete", f"{tid}: DONE without a date")
            if not t.outcome:
                add("task_done_complete",
                    f"{tid}: DONE without an outcome. Record what it produced "
                    f"— a path, a PR, a note.")
            for p in self.prerequisites(tid, adj):
                if self.tasks[p].unfinished:
                    add("task_done_before_prerequisite",
                        f"{tid} is DONE but {p} ({self.tasks[p].status}) has to "
                        f"come first — finish {p}, or the dependency is wrong")

        # What a park is holding up. The counterweight to parking being the
        # cheapest thing this store offers: a drop is interrogated about its
        # dependants at the moment it happens and chased afterwards by the two
        # rules below, while a park settles nothing and, without this, was
        # never brought up again. Somebody stuck reaches for the cheaper one
        # every time, including where the work is genuinely dead, and the
        # backlog fills with parked work silently holding its dependants.
        #
        # State-based, not time-based: `validate` has no clock and must not
        # grow one, or the same store would be valid twice and invalid at
        # midnight. The date is *reported* instead, so a reader can judge
        # staleness themselves — which is the judgement, not the rule.
        #
        # Silent where a park holds nothing up: that park is costing nobody
        # anything, and a warning about it would train the eye past the ones
        # that matter.
        for tid, t in self.tasks.items():
            if not t.parked:
                continue
            held = [w for w in self.unblocks(tid, adj)
                    if self.tasks[w].unfinished]
            if held:
                since = f" since {t.stops[-1].date}" if t.stops else ""
                add("parked_holding_work",
                    f"{tid} has been parked{since} and "
                    f"{', '.join(held)} still waits on it — pick it up, "
                    f"`dg task drop {tid} --why …` if it is not happening "
                    f"(which releases them), or `dg task undep` if the "
                    f"dependency was wrong",
                    "warning")

        # What a drop left behind. Both are warnings and both fire only over
        # work nobody has picked up — TODO and PARKED — which is the
        # acknowledgement path, and the reason there is no `dg task confirm` to
        # match the decision store's `dg confirm`. That one flips a *stored*
        # status; a task has none to flip, so acknowledging would need a new
        # field whose only job is to silence a check, and a stored field that
        # can go stale is exactly what this store refuses to keep. Starting the
        # work, dropping it, or removing the edge that is no longer true all
        # clear these, and each is a real statement about the work rather than a
        # box ticked.
        #
        # PARKED is included because otherwise the parked case is covered by
        # nothing at all. `fallout`'s released branch skips parked dependants
        # deliberately — a park has no startability to release — so a drop says
        # nothing about them, and if this gated on TODO alone nothing would say
        # it afterwards either: nothing in the CLI ever returns a task to TODO
        # (`dg task start` writes DOING), so a parked task can be picked back up
        # with its only prerequisite abandoned and no surface saying so.
        for tid, t in sorted(self.tasks.items()):
            if t.status not in ("TODO", "PARKED"):
                continue
            gone = self.dropped_prerequisites(tid, adj)
            if gone:
                # Two wordings, because "became startable" is false about work
                # that is not being done: `ready` requires TODO, so a parked
                # task was not released by the drop, it was undermined by it
                # while nobody was looking. The remedy is the same either way.
                add("released_by_drop",
                    (f"{tid} is parked behind {', '.join(gone)}, which was "
                     f"abandoned" if t.parked else
                     f"{tid} became startable only because "
                     f"{', '.join(gone)} was abandoned") +
                    f" — check the work is still possible without what it "
                    f"would have produced, then `dg task start {tid}` or "
                    f"`dg task undep {tid} --after {gone[0]}`; or drop it too",
                    "warning")
            # Not asked of work that carries its own justification: a `because`
            # is something *else* recording why this exists, which is the same
            # reason one surviving origin is not a silence either. Only the
            # field's presence is read — resolving it is `cross`'s job and this
            # module cannot see a decision.
            orphaned = self.abandoned_origins(tid)
            if orphaned and not t.because:
                add("orphaned_by_drop",
                    f"{tid} was discovered during {', '.join(orphaned)}, which "
                    f"was abandoned, and nothing else records why this work "
                    f"exists — start it, drop it, or say what it now stands on "
                    f"with `dg task link {tid} --because`",
                    "warning")

        # Each kind walked as its own subgraph, never as one union. Both
        # relations run forward in time, but not necessarily in the same
        # direction between the same pair: `T03 prompted T07` together with
        # `T07 precedes T03` is two true facts about a cleanup that has to land
        # before the work can be redone, and a union walk would call that a
        # cycle and force one of them to be deleted. The finding names the kind
        # for the same reason — with two subgraphs, "cycle: A -> B -> A" does
        # not say which relation is the cyclic one.
        #
        # Iterative, for the reason `model.validate` is: a deep but legal chain
        # must not crash the validator, since a crash is the fail-open the check
        # exists to prevent.
        for kind in KINDS:
            colour: dict[str, int] = {}
            for tid in self.tasks:
                if colour.get(tid):
                    continue
                colour[tid] = 1
                stack = [(tid, iter(self._out(tid, kind, adj)))]
                trail = [tid]
                while stack:
                    node, nxt = stack[-1]
                    for c in nxt:
                        if colour.get(c) == 1:
                            add("task_acyclic",
                                f"{kind} cycle: "
                                f"{' -> '.join(cycle_from(trail, c))}")
                            continue
                        if colour.get(c) == 2:
                            continue
                        colour[c] = 1
                        stack.append((c, iter(self._out(c, kind, adj))))
                        trail.append(c)
                        break
                    else:
                        colour[node] = 2
                        stack.pop()
                        trail.pop()

        return v
