"""Erlay activation (#60): O(diff) inventory reconciliation wired into FabricNode.

The reconcile bisection (``p2p/reconcile.py``) and its carrier-oriented
:class:`~knitweb.p2p.reconcile.ReconcileSession` are socket-free and proven in
``tests/property/test_reconcile.py`` + ``test_reconcile_session.py``. This suite
proves the activation is actually WIRED into the live
:class:`~knitweb.fabric.node.FabricNode`: two peers holding large
overlapping-but-different CID sets reconcile their inventories over the
``inv-recon-req`` / ``inv-recon-range`` / ``inv-recon-result`` envelopes and fetch
ONLY the differing CIDs through the existing ``inv-getdata`` path — O(diff), not
O(total).

The load-bearing assertions:

  * **O(diff), not O(total)** — after reconciling, the number of CIDs pulled via
    ``inv-getdata`` equals the size of the *symmetric difference*, NOT the size of
    the (much larger) shared inventory; both peers converge to the union;
  * **identical sets ⇒ zero getdata** — reconciling two equal inventories pulls
    nothing and the bisection prunes at the root;
  * **determinism** — the same inputs produce the same reconcile message sequence
    (identical ``inv-recon-*`` envelope bytes per round);
  * **byte-identity** — a CID fetched after reconcile carries the verbatim stored
    frame, so the record's CID at the puller equals the source's, byte-for-byte;
  * **reconnect trigger** — a node that drifted apart reconciles + re-converges
    when its reconcile tick runs, fetching only the diff;
  * **anti-entropy backstop intact** — the unconditional full-sync loop is
    untouched and still converges what reconcile is not asked to cover.

Every dial runs over an in-memory carrier framing the SAME canonical-CBOR bytes
the real transports use (no socket, no handshake), so byte-identity is preserved
on the carrier and the test is deterministic and fast.
"""

import asyncio

import pytest

from knitweb.core import canonical
from knitweb.fabric.items import web_state_root
from knitweb.fabric.node import FabricNode
from knitweb.p2p import wire
from knitweb.p2p.inventory import (
    GETDATA,
    MAX_GETDATA_BATCH,
    RECON_REQ,
    RECON_RANGE,
    RECON_RESULT,
    ServeBudget,
)
from knitweb.p2p.relay import ENVELOPE_PEER_KEY
from knitweb.p2p.transport import PeerAddress


# ── instrumented in-memory carrier ────────────────────────────────────────────

class _MemTransport:
    """Socket-free Transport routing a dial straight to a peer's ``_dispatch``.

    Records, at the *sender*, every outbound request's kind and — for
    ``inv-getdata`` — the exact CID count, so a test can prove the number of CIDs
    pulled after reconcile equals the symmetric difference (and NOT the inventory).
    """

    tag = "mem"

    def __init__(self, registry: dict, node_id: int) -> None:
        self._registry = registry
        self._node_id = node_id
        self.sent_kinds: dict[str, int] = {}
        self.getdata_cid_counts: list[int] = []
        self.recon_request_frames: list = []

    def bind(self, node) -> None:
        self._node = node
        self._registry[self._node_id] = self

    async def dial(self, peer: PeerAddress, request: dict) -> dict:
        target = self._registry[int(peer.params["id"])]
        kind = str(request.get("kind"))
        self.sent_kinds[kind] = self.sent_kinds.get(kind, 0) + 1
        if kind == GETDATA:
            cids = request.get("cids")
            self.getdata_cid_counts.append(len(cids) if isinstance(cids, list) else 0)
        if kind in (RECON_REQ, RECON_RANGE):
            # Capture the reconcile PAYLOAD (the kind + the ordered batch of inner
            # reconcile frame bytes) of each request so a test can assert the
            # message SEQUENCE is deterministic across replays. We deliberately
            # exclude the carrier id-proof envelope (``_relay_*``), which is meant
            # to be non-deterministic (fresh nonce + timestamp) and rides OUTSIDE
            # the canonical reconcile bytes — it never enters the bisection logic.
            self.recon_request_frames.append((kind, tuple(request.get("frames", ()))))
        # Frame -> bytes -> frame: the carrier moves opaque canonical bytes only,
        # so a signed record's byte-identity is preserved across the hop.
        raw = wire.write_frame_bytes(request)
        decoded = wire.read_frame_bytes(raw)
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
    kw.setdefault("diffuse_max_ms", 0)
    node = FabricNode(transport=tr, **kw)
    tr.bind(node)
    return node


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=20))


