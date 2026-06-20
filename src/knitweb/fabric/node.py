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

  * ``weave(record)``      — weave locally *and* announce its CID to all peers.
  * ``sync_from(peer)``    — pull a peer's full record set (catch-up for a node
                             that joined after some records were already woven).
  * convergence            — after gossip/sync settles, ``state_root`` matches.

Conflict quarantine, partial proofs, and a real DHT are explicitly out of scope
here and continue to live in / evolve from the feed and ``AsyncioP2PNode`` layers.

Propagation is no longer a full-flood (#64): a weave **announces** the record's
canonical CID (``inv-announce``); each peer replies with only the CIDs it lacks
(``inv-getdata``); the announcer then serves those wants by sending the **stored
frame bytes verbatim** (``inv-data``), so a peer that already holds the CID never
receives the body — collapsing redundant traffic from O(N*body) to ~O(diff). The
:class:`~knitweb.p2p.inventory.InventoryRelay` drives the announce/want dedup; a
per-node ``CID -> verbatim signed-frame bytes`` store backs its ``FrameLookup``
so the served bytes — and therefore the record's CID — are byte-identical across
a hop. The unchanged ``sync_from`` / ``start_anti_entropy`` anti-entropy loop
remains the convergence backstop: anything a best-effort announce/want misses is
re-pulled by the periodic full sync, so every honest node still settles on the
identical ``web_state_root``.

Reconnect/periodic sync is no longer a full inv-announce flood either (#60 —
Erlay activation): :meth:`reconcile_with` drives a
:class:`~knitweb.p2p.reconcile.ReconcileSession` between this node's frame-store
CID set and a peer's, exchanging only compact ``(count, integer-xor-fingerprint)``
range summaries over the ``inv-recon-req`` / ``inv-recon-range`` /
``inv-recon-result`` envelopes. The recursive range bisection zeroes in on the
**symmetric difference** in traffic proportional to the *diff*, not the inventory;
the locally-missing CIDs it discovers are then fetched through the EXISTING
``inv-getdata`` path (stored frames served verbatim), so only ``|diff|`` bodies
travel — O(diff) instead of O(total). Reconciliation moves only CIDs and range
summaries, never a record body, so a signed record's byte-identity (and CID) is
untouched. Anti-entropy stays the unconditional convergence backstop: anything a
best-effort reconcile misses is still re-pulled by the periodic full sync.
"""

from __future__ import annotations

import asyncio
import random as _random_mod
from collections import deque

from ..core import canonical, crypto
from ..p2p.anti_entropy import SyncRound
from ..p2p.base_node import BaseNode
from ..p2p import inventory
from ..p2p.inventory import (
    INV,
    GETDATA,
    RECON_REQ,
    RECON_RANGE,
    RECON_RESULT,
    InventoryRelay,
)
from ..p2p import mesh
from ..p2p.mesh import GRAFT, PRUNE, IHAVE, IWANT, Gossipsub
from ..p2p.relay import ENVELOPE_PEER_KEY, ENVELOPE_ID_PROOF_KEY
from ..p2p.reconcile import ReconcileSession
from .items import web_state_root
from .web import Web
from ..p2p.node import PeerAddress, StaticPeerBook
from ..p2p.reputation import Offense
from ..p2p.transport import Transport
from ..p2p import wire
from ..p2p.wire import WireError

__all__ = ["FabricNode", "FabricNodeError", "WEB_TOPIC"]

# Domain-separation tag: a signature over a broadcast fabric record can never be
# replayed as a signature over a feed head, a Knit, or anything else.
_RECORD_TAG = b"knitweb/fabric-record/v1\x00"

# The single fixed gossipsub topic this increment maintains a mesh for. One web
# topic per fabric (Kademlia + Erlay topic-splitting are separate activations).
# Vocabulary stays Web/Knit/knitweb — never "loom"/"network".
WEB_TOPIC = "web/fabric/v1"

# Erlay activation (#60): the envelope key carrying a reconcile session id, so the
# RESPONDER can match a multi-round bisection's dials to one in-flight
# ReconcileSession. A plain integer-string in the dict carrier; it never touches a
# signed/hashed record body, so a Knit's CID is unaffected by its presence.
_RECON_SESSION_KEY = "session"

# Cap on concurrently-tracked responder reconcile sessions. A session is dropped
# the moment it converges, so honest load is tiny; this integer ceiling stops a
# peer from leaking unbounded session state. Oldest session is evicted on overflow.
_MAX_RECON_SESSIONS = 1024

# The node-layer key carrying the SENDER's stable gossip peer-id (its pubkey)
# alongside a mesh control frame. The mesh control frames themselves are ids-only
# (mesh.py is unedited); this envelope key lets the RECEIVER key its own mesh /
# score state on the announcer's stable ``node:<pubkey>`` rather than an
# ephemeral carrier id, exactly as #58 keys reputation. It is a plain string in
# the dict carrier and never touches a signed/hashed record body.
_MESH_PEER_KEY = "peer"


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
        gossip: Gossipsub | None = None,
        gossip_seed: int | None = None,
        serve_budget: "inventory.ServeBudget | None" = None,
        ingest_budget: "inventory.ServeBudget | None" = None,
        max_gossiped_frames: int = 50_000,
        diffuse_max_ms: int = 0,
        diffuse_seed: int | None = None,
        diffuse_sleep=None,
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
        # The single load-bearing new piece for lazy relay (#64): a per-node
        # CID -> verbatim signed-envelope frame bytes store. The frame is the
        # exact ``write_frame_bytes`` of the ``fabric-record`` envelope we wove
        # or verified; serving these bytes UNCHANGED is what preserves a signed
        # record's byte-identity (and CID) across a relay hop. Populated on every
        # weave AND every successful ingest so a node can re-serve anything it
        # holds. It is node-local, integer-bounded by the relay's SeenSet, and
        # touches no canonical/hash path beyond storing already-canonical bytes.
        self._frames: dict[str, bytes] = {}
        # #92 frame-store bound. The store mixes two provenances with very different
        # eviction safety, so a blanket LRU is WRONG (it would silently drop our own
        # authoritative records — nothing on the web can re-serve them = data loss):
        #   * ``_authored`` — CIDs we wove ourselves; the authoritative source, NEVER
        #     evicted.
        #   * ``_gossiped_order`` — LRU queue of gossiped-in (non-authored) CIDs; only
        #     THIS portion is size-bounded, and evicting one is re-fetch-safe (anti-
        #     entropy / Erlay re-pull it). ``max_gossiped_frames`` caps it.
        self._authored: set[str] = set()
        self._gossiped_order: "deque[str]" = deque()
        self._max_gossiped = max(1, int(max_gossiped_frames))
        # A SEPARATE per-peer byte budget gates INGEST (a peer flooding own-key-signed
        # junk is throttled at _ingest_signed before it consumes memory — eviction
        # alone only caps steady-state). Reuses the #91 ServeBudget primitive (already
        # memory-bounded by its own max_peers LRU, injectable clock for tests).
        self._ingest_budget = (
            ingest_budget if ingest_budget is not None else inventory.ServeBudget()
        )
        # One inventory relay per node; its SeenSet is the announce/want dedup.
        # The lookup is into the frame store above (returns None for a CID we do
        # not hold, so on_getdata never fabricates a body). It also owns the #91
        # anti-amplification ServeBudget: a per-peer byte bucket over an integer
        # window plus a per-request batch cap, enforced on the getdata/IWANT serve
        # path so a single ~2 MiB request can no longer reflect hundreds of GiB.
        # A test injects ``serve_budget`` with a virtual clock so the window
        # boundary is deterministic; prod default uses a monotonic integer clock.
        self._serve_budget = (
            serve_budget if serve_budget is not None else inventory.ServeBudget()
        )
        self._inv = InventoryRelay(
            lambda cid: self._frames.get(cid), budget=self._serve_budget
        )
        # The per-peer serve key for the request currently being dispatched. Set by
        # the :meth:`_dispatch` override (from the carrier-stamped sender identity)
        # so the serve handlers can debit the right peer's byte bucket; ``None``
        # when the carrier could not identify the sender (the per-request count cap
        # still applies, so anonymity cannot bypass the hard ceiling).
        self._serve_peer_key: "str | None" = None
        # Erlay activation (#60): per-(peer, session) RESPONDER state. A reconcile
        # session is a multi-round bisection; on the one-shot dict carrier each
        # round is one dial, so the side being dialed must keep its
        # ReconcileSession alive across dials. Keyed on the session id the
        # initiator stamps. Bounded: a session is dropped as soon as it converges
        # (an empty reply batch) and the map is integer-capped so a peer cannot
        # leak unbounded sessions. The reconciler is socket/clock/RNG-free, so this
        # touches no signed record and no hash path.
        self._recon_sessions: dict[str, ReconcileSession] = {}
        # Monotonic integer session-id counter (initiator side). Deterministic and
        # injectable for tests via ``_recon_seq``; never a wall-clock/random value,
        # so a replayed reconcile mints identical session ids.
        self._recon_seq = 0
        # Background reconcile loop handle (issue #60), opt-in like anti-entropy /
        # gossip; stop() cancels it.
        self._reconcile_task: "asyncio.Task | None" = None
        # One gossipsub mesh for the single web topic (#67 activated on top of the
        # #75 inv->getdata propagation). It is purely a TARGET SELECTOR: it decides
        # WHICH peers a weave eager-pushes to (the bounded <=D mesh) and WHICH get
        # only a lazy IHAVE digest; it never moves a body. The peerbook NAME is the
        # gossip peer-id on the announce side; a peer keys ITS mesh state on the
        # sender's stable pubkey (carried in ``_MESH_PEER_KEY``). RNG/seed are
        # injected so a test replays a byte-identical mesh; prod gets a fresh
        # Random. The epoch is the integer the caller ticks via maintain_mesh().
        if gossip is not None:
            self._gossip = gossip
        else:
            self._gossip = Gossipsub(rng=_random_mod.Random(gossip_seed))
        # Source-privacy diffusion (#93). On the ORIGIN announce path only, each
        # mesh peer's already-built inv frame is dispatched after an independent
        # random integer-millisecond delay drawn uniformly over [0, diffuse_max_ms],
        # so the author is no longer deterministically the first emitter of its own
        # CID (the timing-correlation vector). The draw is INTEGER ms on a runtime
        # RNG — never the canonical byte path — so a fresh Knit CID and the stored
        # signed frame bytes are untouched at any setting. ``diffuse_max_ms == 0``
        # is exact legacy behaviour (no draw, no sleep). The RNG is injected like
        # the gossip RNG above so tests replay deterministically; the sleep clock
        # is injected like start_anti_entropy so a test uses a virtual clock with no
        # real wall-time. Default is 0 (mechanism present, opt-in): enabling it by
        # default would add real first-hop latency to every weave; turning it on as
        # a default is a follow-up once a privacy/latency knob is chosen.
        self._diffuse_max_ms = max(0, int(diffuse_max_ms))
        self._diffuse_rng = _random_mod.Random(diffuse_seed)
        self._diffuse_sleep = diffuse_sleep if diffuse_sleep is not None else asyncio.sleep

    # -- server lifecycle -------------------------------------------------

    @property
    def state_root(self) -> str:
        """The Merkle root of this node's woven Web (the convergence witness)."""
        return web_state_root(self.web)

    async def _dispatch(self, msg: dict) -> dict:
        """Resolve the per-peer serve key, then delegate to the shared dispatch.

        The #91 anti-amplification byte budget is keyed PER PEER, but the shared
        :meth:`BaseNode._dispatch` pops the carrier identity (and any piggybacked
        identity proof) BEFORE it calls :meth:`_route`, so the serve handlers
        downstream would otherwise have no peer to debit. We therefore resolve the
        reputation key here — through the SAME single identity-keying authority the
        base uses (:meth:`_resolve_verdict`, reading the carrier id and the OPTIONAL
        proof NON-destructively so the base still pops and judges them itself) —
        and stash it for the serve path. The proven ``node:<pubkey>`` is used when a
        valid+fresh proof rode along (so a peer cannot dodge its budget by hopping
        carrier ids), else the carrier id, else ``None`` (unidentified sender: only
        the per-request count cap applies). We never mutate ``msg`` and we delegate
        verbatim, so the ban gate, the INVALID_SIGNATURE penalty, and every other
        dispatch behaviour are byte-for-byte the base's.
        """
        carrier_id = msg.get(ENVELOPE_PEER_KEY)
        if not isinstance(carrier_id, str):
            carrier_id = None
        if carrier_id is None:
            self._serve_peer_key = None
        else:
            self._serve_peer_key = self._resolve_peer_id(
                carrier_id, msg.get(ENVELOPE_ID_PROOF_KEY)
            )
        try:
            return await super()._dispatch(msg)
        finally:
            # Scope the key to this dispatch only; never let it leak to the next.
            self._serve_peer_key = None

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

    # -- gossipsub mesh maintenance (issue #78) ---------------------------

    def start_gossip(
        self,
        *,
        interval: int = 1,
        sleep=None,
    ) -> "asyncio.Task":
        """Launch the gossipsub mesh-maintenance loop as a background task (#78).

        Opt-in, exactly like :meth:`start_anti_entropy`: nothing runs until this
        is called, so a plain ``start()`` keeps its existing behaviour and every
        existing test is unaffected. The loop ticks :meth:`maintain_mesh` (the
        GRAFT/PRUNE heartbeat that steers each topic mesh toward ``D``) then
        :meth:`gossip_tick` (the lazy mesh-IHAVE to non-mesh peers) once per
        ``interval``, so in a live node the mesh is actually maintained and the
        eager announce narrows from all-candidates to the bounded ``O(D)`` mesh.

        ``interval`` is an integer cadence (no wall-clock; the gossip heartbeat's
        own notion of time is its integer epoch). The injected ``sleep`` defaults
        to :func:`asyncio.sleep` (the prod clock); a test passes a virtual-clock
        ``sleep`` so the cadence is deterministic with no real time. A raised tick
        is swallowed (an offline peer this cycle) so one bad round never crashes
        the loop — the mesh re-steers on the next heartbeat. It touches no signed
        record and no hash path, so a woven Knit's CID is byte-identical whether
        or not this loop runs.
        """
        if not isinstance(interval, int) or isinstance(interval, bool):
            raise TypeError("interval must be int")
        if interval < 1:
            raise ValueError("interval must be >= 1")
        if self._gossip_task is not None and not self._gossip_task.done():
            return self._gossip_task
        self._gossip_task = asyncio.ensure_future(
            self._gossip_run(
                self._gossip_round,
                interval,
                sleep or self._gossip_sleep,
            )
        )
        return self._gossip_task

    async def _gossip_round(self) -> None:
        """One gossip heartbeat: maintain the mesh, then lazily gossip the fringe.

        The single tick the #78 scheduler drives: :meth:`maintain_mesh` advances
        the integer epoch and ships GRAFT/PRUNE to steer the mesh toward ``D``;
        :meth:`gossip_tick` then sends the lazy mesh-IHAVE digest to every
        non-mesh candidate. Both already swallow per-peer transport faults; this
        ordering is the one the mesh-propagation tests tick by hand.
        """
        await self.maintain_mesh()
        await self.gossip_tick()

    # -- Erlay reconcile (issue #60 activation) ---------------------------

    def _held_cids(self) -> list[str]:
        """This node's authoritative CID set: the verbatim frame-store keys.

        A CID is in the store iff this node holds the record's signed frame and can
        re-serve it byte-identically (populated on every weave AND every ingest).
        This is exactly the set a reconcile session bisects, and exactly the set
        the inv path serves from — so a CID the session flags as locally-missing is
        one ``inv-getdata`` can actually fetch.
        """
        return list(self._frames.keys())

    def start_reconcile(
        self,
        peers: "list[PeerAddress] | None" = None,
        *,
        interval: int = 1,
        sleep=None,
    ) -> "asyncio.Task":
        """Launch the periodic Erlay reconcile loop as a background task (#60).

        Opt-in, exactly like :meth:`start_anti_entropy` / :meth:`start_gossip`:
        nothing runs until this is called, so a plain ``start()`` keeps its
        existing behaviour and every existing test is unaffected. Each tick runs
        one :meth:`reconcile_tick`, which drives a :class:`ReconcileSession`
        against every peer and fetches ONLY the locally-missing CIDs via the
        existing ``inv-getdata`` path — O(diff) instead of an O(total) inv flood.

        ``interval`` is an integer cadence (no wall-clock). The injected ``sleep``
        defaults to :func:`asyncio.sleep`; a test passes a virtual-clock ``sleep``
        so the cadence is deterministic with no real time. A raised tick is
        swallowed (an offline peer this cycle) so one bad round never crashes the
        loop — the next tick re-reconciles, and anti-entropy remains the backstop.
        """
        if not isinstance(interval, int) or isinstance(interval, bool):
            raise TypeError("interval must be int")
        if interval < 1:
            raise ValueError("interval must be >= 1")
        if self._reconcile_task is not None and not self._reconcile_task.done():
            return self._reconcile_task
        self._reconcile_task = asyncio.ensure_future(
            self._gossip_run(
                lambda: self.reconcile_tick(peers),
                interval,
                sleep or self._gossip_sleep,
            )
        )
        return self._reconcile_task

    async def stop_reconcile(self) -> None:
        """Cancel the background reconcile loop if one is running."""
        task = self._reconcile_task
        self._reconcile_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """Stop the listener and ALL background loops (adds reconcile to base).

        :class:`~knitweb.p2p.base_node.BaseNode.stop` tears down the anti-entropy
        and gossip loops; this override also cancels the opt-in reconcile loop
        (#60) before delegating, so a node started with :meth:`start_reconcile`
        shuts down just as cleanly as one started with the other loops.
        """
        await self.stop_reconcile()
        await super().stop()

    async def reconcile_tick(self, peers: "list[PeerAddress] | None" = None) -> int:
        """One reconcile heartbeat: reconcile with every peer; return CIDs fetched.

        Caller-driven (deterministic): a test ticks it explicitly; the #60 loop
        ticks it on its integer cadence; :meth:`sync_from`-style reconnect can call
        it directly. Per-peer transport faults are swallowed (an offline peer this
        cycle) so one bad peer never sinks the tick — the next tick retries and the
        anti-entropy backstop still converges that peer.
        """
        targets = peers if peers is not None else list(self.peerbook.all().values())
        fetched = 0
        for peer in targets:
            try:
                fetched += await self.reconcile_with(peer)
            except (OSError, FabricNodeError, WireError):
                continue
        return fetched

    async def reconcile_with(self, peer: PeerAddress) -> int:
        """Reconcile our CID set with ``peer`` and fetch ONLY the CIDs we lack.

        Drives a :class:`ReconcileSession` over the carrier: we open with a
        full-keyspace probe (``inv-recon-req``), then ping-pong compact
        ``(count, integer-xor-fingerprint)`` range summaries
        (``inv-recon-range`` <-> ``inv-recon-result``) until the bisection has
        zeroed in on the symmetric difference. The traffic is proportional to the
        *diff*, not the inventory: an identical CID set prunes at the root in a
        single round with zero CIDs exchanged.

        The session's ``missing`` set is exactly the CIDs the peer holds and we
        lack. We then issue ONE ``inv-getdata`` for precisely those CIDs; the peer
        serves them as ``inv-data`` (the verbatim stored frames), which we ingest
        through the same crypto gate as any other body — so byte-identity holds and
        only ``|diff|`` bodies ever travel. Returns the number of newly-woven
        records (``<= |missing|``).
        """
        session = ReconcileSession(self._held_cids())
        self._recon_seq += 1
        session_id = str(self._recon_seq)
        # 1) open: send the first probe batch as inv-recon-req.
        batch = session.open()
        kind = RECON_REQ
        while batch and not session.done:
            resp = await self._send(
                peer,
                {
                    "kind": kind,
                    _RECON_SESSION_KEY: session_id,
                    "frames": batch,
                },
            )
            rkind = resp.get("kind")
            if rkind == "error":
                raise FabricNodeError(f"{resp.get('code')}: {resp.get('message')}")
            if rkind != RECON_RESULT:
                raise FabricNodeError(f"unexpected reconcile response kind: {rkind!r}")
            reply = resp.get("frames")
            if not isinstance(reply, list):
                raise FabricNodeError("inv-recon-result frames must be a list")
            batch = session.advance([bytes(fr) for fr in reply])
            kind = RECON_RANGE
        self.metrics.incr("reconcile_sessions")
        # 2) fetch ONLY the locally-missing CIDs through the existing inv-getdata
        #    path: one getdata for exactly the symmetric-difference CIDs we lack.
        missing = sorted(session.missing)
        if not missing:
            return 0
        self.metrics.incr("reconcile_missing", len(missing))
        return await self._pull_cids(peer, missing)

    async def _pull_cids(self, peer: PeerAddress, cids: list[str]) -> int:
        """Fetch ``cids`` from ``peer`` via inv-getdata and ingest the bodies.

        The pull leg of the lazy relay: we dial an ``inv-getdata`` naming exactly
        the CIDs we lack, the peer replies ``inv-data`` carrying ONLY those bodies
        (its stored frames verbatim), and we ingest each through the SAME
        :meth:`_ingest_signed` crypto gate as a gossiped record — so a forged body
        is rejected identically and a valid one keeps its exact CID. Returns the
        number of newly-woven records.
        """
        if not cids:
            return 0
        resp = await self._send(peer, {"kind": GETDATA, "cids": cids})
        kind = resp.get("kind")
        if kind == "error":
            raise FabricNodeError(f"{resp.get('code')}: {resp.get('message')}")
        if kind != "inv-data":
            # The peer no longer holds those CIDs (or answered an ack); nothing to
            # ingest. Anti-entropy remains the backstop for that residue.
            return 0
        records = resp.get("records")
        if not isinstance(records, list):
            raise FabricNodeError("inv-data records must be a list")
        added = 0
        for item in records:
            if self._ingest_signed(item):
                added += 1
        if added:
            self.metrics.incr("reconcile_pulls", added)
        return added

    # -- peer wiring ------------------------------------------------------

    def add_peer(self, name: str, peer: PeerAddress) -> None:
        """Register a peer that local weaves will be broadcast to.

        The peerbook ``name`` doubles as this peer's gossipsub peer-id (the join
        key between the two layers): it is registered as a topic *candidate*, not
        a mesh member — :meth:`maintain_mesh` grafts candidates into the bounded
        mesh as degree requires. ``peerbook.all()`` is the name -> PeerAddress
        resolver that turns a mesh peer-id back into a dial target.
        """
        self.peerbook.add(name, peer)
        self._gossip.add_peer(WEB_TOPIC, name)

    # -- weaving + propagation --------------------------------------------

    async def weave(self, record: dict) -> str:
        """Weave ``record`` into the local Web and announce its CID to all peers.

        Returns the record's CID. Propagation is the lazy two-step relay (#64):
        we announce the canonical CID, each peer wants only what it lacks, and we
        serve the wanted bodies verbatim — so a peer that already holds the CID
        never receives the body. Per-peer failures are swallowed (a peer may be
        offline); convergence for such a peer is reached later by the unchanged
        anti-entropy / :meth:`sync_from` backstop. The signed-envelope frame is
        stored under the CID so the relay can serve it byte-identically.
        """
        before = len(self.web.nodes)
        cid = self.web.weave(record)
        if len(self.web.nodes) > before:
            self.metrics.incr("records_woven")
        # Store the verbatim signed-envelope frame bytes BEFORE announcing, so a
        # peer that wants this CID gets the exact bytes back (byte-identity). Marked
        # authored: our own records are authoritative and never evicted (#92).
        self._store_frame(cid, self._signed_record_msg(record), authored=True)
        await self._eager_announce(cid)
        return cid

    def _eager_targets(self, cid: str) -> list[str]:
        """The bounded set of peer NAMES this weave eager-pushes ``cid`` to.

        Delegates target selection to the gossipsub mesh (``publish`` returns the
        <=D mesh members for the topic, and records the id as held for future
        IHAVE digests). FALLBACK for tiny nets / a cold mesh: when the mesh is
        empty for the topic — which it is at construction before the first
        :meth:`maintain_mesh`, and whenever ``D >= peer-count`` keeps every
        candidate grafted — we announce to ALL candidates, so a weave issued
        immediately after ``add_peer`` (the convergence test's fan-out case) still
        gets the eager push and small nets behave byte-for-byte like #75. Once the
        mesh is non-empty the eager fan-out is bounded to O(D).
        """
        targets = self._gossip.publish(WEB_TOPIC, cid)
        if targets:
            return targets
        return self._gossip.topic_peers(WEB_TOPIC)

    async def link(self, src: str, dst: str, rel: str, weight: int = 1) -> str:
        """Create a signed link edge and announce it to peers for convergence.

        ``src`` and ``dst`` must already exist in the local web, matching
        :meth:`fabric.web.Web.link` semantics. Edges are treated as signed gossip
        objects so peers can converge on the same edge set, not just nodes.
        """
        if not isinstance(src, str) or not isinstance(dst, str) or not isinstance(rel, str):
            raise FabricNodeError("src/dst/rel must be strings")
        if not isinstance(weight, int) or isinstance(weight, bool) or weight < 0:
            raise FabricNodeError("weight must be a non-negative int")
        before_edges = len(self.web._out.get(src, []))
        edge = self.web.link(src, dst, rel, weight=weight)
        # Re-sign and store under the edge CID so peers can fetch the exact body
        # they can verify with the sender's key.
        self._store_frame(edge.cid, self._signed_edge_msg(edge.to_record()))
        # Announce only when the edge is truly new; duplicates are idempotent.
        if len(self.web._out.get(src, [])) > before_edges:
            await self._eager_announce(edge.cid)
            self.metrics.incr("edges_linked")
        return edge.cid

    async def _eager_announce(self, cid: str) -> None:
        """Announce ``cid`` to the MESH (not every peer); serve the bodies it lacks.

        Narrows the #75 eager path's target SET from ``peerbook.all()`` to the
        bounded gossipsub mesh (or all candidates as a cold-mesh fallback) while
        reusing :meth:`_announce_to` VERBATIM — so the inv -> getdata -> inv-data
        body transfer, the SeenSet dedup, and byte-identity are unchanged. Peers
        outside the mesh converge via the lazy IHAVE/IWANT tick and the unchanged
        anti-entropy backstop. The relay's :meth:`announce` returns ``None`` (and
        we skip the send) when the CID was already announced.
        """
        names = self._eager_targets(cid)
        frame = self._inv.announce([cid])
        if frame is None:
            return
        cids = inventory.parse_inv_frame(frame)
        self.metrics.incr("inv_announced", len(cids))
        peers = self._resolve_names(names)
        if not peers:
            return
        results = await asyncio.gather(
            *(self._diffused_announce_to(peer, cids) for peer in peers),
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

    async def _diffused_announce_to(self, peer: PeerAddress, cids: list[str]) -> None:
        """Dispatch one peer's ORIGIN inv-announce after an independent diffusion delay.

        Source-privacy diffusion (#93), applied ONLY on the origin path
        (``weave``/``link`` -> :meth:`_eager_announce`). Each peer draws its OWN
        integer-millisecond delay over ``[0, self._diffuse_max_ms]`` (per call, so
        peers are independent), waits it out on the injected sleep clock, then runs
        the UNCHANGED :meth:`_announce_to` inv exchange. Because the delays are
        independent the author is the first announcer with probability ~``1/peers``
        rather than ~``1``, decorrelating announce order from origin.

        The drawn value stays an INTEGER millisecond count; the only division by
        1000 happens at the :func:`asyncio.sleep` boundary (seconds), never on the
        value path — so nothing float touches the canonical/byte path. When
        ``diffuse_max_ms == 0`` we neither draw nor sleep: behaviour is byte- and
        timing-identical to the legacy one-shot broadcast, which is what the
        byte-identity / interop suites assert. Diffusion gates only WHEN the dial
        starts; the #91/#102 ServeBudget still debits per-peer bytes inside
        :meth:`_announce_to` in the same order and count as before.
        """
        if self._diffuse_max_ms:
            delay_ms = self._diffuse_rng.randint(0, self._diffuse_max_ms)
            await self._diffuse_sleep(delay_ms / 1000)
        await self._announce_to(peer, cids)

    async def _announce_to(self, peer: PeerAddress, cids: list[str]) -> None:
        """Run the inv -> getdata -> inv-data exchange against one peer.

        Carrier model: each ``dial`` is one request map -> one response map, and
        every leg is announcer -> peer (so a pure-push topology where the peer has
        no route back to us still works — matching the convergence test, where a
        weaver knows its peers but the peers need not know the weaver).

          1. dial ``inv-announce`` -> peer replies ``inv-getdata`` (CIDs it lacks)
             or ``inv-ack`` (it has them all -> NO body travels: the O(diff) win).
          2. if it wanted any, dial ``inv-data`` carrying ONLY those bodies
             (stored frames verbatim), which the peer ingests.
        """
        resp = await self._send(peer, {"kind": INV, "cids": cids})
        kind = resp.get("kind")
        if kind == "error":
            raise FabricNodeError(f"{resp.get('code')}: {resp.get('message')}")
        if kind != GETDATA:
            # inv-ack (peer wanted nothing) or any non-want response: done. The
            # body was NOT sent — the whole point of the lazy relay.
            return
        wanted = resp.get("cids")
        if not isinstance(wanted, list):
            raise FabricNodeError("inv-getdata cids must be a list")
        # Serve exactly the wanted CIDs as verbatim stored frames, decoded to
        # their envelope maps to ride the dict carrier (canonical CBOR is
        # deterministic, so the inner record + its CID are byte-stable). The same
        # #91 outbound budget applies: when this push is itself answering an
        # inbound IWANT (``_serve_iwant_response``), ``_serve_peer_key`` names that
        # peer so its byte bucket is debited; on a locally-initiated eager weave it
        # is None and only the per-request count cap applies. Either way a serve
        # can never amplify past the fixed batch/byte ceiling.
        frames = self._inv.on_getdata(
            inventory.build_getdata_frame(wanted), peer=self._serve_peer_key
        )
        if not frames:
            return
        records = [wire.read_frame_bytes(fr) for fr in frames]
        self.metrics.incr("inv_served", len(records))
        ack = await self._send(peer, {"kind": "inv-data", "records": records})
        if ack.get("kind") == "error":
            raise FabricNodeError(f"{ack.get('code')}: {ack.get('message')}")

    # -- mesh maintenance + lazy gossip (issue #67 activation) ------------

    def _resolve_names(self, names: "list[str]") -> "list[PeerAddress]":
        """Resolve gossip peer NAMES back to dial targets via the peerbook.

        A name with no peerbook entry (e.g. a stale mesh id whose peer was
        removed) is silently skipped — the mesh is a target *hint*, never a
        source of truth about reachability.
        """
        book = self.peerbook.all()
        return [book[name] for name in names if name in book]

    async def maintain_mesh(self) -> None:
        """Tick one integer heartbeat epoch and ship the resulting GRAFT/PRUNE.

        Caller-driven (deterministic): a test ticks it explicitly; prod can
        piggyback the anti-entropy interval. :meth:`Gossipsub.heartbeat` advances
        the integer epoch and steers each topic mesh into ``[d_low, d_high]``,
        returning ``{name: [control frames]}``; we resolve each name to a dial
        target and push the GRAFT/PRUNE down the existing carrier (stamping our
        stable pubkey so the receiver keys ITS mesh on ``node:<pubkey>``). A peer
        offline this cycle is swallowed exactly like an eager-announce failure;
        the mesh re-steers next heartbeat. No wall-clock — the epoch is the only
        notion of time and it is an integer.
        """
        out = self._gossip.heartbeat([WEB_TOPIC])
        await self._ship_control(out)

    async def gossip_tick(self) -> None:
        """Send a lazy IHAVE digest to every NON-mesh candidate (the fringe path).

        A node in nobody's mesh receives no eager push; this is how it still
        learns held CIDs. We advertise the topic's held ids to each candidate that
        is NOT a current mesh member (mesh members already got the eager push). The
        peer answers the IHAVE *in the dial response* with an IWANT for the ids it
        lacks (no return route needed — fits the one-shot push carrier), and we
        serve exactly those via the unchanged inv getdata path, so bodies travel
        only through #75's verbatim frame store. Idempotent and bounded by the
        SeenSet at both ends.
        """
        frame = self._gossip.build_ihave(WEB_TOPIC)
        if frame is None:
            return
        mesh_members = set(self._gossip.mesh_peers(WEB_TOPIC))
        fringe = [n for n in self._gossip.topic_peers(WEB_TOPIC) if n not in mesh_members]
        book = self.peerbook.all()
        ihave = wire.read_frame_bytes(frame)
        ihave[_MESH_PEER_KEY] = self.pub
        for name in fringe:
            peer = book.get(name)
            if peer is None:
                continue
            try:
                resp = await self._send(peer, dict(ihave))
            except (OSError, FabricNodeError, WireError):
                continue
            await self._serve_iwant_response(peer, resp)

    async def _serve_iwant_response(self, peer: PeerAddress, resp: dict) -> None:
        """A fringe peer answered our IHAVE with an IWANT; serve it via inv-data.

        The IWANT names the CIDs the peer lacks; we push exactly those bodies
        through the EXISTING :meth:`_announce_to` inv path (verbatim stored
        frames), so the lazy fringe converges using the same byte-identical
        transfer as the eager mesh. A non-IWANT reply (peer already held
        everything) is a no-op.
        """
        if not isinstance(resp, dict) or resp.get("kind") != IWANT:
            return
        ids = resp.get("ids")
        if not isinstance(ids, list) or not ids:
            return
        try:
            await self._announce_to(peer, [str(c) for c in ids])
        except (OSError, FabricNodeError, WireError):
            self.metrics.incr("broadcasts_failed")

    async def _ship_control(self, out: "dict[str, list[bytes]]") -> None:
        """Push per-peer GRAFT/PRUNE control frames down the carrier.

        ``out`` is the ``{name: [frame bytes]}`` map :meth:`Gossipsub.heartbeat`
        returns. We decode each ids-only mesh frame to its dict, stamp our stable
        pubkey, and dial it. Per-peer failures are swallowed (offline peer); the
        mesh re-steers on the next heartbeat.
        """
        book = self.peerbook.all()
        for name, frames in out.items():
            peer = book.get(name)
            if peer is None:
                continue
            for fr in frames:
                msg = wire.read_frame_bytes(fr)
                msg[_MESH_PEER_KEY] = self.pub
                try:
                    await self._send(peer, msg)
                except (OSError, FabricNodeError, WireError):
                    continue

    def _signed_record_msg(self, record: dict) -> dict:
        sig = crypto.sign(self._priv, _record_signable(record))
        return {
            "kind": "fabric-record",
            "author": self.pub,
            "record": record,
            "sig": sig,
        }

    def _signed_edge_msg(self, record: dict) -> dict:
        sig = crypto.sign(self._priv, _record_signable(record))
        return {
            "kind": "fabric-edge",
            "author": self.pub,
            "record": record,
            "sig": sig,
        }

    def _store_frame(self, cid: str, envelope: dict, *, authored: bool = False) -> None:
        """Store the verbatim signed-envelope frame bytes for ``cid``.

        The frame is the canonical ``write_frame_bytes`` of the ``fabric-record``
        envelope. ``on_getdata`` returns these bytes UNCHANGED, so the inner
        record dict (and its CID) are byte-identical across a relay hop. Indexing
        is by ``canonical.cid(record)`` — the Web's own content address — never by
        a CID over the envelope (the envelope is re-signed per relayer by design).

        ``authored`` frames (our own weaves) are the authoritative source and are
        NEVER evicted; gossiped-in frames are tracked for LRU eviction and the
        non-authored portion is bounded at ``max_gossiped_frames`` (#92). Store
        management only — the bytes themselves stay verbatim, so byte-identity and
        the CID are untouched.
        """
        self._frames[cid] = wire.write_frame_bytes(envelope)
        if authored:
            self._authored.add(cid)
            return
        if cid in self._authored:
            return  # we also authored it — keep it un-evictable
        try:
            self._gossiped_order.remove(cid)  # re-store refreshes recency
        except ValueError:
            pass
        self._gossiped_order.append(cid)
        # Evict oldest non-authored frames over the cap (re-fetch-safe via anti-entropy).
        while len(self._gossiped_order) > self._max_gossiped:
            old = self._gossiped_order.popleft()
            if old in self._authored:
                continue  # defensive: never drop an authored frame
            self._frames.pop(old, None)

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

    def _ingest_signed(self, item, *, is_edge: "bool | None" = None) -> bool:
        """Verify an author-signed record envelope and weave it. True if new.

        Routing (node vs edge) is decided by the ENVELOPE kind, never by the
        author/attacker-controllable inner ``record['kind']`` field (#144). The
        author emits a node via a ``fabric-record`` envelope (``weave``) and a
        real edge via a ``fabric-edge`` envelope (``link``); the receiver must
        honour that same envelope-level intent so one signed body cannot be a
        NODE on the author and an EDGE on the receiver (a durable state-root
        partition that anti-entropy can never heal). ``is_edge`` lets the routing
        table (:meth:`_route`) pass the envelope kind it already matched; when
        omitted (relay/sync ingest of a stored envelope) we read the SAME
        authoritative signal off ``item['kind']`` on the envelope itself.
        """
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
        if is_edge is None:
            is_edge = item.get("kind") == "fabric-edge"
        # #92 ingest admission: a peer flooding (validly) own-key-signed junk is throttled
        # HERE — before the record lands in the web or the frame store, so a flood is
        # capped at ingest rather than merely evicted after it has consumed memory.
        # Verify ran first (forged sigs still raise → ban-gate penalty); the dropped valid
        # record is re-fetch-safe (anti-entropy re-delivers when the peer's window refills).
        peer = self._serve_peer_key
        if peer is not None:
            size = len(wire.write_frame_bytes(item))
            if self._ingest_budget.take(peer, size) < size:
                self.metrics.incr("ingest_throttled")
                return False
        before_nodes = len(self.web.nodes)
        before_edges = len(self.web._out.get(record.get("src"), []))
        if is_edge:
            src = record.get("src")
            dst = record.get("dst")
            rel = record.get("rel")
            weight = record.get("weight", 1)
            if not isinstance(src, str) or not isinstance(dst, str) or not isinstance(rel, str):
                raise FabricNodeError("fabric edge envelope missing src/dst/rel")
            if not isinstance(weight, int) or isinstance(weight, bool):
                raise FabricNodeError("fabric edge weight must be an int")
            if weight < 0:
                raise FabricNodeError("fabric edge weight must be non-negative")
            if src not in self.web.nodes or dst not in self.web.nodes:
                cid = canonical.cid(record)
                created = False
            else:
                edge = self.web.link(src, dst, rel, weight=weight)
                cid = edge.cid
                created = len(self.web._out.get(src, [])) > before_edges
        else:
            cid = canonical.cid(record)
            self.web.weave(record)
            created = len(self.web.nodes) > before_nodes

        # Store the verbatim envelope frame for this CID so a node that learned a
        # record by relay/sync can re-serve it byte-identically (the FrameLookup
        # backing store must be populated on ingest, not just on weave — else
        # on_getdata returns None and that peer relies on the anti-entropy
        # backstop). Re-sign under our key on serve happens via _signed_record_msg
        # in weave; here we keep the exact verified envelope bytes.
        self._store_frame(cid, item)
        # Mark the CID seen in the relay so a later inv does not re-want it.
        self._inv.on_record(cid)
        if created:
            self.metrics.incr("records_woven")
            return True
        return False

    # -- server side ------------------------------------------------------

    def _serve_sync(self) -> dict:
        # Re-sign every node we hold under *our* key so a catching-up peer can
        # verify provenance of the snapshot it pulls. (Records keep their own
        # CID identity regardless of who relays them.)
        records = [self._signed_record_msg(rec) for rec in self.web.nodes.values()]
        edges = []
        for src in sorted(self.web.nodes):
            for edge in sorted(self.web._out.get(src, []), key=lambda e: (e.rel, e.dst, e.weight)):
                edges.append(self._signed_edge_msg(edge.to_record()))
        records.extend(edges)
        return {"kind": "fabric-sync-data", "records": records}

    def _route(self, kind, msg: dict, source_id: "str | None" = None) -> dict:
        """Fabric routing table: ingest a gossiped record, or serve a sync snapshot.

        (``source_id`` is accepted for the shared :meth:`BaseNode._dispatch` signature; the
        fabric node does no PEX, so it is unused — the #94 source-group keying lives in
        :class:`~knitweb.p2p.node.AsyncioP2PNode`.)

        A forged author signature raises ``FabricNodeError("invalid author
        signature ...")`` here; the shared :meth:`BaseNode._dispatch` catches it,
        charges the relaying peer an :class:`Offense.INVALID_SIGNATURE` penalty
        (the message carries the word "signature"), and refuses — so the offense
        lands uniformly on the live TCP path and the relay path alike (#52), with
        no node-specific ``_serve_connection`` override to keep in sync.
        """
        if kind == "fabric-record":
            # Envelope-authoritative routing (#144): a 'fabric-record' envelope is
            # ALWAYS a node, regardless of an inner record['kind']=='edge' the
            # author may have set — so the same signed body is a node on both
            # author (weave) and receiver, never a divergent edge.
            self._ingest_signed(msg, is_edge=False)
            return {"kind": "fabric-ack"}
        elif kind == "fabric-edge":
            # A genuine 'fabric-edge' envelope (Edge.to_record() shape, #108) is
            # ALWAYS an edge — the legitimate edge path is preserved exactly.
            self._ingest_signed(msg, is_edge=True)
            return {"kind": "fabric-ack"}
        elif kind == "fabric-sync-request":
            return self._serve_sync()
        elif kind == INV:
            return self._serve_inv(msg)
        elif kind == GETDATA:
            return self._serve_getdata(msg)
        elif kind == "inv-data":
            return self._serve_inv_data(msg)
        elif kind in (RECON_REQ, RECON_RANGE):
            return self._serve_recon(kind, msg)
        elif kind in (GRAFT, PRUNE, IHAVE, IWANT):
            return self._serve_mesh(kind, msg)
        return {"kind": "error", "code": "unknown-kind", "message": str(kind)}

    # -- gossipsub mesh control server side (issue #67) -------------------

    def _serve_mesh(self, kind, msg: dict) -> dict:
        """Handle an inbound mesh control frame, delegating to the unchanged mesh.

        Mesh frames carried over the dict carrier ride an extra ``_MESH_PEER_KEY``
        naming the SENDER's stable pubkey (its gossip peer-id). We register that id
        as a topic candidate (so reciprocal GRAFT/PRUNE/score state keys on the
        stable ``node:<pubkey>``, never an ephemeral carrier id — #58), strip the
        key, and re-encode the ids-only frame bytes mesh.py expects. mesh.py is
        reused UNEDITED; no record body ever rides a mesh frame, so byte-identity
        is preserved trivially.

          * GRAFT -> :meth:`Gossipsub.on_graft` (bounce a PRUNE or accept).
          * PRUNE -> :meth:`Gossipsub.on_prune`.
          * IHAVE -> :meth:`Gossipsub.on_ihave`: reply an IWANT for ids we lack,
            so the announcer serves them via the inv path IN-BAND on this round
            trip (the fringe pull). No body travels in this response.
          * IWANT -> :meth:`Gossipsub.on_iwant`: return a GETDATA of the held ids
            so the body moves through the EXISTING inv getdata path verbatim.
        """
        sender = msg.get(_MESH_PEER_KEY)
        if not isinstance(sender, str) or not sender:
            raise FabricNodeError("mesh control frame missing peer id")
        frame = wire.write_frame_bytes({k: v for k, v in msg.items() if k != _MESH_PEER_KEY})
        # Make the sender a known candidate so on_graft/score state can key on it.
        self._gossip.add_peer(WEB_TOPIC, sender)
        if kind == GRAFT:
            reply = self._gossip.on_graft(sender, frame)
            if reply is None:
                return {"kind": "mesh-ack"}
            bounce = wire.read_frame_bytes(reply)
            bounce[_MESH_PEER_KEY] = self.pub
            return bounce
        if kind == PRUNE:
            self._gossip.on_prune(sender, frame)
            return {"kind": "mesh-ack"}
        if kind == IHAVE:
            want = self._gossip.on_ihave(sender, frame)
            if want is None:
                return {"kind": "mesh-ack"}
            return wire.read_frame_bytes(want)
        # IWANT: return the held ids as a getdata so the sender serves the bodies
        # through the inv path. We hold them in the frame store keyed by CID.
        ids = self._gossip.on_iwant(sender, frame)
        if not ids:
            return {"kind": "mesh-ack"}
        return {"kind": GETDATA, "cids": ids}

    # -- inventory (lazy relay) server side (#64) -------------------------

    def _serve_inv(self, msg: dict) -> dict:
        """Handle an inbound ``inv-announce``: reply with the CIDs we lack.

        Diffs the announced CIDs against our frame store AND the relay SeenSet via
        :meth:`InventoryRelay.on_inv`. Returns an ``inv-getdata`` naming exactly
        the CIDs we want (so the announcer sends only those bodies), or an
        ``inv-ack`` when we already hold everything — in which case NO body ever
        travels (the O(diff) collapse). A malformed inv frame raises
        ``InventoryError`` (a ``ValueError`` subclass), which the shared
        ``_dispatch`` maps to a ``bad-request`` with no new error wiring.
        """
        cids = msg.get("cids")
        if not isinstance(cids, list):
            raise FabricNodeError("inv-announce cids must be a list")
        want_frame = self._inv.on_inv(inventory.build_inv_frame(cids))
        if want_frame is None:
            return {"kind": "inv-ack"}
        wanted = inventory.parse_getdata_frame(want_frame)
        self.metrics.incr("inv_wanted", len(wanted))
        return {"kind": GETDATA, "cids": wanted}

    def _serve_inv_data(self, msg: dict) -> dict:
        """Handle an inbound ``inv-data``: ingest the served record envelopes.

        Each envelope flows through the same :meth:`_ingest_signed` crypto gate as
        a ``fabric-record`` or a ``fabric-sync-data`` item, so a forged body served
        via the lazy path is rejected identically — the Byzantine tests are
        unaffected. Ingest stores the verbatim frame and marks the relay seen.
        """
        records = msg.get("records")
        if not isinstance(records, list):
            raise FabricNodeError("inv-data records must be a list")
        for item in records:
            self._ingest_signed(item)
        return {"kind": "inv-ack"}

    def _serve_getdata(self, msg: dict) -> dict:
        """Handle an inbound ``inv-getdata`` (the reconcile PULL leg): serve bodies.

        A reconciling peer that discovered it lacks some CIDs dials this with the
        exact symmetric-difference CIDs it wants. We return an ``inv-data`` carrying
        ONLY those bodies — the **stored frame bytes verbatim** via
        :meth:`InventoryRelay.on_getdata`, decoded to their envelope maps to ride
        the dict carrier — so the served bytes (and each record's CID) are
        byte-identical, and a CID we do not hold is silently skipped (never
        fabricated). This is the inverse direction of the eager :meth:`_announce_to`
        push: there WE serve after announcing; here the peer pulls exactly its diff.
        """
        cids = msg.get("cids")
        if not isinstance(cids, list):
            raise FabricNodeError("inv-getdata cids must be a list")
        # Serve under the #91 per-peer outbound budget: at most MAX_GETDATA_BATCH
        # bodies, and at most the requesting peer's remaining bytes/window. The
        # peer key is the one this dispatch resolved (proven node id when a proof
        # rode along, else carrier id, else None -> count-cap-only). Un-served CIDs
        # are re-requested next reconcile round (O(remaining-diff)), so an honest
        # large diff still converges across windows; a pathological whole-inventory
        # pull is capped here instead of reflecting hundreds of GiB.
        frames = self._inv.on_getdata(
            inventory.build_getdata_frame(cids), peer=self._serve_peer_key
        )
        if not frames:
            return {"kind": "inv-ack"}
        records = [wire.read_frame_bytes(fr) for fr in frames]
        self.metrics.incr("inv_served", len(records))
        return {"kind": "inv-data", "records": records}

    # -- Erlay reconcile server side (issue #60) --------------------------

    def _serve_recon(self, kind, msg: dict) -> dict:
        """Handle an inbound reconcile batch: drive the RESPONDER session, reply.

        A reconcile session is a multi-round bisection; on the one-shot carrier
        each round is one dial, so we keep a :class:`ReconcileSession` alive per
        session id (stamped by the initiator in ``_RECON_SESSION_KEY``). On the
        OPENING batch (``inv-recon-req``) we create the session over our current
        held CID set; on each batch we feed its frames to the session and reply with
        the frames it produced. An empty reply means we pruned/answered every range
        — the session has converged — so we drop it (bounded state). Only range
        summaries and CID lists ride these envelopes; no record body, so a signed
        record's byte-identity is untouched. The reply is an ``inv-recon-result``.
        """
        session_id = msg.get(_RECON_SESSION_KEY)
        if not isinstance(session_id, str) or not session_id:
            raise FabricNodeError("reconcile frame missing session id")
        frames = msg.get("frames")
        if not isinstance(frames, list):
            raise FabricNodeError("reconcile frames must be a list")
        batch = [bytes(fr) for fr in frames]
        if kind == RECON_REQ:
            # A fresh request opens a new responder session over our held CIDs. Cap
            # the session table so a peer cannot leak unbounded session state.
            if len(self._recon_sessions) >= _MAX_RECON_SESSIONS:
                self._recon_sessions.pop(next(iter(self._recon_sessions)))
            session = ReconcileSession(self._held_cids())
            self._recon_sessions[session_id] = session
        else:
            session = self._recon_sessions.get(session_id)
            if session is None:
                # Unknown/expired session (e.g. we converged + dropped it already):
                # an empty result ends the exchange cleanly on the initiator side.
                return {"kind": RECON_RESULT, "frames": []}
        reply = session.respond(batch)
        if not reply or session.done:
            # Converged on our side: free the session state immediately.
            self._recon_sessions.pop(session_id, None)
        return {"kind": RECON_RESULT, "frames": reply}
