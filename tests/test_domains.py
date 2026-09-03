"""`dgraph/domains.py`: the Domain protocol, discovery, the prose domain,
`PROBES`, the batched evaluator with a deadline, and `core.all_of` (T53).

R1–R4 are owned by docstrings in the module; what this file proves is the
behaviour each rule requires: nothing here opens a store, a kind nobody
claims is a warning and never an import error, a domain that hangs or
raises is `unjudged`, and the finding names stay out of `check.CHECKS`.
"""

import inspect
import time
from pathlib import Path

import pytest

from dgraph import check, domains
from dgraph.domains import (ALL_OF, PROBES, PROSE, Domain, Item, Relation,
                            Result, Unavailable)
from dgraph.violation import DOMAIN, ORIGINS

ROOT = Path(".")


@pytest.fixture(autouse=True)
def _fresh_registry():
    domains.forget()
    yield
    domains.forget()


# ---- the lists --------------------------------------------------------------


def test_probes_and_checks_are_disjoint_and_the_origin_exists():
    """R2 as a test: `dg check` output is a function of the store, so no
    finding a domain emits may be a name the plugin parametrises over."""
    assert not set(PROBES) & set(check.CHECKS)
    assert not any(name in check.ORIGIN for name in PROBES)
    assert DOMAIN in ORIGINS


@pytest.mark.parametrize("name", PROBES)
def test_every_declared_probe_finding_is_emitted(name):
    """`CHECKS`' guard, for this list on its own terms: a name nothing emits
    is dead."""
    assert f'"{name}"' in inspect.getsource(domains), (
        f"{name} is declared in PROBES but never emitted")


def test_the_shape_checks_are_in_checks_with_their_store():
    for name, origin in (("probe_wellformed", "decision"),
                         ("binding_wellformed", "decision"),
                         ("task_probe_wellformed", "task"),
                         ("task_binding_wellformed", "task")):
        assert name in check.CHECKS and check.ORIGIN[name] == origin


def test_the_module_opens_no_store():
    """R3: an evaluator produces results the door turns into ops; nothing on
    this path writes, and nothing on it even imports a store."""
    src = inspect.getsource(domains)
    assert "Graph.load" not in src and "TaskGraph" not in src
    assert ".save(" not in src and "stage" not in src


# ---- the base case --------------------------------------------------------


def test_prose_is_a_domain_and_never_evaluates():
    assert isinstance(PROSE, Domain)
    items = [Item("D01", "prose.rule", {"text": "the corpus changes"}, None),
             Item("T01", "prose.done", {}, None, "task")]
    got = domains.evaluate(items, ROOT)
    assert got["D01"] == Result("unjudged", "the corpus changes")
    assert got["T01"].verdict == "unjudged" and "presented" in got["T01"].sentence
    assert PROSE.relations([], ROOT) == Relation()
    assert PROSE.compose("prose.rule", None, ROOT) == ({}, "")


def test_prose_is_known_without_an_entry_point(monkeypatch):
    monkeypatch.setattr(domains.metadata, "entry_points", lambda **kw: [])
    assert domains.domain_for("prose.anything") is PROSE


# ---- discovery ---------------------------------------------------------------


class _EP:
    def __init__(self, name, value, obj=None, boom=None):
        self.name, self.value, self._obj, self._boom = name, value, obj, boom

    def load(self):
        if self._boom:
            raise self._boom
        return self._obj


class Fake:
    name = "fake"
    kinds = frozenset({"fake.ok"})

    def __init__(self, verdict="holds", sleep=0.0, boom=None, answer=True):
        self.v, self.sleep, self.boom, self.answer = verdict, sleep, boom, answer
        self.calls = 0

    def compose(self, kind, record, root):
        return {}, ""

    def evaluate(self, items, root, *, deadline):
        self.calls += 1
        if self.boom:
            raise self.boom
        time.sleep(self.sleep)
        if not self.answer:
            return {}
        return {it.id: Result(self.v, f"{it.id} judged") for it in items}

    def relations(self, bindings, root):
        return Relation()


def _install(monkeypatch, *eps):
    monkeypatch.setattr(domains.metadata, "entry_points",
                        lambda **kw: list(eps) if kw.get("group") == domains.GROUP else [])


def test_a_prefix_nobody_claims_is_unavailable_not_an_error(monkeypatch):
    _install(monkeypatch)
    got = domains.domain_for("rocq.typechecks")
    assert isinstance(got, Unavailable) and "rocq." in got.why
    items = [Item("D01", "rocq.typechecks", {}, None)]
    res = domains.evaluate(items, ROOT)
    assert res["D01"].verdict == "unjudged"
    f = domains.findings(items, res)
    assert [(v.check, v.severity, v.origin) for v in f] == [
        ("domain_unavailable", "warning", DOMAIN)]


def test_a_registered_domain_is_loaded_lazily_by_prefix(monkeypatch):
    fake = Fake()
    loads = []
    other = _EP("other", "x:y", boom=RuntimeError("never loaded"))
    ep = _EP("fake", "fake:FAKE", fake)
    ep.load = lambda: (loads.append("fake"), fake)[1]
    _install(monkeypatch, other, ep)
    assert domains.domain_for("fake.ok") is fake
    assert domains.domain_for("fake.other") is fake and loads == ["fake"]
    assert domains.claims(fake, "fake.ok") and not domains.claims(fake, "fake.other")


