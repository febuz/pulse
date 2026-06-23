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
import json
import secrets
import urllib.request
from typing import Any

from .transport import FrameFaultHandler, FrameHandler, PeerAddress
from .wire import MAX_FRAME_BYTES, WireError, read_frame_bytes, write_frame_bytes

__all__ = [
    "RelayTransport",
    "RelayPool",
    "RelayError",
    "HttpPoster",
    "ENVELOPE_PEER_KEY",
    "ENVELOPE_ID_PROOF_KEY",
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

# Transport-envelope key carrying an OPTIONAL piggybacked node-identity proof
# (step 2 of #58). A dialing peer that holds a node key attaches its self-minted
# :func:`knitweb.p2p.identity.id_proof_to_record` here; the carrier-agnostic
# dispatch verifies it and, on success, keys reputation on the proven
# ``node:<pubkey>`` instead of the carrier's ``tcp:<ip>`` id, so a forger is
# banned individually with zero NAT collateral. Like the peer key it is a
# ``_relay_*`` correlation key — :func:`_strip_envelope` drops it before any
# signed/business logic, so it never enters canonical/hashed bytes. It is
# OPTIONAL: a request without it falls back to the existing carrier-id behaviour,
# keeping every pre-#58 peer and test unchanged.
#
# SCOPE: this is a TCP-carrier concern. The NAT collateral-ban it removes is
# specific to ``tcp:<ip>`` keying (many honest peers behind one public IP); a
# relay mailbox is already a per-node-stable identity, so the relay carrier keeps
# keying on the reply-to mailbox and strips this key (see ``_dispatch``) — proven
# keying activates only on the live TCP/direct-stream path. The key still lives in
# the reserved ``_relay_*`` namespace so it is stripped uniformly on the relay.
ENVELOPE_ID_PROOF_KEY = "_relay_id_proof"

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

    def _new_rid(self) -> int:
        # SECURITY: the request id is the ONLY thing correlating a relay reply to
        # a pending dial — a relay reply frame carries no authenticated sender, so
        # `_dispatch` resolves a waiter purely by matching `_relay_rid`. A
        # sequential id (the old ``itertools.count(1)``) is trivially guessable, so
        # anyone able to write to this mailbox could deposit a frame with a guessed
        # in-flight rid and no ``_relay_reply_to`` to resolve a pending dial with
        # attacker-chosen content (response spoofing / DoS). An unguessable 63-bit
        # id makes the rid an unforgeable capability the legitimate responder only
        # learns because we mailed it to the dialed peer. 63 bits stays a positive
        # value that fits signed-64-bit wire encodings; collisions are negligible
        # but rejected anyway so two concurrent dials never share a waiter.
        while True:
            rid = secrets.randbits(63)
            if rid not in self._waiters:
                return rid

    async def dial(self, peer: PeerAddress, request: dict) -> dict:
        target = peer.params.get("mailbox")
        if not target:
            raise RelayError("relay peer address is missing a mailbox")
        # We must be polling our own mailbox to receive the reply.
        if self._poll_task is None:
            self._start_polling()
        rid = self._new_rid()
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

    async def listen(
        self, handler: FrameHandler, on_frame_fault: "FrameFaultHandler | None" = None
    ) -> None:
        # ``on_frame_fault`` is part of the Transport.listen contract but unused
        # here: a relay frame that fails to base64/CBOR-decode is dropped in
        # :meth:`_dispatch` *before* the sender's reply-mailbox identity is even
        # parsed, so there is no positively-identified peer to penalize. The relay
        # carrier therefore never charges a malformed-frame offense (it stays a TCP
        # carrier concern), matching the pre-existing behavior.
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
        # Read the OPTIONAL piggybacked identity proof BEFORE _strip_envelope drops
        # the whole ``_relay_*`` namespace, so we can re-stamp it onto the request
        # the way the TCP carrier delivers it verbatim (see below).
        id_proof = decoded.get(ENVELOPE_ID_PROOF_KEY)
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
        # Proven node identity (step 2 of #58): a relay reply-to mailbox is
        # self-asserted and re-mintable per frame (``self.mailbox`` rotates with
        # zero ownership proof), so keying ban/budget/source-group on it alone lets
        # a sender mint a fresh reputation key every frame — evading bans, resetting
        # byte budgets, and spraying addrman source groups (#160/#161). We therefore
        # re-stamp the OPTIONAL piggybacked proof the sender stamped on its outbound
        # dial (``_stamp_id_proof``) onto the request, exactly as the TCP carrier
        # delivers it verbatim. The carrier-agnostic ``_dispatch`` then routes it
        # through the SAME identity gate (``_resolve_verdict``/``_resolve_peer_id``):
        # a VALID + FRESH + BOUND + first-seen proof upgrades the key to the proven
        # ``node:<pubkey>`` so mailbox rotation no longer mints fresh keys; an
        # absent/invalid/replayed/mis-bound proof falls back to ``relay:<mailbox>``
        # unchanged, preserving pre-existing behaviour for proofless relay peers.
        # The proof rides only in the stripped ``_relay_*`` envelope namespace and
        # is popped before any signed/business logic, so it never enters
        # canonical/hashed bytes — byte-identity is preserved.
        if id_proof is not None:
            request[ENVELOPE_ID_PROOF_KEY] = id_proof
        try:
            response = await self._handler(request)
        except Exception:
            # One bad relay frame must not kill the polling loop.
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
            except (asyncio.CancelledError, Exception):
                # Shutdown is best effort; remaining waiters are completed below.
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


_BACKOFF_S: int = 30


class RelayPool:
    """Multi-relay fanout pool with per-relay health tracking and failover.

    ``dial`` fans out to all healthy relays concurrently and returns the first
    successful reply.  ``listen`` starts all relay pollers and merges inbound
    frames through a shared handler.  A relay is marked unhealthy on
    :class:`RelayError` and restored after :data:`_BACKOFF_S` seconds.

    Parameters
    ----------
    relays:
        One or more :class:`RelayTransport` instances.  Must be non-empty.
    """

    def __init__(self, relays: list[RelayTransport]) -> None:
        if not relays:
            raise ValueError("RelayPool requires at least one relay")
        self._relays = list(relays)
        self._healthy: set[str] = {r.base_url for r in self._relays}
        self._unhealthy_until: dict[str, int] = {}

    def _is_healthy(self, relay: RelayTransport) -> bool:
        if relay.base_url in self._unhealthy_until:
            import time
            if int(time.monotonic()) < self._unhealthy_until[relay.base_url]:
                return False
            self._healthy.add(relay.base_url)
            del self._unhealthy_until[relay.base_url]
        return relay.base_url in self._healthy

    def _mark_unhealthy(self, relay: RelayTransport) -> None:
        import time
        self._healthy.discard(relay.base_url)
        self._unhealthy_until[relay.base_url] = int(time.monotonic()) + _BACKOFF_S

    async def dial(self, peer: "PeerAddress", request: dict) -> dict:
        """Fan-out dial to all healthy relays; return first success.

        Falls back to all relays (including temporarily-unhealthy ones) if none
        are currently healthy, so the pool never fully stalls after a blip.
        """
        candidates = [r for r in self._relays if self._is_healthy(r)] or self._relays
        tasks: list[asyncio.Task] = []
        loop = asyncio.get_running_loop()

        async def _try(relay: RelayTransport) -> tuple[RelayTransport, dict]:
            result = await relay.dial(peer, dict(request))
            return relay, result

        for relay in candidates:
            tasks.append(loop.create_task(_try(relay)))

        errors: list[Exception] = []
        pending = set(tasks)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                exc = task.exception()
                if exc is None:
                    relay, result = task.result()
                    for t in pending:
                        t.cancel()
                    return result
                else:
                    errors.append(exc)
                    # find which relay raised and mark unhealthy
                    for t in tasks:
                        if t is task:
                            idx = tasks.index(t)
                            if idx < len(candidates):
                                self._mark_unhealthy(candidates[idx])
                            break
        raise RelayError(f"all relays failed: {errors[0]}") from errors[0]

    async def listen(
        self, handler: "FrameHandler", on_frame_fault: "FrameFaultHandler | None" = None
    ) -> None:
        """Start all relay pollers; all inbound frames are dispatched to ``handler``."""
        for relay in self._relays:
            await relay.listen(handler, on_frame_fault)

    async def close(self) -> None:
        """Close all relay transports."""
        for relay in self._relays:
            await relay.close()


def _strip_envelope(decoded: dict) -> dict:
    """Drop the reserved ``_relay_*`` correlation keys, leaving the carried map.

    The whole :data:`RELAY_ENVELOPE_PREFIX` namespace is stripped (see the module
    docstring's reservation note), so the result is byte-for-byte the map the TCP
    transport would have delivered to the handler.
    """
    return {
        k: v for k, v in decoded.items() if not k.startswith(RELAY_ENVELOPE_PREFIX)
    }