def _knowledge(i: int, author: str) -> dict:
    return {"kind": "knowledge", "title": f"k{i}", "body": str(i), "author": author}


async def _seed(node: FabricNode, indices) -> set:
    """Weave a batch of distinct records into ``node`` (no peers); return the CIDs.

    Weaving with no peers populates the node's frame store / Web without any
    propagation, so the two nodes start with controlled, overlapping-but-different
    inventories — exactly the post-partition state reconcile must heal.
    """
    cids = set()
    for i in indices:
        cids.add(await node.weave(_knowledge(i, node.pub)))
    return cids


# ── 1. O(diff): large overlap, small diff -> getdata == |symmetric difference| ─

@pytest.mark.interop
def test_reconcile_fetches_only_the_diff_not_the_whole_inventory():
    async def scenario():
        reg: dict = {}
        a = _mem_node(reg, 1)  # initiator: reconciles + pulls its missing CIDs
        b = _mem_node(reg, 2)  # responder

        # 2000 SHARED records, plus a small distinct tail on each side. We weave
        # the shared set on a, then mirror those exact records into b, so both hold
        # an identical 2000-CID overlap; then each gets a few records the other
        # lacks. Seeding happens with NO peer wired so there is zero propagation —
        # the two nodes start in a controlled post-partition state. (add_peer only
        # AFTER seeding, so the reconcile is the first thing that crosses.)
        shared_records = [_knowledge(i, a.pub) for i in range(2000)]
        for rec in shared_records:
            await a.weave(rec)
        # Mirror the shared set into b (no peers -> no propagation), byte-identical.
        for rec in shared_records:
            await b.weave(rec)

        a_only = await _seed(a, range(900000, 900005))  # 5 only-a
        b_only = await _seed(b, range(800000, 800007))  # 7 only-b

        assert len(a._frames) == 2005
        assert len(b._frames) == 2007
        sym_diff_for_a = len(b_only)  # a lacks exactly b_only (7)

        # NOW wire the peer (post-seed): reconcile is the first dial that crosses.
        a.add_peer("b", b.address)

        # The inventory is huge (2000+) but the diff a must pull is tiny (7). A
        # full inv-flood would announce ~2007 CIDs; reconcile must pull only 7.
        fetched = await a.reconcile_with(b.address)

        # a converged to the UNION: it now holds every CID b had that it lacked.
        assert fetched == sym_diff_for_a == 7
        for cid in b_only:
            assert a.web.get(cid) is not None

        # LOAD-BEARING O(diff) PROOF: the number of CIDs pulled via inv-getdata
        # equals the symmetric difference (7), NOT the 2007-CID inventory.
        total_getdata_cids = sum(a.transport.getdata_cid_counts)
        assert total_getdata_cids == 7
        # And it is dramatically smaller than the inventory it reconciled.
        assert total_getdata_cids < len(b._frames) // 100
        # The reconcile itself rode the compact bisection envelopes, not an inv
        # flood: at least one reconcile request flew, and no inv-announce did.
        assert a.transport.sent_kinds.get(RECON_REQ, 0) == 1
        assert a.transport.sent_kinds.get("inv-announce", 0) == 0
        # The metric records exactly the symmetric difference it learned to pull.
        assert a.metrics.get("reconcile_missing") == 7
        assert a.metrics.get("reconcile_pulls") == 7

    run(scenario())


