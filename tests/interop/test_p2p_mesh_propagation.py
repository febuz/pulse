"""Gossipsub mesh activation (#67) layered on the #75 inv->getdata propagation.

The dormant gossipsub mesh (:mod:`knitweb.p2p.mesh`) is now WIRED into the live
:class:`~knitweb.fabric.node.FabricNode`: a weave eager-pushes a record's CID
ONLY to the bounded ``<=D`` topic mesh (not to every peer), non-mesh peers learn
held CIDs through a lazy ``mesh-ihave`` digest and pull the bodies via
``mesh-iwant`` -> the EXISTING inv ``getdata`` path (so bodies still travel only
through #75's verbatim frame store), and a caller-driven heartbeat maintains the
mesh degree within ``[d_low, d_high]`` via GRAFT/PRUNE. This suite proves the
activation end to end over an in-memory carrier (no real socket / handshake;
every dial is bounded by ``asyncio.wait_for``):

  * **bounded eager fan-out** — with ``D < peer-count`` the eager announce reaches
    only the ``O(D)`` mesh members, never all candidates;
  * **lazy fringe delivery** — a peer in NOBODY's mesh still RECEIVES the record
    via the IHAVE/IWANT lazy path (resolved through inv-data verbatim);
  * **partial-mesh + churn convergence** — a multi-node web with a bounded mesh
    and a peer that drops + rejoins all settle on one identical ``state_root``;
  * **degree band** — the mesh degree stays within ``[d_low, d_high]`` across
    heartbeats;
  * **byte-identity** — a relayed record's CID == the author's CID ==
    ``core.canonical.cid(record)`` and the stored frame is served verbatim.

All assertions are on integer Web sizes / mesh degrees, hex ``state_root``
witnesses, and CID strings, so a woven Knit's content address is never perturbed.
"""

import asyncio
import random

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.items import web_state_root
from knitweb.fabric.node import FabricNode, WEB_TOPIC, _MESH_PEER_KEY
from knitweb.p2p import identity, wire
from knitweb.p2p.inventory import INV
from knitweb.p2p.mesh import Gossipsub, MeshParams, build_graft_frame
from knitweb.p2p.relay import ENVELOPE_PEER_KEY, ENVELOPE_ID_PROOF_KEY
from knitweb.p2p.transport import PeerAddress


# ── in-memory carrier (socket-free, asyncio.wait_for bounded) ─────────────────

class _MemTransport:
    """A socket-free Transport routing a dial straight to a peer's ``_dispatch``.

    Mirrors the carrier the #75 inventory-relay interop test uses: a dial frames
    the request through the SAME canonical-CBOR codec the real carriers use, hands
    the decoded map to the target's ``_dispatch`` seam (what the live accept loop
    feeds), and frames the response back. Per-kind inbound byte tallies let a test
    prove which message kinds — and how big a fan-out — actually crossed.
    """

    tag = "mem"

    def __init__(self, registry: dict, node_id: int) -> None:
        self._registry = registry
        self._node_id = node_id
        self.bytes_in_by_kind: dict[str, int] = {}
        self.calls_in_by_kind: dict[str, int] = {}

    def bind(self, node) -> None:
        self._node = node
        self._registry[self._node_id] = self

    async def dial(self, peer: PeerAddress, request: dict) -> dict:
        target = self._registry[int(peer.params["id"])]
        raw = wire.write_frame_bytes(request)
        decoded = wire.read_frame_bytes(raw)
        kind = str(decoded.get("kind"))
        target.bytes_in_by_kind[kind] = target.bytes_in_by_kind.get(kind, 0) + len(raw)
        target.calls_in_by_kind[kind] = target.calls_in_by_kind.get(kind, 0) + 1
        resp = await asyncio.wait_for(target._node._dispatch(decoded), timeout=5)
        return wire.read_frame_bytes(wire.write_frame_bytes(resp))

    async def listen(self, handler, on_frame_fault=None) -> None:  # pragma: no cover
        return None

    async def close(self) -> None:  # pragma: no cover
        return None

    def local_address(self) -> PeerAddress:
        return PeerAddress(transport="mem", params={"id": str(self._node_id)})


def _mem_node(registry: dict, node_id: int, **kw) -> FabricNode:
    tr = _MemTransport(registry, node_id)
    node = FabricNode(transport=tr, **kw)
    tr.bind(node)
    return node


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=10))


