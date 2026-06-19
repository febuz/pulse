"""End-to-end Byzantine-consequence loop over the asyncio p2p transport.

These tests exercise the detect → prove → consequence loop wired into the node:

  * a node that syncs two conflicting signed feed histories detects the conflict,
    builds an :class:`EquivocationReport`, bans the offending feed key, and files
    the report for re-gossip;
  * a gossiped, verified equivocation report bans the offender on the receiver and
    freezes the feed — while a forged/tampered report is refused with no penalty;
  * the per-peer reputation gate refuses and disconnects a banned peer;
  * a malformed wire frame penalizes the sender (graded misbehavior points).

The equivocation-report record kind is purely additive: no signed-record bytes
change. Detection / proof / reputation primitives are reused, not re-implemented.
"""

import asyncio

import pytest

from knitweb.core import crypto
from knitweb.fabric.equivocation import prove_equivocation, verify_equivocation_report
from knitweb.fabric.feed import Feed
from knitweb.p2p import AsyncioP2PNode, FeedConflictError, P2PError
from knitweb.p2p.reputation import Offense
from knitweb.p2p.wire import write_frame


def run(coro):
    return asyncio.run(coro)


class _FakeWriter:
    """A minimal StreamWriter stand-in that captures frames and a fixed peername."""

    def __init__(self, peername):
        self._peername = peername
        self.buffer = bytearray()

    def get_extra_info(self, key):
        if key == "peername":
            return self._peername
        return None

    def write(self, data):
        self.buffer.extend(data)

    async def drain(self):
        return None

    def close(self):
        return None

    async def wait_closed(self):
        return None


async def _feed_reader(message: dict) -> asyncio.StreamReader:
    """A StreamReader pre-loaded with one canonical-CBOR frame, at EOF."""
    reader = asyncio.StreamReader()
    sink = _FakeWriter(("x", 0))
    await write_frame(sink, message)
    reader.feed_data(bytes(sink.buffer))
    reader.feed_eof()
    return reader


async def _raw_reader(raw: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(raw)
    reader.feed_eof()
    return reader


def _decode_reply(writer: _FakeWriter) -> dict:
    from knitweb.core import canonical

    n = int.from_bytes(writer.buffer[:4], "big")
    return canonical.decode(bytes(writer.buffer[4:4 + n]))


def _conflicting_feeds():
    """Two feeds under the *same* key that equivocate at the same (length, fork)."""
    priv, _ = crypto.generate_keypair()
    left = Feed(priv)
    right = Feed(priv)
    left.append({"i": 0, "side": "left"})
    right.append({"i": 0, "side": "right"})
    return left, right


@pytest.mark.interop
def test_detected_conflict_bans_offender_and_files_report():
    async def scenario():
        left, right = _conflicting_feeds()
        a = AsyncioP2PNode()
        a.add_feed(left)
        b = AsyncioP2PNode()
        b.add_feed(right)
        client = AsyncioP2PNode()

        async with a, b:
            await client.sync_feed(a.address, left.feed)
            with pytest.raises(FeedConflictError):
                await client.sync_feed(b.address, right.feed)

        # detect → prove → consequence: the offending feed key is banned…
        assert client.reputation.is_banned(left.feed)
        assert client.reputation.score(left.feed) == Offense.EQUIVOCATION.value
        # …a portable report was filed and it verifies from its own bytes.
        report = client.equivocation_reports[left.feed]
        assert report.feed == left.feed
        assert verify_equivocation_report(report)
        assert left.feed in client.frozen_feeds

    run(scenario())


@pytest.mark.interop
def test_verified_report_gossips_bans_and_freezes_on_receiver():
    async def scenario():
        left, right = _conflicting_feeds()
        report = prove_equivocation(left.head(), right.head(), reporter="watcher")
        assert report is not None

        watcher = AsyncioP2PNode()
        receiver = AsyncioP2PNode()
        async with receiver:
            acked = await watcher.gossip_equivocation_report(receiver.address, report)

        assert acked is True
        assert receiver.reputation.is_banned(left.feed)
        assert left.feed in receiver.frozen_feeds
        assert receiver.equivocation_reports[left.feed].feed == left.feed

    run(scenario())


@pytest.mark.interop
def test_forged_report_is_refused_with_no_penalty():
    async def scenario():
        from dataclasses import replace

        left, right = _conflicting_feeds()
        report = prove_equivocation(left.head(), right.head(), reporter="watcher")
        _, other = crypto.generate_keypair()
        forged = replace(report, feed=other)  # re-point at an innocent key

        watcher = AsyncioP2PNode()
        receiver = AsyncioP2PNode()
        async with receiver:
            with pytest.raises(P2PError, match="unverified-report"):
                await watcher.gossip_equivocation_report(receiver.address, forged)

        # No penalty on bad evidence, and the innocent key's feed is not frozen.
        assert receiver.reputation.score(other) == 0
        assert other not in receiver.frozen_feeds

    run(scenario())


@pytest.mark.interop
def test_banned_peer_is_refused_at_the_gate():
    async def scenario():
        feed = Feed.create()
        feed.append({"i": 0})
        server = AsyncioP2PNode()
        server.add_feed(feed)

        # Drive the connection handler with an in-memory transport so we control
        # (and can ban) the exact peer endpoint the gate keys on.
        peername = ("203.0.113.7", 5555)
        server.reputation.penalize("203.0.113.7:5555", Offense.EQUIVOCATION)
        assert server.reputation.is_banned("203.0.113.7:5555")

        reader = await _feed_reader(
            {"kind": "feed-request", "feed": feed.feed, "start": 0, "end": None}
        )
        writer = _FakeWriter(peername)
        await server._handle_peer(reader, writer)

        reply = _decode_reply(writer)
        assert reply.get("kind") == "error"
        assert reply.get("code") == "banned"

    run(scenario())


@pytest.mark.interop
def test_malformed_frame_penalizes_sender():
    async def scenario():
        server = AsyncioP2PNode()
        peername = ("198.51.100.4", 4444)
        # A non-canonical / undecodable frame: a 4-byte length prefix, then garbage.
        reader = await _raw_reader((4).to_bytes(4, "big") + b"\xff\xff\xff\xff")
        writer = _FakeWriter(peername)
        await server._handle_peer(reader, writer)

        reply = _decode_reply(writer)
        assert reply.get("kind") == "error"
        assert reply.get("code") == "bad-frame"
        assert server.reputation.score("198.51.100.4:4444") == Offense.MALFORMED_FRAME.value

    run(scenario())


@pytest.mark.interop
def test_stale_proof_penalizes_served_peer():
    async def scenario():
        feed = Feed.create()
        feed.append({"i": 0})

        # A server that lies: it claims a non-empty Merkle-proof set the MVP rejects.
        class LyingNode(AsyncioP2PNode):
            def _serve_feed(self, msg):
                out = super()._serve_feed(msg)
                if out.get("kind") == "feed-data":
                    out["merkle_nodes"] = [{"x": 1}]  # unsupported / forged proof
                return out

        server = LyingNode()
        server.add_feed(feed)
        client = AsyncioP2PNode()

        async with server:
            with pytest.raises(Exception):
                await client.sync_feed(server.address, feed.feed)

        server_id = f"{server.host}:{server.port}"
        assert client.reputation.score(server_id) == Offense.STALE_OR_FORGED_PROOF.value

    run(scenario())
