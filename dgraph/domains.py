"""Domains: the installed code a probe's `kind` selects, and the base case.

A probe is `{"kind": "<domain>.<name>", "args": {...}}` (`dgraph/probe.py`
has the shape). The core reads the prefix and no further: a **domain** is a
Python distribution registered under the `dgraph.domains` entry-point group
— the mechanism this package already uses for `pytest11` — under the prefix
it claims, discovered lazily when a probe with that prefix is evaluated.
What a domain is to the core is the `Domain` protocol below, and nothing
else: the core never imports one by name.

Four rules hold here, and each is owned by the docstring that enforces it:

- **R1** the store never grows a domain-private field — `dgraph/probe.py`;
- **R2** `check.run` reads stores and never the world — `PROBES`;
- **R3** an evaluator never writes a store; it produces ops — `evaluate`;
- **R4** the core never executes anything named in a store — `Domain`.

**The built-in domain is `prose`, and it never evaluates.** Its answer to
every probe is `unjudged` with the presentation handed back — the falsifier
beside the world as the reader knows it — because that is the current use
case and the base case rather than a fallback: a mechanical domain adds a
verdict on top of what a person reads, it does not replace it. No other
domain is planned; every Rocq example in the proposal is an example.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from dgraph.probe import Bind, kind_fault
from dgraph.violation import DOMAIN, Violation

#: The entry-point group a domain registers under, keyed by the prefix it
#: claims. `pyproject.toml` registers `prose` here as the example to copy.
GROUP = "dgraph.domains"

#: The prefix the core evaluates itself: `core.all_of` (proposal S1). Not a
#: domain — nothing is discovered for it — and the only composition the core
#: knows; a domain that wants `any_of` writes its own kind.
CORE = "core"
ALL_OF = "core.all_of"

#: Every finding `dg probe` can emit, and `check.run` never does. Its own
#: tuple with its own origin (`violation.DOMAIN`), kept apart from
#: `check.CHECKS` on purpose (proposal C6): those names are parametrised over
#: by the pytest plugin at every run, under the commit gate's budget, on
#: whatever machine the hook is on — and a finding that depends on what that
#: machine has installed would make `dg check` say different things about
#: one store on two machines, which is the property R2 exists to refuse. The
#: opt-in parametrises over this list the same way, so *a name nothing emits*
#: is caught for each list on its own terms (`tests/test_domains.py`).
PROBES: tuple[str, ...] = (
    # a kind no installed domain claims: a warning, never an import error —
    # a plain install must present a Rocq project's graph (R1)
    "domain_unavailable",
    # the three verdicts. `probe_fired` is the one that produces an op.
    "probe_fired",
    "probe_holds",
    # not `fired`: the domain could not run, timed out, or is `prose`. A
    # broken environment is not evidence about a lemma.
    "probe_unjudged",
)

Verdict = Literal["holds", "fired", "unjudged"]

#: Seconds a domain has for one batch before everything it has not answered
#: is `unjudged`. `--timeout` on the door overrides it (proposal E5).
DEADLINE = 60.0


@dataclass(frozen=True)
class Item:
    """One pre-commitment to judge: which record, which criterion, and the
    record itself for the domain to read. `slot` says where the probe sits —
    `edge` (a falsifier's twin), `task` (a definition of done) or `vertex`
    (a rule for settling) — because the act a verdict produces differs."""

    id: str
    kind: str
    args: dict
    record: object
    slot: str = "edge"


@dataclass
class Result:
    """What a domain said about one item.

    `sentence` is what a person reads — for `prose`, the presentation itself.
    `payload` is for `decide`: the fields a `close` would carry, where a
    domain can supply them. Neither is a store field. The sentence
    **must pass** `limits.refuse_verbose` on the day a `--why` is composed from it
    (R4, as reconciled: a domain's sentence cannot carry a file into a
    commit) — nothing composes one yet, the door only presents, and
    `tests/test_probe_door.py` holds this sentence to saying so rather than
    claiming a bound no code applies (audit `N-F6`).
    """

    verdict: Verdict
    sentence: str = ""
    payload: dict | None = None


@dataclass
class Relation:
    """`(uses, used)` pairs over bound refs, and whether the relation is
    *complete* — the artefact is the only source of that dependence, so a
    pair absent here is a pair that does not hold. Empty for most domains,
    and for `prose` always."""

    pairs: list[tuple[Bind, Bind]] = field(default_factory=list)
    complete: bool = False


@runtime_checkable
class Domain(Protocol):
    """What a domain is to the core.

    **R4, owned here.** A `kind` string selects a domain that was installed
    on this machine by somebody with the authority to install software; the
    store carries `args` that domain interprets, and never a command the
    core runs. The core cannot enforce what an installed domain does with
    `args`, and does not pretend to: what it asks is that a domain read
    under `root` and nowhere else, and treat a path in `args` that resolves
    outside it as `unjudged` (`Path.resolve()` against `root`, and the
    built-in example obeys it). A sentence a domain returns is bounded
    before it reaches a `--why`.
    """

    name: str                       # the prefix it claims
    kinds: frozenset[str]           # for the shape checks; empty = any name

    def compose(self, kind: str, record: object, root: Path) -> tuple[dict, str]:
        """When the pre-commitment is written: the args now, and the prose
        twin they mean — so the two are born agreeing."""

    def evaluate(self, items: list[Item], root: Path, *,
                 deadline: float) -> dict[str, Result]:
        """Every pre-commitment this domain claims in this run, judged
        together. A domain that has nothing to share across records loops
        inside; one that does — a build, a test run — pays for it once. An
        id absent from the result is `unjudged`. Returns by `deadline` (a
        `time.monotonic()` value) or is treated as unjudged for everything it
        did not answer; never raises — the caller turns an exception into
        unjudged, and says so."""

    def relations(self, bindings: list[Bind], root: Path) -> Relation:
        """`(uses, used)` pairs over bound refs, and whether the relation is
        complete. Empty for most domains."""


# ---- the base case --------------------------------------------------------


class Prose:
    """The domain that never evaluates.

    Every probe under `prose.` is `unjudged`, and the sentence is the
    presentation: the criterion's own prose, read beside whatever the door
    puts next to it. The base case rather than a fallback — see the module
    docstring — and the example a domain author copies for the shape of the
    three methods.
    """

    name = "prose"
    kinds: frozenset[str] = frozenset()

    def compose(self, kind: str, record: object, root: Path) -> tuple[dict, str]:
        return {}, ""

    def evaluate(self, items: list[Item], root: Path, *,
                 deadline: float) -> dict[str, Result]:
        return {it.id: Result("unjudged", _present(it)) for it in items}

    def relations(self, bindings: list[Bind], root: Path) -> Relation:
        return Relation()


def _present(it: Item) -> str:
    """What a person reads for a prose probe: its args' `text`, if the writer
    put the criterion there, else the kind — the door adds the twin."""
    text = it.args.get("text") if isinstance(it.args, dict) else None
    return str(text) if text else f"{it.kind} — presented, not judged"


PROSE = Prose()


# ---- discovery -----------------------------------------------------------


@dataclass
class Unavailable:
    """Why no domain answers for a prefix: the finding `domain_unavailable`
    carries, one per prefix and never an import error."""

    prefix: str
    why: str


_registry: dict[str, Domain | Unavailable] = {}


def domain_for(kind: str) -> Domain | Unavailable:
    """The domain that claims `kind`'s prefix, loaded on first use.

    Lazily, and by prefix rather than by scanning every entry point at
    start-up: discovery is environment-dependent, so it runs only when a
    probe actually asks for a domain, and only for the prefix it asks for.
    `prose` is known without any entry point at all — a plain install has
    no distribution metadata to consult and must still present. The answer
    is cached for the process, including the negative one.
    """
    fault = kind_fault(kind, "a probe's")
    if fault:
        return Unavailable(kind, fault)
    prefix = kind.partition(".")[0]
    if prefix in _registry:
        return _registry[prefix]
    found: Domain | Unavailable
    if prefix == PROSE.name:
        found = PROSE
    else:
        found = _load(prefix)
    _registry[prefix] = found
    return found


def _load(prefix: str) -> Domain | Unavailable:
    eps = [ep for ep in metadata.entry_points(group=GROUP) if ep.name == prefix]
    if not eps:
        return Unavailable(prefix, f"no installed domain claims `{prefix}.` — "
                                   f"nothing registered under `{GROUP}`")
    try:
        obj = eps[0].load()
    except Exception as exc:          # a broken install is not evidence
        return Unavailable(prefix, f"`{prefix}` is registered by "
                                   f"{eps[0].value} but failed to load: "
                                   f"{type(exc).__name__}: {exc}")
    if not isinstance(obj, Domain) or getattr(obj, "name", None) != prefix:
        return Unavailable(prefix, f"`{prefix}` is registered by {eps[0].value}"
                                   f" but is not a Domain claiming that prefix")
    return obj


def forget() -> None:
    """Drop the cache. For tests that install a domain mid-process."""
    _registry.clear()


def claims(domain: Domain, kind: str) -> bool:
    """Whether `domain` accepts `kind`: its prefix, and its `kinds` if it
    names any."""
    prefix, _, name = kind.partition(".")
    return prefix == domain.name and (not domain.kinds or kind in domain.kinds)


# ---- the evaluator ---------------------------------------------------------


def evaluate(items: list[Item], root: Path, *,
             timeout: float = DEADLINE) -> dict[str, Result]:
    """Every item judged, grouped by domain and each domain called once.

    **R3, owned here.** Nothing this returns is written anywhere: a `Result`
    is what the door presents and, for `fired`, what it composes an op from
    — and the op goes to the tray under the same rules as one a person
    typed. No store is opened for writing on this path.

    `timeout` is seconds per domain (proposal E5). A domain runs on its own
    thread; one that has not returned by then is `unjudged` for everything
    it was asked, with the sentence saying so, and the door moves on. A
    domain that raises is `unjudged` likewise — never an error, because a
    broken environment is not evidence about the thing the probe is about.
    `core.all_of` members are dispatched through the same batch and combined
    afterwards.
    """
    out: dict[str, Result] = {}
    flat: list[Item] = []
    composite: dict[str, list[str]] = {}
    for it in items:
        if it.kind == ALL_OF:
            members = it.args.get("probes") if isinstance(it.args, dict) else None
            if not isinstance(members, list) or not members:
                out[it.id] = Result("unjudged", "core.all_of names no probes")
                continue
            ids = []
            for i, m in enumerate(members):
                mid = f"{it.id}#{i}"
                if not isinstance(m, dict) or kind_fault(m.get("kind"), "a probe's"):
                    out[mid] = Result("unjudged", "malformed member probe")
                else:
                    flat.append(Item(mid, m["kind"], m.get("args") or {},
                                     it.record, it.slot))
                ids.append(mid)
            composite[it.id] = ids
        elif it.kind.partition(".")[0] == CORE:
            out[it.id] = Result("unjudged", f"{it.kind} is not a kind the core "
                                            f"evaluates — only {ALL_OF} is")
        else:
            flat.append(it)

    by_prefix: dict[str, list[Item]] = {}
    for it in flat:
        by_prefix.setdefault(it.kind.partition(".")[0], []).append(it)
    for prefix, group in by_prefix.items():
        dom = domain_for(group[0].kind)
        if isinstance(dom, Unavailable):
            for it in group:
                out[it.id] = Result("unjudged", dom.why)
            continue
        out.update(_run(dom, group, root, timeout))

    for cid, ids in composite.items():
        verdicts = [out[i].verdict for i in ids]
        sentences = [out[i].sentence for i in ids if out[i].sentence]
        verdict: Verdict = ("fired" if "fired" in verdicts
                            else "holds" if all(v == "holds" for v in verdicts)
                            else "unjudged")
        out[cid] = Result(verdict, "; ".join(sentences))
    return out


def _run(dom: Domain, group: list[Item], root: Path,
         timeout: float) -> dict[str, Result]:
    """One domain, one batch, one thread, one deadline."""
    box: dict[str, object] = {}

    def go() -> None:
        try:
            box["result"] = dom.evaluate(group, root,
                                         deadline=time.monotonic() + timeout)
        except BaseException as exc:      # never the door's problem
            box["error"] = exc

    t = threading.Thread(target=go, name=f"dg-domain-{dom.name}", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        why = f"`{dom.name}` did not answer within {timeout:g}s"
        return {it.id: Result("unjudged", why) for it in group}
    if "error" in box:
        exc = box["error"]
        why = f"`{dom.name}` raised {type(exc).__name__}: {exc}"
        return {it.id: Result("unjudged", why) for it in group}
    got = box.get("result")
    if not isinstance(got, dict):
        why = f"`{dom.name}` returned {type(got).__name__}, not a dict"
        return {it.id: Result("unjudged", why) for it in group}
    out = {}
    for it in group:
        r = got.get(it.id)
        if isinstance(r, Result) and r.verdict in ("holds", "fired", "unjudged"):
            out[it.id] = r
        else:
            out[it.id] = Result("unjudged", f"`{dom.name}` returned no verdict")
    return out


# ---- findings ----------------------------------------------------------------


def findings(items: list[Item], results: dict[str, Result]) -> list[Violation]:
    """The results as `Violation`s with origin `domain`, one per item.

    So that `dg brief`, the pytest opt-in and the web panel need no new
    phrasing (proposal §The door). `probe_fired` is the only error — the
    door's exit code is `dg check`'s, non-zero when anything fired; a
    holding probe and an unjudged one are both warnings, since neither is a
    problem with the store and `Violation` has two severities. A kind no
    domain claims is `domain_unavailable`, once per item, and carries the
    reason discovery gave.
    """
    out = []
    for it in items:
        r = results.get(it.id) or Result("unjudged", "no result")
        dom = domain_for(it.kind) if it.kind.partition(".")[0] != CORE else None
        if isinstance(dom, Unavailable):
            out.append(Violation("domain_unavailable",
                                 f"{it.id}: {dom.why}", "warning", DOMAIN))
            continue
        name = {"fired": "probe_fired", "holds": "probe_holds",
                "unjudged": "probe_unjudged"}[r.verdict]
        sev = "error" if r.verdict == "fired" else "warning"
        out.append(Violation(name, f"{it.id}: {it.kind} {r.verdict}"
                                   + (f" — {r.sentence}" if r.sentence else ""),
                             sev, DOMAIN))
    return out
