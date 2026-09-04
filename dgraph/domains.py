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
    *complete*. Empty for most domains, and for `prose` always.

    `complete` is the domain's claim about **its own prefix**: over refs
    bound under that prefix, the artefact is the only source of dependence,
    so a pair absent here is a pair that does not hold. It says nothing
    about an endpoint's other binds. Whether an *edge* may be dropped on the
    strength of it is judged per edge by the core (`D83`): only where every
    bind on both endpoints is under a prefix whose relation was computed in
    this run and is complete, and the union of every relation's pairs has
    none for the edge. One bind under an incomplete, empty or unavailable
    prefix turns the finding into a report. So a domain never composes an
    `undep` itself — it reports what it sees and declares how far that
    sight reaches.
    """

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
        complete. Empty for most domains.

        `bindings` is **every bind on the graph**, under every prefix, not
        only this domain's (`D83`): a domain that knows files and constants
        both wants the lot, and reading a ref is not executing anything, so
        R4 does not object. What a domain answers for is still its own
        prefix — `Relation.complete` is coverage over that and no more —
        and closure and reduction run once, over the union of what every
        domain returned, in the core (`relations` below)."""


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
    if len(eps) > 1:
        # Two distributions, one prefix: the first in entry-point order would
        # silently shadow the other, and which is first is an accident of the
        # install. Refuse, naming both — a person uninstalls one (T70).
        names = ", ".join(sorted(ep.value for ep in eps))
        return Unavailable(prefix, f"`{prefix}` is claimed by {len(eps)} "
                                   f"installed distributions ({names}) — "
                                   f"uninstall all but one")
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
             timeout: float | None = None) -> dict[str, Result]:
    """Every item judged, grouped by domain and each domain called once.

    **R3, owned here.** Nothing this returns is written anywhere: a `Result`
    is what the door presents and, for `fired`, what it composes an op from
    — and the op goes to the tray under the same rules as one a person
    typed. No store is opened for writing on this path.

    Each domain has seconds per batch (proposal E5): the `deadline` it
    declares, else `DEADLINE`; `timeout`, when given, overrides every
    domain's — a person at the door outranks a distribution (`D85`). A
    domain runs in a forked child of its own (`_run`, `D86`); one that has
    not answered by then is ended and `unjudged` for everything it was
    asked, with the sentence saying so. A domain that raises is `unjudged`
    likewise — never an error, because a broken environment is not evidence
    about the thing the probe is about. Domains run one at a time, and the
    next starts only when the last is gone: two building under one `root`
    concurrently is worse than slow. `core.all_of` members are dispatched
    through the same batch and combined afterwards.
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


def seconds_for(dom: Domain, timeout: float | None = None) -> float:
    """How long `dom` gets for one batch: `timeout` if the door gave one,
    else what the domain declares as `deadline`, else `DEADLINE`. A declared
    value that is not a positive number is ignored rather than trusted —
    a domain's metadata is not the place to hang the door."""
    if timeout is not None:
        return timeout
    own = getattr(dom, "deadline", None)
    if isinstance(own, (int, float)) and not isinstance(own, bool) and own > 0:
        return float(own)
    return DEADLINE


#: Seconds a child may take to come up before the domain's own clock starts
#: — a fresh interpreter importing the domain (`_context`), not the batch.
STARTUP = 30.0


def _child(conn, dom: Domain, group: list[Item], root: Path, timeout: float) -> None:
    """The batch, in the child: *started* first, so the domain's deadline is
    counted from the moment it could run and not from the fork; then one
    message back — the result, or why not."""
    try:
        conn.send(("started", None))
        got = dom.evaluate(group, root, deadline=time.monotonic() + timeout)
        conn.send(("result", got))
    except BaseException as exc:          # never the door's problem
        try:
            conn.send(("error", f"{type(exc).__name__}: {exc}"))
        except BaseException:
            pass
    finally:
        conn.close()


