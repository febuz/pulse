"""Pluggable transport abstraction for the Knitweb p2p layer.

Real-world Knitweb nodes are mostly behind NAT/firewalls: inbound raw TCP is
dropped, so a node cannot simply ``asyncio.start_server`` and expect peers to
reach it. To converge anyway, the wire layer is split from the *carrier*:

  * A :class:`Transport` is the carrier. It knows how to **dial** a peer (open a
    one-shot request/response channel and return the reply) and how to **listen**
    (accept inbound requests and feed them to a handler). It never inspects the
    payload — frames stay byte-identical canonical CBOR (:mod:`knitweb.p2p.wire`),
    so no signed-record bytes ever change as a message crosses a carrier.

  * A :class:`PeerAddress` now carries a ``transport`` tag (``"tcp"`` /
    ``"relay"``) plus an opaque ``params`` map. A :class:`Dialer` routes each
    dial to the transport that owns that tag, so a single node can hold a mix of
    directly-reachable TCP peers and NAT'd relay-mailbox peers at once.

Two transports ship today:

  * :class:`TcpTransport` — the original ``asyncio.open_connection`` /
    ``asyncio.start_server`` behavior, extracted verbatim.
  * ``RelayTransport`` (in :mod:`knitweb.p2p.relay`) — an HTTP client for the live
    PHP store-and-forward relay: a firewalled node registers a mailbox and polls
    ``fetch`` instead of accepting inbound TCP.

A future **hole-punch** transport (e.g. STUN-assisted rendezvous then a direct
UDP/TCP session) slots in behind the same :class:`Transport` protocol — see the
``HOLE-PUNCH SEAM`` note on :class:`Transport.listen`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol, runtime_checkable

from .wire import WireError, read_frame, write_frame

__all__ = [
    "PeerAddress",
    "FrameHandler",
    "FrameFaultHandler",
    "Transport",
    "Dialer",
    "TcpTransport",
    "DEFAULT_MAX_INBOUND",
    "DEFAULT_READ_TIMEOUT_S",
    "DEFAULT_MAX_OPEN_CONNS",
    "DEFAULT_ACCEPT_QUEUE_TIMEOUT_S",
    "TCP_PEER_PREFIX",
    "tcp_peer_id",
    "parse_peer_uri",
]

# A frame handler takes one decoded request map and returns the response map.
FrameHandler = Callable[[dict], Awaitable[dict]]

# A frame-fault handler turns a carrier-level read failure (a malformed or
# oversized frame) for a positively-identified peer into a response map — letting
# the node layer record the matching reputation penalty without the carrier ever
# owning reputation itself. Takes ``(peer_id, WireError)`` and returns the error
# map to write back. Optional: a carrier that cannot identify a sender (or a node
# that does not supply one) simply omits it and a faulted frame is dropped.
FrameFaultHandler = Callable[[str, "WireError"], dict]

# Reputation-key prefix for a TCP sender, derived from its remote IP. It mirrors
# the ``relay:`` prefix the relay carrier uses (:func:`knitweb.p2p.relay.relay_peer_id`)
# so the two address spaces never collide in the reputation ledger.
TCP_PEER_PREFIX = "tcp:"


def tcp_peer_id(host: str) -> str:
    """Stable reputation key for a TCP sender, derived from its remote IP only.

    The id deliberately drops the *port*: a peer's source port is ephemeral and
    changes every reconnect, so keying on ``host:port`` would mint a fresh
    identity per connection and a repeat forger could never accrue a ban. Keying
    on the remote IP alone makes misbehavior stick across connections. It is the
    most stable identity a raw socket exposes; a NAT/proxy can share an IP across
    honest peers, so the ban thresholds stay graded (one forgery never bans) to
    keep an honest peer behind a shared IP from being collateral-banned by a
    co-located attacker before its own honest traffic is even seen.
    """
    return f"{TCP_PEER_PREFIX}{host}"


@dataclass(frozen=True)
class PeerAddress:
    """A peer endpoint, tagged with the transport that can reach it.

    ``transport`` selects the carrier (``"tcp"`` for a directly-dialable socket,
    ``"relay"`` for an HTTP store-and-forward mailbox). ``host``/``port`` keep the
    classic TCP shape so existing call sites construct ``PeerAddress(host, port)``
    unchanged; relay (and future) transports stash their routing in ``params``.

    A canonical string form is available via :meth:`uri` /
    :func:`parse_peer_uri` (``tcp://host:port`` or ``relay://mailbox@base_url``).
    """

    host: str = ""
    port: int = 0
    transport: str = "tcp"
    params: dict[str, str] = field(default_factory=dict)

    def __hash__(self) -> int:
        # Frozen + hashable (peers live in sets/dicts in the discovery layer).
        # The params dict is folded in via its sorted items so two equal
        # addresses hash equal without depending on insertion order.
        return hash(
            (self.host, self.port, self.transport, tuple(sorted(self.params.items())))
        )

    def uri(self) -> str:
        if self.transport == "tcp":
            return f"tcp://{self.host}:{self.port}"
        if self.transport == "relay":
            mailbox = self.params.get("mailbox", "")
            base = self.params.get("base_url", "")
            return f"relay://{mailbox}@{base}"
        # Generic fallback so unknown transports still round-trip to a string.
        joined = ",".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.transport}://{joined}"


def parse_peer_uri(uri: str) -> PeerAddress:
    """Parse a ``tcp://host:port`` or ``relay://mailbox@base_url`` peer URI."""
    scheme, _, rest = uri.partition("://")
    if not _:
        raise ValueError(f"peer uri missing scheme: {uri!r}")
    if scheme == "tcp":
        host, _, port = rest.rpartition(":")
        if not host:
            raise ValueError(f"tcp peer uri must be tcp://host:port: {uri!r}")
        return PeerAddress(host=host, port=int(port), transport="tcp")
    if scheme == "relay":
        mailbox, sep, base = rest.partition("@")
        if not sep or not mailbox or not base:
            raise ValueError(
                f"relay peer uri must be relay://mailbox@base_url: {uri!r}"
            )
        return PeerAddress(
            transport="relay", params={"mailbox": mailbox, "base_url": base}
        )
    raise ValueError(f"unknown peer transport scheme: {scheme!r}")


@runtime_checkable
class Transport(Protocol):
    """A pluggable p2p carrier: dial a peer, or listen for inbound requests.

    Implementations move opaque, length-prefixed canonical-CBOR frames and never
    interpret them, so the byte-identity of signed records is preserved end to
    end. Both methods speak in already-decoded maps purely as a convenience — the
    framing/parsing (:mod:`knitweb.p2p.wire`) is shared, the *carriage* is what
    differs between transports.
    """

    #: The ``PeerAddress.transport`` tag this transport dials.
    tag: str

    async def dial(self, peer: PeerAddress, request: dict) -> dict:
        """Send one ``request`` map to ``peer`` and return its response map."""
        ...

    async def listen(
        self, handler: FrameHandler, on_frame_fault: "FrameFaultHandler | None" = None
    ) -> None:
        """Begin accepting inbound requests, dispatching each to ``handler``.

        A carrier that can positively identify the sender stamps that identity
        onto the decoded request as the transport-envelope key
        (:data:`knitweb.p2p.relay.ENVELOPE_PEER_KEY`) before calling ``handler``,
        so the carrier-agnostic dispatch can apply the same reputation/ban gate
        uniformly. ``on_frame_fault`` is an optional hook the carrier calls when an
        *identified* peer sends a malformed/oversized frame (one that never decodes
        into a request map), letting the node record the matching reputation
        penalty; it returns the error map to write back. A carrier with no stable
        peer identity (or a node that supplies no hook) simply drops a faulted
        frame, exactly as before.

        HOLE-PUNCH SEAM
        ---------------
        A hole-punch transport implements this the same way TCP does — the only
        difference is *how the listening socket becomes reachable*. Before
        binding, such a transport would (1) contact a rendezvous/STUN spider to
        learn its public ``host:port``, (2) coordinate simultaneous-open with the
        dialing peer, then (3) hand the resulting connected socket to the same
        ``handler`` loop below. Nothing in this protocol — nor in the node layer
        that consumes it — needs to change to add that transport.
        """
        ...

    async def close(self) -> None:
        """Release any listening/polling resources. Idempotent."""
        ...

    def local_address(self) -> PeerAddress:
        """The address peers should dial to reach this transport's listener."""
        ...


class Dialer:
    """Routes a dial to the transport that owns the peer's ``transport`` tag.

    A node holds one ``Dialer`` registered with every transport it can speak. A
    peer's :class:`PeerAddress.transport` tag picks the carrier, so TCP peers and
    relay-mailbox peers coexist transparently behind a single ``dial`` call.
    """

    def __init__(self) -> None:
        self._by_tag: dict[str, Transport] = {}

    def register(self, transport: Transport) -> None:
        self._by_tag[transport.tag] = transport

    def transport_for(self, peer: PeerAddress) -> Transport:
        try:
            return self._by_tag[peer.transport]
        except KeyError:
            raise ValueError(
                f"no transport registered for {peer.transport!r} peer"
            ) from None

    async def dial(self, peer: PeerAddress, request: dict) -> dict:
        return await self.transport_for(peer).dial(peer, request)


#: Default ceiling on concurrently-served inbound connections. A deterministic
#: integer (no random, no wall-clock) so behavior is reproducible across nodes;
#: it is a pure carrier-policy knob and never touches a hashed/signed path.
DEFAULT_MAX_INBOUND = 64

#: Default per-connection read deadline, in **integer** seconds, covering the
#: time to read the single request frame. A slow-loris peer that dribbles bytes
#: (or never completes its length-prefixed frame) is dropped at this deadline
#: instead of pinning a connection slot. Like the cap above this is a transport
#: policy timeout, not part of any deterministic/state path.
DEFAULT_READ_TIMEOUT_S = 30

#: Default hard ceiling on concurrently-OPEN inbound sockets (parked + served).
#: ``max_inbound`` bounds only SERVED connections; ``accept_queue_timeout_s``
#: (below, from #173) bounds parked connections by ``(arrival_rate x timeout)``,
#: which under a high arrival rate still TRANSIENTLY exhausts the process fd table
#: (default ulimit ~1024) WITHIN the timeout window, before any deadline fires
#: (#174). This ceiling closes that residual: it is checked at accept time,
#: *before* the connection parks on the serving semaphore, so when concurrently-
#: open inbound sockets already reach it the newest connection is closed
#: immediately and never holds an fd — bounding the live fd count to a hard
#: constant regardless of arrival rate. Validated ``>= max_inbound`` so serving
#: slots are never starved. The default (512 = 8x ``max_inbound``) is generous
#: enough that an honest peer under normal load is never refused. A pure integer
#: carrier-policy knob; never touches a hashed/signed path.
DEFAULT_MAX_OPEN_CONNS = 512

#: Default deadline, in **integer** seconds, for a freshly-accepted inbound
#: connection to obtain a serving slot (#173). Each accepted socket spawns a
#: coroutine that pins an open fd the instant it runs, *before* the
#: ``max_inbound`` semaphore. Without a deadline on that wait a peer can open many
#: idle sockets that never send a frame and park one fd-holding coroutine per
#: socket ahead of the cap, so concurrently-open inbound connections — and thus
#: held fds — are unbounded by ``max_inbound``. A connection that cannot get a
#: slot within this deadline is closed cleanly, bounding parked connections by
#: (arrival_rate x timeout). Composes with ``max_open_conns`` above: the ceiling
#: caps the live fd count during the window, this deadline reclaims an
#: under-ceiling socket that parks without ever doing work. Generous enough that
#: an honest peer under normal load is never spuriously closed; like the caps
#: above it is a deterministic integer carrier-policy knob that never touches a
#: hashed/signed path.
DEFAULT_ACCEPT_QUEUE_TIMEOUT_S = 10


class TcpTransport:
    """Direct asyncio TCP transport — the original node behavior, extracted.

    ``dial`` opens a one-shot connection, writes the request frame, reads the
    response frame, and closes (matching the prior
    ``asyncio.open_connection`` round-trip). ``listen`` runs an
    ``asyncio.start_server`` accept loop and hands each request to ``handler``.

    BACKPRESSURE / DoS GUARDING
    ---------------------------
    The accept loop is bounded by four deterministic, carrier-level knobs so a
    connection flood or a slow-loris peer cannot exhaust the node:

      * ``max_inbound`` — an :class:`asyncio.Semaphore` caps how many inbound
        connections are *served* at once. A peer that opens more than the cap is
        accepted by the kernel but its handler waits for a free slot, and is
        dropped without ever reaching ``handler`` if the connection breaks while
        queued. This bounds per-connection memory and handler concurrency.

      * ``read_timeout_s`` — each connection gets a single-frame read deadline.
        A slow-loris that trickles header/payload bytes (or stalls mid-frame)
        is closed at the deadline, freeing its slot, rather than holding it
        open indefinitely.

      * ``accept_queue_timeout_s`` — the wait for a free slot is itself bounded.
        An accepted connection pins an open fd the instant its coroutine runs,
        *before* the semaphore; without this deadline a peer could open many
        idle sockets that never send a frame and park one fd-holding coroutine
        per socket ahead of the cap, leaving concurrently-open inbound
        connections unbounded by ``max_inbound``. A connection that cannot get a
        slot within the deadline is closed cleanly, bounding parked connections
        (and thus held fds) by ``(arrival_rate x accept_queue_timeout_s)``.

      * ``max_open_conns`` — a hard ceiling on concurrently-OPEN inbound sockets
        (parked + served), checked at accept *before* the connection parks on the
        semaphore. ``accept_queue_timeout_s`` alone bounds parked fds only by
        ``(arrival_rate x timeout)``, so a high enough arrival rate still pins a
        burst of fds for the whole window before any deadline fires. The ceiling
        caps that live fd count to a hard constant regardless of arrival rate:
        over the ceiling the newest connection is closed immediately and never
        holds an fd. An exact integer counter (``_open_conns``) tracks held fds,
        incremented once the coroutine commits to holding the socket and
        decremented in a ``finally`` on every exit path.

    Exactly one request frame is read per connection (the original one-shot
    request/response shape), so a peer cannot pipeline a flood of frames down a
    single accepted socket to amortize past the connection cap. Every knob is an
    integer with no randomness and lives entirely in the carrier — the wire
    framing bytes, and thus every signed record's byte-identity, are untouched.
    """

    tag = "tcp"

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        max_inbound: int = DEFAULT_MAX_INBOUND,
        read_timeout_s: int = DEFAULT_READ_TIMEOUT_S,
        max_open_conns: int = DEFAULT_MAX_OPEN_CONNS,
        accept_queue_timeout_s: int = DEFAULT_ACCEPT_QUEUE_TIMEOUT_S,
    ) -> None:
        if max_inbound < 1:
            raise ValueError("max_inbound must be a positive integer")
        if read_timeout_s < 1:
            raise ValueError("read_timeout_s must be a positive integer")
        if accept_queue_timeout_s < 1:
            raise ValueError("accept_queue_timeout_s must be a positive integer")
        if max_open_conns < max_inbound:
            # The open ceiling must leave room for every serving slot, else served
            # connections would be rejected before they could acquire a slot.
            raise ValueError("max_open_conns must be >= max_inbound")
        self.host = host
        self.port = port
        self.max_inbound = max_inbound
        self.read_timeout_s = read_timeout_s
        self.max_open_conns = max_open_conns
        self.accept_queue_timeout_s = accept_queue_timeout_s
        self._server: asyncio.AbstractServer | None = None
        # Bounds the number of inbound connections served concurrently. Created
        # lazily in ``listen`` so the semaphore binds to the running loop.
        self._inbound: asyncio.Semaphore | None = None
        # Count of inbound connections currently OPEN (accepted but not yet
        # closed), bounded by ``max_open_conns``. Single-threaded asyncio makes
        # the check-and-increment race-free between awaits.
        self._open_conns = 0

    async def dial(self, peer: PeerAddress, request: dict) -> dict:
        reader, writer = await asyncio.open_connection(peer.host, peer.port)
        try:
            await write_frame(writer, request)
            return await read_frame(reader)
        finally:
            writer.close()
            await writer.wait_closed()

    async def listen(
        self, handler: FrameHandler, on_frame_fault: "FrameFaultHandler | None" = None
    ) -> None:
        if self._server is not None:
            return

        # Imported lazily: relay.py imports from this module, so a top-level
        # import here would be circular. ENVELOPE_PEER_KEY is the carrier-agnostic
        # transport-envelope key the dispatch ban gate honours; stamping it makes
        # the live TCP path carry a peer identity exactly like the relay carrier.
        from .relay import ENVELOPE_PEER_KEY

        self._inbound = asyncio.Semaphore(self.max_inbound)

        async def _close(writer: asyncio.StreamWriter) -> None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                # The peer may have already reset the socket; the fd is freed
                # regardless, so a close-time error is not actionable.
                pass

        async def _accept(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            assert self._inbound is not None
            # Hard ceiling on concurrently-OPEN inbound sockets (#174). The
            # ``max_inbound`` semaphore below bounds only how many connections are
            # SERVED; the ``accept_queue_timeout_s`` wait_for (further down, from
            # #173) bounds parked connections only by (arrival_rate x timeout), so
            # a high enough arrival rate still pins a burst of fds for the whole
            # window before any deadline fires. We check the live open-count here,
            # BEFORE parking on the semaphore: over the ceiling we drop the newest
            # immediately, so it can never hold an fd, capping the live fd count to
            # a hard constant regardless of arrival rate. The counter is read and
            # incremented with no intervening await, so under single-threaded
            # asyncio the check-and-increment is race-free.
            if self._open_conns >= self.max_open_conns:
                await _close(writer)
                return
            self._open_conns += 1
            try:
                # Bound how long a connection may park waiting for a serving slot,
                # so even an under-ceiling socket cannot pin its fd indefinitely
                # before doing any work. On timeout it is dropped (slot never
                # acquired ⇒ nothing to release).
                try:
                    await asyncio.wait_for(
                        self._inbound.acquire(),
                        timeout=self.accept_queue_timeout_s,
                    )
                except asyncio.TimeoutError:
                    return
                # The remote IP is the stable reputation identity a raw socket
                # exposes (the port is ephemeral — see :func:`tcp_peer_id`).
                # Stamped onto the request so the carrier-agnostic dispatch applies
                # the same ban gate + signature-offense penalty the relay carrier
                # already gets.
                peer_id = self._socket_peer_id(writer)
                try:
                    # Read exactly one request frame under a deadline: a slow-loris
                    # peer that stalls mid-frame is dropped here, freeing the slot.
                    try:
                        request = await asyncio.wait_for(
                            read_frame(reader), timeout=self.read_timeout_s
                        )
                    except WireError as exc:
                        # A malformed/oversized frame: hand the node a chance to
                        # record the graded reputation penalty (it owns reputation,
                        # not the carrier), then write back its error reply. With no
                        # hook or no peer id the frame is dropped quietly as before.
                        if on_frame_fault is not None and peer_id is not None:
                            await write_frame(writer, on_frame_fault(peer_id, exc))
                        return
                    if peer_id is not None:
                        request[ENVELOPE_PEER_KEY] = peer_id
                    response = await handler(request)
                    await write_frame(writer, response)
                except (asyncio.TimeoutError, OSError, WireError):
                    # Carrier-level drop: a peer that cannot complete its single
                    # frame in time, or whose socket fails, loses its slot quietly
                    # rather than pinning resources.
                    pass
                finally:
                    self._inbound.release()
            finally:
                self._open_conns -= 1
                await _close(writer)

        self._server = await asyncio.start_server(_accept, self.host, self.port)
        sock = self._server.sockets[0]
        self.host, self.port = sock.getsockname()[:2]

    @staticmethod
    def _socket_peer_id(writer: asyncio.StreamWriter) -> str | None:
        """Stable ``tcp:<ip>`` reputation key for the connected peer, or None.

        Returns None when the socket exposes no usable remote IP, so the dispatch
        gate stays a no-op rather than minting a junk identity (and never
        collateral-banning on a meaningless key).
        """
        peername = writer.get_extra_info("peername")
        if isinstance(peername, tuple) and len(peername) >= 1 and peername[0]:
            return tcp_peer_id(str(peername[0]))
        return None

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    def local_address(self) -> PeerAddress:
        return PeerAddress(host=self.host, port=self.port, transport="tcp")
