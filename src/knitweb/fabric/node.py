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
from ..p2p.anti_entropy import AntiEntropy, Backoff, SyncRound
from .items import web_state_root
from .web import Web
from ..p2p.metrics import Metrics
from ..p2p.node import PeerAddress, StaticPeerBook
from ..p2p.relay import ENVELOPE_PEER_KEY
from ..p2p.reputation import Offense, PeerReputation
from ..p2p.transport import Dialer, TcpTransport, Transport
from ..p2p.wire import WireError, read_frame, write_frame

__all__ = ["FabricNode", "FabricNodeError"]

# Domain-separation tag: a signature over a broadcast fabric record can never be
# replayed as a signature over a feed head, a Knit, or anything else.
_RECORD_TAG = b"knitweb/fabric-record/v1\x00"


class FabricNodeError(RuntimeError):
    """Raised when the fabric gossip protocol refuses or cannot complete a request."""


def _record_signable(record: dict) -> bytes:
    """Canonical, domain-separated bytes an author signs to vouch for a record."""
    return _RECORD_TAG + canonical.encode(record)


class FabricNode:
    """A live p2p fabric peer: a fabric Web plus record gossip + convergence.

    Each node has its own author keypair (a fresh secp256k1 key unless one is
    supplied). Records woven locally are signed and broadcast to every peer in
    the node's :class:`StaticPeerBook`; peers verify the author signature and
    weave the record into their own Web. Because :class:`Web.weave` is
    content-addressed and idempotent, gossip converges to an identical node set
    regardless of arrival order or duplicate delivery.
    """

    def __init__(
        self,
        *,
        priv: str | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        transport: Transport | None = None,
        extra_transports: list[Transport] | None = None,
    ) -> None:
        if priv is None:
            priv, _ = crypto.generate_keypair()
        self._priv = priv
        self.pub = crypto.public_from_private(priv)
        self.web = Web()
        self.peerbook = StaticPeerBook()
        # Same pluggable carrier as AsyncioP2PNode: TCP by default, or a
        # RelayTransport so a NAT'd fabric node still gossips and converges.
        self.transport: Transport = transport or TcpTransport(host=host, port=port)
        self.dialer = Dialer()
        for tr in [self.transport, *(extra_transports or [])]:
            self.dialer.register(tr)
        # The Byzantine-consequence ledger: malformed/oversized frames and
        # forged record signatures accrue misbehavior points; banned peers are
        # refused and disconnected (the same loop AsyncioP2PNode runs).
        self.reputation = PeerReputation()
        # Integer-only observability over the gossip path (records woven,
        # broadcasts sent/failed, sync pulls, frames in/out, malformed/oversized
        # frames, banned-peer refusals). Node-local bookkeeping only: it touches
        # no signed record and no hash path, so a woven Knit's CID is unchanged.
        self.metrics = Metrics()
        self._listening = False
        # Opt-in self-healing convergence loop (issue #44). Off by default: a
        # node only starts re-syncing on a background task once
        # start_anti_entropy is called, so existing serve behaviour is unchanged.
        self._anti_entropy_task: "asyncio.Task | None" = None

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

    @property
    def state_root(self) -> str:
        """The Merkle root of this node's woven Web (the convergence witness)."""
        return web_state_root(self.web)

    async def start(self) -> None:
        """Start listening for gossip frames (one request per connection)."""
        if self._listening:
            return
        await self.transport.listen(self._dispatch)
        self._listening = True

    async def stop(self) -> None:
        await self.stop_anti_entropy()
        if not self._listening:
            return
        await self.transport.close()
        self._listening = False

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
        if self._anti_entropy_task is not None and not self._anti_entropy_task.done():
            return self._anti_entropy_task
        driver = AntiEntropy(
            self._anti_entropy_rounds(peers),
            sleep=sleep or self._anti_entropy_sleep,
            backoff=Backoff(base=interval, ceiling=ceiling),
        )
        self._anti_entropy = driver
        self._anti_entropy_task = asyncio.ensure_future(self._anti_entropy_run(driver))
        return self._anti_entropy_task

    async def stop_anti_entropy(self) -> None:
        """Cancel the background anti-entropy loop if one is running."""
        task = self._anti_entropy_task
        self._anti_entropy_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @staticmethod
    async def _anti_entropy_sleep(delay: int) -> None:
        # The prod clock: a seconds-based asyncio sleep. Tests inject a virtual
        # clock by driving the AntiEntropy driver directly instead.
        await asyncio.sleep(delay)

    async def _anti_entropy_run(self, driver: AntiEntropy) -> None:
        try:
            while True:
                await driver.run_cycle()
        except asyncio.CancelledError:
            raise

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

    async def __aenter__(self) -> "FabricNode":
        await self.start()
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.stop()

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

    async def _send(self, peer: PeerAddress, msg: dict) -> dict:
        # Routed by peer.transport (tcp:// or relay://); the carrier moves the
        # same opaque canonical-CBOR frame, so a signed record's bytes are
        # untouched whether it travels over a socket or the HTTP relay.
        return await self.dialer.dial(peer, msg)

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

    async def _dispatch(self, msg: dict) -> dict:
        """Transport-agnostic gossip handler: request map in, response map out.

        The handler the listening :class:`Transport` feeds decoded requests to.
        The TCP stream applies its banned-peer gate and the signature-offense
        penalty in :meth:`_handle_peer` (a socket peer key and a frame penalty are
        concerns the carrier owns). The relay carrier has no socket, so it stamps
        the sender's identity onto the request as a transport-envelope key
        (:data:`ENVELOPE_PEER_KEY`); here we honour the *same* ban gate before any
        work, then drop the key so it never reaches signed/business logic.
        """
        self.metrics.incr("frames_in")
        peer_id = msg.pop(ENVELOPE_PEER_KEY, None)
        if isinstance(peer_id, str) and self.reputation.is_banned(peer_id):
            self.metrics.incr("banned_refusals")
            return {"kind": "error", "code": "banned", "message": "peer is banned"}
        try:
            kind = msg.get("kind")
            if kind == "fabric-record":
                self._ingest_signed(msg)
                out: dict = {"kind": "fabric-ack"}
            elif kind == "fabric-sync-request":
                out = self._serve_sync()
            else:
                out = {"kind": "error", "code": "unknown-kind", "message": str(kind)}
        except (FabricNodeError, WireError, ValueError) as exc:
            out = {"kind": "error", "code": "bad-request", "message": str(exc)}
        self.metrics.incr("frames_out")
        return out

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

        Refuses a banned peer before any work, penalizes malformed/oversized
        frames, and (uniquely to the fabric node) turns a forged author signature
        into an :class:`Offense.INVALID_SIGNATURE` penalty on the relaying peer.
        Routing of a decoded request matches :meth:`_dispatch` (the carrier path).
        """
        peer_id = self._peer_id(writer)
        try:
            if self.reputation.is_banned(peer_id):
                self.metrics.incr("banned_refusals")
                await write_frame(
                    writer, {"kind": "error", "code": "banned", "message": "peer is banned"}
                )
                return
            try:
                msg = await read_frame(reader)
            except WireError as exc:
                oversized = "too large" in str(exc)
                self.metrics.incr(
                    "frames_oversized" if oversized else "frames_malformed"
                )
                offense = Offense.OVERSIZED_FRAME if oversized else Offense.MALFORMED_FRAME
                self.reputation.penalize(peer_id, offense)
                await write_frame(
                    writer, {"kind": "error", "code": "bad-frame", "message": str(exc)}
                )
                return
            self.metrics.incr("frames_in")
            kind = msg.get("kind")
            if kind == "fabric-record":
                self._ingest_signed(msg)
                out = {"kind": "fabric-ack"}
            elif kind == "fabric-sync-request":
                out = self._serve_sync()
            else:
                out = {"kind": "error", "code": "unknown-kind", "message": str(kind)}
            self.metrics.incr("frames_out")
            await write_frame(writer, out)
        except FabricNodeError as exc:
            # A forged author signature (or other ingest fault) is a signature
            # offense — penalize the relaying peer, then refuse.
            if "signature" in str(exc):
                self.reputation.penalize(peer_id, Offense.INVALID_SIGNATURE)
            self.metrics.incr("frames_out")
            await write_frame(
                writer, {"kind": "error", "code": "bad-request", "message": str(exc)}
            )
        except WireError as exc:
            # Explicit: a routing-time wire fault is refused as a bad request.
            # (WireError subclasses ValueError, but we keep it explicit so the
            # handling never silently rides on that subclassing.)
            self.metrics.incr("frames_out")
            await write_frame(
                writer, {"kind": "error", "code": "bad-request", "message": str(exc)}
            )
        except ValueError as exc:
            self.metrics.incr("frames_out")
            await write_frame(
                writer, {"kind": "error", "code": "bad-request", "message": str(exc)}
            )
        finally:
            writer.close()
            await writer.wait_closed()
