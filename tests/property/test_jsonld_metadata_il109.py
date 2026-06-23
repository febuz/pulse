"""IL-109 — Rich reputation / deploy / debug metalayer as chunk metadata (off-wire).

Tests for all four acceptance criteria:

AC1 — fabric/jsonld.py has reputation/deploy-location/debug-score vocabulary
AC2 — metadata is off-wire (not part of signed bytecode surface)
AC3 — retrieve reads reputation metadata to rank candidates
AC4 — PII-free by construction (schema-validation test)
"""

from __future__ import annotations

import pytest

from knitweb.fabric.jsonld import (
    EDGE_METADATA_KEYS,
    JSONLD_CONTEXT,
    edges_of,
    export_web,
    import_web,
    validate_edge_metadata,
)
from knitweb.fabric.web import Web
from knitweb.interpret.retrieve import retrieve


# ---------------------------------------------------------------------------
# AC1 — metadata vocabulary in JSONLD_CONTEXT
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_jsonld_context_has_reputation_term():
    assert "reputation" in JSONLD_CONTEXT
    assert JSONLD_CONTEXT["reputation"] == "knit:reputation"


@pytest.mark.property
def test_jsonld_context_has_deploy_location_term():
    assert "deploy-location" in JSONLD_CONTEXT


@pytest.mark.property
def test_jsonld_context_has_debug_score_term():
    assert "debug-score" in JSONLD_CONTEXT


@pytest.mark.property
def test_edge_metadata_keys_covers_all_three_terms():
    assert EDGE_METADATA_KEYS == frozenset({"reputation", "deploy-location", "debug-score"})


@pytest.mark.property
def test_validate_edge_metadata_accepts_reputation_int():
    md = validate_edge_metadata({"reputation": 42})
    assert md["reputation"] == 42


@pytest.mark.property
def test_validate_edge_metadata_accepts_deploy_location_str():
    md = validate_edge_metadata({"deploy-location": "eu-west-1"})
    assert md["deploy-location"] == "eu-west-1"


@pytest.mark.property
def test_validate_edge_metadata_accepts_debug_score_int():
    md = validate_edge_metadata({"debug-score": 7})
    assert md["debug-score"] == 7


@pytest.mark.property
def test_validate_edge_metadata_accepts_all_three():
    md = validate_edge_metadata({
        "reputation": 10,
        "deploy-location": "us-east",
        "debug-score": 3,
    })
    assert len(md) == 3


@pytest.mark.property
def test_validate_edge_metadata_rejects_unknown_key():
    with pytest.raises(ValueError, match="unsupported"):
        validate_edge_metadata({"unknown-field": "x"})


@pytest.mark.property
def test_validate_edge_metadata_rejects_non_dict():
    with pytest.raises(TypeError):
        validate_edge_metadata(["reputation", 5])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AC2 — metadata is off-wire (not part of signed bytecode surface)
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_metadata_not_in_compiled_bundle():
    """Edge metadata must not appear in the signed bytecode bundle."""
    from knitweb import sdk
    from knitweb.core import crypto

    web = Web()
    a = web.weave({"kind": "knowledge", "title": "A", "scope": "public"})
    b = web.weave({"kind": "knowledge", "title": "B", "scope": "public"})
    web.link(a, b, "supports", weight=5, metadata={"reputation": 99})

    priv, pub = crypto.generate_keypair()
    data, sig = sdk.distill_bundle("A", ("public",), priv, web=web)
    decoded = sdk.decode_bundle(data)

    # Bundle contains relations but no edge metadata fields
    for rel in decoded["relations"]:
        rel_dict = rel.__dict__ if hasattr(rel, "__dict__") else {}
        assert "reputation" not in rel_dict
        assert "deploy-location" not in rel_dict
        assert "debug-score" not in rel_dict


@pytest.mark.property
def test_export_import_round_trip_preserves_metadata():
    """export_web/import_web round-trip keeps edge metadata intact (off-wire transport)."""
    web = Web()
    a = web.weave({"kind": "knowledge", "title": "A", "scope": "public"})
    b = web.weave({"kind": "knowledge", "title": "B", "scope": "public"})
    web.link(a, b, "supports", weight=3, metadata={"reputation": 77})

    doc = export_web(web)
    web2 = import_web(doc)

    edges = edges_of(web2)
    support_edges = [e for e in edges if e.rel == "supports"]
    assert len(support_edges) == 1
    meta = web2.edge_metadata(support_edges[0])
    assert meta.get("reputation") == 77


@pytest.mark.property
def test_export_edge_with_metadata_contains_metadata_key():
    web = Web()
    a = web.weave({"kind": "knowledge", "title": "A", "scope": "public"})
    b = web.weave({"kind": "knowledge", "title": "B", "scope": "public"})
    web.link(a, b, "supports", weight=1, metadata={"reputation": 5, "deploy-location": "eu"})

    doc = export_web(web)
    graph = doc["@graph"]
    # find the edge in the document
    found_metadata = None
    for node in graph:
        for edge in node.get("edges", []):
            if edge.get("rel") == "supports":
                found_metadata = edge.get("metadata")
    assert found_metadata is not None
    assert found_metadata["reputation"] == 5


