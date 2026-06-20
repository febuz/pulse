"""Fabric edge gossip edge-cases (node-local)."""

from knitweb.fabric.node import FabricNode


def test_ingest_signed_fabric_edge_populates_web_link() -> None:
    src = FabricNode()
    dst = FabricNode()

    a = src.web.weave({"kind": "knowledge", "title": "A", "body": "source", "author": src.pub})
    b = src.web.weave({"kind": "knowledge", "title": "B", "body": "target", "author": src.pub})
    edge = src.web.link(a, b, "supports", weight=2)

    # The remote node receives the same two records and the signed edge envelope.
    receiver = FabricNode()
    receiver.web.weave({"kind": "knowledge", "title": "A", "body": "source", "author": src.pub})
    receiver.web.weave({"kind": "knowledge", "title": "B", "body": "target", "author": src.pub})
    envelope = src._signed_edge_msg(edge.to_record())

    assert receiver._ingest_signed(envelope)
    assert receiver.web._out[a][0].cid == edge.cid
    assert receiver._ingest_signed(envelope) is False


def test_fabric_node_snapshot_contains_signed_edges() -> None:
    node = FabricNode()
    a = node.web.weave({"kind": "knowledge", "title": "A", "body": "source", "author": node.pub})
    b = node.web.weave({"kind": "knowledge", "title": "B", "body": "target", "author": node.pub})
    node.web.link(a, b, "supports")

    snapshot = node._serve_sync()
    cids = {item["record"]["kind"] if item["record"].get("kind") is not None else None for item in snapshot["records"]}
    assert cids == {"knowledge", "edge"}
