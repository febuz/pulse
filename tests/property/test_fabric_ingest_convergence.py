"""#167 convergence-soundness: one signed record yields ONE Web shape on every
ingest carrier — routing is decided by the SIGNED record CONTENT, never by the
relayer/attacker-controllable, unsigned envelope ``kind``.

Each assertion compares the full ``web_state_root`` (which commits to BOTH node
CIDs and ``_out`` edges) byte-for-byte, so a node-vs-edge routing split is
visible as a divergent root.

#167 history: this module previously pinned the BUGGY side. Its old
``test_node_record_converges_author_and_receiver`` asserted that an edge-shaped
signed body (``kind=='edge'`` with real ``src``/``dst``/``rel``) delivered under a
``fabric-record`` envelope was a NODE — and it manufactured author convergence by
calling ``author.web.weave(poison)`` to force the edge-shaped body into
``web.nodes``. No honest author does that: an author who means an edge calls
``link`` (filing by content into ``_out``). Meanwhile the relay/sync seam
(``_serve_inv_data`` / ``_pull_cids`` / ``sync_from``) routed the identical signed
body as an EDGE by content, so two honest peers held a permanently divergent
``web_state_root`` for one CID (#167). The envelope-authoritative assertion WAS
the bug; it is replaced below with the content-authoritative truth — the signed
edge body is an EDGE on every carrier — which is what the ``_route`` fix delivers.
"""

from knitweb.fabric.items import web_state_root
from knitweb.fabric.node import FabricNode


def test_edge_shaped_body_is_edge_on_route_carrier() -> None:
    """An edge-shaped signed body (``Edge.to_record()`` shape) delivered under the
    ``_route`` ``fabric-record`` envelope is filed by its SIGNED content as an
    EDGE — identically to the relay/sync seam — so the receiver converges with an
    author who files the same body by content via ``link`` (#167)."""
    author = FabricNode()
    n1 = author.web.weave({"kind": "knowledge", "title": "A", "author": author.pub})
    n2 = author.web.weave({"kind": "knowledge", "title": "B", "author": author.pub})
    # An honest author of this relation files it by CONTENT: link -> web._out.
    author.web.link(n1, n2, "supports", weight=1)

    # The wire body is exactly that edge record; the envelope kind is the unsigned,
    # relayer-controllable carrier — here the 'fabric-record' carrier (#167 vector).
    edge_record = {"kind": "edge", "src": n1, "dst": n2, "rel": "supports", "weight": 1}
    envelope = author._signed_record_msg(edge_record)  # envelope kind == 'fabric-record'

    receiver = FabricNode()
    receiver.web.weave({"kind": "knowledge", "title": "A", "author": author.pub})
    receiver.web.weave({"kind": "knowledge", "title": "B", "author": author.pub})
    # Receiver ingests the SAME signed envelope via the routing table.
    receiver._route("fabric-record", envelope)

    # Same signed body -> same Web shape on both sides (no durable partition).
    assert web_state_root(author.web) == web_state_root(receiver.web)
    # And it landed as an EDGE (signed content), not a node, on the receiver —
    # the same side the relay/sync seam files it, so every carrier agrees.
    assert sum(len(v) for v in receiver.web._out.values()) == 1
    assert len(receiver.web.nodes) == 2


def test_edge_record_converges_author_and_receiver() -> None:
    """A genuine 'fabric-edge' envelope (#108) still ingests as an edge on both
    sides — the legitimate edge path is preserved."""
    author = FabricNode()
    a = author.web.weave({"kind": "knowledge", "title": "X", "author": author.pub})
    b = author.web.weave({"kind": "knowledge", "title": "Y", "author": author.pub})
    edge = author.web.link(a, b, "supports", weight=2)
    envelope = author._signed_edge_msg(edge.to_record())  # envelope kind == 'fabric-edge'

    receiver = FabricNode()
    receiver.web.weave({"kind": "knowledge", "title": "X", "author": author.pub})
    receiver.web.weave({"kind": "knowledge", "title": "Y", "author": author.pub})
    receiver._route("fabric-edge", envelope)

    assert web_state_root(author.web) == web_state_root(receiver.web)
    # It landed as a real edge on the receiver.
    assert receiver.web._out[a][0].cid == edge.cid
    assert sum(len(v) for v in receiver.web._out.values()) == 1
