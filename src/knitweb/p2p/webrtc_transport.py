"""WebRtcTransport — browser DataChannel carrier for in-tab Knitweb peers.

Canonical server-free architecture (Variant A: "the real engine in every tab"):
every browser tab IS a full Knitweb peer running the *unchanged* ``knitweb``
Python bytes via Pyodide in a module-type Web Worker.  A browser WASM Worker
cannot reach ``RTCPeerConnection``/``RTCDataChannel`` directly, so the JS shell
owns those objects and this module is their Python half: a single
:class:`WebRtcTransport` that satisfies the existing five-method
:class:`~knitweb.p2p.transport.Transport` Protocol
(``tag`` / ``async dial`` / ``async listen`` / ``async close`` /
``local_address``).

This is exactly the carrier the **HOLE-PUNCH SEAM** docstring on
:meth:`Transport.listen <knitweb.p2p.transport.Transport.listen>` anticipates:

    "A hole-punch transport implements this the same way TCP does — the only
    difference is *how the listening socket becomes reachable*. … Nothing in
    this protocol — nor in the node layer that consumes it — needs to change
    to add that transport."

Registering it via :meth:`BaseNode.add_transport` lets webrtc / relay / tcp
peers coexist transparently with ZERO edits to ``node.py`` / ``base_node.py``.
``TcpTransport`` is simply not instantiated in-tab (a tab cannot
``asyncio.start_server`` reachably anyway).

WIRE / CRYPTO CONTRACT (must stay byte-identical across peers)
--------------------------------------------------------------
* Frame format: 4-byte big-endian length prefix + canonical-CBOR body;
  ``MAX_FRAME_BYTES = 8 MiB``. Reuses :func:`knitweb.p2p.wire.write_frame_bytes`
  and :func:`knitweb.p2p.wire.read_frame_bytes` VERBATIM — no new encoder ever
  sits on a hashed/signed path, so a Knit's CID and a DER signature are
  unchanged as a frame crosses this carrier.
* Request/response correlation mirrors :mod:`knitweb.p2p.relay` exactly: each
  request is tagged with a fresh INTEGER ``_relay_rid`` + ``_relay_reply_to``
  in the *transport envelope*; the reserved ``_relay_*`` namespace is stripped
  by :func:`knitweb.p2p.relay._strip_envelope` BEFORE any signed/business
  logic runs, so it never enters canonical/hashed bytes.  The responder echoes
  the same ``rid``.  (A ``DataChannel`` is message-oriented, so the same
  one-shot request→reply shape the relay uses fits without change.)
* Sender identity: a carrier that can positively identify the peer stamps it as
  :data:`knitweb.p2p.relay.ENVELOPE_PEER_KEY` on the decoded request, so the
  carrier-agnostic dispatch applies the SAME reputation/ban gate uniformly.
  Here the peer id is the wallet-signed-QR pubkey the JS shell verified
  (``crypto.verify``) BEFORE the DataChannel opened — signature-gated
  authentication, never an unauthenticated backdoor.

SACRED INVARIANTS honored
--------------------------
(a) INTEGER-ONLY: ``rid`` is an integer counter (``itertools.count``), never a
    clock; no float, ``//`` never ``/`` anywhere on a path that matters.
(b) NO wall-clock / NO randomness on any decision/scoring/ordering path.  The
    correlation id is a pure integer counter.  The carrier reads no clock to
    decide anything; the only timeout is a transport-policy ceiling (never a
    CID/ordering input).
(c) BYTE-IDENTITY: opaque carriage — the frame bytes are produced/consumed only
    by :mod:`knitweb.p2p.wire`; this adapter never re-encodes a body.

The bridge to the JS shell is a small injectable seam (:class:`WorkerBridge`)
so the engine stays testable off-browser (a fake bridge loops frames in-process)
and so the only Pyodide-specific code is one thin class (:func:`pyodide_bridge`).
"""

from __future__ import annotations

import asyncio
import itertools
from typing import Awaitable, Callable, Optional

from .relay import (
    ENVELOPE_PEER_KEY,
    RELAY_ENVELOPE_PREFIX,
    _strip_envelope,
)
from .transport import FrameFaultHandler, FrameHandler, PeerAddress
from .wire import WireError, read_frame_bytes, write_frame_bytes

__all__ = [
    "WebRtcTransport",
    "WebRtcError",
    "WorkerBridge",
    "webrtc_peer_id",
    "WEBRTC_TAG",
]

