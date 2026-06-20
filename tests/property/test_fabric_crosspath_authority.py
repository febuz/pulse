"""#167 cross-path authority: ONE self-signed edge-shaped body must land the SAME
way on every fabric ingest carrier, so two honest peers can never hold divergent
``web_state_root`` for the same CID.

The bug: the ``_route`` ``kind=='fabric-record'`` seam filed the body envelope-
authoritatively as a NODE, while the no-``is_edge`` relay/sync seam
(``_serve_inv_data`` / ``_pull_cids`` / ``sync_from`` / ``_ingest_signed``) files it
content-authoritatively as an EDGE. A remote peer self-signs an edge-shaped body,
delivers it to peer A under ``fabric-record`` and to peer B via the relay/sync
seam, and the two peers commit permanently divergent state roots for one CID.

This test drives the identical signed envelope into A via the ``_route``
``fabric-record`` branch and into B via the no-``is_edge`` ``_ingest_signed`` seam,
and asserts the two peers converge. Pre-fix it FAILS-as-divergent (the two roots
differ); post-fix both carriers file the body as an EDGE and the roots match.
"""

from knitweb.fabric.items import web_state_root
from knitweb.fabric.node import FabricNode


def _seed_two_nodes(node, author):
    """Seed the two endpoint nodes the poison edge references, so both peers
    share an identical node set before the cross-path delivery."""
    n1 = node.web.weave({"kind": "knowledge", "title": "A", "author": author.pub})
    n2 = node.web.weave({"kind": "knowledge", "title": "B", "author": author.pub})
    return n1, n2


def test_crosspath_edge_body_converges_across_route_and_relay_seams() -> None:
    """The same self-signed edge-shaped body delivered to A via ``_route``
    ('fabric-record') and to B via the no-``is_edge`` relay seam must produce an
    identical ``web_state_root`` on both peers (no durable partition)."""
    attacker = FabricNode()
    # Both honest peers already hold the two endpoint nodes the edge references.
    peer_a = FabricNode()
    peer_b = FabricNode()
    n1, n2 = _seed_two_nodes(peer_a, attacker)
    _seed_two_nodes(peer_b, attacker)

    # ONE self-signed edge-shaped body. The envelope kind is 'fabric-record', but
    # the SIGNED record is Edge.to_record() shape: kind=='edge' with str endpoints.
    poison = {"kind": "edge", "src": n1, "dst": n2, "rel": "supports", "weight": 1}
    envelope = attacker._signed_record_msg(poison)  # envelope kind == 'fabric-record'

    # Peer A: delivered under the gossip routing table, kind=='fabric-record'.
    peer_a._route("fabric-record", dict(envelope))
    # Peer B: delivered via the real relay/sync seam (no explicit is_edge), the
    # path _serve_inv_data / _pull_cids / sync_from all funnel through.
    peer_b._ingest_signed(dict(envelope))

    root_a = web_state_root(peer_a.web)
    root_b = web_state_root(peer_b.web)

    # POST-FIX: content is the single authority -> both file it as an EDGE.
    assert root_a == root_b, (
        f"cross-path state-root partition: A={root_a} B={root_b}"
    )
    # Both peers filed the body as an EDGE (content-authoritative), not a node.
    assert sum(len(v) for v in peer_a.web._out.values()) == 1
    assert sum(len(v) for v in peer_b.web._out.values()) == 1
    assert len(peer_a.web.nodes) == 2
    assert len(peer_b.web.nodes) == 2
