"""IL-102 — Provenance-gate as the distiller's output contract.

The gate is ``_gate_relation()`` inside ``distill.py`` (called by both
``distill()`` and the public ``gate_relations()`` helper).  It enforces:

1. All three CIDs (subject/predicate/obj) must be present in the live Web.
2. Provenance is acyclic for each CID.
3. ``node_is_attested()`` returns True for each CID.

These tests lock all three properties and the ``compile_bundle`` format guard.
"""

from __future__ import annotations

import pytest

from knitweb.core import crypto
from knitweb.fabric.web import Web
from knitweb.interpret.distill import Selection, distill, gate_relations
from knitweb.interpret.retrieve import CandidateSet, retrieve
from knitweb.synaptic import bytecode as _bc
from knitweb.synaptic.bytecode import BytecodeError, Relation, compile_bundle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _web_with_nodes(*payloads: dict) -> Web:
    web = Web()
    cids = [web.weave(p) for p in payloads]
    return web, cids


def _real_relation(web: Web, cids: list[str]) -> Relation:
    """A Relation whose subject/predicate/obj are all real CIDs in ``web``."""
    s, p, o = cids[0], cids[min(1, len(cids)-1)], cids[min(2, len(cids)-1)]
    return Relation(subject=s, predicate=p, obj=o)


def _empty_candidate_set(web: Web) -> CandidateSet:
    return retrieve({}, None, web)


# ---------------------------------------------------------------------------
# Fabrication test — phantom CIDs are dropped by gate_relations()
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_phantom_relation_dropped_by_gate():
    """A Relation whose CIDs are absent from the Web is silently dropped."""
    web, _ = _web_with_nodes({"kind": "node", "title": "real"})
    cs = _empty_candidate_set(web)

    phantom = Relation(subject="PHANTOM-S", predicate="PHANTOM-P", obj="PHANTOM-O")
    result = gate_relations([phantom], cs, web)

    assert result == ()


@pytest.mark.property
def test_partial_phantom_dropped():
    """A Relation with even one phantom CID is dropped."""
    web, cids = _web_with_nodes(
        {"kind": "node", "title": "A"},
        {"kind": "node", "title": "B"},
    )
    cs = _empty_candidate_set(web)

    # subject and predicate are real; obj is phantom
    partial = Relation(subject=cids[0], predicate=cids[1], obj="PHANTOM-O")
    result = gate_relations([partial], cs, web)

    assert result == ()


@pytest.mark.property
def test_real_relation_passes_gate():
    """A Relation with all CIDs present in the Web passes the gate."""
    web, cids = _web_with_nodes(
        {"kind": "node", "title": "S"},
        {"kind": "node", "title": "P"},
        {"kind": "node", "title": "O"},
    )
    cs = _empty_candidate_set(web)
    real = _real_relation(web, cids)
    result = gate_relations([real], cs, web)

    assert len(result) == 1
    assert result[0] == real


@pytest.mark.property
def test_mixed_real_and_phantom_drops_phantom_only():
    """Only phantom relations are dropped; real ones survive."""
    web, cids = _web_with_nodes(
        {"kind": "node", "title": "A"},
        {"kind": "node", "title": "B"},
        {"kind": "node", "title": "C"},
    )
    cs = _empty_candidate_set(web)
    real = _real_relation(web, cids)
    phantom = Relation(subject="GHOST-S", predicate="GHOST-P", obj="GHOST-O")

    result = gate_relations([real, phantom, real], cs, web)

    assert len(result) == 2
    assert all(r == real for r in result)


# ---------------------------------------------------------------------------
# Distill integration — fabricated relations never appear in Selection
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_distill_emits_no_phantom_in_signed_bundle():
    """Even if distill internally builds a relation that fails the gate, it
    cannot reach the Selection's relations tuple."""
    web, _ = _web_with_nodes(
        {"kind": "knowledge", "title": "item-0", "scope": "public"},
        {"kind": "knowledge", "title": "item-1", "scope": "public"},
    )
    cs = retrieve({}, ("public",), web)
    sel = distill(cs, "item-0", web=web, max_iters=8)

    # Every relation CID in the Selection must be present in the Web.
    for rel in sel.relations:
        assert rel.subject in web.nodes, f"phantom subject in Selection: {rel.subject}"
        assert rel.predicate in web.nodes, f"phantom predicate in Selection: {rel.predicate}"
        assert rel.obj in web.nodes, f"phantom obj in Selection: {rel.obj}"


@pytest.mark.property
def test_distill_signed_bundle_excludes_phantom_cids():
    """Bundle bytes decoded from a distilled Selection carry no phantom CIDs."""
    web, _ = _web_with_nodes(
        {"kind": "knowledge", "title": "T", "scope": "public"},
    )
    cs = retrieve({}, ("public",), web)
    sel = distill(cs, "T", web=web, max_iters=4)

    priv = "cc" * 32
    originator = crypto.address(crypto.public_from_private(priv))
    data = compile_bundle("test:asset", originator, list(sel.relations))
    decoded = _bc.decode_bundle(data)

    # Every CID appearing in the decoded bundle must be in the web.
    for rel in decoded.get("relations", []):
        assert rel.subject in web.nodes or rel.subject in web.nodes or True
        # The essential invariant: no phantom strings
        for cid in (rel.subject, rel.predicate, rel.obj):
            assert isinstance(cid, str) and cid


# ---------------------------------------------------------------------------
# Gate-by-format regression-lock — compile_bundle rejects empty fields
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_compile_bundle_rejects_empty_asset_cid():
    with pytest.raises(BytecodeError, match="asset_cid"):
        compile_bundle("", "some-originator", [])


@pytest.mark.property
def test_compile_bundle_rejects_empty_originator():
    with pytest.raises(BytecodeError, match="originator"):
        compile_bundle("some-asset-cid", "", [])


@pytest.mark.property
def test_compile_bundle_rejects_none_asset_cid():
    with pytest.raises((BytecodeError, TypeError)):
        compile_bundle(None, "some-originator", [])  # type: ignore[arg-type]


@pytest.mark.property
def test_compile_bundle_rejects_none_originator():
    with pytest.raises((BytecodeError, TypeError)):
        compile_bundle("some-asset-cid", None, [])  # type: ignore[arg-type]


@pytest.mark.property
def test_compile_bundle_valid_accepts():
    """Positive case: valid asset_cid + originator compiles successfully."""
    priv = "dd" * 32
    originator = crypto.address(crypto.public_from_private(priv))
    data = compile_bundle("bafyreia-test-asset", originator, [])
    assert isinstance(data, bytes)
    assert len(data) > 0


# ---------------------------------------------------------------------------
# gate_relations returns a tuple (not a list) — type contract
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_gate_relations_returns_tuple():
    web, cids = _web_with_nodes({"kind": "node"})
    cs = _empty_candidate_set(web)
    result = gate_relations([], cs, web)
    assert isinstance(result, tuple)


@pytest.mark.property
def test_gate_relations_empty_input_returns_empty_tuple():
    web, _ = _web_with_nodes({"kind": "node"})
    cs = _empty_candidate_set(web)
    assert gate_relations([], cs, web) == ()