# ── 2. identical inventories -> ZERO getdata, root prune ──────────────────────

@pytest.mark.interop
def test_identical_inventories_pull_nothing():
    async def scenario():
        reg: dict = {}
        a = _mem_node(reg, 1)
        b = _mem_node(reg, 2)
        a.add_peer("b", b.address)

        records = [_knowledge(i, a.pub) for i in range(500)]
        for rec in records:
            await a.weave(rec)
            await b.weave(rec)
        assert len(a._frames) == len(b._frames) == 500

        fetched = await a.reconcile_with(b.address)

        # Nothing to fetch: the bisection pruned at the root.
        assert fetched == 0
        assert a.transport.getdata_cid_counts == []  # no getdata at all
        assert a.transport.sent_kinds.get(GETDATA, 0) == 0
        # Exactly ONE reconcile round trip (the full-keyspace probe pruned).
        assert a.transport.sent_kinds.get(RECON_REQ, 0) == 1
        assert a.transport.sent_kinds.get(RECON_RANGE, 0) == 0
        assert a.metrics.get("reconcile_missing") == 0

    run(scenario())


# ── 3. determinism: same inputs -> same reconcile message sequence ────────────

# Fixed 32-byte secp256k1 scalars (hex) so two runs mint the IDENTICAL node
# identities — hence identical record authors, hence identical CID sets, hence a
# byte-identical reconcile message sequence. (A fresh random keypair per run would
# change every record's author and therefore every CID, masking the determinism of
# the bisection itself, which is what this test pins.)
_PRIV_A = "11" * 32
_PRIV_B = "22" * 32


@pytest.mark.interop
def test_reconcile_message_sequence_is_deterministic():
    async def scenario():
        async def run_once():
            reg: dict = {}
            a = _mem_node(reg, 1, priv=_PRIV_A)
            b = _mem_node(reg, 2, priv=_PRIV_B)
            # Seed with NO peer wired (zero propagation), then reconcile directly.
            shared = [_knowledge(i, a.pub) for i in range(1000)]
            for rec in shared:
                await a.weave(rec)
                await b.weave(rec)
            await _seed(a, range(700000, 700010))
            await _seed(b, range(600000, 600010))
            await a.reconcile_with(b.address)
            return a.transport.recon_request_frames

        seq1 = await run_once()
        seq2 = await run_once()
        # Same inputs -> byte-identical reconcile request envelope sequence.
        assert seq1 == seq2
        assert len(seq1) >= 2  # opened + at least one range round

    run(scenario())


# ── 4. byte-identity: a reconciled-then-fetched frame is verbatim ─────────────

@pytest.mark.interop
def test_reconciled_then_fetched_cid_is_byte_identical():
    async def scenario():
        reg: dict = {}
        a = _mem_node(reg, 1)
        b = _mem_node(reg, 2)
        a.add_peer("b", b.address)

        # Common ground so reconcile has overlap to prune, plus one record only b
        # holds — its CID is what a will learn via reconcile and pull via getdata.
        for i in range(50):
            rec = _knowledge(i, a.pub)
            await a.weave(rec)
            await b.weave(rec)
        only_b = _knowledge(999999, b.pub)
        cid_source = await b.weave(only_b)
        cid_author = canonical.cid(only_b)
        assert cid_source == cid_author

        await a.reconcile_with(b.address)

        # a fetched exactly that CID via the inv-getdata pull, and its body is
        # byte-for-byte the source's: same CID, same inner record dict.
        assert a.web.get(cid_author) is not None
        assert canonical.cid(a.web.get(cid_author)) == cid_author
        assert a.web.get(cid_author) == only_b
        # a's own stored verbatim frame re-derives the identical CID.
        assert wire.read_frame_bytes(a._frames[cid_author])["record"] == only_b
        assert canonical.cid(wire.read_frame_bytes(a._frames[cid_author])["record"]) == cid_author

    run(scenario())


