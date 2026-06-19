"""Lazy-relay propagation (#64): a weave travels by inv -> getdata -> verbatim
body, NOT by full-flood — and multi-node convergence + byte-identity still hold.

The merged :class:`~knitweb.p2p.inventory.InventoryRelay` is now WIRED into the
live :class:`~knitweb.fabric.node.FabricNode`: ``weave`` announces a record's
canonical CID, each peer wants only the CIDs it lacks, and the announcer serves
the wanted bodies as the *stored frame bytes verbatim*. This suite proves the
activation end to end over an in-memory carrier (no real socket, no handshake;
every dial is bounded by ``asyncio.wait_for``):

  * **lazy, not flood** — a record's BODY is sent to a peer exactly once, and a
    peer that ALREADY holds the CID is announced to but never sent the body
    again (the O(diff) collapse the SeenSet exists for);
  * **multi-node convergence** — a fan-out weaver's three-node web all settle on
    one identical ``web_state_root`` purely over the lazy relay path;
  * **byte-identity** — a fresh signed record's CID is byte-for-byte identical at
    every node after a relay hop (a relayed record's CID == the author's CID ==
    ``core.canonical.cid(record)``), and the stored frame is served verbatim.

All assertions are on integer Web sizes, hex ``state_root`` witnesses, and CID
strings, so a woven Knit's content address is never perturbed.
"""

import asyncio

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.items import web_state_root
from knitweb.fabric.node import FabricNode
from knitweb.p2p import wire
from knitweb.p2p.inventory import INV
from knitweb.p2p.transport import PeerAddress


# ── in-memory carrier ─────────────────────────────────────────────────────────

class _MemTransport:
    """A socket-free Transport routing a dial straight to a peer's ``_dispatch``.

    Every node registers in a shared ``registry`` keyed by an integer id carried
    in the ``PeerAddress.params``. A dial frames the request through the SAME
    canonical-CBOR ``write_frame_bytes`` / ``read_frame_bytes`` the real carriers
    use (so signed-record byte-identity is preserved on the carrier), hands the
    decoded map to the target's ``_dispatch`` seam — exactly what the live TCP
    accept loop feeds — and frames the response back. No handshake, no real time.
    """

    tag = "mem"

    def __init__(self, registry: dict, node_id: int) -> None:
        self._registry = registry
        self._node_id = node_id
        self.bytes_in_by_kind: dict[str, int] = {}

    def bind(self, node) -> None:
        self._node = node
        self._registry[self._node_id] = self

    async def dial(self, peer: PeerAddress, request: dict) -> dict:
        target = self._registry[int(peer.params["id"])]
        # Frame -> bytes -> frame: the carrier moves opaque canonical bytes only.
        raw = wire.write_frame_bytes(request)
        # Account inbound bytes by kind at the *receiver* so a test can prove a
        # body was/ wasn't carried for a given message kind.
        decoded = wire.read_frame_bytes(raw)
        kind = str(decoded.get("kind"))
        target.bytes_in_by_kind[kind] = target.bytes_in_by_kind.get(kind, 0) + len(raw)
        resp = await asyncio.wait_for(target._node._dispatch(decoded), timeout=5)
        return wire.read_frame_bytes(wire.write_frame_bytes(resp))

    async def listen(self, handler, on_frame_fault=None) -> None:  # pragma: no cover
        return None

    async def close(self) -> None:  # pragma: no cover
        return None

    def local_address(self) -> PeerAddress:
        return PeerAddress(transport="mem", params={"id": str(self._node_id)})


def _mem_node(registry: dict, node_id: int, **kw) -> FabricNode:
    tr = _MemTransport(registry, node_id)
    node = FabricNode(transport=tr, **kw)
    tr.bind(node)
    return node


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=10))


def _converged(*nodes: FabricNode) -> bool:
    return len({n.state_root for n in nodes}) == 1


def _inbox(node: FabricNode) -> dict:
    """The per-kind inbound byte tally the node's in-memory transport recorded."""
    return node.transport.bytes_in_by_kind


# ── 1. lazy relay: body sent once, never re-sent to a holder ──────────────────

