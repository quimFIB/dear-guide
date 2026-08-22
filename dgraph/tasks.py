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
  would only create something that can go stale. `stale_block` has no analogue
  here; it cannot occur.
- **Supersession, in one place only.** A decision that is overturned keeps its
  old answer forever, because how a project changed its mind is worth more than
  the conclusion. Here that applies to one question — *why is this work not
  being done* — because it is the one whose answer can stop being true and
  still be worth having. Every stoppage appends to `Task.stops` and nothing
  ever clears it; work put down three times says so. Every other field this
  store keeps is current-state and is cleared when the state it describes
  stops holding.
- **Two ways to stop, and they differ downstream, not in the record.** `PARKED`
  is work nobody is doing; `DROPPED` is work nobody is going to do. Both write
  the same `Stop`. What separates them is whether the work that waited on this
  is now free to proceed — abandoning says yes and releases it, parking says no
  and holds it. A store with one stopping status has to answer that question
  the same way twice, and neither answer is right for both cases.
- **No falsifier, no answer.** A task is finished when it is done, and what it
  produced is an `outcome` — a path, a PR, a note. That is a record, not a
  claim about the world, so nothing can falsify it.
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
    """

    why: str
    date: str


@dataclass
class Task:
    id: str
    title: str
    area: str
    status: str = "TODO"
    note: str | None = None      # prose: what this involves, why it is parked
    done: str | None = None      # ISO date, required once DONE
    outcome: str | None = None   # what the work produced, required once DONE
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
    #: The decision this work exists because of — a `D`-id in the *other*
    #: store. Held here and nowhere else: `decisions.json` never names a task,
    #: so a change to it always means a decision changed. Nothing in this
    #: module resolves it; that is `dgraph/cross.py`'s job, and this module
    #: cannot even see the decision store.
    because: str | None = None
    #: The decision this work will *bear on* — the spike whose result feeds a
    #: question, or the chore that turned out to raise one. Opposite polarity
    #: to `because`: that one makes the task wait on the decision, this one
    #: makes the decision wait on the task. Deliberately not called `settles`:
    #: a task produces evidence, a person decides.
    evidence_for: str | None = None

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
    def stopped_because(self) -> str | None:
        """The live reason, or None where the status makes no such claim.

        Derived rather than stored, which is what retires the whole class of
        violation the old `why` field needed: prose that outlived its status
        cannot exist if the status is what decides whether prose is live.
        """
        if self.status not in ("PARKED", "DROPPED") or not self.stops:
            return None
        return self.stops[-1].why


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
            and t.because == op.get("because")
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
        order = {a: i for i, a in enumerate(self.areas)}
        rows = sorted(self.tasks.values(), key=lambda t: (order.get(t.area, 99), t.id))
        return {
            "areas": self.areas,
            "tasks": [
                {
                    k: val
                    for k, val in (
                        ("id", t.id), ("title", t.title), ("area", t.area),
                        ("status", t.status), ("note", t.note),
                        ("done", t.done), ("outcome", t.outcome),
                        # Absent rather than `[]` when there is none, matching
                        # every other field here: the store stays readable, and
                        # work that never stopped says so by silence.
                        ("stops", [{"why": k.why, "date": k.date}
                                   for k in t.stops] or None),
                        ("format", t.format), ("because", t.because),
                        ("evidence_for", t.evidence_for),
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

    def _out(self, tid: str, kind: str) -> list[str]:
        """The heads of every `kind` edge leaving `tid`.

        Unions every matching edge rather than demanding one, because a task
        edge carries no payload beyond its kind: two `precedes` edges out of
        `T01` are two sets of successors and mean exactly their union. The
        decision graph needs `one_active_edge` because two answers to one
        question is a contradiction; here there is nothing to contradict. The
        grouping is by `(src, kind)` and not by `src` — the kinds are separate
        relations, and unioning across them is what would lose the distinction.
        """
        return sorted({t for e in self.edges
                       if e.src == tid and e.kind == kind
                       for t in e.to if t in self.tasks})

    def _in(self, tid: str, kind: str) -> list[str]:
        """The tails of every `kind` edge arriving at `tid`.

        Skips a source naming no task, exactly as `Graph.depends` does: a
        traversal helper that trusts ids crashes the validator that was about to
        report the dangling reference.
        """
        return sorted({e.src for e in self.edges
                       if e.kind == kind and tid in e.to and e.src in self.tasks})

    def unblocks(self, tid: str) -> list[str]:
        """The tasks this one is a prerequisite for."""
        return self._out(tid, "precedes")

    def prerequisites(self, tid: str) -> list[str]:
        """Derived, never stored: what must be resolved before this can start."""
        return self._in(tid, "precedes")

    def prompted(self, tid: str) -> list[str]:
        """The work that doing this one turned up. Provenance, not dependency.

        Deliberately absent from `waiting_on` and `ready`, which is the whole
        reason it is a separate kind: a chore noticed while doing `tid` is
        usually startable at once, and frequently has to land *before* `tid`
        can be finished. Treating it as an ordering would assert the opposite.

        What it is for is the question the edge list cannot otherwise answer —
        when `tid` is abandoned, which work existed only because of it.
        """
        return self._out(tid, "prompted")

    def discovered_during(self, tid: str) -> list[str]:
        """The work whose doing turned this one up. The reverse of `prompted`."""
        return self._in(tid, "prompted")

    # ---- what abandoning work leaves behind ------------------------------
    #
    # `RESOLVED` covers DONE and DROPPED alike, so `waiting_on` cannot tell a
    # prerequisite that finished from one that was given up on. That is the
    # right default — the alternative deadlocks everything downstream of any
    # abandoned task forever — but it makes the release silent, and these two
    # are what `validate` asks in order to break the silence.

    def dropped_prerequisites(self, tid: str) -> list[str]:
        """The prerequisites of `tid` that were abandoned rather than finished.

        A released dependant is not always a startable one: a prerequisite that
        *produced* something this work consumes does not release it, it
        undermines it. The store cannot tell those apart — which of the two an
        edge was usually only becomes clear when you try to proceed without it
        — so it reports the fact and leaves the judgement where the knowledge is.
        """
        return [p for p in self.prerequisites(tid)
                if self.tasks[p].status == "DROPPED"]

    def abandoned_origins(self, tid: str) -> list[str]:
        """The origins of `tid`, but only when *every* one was abandoned.

        Partial abandonment is not a silence worth breaking: one surviving
        origin still explains why the work exists. Empty for work with no
        origin recorded, which is the ordinary case for anything planned.
        """
        origins = self.discovered_during(tid)
        return origins if origins and all(
            self.tasks[o].status == "DROPPED" for o in origins) else []

    def waiting_on(self, tid: str) -> list[str]:
        """The prerequisites that are not resolved yet."""
        return [p for p in self.prerequisites(tid) if not self.tasks[p].resolved]

    def ready(self, tid: str) -> bool:
        """Startable now: not yet begun, and nothing outstanding before it."""
        return self.tasks[tid].status == "TODO" and not self.waiting_on(tid)

    def blocked(self, tid: str) -> bool:
        return self.tasks[tid].unfinished and bool(self.waiting_on(tid))

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

        for tid, t in self.tasks.items():
            if not ID_RE.fullmatch(tid):
                add("task_ids_wellformed",
                    f"malformed id {tid!r} — expected something like T07")
            if t.area not in self.areas:
                add("task_area_known", f"{tid}: unknown area {t.area!r}")
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
            if t.status != "DONE":
                # Applying a status change clears these, so this catches a
                # hand-edit: an outcome under unfinished work is a claim the
                # store cannot support, and the view prints it as if it could.
                stale = [f for f in ("done", "outcome") if getattr(t, f)]
                if stale:
                    add("task_done_complete",
                        f"{tid} is {t.status} but still carries "
                        f"{' and '.join(stale)} — completion data from an "
                        f"earlier DONE; clear it, or set the status back")
                continue
            if not t.done:
                add("task_done_complete", f"{tid}: DONE without a date")
            if not t.outcome:
                add("task_done_complete",
                    f"{tid}: DONE without an outcome. Record what it produced "
                    f"— a path, a PR, a note.")
            for p in self.prerequisites(tid):
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
            held = [w for w in self.unblocks(tid) if self.tasks[w].unfinished]
            if held:
                since = f" since {t.stops[-1].date}" if t.stops else ""
                add("parked_holding_work",
                    f"{tid} has been parked{since} and "
                    f"{', '.join(held)} still waits on it — pick it up, "
                    f"`dg task drop {tid} --why …` if it is not happening "
                    f"(which releases them), or `dg task undep` if the "
                    f"dependency was wrong",
                    "warning")

        # What a drop left behind. Both are warnings and both fire only while
        # the work is still TODO, which is the acknowledgement path — and the
        # reason there is no `dg task confirm` to match the decision store's
        # `dg confirm`. That one flips a *stored* status; a task has none to
        # flip, so acknowledging would need a new field whose only job is to
        # silence a check, and a stored field that can go stale is exactly what
        # this store refuses to keep. Starting the work, dropping it, or
        # removing the edge that is no longer true all clear these, and each is
        # a real statement about the work rather than a box ticked.
        for tid, t in sorted(self.tasks.items()):
            if t.status != "TODO":
                continue
            gone = self.dropped_prerequisites(tid)
            if gone:
                add("released_by_drop",
                    f"{tid} became startable only because "
                    f"{', '.join(gone)} was abandoned — check the work is "
                    f"still possible without what it would have produced, then "
                    f"`dg task start {tid}` or `dg task undep {tid} --after "
                    f"{gone[0]}`; or drop it too",
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
                stack = [(tid, iter(self._out(tid, kind)))]
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
                        stack.append((c, iter(self._out(c, kind))))
                        trail.append(c)
                        break
                    else:
                        colour[node] = 2
                        stack.pop()
                        trail.pop()

        return v