def _context():
    """How the child is started. `fork` while the door is the only thread —
    `dg probe`, the commit gate — since it inherits the loaded domain and
    the items and pickles nothing. A parent that already has threads
    (`dg serve`, a pytest worker) may not fork safely: a lock a thread held
    at the fork is held forever in the child, and Python 3.12+ says so.
    There the child is started fresh (`forkserver`, else `spawn`) and the
    domain, the items and the root are pickled on the way in — so a domain
    is a class importable by name, and `Item.record` a plain record.
    """
    import multiprocessing as mp
    methods = mp.get_all_start_methods()
    if "fork" in methods and _threads() == 1:
        return mp.get_context("fork")
    return mp.get_context("forkserver" if "forkserver" in methods else "spawn")


def _threads() -> int:
    """How many threads this process has, counted the way the interpreter
    counts them before it warns about a fork: at the OS, where a thread a
    C extension or a test runner started shows up and `threading` does not
    see it. Elsewhere than Linux, `threading`'s count."""
    import threading
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("Threads:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return threading.active_count()


def _run(dom: Domain, group: list[Item], root: Path,
         timeout: float | None) -> dict[str, Result]:
    """One domain, one batch, one child process, one deadline.

    A forked child rather than a thread (`D86`): Python cannot end a thread,
    so a domain that ignored its deadline used to run on beside the next
    domain and past the door's exit — two building under one root, which is
    the case the deadline exists for. The operating system can end a process,
    and does, at the deadline: everything the domain did not answer is
    `unjudged`, nothing of it is left running, and the next domain starts
    only after. A domain that raises, exits, or answers something that is
    not a dict of `Result`s is `unjudged` likewise, with the reason.

    Under `fork` the child inherits the loaded domain and the items as they
    stand and nothing is pickled on the way in; only the result crosses
    back, so a domain cannot reach the door's state (R3, by construction).
    A threaded parent gets a fresh child instead (`_context`), which pickles
    the domain in. A domain's own heavy work belongs in a subprocess with a
    `timeout` derived from `deadline`, as the cookbook's `grep` does — then
    a cut-off is clean at every level.
    """
    timeout = seconds_for(dom, timeout)
    ctx = _context()
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, name=f"dg-domain-{dom.name}", daemon=True,
                       args=(child, dom, group, root, timeout))
    proc.start()
    child.close()
    message = None
    try:
        if parent.poll(STARTUP) and parent.recv()[0] == "started":
            if parent.poll(timeout):
                message = parent.recv()
    except (EOFError, OSError):
        message = None
    finally:
        parent.close()
    if proc.is_alive():
        proc.terminate()
    proc.join()
    if message is None:
        if proc.exitcode not in (0, None) and proc.exitcode < 0:
            why = f"`{dom.name}` did not answer within {timeout:g}s"
        else:
            why = f"`{dom.name}` exited without answering"
        return {it.id: Result("unjudged", why) for it in group}
    kind, payload = message
    if kind == "error":
        why = f"`{dom.name}` raised {payload}"
        return {it.id: Result("unjudged", why) for it in group}
    if not isinstance(payload, dict):
        why = f"`{dom.name}` returned {type(payload).__name__}, not a dict"
        return {it.id: Result("unjudged", why) for it in group}
    out = {}
    for it in group:
        r = payload.get(it.id)
        if isinstance(r, Result) and r.verdict in ("holds", "fired", "unjudged"):
            out[it.id] = r
        else:
            out[it.id] = Result("unjudged", f"`{dom.name}` returned no verdict")
    return out


# ---- findings ----------------------------------------------------------------


def kinds_of(kind: str, args: object) -> list[str]:
    """The kinds a criterion reaches, for whoever asks *which domains does
    this need*: the kind itself, or for `core.all_of` its well-formed
    members' — a composite is one criterion (`D85`) and every member is a
    probe under its own prefix. A `core.` kind that is not the composite
    reaches none. `Row.prefixes`, `unavailable` and `findings` all read
    this, so the door's `--domain` and its footer cannot disagree about
    what a composite is under (audit `J-F1`)."""
    if kind == ALL_OF:
        members = args.get("probes") if isinstance(args, dict) else None
        return [m["kind"] for m in (members or []) if isinstance(m, dict)
                and isinstance(m.get("kind"), str)
                and not kind_fault(m["kind"], "a probe's")]
    if kind.partition(".")[0] == CORE:
        return []
    return [kind]