def test_a_domain_that_fails_to_load_or_is_not_one_is_unavailable(monkeypatch):
    _install(monkeypatch, _EP("bad", "bad:X", boom=ImportError("no such")),
             _install and _EP("notadomain", "n:o", object()))
    bad = domains.domain_for("bad.x")
    assert isinstance(bad, Unavailable) and "ImportError" in bad.why
    nd = domains.domain_for("notadomain.x")
    assert isinstance(nd, Unavailable) and "not a Domain" in nd.why


def test_a_malformed_kind_is_unavailable_with_the_shape_fault():
    got = domains.domain_for("nodot")
    assert isinstance(got, Unavailable) and "does not name a domain" in got.why


# ---- the evaluator -------------------------------------------------------


def test_one_call_per_domain_with_every_item_of_its_prefix(monkeypatch):
    fake = Fake("fired")
    _install(monkeypatch, _EP("fake", "f:F", fake))
    items = [Item("D01", "fake.ok", {}, None), Item("D02", "fake.ok", {}, None),
             Item("D03", "prose.rule", {}, None)]
    res = domains.evaluate(items, ROOT)
    assert fake.calls == 1
    assert res["D01"].verdict == res["D02"].verdict == "fired"
    assert res["D03"].verdict == "unjudged"
    f = domains.findings(items, res)
    assert [v.check for v in f] == ["probe_fired", "probe_fired", "probe_unjudged"]
    assert f[0].blocking and not f[2].blocking and f[0].origin == DOMAIN


def test_a_domain_that_overruns_is_unjudged_for_everything(monkeypatch):
    fake = Fake(sleep=2.0)
    _install(monkeypatch, _EP("fake", "f:F", fake))
    items = [Item("D01", "fake.ok", {}, None)]
    t0 = time.monotonic()
    res = domains.evaluate(items, ROOT, timeout=0.05)
    assert time.monotonic() - t0 < 1.0
    assert res["D01"].verdict == "unjudged" and "did not answer" in res["D01"].sentence


def test_a_domain_that_raises_or_answers_badly_is_unjudged(monkeypatch):
    fake = Fake(boom=ValueError("toolchain missing"))
    _install(monkeypatch, _EP("fake", "f:F", fake))
    res = domains.evaluate([Item("D01", "fake.ok", {}, None)], ROOT)
    assert res["D01"].verdict == "unjudged" and "ValueError" in res["D01"].sentence
    domains.forget()
    fake = Fake(answer=False)
    _install(monkeypatch, _EP("fake", "f:F", fake))
    res = domains.evaluate([Item("D01", "fake.ok", {}, None)], ROOT)
    assert res["D01"].verdict == "unjudged" and "no verdict" in res["D01"].sentence


def test_a_holding_probe_is_a_warning_not_an_error(monkeypatch):
    fake = Fake("holds")
    _install(monkeypatch, _EP("fake", "f:F", fake))
    items = [Item("D01", "fake.ok", {}, None)]
    f = domains.findings(items, domains.evaluate(items, ROOT))
    assert f[0].check == "probe_holds" and not f[0].blocking


# ---- core.all_of --------------------------------------------------------------


def test_all_of_fires_if_any_member_fires_and_holds_only_if_all_hold(monkeypatch):
    fake = Fake("holds")
    _install(monkeypatch, _EP("fake", "f:F", fake))
    both = Item("D01", ALL_OF, {"probes": [{"kind": "fake.ok", "args": {}},
                                           {"kind": "fake.ok", "args": {}}]}, None)
    assert domains.evaluate([both], ROOT)["D01"].verdict == "holds"
    domains.forget(); _install(monkeypatch, _EP("fake", "f:F", Fake("fired")))
    assert domains.evaluate([both], ROOT)["D01"].verdict == "fired"
    mixed = Item("D02", ALL_OF, {"probes": [{"kind": "fake.ok", "args": {}},
                                            {"kind": "prose.rule", "args": {}}]},
                 None)
    domains.forget(); _install(monkeypatch, _EP("fake", "f:F", Fake("holds")))
    res = domains.evaluate([mixed], ROOT)
    assert res["D02"].verdict == "unjudged"          # prose is never judged
    assert "presented" in res["D02"].sentence


def test_all_of_with_no_members_or_a_bad_member_is_unjudged():
    empty = Item("D01", ALL_OF, {"probes": []}, None)
    assert domains.evaluate([empty], ROOT)["D01"].verdict == "unjudged"
    bad = Item("D02", ALL_OF, {"probes": [{"kind": "nodot"}]}, None)
    assert domains.evaluate([bad], ROOT)["D02"].verdict == "unjudged"
    other = Item("D03", "core.any_of", {}, None)
    res = domains.evaluate([other], ROOT)
    assert res["D03"].verdict == "unjudged" and ALL_OF in res["D03"].sentence