# ── 5. reconnect trigger: drifted node reconciles + converges via the tick ────

@pytest.mark.interop
def test_reconnect_tick_reconciles_and_reconverges_on_the_diff_only():
    async def scenario():
        reg: dict = {}
        a = _mem_node(reg, 1)  # source of truth
        b = _mem_node(reg, 2)  # drifted peer that reconciles back

        # Both start with a shared base; then a weaves more WHILE b is "away".
        for i in range(300):
            rec = _knowledge(i, a.pub)
            await a.weave(rec)
            await b.weave(rec)
        assert web_state_root(a.web) == web_state_root(b.web)  # converged base

        # a moves ahead by 4 records b never saw (the post-partition drift).
        a_ahead = await _seed(a, range(500000, 500004))
        assert web_state_root(a.web) != web_state_root(b.web)  # drifted

        # b points its reconcile at a and ticks once (the reconnect trigger). It
        # is scheduled exactly like maintain_mesh / gossip_tick — a callable tick
        # the loop drives; here we tick it by hand for determinism.
        b.add_peer("a", a.address)
        fetched = await b.reconcile_tick([a.address])

        # b pulled ONLY the 4-record drift and re-converged on a's current root.
        assert fetched == 4
        for cid in a_ahead:
            assert b.web.get(cid) is not None
        assert web_state_root(a.web) == web_state_root(b.web)
        # O(diff): exactly 4 CIDs crossed via getdata, not the 304-CID inventory.
        assert sum(b.transport.getdata_cid_counts) == 4

    run(scenario())


# ── 6. background loop wiring: opt-in, schedulable, cancellable ───────────────

@pytest.mark.interop
def test_reconcile_loop_is_opt_in_and_runs_like_the_other_loops():
    async def scenario():
        reg: dict = {}
        a = _mem_node(reg, 1)
        b = _mem_node(reg, 2)
        b.add_peer("a", a.address)

        # A plain node has no reconcile loop until start_reconcile is called.
        assert b._reconcile_task is None

        for i in range(120):
            rec = _knowledge(i, a.pub)
            await a.weave(rec)
            await b.weave(rec)
        a_ahead = await _seed(a, range(400000, 400005))

        # Drive the loop with a virtual clock that elapses no real time but yields
        # to the event loop, exactly as the anti-entropy / gossip loops are tested.
        delays: list[int] = []

        async def vclock(delay: int) -> None:
            assert isinstance(delay, int) and not isinstance(delay, bool)
            delays.append(delay)
            await asyncio.sleep(0)

        b.start_reconcile([a.address], interval=1, sleep=vclock)
        task = b._reconcile_task
        assert task is not None and not task.done()

        # Yield until b has converged onto a's drifted root via the loop.
        for _ in range(200):
            if web_state_root(a.web) == web_state_root(b.web):
                break
            await asyncio.sleep(0)
        assert web_state_root(a.web) == web_state_root(b.web)
        for cid in a_ahead:
            assert b.web.get(cid) is not None
        assert delays  # the loop actually ticked on its injected cadence

        # stop() tears the loop down cleanly (the FabricNode override).
        await b.stop()
        assert b._reconcile_task is None

    run(scenario())


# ── 7. anti-entropy backstop remains intact alongside reconcile ───────────────

@pytest.mark.interop
def test_anti_entropy_still_converges_what_reconcile_is_not_run_for():
    async def scenario():
        reg: dict = {}
        a = _mem_node(reg, 1)
        b = _mem_node(reg, 2)

        # a holds records b has never seen; b NEVER reconciles — it relies purely
        # on the unchanged anti-entropy sync_from backstop to converge. This proves
        # the Erlay activation did not disturb the backstop path.
        for i in range(40):
            await a.weave(_knowledge(i, a.pub))
        assert web_state_root(a.web) != web_state_root(b.web)

        pulled = await b.sync_from(a.address)  # the backstop path, unchanged
        assert pulled == 40
        assert web_state_root(a.web) == web_state_root(b.web)
        # b pulled via the full-sync backstop, issuing no reconcile or getdata.
        assert b.transport.sent_kinds.get(RECON_REQ, 0) == 0
        assert b.transport.sent_kinds.get(GETDATA, 0) == 0

    run(scenario())


