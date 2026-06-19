"""Property tests for the JSON-LD / OriginTrail-DKG export of the fabric Web (#22).

Proves: deterministic, byte-stable export; content-derived ``@id`` per node; a clean
round-trip (export -> import -> export reproduces identical content and bytes); and that
the document is itself canonical-CBOR-encodable (float-free, string/integer-only) so it
is interoperable with the same DKG primitives the anchor backend uses.
"""

import pytest

from knitweb.core import canonical
from knitweb.fabric.jsonld import (
    DKG_NAMESPACE,
    EDGE_TYPE,
    JSONLD_CONTEXT,
    NODE_TYPE,
    edges_of,
    export_web,
    import_web,
    ual_for_node,
)
from knitweb.fabric.web import Web


def _sample_web() -> Web:
    web = Web()
    a = web.weave({"kind": "knowledge", "title": "fibers conserve mass"})
    b = web.weave({"kind": "resource", "n": "b"})
    c = web.weave({"kind": "receipt", "n": "c"})
    web.link(a, b, "supports", weight=3)
    web.link(b, c, "supports")
    web.link(a, c, "cites", weight=2)
    return web


@pytest.mark.property
def test_export_shape_and_content_derived_ids():
    web = _sample_web()
    doc = export_web(web)

    assert doc["@context"] is JSONLD_CONTEXT
    assert doc["@context"]["@vocab"] == "https://schema.org/"

    graph = doc["@graph"]
    assert len(graph) == web.size[0]
    for node in graph:
        assert node["type"] == NODE_TYPE
        # @id is the CID derived from the node's own record — recompute and check offline.
        assert node["id"] == canonical.cid(node["record"])
        assert node["ual"] == ual_for_node(node["id"])
        assert node["ual"].startswith(DKG_NAMESPACE + "/")
        for edge in node["edges"]:
            assert edge["type"] == EDGE_TYPE
            assert isinstance(edge["weight"], int) and not isinstance(edge["weight"], bool)


@pytest.mark.property
def test_export_is_deterministic_regardless_of_insertion_order():
    # Build the same logical graph two ways: different weave/link order.
    w1 = Web()
    a = w1.weave({"n": "a"})
    b = w1.weave({"n": "b"})
    c = w1.weave({"n": "c"})
    w1.link(a, c, "cites", weight=2)
    w1.link(a, b, "supports", weight=3)
    w1.link(b, c, "supports")

    w2 = Web()
    c2 = w2.weave({"n": "c"})
    b2 = w2.weave({"n": "b"})
    a2 = w2.weave({"n": "a"})
    w2.link(b2, c2, "supports")
    w2.link(a2, b2, "supports", weight=3)
    w2.link(a2, c2, "cites", weight=2)

    assert export_web(w1) == export_web(w2)
    # ...and byte-identical under canonical CBOR (the DKG/anchor encoding).
    assert canonical.encode(export_web(w1)) == canonical.encode(export_web(w2))


@pytest.mark.property
def test_export_is_canonical_cbor_safe():
    web = _sample_web()
    doc = export_web(web)
    # The document round-trips through the strict canonical codec untouched (no floats,
    # string/int-only) — so it shares the byte-identity guarantee of every signed record.
    assert canonical.decode(canonical.encode(doc)) == doc


@pytest.mark.property
def test_round_trip_reconstructs_identical_web():
    web = _sample_web()
    doc = export_web(web)
    rebuilt = import_web(doc)

    # Same nodes (by CID), same edge count.
    assert set(rebuilt.nodes.keys()) == set(web.nodes.keys())
    assert rebuilt.size == web.size
    # Same flat edge set (src, rel, dst, weight all preserved).
    assert {e.cid for e in edges_of(rebuilt)} == {e.cid for e in edges_of(web)}
    # Re-export reproduces the document byte-for-byte (export ∘ import is identity).
    assert export_web(rebuilt) == doc
    assert canonical.encode(export_web(rebuilt)) == canonical.encode(doc)


@pytest.mark.property
def test_round_trip_preserves_edge_weights_and_relations():
    web = _sample_web()
    rebuilt = import_web(export_web(web))
    rebuilt_edges = {(e.src, e.rel, e.dst): e.weight for e in edges_of(rebuilt)}
    orig_edges = {(e.src, e.rel, e.dst): e.weight for e in edges_of(web)}
    assert rebuilt_edges == orig_edges
    assert orig_edges  # non-trivial


@pytest.mark.property
def test_import_rejects_tampered_node_id():
    web = _sample_web()
    doc = export_web(web)
    # Flip a node's @id so it no longer matches its content-derived CID.
    doc["@graph"][0]["id"] = "bdeadbeef"
    with pytest.raises(ValueError, match="content-derived CID"):
        import_web(doc)


@pytest.mark.property
def test_export_does_not_mutate_or_rehash_node_records():
    web = _sample_web()
    before = {cid: dict(rec) for cid, rec in web.nodes.items()}
    doc = export_web(web)
    # Records carried verbatim; CIDs unchanged (a fresh Knit's CID must not move).
    for node in doc["@graph"]:
        assert node["record"] == before[node["id"]]
        assert canonical.cid(node["record"]) == node["id"]
    assert web.nodes == before


@pytest.mark.property
def test_empty_web_round_trips():
    doc = export_web(Web())
    assert doc["@graph"] == []
    assert export_web(import_web(doc)) == doc
