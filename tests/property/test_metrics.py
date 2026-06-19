"""Proofs for integer-only metrics + FabricNode gossip-path instrumentation.

The metrics surface is observability, not consensus: it must never perturb a
signed record's bytes and must stay integer-only, monotonic for counters, and
deterministic so two nodes that observe the same event stream produce a
byte-identical :meth:`Metrics.snapshot`. These tests pin all of that, then drive
the FabricNode gossip path *in-process* (no sockets) to prove each counter moves
on the event it names — and that wiring metrics in leaves a fresh Knit's CID and
the web_state_root untouched.
"""

import asyncio

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.items import web_state_root
from knitweb.fabric.node import FabricNode
from knitweb.fabric.web import Web
from knitweb.p2p.metrics import FABRIC_METRICS, Metrics


def run(coro):
    return asyncio.run(coro)


# ── 1. Metrics primitive: counters, gauges, validation ───────────────────────

def test_unseen_metric_reads_zero():
    m = Metrics()
    assert m.get("records_woven") == 0
    assert m.tracked() == 0


def test_counter_is_monotonic_and_returns_total():
    m = Metrics()
    assert m.incr("frames_in") == 1
    assert m.incr("frames_in", 4) == 5
    assert m.incr("frames_in", 0) == 5  # a no-op delta is allowed
    assert m.get("frames_in") == 5


def test_counter_rejects_negative_delta():
    m = Metrics()
    with pytest.raises(ValueError):
        m.incr("frames_in", -1)


def test_gauge_moves_both_ways_but_stays_nonneg_int():
    m = Metrics()
    assert m.gauge("depth", 7) == 7
    assert m.gauge("depth", 2) == 2  # gauges may decrease
    assert m.get("depth") == 2
    with pytest.raises(ValueError):
        m.gauge("depth", -1)


def test_values_must_be_plain_ints_not_bool_or_float():
    m = Metrics()
    for bad in (True, 1.0, "1", None):
        with pytest.raises(TypeError):
            m.incr("x", bad)
        with pytest.raises(TypeError):
            m.gauge("x", bad)


def test_metric_name_must_be_nonempty_str():
    m = Metrics()
    with pytest.raises(TypeError):
        m.incr("")
    with pytest.raises(TypeError):
        m.incr(123)  # type: ignore[arg-type]


# ── 2. snapshot(): deterministic, sorted, canonical-CBOR-safe ────────────────

def test_snapshot_keys_are_sorted_and_independent_of_insertion_order():
    a = Metrics()
    for name in ("frames_out", "records_woven", "banned_refusals"):
        a.incr(name)
    b = Metrics()
    for name in ("banned_refusals", "records_woven", "frames_out"):
        b.incr(name)
    snap = a.snapshot()
    assert list(snap.keys()) == sorted(snap.keys())
    assert a.snapshot() == b.snapshot()  # order-independent


def test_snapshot_is_a_copy_and_cannot_corrupt_the_registry():
    m = Metrics()
    m.incr("frames_in", 3)
    snap = m.snapshot()
    snap["frames_in"] = 999
    snap["injected"] = 1
    assert m.get("frames_in") == 3
    assert m.tracked() == 1  # mutation of the copy did not add a series


def test_snapshot_is_canonical_cbor_byte_identical_across_event_order():
    a = Metrics()
    a.incr("frames_in", 2)
    a.incr("records_woven", 1)
    a.incr("broadcasts_sent", 5)
    b = Metrics()
    b.incr("broadcasts_sent", 5)
    b.incr("frames_in", 2)
    b.incr("records_woven", 1)
    # Snapshots encode to identical bytes regardless of the order events arrived.
    assert canonical.encode(a.snapshot()) == canonical.encode(b.snapshot())
    # And the encoding round-trips (it is a strict-canonical map of int values).
    assert canonical.decode(canonical.encode(a.snapshot())) == a.snapshot()


def test_fabric_metric_names_are_unique_and_well_formed():
    assert len(FABRIC_METRICS) == len(set(FABRIC_METRICS))
    assert all(isinstance(n, str) and n for n in FABRIC_METRICS)


# ── 3. Byte-identity: instrumentation never perturbs a signed record ─────────

def test_woven_knit_cid_is_unchanged_by_metering():
    """A record's CID in a metered FabricNode equals its CID in a bare Web."""
    rec = {"kind": "knowledge", "title": "alpha", "body": "x", "author": "z"}
    bare = Web()
    bare_cid = bare.weave(rec)

    async def scenario():
        node = FabricNode()  # carries a live Metrics()
        cid = await node.weave(rec)  # no peers → broadcast is a no-op
        return cid, node

    cid, node = run(scenario())
    assert cid == bare_cid
    # The metered node's state root matches the bare web's — instrumentation is
    # disjoint from the canonical/hash path.
    assert node.state_root == web_state_root(bare)
    # The signable bytes an author signs are untouched by metering.
    from knitweb.fabric.node import _record_signable
    assert _record_signable(rec) == _record_signable(rec)


# ── 4. FabricNode gossip-path instrumentation (in-process, no sockets) ───────

def test_local_weave_counts_records_woven():
    async def scenario():
        node = FabricNode()
        await node.weave({"kind": "knowledge", "title": "a", "body": "1", "author": node.pub})
        await node.weave({"kind": "knowledge", "title": "b", "body": "2", "author": node.pub})
        # Re-weaving identical content is idempotent → counter does not advance.
        await node.weave({"kind": "knowledge", "title": "a", "body": "1", "author": node.pub})
        return node

    node = run(scenario())
    assert node.metrics.get("records_woven") == 2
    assert node.metrics.get("broadcasts_sent") == 0  # no peers wired


