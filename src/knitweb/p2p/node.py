"""Stdlib asyncio P2P node for Phase 3 feed sync and Knit handshakes.

The full roadmap still leaves room for a py-libp2p backend once it is installable
in a sanctioned environment. This module is the proofable MVP available today:
static peers, canonical-CBOR frames, signed feed replication, conflict quarantine,
and a two-party Knit exchange over localhost.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..core import crypto
from ..fabric.equivocation import (
    EquivocationReport,
    prove_equivocation,
    verify_equivocation_report,
)
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
from .discovery import (
    PEER_EXCHANGE_KIND,
    bootstrap_round,
    directory_from_peerbook,
    handle_peer_exchange,
    peer_exchange_message,
)
from .policing import police_equivocation_report
from .relay import ENVELOPE_PEER_KEY
from .reputation import Offense, PeerReputation
from .transport import Dialer, PeerAddress, TcpTransport, Transport
from .wire import (
    WireError,
    equivocation_report_from_record,
    equivocation_report_to_record,
    feed_head_from_record,
    feed_head_to_record,
    knit_from_record,
    knit_to_record,
    multiproof_from_record,
    multiproof_to_record,
    read_frame,
    write_frame,
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
        # The Byzantine-consequence ledger this node owns: detected/proven
        # misbehavior is funnelled here, and the per-connection _handle_peer
        # wrapper refuses banned peers before _dispatch ever sees a request.
        self.reputation = PeerReputation()
        # Equivocation reports this node has built or ingested, keyed by feed,
        # so they can be re-gossiped as the additive ``equivocation-report`` kind.
        self.equivocation_reports: dict[str, EquivocationReport] = {}
        self._seen_incoming_nonces: set[tuple[str, int, int]] = set()
        # The listening transport (TCP by default; pass a RelayTransport to be
        # reachable from behind NAT). Outbound dials are routed by the Dialer
        # according to each PeerAddress's transport tag, so a node can hold a mix
        # of tcp:// and relay:// peers at once.
        self.transport: Transport = transport or TcpTransport(host=host, port=port)
        self.peerbook = StaticPeerBook()
        # The growing peer set: a PEX directory seeded from the hand-configured
        # StaticPeerBook. Peers learned over ``peer-exchange`` are merged here so the
        # node discovers endpoints beyond the ones typed in. Seeded from whatever the
        # peerbook holds at construction; ``bootstrap_peers`` re-seeds before dialing
        # so peers added after __init__ are included.
        self.peers = directory_from_peerbook(self.peerbook)
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
        try:
            replica = self._replica_from_message(msg)
        except P2PError:
            # The peer served entries that don't match the signed head, or an
            # unsupported/forged proof: a stale-or-forged-proof offense.
            self.reputation.penalize(
                f"{peer.host}:{peer.port}", Offense.STALE_OR_FORGED_PROOF
            )
            raise
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
                self._consequence_on_conflict(current.head, incoming.head, reason)
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

    def _consequence_on_conflict(
        self, head_a: FeedHead, head_b: FeedHead, reason: str
    ) -> None:
        """Close the detect→prove→consequence loop for a detected feed conflict.

        If the two heads are a *check_conflict*-style equivocation (same
        ``(length, fork)``, different root), build a portable
        :class:`EquivocationReport`, file it for gossip, and feed it through
        :func:`police_equivocation_report` so the offending feed key is banned in
        this node's reputation ledger. A prefix conflict (a rewrite of an already
        signed prefix) is not a one-position double-sign, so it carries the graded
        ``FEED_CONFLICT`` penalty directly. The offending identity is the feed key.
        """
        report = prove_equivocation(head_a, head_b, reporter=self._reporter_id)
        if report is not None:
            self.equivocation_reports[report.feed] = report
            police_equivocation_report(self.reputation, report)
            return
        # Prefix conflict: provably bad, but not a single-position equivocation.
        self.reputation.penalize(head_a.feed, Offense.FEED_CONFLICT)

    @property
    def _reporter_id(self) -> str:
        """Identity this node stamps onto equivocation reports it authors."""
        if self.account is not None:
            return self.account.pub
        return f"{self.host}:{self.port}"

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

    # -- peer discovery (PEX) ---------------------------------------------

    def _handle_peer_exchange(self, msg: dict) -> dict:
        """Inbound ``peer-exchange``: merge the sender's peers, reply with ours.

        Carrier-agnostic — this is reached from :meth:`_dispatch` whether the request
        arrived over a TCP stream or a relay mailbox, and the reply frame bytes are
        identical either way. Pure logic lives in :func:`handle_peer_exchange`.
        """
        try:
            return handle_peer_exchange(self.peers, msg)
        except (ValueError, WireError) as exc:
            return self._error("bad-peer-exchange", str(exc))

    async def bootstrap_peers(self, seeds: "list[PeerAddress] | None" = None) -> int:
        """Dial seed peers, exchange known peers, and grow the directory.

        Re-seeds the directory from the current ``StaticPeerBook`` (so peers added
        after construction are included), then runs one PEX round against each seed
        (the peerbook's peers by default): send our share, merge the reply. Returns
        the total number of *newly* learned peers across all seeds.

        Routes through the shared :class:`Dialer`, so a ``tcp://`` seed and a
        ``relay://`` seed are dialed over their own carriers with identical frame
        bytes — discovery is carrier-independent. A seed that errors or returns a
        non-PEX reply is skipped without aborting the whole round.
        """
        self.peers.merge(list(self.peerbook.all().values()))
        targets = seeds if seeds is not None else list(self.peerbook.all().values())
        learned = 0
        for seed in targets:
            request = peer_exchange_message(self.peers)
            try:
                reply = await self._roundtrip(seed, request)
            except (P2PError, WireError, OSError):
                # An unreachable or misbehaving seed must not sink the bootstrap.
                continue
            if reply.get("kind") != PEER_EXCHANGE_KIND:
                continue
            try:
                learned += bootstrap_round(self.peers, reply)
            except (ValueError, WireError):
                continue
        return learned

    # -- equivocation gossip ----------------------------------------------

    async def gossip_equivocation_report(
        self, peer: PeerAddress, report: EquivocationReport
    ) -> bool:
        """Send a proven equivocation report to ``peer``; return its accept ack.

        The receiver re-verifies the report from its own bytes before acting, so a
        forged or tampered report is rejected with no consequence on its end.
        """
        msg = await self._roundtrip(
            peer,
            {
                "kind": "equivocation-report",
                "report": equivocation_report_to_record(report),
            },
        )
        if msg.get("kind") == "error":
            raise P2PError(f"{msg.get('code')}: {msg.get('message')}")
        return msg.get("kind") == "equivocation-ack"

    def _handle_equivocation_report(self, msg: dict) -> dict:
        """Ingest a gossiped equivocation report: verify, ban, and freeze locally."""
        try:
            report = equivocation_report_from_record(msg.get("report"))
        except WireError as exc:
            return self._error("bad-report", str(exc))
        if not verify_equivocation_report(report):
            return self._error("unverified-report", "report does not prove equivocation")
        police_equivocation_report(self.reputation, report)
        self.equivocation_reports[report.feed] = report
        self.frozen_feeds.setdefault(
            report.feed, "ingested a verified equivocation report for this feed"
        )
        return {"kind": "equivocation-ack", "feed": report.feed}

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
        """Transport-agnostic request handler: request map in, response map out.

        This is the handler the listening :class:`Transport` feeds every decoded
        request to (TCP accept loop or relay mailbox poll alike). The TCP stream
        applies its banned-peer gate and frame-level misbehavior penalties in
        :meth:`_handle_peer` (a socket peer key and a malformed-frame penalty are
        concerns the carrier owns before a request is ever decoded). The relay
        carrier has no socket, so it stamps the sender's identity onto the request
        as a transport-envelope key (:data:`ENVELOPE_PEER_KEY`); here we honour
        the *same* ban gate before any work, then drop the key so it never reaches
        signed/business logic.
        """
        peer_id = msg.pop(ENVELOPE_PEER_KEY, None)
        if isinstance(peer_id, str) and self.reputation.is_banned(peer_id):
            return self._error("banned", "peer is banned")
        try:
            kind = msg.get("kind")
            if kind == "feed-request":
                return self._serve_feed(msg)
            if kind == "knit-proposal":
                return self._handle_knit_proposal(msg)
            if kind == "knit-finalize":
                return self._handle_knit_finalize(msg)
            if kind == "equivocation-report":
                return self._handle_equivocation_report(msg)
            if kind == PEER_EXCHANGE_KIND:
                return self._handle_peer_exchange(msg)
            return self._error("unknown-kind", str(kind))
        except (P2PError, WireError, ValueError) as exc:
            return self._error("bad-request", str(exc))

    @staticmethod
    def _peer_id(writer: asyncio.StreamWriter) -> str:
        """A stable reputation key for the connected peer (its remote endpoint)."""
        peername = writer.get_extra_info("peername")
        if isinstance(peername, tuple) and len(peername) >= 2:
            return f"{peername[0]}:{peername[1]}"
        return str(peername)

    async def _handle_peer(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Per-connection reputation wrapper over a single TCP stream.

        The Byzantine-consequence loop's connection-level half: it refuses a
        banned peer before any work, penalizes malformed/oversized frames, then
        delegates routing of the decoded request to the shared, carrier-agnostic
        :meth:`_dispatch`. (The relay carrier funnels into ``_dispatch`` directly;
        peer-keyed banning there is a follow-up, as a mailbox has no socket peer.)
        """
        peer_id = self._peer_id(writer)
        try:
            # Reputation gate: a peer the node has banned (a proven equivocator,
            # an accumulated bad-proof seeder, …) is refused and disconnected.
            if self.reputation.is_banned(peer_id):
                await write_frame(writer, self._error("banned", "peer is banned"))
                return
            try:
                msg = await read_frame(reader)
            except WireError as exc:
                # Malformed or oversized wire frame → graded misbehavior points.
                offense = (
                    Offense.OVERSIZED_FRAME
                    if "too large" in str(exc)
                    else Offense.MALFORMED_FRAME
                )
                self.reputation.penalize(peer_id, offense)
                await write_frame(writer, self._error("bad-frame", str(exc)))
                return
            out = await self._dispatch(msg)
            await write_frame(writer, out)
        except (P2PError, ValueError) as exc:
            await write_frame(writer, self._error("bad-request", str(exc)))
        finally:
            writer.close()
            await writer.wait_closed()

    @staticmethod
    def _error(code: str, message: str) -> dict:
        return {"kind": "error", "code": code, "message": message}