def _converged(*nodes: FabricNode) -> bool:
    return len({n.state_root for n in nodes}) == 1


def _calls(node: FabricNode) -> dict:
    return node.transport.calls_in_by_kind


def _knowledge(author_pub: str, title: str) -> dict:
    return {"kind": "knowledge", "title": title, "body": title, "author": author_pub}


# ── 1. eager fan-out is bounded to the mesh (O(D), not O(all)) ────────────────

@pytest.mark.interop
def test_eager_announce_reaches_only_mesh_members_not_all_peers():
    """With D < peer-count, a weave's inv-announce reaches only the <=D mesh.

    A weaver with many candidates and a small ``D`` runs a heartbeat to graft a
    bounded mesh, then weaves. The number of peers that received an ``inv``
    announce equals the mesh degree (<=D), strictly fewer than the candidate
    count — the O(all) -> O(D) fan-out reduction. Mesh members converge; non-mesh
    peers do NOT receive the eager push (they would converge via the lazy/anti-
    entropy channels, exercised separately).
    """
    async def scenario():
        reg: dict = {}
        params = MeshParams(d=2, d_low=2, d_high=4)
        a = _mem_node(reg, 1, gossip=Gossipsub(rng=random.Random(7), params=params))
        peers = [_mem_node(reg, i + 2) for i in range(6)]
        for i, p in enumerate(peers):
            a.add_peer(f"p{i + 2}", p.address)

        # Cold mesh -> first heartbeat grafts up to D candidates.
        await a.maintain_mesh()
        mesh = set(a._gossip.mesh_peers(WEB_TOPIC))
        assert 0 < len(mesh) <= params.d  # bounded eager set

        cid = await a.weave(_knowledge(a.pub, "alpha"))

        # Exactly the mesh members received the inv announce — O(D), not O(6).
        got_inv = [p for i, p in enumerate(peers) if _calls(p).get(INV, 0) > 0]
        assert len(got_inv) == len(mesh)
        assert len(got_inv) < len(peers)  # strictly bounded below the candidate count

        # Every mesh member converged on the weaver via the eager push...
        for i, p in enumerate(peers):
            if f"p{i + 2}" in mesh:
                assert p.web.get(cid) is not None
                assert _converged(a, p)
        # ...and a non-mesh peer got NO inv announce at all (pure eager isolation).
        non_mesh = [p for i, p in enumerate(peers) if f"p{i + 2}" not in mesh]
        assert non_mesh, "test needs at least one non-mesh peer"
        assert all(_calls(p).get(INV, 0) == 0 for p in non_mesh)

    run(scenario())


# ── 2. a non-mesh peer still RECEIVES the record via lazy IHAVE/IWANT ─────────

@pytest.mark.interop
def test_non_mesh_peer_receives_record_via_lazy_ihave_iwant():
    """A peer in nobody's mesh converges via the lazy gossip pull (IHAVE->IWANT).

    The weaver grafts a bounded mesh that EXCLUDES one peer, weaves (that peer
    gets no eager push), then runs one ``gossip_tick``: it sends the excluded peer
    a ``mesh-ihave`` digest; the peer answers ``mesh-iwant`` for the CID it lacks
    and the weaver serves the body through the EXISTING inv-data path. The fringe
    peer ends up holding the verbatim record (same CID) and converged.
    """
    async def scenario():
        reg: dict = {}
        params = MeshParams(d=2, d_low=2, d_high=4)
        a = _mem_node(reg, 1, gossip=Gossipsub(rng=random.Random(3), params=params))
        peers = [_mem_node(reg, i + 2) for i in range(5)]
        for i, p in enumerate(peers):
            a.add_peer(f"p{i + 2}", p.address)

        await a.maintain_mesh()
        mesh = set(a._gossip.mesh_peers(WEB_TOPIC))
        fringe_idx = next(i for i, p in enumerate(peers) if f"p{i + 2}" not in mesh)
        fringe = peers[fringe_idx]

        cid = await a.weave(_knowledge(a.pub, "lazy"))
        # The fringe peer got NO eager push: zero inv announces from the weave.
        assert fringe.web.get(cid) is None
        assert _calls(fringe).get(INV, 0) == 0
        assert _calls(fringe).get("mesh-ihave", 0) == 0

        # One lazy gossip tick: the weaver IHAVEs the fringe; it IWANTs the CID it
        # lacks; the weaver serves the body through the inv getdata path. The lazy
        # serve reuses _announce_to, so the fringe sees ONE inv announce + ONE
        # inv-data here — both triggered by the IHAVE digest, not the eager weave.
        await a.gossip_tick()

        # The fringe peer now holds the verbatim record (byte-identical CID) and
        # converged purely over the lazy IHAVE-initiated path.
        assert fringe.web.get(cid) is not None
        assert canonical.cid(fringe.web.get(cid)) == cid
        assert _converged(a, fringe)
        assert _calls(fringe).get("mesh-ihave", 0) > 0  # the lazy digest arrived
        assert _calls(fringe).get("inv-data", 0) > 0     # body via verbatim inv path
        assert _calls(fringe).get(INV, 0) == 1           # exactly the lazy serve's announce

    run(scenario())


