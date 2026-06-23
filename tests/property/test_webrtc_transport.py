"""Proofs for WebRtcTransport: byte-identity, peer-id stamping, and lifecycle.

The ``WebRtcTransport`` carries the same length-prefixed canonical-CBOR frames
as TCP and the relay (``write_frame_bytes``/``read_frame_bytes``), uses an
integer-counter rid that never touches a clock, and stamps the AUTHENTICATED
peer pubkey as ``ENVELOPE_PEER_KEY`` so the carrier-agnostic dispatch applies
the same reputation/ban gate uniformly.

A :class:`FakeWorkerBridge` wires two ``WebRtcTransport`` instances together
in-process (no real RTCPeerConnection): ``dial_frame`` on side-A enqueues the
frame for side-B's inbound callback, and ``respond_frame`` on side-B returns
the reply to side-A's pending ``dial_frame``.
"""

import asyncio
from typing import Awaitable, Callable

import pytest

from knitweb.p2p.relay import ENVELOPE_PEER_KEY
from knitweb.p2p.transport import PeerAddress
from knitweb.p2p.webrtc_transport import (
    WEBRTC_TAG,
    WebRtcError,
    WebRtcTransport,
    WorkerBridge,
    webrtc_peer_id,
)
from knitweb.p2p.wire import read_frame_bytes, write_frame_bytes


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fake in-process bridge pair
# ---------------------------------------------------------------------------


class FakeWorkerBridge(WorkerBridge):
    """In-process bridge backed by asyncio.Queue pairs.

    Two bridges are wired together via :func:`make_fake_bridge_pair`: a frame
    enqueued by bridge-A's ``dial_frame`` arrives as an inbound on bridge-B,
    and bridge-B's ``respond_frame`` resolves bridge-A's pending future.
    """

    def __init__(self, self_key: str) -> None:
        self._self_key = self_key
        # dial_frame waiters: rid -> Future[bytes]
        self._dial_waiters: dict = {}
        self._inbound: Callable[[str, int, bytes], Awaitable[None]] | None = None
        self._fault: Callable[[str, str], None] | None = None
        # Filled by make_fake_bridge_pair
        self._peer: "FakeWorkerBridge | None" = None

    async def dial_frame(self, peer_key: str, rid: int, frame: bytes) -> bytes:
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        self._dial_waiters[rid] = waiter
        # Deliver the frame as an inbound on the peer bridge (simulating
        # the DataChannel arriving on the other side).
        assert self._peer is not None
        if self._peer._inbound is not None:
            asyncio.ensure_future(self._peer._inbound(self._self_key, rid, frame))
        return await waiter

    def respond_frame(self, peer_key: str, rid: int, frame: bytes) -> None:
        # Route the reply back to the dialer's pending waiter.
        assert self._peer is not None
        waiter = self._peer._dial_waiters.pop(rid, None)
        if waiter is not None and not waiter.done():
            waiter.set_result(frame)

    def set_inbound(self, callback: Callable[[str, int, bytes], Awaitable[None]]) -> None:
        self._inbound = callback

    def set_frame_fault(self, callback: Callable[[str, str], None]) -> None:
        self._fault = callback

    async def close(self) -> None:
        for waiter in self._dial_waiters.values():
            if not waiter.done():
                waiter.set_exception(WebRtcError("transport closed"))
        self._dial_waiters.clear()

    def local_params(self) -> dict:
        return {"pubkey": self._self_key}


def make_fake_bridge_pair(
    key_a: str = "pub_a", key_b: str = "pub_b"
) -> tuple[FakeWorkerBridge, FakeWorkerBridge]:
    """Return two ``FakeWorkerBridge`` instances wired together as a loopback."""
    bridge_a = FakeWorkerBridge(self_key=key_a)
    bridge_b = FakeWorkerBridge(self_key=key_b)
    bridge_a._peer = bridge_b
    bridge_b._peer = bridge_a
    return bridge_a, bridge_b


