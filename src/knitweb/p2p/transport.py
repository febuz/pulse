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

from .wire import read_frame, write_frame

__all__ = [
    "PeerAddress",
    "FrameHandler",
    "Transport",
    "Dialer",
    "TcpTransport",
    "parse_peer_uri",
]

# A frame handler takes one decoded request map and returns the response map.
FrameHandler = Callable[[dict], Awaitable[dict]]


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

    async def listen(self, handler: FrameHandler) -> None:
        """Begin accepting inbound requests, dispatching each to ``handler``.

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


class TcpTransport:
    """Direct asyncio TCP transport — the original node behavior, extracted.

    ``dial`` opens a one-shot connection, writes the request frame, reads the
    response frame, and closes (matching the prior
    ``asyncio.open_connection`` round-trip). ``listen`` runs an
    ``asyncio.start_server`` accept loop and hands each request to ``handler``.
    """

    tag = "tcp"

    def __init__(self, *, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self._server: asyncio.AbstractServer | None = None

    async def dial(self, peer: PeerAddress, request: dict) -> dict:
        reader, writer = await asyncio.open_connection(peer.host, peer.port)
        try:
            await write_frame(writer, request)
            return await read_frame(reader)
        finally:
            writer.close()
            await writer.wait_closed()

    async def listen(self, handler: FrameHandler) -> None:
        if self._server is not None:
            return

        async def _accept(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                request = await read_frame(reader)
                response = await handler(request)
                await write_frame(writer, response)
            finally:
                writer.close()
                await writer.wait_closed()

        self._server = await asyncio.start_server(_accept, self.host, self.port)
        sock = self._server.sockets[0]
        self.host, self.port = sock.getsockname()[:2]

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    def local_address(self) -> PeerAddress:
        return PeerAddress(host=self.host, port=self.port, transport="tcp")