# ── 3. mesh control frames carry only ids — byte-identity preserved ───────────

@pytest.mark.interop
def test_mesh_control_frames_carry_only_ids_no_record_body():
    """mesh-graft / mesh-ihave frames carry ids/topic + sender id, never a body.

    The body always travels through the inv-data path; a mesh frame never
    re-encodes a record, so a signed record's CID byte-identity is preserved
    trivially. We intercept the actual control maps the weaver dials and assert
    their keys are exactly the ids-only mesh schema plus the sender id envelope
    key — no ``author`` / ``record`` / ``sig`` body fields ever ride a mesh kind.
    """
    async def scenario():
        reg: dict = {}
        a = _mem_node(reg, 1, gossip=Gossipsub(rng=random.Random(1)))
        peers = [_mem_node(reg, i + 2) for i in range(4)]
        for i, p in enumerate(peers):
            a.add_peer(f"p{i + 2}", p.address)

        # Intercept every outbound dial to capture the raw control maps.
        sent: list[dict] = []
        orig_send = a._send

        async def spy(peer, msg):
            sent.append(msg)
            return await orig_send(peer, msg)

        a._send = spy

        await a.maintain_mesh()             # GRAFT frames
        await a.weave(_knowledge(a.pub, "x"))
        await a.gossip_tick()               # IHAVE frames

        mesh_kinds = {"mesh-graft", "mesh-prune", "mesh-ihave", "mesh-iwant"}
        seen_mesh = [m for m in sent if m.get("kind") in mesh_kinds]
        assert seen_mesh, "expected at least one mesh control frame on the wire"
        for m in seen_mesh:
            # ids-only schema: kind/topic/ids/cids + the sender-id envelope key.
            assert set(m) <= {"kind", "topic", "ids", "cids", _MESH_PEER_KEY}
            # Never a record body.
            assert "author" not in m and "record" not in m and "sig" not in m
            # The sender id is this node's AUTHENTICATED mesh id: its proven
            # ``node:<pubkey>`` (#143/#89), which the receiver's #143
            # identity-binding check (``asserted == proven``) admits. A keyed node
            # asserts exactly the id its piggybacked proof proves, so a forged
            # ``_MESH_PEER_KEY`` over a proven carrier can never mint a candidate.
            assert m[_MESH_PEER_KEY] == a._mesh_self_id()

    run(scenario())


# ── 4. mesh degree stays within [d_low, d_high] across heartbeats ─────────────

@pytest.mark.interop
def test_mesh_degree_stays_within_band_over_heartbeats():
    """The bounded-mesh invariant holds through the live node heartbeat driver."""
    async def scenario():
        reg: dict = {}
        params = MeshParams(d=4, d_low=3, d_high=6)
        a = _mem_node(reg, 1, gossip=Gossipsub(rng=random.Random(11), params=params))
        peers = [_mem_node(reg, i + 2) for i in range(20)]
        for i, p in enumerate(peers):
            a.add_peer(f"p{i + 2}", p.address)

        for _ in range(15):
            await a.maintain_mesh()
            deg = a._gossip.mesh_degree(WEB_TOPIC)
            assert deg <= params.d_high
            assert params.d_low <= deg  # plenty of candidates -> at/above d_low
        assert params.d_low <= a._gossip.mesh_degree(WEB_TOPIC) <= params.d_high

    run(scenario())


# ── 5. partial mesh + churn: all nodes converge on one state_root ─────────────

