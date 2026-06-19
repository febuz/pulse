"""Property proofs for the node-owned Byzantine-consequence loop (#24 hardening).

These are deterministic, socket-free proofs that the reputation wiring on
:class:`AsyncioP2PNode` / :class:`FabricNode` behaves as a *policy*:

  * detecting a feed conflict during merge bans the offending feed key and files
    a verifiable, byte-identical equivocation report (no signed-record bytes change);
  * a prefix-rewrite conflict carries the graded FEED_CONFLICT penalty;
  * an honest replication never penalizes anyone;
  * the equivocation-report wire codec round-trips and preserves canonical bytes;
  * malformed / oversized / invalid-signature offenses land their graded weights.

No wall-clock, no randomness beyond key generation: the same offense stream yields
the same ban verdict on every honest node, which is the whole point of reputation.
"""

import asyncio

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.equivocation import prove_equivocation, verify_equivocation_report
from knitweb.fabric.feed import Feed
from knitweb.fabric.node import FabricNode
from knitweb.p2p.node import AsyncioP2PNode, FeedConflictError, FeedReplica
from knitweb.p2p.reputation import Offense
from knitweb.p2p.transport import tcp_peer_id
from knitweb.p2p.wire import (
    equivocation_report_from_record,
    equivocation_report_to_record,
    write_frame,
)


# ── helpers ──────────────────────────────────────────────────────────────────

class _FakeWriter:
    def __init__(self, peername):
        self._peername = peername
        self.buffer = bytearray()

    def get_extra_info(self, key):
        return self._peername if key == "peername" else None

    def write(self, data):
        self.buffer.extend(data)

    async def drain(self):
        return None

    def close(self):
        return None

    async def wait_closed(self):
        return None


def _conflicting_heads():
    """Two heads under one key that equivocate at the same (length, fork)."""
    priv, _ = crypto.generate_keypair()
    left = Feed(priv)
    right = Feed(priv)
    left.append({"i": 0, "side": "left"})
    right.append({"i": 0, "side": "right"})
    return left.feed, left.head(), right.head()


async def _reader_with_frame(message: dict) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    sink = _FakeWriter(("x", 0))
    await write_frame(sink, message)
    reader.feed_data(bytes(sink.buffer))
    reader.feed_eof()
    return reader


