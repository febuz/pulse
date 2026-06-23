"""IL-104 — Compile the answer to a question (not just a named asset).

Locks the four acceptance criteria for sdk.distill_bundle:

1. distill_bundle(query, subscription, priv) → (bytecode, sig); verifies + decodes.
2. The resolve_asset / compile_asset path is unchanged (regression).
3. decode_bundle reconstructs exactly the gated relation set.
4. Content-addressable: identical relation set → identical bundle_digest.
"""

from __future__ import annotations

import pytest

from knitweb import sdk
from knitweb.core import crypto
from knitweb.fabric.web import Web
from knitweb.synaptic.bytecode import bundle_digest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _priv_pub():
    return crypto.generate_keypair()


def _knowledge_web() -> tuple[Web, str, str]:
    """Two-node web: A links to B via 'supports'. Returns (web, cid_a, cid_b)."""
    web = Web()
    a = web.weave({"kind": "knowledge", "title": "NodeA", "scope": "public"})
    b = web.weave({"kind": "knowledge", "title": "NodeB", "scope": "public"})
    web.link(a, b, "supports", weight=1)
    return web, a, b


# ---------------------------------------------------------------------------
# AC 1 — distill_bundle returns (bytes, str); verifies and decodes
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_distill_bundle_returns_bytes_and_hex_string():
    web, _, _ = _knowledge_web()
    priv, _ = _priv_pub()
    data, sig = sdk.distill_bundle("NodeA", ("public",), priv, web=web)
    assert isinstance(data, bytes) and len(data) > 0
    assert isinstance(sig, str) and len(sig) > 0


@pytest.mark.property
def test_distill_bundle_signature_verifies():
    web, _, _ = _knowledge_web()
    priv, pub = _priv_pub()
    data, sig = sdk.distill_bundle("NodeA", ("public",), priv, web=web)
    assert sdk.verify_bundle(pub, data, sig)


@pytest.mark.property
def test_distill_bundle_wrong_key_fails_verify():
    web, _, _ = _knowledge_web()
    priv, _ = _priv_pub()
    _, other_pub = _priv_pub()
    data, sig = sdk.distill_bundle("NodeA", ("public",), priv, web=web)
    assert not sdk.verify_bundle(other_pub, data, sig)


@pytest.mark.property
def test_distill_bundle_asset_cid_starts_with_distill():
    web, _, _ = _knowledge_web()
    priv, _ = _priv_pub()
    data, sig = sdk.distill_bundle("NodeA", ("public",), priv, web=web)
    decoded = sdk.decode_bundle(data)
    assert decoded["asset_cid"].startswith("distill:"), (
        f"expected distill: prefix, got {decoded['asset_cid']!r}"
    )


@pytest.mark.property
def test_distill_bundle_originator_matches_priv():
    web, _, _ = _knowledge_web()
    priv, pub = _priv_pub()
    data, _ = sdk.distill_bundle("NodeA", ("public",), priv, web=web)
    decoded = sdk.decode_bundle(data)
    assert decoded["originator"] == crypto.address(pub)


# ---------------------------------------------------------------------------
# AC 2 — resolve_asset / compile_asset path unchanged (regression)
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_compile_asset_still_works_after_distill_bundle():
    """The named-asset path must still compile and verify unchanged."""
    priv, pub = _priv_pub()
    asset = {
        "origintrail_id": 99,
        "originator": "Acme",
        "linked_sources": [
            {"type": "IFRS_File", "url": "https://ifrs.org"},
        ],
    }
    data, sig = sdk.compile_asset(asset, priv)
    assert sdk.verify_bundle(pub, data, sig)
    decoded = sdk.decode_bundle(data)
    assert decoded["originator"] == "Acme"
    assert len(decoded["relations"]) >= 1