@pytest.mark.interop
def test_partial_mesh_plus_churn_converges_to_one_state_root():
    """A bounded mesh + a churning peer (drop + rejoin) -> one identical root.

    The weaver keeps a STRICT-subset mesh (``D`` < peer-count), so eager push
    never reaches every peer. Convergence relies on the union of three channels:
    eager mesh push, the lazy IHAVE/IWANT tick (reaches the fringe), and the
    anti-entropy backstop (catches a peer that churned through a publish gap). One
    peer drops mid-stream (its candidacy removed), the weaver weaves more, the peer
    rejoins fresh (same identity, empty Web) and pulls via sync_from. Every node
    settles on the weaver's post-churn root.
    """
    async def scenario():
        reg: dict = {}
        params = MeshParams(d=2, d_low=2, d_high=3)
        a = _mem_node(reg, 1, gossip=Gossipsub(rng=random.Random(5), params=params))
        peers = [_mem_node(reg, i + 2) for i in range(5)]
        names = [f"p{i + 2}" for i in range(5)]
        for name, p in zip(names, peers):
            a.add_peer(name, p.address)

        await a.maintain_mesh()
        assert a._gossip.mesh_degree(WEB_TOPIC) <= params.d
        assert a._gossip.mesh_degree(WEB_TOPIC) < len(peers)  # strict partial mesh

        # Weave two records, then run a lazy tick so the fringe pulls them.
        c0 = await a.weave(_knowledge(a.pub, "r0"))
        c1 = await a.weave(_knowledge(a.pub, "r1"))
        await a.gossip_tick()

        # Eager-mesh members + lazy-fringe peers all converged on the two records.
        # (build_ihave digest covers the fringe; anti-entropy is the hard backstop
        #  used below for the reborn peer.)
        for p in peers:
            # Either the eager push or the lazy tick delivered both records.
            if p.web.size != (2, 0):
                # Fringe peers that were not reached by this single tick fall back
                # to the anti-entropy backstop: pull the weaver's full set.
                await p.sync_from(a.address)
            assert p.web.get(c0) is not None
            assert p.web.get(c1) is not None
        assert _converged(a, *peers)

        # --- churn: drop p (remove its candidacy), weave more, then rejoin ---
        churned_name = names[-1]
        churned = peers[-1]
        a._gossip.remove_peer(WEB_TOPIC, churned_name)
        # Weave during the gap: the dropped peer misses the eager push entirely.
        c2 = await a.weave(_knowledge(a.pub, "r2"))
        await a.maintain_mesh()  # re-steer the mesh after the drop
        await a.gossip_tick()

        # The remaining peers track the new record (eager mesh or lazy tick;
        # anti-entropy backstop otherwise).
        for name, p in zip(names[:-1], peers[:-1]):
            if p.web.get(c2) is None:
                await p.sync_from(a.address)
            assert p.web.get(c2) is not None

        # The reborn peer: fresh listener (new transport id), same identity, empty
        # Web. It re-syncs to the weaver's post-churn root via the unchanged
        # anti-entropy backstop — convergence holds across the churn gap.
        reborn = _mem_node(reg, 99, priv=churned._priv)
        assert reborn.web.size == (0, 0)
        await reborn.sync_from(a.address)
        assert reborn.web.size == (3, 0)
        assert reborn.web.get(c2) is not None

        assert _converged(a, *peers[:-1], reborn)
        assert web_state_root(reborn.web) == a.state_root

    run(scenario())


# ── 6. tiny net (D >= peer-count) behaves exactly like #75 all-peer announce ──

@pytest.mark.interop
def test_tiny_net_eager_announce_reaches_all_peers_like_75():
    """With D >= peer-count the mesh is effectively all peers: #75 fan-out intact.

    A two/three-node web with the default ``D`` (>= the candidate count) grafts
    EVERY candidate into the mesh on heartbeat, so publish targets all of them and
    the eager path is byte-for-byte #75's all-peer announce. AND: a weave issued
    BEFORE any heartbeat (cold mesh) must still reach every peer via the all-
    candidates fallback — this is the load-bearing tiny-net regression guard.
    """
    async def scenario():
        reg: dict = {}
        a = _mem_node(reg, 1)  # default MeshParams(d=6) >= 2 peers
        b = _mem_node(reg, 2)
        c = _mem_node(reg, 3)
        a.add_peer("b", b.address)
        a.add_peer("c", c.address)

        # Weave immediately, BEFORE any heartbeat: cold mesh -> all-candidates
        # fallback still eager-pushes to both peers (the #75 behaviour).
        cid = await a.weave(_knowledge(a.pub, "cold"))
        assert b.web.get(cid) is not None
        assert c.web.get(cid) is not None
        assert _converged(a, b, c)

        # After a heartbeat D(=6) >= 2 grafts both, so publish == all candidates.
        await a.maintain_mesh()
        assert set(a._gossip.mesh_peers(WEB_TOPIC)) == {"b", "c"}
        cid2 = await a.weave(_knowledge(a.pub, "warm"))
        assert b.web.get(cid2) is not None
        assert c.web.get(cid2) is not None
        assert _converged(a, b, c)

    run(scenario())


