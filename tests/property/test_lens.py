"""Property tests for the Knitweb Lens (MeTTa-inspired atomspace / adapter).

These pin the translation of the Hyperon atom/space/interpret pattern into
Knitweb's dependency-free Python core, plus the adapter that makes the fabric
digestible by virtualpc LLM agents.
"""

from __future__ import annotations

import json

import pytest

from knitweb.core.pulse import Pulse
from knitweb.fabric.web import Web
from knitweb.synaptic import bytecode as bc
from knitweb.synaptic.origintrail import resolve_asset
from knitweb.lens import (
    SymbolAtom,
    ExpressionAtom,
    VariableAtom,
    GroundedAtom,
    LensSpace,
    KnitwebLensAdapter,
    digest_context,
)


# ---------------------------------------------------------------------------
# Atom properties
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_atoms_are_hashable_and_value_based():
    a = SymbolAtom("foo")
    b = SymbolAtom("foo")
    c = SymbolAtom("bar")
    assert a == b
    assert hash(a) == hash(b)
    assert a != c
    # Usable as dict keys / set members.
    assert len({a, b, c}) == 2


@pytest.mark.property
def test_expression_atom_recursion_and_equality():
    a = ExpressionAtom(SymbolAtom("Edge"), SymbolAtom("src"), SymbolAtom("dst"))
    b = ExpressionAtom(SymbolAtom("Edge"), SymbolAtom("src"), SymbolAtom("dst"))
    assert a == b
    assert hash(a) == hash(b)


@pytest.mark.property
def test_grounded_atom_uses_stable_repr_for_equality():
    mutable = {"x": 1}
    a = GroundedAtom(mutable, "Record")
    b = GroundedAtom({"x": 1}, "Record")
    assert a == b
    assert hash(a) == hash(b)


@pytest.mark.property
def test_grounded_atom_differs_by_typename():
    a = GroundedAtom({"x": 1}, "RecordA")
    b = GroundedAtom({"x": 1}, "RecordB")
    assert a != b
    assert hash(a) != hash(b)


@pytest.mark.property
def test_grounded_atom_is_mutation_resilient():
    mutable = {"x": 1}
    a = GroundedAtom(mutable, "Record")
    original_render = a.render
    mutable["x"] = 2
    # The atom's render/equality/hash must not change when the wrapped value is mutated.
    assert a.render == original_render
    b = GroundedAtom({"x": 1}, "Record")
    assert a == b
    assert hash(a) == hash(b)


@pytest.mark.property
def test_variable_only_differs_by_name():
    v1 = VariableAtom("x")
    v2 = VariableAtom("x")
    v3 = VariableAtom("y")
    assert v1 == v2
    assert v1 != v3


# ---------------------------------------------------------------------------
# Space properties
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_space_add_is_idempotent():
    space = LensSpace()
    atom = SymbolAtom("Pulse")
    space.add(atom)
    space.add(atom)
    assert len(space) == 1
    assert atom in space


@pytest.mark.property
def test_space_query_binds_variable():
    space = LensSpace()
    space.add(ExpressionAtom(SymbolAtom("Beat"), SymbolAtom("epoch-0")))
    space.add(ExpressionAtom(SymbolAtom("Beat"), SymbolAtom("epoch-1")))

    pattern = ExpressionAtom(SymbolAtom("Beat"), VariableAtom("epoch"))
    results = space.query(pattern)
    assert len(results) == 2
    bindings = {r[1].get("epoch").name for r in results}
    assert bindings == {"epoch-0", "epoch-1"}


@pytest.mark.property
def test_space_query_enforces_consistent_variable_binding():
    space = LensSpace()
    space.add(ExpressionAtom(SymbolAtom("Edge"), SymbolAtom("A"), SymbolAtom("A")))
    space.add(ExpressionAtom(SymbolAtom("Edge"), SymbolAtom("A"), SymbolAtom("B")))

    # Only the self-loop matches when both ends must bind to the same atom.
    pattern = ExpressionAtom(
        SymbolAtom("Edge"), VariableAtom("x"), VariableAtom("x")
    )
    results = space.query(pattern)
    assert len(results) == 1


@pytest.mark.property
def test_space_atoms_are_deterministically_ordered():
    space = LensSpace()
    for name in ("z", "a", "m"):
        space.add(SymbolAtom(name))
    assert [str(a) for a in space.atoms()] == ["a", "m", "z"]