#: The ``PeerAddress.transport`` tag this carrier owns.
WEBRTC_TAG = "webrtc"

#: Reputation-key prefix for a WebRTC sender, distinct from ``tcp:`` / ``relay:``.
_WEBRTC_PEER_PREFIX = "webrtc:"

#: Overall ceiling (integer seconds) a :meth:`WebRtcTransport.dial` waits for a
#: correlated reply before giving up.  Transport-policy only — never a CID input.
_DIAL_TIMEOUT_S = 30

# Transport-envelope correlation keys in the reserved ``_relay_*`` namespace so
# :func:`_strip_envelope` removes them before any signed/business logic and they
# never enter canonical/hashed bytes.  Reusing the SAME prefix as the relay carrier
# means the existing envelope-strip handles them with zero new surface.
_RID_KEY = RELAY_ENVELOPE_PREFIX + "rid"           # "_relay_rid"
_REPLY_TO_KEY = RELAY_ENVELOPE_PREFIX + "reply_to"  # "_relay_reply_to"


class WebRtcError(RuntimeError):
    """Raised when the WebRTC carrier hop fails or times out."""


def webrtc_peer_id(pubkey: str) -> str:
    """Stable reputation key for a WebRTC sender from its AUTHENTICATED pubkey.

    Unlike a relay mailbox (self-asserted) or a TCP source IP (shared across
    NAT peers), the WebRTC peer id is the pubkey the wallet-signed-QR handshake
    already proved possession of — a per-identity-stable key so a forger is
    banned individually with zero NAT collateral.
    """
    return f"{_WEBRTC_PEER_PREFIX}{pubkey}"


class WorkerBridge:
    """Injectable seam between this Python transport and the JS shell.

    Under Pyodide the concrete bridge marshals frames over ``postMessage``
    to/from the JS ``transport_webrtc.js`` module.  Factored out as a
    Protocol-shaped seam so the engine is testable off-browser (a fake bridge
    loops frames in-process) and so the only Pyodide-specific code is the thin
    :func:`pyodide_bridge` factory at the bottom of this module.

    Contract (all ``frame`` args are OPAQUE length-prefixed canonical-CBOR bytes
    produced/consumed only by :mod:`knitweb.p2p.wire`):

    * ``async dial_frame(peer_key, rid, frame) -> bytes`` — send a request
      frame to the AUTHENTICATED ``peer_key`` over its DataChannel and return
      the correlated reply frame.  Raises :class:`WebRtcError` on
      timeout/closure.
    * ``respond_frame(peer_key, rid, frame) -> None`` — mail a reply frame back
      to ``peer_key`` for the inbound request tagged ``rid``.
    * ``set_inbound(callback)`` — register the async callback the shell invokes
      for every inbound request: ``await callback(peer_key, rid, frame)``.
    * ``set_frame_fault(callback)`` — register the callback the shell invokes
      when an *identified* peer sends a malformed/oversized frame:
      ``callback(peer_key, error_str)``.
    * ``async close()`` — tear down all peer connections.
    * ``local_params() -> dict`` — routing/identity params for
      :meth:`local_address` (e.g. ``{"pubkey": ..., "mailbox": ...}``).

    This base class is abstract; subclass it or use :func:`pyodide_bridge`.
    """

    async def dial_frame(self, peer_key: str, rid: int, frame: bytes) -> bytes:
        raise NotImplementedError

    def respond_frame(self, peer_key: str, rid: int, frame: bytes) -> None:
        raise NotImplementedError

    def set_inbound(
        self, callback: Callable[[str, int, bytes], Awaitable[None]]
    ) -> None:
        raise NotImplementedError

    def set_frame_fault(self, callback: Callable[[str, str], None]) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError

    def local_params(self) -> dict:
        raise NotImplementedError

    def configure(self, msg: dict) -> None:
        pass