# ── 7. the #78 gossip scheduler ticks maintain_mesh + gossip_tick in prod ─────

class _VirtualClock:
    """Injected sleep: elapses an integer virtual clock, no real time.

    Mirrors the anti-entropy node test's clock. ``await sleep(delay)`` records the
    integer delay and yields once to the loop (``asyncio.sleep(0)``) so the
    background gossip round's in-memory dials get a chance to run — but zero real
    wall-clock time passes, so the cadence is deterministic and the suite is fast.
    """

    def __init__(self) -> None:
        self.now = 0
        self.delays: list[int] = []

    async def sleep(self, delay: int) -> None:
        assert isinstance(delay, int) and not isinstance(delay, bool)
        assert delay >= 0
        self.delays.append(delay)
        self.now += delay
        await asyncio.sleep(0)


async def _settle(predicate, *, limit: int = 400) -> bool:
    """Yield to the loop until ``predicate()`` holds (bounded), no real sleeps."""
    for _ in range(limit):
        if predicate():
            return True
        await asyncio.sleep(0)
    return predicate()


@pytest.mark.interop
def test_gossip_scheduler_off_by_default_does_not_change_serve():
    """Opt-in: a plain start() launches NO gossip loop (existing serve behaviour)."""
    async def scenario():
        a = _mem_node({}, 1)
        async with a:
            assert a._gossip_task is None
            await a.weave(_knowledge(a.pub, "x"))
            assert a._gossip_task is None  # weaving never starts the loop either

    run(scenario())


@pytest.mark.interop
def test_start_gossip_drives_maintain_mesh_then_gossip_tick_and_converges_mesh():
    """start_gossip ticks maintain_mesh + gossip_tick on a loop; the mesh grows to D.

    A weaver with many candidates and a small ``D`` starts the #78 background
    scheduler on a virtual clock. After several injected ticks BOTH heartbeat
    halves ran (maintain_mesh built a bounded mesh that converges toward ``D``, and
    gossip_tick lazily reached the fringe so it holds the record), and the
    byte-identity of a freshly woven Knit is unperturbed. stop_gossip then cancels
    the loop cleanly. Socket-free (in-memory carrier), asyncio.wait_for bounded.
    """
    async def scenario():
        reg: dict = {}
        clock = _VirtualClock()
        params = MeshParams(d=3, d_low=2, d_high=4)
        a = _mem_node(reg, 1, gossip=Gossipsub(rng=random.Random(13), params=params))
        peers = [_mem_node(reg, i + 2) for i in range(8)]
        for i, p in enumerate(peers):
            a.add_peer(f"p{i + 2}", p.address)

        # A record woven cold (pre-mesh) keeps its exact content address — the
        # scheduler is a heartbeat, it touches no record/CID.
        cid = await a.weave(_knowledge(a.pub, "seed"))
        assert canonical.cid(a.web.get(cid)) == cid

        epoch0 = a._gossip.epoch
        a.start_gossip(interval=1, sleep=clock.sleep)
        assert a._gossip_task is not None

        # After N injected ticks: the heartbeat epoch advanced (maintain_mesh ran
        # repeatedly) and the mesh degree converged toward D within the band.
        assert await _settle(
            lambda: params.d_low <= a._gossip.mesh_degree(WEB_TOPIC) <= params.d_high
        )
        assert a._gossip.epoch > epoch0          # maintain_mesh ticked
        assert len(clock.delays) >= 1 and set(clock.delays) == {1}  # integer cadence

        # gossip_tick ran too: at least one NON-mesh fringe peer received the lazy
        # mesh-ihave digest and converged on the seed CID over the lazy path.
        mesh = set(a._gossip.mesh_peers(WEB_TOPIC))
        fringe = [p for i, p in enumerate(peers) if f"p{i + 2}" not in mesh]
        assert fringe, "test needs at least one fringe peer (D < peer-count)"
        assert await _settle(
            lambda: any(_calls(p).get("mesh-ihave", 0) > 0 for p in fringe)
        )
        assert any(p.web.get(cid) is not None for p in fringe)

        # A fresh weave AFTER the scheduler ran is still byte-identical.
        cid2 = await a.weave(_knowledge(a.pub, "after"))
        assert canonical.cid(a.web.get(cid2)) == cid2

        # stop_gossip cancels the loop cleanly and clears the handle.
        await a.stop_gossip()
        assert a._gossip_task is None

    run(scenario())