# ── 8. anti-amplification budget on the live serve path (#91) ─────────────────
#
# The reconcile PULL leg (``_serve_getdata``) and the mesh-IWANT serve return the
# verbatim stored body for every CID a peer names, with (pre-#91) no per-peer cap
# on count or bytes. A single ~2 MiB getdata naming the whole inventory — or a
# peer hammering it — could reflect hundreds of GiB. These prove the per-request
# batch cap and the per-peer byte budget are WIRED into the live FabricNode serve
# path, while an honest reconcile of a realistic diff still converges.


class _IdMemTransport(_MemTransport):
    """A mem carrier that ALSO stamps the sender's stable identity per dial.

    The byte budget keys per peer; the live carrier stamps the sender id as
    ``ENVELOPE_PEER_KEY`` (a TCP carrier does this from the remote IP). We stamp a
    fixed id so the responder's #91 byte bucket is keyed and debited exactly as in
    production — without it the dispatch would see an unidentified sender and only
    the count cap would apply.
    """

    def __init__(self, registry: dict, node_id: int, sender_id: str) -> None:
        super().__init__(registry, node_id)
        self._sender_id = sender_id

    async def dial(self, peer: PeerAddress, request: dict) -> dict:
        target = self._registry[int(peer.params["id"])]
        kind = str(request.get("kind"))
        self.sent_kinds[kind] = self.sent_kinds.get(kind, 0) + 1
        if kind == GETDATA:
            cids = request.get("cids")
            self.getdata_cid_counts.append(len(cids) if isinstance(cids, list) else 0)
        raw = wire.write_frame_bytes(request)
        decoded = wire.read_frame_bytes(raw)
        # Stamp the carrier-identified sender so the responder keys its budget.
        decoded[ENVELOPE_PEER_KEY] = self._sender_id
        resp = await asyncio.wait_for(target._node._dispatch(decoded), timeout=5)
        return wire.read_frame_bytes(wire.write_frame_bytes(resp))


class _Clock:
    def __init__(self, t: int = 0) -> None:
        self._t = t

    def __call__(self) -> int:
        return self._t

    def advance(self, secs: int) -> None:
        self._t += secs


@pytest.mark.interop
def test_live_getdata_serve_is_capped_at_the_batch_not_the_whole_store():
    """A getdata naming FAR more CIDs than the batch cap serves AT MOST the cap.

    LOAD-BEARING at the NODE level: the responder holds 2x the batch cap; an
    attacker dials one getdata naming every held CID. The live ``_serve_getdata``
    returns at most ``MAX_GETDATA_BATCH`` records — the whole store does NOT
    reflect back, so the ~135,000x amplification is dead.
    """
    async def scenario():
        reg: dict = {}
        attacker = _IdMemTransport(reg, 1, sender_id="tcp:attacker")
        victim_tr = _IdMemTransport(reg, 2, sender_id="tcp:victim")
        attacker_node = FabricNode(transport=attacker)
        attacker.bind(attacker_node)
        victim = FabricNode(transport=victim_tr)
        victim_tr.bind(victim)

        # Victim holds 2x the batch cap of records.
        n = MAX_GETDATA_BATCH * 2
        all_cids = []
        for i in range(n):
            all_cids.append(await victim.weave(_knowledge(i, victim.pub)))

        # Attacker dials ONE getdata naming every held CID (the amplification req).
        resp = await attacker.dial(victim.address, {"kind": GETDATA, "cids": all_cids})
        records = resp.get("records", [])
        assert resp.get("kind") == "inv-data"
        # AT MOST the batch cap is served — not the whole 2x-cap store.
        assert len(records) == MAX_GETDATA_BATCH
        assert len(records) < n

    run(scenario())


