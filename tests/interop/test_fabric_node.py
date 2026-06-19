"""Two in-process FabricNodes converge on the same web_state_root.

Covers the first increment of issue #9: record propagation + convergence over
the asyncio p2p transport.
"""

import asyncio

import pytest

from knitweb.core import crypto
from knitweb.fabric.items import web_state_root
from knitweb.fabric.node import FabricNode, FabricNodeError


def run(coro):
    return asyncio.run(coro)


@pytest.mark.interop
def test_two_nodes_converge_on_state_root_via_broadcast():
    async def scenario():
        a = FabricNode()
        b = FabricNode()
        async with a, b:
            # a knows about b and gossips its weaves there.
            a.add_peer("b", b.address)

            assert a.state_root == b.state_root  # both empty → equal

            await a.weave({"kind": "knowledge", "title": "alpha", "body": "x", "author": a.pub})
            await a.weave({"kind": "resource", "resource_kind": "gpu",
                           "capacity": 4, "price_per_epoch": 9, "provider": a.pub})

            # b ingested both records → identical node set → identical root.
            assert b.web.size == a.web.size == (2, 0)
            assert a.state_root != crypto.sha256(b"").hex()  # non-empty root
            assert b.state_root == a.state_root

        assert web_state_root(b.web) == a.state_root

    run(scenario())


@pytest.mark.interop
def test_late_joiner_catches_up_with_sync_from():
    async def scenario():
        a = FabricNode()
        b = FabricNode()
        async with a, b:
            # Records woven *before* b is wired up: broadcast misses b.
            cid1 = await a.weave({"kind": "knowledge", "title": "early", "body": "1", "author": a.pub})
            cid2 = await a.weave({"kind": "knowledge", "title": "later", "body": "2", "author": a.pub})

            assert b.web.size == (0, 0)
            assert b.state_root != a.state_root

            # b pulls a's full record set and converges.
            added = await b.sync_from(a.address)
            assert added == 2
            assert b.web.get(cid1) is not None
            assert b.web.get(cid2) is not None
            assert b.state_root == a.state_root

            # Idempotent: a second sync weaves nothing new.
            assert await b.sync_from(a.address) == 0

    run(scenario())


@pytest.mark.interop
def test_idempotent_and_order_independent_convergence():
    async def scenario():
        a = FabricNode()
        b = FabricNode()
        async with a, b:
            a.add_peer("b", b.address)
            rec = {"kind": "knowledge", "title": "dup", "body": "z", "author": a.pub}
            cid_first = await a.weave(rec)
            cid_again = await a.weave(rec)  # re-weave identical content
            assert cid_first == cid_again
            assert a.web.size == (1, 0)
            assert b.web.size == (1, 0)
            assert a.state_root == b.state_root

    run(scenario())


@pytest.mark.interop
def test_tampered_record_is_rejected_by_signature_check():
    async def scenario():
        a = FabricNode()
        b = FabricNode()
        async with b:
            rec = {"kind": "knowledge", "title": "honest", "body": "h", "author": a.pub}
            msg = a._signed_record_msg(rec)
            # Tamper with the record after signing → signature no longer matches.
            msg["record"] = {**rec, "body": "forged"}
            reply = await a._send(b.address, msg)
            # Server rejects with an error frame and weaves nothing.
            assert reply.get("kind") == "error"
            assert b.web.size == (0, 0)

    run(scenario())


def test_gossiped_frames_bounded_while_authored_survive():
    """#92: the gossiped-in (non-authored) frame portion is size-bounded with LRU
    eviction, while every weave()-authored CID stays served — our own records are the
    authoritative source (their loss would be permanent), a gossiped frame is re-fetch-safe
    via anti-entropy. A blanket LRU (no _authored exemption) would evict `mine` and fail."""
    async def scenario():
        a = FabricNode()
        b = FabricNode(max_gossiped_frames=3)
        async with a, b:
            mine = await b.weave({"kind": "knowledge", "title": "mine", "body": "0", "author": b.pub})
            gcids = []
            for i in range(10):                      # flood gossiped-in past the cap of 3
                rec = {"kind": "knowledge", "title": f"g{i}", "body": str(i), "author": a.pub}
                cid = await a.weave(rec)
                b._ingest_signed(a._signed_record_msg(rec))   # b._serve_peer_key is None -> no throttle
                gcids.append(cid)
            assert mine in b._frames                                   # authored survives the flood
            assert sum(c in b._frames for c in gcids) <= 3            # non-authored bounded at the cap
            assert gcids[-1] in b._frames and gcids[0] not in b._frames  # LRU: newest kept, oldest evicted

    asyncio.run(scenario())