@pytest.mark.interop
def test_start_gossip_swallows_a_throwing_tick_and_keeps_looping():
    """A round that raises does NOT kill the loop — the next heartbeat still ticks.

    maintain_mesh is monkeypatched to throw on its first call, then behave. The
    scheduler must swallow that raise (mirrors AntiEntropy's failed-round swallow)
    and keep ticking, so the epoch still advances on a later cycle and the loop is
    still alive (cancellable) at the end.
    """
    async def scenario():
        reg: dict = {}
        clock = _VirtualClock()
        a = _mem_node(reg, 1, gossip=Gossipsub(rng=random.Random(2)))
        for i in range(4):
            a.add_peer(f"p{i + 2}", _mem_node(reg, i + 2).address)

        real_maintain = a.maintain_mesh
        state = {"calls": 0}

        async def flaky_maintain():
            state["calls"] += 1
            if state["calls"] == 1:
                raise RuntimeError("boom: simulated offline-peer round failure")
            await real_maintain()

        a.maintain_mesh = flaky_maintain  # type: ignore[method-assign]

        epoch0 = a._gossip.epoch
        a.start_gossip(interval=1, sleep=clock.sleep)

        # The first tick raised; the loop survived and a later tick advanced the
        # heartbeat epoch — proof the raise was swallowed, not fatal.
        assert await _settle(lambda: a._gossip.epoch > epoch0)
        assert state["calls"] >= 2
        assert not a._gossip_task.done()  # loop still alive after the throw

        await a.stop_gossip()
        assert a._gossip_task is None

    run(scenario())


# ── #143: mesh peer-id auth (unbounded candidate growth + sybil eclipse) ──────
#
# Root cause: ``_serve_mesh`` trusted the unsigned ``_MESH_PEER_KEY`` body field
# to key ``_scores``/``_topic_peers`` (neither is capacity-bounded) and the mesh.
# One carrier could mint a fresh fabricated peer-id per request -> unbounded
# growth (IMPACT #1); 12 fabricated ids from one connection start at score 0 and
# saturate the d_high=12 mesh, PRUNE-bouncing honest GRAFTs -> eclipse (#2). The
# fix binds the mesh candidate to the ALREADY-RESOLVED dispatch identity
# (``_serve_peer_key``: proven ``node:<pubkey>`` when a proof rode along, else the
# carrier id), so a forged id over a proven carrier mints nothing and ALL ids from
# one carrier collapse to one candidate. These tests are load-bearing: reverting
# the bind (keying ``add_peer`` on the raw asserted id) returns BOTH regressions.

_MESH_VICTIM_PARAMS = MeshParams(d=6, d_low=4, d_high=12)


def _victim_node() -> FabricNode:
    """A receiver-only mesh node with a deterministic gossip RNG (#143 tests)."""
    reg: dict = {}
    return _mem_node(
        reg, 1, gossip=Gossipsub(rng=random.Random(7), params=_MESH_VICTIM_PARAMS)
    )


def _spoofed_graft(asserted_id: str, carrier_id: str) -> dict:
    """A GRAFT frame asserting ``asserted_id`` over an identified ``carrier_id``,
    with NO identity proof (the unidentified-author case the attacker uses)."""
    msg = wire.read_frame_bytes(build_graft_frame(WEB_TOPIC))
    msg[_MESH_PEER_KEY] = asserted_id
    msg[ENVELOPE_PEER_KEY] = carrier_id
    return msg