# ---------------------------------------------------------------------------
# Adapter / ingestion properties
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_adapter_ingests_pulse_and_renders_digest():
    pulse = Pulse(interval_s=10, genesis_ts=1000)
    pulse.beat(1000, state_root="00")
    pulse.beat(1010, state_root="11")

    adapter = KnitwebLensAdapter()
    adapter.ingest_pulse(pulse)
    assert len(adapter.space) == 2

    digest = adapter.digest()
    assert "Knitweb Lens digest" in digest
    assert "Beat" in digest
    assert "epoch" in digest


@pytest.mark.property
def test_adapter_ingests_web_and_query_finds_edges():
    web = Web()
    a = web.weave({"n": "a"})
    b = web.weave({"n": "b"})
    web.link(a, b, "supports", weight=2)

    adapter = KnitwebLensAdapter()
    adapter.ingest_web(web)
    assert len(adapter.space) == 3  # 2 nodes + 1 edge

    pattern = ExpressionAtom(
        SymbolAtom("Edge"),
        VariableAtom("src"),
        SymbolAtom("supports"),
        VariableAtom("dst"),
        VariableAtom("weight"),
    )
    matches = adapter.space.query(pattern)
    assert len(matches) == 1
    assert matches[0][1].get("weight").value == 2


@pytest.mark.property
def test_adapter_ingests_synaptic_bundle():
    rels = [
        bc.Relation("asset:1", "hasSource", "https://ifrs.org", "IFRS_File"),
        bc.Relation("asset:1", "hasSource", "https://youtube.com/x", "YouTube_Video"),
    ]
    bundle = bc.compile_bundle("asset:1", "Originator Inc", rels)

    adapter = KnitwebLensAdapter()
    adapter.ingest_bundle(bundle)
    assert len(adapter.space) == 3  # 1 Asset + 2 Relations

    digest = adapter.digest()
    assert "Asset" in digest
    assert "Originator Inc" in digest
    assert "hasSource" in digest


@pytest.mark.property
def test_adapter_ingests_origintrail_asset():
    asset = {
        "origintrail_id": 99482,
        "originator": "Global Finance & Media Corp",
        "linked_sources": [
            {"type": "IFRS_File", "url": "https://ifrs.org"},
            {"type": "YouTube_Video", "url": "https://youtube.com"},
        ],
    }
    asset_id, originator, relations = resolve_asset(asset)

    adapter = KnitwebLensAdapter()
    adapter.ingest_asset(asset)
    assert len(adapter.space) == 3  # 1 Asset + 2 Relations
    assert asset_id == "99482"
    assert originator == "Global Finance & Media Corp"


@pytest.mark.property
def test_adapter_message_payload_is_json_serialisable():
    pulse = Pulse(interval_s=10, genesis_ts=1000)
    pulse.beat(1000, state_root="00")

    adapter = KnitwebLensAdapter()
    adapter.ingest_pulse(pulse)
    payload = adapter.to_message_payload(sender="lens-agent", topic="fabric-digest")

    assert payload["sender"] == "lens-agent"
    assert payload["topic"] == "fabric-digest"
    assert payload["kind"] == "knitweb-lens-digest"
    assert payload["atom_count"] == 1
    # Must be safe to push through virtualpc's JSON-logged message bus.
    json_text = json.dumps(payload)
    restored = json.loads(json_text)
    assert "Knitweb Lens digest" in restored["content"]


@pytest.mark.property
def test_digest_focus_moves_atom_to_front():
    space = LensSpace()
    space.add(SymbolAtom("zeta"))
    space.add(SymbolAtom("alpha"))
    focus = SymbolAtom("focus")
    space.add(focus)

    digest = digest_context(space, focus=focus)
    # The focus line should appear early, followed by the focused atom line.
    assert "Focus: focus" in digest
    lines = [line for line in digest.splitlines() if line.startswith("- ")]
    assert lines[0] == "- focus"


@pytest.mark.property
def test_digest_pattern_filters_atoms():
    space = LensSpace()
    space.add(ExpressionAtom(SymbolAtom("Beat"), SymbolAtom("e0")))
    space.add(SymbolAtom("unrelated"))

    pattern = ExpressionAtom(SymbolAtom("Beat"), VariableAtom("x"))
    digest = digest_context(space, pattern=pattern)
    assert "Beat" in digest
    assert "unrelated" not in digest