@pytest.mark.property
def test_compile_asset_asset_cid_not_distill_prefix():
    """Named-asset bundles must NOT carry a 'distill:' prefix — distinct paths."""
    priv, _ = _priv_pub()
    asset = {
        "origintrail_id": 7,
        "originator": "Test",
        "linked_sources": [{"type": "IFRS_File", "url": "https://example.org"}],
    }
    data, _ = sdk.compile_asset(asset, priv)
    decoded = sdk.decode_bundle(data)
    assert not decoded["asset_cid"].startswith("distill:")


# ---------------------------------------------------------------------------
# AC 3 — decode_bundle reconstructs exactly the gated relation set
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_decode_bundle_reconstructs_gated_relations():
    """All relations in the decoded bundle must have CIDs present in the Web."""
    web, _, _ = _knowledge_web()
    priv, _ = _priv_pub()
    data, _ = sdk.distill_bundle("NodeA", ("public",), priv, web=web)
    decoded = sdk.decode_bundle(data)

    assert "relations" in decoded
    for rel in decoded["relations"]:
        assert rel.subject in web.nodes, f"decoded relation subject {rel.subject!r} not in web"
        assert rel.predicate in web.nodes, f"decoded relation predicate {rel.predicate!r} not in web"
        assert rel.obj in web.nodes, f"decoded relation obj {rel.obj!r} not in web"


@pytest.mark.property
def test_decode_bundle_returns_relation_objects():
    """Each decoded relation must be a synaptic Relation with required fields."""
    from knitweb.synaptic.bytecode import Relation
    web, _, _ = _knowledge_web()
    priv, _ = _priv_pub()
    data, _ = sdk.distill_bundle("NodeA", ("public",), priv, web=web)
    decoded = sdk.decode_bundle(data)
    for rel in decoded["relations"]:
        assert isinstance(rel, Relation)
        assert isinstance(rel.subject, str) and rel.subject
        assert isinstance(rel.predicate, str) and rel.predicate
        assert isinstance(rel.obj, str) and rel.obj
        assert isinstance(rel.weight, int)


# ---------------------------------------------------------------------------
# AC 4 — content-addressable: identical relation set → identical bundle_digest
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_distill_bundle_identical_query_same_digest():
    """Same query + same web + same priv → identical bundle_digest (deterministic)."""
    web_a, _, _ = _knowledge_web()
    web_b, _, _ = _knowledge_web()
    priv, _ = _priv_pub()

    data_a, _ = sdk.distill_bundle("NodeA", ("public",), priv, web=web_a)
    data_b, _ = sdk.distill_bundle("NodeA", ("public",), priv, web=web_b)

    assert bundle_digest(data_a) == bundle_digest(data_b), (
        "bundle_digest diverged for identical query/web/key"
    )


@pytest.mark.property
def test_distill_bundle_different_query_different_digest():
    """Different queries produce different asset_cids even on the same web."""
    web, _, _ = _knowledge_web()
    priv, _ = _priv_pub()

    data_a, _ = sdk.distill_bundle("NodeA", ("public",), priv, web=web)
    data_b, _ = sdk.distill_bundle("NodeB", ("public",), priv, web=web)

    decoded_a = sdk.decode_bundle(data_a)
    decoded_b = sdk.decode_bundle(data_b)
    # asset_cid encodes the query; different queries → different ids
    assert decoded_a["asset_cid"] != decoded_b["asset_cid"]


@pytest.mark.property
def test_bundle_digest_stable_identity():
    """bundle_digest of the same bytes is always the same (no random component)."""
    web, _, _ = _knowledge_web()
    priv, _ = _priv_pub()
    data, _ = sdk.distill_bundle("NodeA", ("public",), priv, web=web)
    assert bundle_digest(data) == bundle_digest(data)


@pytest.mark.property
def test_distill_bundle_none_subscription_works():
    """subscription=None (no filter) should compile successfully."""
    web, _, _ = _knowledge_web()
    priv, pub = _priv_pub()
    data, sig = sdk.distill_bundle("NodeA", None, priv, web=web)
    assert sdk.verify_bundle(pub, data, sig)
