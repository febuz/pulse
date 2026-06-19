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
from knitweb.p2p.inventory import GETDATA, RECON_REQ, RECON_RANGE, RECON_RESULT
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