def _proven_graft(victim, financial_priv, carrier_id, *, asserted_override=None):
    """A GRAFT carrying a REAL identity proof for ``financial_priv``'s identity key
    over ``carrier_id``. Returns the frame and the proven ``node:<pubkey>``.

    The proof is BOUND TO THE BUSINESS BODY (#90) exactly as a real dialer's
    ``_stamp_id_proof`` binds it — over the graft map with ``_MESH_PEER_KEY`` set
    but the ``_relay_*`` envelope keys excluded (the body the receiver reconstructs
    after popping those keys). This keeps the serve-path key resolution and the ban
    gate judging the proof under the IDENTICAL binding (the one identity-keying
    authority), not a weaker empty binding. ``asserted`` defaults to the proven id
    (an honest GRAFT) but can be overridden to forge a DIFFERENT id while presenting
    a valid proof (the impersonation case)."""
    id_key = identity.network_signing_key(financial_priv)
    proven = identity.node_peer_id(crypto.public_from_private(id_key))
    msg = wire.read_frame_bytes(build_graft_frame(WEB_TOPIC))
    msg[_MESH_PEER_KEY] = proven if asserted_override is None else asserted_override
    # Bind over the business body the receiver will reconstruct (the graft map with
    # the peer key, before the _relay_* envelope keys are added).
    binding = crypto.sha256(canonical.encode(msg))
    msg[ENVELOPE_PEER_KEY] = carrier_id
    proof = identity.make_id_proof(
        id_key, timestamp=victim._id_proof_now(), binding=binding
    )
    msg[ENVELOPE_ID_PROOF_KEY] = identity.id_proof_to_record(proof)
    return msg, proven


def _topic_peer_count(node) -> int:
    return len(node._gossip._topic_peers.get(WEB_TOPIC, set()))


def _score_count(node) -> int:
    return len(node._gossip._scores)


@pytest.mark.interop
def test_143_spoofed_peer_ids_from_one_carrier_add_at_most_one_candidate():
    """IMPACT #1 bounded: N fabricated ``_MESH_PEER_KEY`` strings over ONE carrier
    add <=1 entry to BOTH ``_scores`` and ``_topic_peers`` (was ~N before the
    fix), because the candidate is keyed on the carrier id, not the spoofed id."""
    async def scenario():
        n = _victim_node()
        N = 200
        assert _score_count(n) == 0 and _topic_peer_count(n) == 0
        for i in range(N):
            await n._dispatch(_spoofed_graft("fake_%064d" % i, "tcp:1.2.3.4:5555"))
        assert _score_count(n) <= 1, _score_count(n)
        assert _topic_peer_count(n) <= 1, _topic_peer_count(n)

    run(scenario())


@pytest.mark.interop
def test_143_one_carrier_cannot_eclipse_mesh_and_honest_graft_admitted():
    """IMPACT #2 prevented: 12 fabricated ids from ONE carrier occupy <=1 mesh
    slot (not 12), and a DISTINCT honest carrier's GRAFT is still admitted — no
    eclipse of the fast path."""
    async def scenario():
        n = _victim_node()
        for i in range(12):
            await n._dispatch(_spoofed_graft("sybil_%064d" % i, "tcp:9.9.9.9:6666"))
        mesh = set(n._gossip.mesh_peers(WEB_TOPIC))
        assert len(mesh) <= 1, mesh
        resp = await n._dispatch(_spoofed_graft("honest_x", "tcp:8.8.8.8:7777"))
        assert resp.get("kind") == "mesh-ack"
        assert "tcp:8.8.8.8:7777" in set(n._gossip.mesh_peers(WEB_TOPIC))

    run(scenario())


@pytest.mark.interop
def test_143_proven_node_graft_meshes_and_forged_id_mints_nothing():
    """LEGIT mesh preserved + forgery blocked: a genuinely-proven ``node:<pubkey>``
    GRAFT (asserted == proven) meshes normally; a valid proof presenting a
    DIFFERENT asserted ``_MESH_PEER_KEY`` mints no candidate (the frame is
    ignored), so a forged id can never ride a real proof into the mesh."""
    async def scenario():
        n = _victim_node()
        graft, proven = _proven_graft(n, "33" * 32, "tcp:5.5.5.5:1111")
        resp = await n._dispatch(graft)
        assert resp.get("kind") == "mesh-ack"
        assert proven in set(n._gossip.mesh_peers(WEB_TOPIC))
        assert proven in n._gossip._topic_peers.get(WEB_TOPIC, set())
        n2 = _victim_node()
        before = _score_count(n2)
        forged, _ = _proven_graft(
            n2, "44" * 32, "tcp:6.6.6.6:2222", asserted_override="forged_" + "0" * 58
        )
        await n2._dispatch(forged)
        assert _score_count(n2) == before
        assert _topic_peer_count(n2) == 0

    run(scenario())