# ---------------------------------------------------------------------------
# AC3 — retrieve uses reputation metadata to rank candidates
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_retrieve_ranks_high_reputation_first():
    """A chunk with higher reputation on its incoming edge ranks before a lower one."""
    from knitweb.fabric.items import web_state_root

    web = Web()
    query_node = web.weave({"kind": "knowledge", "title": "Query", "scope": "public"})
    high_rep = web.weave({"kind": "knowledge", "title": "HighRep", "scope": "public"})
    low_rep = web.weave({"kind": "knowledge", "title": "LowRep", "scope": "public"})

    # Link query → high_rep with high reputation
    web.link(query_node, high_rep, "supports", weight=1, metadata={"reputation": 100})
    # Link query → low_rep with low reputation
    web.link(query_node, low_rep, "supports", weight=1, metadata={"reputation": 1})

    wsc = web_state_root(web)
    cands = retrieve("Query", ("public",), web, web_state_cid=wsc)

    # Both should appear; high-rep should be earlier in the ordered list
    assert high_rep in cands.cids
    assert low_rep in cands.cids
    high_idx = list(cands.cids).index(high_rep)
    low_idx = list(cands.cids).index(low_rep)
    assert high_idx < low_idx, (
        f"expected high-rep ({high_rep[:8]}) before low-rep ({low_rep[:8]}), "
        f"got indices {high_idx} vs {low_idx}"
    )


@pytest.mark.property
def test_retrieve_candidate_reputation_reads_edge_metadata():
    """Candidate.reputation reflects the highest reputation value on adjacent edges."""
    from knitweb.fabric.items import web_state_root

    web = Web()
    seed = web.weave({"kind": "knowledge", "title": "Seed", "scope": "public"})
    target = web.weave({"kind": "knowledge", "title": "Target", "scope": "public"})
    web.link(seed, target, "supports", weight=1, metadata={"reputation": 55})

    wsc = web_state_root(web)
    cands = retrieve("Seed", ("public",), web, web_state_cid=wsc)

    target_candidate = next((c for c in cands.candidates if c.cid == target), None)
    assert target_candidate is not None
    assert target_candidate.reputation == 55


@pytest.mark.property
def test_retrieve_zero_reputation_still_includes_candidate():
    """A node with no reputation metadata is still reachable (defaults to score 0)."""
    from knitweb.fabric.items import web_state_root

    web = Web()
    seed = web.weave({"kind": "knowledge", "title": "Seed", "scope": "public"})
    unrated = web.weave({"kind": "knowledge", "title": "Unrated", "scope": "public"})
    web.link(seed, unrated, "supports", weight=1)  # no metadata

    wsc = web_state_root(web)
    cands = retrieve("Seed", ("public",), web, web_state_cid=wsc)
    assert unrated in cands.cids


# ---------------------------------------------------------------------------
# AC4 — PII-free by construction
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_validate_rejects_email_field():
    with pytest.raises(ValueError, match="not allowed"):
        validate_edge_metadata({"email": "user@example.com"})


@pytest.mark.property
def test_validate_rejects_name_field():
    with pytest.raises(ValueError, match="not allowed"):
        validate_edge_metadata({"name": "Alice"})


@pytest.mark.property
def test_validate_rejects_phone_field():
    with pytest.raises(ValueError, match="not allowed"):
        validate_edge_metadata({"phone": "+31600000000"})


@pytest.mark.property
def test_validate_rejects_address_field():
    with pytest.raises(ValueError, match="not allowed"):
        validate_edge_metadata({"address": "123 Main St"})


@pytest.mark.property
def test_validate_rejects_ip_field():
    with pytest.raises(ValueError, match="not allowed"):
        validate_edge_metadata({"ip": "192.168.1.1"})


@pytest.mark.property
def test_validate_rejects_private_key_field():
    with pytest.raises(ValueError, match="not allowed"):
        validate_edge_metadata({"private_key": "secret"})


@pytest.mark.property
def test_validate_rejects_public_key_field():
    with pytest.raises(ValueError, match="not allowed"):
        validate_edge_metadata({"public_key": "pubkey"})


@pytest.mark.property
def test_validate_rejects_signature_field():
    with pytest.raises(ValueError, match="not allowed"):
        validate_edge_metadata({"signature": "sig-hex"})


@pytest.mark.property
def test_validate_empty_dict_ok():
    """Empty metadata is valid (no PII and no unsupported keys)."""
    assert validate_edge_metadata({}) == {}


@pytest.mark.property
def test_validate_rejects_non_string_key():
    with pytest.raises((ValueError, TypeError)):
        validate_edge_metadata({123: "val"})  # type: ignore[dict-item]