async def _reader_with_raw(raw: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(raw)
    reader.feed_eof()
    return reader


def _decode_reply(writer: _FakeWriter) -> dict:
    n = int.from_bytes(writer.buffer[:4], "big")
    return canonical.decode(bytes(writer.buffer[4:4 + n]))


# ── 1. detect → prove → consequence in _merge_replica ────────────────────────

def test_merge_conflict_bans_offender_and_files_verifiable_report():
    feed_key, head_a, head_b = _conflicting_heads()
    node = AsyncioP2PNode()
    node.replicas[feed_key] = FeedReplica(head=head_a, entries=[{"i": 0, "side": "left"}])

    with pytest.raises(FeedConflictError):
        node._merge_replica(FeedReplica(head=head_b, entries=[{"i": 0, "side": "right"}]))

    assert node.reputation.is_banned(feed_key)
    assert node.reputation.score(feed_key) == Offense.EQUIVOCATION.value
    report = node.equivocation_reports[feed_key]
    assert report.feed == feed_key
    assert verify_equivocation_report(report)
    assert feed_key in node.frozen_feeds


def test_honest_replication_penalizes_nobody():
    feed = Feed.create()
    feed.append({"i": 0})
    feed.append({"i": 1})
    node = AsyncioP2PNode()
    node._merge_replica(FeedReplica(head=feed.head(), entries=feed.entries))
    assert node.reputation.tracked() == 0
    assert not node.frozen_feeds


def test_prefix_rewrite_conflict_takes_graded_feed_conflict_penalty():
    # A longer feed that rewrites an already-signed prefix is a conflict, but not a
    # single-position double-sign, so it gets FEED_CONFLICT (not an EQUIVOCATION report).
    priv, _ = crypto.generate_keypair()
    short = Feed(priv)
    short.append({"i": 0, "v": "a"})
    long = Feed(priv)
    long.append({"i": 0, "v": "b"})  # different signed prefix
    long.append({"i": 1, "v": "c"})

    node = AsyncioP2PNode()
    node.replicas[short.feed] = FeedReplica(head=short.head(), entries=short.entries)
    with pytest.raises(FeedConflictError):
        node._merge_replica(FeedReplica(head=long.head(), entries=long.entries))

    assert node.reputation.score(short.feed) == Offense.FEED_CONFLICT.value
    # A prefix conflict is not packaged as a one-position equivocation report.
    assert short.feed not in node.equivocation_reports


# ── 2. equivocation-report wire codec preserves canonical bytes ──────────────

def test_equivocation_report_record_is_byte_identical_round_trip():
    _, head_a, head_b = _conflicting_heads()
    report = prove_equivocation(head_a, head_b, reporter="watcher")
    record = equivocation_report_to_record(report)

    # The wire record is exactly the fabric record kind — same CID, same bytes.
    assert record == report.to_record()
    assert canonical.encode(record) == canonical.encode(report.to_record())
    # Round-trip through the wire codec yields an equal, still-verifiable report.
    parsed = equivocation_report_from_record(record)
    assert parsed == report
    assert verify_equivocation_report(parsed)
    assert parsed.cid == report.cid


# ── 3. reputation gate + frame penalties on the connection handler ───────────

def test_banned_peer_is_refused_before_any_work():
    node = AsyncioP2PNode()
    node.reputation.penalize(tcp_peer_id("10.0.0.9"), Offense.EQUIVOCATION)

    async def drive():
        reader = await _reader_with_frame({"kind": "feed-request", "feed": "x",
                                           "start": 0, "end": None})
        writer = _FakeWriter(("10.0.0.9", 7))
        await node._handle_peer(reader, writer)
        return _decode_reply(writer)

    reply = asyncio.run(drive())
    assert reply == {"kind": "error", "code": "banned", "message": "peer is banned"}


def test_malformed_frame_penalizes_sender_graded():
    node = AsyncioP2PNode()

    async def drive():
        reader = await _reader_with_raw((4).to_bytes(4, "big") + b"\xff\xff\xff\xff")
        writer = _FakeWriter(("10.0.0.1", 1))
        await node._handle_peer(reader, writer)
        return _decode_reply(writer)

    reply = asyncio.run(drive())
    assert reply.get("code") == "bad-frame"
    assert node.reputation.score(tcp_peer_id("10.0.0.1")) == Offense.MALFORMED_FRAME.value


def test_oversized_frame_penalizes_sender_graded():
    node = AsyncioP2PNode()
    from knitweb.p2p.wire import MAX_FRAME_BYTES

    async def drive():
        # A header that declares a frame above the cap → OVERSIZED_FRAME.
        reader = await _reader_with_raw((MAX_FRAME_BYTES + 1).to_bytes(4, "big"))
        writer = _FakeWriter(("10.0.0.2", 2))
        await node._handle_peer(reader, writer)
        return _decode_reply(writer)

    reply = asyncio.run(drive())
    assert reply.get("code") == "bad-frame"
    assert node.reputation.score(tcp_peer_id("10.0.0.2")) == Offense.OVERSIZED_FRAME.value


def test_two_malformed_frames_do_not_yet_ban():
    # Graded noise: MALFORMED_FRAME is cheap; it takes many to approach a ban.
    node = AsyncioP2PNode()
    for _ in range(2):
        node.reputation.penalize("noisy:1", Offense.MALFORMED_FRAME)
    assert not node.reputation.is_banned("noisy:1")
    assert node.reputation.score("noisy:1") == 2 * Offense.MALFORMED_FRAME.value


# ── 4. FabricNode shares the same consequence loop ───────────────────────────

def test_fabric_node_penalizes_forged_signature_and_bans():
    node = FabricNode()
    author = FabricNode()
    rec = {"kind": "knowledge", "title": "t", "body": "b", "author": author.pub}
    msg = author._signed_record_msg(rec)
    msg["record"] = {**rec, "body": "forged"}  # tamper after signing

    async def drive():
        reader = await _reader_with_frame(msg)
        writer = _FakeWriter(("172.16.0.5", 9))
        await node._handle_peer(reader, writer)
        return _decode_reply(writer)

    reply = asyncio.run(drive())
    assert reply.get("kind") == "error"
    assert node.reputation.score(tcp_peer_id("172.16.0.5")) == Offense.INVALID_SIGNATURE.value
    assert node.web.size == (0, 0)  # forged record never woven


def test_fabric_node_refuses_banned_peer():
    node = FabricNode()
    node.reputation.penalize(tcp_peer_id("172.16.0.6"), Offense.INVALID_SIGNATURE)
    node.reputation.penalize(tcp_peer_id("172.16.0.6"), Offense.STALE_OR_FORGED_PROOF)  # → 100, banned
    assert node.reputation.is_banned(tcp_peer_id("172.16.0.6"))

    async def drive():
        reader = await _reader_with_frame({"kind": "fabric-sync-request"})
        writer = _FakeWriter(("172.16.0.6", 9))
        await node._handle_peer(reader, writer)
        return _decode_reply(writer)

    reply = asyncio.run(drive())
    assert reply.get("code") == "banned"
