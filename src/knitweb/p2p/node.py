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
from .anti_entropy import SyncRound
from .base_node import BaseNode
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
from .addrbook import AddrBook
from .discovery import (
    MAX_PEX_INBOUND,
    PEER_EXCHANGE_KIND,
    addrbook_share_message,
    directory_from_peerbook,
    handle_peer_exchange,
    learn_peers,
    peers_from_records,
)
from .policing import police_equivocation_report
from .reputation import Offense
from .transport import PeerAddress, Transport
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


class AsyncioP2PNode(BaseNode):
    """One Knitweb peer speaking the Phase 3 asyncio wire protocol."""

    # The asyncio _dispatch catches the P2P error family (plus wire/value);
    # FabricNode catches its own. Banned-branch frames_out: this node increments.
    _dispatch_errors = (P2PError, WireError, ValueError)
    _count_frames_out_on_banned = True

    def __init__(
        self,
        *,
        account: AccountNode | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        transport: Transport | None = None,
        extra_transports: list[Transport] | None = None,
    ) -> None:
        super().__init__(
            host=host,
            port=port,
            transport=transport,
            extra_transports=extra_transports,
        )
        self.account = account
        self.feeds: dict[str, Feed] = {}
        self.replicas: dict[str, FeedReplica] = {}
        self.frozen_feeds: dict[str, str] = {}
        # Equivocation reports this node has built or ingested, keyed by feed,
        # so they can be re-gossiped as the additive ``equivocation-report`` kind.
        self.equivocation_reports: dict[str, EquivocationReport] = {}
        self._seen_incoming_nonces: set[tuple[str, int, int]] = set()
        self.peerbook = StaticPeerBook()
        # The growing peer set: a PEX directory seeded from the hand-configured
        # StaticPeerBook. Peers learned over ``peer-exchange`` are merged here so the
        # node discovers endpoints beyond the ones typed in. Seeded from whatever the
        # peerbook holds at construction; ``bootstrap_peers`` re-seeds before dialing
        # so peers added after __init__ are included.
        self.peers = directory_from_peerbook(self.peerbook)
        # Eclipse-resistant peer selection (#63 -> live). The flat ``self.peers``
        # above stays the dedup/membership truth; ``self.addrbook`` is the bucketed
        # mirror the node SAMPLES from for dialing and PEX replies, so a PEX flood of
        # one source group cannot crowd an honest minority out of the dial set. It is
        # seeded with the same peerbook addresses (locally minted -> source=None) and
        # grows via ``learn_peers`` on every PEX merge. Construct it with a per-node
        # secret derived from this node's identity: deterministic (same node -> same
        # buckets, reproducible in tests) yet entirely LOCAL — it salts in-memory
        # bucket placement only and never enters a canonical record, a Knit, a
        # signature, or a CID.
        self.addrbook = AddrBook(self._addrbook_secret())
        for peer in self.peers.known():
            self.addrbook.add_new(peer, source=None)

    def _addrbook_secret(self) -> bytes:
        """Per-node, off-record salt for AddrBook bucket placement.

        Derived from the node identity (the account pubkey when keyed, else the
        carrier host:port) via SHA-256 under a fixed local domain tag. This is a
        *local* value: it perturbs which in-memory bucket a learned address lands in
        so an attacker who does not know it cannot pre-compute collisions into one
        victim bucket. It is never hashed into a signed/canonical record — canonical
        bytes and every Knit CID are untouched.
        """
        identity = self._reporter_id  # account pubkey when keyed, else host:port
        return crypto.sha256(b"knitweb-addrbook-secret:v1|" + identity.encode("utf-8"))

    # -- server lifecycle -------------------------------------------------

    def start_anti_entropy(
        self,
        peers: "list[PeerAddress] | None" = None,
        *,
        feeds: "list[str] | None" = None,
        interval: int = 1,
        ceiling: int = 64,
        sleep=None,
    ) -> "asyncio.Task":
        """Launch the self-healing anti-entropy loop as a background task (#44).

        Opt-in: nothing runs until this is called, so a plain ``start()`` keeps
        its existing behaviour. The loop periodically re-bootstraps the peer
        directory and re-pulls every named feed, so a peer that fell out of the
        Web after a disconnect climbs back in and re-converges on reconnect.

        ``peers`` are dialed as PEX seeds each cycle (``None`` uses the configured
        peerbook); ``feeds`` are the feed ids to re-sync from those peers. The
        injected clock defaults to :func:`asyncio.sleep` (the prod clock); a test
        passes a virtual-clock ``sleep`` so convergence is deterministic with no
        real time. The schedule is the integer backoff from #43. The driver
        swallows a failed round, so a refusing/dropped peer never crashes the
        loop. Returns the background task.
        """
        return self._spawn_anti_entropy(
            self._anti_entropy_rounds(peers, feeds),
            interval=interval,
            ceiling=ceiling,
            sleep=sleep,
        )

    def _anti_entropy_rounds(
        self,
        peers: "list[PeerAddress] | None",
        feeds: "list[str] | None",
    ) -> "list[SyncRound]":
        seeds = peers  # None → bootstrap_peers falls back to the peerbook

        async def bootstrap_round() -> int:
            return await self.bootstrap_peers(seeds)

        rounds: list[SyncRound] = [bootstrap_round]
        for feed_id in feeds or []:
            rounds.append(self._make_feed_round(feed_id, seeds))
        return rounds

    def _make_feed_round(
        self, feed_id: str, seeds: "list[PeerAddress] | None"
    ) -> "SyncRound":
        async def feed_round() -> int:
            targets = seeds if seeds is not None else list(self.peerbook.all().values())
            pulled = 0
            for peer in targets:
                before = self._feed_length(feed_id)
                # A refusing/unreachable peer raises here; the driver treats a
                # cycle where *every* round raised as a failure and backs off,
                # but a single bad peer among several never sinks the round.
                try:
                    await self.sync_feed(peer, feed_id)
                except (P2PError, WireError, OSError):
                    continue
                pulled += max(0, self._feed_length(feed_id) - before)
            return pulled

        return feed_round

    def _feed_length(self, feed_id: str) -> int:
        replica = self._owned_or_replicated(feed_id)
        return replica.head.length if replica is not None else 0

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
        before = self._feed_length(replica.head.feed)
        merged = self._merge_replica(replica)
        # sync_pulls counts entries newly woven into the local Web by a catch-up
        # pull — the post-merge length minus what was held before (0 when the
        # pull was a no-op or the replica lost a fork/length tie-break).
        added = merged.head.length - before
        if added > 0:
            self.metrics.incr("sync_pulls", added)
        return merged

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
        identical either way. The merge mirrors into the bucketed :attr:`addrbook`,
        and the reply share is drawn from :func:`addrbook_share_message` (source-group
        diverse, tried-biased) instead of the flat first-``k`` — so what this node
        re-advertises cannot be dominated by a flooded group either. The reputation/
        ban gate (handled in :meth:`_dispatch` before routing) already stripped the
        carrier id, so the advertised peers are learned as locally-heard
        (``source=None``); the source-tagged eclipse defence runs on the outbound
        bootstrap-reply ingest where the advertising seed IS known.
        """
        try:
            handle_peer_exchange(self.peers, msg)  # validate + merge into the flat truth
        except (ValueError, WireError) as exc:
            return self._error("bad-peer-exchange", str(exc))
        # Mirror the freshly-merged set into the bucketed book (idempotent re-adds),
        # then reply with the diversity-spread sample.
        for peer in self.peers.known():
            self.addrbook.add_new(peer, source=None)
        return addrbook_share_message(self.addrbook)

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
        # Re-seed BOTH the flat directory and the bucketed book from the peerbook
        # (locally-minted addresses -> source=None) so peers typed in after
        # construction are dialable and bucketed.
        learn_peers(self.peers, self.addrbook, list(self.peerbook.all().values()), source=None)
        targets = seeds if seeds is not None else list(self.peerbook.all().values())
        learned = 0
        for seed in targets:
            # Advertise a diversity-spread sample (not the flat first-k) so a flooded
            # group cannot dominate what we push to seeds either.
            request = addrbook_share_message(self.addrbook)
            try:
                reply = await self._roundtrip(seed, request)
            except (P2PError, WireError, OSError):
                # An unreachable or misbehaving seed must not sink the bootstrap.
                continue
            # The roundtrip succeeded: this seed is a peer we actually reached, so
            # promote it into the *tried* table (Bitcoin-addrman test-before-evict).
            self.addrbook.mark_tried(seed)
            if reply.get("kind") != PEER_EXCHANGE_KIND:
                continue
            try:
                # Ingest the reply's peers into BOTH layers, keyed on the advertising
                # ``seed`` as the PEX source. THIS is the eclipse defence on the live
                # path: every address a flooding seed pushes shares that seed's source
                # group, so they compete for a bounded set of new-table buckets and
                # cannot crowd an honest minority out of the dial sample.
                #
                # Cap the reply BEFORE parsing — mirroring bootstrap_round (#95) and
                # handle_peer_exchange — so a single malicious bootstrap reply cannot
                # contribute more than MAX_PEX_INBOUND learned entries (#98). The
                # dir-size/static-floor eviction below stays as the second line of
                # defence; this is the per-reply bound the live path was missing.
                truncated = (reply.get("peers") or [])[:MAX_PEX_INBOUND]
                peers = peers_from_records(truncated)
            except (ValueError, WireError):
                continue
            learned += learn_peers(self.peers, self.addrbook, peers, source=seed)
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

    def _id_signing_key(self) -> "str | None":
        """This node's account key signs its OPTIONAL piggybacked identity proofs.

        A keyless (account-less) node returns ``None`` and dials without a proof,
        so the receiver falls back to the carrier id — pre-#58 behaviour.
        """
        return self.account.priv if self.account is not None else None

    async def _roundtrip(self, peer: PeerAddress, msg: dict) -> dict:
        # The Dialer routes by peer.transport, so a tcp:// peer uses TcpTransport
        # and a relay:// peer uses RelayTransport — identical frame bytes either
        # way (the carrier never re-encodes the canonical-CBOR payload). We stamp
        # an OPTIONAL identity proof onto the outbound request (step 2 of #58) so
        # the receiver keys reputation on our proven node key, not our IP; the
        # proof rides in the stripped _relay_* envelope and never touches the
        # canonical frame bytes.
        return await self.dialer.dial(peer, self._stamp_id_proof(msg))

    def _route(self, kind, msg: dict) -> dict:
        """Asyncio routing table: feed sync, Knit handshake, equivocation, PEX."""
        if kind == "feed-request":
            return self._serve_feed(msg)
        elif kind == "knit-proposal":
            return self._handle_knit_proposal(msg)
        elif kind == "knit-finalize":
            return self._handle_knit_finalize(msg)
        elif kind == "equivocation-report":
            return self._handle_equivocation_report(msg)
        elif kind == PEER_EXCHANGE_KIND:
            return self._handle_peer_exchange(msg)
        return self._error("unknown-kind", str(kind))