@pytest.mark.interop
def test_live_byte_budget_throttles_a_hammering_peer():
    """A peer hammering getdata is throttled by the live per-peer byte budget.

    With a tiny injected byte budget, the first request serves up to the byte
    ceiling and a second request in the SAME window serves nothing; crossing into
    the next window refills. Proves the bucket is wired into the live serve path.
    """
    async def scenario():
        reg: dict = {}
        attacker = _IdMemTransport(reg, 1, sender_id="tcp:hammer")
        clock = _Clock(0)
        # Hold a few records; size the budget to ~2 bodies/window.
        victim_tr = _IdMemTransport(reg, 2, sender_id="tcp:victim")
        victim = FabricNode(
            transport=victim_tr,
            serve_budget=ServeBudget(
                bytes_per_window=1, window_seconds=5, clock=clock
            ),
        )
        victim_tr.bind(victim)
        attacker_node = FabricNode(transport=attacker)
        attacker.bind(attacker_node)

        cids = [await victim.weave(_knowledge(i, victim.pub)) for i in range(6)]
        # Size the budget to EXACTLY the first two served bodies (serve order is
        # request order) plus a sub-body sliver of headroom, so a window admits 2
        # whole bodies and no more — proving the bucket stops on a whole-body
        # boundary, never truncating a frame even with room left over.
        #
        # The signed frames are NOT all the same length: the signature/CID
        # minimal-int encoding makes each record's frame vary by a couple of bytes.
        # So the sliver must stay below BOTH bodies the budget could next meet —
        # body #2 (the 1st request stops there) and body #0 (the same-window
        # re-request retries there). A fixed `third_body - 1` sliver can exceed
        # body #0 when it is the smaller frame, letting the 2nd request serve a body
        # (~1/3 flake); bounding by the min keeps it a strict fraction of the next
        # body regardless of per-record size jitter.
        two_bodies = len(victim._frames[cids[0]]) + len(victim._frames[cids[1]])
        sliver = min(len(victim._frames[cids[0]]), len(victim._frames[cids[2]])) - 1
        victim._serve_budget = ServeBudget(
            bytes_per_window=two_bodies + sliver,
            window_seconds=5,
            clock=clock,
        )
        victim._inv.budget = victim._serve_budget

        req = {"kind": GETDATA, "cids": cids}
        first = await attacker.dial(victim.address, req)
        assert len(first.get("records", [])) == 2  # capped to the byte budget

        second = await attacker.dial(victim.address, req)
        # Same window, budget exhausted -> nothing more served (drop/defer).
        assert second.get("kind") == "inv-ack"
        assert second.get("records", []) == []

        clock.advance(5)  # next window refills the bucket
        third = await attacker.dial(victim.address, req)
        assert len(third.get("records", [])) == 2

    run(scenario())


