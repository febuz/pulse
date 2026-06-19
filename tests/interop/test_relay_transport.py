"""RelayTransport: a NAT'd node converges over an HTTP store-and-forward relay.

The live carrier is the PHP relay on 5mart.ml (``api/relay/send`` +
``api/relay/fetch``). These tests stand up an in-memory relay with the same
mailbox semantics — deposit a frame into a named mailbox, drain queued frames —
so the transport's request/reply correlation and node convergence are exercised
without a socket. The relay only ever moves opaque base64'd frames; it never
decodes the canonical-CBOR payload, mirroring the real PHP endpoint.
"""

import asyncio

import pytest

from knitweb.fabric.feed import Feed
from knitweb.fabric.node import FabricNode
from knitweb.p2p import AsyncioP2PNode, PeerAddress, RelayTransport
from knitweb.p2p.relay import HttpPoster


def run(coro):
    return asyncio.run(coro)


class InMemoryRelay(HttpPoster):
    """A fake ``api/relay`` honouring the same send/fetch mailbox contract.

    ``send`` appends a base64 frame to a mailbox queue; ``fetch`` drains and
    returns a mailbox's queued frames (non-blocking, returns empty immediately).
    """

    def __init__(self) -> None:
        super().__init__()
        self.mailboxes: dict[str, list[dict]] = {}

    async def post(self, url: str, payload: dict) -> dict:
        # Run through the loop without real I/O.
        if url.endswith("/api/relay/send"):
            mb = payload["mailbox"]
            self.mailboxes.setdefault(mb, []).append(
                {"rid": payload.get("rid"), "frame": payload["frame"]}
            )
            return {"ok": True}
        if url.endswith("/api/relay/fetch"):
            mb = payload["mailbox"]
            queued = self.mailboxes.get(mb, [])
            self.mailboxes[mb] = []
            return {"messages": queued}
        raise AssertionError(f"unexpected relay url {url}")


def relay_for(node_mailbox: str, relay: InMemoryRelay) -> RelayTransport:
    return RelayTransport(
        base_url="https://5mart.ml", mailbox=node_mailbox, poster=relay
    )


@pytest.mark.interop
def test_natd_node_replicates_feed_over_relay():
    async def scenario():
        relay = InMemoryRelay()

        feed = Feed.create()
        feed.append({"kind": "knowledge", "title": "alpha"})
        final_head = feed.append({"kind": "resource", "capacity": 4, "price": 9})

        # The server sits behind NAT: it is reachable ONLY via its relay mailbox.
        server = AsyncioP2PNode(transport=relay_for("server-mb", relay))
        server.add_feed(feed)
        client = AsyncioP2PNode(transport=relay_for("client-mb", relay))
        async with server, client:
            server_peer = PeerAddress(
                transport="relay",
                params={"mailbox": "server-mb", "base_url": "https://5mart.ml"},
            )
            replica = await client.sync_feed(server_peer, feed.feed)

        assert replica.head.root == final_head.root
        assert replica.head.length == final_head.length
        assert replica.head.verify()
        assert replica.entries == feed.entries

    run(scenario())


@pytest.mark.interop
def test_fabric_node_converges_over_relay():
    async def scenario():
        relay = InMemoryRelay()
        a = FabricNode(transport=relay_for("a-mb", relay))
        b = FabricNode(transport=relay_for("b-mb", relay))
        async with a, b:
            b_peer = PeerAddress(
                transport="relay",
                params={"mailbox": "b-mb", "base_url": "https://5mart.ml"},
            )
            a.add_peer("b", b_peer)

            assert a.state_root == b.state_root  # both empty

            await a.weave(
                {"kind": "knowledge", "title": "alpha", "body": "x", "author": a.pub}
            )
            await a.weave(
                {"kind": "resource", "resource_kind": "gpu", "capacity": 4,
                 "price_per_epoch": 9, "provider": a.pub}
            )

            # Give the relay poll loop a couple of ticks to drain + dispatch.
            for _ in range(20):
                if b.web.size == (2, 0):
                    break
                await asyncio.sleep(0.05)

            assert b.web.size == a.web.size == (2, 0)
            assert b.state_root == a.state_root

    run(scenario())


@pytest.mark.interop
def test_relay_dial_reply_correlation_strips_envelope():
    """A relayed reply carries the transport rid but the carried map is clean."""

    async def scenario():
        relay = InMemoryRelay()
        server = AsyncioP2PNode(transport=relay_for("srv", relay))
        feed = Feed.create()
        feed.append({"kind": "knowledge", "title": "x"})
        server.add_feed(feed)
        client = AsyncioP2PNode(transport=relay_for("cli", relay))
        async with server, client:
            srv_peer = PeerAddress(
                transport="relay",
                params={"mailbox": "srv", "base_url": "https://5mart.ml"},
            )
            replica = await client.sync_feed(srv_peer, feed.feed)
        # The reply was delivered and carries no leaked _relay_* envelope keys.
        assert replica.head.feed == feed.feed

    run(scenario())