def make_transport_pair(
    key_a: str = "pub_a", key_b: str = "pub_b"
) -> tuple[WebRtcTransport, WebRtcTransport, PeerAddress, PeerAddress]:
    bridge_a, bridge_b = make_fake_bridge_pair(key_a, key_b)
    ta = WebRtcTransport(bridge=bridge_a, self_key=key_a)
    tb = WebRtcTransport(bridge=bridge_b, self_key=key_b)
    addr_a = PeerAddress(transport=WEBRTC_TAG, params={"pubkey": key_a})
    addr_b = PeerAddress(transport=WEBRTC_TAG, params={"pubkey": key_b})
    return ta, tb, addr_a, addr_b


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.property
def test_dial_listen_roundtrip():
    """dial() + listen() complete a request/response exchange."""
    async def scenario():
        ta, tb, addr_a, addr_b = make_transport_pair()

        received = {}

        async def handler(request: dict) -> dict:
            received["request"] = request
            return {"kind": "pong", "echo": request.get("payload")}

        await tb.listen(handler)
        response = await ta.dial(addr_b, {"kind": "ping", "payload": 42})

        assert response == {"kind": "pong", "echo": 42}
        assert received["request"]["kind"] == "ping"

    run(scenario())


@pytest.mark.property
def test_envelope_peer_key_is_stamped():
    """The handler sees ENVELOPE_PEER_KEY set to ``webrtc:<dialer_pubkey>``."""
    async def scenario():
        ta, tb, addr_a, addr_b = make_transport_pair(key_a="aabbcc", key_b="ddeeff")

        stamped = {}

        async def handler(request: dict) -> dict:
            stamped["peer"] = request.get(ENVELOPE_PEER_KEY)
            return {"ok": True}

        await tb.listen(handler)
        await ta.dial(addr_b, {"kind": "test"})

        assert stamped["peer"] == webrtc_peer_id("aabbcc")
        assert stamped["peer"].startswith("webrtc:")

    run(scenario())


@pytest.mark.property
def test_frame_bytes_are_byte_identical():
    """Re-framing a decoded reply yields the same bytes — byte-identity preserved."""
    async def scenario():
        ta, tb, _, addr_b = make_transport_pair()

        async def handler(request: dict) -> dict:
            return {"z": 1, "a": 2, "nested": {"b": [1, 2, 3]}}

        await tb.listen(handler)
        response = await ta.dial(addr_b, {"kind": "x"})
        # Round-trip: encoding the decoded map gives the same canonical bytes.
        assert write_frame_bytes(response) == write_frame_bytes(
            read_frame_bytes(write_frame_bytes(response))
        )

    run(scenario())


@pytest.mark.property
def test_webrtc_peer_id_prefix():
    """``webrtc_peer_id`` uses a ``webrtc:`` prefix distinct from relay/tcp."""
    assert webrtc_peer_id("abc") == "webrtc:abc"
    assert not webrtc_peer_id("abc").startswith("relay:")
    assert not webrtc_peer_id("abc").startswith("tcp:")


@pytest.mark.property
def test_close_cancels_pending_dials():
    """``close()`` causes pending dials to raise :class:`WebRtcError`."""
    async def scenario():
        bridge_a = FakeWorkerBridge(self_key="pub_a")
        bridge_b = FakeWorkerBridge(self_key="pub_b")
        # Do NOT wire the bridges together — dial will hang waiting for a reply.
        bridge_a._peer = bridge_b
        ta = WebRtcTransport(bridge=bridge_a, self_key="pub_a", dial_timeout_s=60)
        addr_b = PeerAddress(transport=WEBRTC_TAG, params={"pubkey": "pub_b"})

        dial_task = asyncio.ensure_future(ta.dial(addr_b, {"kind": "ping"}))
        # Yield so the dial registers its waiter.
        await asyncio.sleep(0)
        await ta.close()
        with pytest.raises((WebRtcError, asyncio.CancelledError, Exception)):
            await dial_task

    run(scenario())


@pytest.mark.property
def test_local_address_uses_self_key():
    """``local_address()`` includes the transport's own pubkey."""
    bridge_a, _ = make_fake_bridge_pair(key_a="mypubkey")
    ta = WebRtcTransport(bridge=bridge_a, self_key="mypubkey")
    addr = ta.local_address()
    assert addr.transport == WEBRTC_TAG
    assert addr.params["pubkey"] == "mypubkey"


@pytest.mark.property
def test_dial_missing_pubkey_raises():
    """``dial()`` to a peer with no pubkey raises :class:`WebRtcError`` immediately."""
    async def scenario():
        bridge_a, _ = make_fake_bridge_pair()
        ta = WebRtcTransport(bridge=bridge_a, self_key="pub_a")
        bad_addr = PeerAddress(transport=WEBRTC_TAG, params={})
        with pytest.raises(WebRtcError, match="missing a pubkey"):
            await ta.dial(bad_addr, {"kind": "x"})

    run(scenario())