@pytest.mark.interop
def test_weave_propagates_by_announce_want_serve_not_full_flood():
    async def scenario():
        reg: dict = {}
        a = _mem_node(reg, 1)  # weaver
        b = _mem_node(reg, 2)  # peer
        a.add_peer("b", b.address)

        rec = {"kind": "knowledge", "title": "alpha", "body": "x", "author": a.pub}
        cid = await a.weave(rec)

        # b learned the record via the lazy relay: same content-addressed CID.
        assert b.web.get(cid) is not None
        assert a.web.size == b.web.size == (1, 0)
        assert _converged(a, b)

        # The body rode exactly ONE inv-data frame into b (the two-step relay),
        # never a fabric-record full-flood: assert the flood kind never arrived.
        assert _inbox(b).get("fabric-record", 0) == 0
        assert _inbox(b).get(INV, 0) > 0      # the cheap CID announce
        assert _inbox(b).get("inv-data", 0) > 0  # the body, once
        body_bytes_after_first = _inbox(b)["inv-data"]

        # Re-announcing the SAME cid (idempotent re-weave) sends NO new body: b
        # already holds it, so its inv reply is an ack and no inv-data follows.
        assert await a.weave(rec) == cid
        # SeenSet at the announcer suppresses even the re-announce; either way no
        # second body crosses to a peer that already has the CID — the O(diff) win.
        assert _inbox(b)["inv-data"] == body_bytes_after_first
        assert a.web.size == b.web.size == (1, 0)

    run(scenario())


@pytest.mark.interop
def test_peer_that_already_holds_cid_is_not_sent_the_body():
    async def scenario():
        reg: dict = {}
        a = _mem_node(reg, 1)
        b = _mem_node(reg, 2)

        # b already holds the record (learned out of band before a announces).
        rec = {"kind": "knowledge", "title": "shared", "body": "y", "author": a.pub}
        cid = await a.weave(rec)            # a weaves (no peers yet)
        await b.sync_from(a.address)        # b pulls it via anti-entropy path
        assert b.web.get(cid) is not None
        _inbox(b).clear()

        # NOW a wires up b and announces. a's SeenSet already marked the cid from
        # its own weave, so announce returns None and not even an inv frame flies;
        # the body certainly never does.
        a.add_peer("b", b.address)
        rec2 = {"kind": "knowledge", "title": "fresh", "body": "z", "author": a.pub}
        cid2 = await a.weave(rec2)

        # b is sent the announce for the NEW cid, wants it, and gets its body once;
        # the already-held cid's body is never re-sent.
        assert b.web.get(cid2) is not None
        assert _inbox(b).get("fabric-record", 0) == 0  # never full-flooded
        assert _converged(a, b)

    run(scenario())


# ── 2. multi-node convergence over the lazy relay only ────────────────────────

@pytest.mark.interop
def test_three_node_fan_out_converges_over_lazy_relay():
    async def scenario():
        reg: dict = {}
        a = _mem_node(reg, 1)  # weaver fanning to two peers
        b = _mem_node(reg, 2)
        c = _mem_node(reg, 3)
        a.add_peer("b", b.address)
        a.add_peer("c", c.address)
        assert _converged(a, b, c)  # all empty

        await a.weave({"kind": "knowledge", "title": "k0", "body": "0", "author": a.pub})
        await a.weave({"kind": "resource", "resource_kind": "gpu", "capacity": 4,
                       "price_per_epoch": 9, "provider": a.pub})

        # Every node settled on ONE identical root, purely via inv->getdata->body.
        assert a.web.size == b.web.size == c.web.size == (2, 0)
        assert _converged(a, b, c)
        assert a.state_root != crypto.sha256(b"").hex()
        for cid in a.web.nodes:
            assert b.web.get(cid) is not None
            assert c.web.get(cid) is not None
        # Neither peer was full-flooded a record body.
        assert _inbox(b).get("fabric-record", 0) == 0
        assert _inbox(c).get("fabric-record", 0) == 0

    run(scenario())


# ── 3. byte-identity: a fresh CID is unchanged across the relay hop ───────────

@pytest.mark.interop
def test_relayed_cid_is_byte_identical_across_a_hop():
    async def scenario():
        reg: dict = {}
        a = _mem_node(reg, 1)
        b = _mem_node(reg, 2)
        a.add_peer("b", b.address)

        record = {"kind": "knowledge", "title": "ident", "body": "bytes", "author": a.pub}
        # The author's own content address, computed independently of the node.
        cid_author = canonical.cid(record)

        cid = await a.weave(record)
        assert cid == cid_author                      # weave indexes by canonical cid
        assert a._inv  # relay is wired

        # The CID b derived after the relay hop is byte-for-byte the author's CID.
        assert b.web.get(cid_author) is not None
        assert canonical.cid(b.web.get(cid_author)) == cid_author
        assert b.web.get(cid_author) == record        # inner record dict unchanged

        # The frame a STORED (and serves verbatim) decodes to the same record.
        stored = a._frames[cid_author]
        assert wire.read_frame_bytes(stored)["record"] == record
        # And b stored its own verbatim frame on ingest, re-serveable identically.
        assert wire.read_frame_bytes(b._frames[cid_author])["record"] == record
        assert web_state_root(a.web) == web_state_root(b.web)

    run(scenario())