class WebRtcTransport:
    """WebRTC ``RTCDataChannel`` carrier satisfying the :class:`Transport` Protocol.

    Parameters
    ----------
    bridge:
        A :class:`WorkerBridge` to the JS shell that owns the actual
        ``RTCPeerConnection`` / ``RTCDataChannel`` objects.  Injected for
        testability.
    self_key:
        This tab's 33-byte compressed pubkey hex (``AccountNode.pub``).
    dial_timeout_s:
        Integer-seconds ceiling for a correlated reply (transport policy only).
    """

    tag = WEBRTC_TAG

    def __init__(
        self,
        *,
        bridge: WorkerBridge,
        self_key: str,
        dial_timeout_s: int = _DIAL_TIMEOUT_S,
    ) -> None:
        if dial_timeout_s < 1:
            raise ValueError("dial_timeout_s must be a positive integer")
        self.bridge = bridge
        self.self_key = self_key
        self.dial_timeout_s = dial_timeout_s
        self._handler: Optional[FrameHandler] = None
        self._on_frame_fault: Optional[FrameFaultHandler] = None
        # Pure integer counter — never a clock — mirroring relay.py.
        self._rid = itertools.count(1)
        self._closed = False

    # -- dial (request -> correlated reply) -----------------------------------

    async def dial(self, peer: PeerAddress, request: dict) -> dict:
        """Send one ``request`` to ``peer`` over its DataChannel; return the reply.

        Mirrors :meth:`RelayTransport.dial`: stamp the transport envelope with a
        fresh integer ``rid`` + our reply address (our own pubkey), frame it with
        :func:`write_frame_bytes`, hand the OPAQUE frame to the bridge, decode the
        correlated reply with :func:`read_frame_bytes`, and strip the envelope.
        """
        peer_key = peer.params.get("pubkey")
        if not peer_key:
            raise WebRtcError("webrtc peer address is missing a pubkey")
        rid = next(self._rid)
        envelope = dict(request)
        envelope[_RID_KEY] = rid
        envelope[_REPLY_TO_KEY] = self.self_key
        frame = write_frame_bytes(envelope)
        try:
            reply_frame = await asyncio.wait_for(
                self.bridge.dial_frame(peer_key, rid, frame),
                timeout=self.dial_timeout_s,
            )
        except asyncio.TimeoutError as exc:
            raise WebRtcError("webrtc dial timed out waiting for reply") from exc
        except WebRtcError:
            raise
        except Exception as exc:
            raise WebRtcError(f"webrtc dial failed: {exc}") from exc
        try:
            decoded = read_frame_bytes(reply_frame)
        except WireError as exc:
            raise WebRtcError(f"webrtc reply frame malformed: {exc}") from exc
        return _strip_envelope(decoded)

    # -- listen (inbound request -> handler -> reply) -------------------------

    async def listen(
        self,
        handler: FrameHandler,
        on_frame_fault: "FrameFaultHandler | None" = None,
    ) -> None:
        """Begin accepting inbound DataChannel requests, dispatching to ``handler``.

        HOLE-PUNCH SEAM realized: the only difference from the TCP listener is
        *how the channel becomes reachable* — the JS shell, after the
        wallet-signed-QR / STUN / PEX signaling ladder, hands us a connected,
        AUTHENTICATED channel.  We register an inbound callback with the bridge;
        for every inbound request it invokes :meth:`_on_inbound`, which decodes
        the frame, stamps the verified sender pubkey as
        :data:`ENVELOPE_PEER_KEY`, runs ``handler``, and mails the framed reply
        back over the same channel.
        """
        self._handler = handler
        self._on_frame_fault = on_frame_fault
        self.bridge.set_inbound(self._on_inbound)
        self.bridge.set_frame_fault(self._on_inbound_fault)

    async def _on_inbound(self, peer_key: str, rid: int, frame: bytes) -> None:
        """Decode one inbound request, dispatch, mail reply back."""
        if self._handler is None:
            return
        try:
            decoded = read_frame_bytes(frame)
        except WireError as exc:
            self._on_inbound_fault(peer_key, str(exc))
            return
        request = _strip_envelope(decoded)
        # The pubkey was verified by the signed-QR handshake BEFORE the channel
        # opened — a proven, per-identity-stable reputation key.
        request[ENVELOPE_PEER_KEY] = webrtc_peer_id(peer_key)
        try:
            response = await self._handler(request)
        except Exception:
            return
        try:
            out_frame = write_frame_bytes(response)
        except WireError:
            return
        self.bridge.respond_frame(peer_key, rid, out_frame)

    def _on_inbound_fault(self, peer_key: str, error: str) -> None:
        if self._on_frame_fault is None:
            return
        self._on_frame_fault(webrtc_peer_id(peer_key), WireError(error))

    # -- lifecycle ------------------------------------------------------------

    async def close(self) -> None:
        """Release all peer connections. Idempotent."""
        if self._closed:
            return
        self._closed = True
        await self.bridge.close()

    def local_address(self) -> PeerAddress:
        """Address peers dial to reach this transport (``transport="webrtc"``)."""
        params = dict(self.bridge.local_params())
        params.setdefault("pubkey", self.self_key)
        return PeerAddress(transport=WEBRTC_TAG, params=params)


