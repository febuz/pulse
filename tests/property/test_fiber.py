"""Property tests for the synaptic fiber taxonomy."""

from __future__ import annotations

import pytest

from knitweb.synaptic.bytecode import Relation
from knitweb.synaptic.fiber import (
    Fiber,
    FIBER_PREDICATE,
    DOMAIN_PREDICATE,
    normalize_fiber,
    normalize_domain,
    fiber_relations,
    FiberMeta,
)


@pytest.mark.property
def test_normalize_fiber_accepts_enum_and_strings():
    assert normalize_fiber("data") is Fiber.DATA
    assert normalize_fiber("  DATA ") is Fiber.DATA
    assert normalize_fiber(Fiber.CHEM) is Fiber.CHEM


@pytest.mark.property
def test_normalize_fiber_rejects_unknown():
    with pytest.raises(ValueError):
        normalize_fiber("magic")


@pytest.mark.property
def test_normalize_domain_cleans_input():
    assert normalize_domain("Data Governance") == "data-governance"
    assert normalize_domain("  Organic   Chemistry ") == "organic-chemistry"


@pytest.mark.property
def test_fiber_relations_include_fiber_and_domains():
    rels = fiber_relations("asset:1", "data", ["governance", "quality"])
    assert len(rels) == 3
    assert rels[0].predicate == FIBER_PREDICATE
    assert rels[0].obj == "data"
    domains = {r.obj for r in rels if r.predicate == DOMAIN_PREDICATE}
    assert domains == {"governance", "quality"}


@pytest.mark.property
def test_fiber_relations_empty_domains_ok():
    rels = fiber_relations("asset:2", Fiber.PSEUDO)
    assert len(rels) == 1
    assert rels[0].obj == "pseudo"


@pytest.mark.property
def test_fiber_meta_round_trip():
    meta = FiberMeta(Fiber.ACADEMIC, ("mathematics", "physics"))
    rels = meta.to_relations("asset:3")
    assert any(r.obj == "academic" for r in rels)
    assert any(r.obj == "mathematics" for r in rels)