@pytest.mark.interop
def test_honest_reconcile_still_converges_under_the_serve_budget():
    """An honest Erlay reconcile of a moderate diff STILL pulls all its CIDs.

    The #91 cap must NOT starve honest reconcile: with the (generous) prod budget
    active and the live carrier stamping identities, a node reconciling a moderate
    symmetric difference still fetches every missing CID and converges. This is the
    Erlay-preserved proof under the new budget.
    """
    async def scenario():
        reg: dict = {}
        a_tr = _IdMemTransport(reg, 1, sender_id="tcp:a")
        b_tr = _IdMemTransport(reg, 2, sender_id="tcp:b")
        # Prod-default generous byte budget on the responder.
        a = FabricNode(transport=a_tr, serve_budget=ServeBudget())
        a_tr.bind(a)
        b = FabricNode(transport=b_tr, serve_budget=ServeBudget())
        b_tr.bind(b)

        # 300 shared, a moderate diff b holds that a lacks (well under the cap).
        shared = [_knowledge(i, a.pub) for i in range(300)]
        for rec in shared:
            await a.weave(rec)
            await b.weave(rec)
        diff_n = 250
        b_only = set()
        for i in range(700000, 700000 + diff_n):
            b_only.add(await b.weave(_knowledge(i, b.pub)))

        a.add_peer("b", b.address)
        fetched = await a.reconcile_with(b.address)

        # Honest reconcile converged under the budget: every missing CID pulled.
        assert fetched == diff_n
        for cid in b_only:
            assert a.web.get(cid) is not None
        # And it pulled exactly the diff via getdata (O(diff), Erlay intact).
        assert sum(a.transport.getdata_cid_counts) == diff_n

    run(scenario())


@pytest.mark.interop
def test_honest_over_budget_fetch_fully_converges_across_windows():
    """An honest peer whose diff exceeds ONE window's byte budget STILL converges,
    across successive windows — the positive liveness counterpart to the throttle
    test (which proves a hammering peer is cut off in-window).

    The #91 byte budget must throttle abuse WITHOUT permanently starving honest
    sync. Each window serves only a budget-bounded subset of WHOLE bodies (#189
    all-or-nothing), so a large honest diff cannot land in a single window. It still
    converges because honest reconcile re-requests only the still-MISSING CIDs each
    round (the shrinking symmetric difference) — never the full list. on_getdata is
    stateless and serves the requested list in REQUEST ORDER up to budget EVERY
    window, so re-asking for the full list would re-serve the same prefix forever;
    progress relies on asking for the missing tail, which reconcile_with does by
    recomputing the diff each call. Guards that whole path end-to-end: a broken
    window refill, a budget that never recovers, or a reconcile that stopped
    recomputing the diff would leave ``a`` permanently short of ``b`` here.
    """
    async def scenario():
        reg: dict = {}
        clock = _Clock(0)
        a_tr = _IdMemTransport(reg, 1, sender_id="tcp:a")
        b_tr = _IdMemTransport(reg, 2, sender_id="tcp:b")
        a = FabricNode(transport=a_tr)
        a_tr.bind(a)
        b = FabricNode(
            transport=b_tr,
            serve_budget=ServeBudget(bytes_per_window=1, window_seconds=5, clock=clock),
        )
        b_tr.bind(b)

        # An honest diff b holds that a lacks — well under the batch cap, so the
        # per-window BYTE budget (not the count cap) is the only limiter under test.
        diff_n = 24
        b_only = [
            await b.weave(_knowledge(i, b.pub))
            for i in range(800000, 800000 + diff_n)
        ]
        a.add_peer("b", b.address)

        # Size b's window to ~6 whole bodies: small enough that a 24-record diff
        # needs several windows, large enough to always admit >=1 whole body (so it
        # can never stall). Signed frames jitter a couple bytes per keypair, so size
        # by a real body with a wide margin — no exact-boundary assertion is made.
        body = len(b._frames[b_only[0]])
        b._serve_budget = ServeBudget(
            bytes_per_window=body * 6, window_seconds=5, clock=clock
        )
        b._inv.budget = b._serve_budget

        # Round 1: the budget BITES — fewer than the whole diff is pulled at once.
        first = await a.reconcile_with(b.address)
        assert 0 < first < diff_n

        # Advance windows and re-reconcile the missing tail until converged.
        rounds = 1
        while [c for c in b_only if a.web.get(c) is None] and rounds < 20:
            clock.advance(5)
            await a.reconcile_with(b.address)
            rounds += 1

        # Converged: every diff CID is now held by a — honest sync was throttled
        # but never starved, and it genuinely spanned multiple windows.
        assert all(a.web.get(c) is not None for c in b_only)
        assert rounds > 1

    run(scenario())
