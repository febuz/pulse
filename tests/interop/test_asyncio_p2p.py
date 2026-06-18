import asyncio

import pytest

from knitweb.fabric.feed import Feed
from knitweb.core import crypto
from knitweb.ledger.node import AccountNode
from knitweb.p2p import AsyncioP2PNode, FeedConflictError, P2PError, PeerAddress
from knitweb.p2p.wire import feed_head_from_record, feed_head_to_record


def run(coro):
    return asyncio.run(coro)


@pytest.mark.interop
def test_feed_head_wire_round_trips():
    feed = Feed.create()
    head = feed.append({"kind": "knowledge", "i": 1})
    assert feed_head_from_record(feed_head_to_record(head)) == head


@pytest.mark.interop
def test_two_nodes_replicate_signed_feed_over_asyncio():
    async def scenario():
        feed = Feed.create()
        feed.append({"kind": "knowledge", "title": "alpha"})
        final_head = feed.append({"kind": "resource", "capacity": 4, "price": 9})

        server = AsyncioP2PNode()
        server.add_feed(feed)
        client = AsyncioP2PNode()
        async with server:
            replica = await client.sync_feed(server.address, feed.feed)

        assert replica.head.feed == final_head.feed
        assert replica.head.root == final_head.root
        assert replica.head.length == final_head.length
        assert replica.head.fork == final_head.fork
        assert replica.head.verify()
        assert replica.entries == feed.entries
        assert client.replicas[feed.feed].head.root == final_head.root

    run(scenario())


@pytest.mark.interop
def test_conflicting_feed_history_is_frozen():
    async def scenario():
        priv, _ = crypto.generate_keypair()
        left = Feed(priv)
        right = Feed(priv)
        left.append({"i": 0})
        left.append({"i": 1, "side": "left"})
        right.append({"i": 0})
        right.append({"i": 1, "side": "right"})

        a = AsyncioP2PNode()
        a.add_feed(left)
        b = AsyncioP2PNode()
        b.add_feed(right)
        client = AsyncioP2PNode()

        async with a, b:
            await client.sync_feed(a.address, left.feed)
            with pytest.raises(FeedConflictError):
                await client.sync_feed(b.address, right.feed)

        assert left.feed in client.frozen_feeds

    run(scenario())


@pytest.mark.interop
def test_knit_completes_over_asyncio_wire_and_braids_validate():
    async def scenario():
        alice = AccountNode(genesis_balances={"PLS": 100})
        bob = AccountNode()

        sender = AsyncioP2PNode(account=alice)
        receiver = AsyncioP2PNode(account=bob)

        async with receiver:
            knit = await sender.send_knit(
                receiver.address,
                to_pub=bob.pub,
                symbol="PLS",
                amount=25,
                timestamp=1,
            )

        assert knit.from_pub == alice.pub
        assert knit.to_pub == bob.pub
        assert alice.balance("PLS") == 75
        assert bob.balance("PLS") == 25
        assert alice.braid.validate()
        assert bob.braid.validate()

    run(scenario())


@pytest.mark.interop
def test_overdraft_knit_is_rejected_before_receiver_credit():
    async def scenario():
        alice = AccountNode(genesis_balances={"PLS": 10})
        bob = AccountNode()

        sender = AsyncioP2PNode(account=alice)
        receiver = AsyncioP2PNode(account=bob)

        async with receiver:
            with pytest.raises(P2PError, match="overdraft"):
                await sender.send_knit(
                    receiver.address,
                    to_pub=bob.pub,
                    symbol="PLS",
                    amount=11,
                    timestamp=1,
                )

        assert alice.balance("PLS") == 10
        assert bob.balance("PLS") == 0

    run(scenario())


@pytest.mark.interop
def test_static_peerbook_returns_registered_peer():
    node = AsyncioP2PNode()
    peer = PeerAddress("127.0.0.1", 9000)
    node.peerbook.add("bob", peer)
    assert node.peerbook.get("bob") == peer