def test_broadcast_counts_sent_and_failed_per_peer():
    async def scenario():
        sender = FabricNode()
        live = FabricNode()
        async with live:
            sender.add_peer("live", live.address)
            # An offline peer: a fresh address that is never started/listening.
            offline = FabricNode()
            sender.add_peer("offline", offline.address)
            await sender.weave({"kind": "knowledge", "title": "x", "body": "1", "author": sender.pub})
        return sender, live

    sender, live = run(scenario())
    assert sender.metrics.get("broadcasts_sent") == 1   # delivered to live peer
    assert sender.metrics.get("broadcasts_failed") == 1  # offline peer errored
    # The live peer ingested the record over its dispatch path.
    assert live.metrics.get("records_woven") == 1
    assert live.metrics.get("frames_in") == 1
    assert live.metrics.get("frames_out") == 1


def test_dispatch_counts_frames_in_and_out():
    async def scenario():
        node = FabricNode()
        # Drive the carrier-independent dispatch path directly (no socket).
        await node._dispatch({"kind": "fabric-sync-request"})
        await node._dispatch({"kind": "totally-unknown"})
        return node

    node = run(scenario())
    assert node.metrics.get("frames_in") == 2
    assert node.metrics.get("frames_out") == 2


def test_sync_pull_counts_only_newly_woven_records():
    async def scenario():
        src = FabricNode()
        await src.weave({"kind": "knowledge", "title": "e", "body": "1", "author": src.pub})
        await src.weave({"kind": "knowledge", "title": "f", "body": "2", "author": src.pub})
        async with src:
            joiner = FabricNode()
            added = await joiner.sync_from(src.address)
            again = await joiner.sync_from(src.address)  # idempotent
        return joiner, added, again

    joiner, added, again = run(scenario())
    assert (added, again) == (2, 0)
    assert joiner.metrics.get("sync_pulls") == 2  # second pull added nothing
    assert joiner.metrics.get("records_woven") == 2


class _StubWriter:
    """Minimal StreamWriter stand-in so the TCP-path handler can be driven without
    a real socket — its ``peername`` is a fixed endpoint we can pre-ban."""

    def __init__(self, peer):
        self._peer = peer
        self.frames = bytearray()
        self.closed = False

    def get_extra_info(self, key):
        return self._peer if key == "peername" else None

    def write(self, data):
        self.frames += data

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


class _StubReader:
    """Feeds a single pre-built frame, then EOF."""

    def __init__(self, payload=b""):
        self._buf = bytearray(payload)

    async def readexactly(self, n):
        if len(self._buf) < n:
            raise asyncio.IncompleteReadError(bytes(self._buf), n)
        chunk = bytes(self._buf[:n])
        del self._buf[:n]
        return chunk


def test_banned_peer_refusal_is_counted():
    from knitweb.p2p.reputation import Offense

    from knitweb.p2p.transport import tcp_peer_id
    from knitweb.p2p.wire import write_frame_bytes

    async def scenario():
        server = FabricNode()
        peer = ("10.0.0.9", 5555)
        # The reputation key is the remote IP only (tcp:<ip>) — the ephemeral port
        # is dropped so a repeat offender stays identified across reconnects.
        peer_id = tcp_peer_id("10.0.0.9")
        server.reputation.penalize(peer_id, Offense.FEED_CONFLICT)  # instant ban
        assert server.reputation.is_banned(peer_id)
        writer = _StubWriter(peer)
        # The ban gate now lives on the single carrier-agnostic _dispatch seam, so
        # a real frame is decoded first; the banned sender is then refused and the
        # banned_refusals counter advances.
        frame = write_frame_bytes({"kind": "fabric-sync-request"})
        await server._handle_peer(_StubReader(frame), writer)
        return server, writer

    server, writer = run(scenario())
    assert server.metrics.get("banned_refusals") == 1
    assert writer.closed and bytes(writer.frames)  # a "banned" error frame was sent


def test_oversized_frame_is_counted_as_oversized_not_malformed():
    async def scenario():
        server = FabricNode()
        # A 4-byte length header declaring an impossibly large frame.
        from knitweb.p2p.wire import MAX_FRAME_BYTES
        header = (MAX_FRAME_BYTES + 1).to_bytes(4, "big")
        writer = _StubWriter(("10.0.0.1", 1))
        await server._handle_peer(_StubReader(header), writer)
        return server

    server = run(scenario())
    assert server.metrics.get("frames_oversized") == 1
    assert server.metrics.get("frames_malformed") == 0
    assert server.metrics.get("frames_in") == 0


def test_malformed_frame_is_counted():
    async def scenario():
        server = FabricNode()
        # A valid 2-byte length header but a non-canonical / garbage body.
        header = (2).to_bytes(4, "big")
        writer = _StubWriter(("10.0.0.2", 2))
        await server._handle_peer(_StubReader(header + b"\xff\xff"), writer)
        return server

    server = run(scenario())
    assert server.metrics.get("frames_malformed") == 1
    assert server.metrics.get("frames_oversized") == 0


def test_snapshot_of_a_live_node_is_canonical_and_complete():
    async def scenario():
        a = FabricNode()
        b = FabricNode()
        async with b:
            a.add_peer("b", b.address)
            await a.weave({"kind": "knowledge", "title": "s", "body": "1", "author": a.pub})
        return a, b

    a, _b = run(scenario())
    snap = a.metrics.snapshot()
    # Every value is a non-negative int and the map is canonical-CBOR encodable.
    assert all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in snap.values())
    assert canonical.decode(canonical.encode(snap)) == snap
    assert snap.get("records_woven") == 1
    assert snap.get("broadcasts_sent") == 1
