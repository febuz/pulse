"""FabricNode — a live p2p node that weaves into a fabric Web and converges.

A :class:`FabricNode` is the smallest useful "live web" peer: it owns a fabric
:class:`~knitweb.fabric.web.Web`, accepts local weaves, and **gossips every woven
record over the p2p transport** so that connected peers ingest the same records
into their own Web. Once a record has propagated, two nodes hold the *same set of
node CIDs* and therefore the same :func:`~knitweb.fabric.items.web_state_root` —
they have **converged**.

This is the first increment of issue #9 (a live p2p fabric node). It deliberately
reuses the existing Phase-3 transport primitives rather than inventing a new one:

  * the canonical length-prefixed CBOR frames (:mod:`knitweb.p2p.wire`),
  * the static peer book + ``PeerAddress`` shape (:mod:`knitweb.p2p.node`),
  * the existing author signing (a feed keypair signs each broadcast record so a
    peer can verify provenance before weaving — equivocation-proofing of the feed
    history itself is left to the feed layer and a later increment).

Scope of this increment: **record propagation + convergence**.

  * ``weave(record)``      — weave locally *and* broadcast to all known peers.
  * ``sync_from(peer)``    — pull a peer's full record set (catch-up for a node
                             that joined after some records were already woven).
  * convergence            — after gossip/sync settles, ``state_root`` matches.

Conflict quarantine, partial proofs, and a real DHT are explicitly out of scope
here and continue to live in / evolve from the feed and ``AsyncioP2PNode`` layers.
"""

from __future__ import annotations

import asyncio

from ..core import canonical, crypto
from ..p2p.anti_entropy import SyncRound
from ..p2p.base_node import BaseNode
from .items import web_state_root
from .web import Web
from ..p2p.node import PeerAddress, StaticPeerBook
from ..p2p.reputation import Offense
from ..p2p.transport import Transport
from ..p2p.wire import WireError

__all__ = ["FabricNode", "FabricNodeError"]

# Domain-separation tag: a signature over a broadcast fabric record can never be
# replayed as a signature over a feed head, a Knit, or anything else.
_RECORD_TAG = b"knitweb/fabric-record/v1\x00"


class FabricNodeError(RuntimeError):
    """Raised when the fabric gossip protocol refuses or cannot complete a request."""


def _record_signable(record: dict) -> bytes:
    """Canonical, domain-separated bytes an author signs to vouch for a record."""
    return _RECORD_TAG + canonical.encode(record)


