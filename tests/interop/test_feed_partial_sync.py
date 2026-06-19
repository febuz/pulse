"""Two-node partial feed replication over the asyncio wire (#24).

A seeder publishes a large feed; a fresh peer fetches only a contiguous slice
plus an O(count + log n) range multiproof and verifies it against the signed
head — without ever transferring the full log. Tampering and bad ranges are
rejected, and the existing whole-feed path keeps working unchanged.
"""

import asyncio

import pytest

from knitweb.core import crypto
from knitweb.fabric.feed import Feed
from knitweb.fabric.feed_multiproof import verify_range_multiproof
from knitweb.p2p import AsyncioP2PNode, P2PError


def run(coro):
    return asyncio.run(coro)


def _feed(n, tag="entry"):
    priv, _ = crypto.generate_keypair()
    f = Feed(priv)
    for i in range(n):
        f.append({"i": i, "payload": f"{tag}-{i}"})
    return f


@pytest.mark.interop
def test_peer_verifies_a_slice_without_the_full_log():
    async def scenario():
        feed = _feed(40)
        head = feed.head()
        server = AsyncioP2PNode()
        server.add_feed(feed)
        client = AsyncioP2PNode()

        async with server:
            sl = await client.sync_feed_range(server.address, feed.feed, start=10, count=8)

        assert sl.start == 10
        assert sl.entries == [feed.entry(10 + j) for j in range(8)]
        assert sl.head.feed == head.feed
        assert sl.head.root == head.root
        assert sl.head.length == head.length
        # the slice authenticates against the signed head on its own
        from knitweb.fabric.feed_multiproof import prove_range

        proof = prove_range(feed.entries, 10, 8)
        assert verify_range_multiproof(sl.head, sl.entries, proof)
        # a partial fetch is *not* stored as a full replica
        assert feed.feed not in client.replicas

    run(scenario())


@pytest.mark.interop
def test_single_entry_slice_round_trips():
    async def scenario():
        feed = _feed(33)
        server = AsyncioP2PNode()
        server.add_feed(feed)
        client = AsyncioP2PNode()
        async with server:
            sl = await client.sync_feed_range(server.address, feed.feed, start=17, count=1)
        assert sl.entries == [feed.entry(17)]

    run(scenario())


@pytest.mark.interop
def test_full_feed_sync_still_works_alongside_range():
    async def scenario():
        feed = _feed(12)
        server = AsyncioP2PNode()
        server.add_feed(feed)
        client = AsyncioP2PNode()
        async with server:
            replica = await client.sync_feed(server.address, feed.feed)
        assert replica.entries == feed.entries
        assert replica.head.root == feed.head().root

    run(scenario())


@pytest.mark.interop
def test_out_of_bounds_range_is_refused():
    async def scenario():
        feed = _feed(5)
        server = AsyncioP2PNode()
        server.add_feed(feed)
        client = AsyncioP2PNode()
        async with server:
            with pytest.raises(P2PError):
                await client.sync_feed_range(server.address, feed.feed, start=3, count=5)

    run(scenario())


@pytest.mark.interop
def test_unknown_feed_range_is_refused():
    async def scenario():
        server = AsyncioP2PNode()
        client = AsyncioP2PNode()
        async with server:
            with pytest.raises(P2PError):
                await client.sync_feed_range(server.address, "deadbeef", start=0, count=1)

    run(scenario())


@pytest.mark.interop
def test_nonpositive_count_rejected_locally():
    async def scenario():
        server = AsyncioP2PNode()
        client = AsyncioP2PNode()
        async with server:
            with pytest.raises(P2PError):
                await client.sync_feed_range(server.address, "feed", start=0, count=0)

    run(scenario())