# ---------------------------------------------------------------------------
# Concrete Pyodide bridge — marshals frames over ``postMessage`` to the JS shell.
#
# Kept at module end and lazily importing ``js`` so the module imports cleanly
# under CPython (for tests and off-browser use), where a fake :class:`WorkerBridge`
# is injected instead.
# ---------------------------------------------------------------------------


def pyodide_bridge(
    post_to_shell,
    self_key: str,
    mailbox: str,
    *,
    stun_servers: tuple[str, ...] = ("stun:stun.l.google.com:19302",),
) -> WorkerBridge:
    """Build the real :class:`WorkerBridge` for a Pyodide module-Worker.

    ``post_to_shell`` is the worker's ``postMessage`` (a JS function proxied
    into Python by Pyodide).  This bridge:

    * ``dial_frame`` posts ``{op:"webrtc_dial", peerKey, rid, frame}`` to the
      shell and awaits the matching ``webrtc_dial_result`` keyed by ``rid``.
    * ``respond_frame`` posts ``{op:"webrtc_respond", peerKey, rid, frame}``.
    * The shell delivers inbound requests / faults by calling the registered
      callbacks (the worker's onmessage routes ``webrtc_inbound`` /
      ``webrtc_frame_fault`` to them).

    All ``frame`` payloads are OPAQUE bytes — Pyodide copies them to/from a JS
    ``Uint8Array`` without this Python code ever decoding the body.
    """

    class _PyodideBridge(WorkerBridge):
        def __init__(self) -> None:
            self._post = post_to_shell
            self._self_key = self_key
            self._mailbox = mailbox
            self._stun_servers: list[str] = list(stun_servers)
            self._stun_configured = False
            self._inbound: Optional[
                Callable[[str, int, bytes], Awaitable[None]]
            ] = None
            self._fault: Optional[Callable[[str, str], None]] = None
            self._dial_waiters: dict = {}

        def on_dial_result(self, rid: int, frame: bytes) -> None:
            waiter = self._dial_waiters.pop(rid, None)
            if waiter is not None and not waiter.done():
                waiter.set_result(frame)

        def on_dial_error(self, rid: int, error: str) -> None:
            waiter = self._dial_waiters.pop(rid, None)
            if waiter is not None and not waiter.done():
                waiter.set_exception(WebRtcError(error))

        def on_inbound(self, peer_key: str, rid: int, frame: bytes) -> None:
            if self._inbound is not None:
                asyncio.ensure_future(self._inbound(peer_key, rid, frame))

        def on_frame_fault(self, peer_key: str, error: str) -> None:
            if self._fault is not None:
                self._fault(peer_key, error)

        async def dial_frame(self, peer_key: str, rid: int, frame: bytes) -> bytes:
            loop = asyncio.get_running_loop()
            waiter = loop.create_future()
            self._dial_waiters[rid] = waiter
            try:
                self._post({"op": "webrtc_dial", "peerKey": peer_key, "rid": rid, "frame": frame})
                return await waiter
            finally:
                self._dial_waiters.pop(rid, None)

        def respond_frame(self, peer_key: str, rid: int, frame: bytes) -> None:
            self._post({"op": "webrtc_respond", "peerKey": peer_key, "rid": rid, "frame": frame})

        def configure(self, msg: dict) -> None:
            self._post(msg)

        def set_inbound(self, callback: Callable[[str, int, bytes], Awaitable[None]]) -> None:
            if not self._stun_configured:
                self._stun_configured = True
                self._post({"op": "webrtc_configure", "stunServers": self._stun_servers})
            self._inbound = callback

        def set_frame_fault(self, callback: Callable[[str, str], None]) -> None:
            self._fault = callback

        async def close(self) -> None:
            self._post({"op": "webrtc_close"})
            for waiter in self._dial_waiters.values():
                if not waiter.done():
                    waiter.set_exception(WebRtcError("transport closed"))
            self._dial_waiters.clear()

        def local_params(self) -> dict:
            return {"pubkey": self._self_key, "mailbox": self._mailbox}

    return _PyodideBridge()