def findings(items: list[Item], results: dict[str, Result]) -> list[Violation]:
    """The results as `Violation`s with origin `domain`, one per item.

    So that `dg brief`, the pytest opt-in and the web panel need no new
    phrasing (proposal §The door). `probe_fired` is the only error — the
    door's exit code is `dg check`'s, non-zero when anything fired; a
    holding probe and an unjudged one are both warnings, since neither is a
    problem with the store and `Violation` has two severities. A kind no
    domain claims is `domain_unavailable`, **once per prefix** and carrying
    the reason discovery gave: a missing `bench` on sixty probes is one line
    saying what this machine's verdict does not cover, not sixty (T71). Each
    of those items is still `probe_unjudged` on its own, so the count of
    what was not judged is unchanged and so is the exit code. An item whose
    every kind is under a missing prefix — a `core.all_of` of `bench.`
    members included — carries no sentence of its own, since the line above
    says why; one with a judged member beside a missing one keeps what the
    judged member said.
    """
    out = []
    missing: dict[str, tuple[str, list[str]]] = {}
    for it in items:
        r = results.get(it.id) or Result("unjudged", "no result")
        kinds = kinds_of(it.kind, it.args)
        gone = []
        for k in kinds:
            dom = domain_for(k)
            if isinstance(dom, Unavailable):
                gone.append((k.partition(".")[0], dom.why))
        for prefix, why in dict(gone).items():
            _, ids = missing.setdefault(prefix, (why, []))
            if it.id not in ids:
                ids.append(it.id)
        if gone and len(gone) == len(kinds):
            r = Result("unjudged", "")
        name = {"fired": "probe_fired", "holds": "probe_holds",
                "unjudged": "probe_unjudged"}[r.verdict]
        sev = "error" if r.verdict == "fired" else "warning"
        out.append(Violation(name, f"{it.id}: {it.kind} {r.verdict}"
                                   + (f" — {r.sentence}" if r.sentence else ""),
                             sev, DOMAIN))
    for prefix, (why, ids) in missing.items():
        out.insert(0, Violation("domain_unavailable",
                                f"`{prefix}.` — {len(ids)} probe(s) not judged "
                                f"on this machine ({_some(ids)}): {why}",
                                "warning", DOMAIN))
    return out


def _some(ids: list[str], n: int = 5) -> str:
    return ", ".join(ids[:n]) + (f", … {len(ids) - n} more" if len(ids) > n else "")


def unavailable(items: list[Item]) -> dict[str, tuple[str, list[str]]]:
    """`prefix -> (why, kinds)` for every kind among `items` that no installed
    domain claims — a composite's members included — what a door prints as
    one footer line per prefix."""
    out: dict[str, tuple[str, list[str]]] = {}
    for it in items:
        for k in kinds_of(it.kind, it.args):
            dom = domain_for(k)
            if isinstance(dom, Unavailable):
                out.setdefault(k.partition(".")[0], (dom.why, []))[1].append(k)
    return out


# ---- relations ---------------------------------------------------------------


def relations(bindings: list[Bind], root: Path) -> dict[str, Relation]:
    """Every installed domain's relation over **all** of `bindings`, keyed by
    prefix — the one caller of `Domain.relations` (`D83`).

    Each domain whose prefix appears among the binds is handed the whole
    list, never only its own; a prefix no domain claims, or a domain that
    raises or returns the wrong thing, is simply absent from the result, so
    an edge with a bind under it can never be judged fully covered. Nothing
    here walks the graph: closure and reduction over the union of these
    are the discovery door's, when it lands.
    """
    out: dict[str, Relation] = {}
    for prefix in sorted({b.kind.partition(".")[0] for b in bindings}):
        if prefix == CORE:
            continue
        dom = domain_for(f"{prefix}.x")
        if isinstance(dom, Unavailable):
            continue
        try:
            rel = dom.relations(list(bindings), root)
        except Exception:                # a broken domain is not evidence
            continue
        if isinstance(rel, Relation):
            out[prefix] = rel
    return out