class FabricNode(BaseNode):
    """A live p2p fabric peer: a fabric Web plus record gossip + convergence.

    Each node has its own author keypair (a fresh secp256k1 key unless one is
    supplied). Records woven locally are signed and broadcast to every peer in
    the node's :class:`StaticPeerBook`; peers verify the author signature and
    weave the record into their own Web. Because :class:`Web.weave` is
    content-addressed and idempotent, gossip converges to an identical node set
    regardless of arrival order or duplicate delivery.
    """

    # The fabric _dispatch catches the fabric error family (plus wire/value).
    # Banned-branch frames_out: this node does NOT increment (diverges from
    # AsyncioP2PNode), preserving the existing gossip-path metric exactly.
    _dispatch_errors = (FabricNodeError, WireError, ValueError)
    _count_frames_out_on_banned = False

    def __init__(
        self,
        *,
        priv: str | None = None,
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
        if priv is None:
            priv, _ = crypto.generate_keypair()
        self._priv = priv
        self.pub = crypto.public_from_private(priv)
        self.web = Web()
        self.peerbook = StaticPeerBook()

    # -- server lifecycle -------------------------------------------------

    @property
    def state_root(self) -> str:
        """The Merkle root of this node's woven Web (the convergence witness)."""
        return web_state_root(self.web)

    # -- self-healing anti-entropy (issue #44) ----------------------------

    def start_anti_entropy(
        self,
        peers: "list[PeerAddress] | None" = None,
        *,
        interval: int = 1,
        ceiling: int = 64,
        sleep=None,
    ) -> "asyncio.Task":
        """Launch the self-healing anti-entropy loop as a background task (#44).

        Opt-in: nothing runs until this is called, so a plain ``start()`` keeps
        its existing behaviour. The loop periodically re-pulls every peer's Web
        snapshot via :meth:`sync_from`, so a node that drifted apart after a
        disconnect re-converges on its peer's ``state_root`` once the peer is
        reachable again.

        ``peers`` are the endpoints to re-sync from (``None`` uses the configured
        peerbook). The injected clock defaults to :func:`asyncio.sleep` (the prod
        clock); a test passes a virtual-clock ``sleep`` so convergence is
        deterministic with no real time. The schedule is the integer backoff from
        #43. The driver swallows a failed round, so a dropped/refusing peer backs
        the schedule off rather than crashing the loop; on reconnect the next
        round re-syncs and the node re-converges.
        """
        return self._spawn_anti_entropy(
            self._anti_entropy_rounds(peers),
            interval=interval,
            ceiling=ceiling,
            sleep=sleep,
        )

    def _anti_entropy_rounds(
        self, peers: "list[PeerAddress] | None"
    ) -> "list[SyncRound]":
        async def sync_round() -> int:
            targets = (
                peers if peers is not None else list(self.peerbook.all().values())
            )
            if not targets:
                return 0
            pulled = 0
            reached = False
            for peer in targets:
                # A dropped/refusing peer raises here; swallow it so one bad peer
                # never sinks the round. If *every* peer raised the round itself
                # raises (below), and the driver backs the schedule off.
                try:
                    pulled += await self.sync_from(peer)
                except (OSError, FabricNodeError, WireError):
                    continue
                reached = True
            if not reached:
                # No peer was reachable this cycle: surface a failed round so the
                # driver escalates the backoff (it swallows the raise itself).
                raise FabricNodeError("no peer reachable for anti-entropy round")
            return pulled

        return [sync_round]

    # -- peer wiring ------------------------------------------------------

    def add_peer(self, name: str, peer: PeerAddress) -> None:
        """Register a peer that local weaves will be broadcast to."""
        self.peerbook.add(name, peer)

    # -- weaving + propagation --------------------------------------------

    async def weave(self, record: dict) -> str:
        """Weave ``record`` into the local Web and broadcast it to all peers.

        Returns the record's CID. Broadcast failures to individual peers are
        swallowed (a peer may be offline); convergence for such a peer can later
        be reached with :meth:`sync_from`. Returns once every reachable peer has
        acknowledged the record.
        """
        before = len(self.web.nodes)
        cid = self.web.weave(record)
        if len(self.web.nodes) > before:
            self.metrics.incr("records_woven")
        await self._broadcast(record)
        return cid

    async def _broadcast(self, record: dict) -> None:
        msg = self._signed_record_msg(record)
        peers = list(self.peerbook.all().values())
        if not peers:
            return
        results = await asyncio.gather(
            *(self._send(peer, msg) for peer in peers),
            return_exceptions=True,
        )
        # Swallow per-peer transport errors (offline peer); they are recoverable
        # via sync_from. Re-raise anything unexpected so bugs are not hidden.
        for result in results:
            if isinstance(result, Exception):
                if not isinstance(result, (OSError, FabricNodeError, WireError)):
                    raise result
                self.metrics.incr("broadcasts_failed")
            else:
                self.metrics.incr("broadcasts_sent")

    def _signed_record_msg(self, record: dict) -> dict:
        sig = crypto.sign(self._priv, _record_signable(record))
        return {
            "kind": "fabric-record",
            "author": self.pub,
            "record": record,
            "sig": sig,
        }

    def _id_signing_key(self) -> "str | None":
        """This node's author key signs its OPTIONAL piggybacked identity proofs.

        A FabricNode always holds a key, so its dials always carry a proof and a
        receiver keys reputation on this node's proven ``node:<pubkey>`` rather
        than its IP (step 2 of #58).
        """
        return self._priv

    async def _send(self, peer: PeerAddress, msg: dict) -> dict:
        # Routed by peer.transport (tcp:// or relay://); the carrier moves the
        # same opaque canonical-CBOR frame, so a signed record's bytes are
        # untouched whether it travels over a socket or the HTTP relay. We stamp
        # an OPTIONAL identity proof onto the outbound request (step 2 of #58) so
        # the receiver keys reputation on our proven node key, not our IP; the
        # proof rides in the stripped _relay_* envelope and never touches the
        # canonical frame bytes.
        return await self.dialer.dial(peer, self._stamp_id_proof(msg))

    # -- catch-up sync ----------------------------------------------------

    async def sync_from(self, peer: PeerAddress) -> int:
        """Pull ``peer``'s full record set and weave any records we are missing.

        Returns the number of newly woven records. This lets a node that joined
        after some records were already gossiped converge to the same Web.
        """
        msg = await self._send(peer, {"kind": "fabric-sync-request"})
        if msg.get("kind") == "error":
            raise FabricNodeError(f"{msg.get('code')}: {msg.get('message')}")
        if msg.get("kind") != "fabric-sync-data":
            raise FabricNodeError(f"unexpected response kind: {msg.get('kind')!r}")
        signed = msg.get("records")
        if not isinstance(signed, list):
            raise FabricNodeError("fabric-sync-data records must be a list")
        added = 0
        for item in signed:
            if self._ingest_signed(item):
                added += 1
        if added:
            self.metrics.incr("sync_pulls", added)
        return added

    # -- ingestion --------------------------------------------------------

    def _ingest_signed(self, item) -> bool:
        """Verify an author-signed record envelope and weave it. True if new."""
        if not isinstance(item, dict):
            raise FabricNodeError("record envelope must be a map")
        author = item.get("author")
        record = item.get("record")
        sig = item.get("sig")
        if not isinstance(author, str) or not isinstance(sig, str):
            raise FabricNodeError("record envelope missing author/sig")
        if not isinstance(record, dict):
            raise FabricNodeError("record must be a map")
        if not crypto.verify(author, _record_signable(record), sig):
            raise FabricNodeError("invalid author signature on fabric record")
        before = len(self.web.nodes)
        self.web.weave(record)
        if len(self.web.nodes) > before:
            self.metrics.incr("records_woven")
            return True
        return False

    # -- server side ------------------------------------------------------

    def _serve_sync(self) -> dict:
        # Re-sign every node we hold under *our* key so a catching-up peer can
        # verify provenance of the snapshot it pulls. (Records keep their own
        # CID identity regardless of who relays them.)
        records = [self._signed_record_msg(rec) for rec in self.web.nodes.values()]
        return {"kind": "fabric-sync-data", "records": records}

    def _route(self, kind, msg: dict) -> dict:
        """Fabric routing table: ingest a gossiped record, or serve a sync snapshot.

        A forged author signature raises ``FabricNodeError("invalid author
        signature ...")`` here; the shared :meth:`BaseNode._dispatch` catches it,
        charges the relaying peer an :class:`Offense.INVALID_SIGNATURE` penalty
        (the message carries the word "signature"), and refuses — so the offense
        lands uniformly on the live TCP path and the relay path alike (#52), with
        no node-specific ``_serve_connection`` override to keep in sync.
        """
        if kind == "fabric-record":
            self._ingest_signed(msg)
            return {"kind": "fabric-ack"}
        elif kind == "fabric-sync-request":
            return self._serve_sync()
        return {"kind": "error", "code": "unknown-kind", "message": str(kind)}
