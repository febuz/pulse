"""JSON-LD / OriginTrail-DKG export of the fabric Web (one woven graph, externally verifiable).

The :class:`~knitweb.fabric.web.Web` is Knitweb's woven graph of content-addressed
**nodes** (any canonical record, keyed by its CID) and first-class **edges** (typed,
weighted, directional links between CIDs). This module turns that graph into a
**JSON-LD document** with a stable ``@context``, so the Web is interoperable with the
OriginTrail Decentralized Knowledge Graph (DKG) and verifiable by any external party
that speaks JSON-LD — without pulling in a single dependency.

The export mirrors the proven content-derived pattern from
:mod:`knitweb.anchor.origintrail`: it reuses the same ``schema.org`` ``@context`` style
the DKG anchor assertions use, and every node's ``@id`` is its CID — a *content-derived*
identifier — so an external verifier can recompute each id from the node's own bytes and
check it offline, with no trust in the exporter.

Determinism (the whole point):

  * nodes are emitted in ascending CID order (the same order
    :func:`knitweb.fabric.items.web_state_root` sorts them for its Merkle root);
  * each node's outgoing edges are emitted sorted by ``(rel, dst)`` — exactly the order
    :meth:`knitweb.fabric.web.Web.traverse` walks them;
  * weights are integers and ids/relations are strings only — float-free, so the document
    canonically CBOR-encodes and re-exporting identical content is **byte-stable**.

:func:`export_web` and :func:`import_web` are inverses, so a Web survives a JSON-LD
round-trip with identical node CIDs, edges, and weights — the woven graph is its own proof.
This is a *pure read/write over the graph*; it never re-hashes or rewrites a node record,
so a fresh signed Knit's CID is untouched. No P2P involvement.
"""

from __future__ import annotations

from .web import Edge, Web

__all__ = [
    "DKG_NAMESPACE",
    "JSONLD_CONTEXT",
    "NODE_TYPE",
    "EDGE_TYPE",
    "ual_for_node",
    "export_web",
    "import_web",
]

# Shared with the OriginTrail anchor backend (anchor/origintrail.py): a Web node, like a
# checkpoint anchor, is a DKG Knowledge Asset whose id is content-derived from its CID.
DKG_NAMESPACE = "did:dkg:knitweb"

NODE_TYPE = "KnitwebWebNode"
EDGE_TYPE = "KnitwebWebEdge"

# A stable, schema.org-rooted JSON-LD @context. It is string-only and float-free, so the
# exported document is itself canonical-CBOR-encodable. Term -> IRI mappings give the
# compacted keys (``rel``, ``weight``, ``record`` ...) a stable global meaning for any
# external JSON-LD/DKG consumer, while keeping the document compact and deterministic.
JSONLD_CONTEXT: dict = {
    "@vocab": "https://schema.org/",
    "knit": "did:dkg:knitweb#",
    "id": "@id",
    "type": "@type",
    "record": "knit:record",
    "edges": "knit:edges",
    "rel": "knit:rel",
    "weight": "knit:weight",
    "src": {"@id": "knit:src", "@type": "@id"},
    "dst": {"@id": "knit:dst", "@type": "@id"},
}


def ual_for_node(node_cid: str) -> str:
    """The DKG Universal Asset Locator for a Web node — content-derived from its CID.

    Mirrors :func:`knitweb.anchor.origintrail.ual`: a verifier resolves the node back
    from this locator and recomputes the CID from the node's own canonical bytes.
    """
    return f"{DKG_NAMESPACE}/{node_cid}"


def _node_object(web: Web, node_cid: str) -> dict:
    """One JSON-LD node object: the record under a content-derived ``@id`` + its edges.

    Outgoing edges are emitted in ``(rel, dst)`` order — the canonical traversal order —
    so the serialization is deterministic regardless of link insertion order.
    """
    out_edges = sorted(web._out.get(node_cid, []), key=lambda e: (e.rel, e.dst, e.weight))
    return {
        "id": node_cid,
        "type": NODE_TYPE,
        "ual": ual_for_node(node_cid),
        "record": web.nodes[node_cid],
        "edges": [
            {
                "type": EDGE_TYPE,
                "rel": e.rel,
                "dst": e.dst,
                "weight": e.weight,
            }
            for e in out_edges
        ],
    }


def export_web(web: Web) -> dict:
    """Export *web* as a deterministic JSON-LD document (DKG/OriginTrail-compatible).

    The document is a ``@graph`` of node objects keyed by their CID (``@id``), each
    carrying its raw record and its outgoing typed edges. Nodes are sorted by CID and
    edges by ``(rel, dst, weight)``, so two Webs holding identical content always export
    to byte-identical JSON-LD (and identical canonical CBOR / CID).
    """
    return {
        "@context": JSONLD_CONTEXT,
        "@graph": [_node_object(web, cid) for cid in sorted(web.nodes.keys())],
    }


def import_web(doc: dict) -> Web:
    """Reconstruct a :class:`~knitweb.fabric.web.Web` from an exported JSON-LD document.

    Inverse of :func:`export_web`: weaving the records back yields the same node CIDs,
    and relinking the edges yields the same edge set, so ``export_web(import_web(doc))``
    reproduces ``doc`` byte-for-byte. Raises ``ValueError`` if a node's stated ``@id`` does
    not match the CID derived from its own record (the content-derived id is self-checking).
    """
    graph = doc.get("@graph", [])

    web = Web()
    # First pass: weave every node so edge endpoints exist before we link.
    for node in graph:
        record = node["record"]
        woven_cid = web.weave(record)
        stated = node.get("id")
        if stated is not None and stated != woven_cid:
            raise ValueError(
                f"node @id {stated!r} does not match its content-derived CID {woven_cid!r}"
            )

    # Second pass: relink edges (endpoints are now guaranteed present).
    for node in graph:
        src = web.weave(node["record"])  # idempotent; returns the same CID
        for e in node.get("edges", []):
            web.link(src, e["dst"], e["rel"], weight=e.get("weight", 1))

    return web


def edges_of(web: Web) -> list[Edge]:
    """All edges of *web* in deterministic (src, rel, dst, weight) order.

    Convenience for callers/tests that want the flat edge set the document encodes.
    """
    flat: list[Edge] = []
    for cid in sorted(web.nodes.keys()):
        flat.extend(sorted(web._out.get(cid, []), key=lambda e: (e.rel, e.dst, e.weight)))
    return flat
