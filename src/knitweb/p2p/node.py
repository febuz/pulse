"""Stdlib asyncio P2P node for Phase 3 feed sync and Knit handshakes.

The full roadmap still leaves room for a py-libp2p backend once it is installable
in a sanctioned environment. This module is the proofable MVP available today:
static peers, canonical-CBOR frames, signed feed replication, conflict quarantine,
and a two-party Knit exchange over localhost.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import crypto
from ..fabric.feed import (
    Feed,
    FeedHead,
    check_conflict,
    check_prefix_conflict,
    verify_entries,
)
from ..fabric.feed_multiproof import prove_range, verify_range_multiproof
from ..ledger import knitweb as kw
from ..ledger.knit import Knit
from ..ledger.node import AccountNode
from .transport import Dialer, PeerAddress, TcpTransport, Transport
from .wire import (
    WireError,
    feed_head_from_record,
    feed_head_to_record,
    knit_from_record,
    knit_to_record,
    multiproof_from_record,
    multiproof_to_record,
)

__all__ = [
    "PeerAddress",
    "StaticPeerBook",
    "FeedReplica",
    "FeedSlice",
    "P2PError",
    "FeedConflictError",
    "AsyncioP2PNode",
]


class P2PError(RuntimeError):
    """Raised when the peer protocol refuses or cannot complete a request."""


class FeedConflictError(P2PError):
    """Raised when two signed feed histories prove equivocation."""


class StaticPeerBook:
    """Tiny static-peer registry; a real DHT can replace it behind this shape."""

    def __init__(self) -> None:
        self._peers: dict[str, PeerAddress] = {}

    def add(self, name: str, peer: PeerAddress) -> None:
        self._peers[name] = peer

    def get(self, name: str) -> PeerAddress:
        return self._peers[name]

    def all(self) -> dict[str, PeerAddress]:
        return dict(self._peers)


@dataclass(frozen=True)
class FeedReplica:
    """A verified remote feed state."""

    head: FeedHead
    entries: list[dict]


@dataclass(frozen=True)
class FeedSlice:
    """A verified contiguous slice ``[start, start+len(entries))`` of a signed feed.

    The slice is authenticated against the feed's *full* signed ``head`` by a range
    multiproof, so a peer trusts the entries exactly as much as the feed author
    without holding (or transferring) the whole log.
    """

    head: FeedHead
    start: int
    entries: list[dict]


class AsyncioP2PNode:
    """One Knitweb peer speaking the Phase 3 asyncio wire protocol."""

    def __init__(
        self,
        *,
        account: AccountNode | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        transport: Transport | None = None,
        extra_transports: list[Transport] | None = None,
    ) -> None:
        self.account = account
        self.feeds: dict[str, Feed] = {}
        self.replicas: dict[str, FeedReplica] = {}
        self.frozen_feeds: dict[str, str] = {}
        self._seen_incoming_nonces: set[tuple[str, int, int]] = set()
        # The listening transport (TCP by default; pass a RelayTransport to be
        # reachable from behind NAT). Outbound dials are routed by the Dialer
        # according to each PeerAddress's transport tag, so a node can hold a mix
        # of tcp:// and relay:// peers at once.
        self.transport: Transport = transport or TcpTransport(host=host, port=port)
        self.peerbook = StaticPeerBook()
        self.dialer = Dialer()
        for tr in [self.transport, *(extra_transports or [])]:
            self.dialer.register(tr)
        self._listening = False

    # -- server lifecycle -------------------------------------------------

    @property
    def address(self) -> PeerAddress:
        return self.transport.local_address()

    @property
    def host(self) -> str:
        return self.transport.local_address().host

    @property
    def port(self) -> int:
        return self.transport.local_address().port

    def add_transport(self, transport: Transport) -> None:
        """Register an extra outbound transport (e.g. relay:// dialing)."""
        self.dialer.register(transport)

    async def start(self) -> None:
        """Start listening for one-request-per-connection peer calls."""
        if self._listening:
            return
        await self.transport.listen(self._dispatch)
        self._listening = True

    async def stop(self) -> None:
        """Stop the listener."""
        if not self._listening:
            return
        await self.transport.close()
        self._listening = False

    async def __aenter__(self) -> "AsyncioP2PNode":
        await self.start()
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.stop()

    # -- local state ------------------------------------------------------

    def add_feed(self, feed: Feed) -> None:
        """Publish a local feed for peers to replicate."""
        self.feeds[feed.feed] = feed

    def _owned_or_replicated(self, feed_id: str) -> FeedReplica | None:
        if feed_id in self.feeds:
            feed = self.feeds[feed_id]
            return FeedReplica(head=feed.head(), entries=feed.entries)
        return self.replicas.get(feed_id)

    # -- feed sync --------------------------------------------------------

    async def sync_feed(self, peer: PeerAddress, feed_id: str) -> FeedReplica:
        """Fetch and verify a whole feed from ``peer``.

        Requests every entry (``count = null``). The full entry set is checked
        against the signed head via :func:`verify_entries`; ``merkle_nodes`` is
        empty because no slicing is needed when the reader holds the whole log.
        """
        msg = await self._roundtrip(peer, {
            "kind": "feed-request",
            "feed": feed_id,
            "start": 0,
            "count": None,
        })
        if msg.get("kind") == "error":
            raise P2PError(f"{msg.get('code')}: {msg.get('message')}")
        if msg.get("kind") != "feed-data":
            raise P2PError(f"unexpected response kind: {msg.get('kind')!r}")
        replica = self._replica_from_message(msg)
        return self._merge_replica(replica)

    async def sync_feed_range(
        self, peer: PeerAddress, feed_id: str, start: int, count: int
    ) -> FeedSlice:
        """Fetch and verify a contiguous slice ``[start, start+count)`` from ``peer``.

        Transfers ``count`` entries plus an O(count + log n) range multiproof
        instead of the whole log, then verifies the slice against the feed's signed
        head with :func:`verify_range_multiproof`. This is partial replication for
        large feeds: a peer can authenticate any window without the full history.
        """
        if not isinstance(start, int) or isinstance(start, bool):
            raise P2PError("start must be int")
        if not isinstance(count, int) or isinstance(count, bool):
            raise P2PError("count must be int")
        if start < 0:
            raise P2PError("start must be non-negative")
        if count <= 0:
            raise P2PError("count must be positive")
        msg = await self._roundtrip(peer, {
            "kind": "feed-request",
            "feed": feed_id,
            "start": start,
            "count": count,
        })
        if msg.get("kind") == "error":
            raise P2PError(f"{msg.get('code')}: {msg.get('message')}")
        if msg.get("kind") != "feed-data":
            raise P2PError(f"unexpected response kind: {msg.get('kind')!r}")
        return self._slice_from_message(msg, start, count)

    def _replica_from_message(self, msg: dict) -> FeedReplica:
        head = feed_head_from_record(msg.get("head"))
        entries = msg.get("entries")
        if not isinstance(entries, list):
            raise P2PError("feed-data entries must be a list")
        if msg.get("merkle_nodes") != []:
            raise P2PError("full feed-data must not carry a partial proof")
        if not verify_entries(head, entries):
            raise P2PError("feed entries do not match signed head")
        return FeedReplica(head=head, entries=entries)

    def _slice_from_message(self, msg: dict, start: int, count: int) -> FeedSlice:
        head = feed_head_from_record(msg.get("head"))
        entries = msg.get("entries")
        if not isinstance(entries, list):
            raise P2PError("feed-data entries must be a list")
        if len(entries) != count:
            raise P2PError("peer returned a different number of entries than requested")
        proof = multiproof_from_record(msg.get("merkle_nodes"))
        if proof.start != start or proof.count != count:
            raise P2PError("multiproof does not cover the requested range")
        # The multiproof reconstructs the *signed* root from the slice + carried
        # siblings, so a verified slice is trusted as much as the feed author —
        # without ever holding the full log (O(count + log n), not O(length)).
        if not verify_range_multiproof(head, entries, proof):
            raise P2PError("feed slice does not match signed head")
        return FeedSlice(head=head, start=start, entries=entries)

    def _merge_replica(self, incoming: FeedReplica) -> FeedReplica:
        feed_id = incoming.head.feed
        if feed_id in self.frozen_feeds:
            raise FeedConflictError(self.frozen_feeds[feed_id])

        current = self._owned_or_replicated(feed_id)
        if current is not None:
            reason = self._conflict_reason(current, incoming)
            if reason is not None:
                self.frozen_feeds[feed_id] = reason
                raise FeedConflictError(reason)
            if feed_id in self.feeds:
                return current
            if incoming.head.fork < current.head.fork:
                return current
            if (
                incoming.head.fork == current.head.fork
                and incoming.head.length < current.head.length
            ):
                return current

        self.replicas[feed_id] = incoming
        return incoming

    @staticmethod
    def _conflict_reason(a: FeedReplica, b: FeedReplica) -> str | None:
        if check_conflict(a.head, b.head):
            return "same feed signed two roots at the same length/fork"
        if a.head.length <= b.head.length:
            if check_prefix_conflict(a.head, b.head, b.entries):
                return "longer feed rewrote an already-signed prefix"
        else:
            if check_prefix_conflict(b.head, a.head, a.entries):
                return "shorter feed conflicts with the stored prefix"
        return None

    def _serve_feed(self, msg: dict) -> dict:
        feed_id = msg.get("feed")
        if not isinstance(feed_id, str):
            return self._error("bad-request", "feed must be str")
        start = msg.get("start")
        count = msg.get("count")
        if not isinstance(start, int) or isinstance(start, bool):
            return self._error("bad-request", "start must be int")
        if not (count is None or (isinstance(count, int) and not isinstance(count, bool))):
            return self._error("bad-request", "count must be int or null")
        if feed_id in self.frozen_feeds:
            return self._error("frozen-feed", self.frozen_feeds[feed_id])
        replica = self._owned_or_replicated(feed_id)
        if replica is None:
            return self._error("unknown-feed", feed_id)

        head = replica.head
        if count is None:
            # Whole-feed request: only the canonical full log (start at 0) is served
            # this way; the entries verify directly against the signed head.
            if start != 0:
                return self._error(
                    "unsupported-range", "whole-feed request must start at 0"
                )
            return {
                "kind": "feed-data",
                "head": feed_head_to_record(head),
                "entries": replica.entries,
                "merkle_nodes": [],
            }

        # Range request: serve the slice plus a shared-path multiproof so the peer
        # can verify it against the signed head without the full log.
        if count <= 0:
            return self._error("bad-request", "count must be positive")
        if start < 0 or start + count > head.length:
            return self._error(
                "unsupported-range",
                f"range [{start},{start + count}) out of bounds for length {head.length}",
            )
        proof = prove_range(replica.entries, start, count)
        return {
            "kind": "feed-data",
            "head": feed_head_to_record(head),
            "entries": replica.entries[start : start + count],
            "merkle_nodes": multiproof_to_record(proof),
        }

    # -- Knit handshake ---------------------------------------------------

    async def send_knit(
        self,
        peer: PeerAddress,
        to_pub: str,
        symbol: str,
        amount: int,
        timestamp: int,
    ) -> Knit:
        """Propose, finalize, and locally apply a Knit with ``peer``."""
        if self.account is None:
            raise P2PError("node has no account")
        if amount <= 0:
            raise P2PError("amount must be positive")
        if self.account.balance(symbol) < amount:
            raise P2PError("overdraft: local balance is too low")
        proposed = self.account.propose(to_pub, symbol, amount, timestamp)
        msg = await self._roundtrip(peer, {
            "kind": "knit-proposal",
            "knit": knit_to_record(proposed),
        })
        if msg.get("kind") == "error":
            raise P2PError(f"{msg.get('code')}: {msg.get('message')}")
        if msg.get("kind") != "knit-accepted":
            raise P2PError(f"unexpected response kind: {msg.get('kind')!r}")
        signed = knit_from_record(msg.get("knit"))
        ok, reason = kw.validate_knit(signed, self.account.network)
        if not ok:
            raise P2PError(f"receiver returned invalid knit: {reason}")
        final = await self._roundtrip(peer, {
            "kind": "knit-finalize",
            "knit": knit_to_record(signed),
        })
        if final.get("kind") == "error":
            raise P2PError(f"{final.get('code')}: {final.get('message')}")
        if final.get("kind") != "knit-finalized":
            raise P2PError(f"unexpected response kind: {final.get('kind')!r}")
        finalized = knit_from_record(final.get("knit"))
        if finalized != signed:
            raise P2PError("peer finalized a different knit")
        self.account.apply_sent(finalized)
        return finalized

    def _handle_knit_proposal(self, msg: dict) -> dict:
        if self.account is None:
            return self._error("no-account", "peer cannot accept knits")
        try:
            knit = knit_from_record(msg.get("knit"))
            self._validate_incoming_proposal(knit)
            signed = self.account.accept(knit)
            ok, reason = kw.validate_knit(signed, self.account.network)
            if not ok:
                return self._error("invalid-knit", reason)
            return {"kind": "knit-accepted", "knit": knit_to_record(signed)}
        except (ValueError, WireError) as exc:
            return self._error("invalid-knit", str(exc))

    def _handle_knit_finalize(self, msg: dict) -> dict:
        if self.account is None:
            return self._error("no-account", "peer cannot finalize knits")
        try:
            knit = knit_from_record(msg.get("knit"))
            if knit.to_pub != self.account.pub:
                return self._error("invalid-knit", "knit is not addressed to this peer")
            ok, reason = kw.validate_knit(knit, self.account.network)
            if not ok:
                return self._error("invalid-knit", reason)
            nonce_key = (knit.from_pub, knit.network, knit.from_nonce)
            if nonce_key in self._seen_incoming_nonces:
                return self._error("duplicate-nonce", "sender nonce already finalized")
            self.account.apply_received(knit)
            self._seen_incoming_nonces.add(nonce_key)
            return {"kind": "knit-finalized", "knit": knit_to_record(knit)}
        except (ValueError, WireError) as exc:
            return self._error("invalid-knit", str(exc))

    def _validate_incoming_proposal(self, knit: Knit) -> None:
        if knit.to_pub != self.account.pub:
            raise ValueError("knit is not addressed to this peer")
        if knit.network != self.account.network:
            raise ValueError(
                f"wrong network: knit {knit.network} != expected {self.account.network}"
            )
        if knit.to_sig is not None:
            raise ValueError("proposal already carries receiver signature")
        if not knit.from_sig:
            raise ValueError("proposal is missing sender signature")
        if knit.amount <= 0:
            raise ValueError("amount must be positive")
        if knit.from_pub == knit.to_pub:
            raise ValueError("sender and receiver must differ")
        if not crypto.verify(knit.from_pub, knit.signing_bytes, knit.from_sig):
            raise ValueError("invalid sender signature")

    # -- transport --------------------------------------------------------

    async def _roundtrip(self, peer: PeerAddress, msg: dict) -> dict:
        # The Dialer routes by peer.transport, so a tcp:// peer uses TcpTransport
        # and a relay:// peer uses RelayTransport — identical frame bytes either
        # way (the carrier never re-encodes the canonical-CBOR payload).
        return await self.dialer.dial(peer, msg)

    async def _dispatch(self, msg: dict) -> dict:
        """Transport-agnostic request handler: request map in, response map out."""
        try:
            kind = msg.get("kind")
            if kind == "feed-request":
                return self._serve_feed(msg)
            if kind == "knit-proposal":
                return self._handle_knit_proposal(msg)
            if kind == "knit-finalize":
                return self._handle_knit_finalize(msg)
            return self._error("unknown-kind", str(kind))
        except (P2PError, WireError, ValueError) as exc:
            return self._error("bad-request", str(exc))

    @staticmethod
    def _error(code: str, message: str) -> dict:
        return {"kind": "error", "code": code, "message": message}
