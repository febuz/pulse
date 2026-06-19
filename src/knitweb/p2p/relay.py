"""RelayTransport — HTTP store-and-forward client for NAT'd Knitweb nodes.

Most Knitweb nodes sit behind NAT/firewalls: inbound raw TCP is dropped, so they
can neither ``asyncio.start_server`` reachably nor be dialed directly. The live
PHP relay on ``5mart.ml`` is a **store-and-forward mailbox** that bridges them:

  * ``POST api/relay/send``  — deposit one opaque frame into a named mailbox.
  * ``POST api/relay/fetch`` — drain queued frames for a mailbox (long-poll).

A firewalled node therefore *listens* by registering a mailbox and polling
``fetch``; a peer reaches it by ``send``-ing to that mailbox instead of opening a
socket. The relay is a dumb pipe: it carries an opaque, length-prefixed
canonical-CBOR frame, base64-wrapped only so it survives an HTTP/JSON hop. The
relay never decodes the payload; this client only base64-(de)codes the exact
length-prefixed bytes :func:`knitweb.p2p.wire.write_frame_bytes` produced.

Byte-identity, precisely: the relay outer frame is **not** byte-identical to the
TCP frame for the same request. The outer map is the carried map plus the
transport-only correlation keys ``_relay_rid``/``_relay_reply_to`` (see the
``_relay_`` reservation below), so it has extra entries the TCP framing never
adds. What *is* byte-identical is (a) every nested signed record carried inside
the map — :func:`write_frame_bytes` re-encodes those embedded CBOR-record bytes
verbatim, so a Knit's CID is unchanged across the relay — and (b) the *stripped*
carried map: after :func:`_strip_envelope` removes the ``_relay_*`` keys, the
handler sees exactly the map the TCP transport would have delivered.

Request/response correlation: each request frame is tagged with a fresh integer
``rid`` and a ``reply_to`` mailbox; the responder ``send``s its reply frame to
``reply_to`` carrying the same ``rid``. This keeps the carrier a plain mailbox
while still giving :meth:`RelayTransport.dial` a one-shot request→response shape.

The ``_relay_`` key namespace is RESERVED for these transport-envelope keys. All
top-level keys whose name begins with :data:`RELAY_ENVELOPE_PREFIX` are stripped
by :func:`_strip_envelope` before any signed/business logic runs, so they never
enter canonical/hashed bytes. Because the strip is prefix-wide, a future
top-level ``_relay_*`` business key would be silently dropped over the relay — so
this is an intentional, documented reservation: do not introduce a non-transport
top-level key under this prefix.

Transport is dependency-free: it uses stdlib :mod:`urllib.request` (consistent
with the rest of the minimal-deps core) executed in a thread so the event loop is
never blocked.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import secrets
import urllib.request
from typing import Any

from .transport import FrameHandler, PeerAddress
from .wire import MAX_FRAME_BYTES, WireError, read_frame_bytes, write_frame_bytes

__all__ = [
    "RelayTransport",
    "RelayError",
    "HttpPoster",
    "ENVELOPE_PEER_KEY",
    "RELAY_ENVELOPE_PREFIX",
    "relay_peer_id",
]

# RESERVED top-level key namespace for transport-envelope correlation keys
# (``_relay_rid``, ``_relay_reply_to``, ``_relay_peer``). Every top-level key
# with this prefix is removed by :func:`_strip_envelope` before any
# signed/business logic runs, so it never enters canonical/hashed bytes. The
# strip is prefix-wide and therefore reserves the whole namespace: a future
# top-level ``_relay_*`` business key would be silently dropped over the relay,
# so this prefix must stay transport-only (see the module docstring).
RELAY_ENVELOPE_PREFIX = "_relay_"

# Transport-envelope key carrying the sender's relay identity to the handler.
# It is a ``_relay_*`` correlation key, so :func:`_strip_envelope` removes it
# before any signed/business logic runs — it never enters canonical/hashed bytes.
ENVELOPE_PEER_KEY = "_relay_peer"

# Reputation-key prefix for a relay sender, distinguishing a ``relay://`` mailbox
# from a TCP ``host:port`` so the two address spaces never collide in the ledger.
_RELAY_PEER_PREFIX = "relay:"


def relay_peer_id(mailbox: str) -> str:
    """Stable reputation key for a relay sender, derived from its reply mailbox."""
    return f"{_RELAY_PEER_PREFIX}{mailbox}"

# How long a single fetch poll waits server-side before returning empty, and how
# long dial() waits overall for a correlated reply.
_FETCH_TIMEOUT_S = 20
_DIAL_TIMEOUT_S = 30
_POLL_INTERVAL_S = 1


class RelayError(RuntimeError):
    """Raised when the relay HTTP hop fails or returns a malformed envelope."""


class HttpPoster:
    """Tiny stdlib JSON-over-HTTP POST helper, run off-loop in a thread.

    Factored out as a seam so tests can inject an in-memory relay without a
    socket and without monkeypatching urllib.
    """

    def __init__(self, *, timeout: int = _FETCH_TIMEOUT_S + 5) -> None:
        self.timeout = timeout

    async def post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._post_sync, url, payload)

    def _post_sync(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except OSError as exc:  # URLError, timeouts, refused connections
            raise RelayError(f"relay POST {url} failed: {exc}") from exc
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise RelayError(f"relay returned non-JSON from {url}") from exc
        if not isinstance(decoded, dict):
            raise RelayError(f"relay returned non-object from {url}")
        return decoded


def _b64encode(frame: bytes) -> str:
    return base64.b64encode(frame).decode("ascii")


def _b64decode(value: Any) -> bytes:
    if not isinstance(value, str):
        raise RelayError("relay envelope frame must be a base64 string")
    try:
        frame = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise RelayError("relay envelope frame is not valid base64") from exc
    if len(frame) > MAX_FRAME_BYTES + 4:
        raise RelayError("relayed frame exceeds maximum size")
    return frame


class RelayTransport:
    """HTTP store-and-forward transport over the PHP ``api/relay`` mailbox.

    Parameters
    ----------
    base_url:
        Relay root, e.g. ``"https://5mart.ml"``. ``api/relay/{send,fetch}`` are
        appended.
    mailbox:
        This node's inbound mailbox id. Peers ``send`` here to reach the node;
        :meth:`listen` polls ``fetch`` on it. A random id is minted if omitted.
    poster:
        Injectable :class:`HttpPoster` (tests pass an in-memory relay).
    """

    tag = "relay"

    def __init__(
        self,
        *,
        base_url: str,
        mailbox: str | None = None,
        poster: HttpPoster | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.mailbox = mailbox or secrets.token_hex(16)
        self._poster = poster or HttpPoster()
        self._handler: FrameHandler | None = None
        self._poll_task: asyncio.Task | None = None
        # Pending dials waiting on a correlated reply, keyed by request id.
        self._waiters: dict[int, asyncio.Future] = {}
        self._rid = itertools.count(1)

    # -- relay endpoints --------------------------------------------------

    @property
    def _send_url(self) -> str:
        return f"{self.base_url}/api/relay/send"

    @property
    def _fetch_url(self) -> str:
        return f"{self.base_url}/api/relay/fetch"

    async def _send_frame(self, mailbox: str, frame: bytes, rid: int) -> None:
        reply = await self._poster.post(
            self._send_url,
            {"mailbox": mailbox, "rid": rid, "frame": _b64encode(frame)},
        )
        if reply.get("ok") is False:
            raise RelayError(f"relay send refused: {reply.get('error')!r}")

    async def _fetch_frames(self) -> list[dict[str, Any]]:
        reply = await self._poster.post(
            self._fetch_url,
            {"mailbox": self.mailbox, "wait": _FETCH_TIMEOUT_S},
        )
        messages = reply.get("messages", [])
        if not isinstance(messages, list):
            raise RelayError("relay fetch returned non-list messages")
        return messages

    # -- dial (request → correlated reply) --------------------------------

    async def dial(self, peer: PeerAddress, request: dict) -> dict:
        target = peer.params.get("mailbox")
        if not target:
            raise RelayError("relay peer address is missing a mailbox")
        # We must be polling our own mailbox to receive the reply.
        if self._poll_task is None:
            self._start_polling()
        rid = next(self._rid)
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future = loop.create_future()
        self._waiters[rid] = waiter
        # Stamp the request so the responder knows where/how to reply. These keys
        # live in the *transport envelope*, not in any signed record — the opaque
        # frame bytes we relay are produced from `request` exactly as the TCP
        # transport would frame them.
        envelope = dict(request)
        envelope["_relay_rid"] = rid
        envelope["_relay_reply_to"] = self.mailbox
        frame = write_frame_bytes(envelope)
        try:
            await self._send_frame(target, frame, rid)
            return await asyncio.wait_for(waiter, timeout=_DIAL_TIMEOUT_S)
        except asyncio.TimeoutError as exc:
            raise RelayError("relay dial timed out waiting for reply") from exc
        finally:
            self._waiters.pop(rid, None)

    # -- listen (poll mailbox, dispatch to handler) -----------------------

    async def listen(self, handler: FrameHandler) -> None:
        self._handler = handler
        if self._poll_task is None:
            self._start_polling()

    def _start_polling(self) -> None:
        self._poll_task = asyncio.ensure_future(self._poll_loop())

    async def _poll_loop(self) -> None:
        while True:
            try:
                messages = await self._fetch_frames()
            except RelayError:
                # Transient relay outage: back off, keep the mailbox alive.
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue
            for message in messages:
                await self._dispatch(message)
            if not messages:
                await asyncio.sleep(_POLL_INTERVAL_S)

    async def _dispatch(self, message: dict[str, Any]) -> None:
        if not isinstance(message, dict):
            return
        try:
            frame = _b64decode(message.get("frame"))
            decoded = read_frame_bytes(frame)
        except (RelayError, WireError):
            return
        rid = decoded.get("_relay_rid")
        reply_to = decoded.get("_relay_reply_to")
        # A reply to one of our own dials?
        if "_relay_reply_to" not in decoded and isinstance(rid, int):
            waiter = self._waiters.get(rid)
            if waiter is not None and not waiter.done():
                waiter.set_result(_strip_envelope(decoded))
            return
        # An inbound request: dispatch to the handler and mail the reply back.
        if self._handler is None:
            return
        # Stamp a transport-envelope peer id so the carrier-agnostic handler can
        # apply the same reputation/ban gate the TCP _handle_peer wrapper applies
        # per-socket. The id is the sender's reply-to mailbox — the only stable
        # identity a store-and-forward mailbox exposes. It rides as a transport
        # envelope key (``_relay_*``), so `_strip_envelope` still drops it before
        # any signed/business logic and it never enters canonical/hashed bytes.
        request = _strip_envelope(decoded)
        if isinstance(reply_to, str):
            request[ENVELOPE_PEER_KEY] = relay_peer_id(reply_to)
        try:
            response = await self._handler(request)
        except Exception:  # noqa: BLE001 — never let one bad frame kill the loop
            return
        if isinstance(rid, int) and isinstance(reply_to, str):
            out = dict(response)
            out["_relay_rid"] = rid
            await self._send_frame(reply_to, write_frame_bytes(out), rid)

    # -- lifecycle --------------------------------------------------------

    async def close(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._poll_task = None
        for waiter in self._waiters.values():
            if not waiter.done():
                waiter.cancel()
        self._waiters.clear()

    def local_address(self) -> PeerAddress:
        return PeerAddress(
            transport="relay",
            params={"mailbox": self.mailbox, "base_url": self.base_url},
        )


def _strip_envelope(decoded: dict) -> dict:
    """Drop the reserved ``_relay_*`` correlation keys, leaving the carried map.

    The whole :data:`RELAY_ENVELOPE_PREFIX` namespace is stripped (see the module
    docstring's reservation note), so the result is byte-for-byte the map the TCP
    transport would have delivered to the handler.
    """
    return {
        k: v for k, v in decoded.items() if not k.startswith(RELAY_ENVELOPE_PREFIX)
    }
