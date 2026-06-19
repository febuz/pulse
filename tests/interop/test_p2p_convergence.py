"""Multi-node convergence: 3+ real FabricNodes converge on one state_root.

The existing fabric/anti-entropy suites prove the *two-node* case: a→b broadcast,
a single late joiner's ``sync_from``, and one healer re-syncing from one weaver.
This suite raises the node count: it stands up **three or more real in-process
nodes** over the real :class:`~knitweb.p2p.transport.TcpTransport` carrier and
proves they all settle on a single ``web_state_root`` through topologies a
two-node test cannot express:

  * a **fan-out** weaver gossiping to two peers at once (one broadcast, two
    convergences),
  * a **chain catch-up** where a late joiner ``sync_from``s an intermediary that
    itself only learned the records by syncing — provenance survives a relay hop,
  * **order/source independence**: two weavers each author half the records and
    every node, pulling from different sources, lands on the *same* root,
  * **self-healing at fan-out**: a star of healers all run the real
    ``start_anti_entropy`` loop against one weaver; one drops mid-churn and
    rejoins, and the whole star re-converges on the weaver's post-churn root.

Everything runs against the genuine node/transport/sync/anti-entropy code — no
mocks of the logic under test. The anti-entropy clock is a virtual clock that
elapses an integer count and yields to the loop, so churn is deterministic and
fast: no real ``sleep``, no flakiness. All assertions are on integer Web sizes
and the hex ``state_root`` witness, so a woven Knit's CID is never touched.
"""

import asyncio

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.items import web_state_root
from knitweb.fabric.node import FabricNode, _RECORD_TAG


def run(coro):
    return asyncio.run(coro)


class VirtualClock:
    """Injected anti-entropy sleep: integer virtual time, zero real time.

    ``await sleep(delay)`` records the (integer) delay and yields once to the
    event loop so the background socket I/O an anti-entropy round kicked off gets
    to run — but no wall-clock time passes, keeping the churn scenario fast and
    deterministic.
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


def _converged(*nodes: FabricNode) -> bool:
    """True iff every node holds the identical (non-trivial) state_root."""
    roots = {n.state_root for n in nodes}
    return len(roots) == 1


@pytest.mark.interop
def test_fan_out_weave_converges_three_nodes():
    """One weaver broadcasting to two peers: a single weave, two convergences."""
    async def scenario():
        a = FabricNode()  # the weaver
        b = FabricNode()
        c = FabricNode()
        async with a, b, c:
            # a fans every weave out to BOTH peers at once.
            a.add_peer("b", b.address)
            a.add_peer("c", c.address)
            assert _converged(a, b, c)  # all empty → all equal

            await a.weave({"kind": "knowledge", "title": "alpha", "body": "x", "author": a.pub})
            await a.weave({"kind": "resource", "resource_kind": "gpu",
                           "capacity": 4, "price_per_epoch": 9, "provider": a.pub})

            # Both peers ingested both records over the broadcast path.
            assert a.web.size == b.web.size == c.web.size == (2, 0)
            assert a.state_root != crypto.sha256(b"").hex()  # non-empty witness
            assert _converged(a, b, c)
            # Identity is content-addressed, not source-addressed: a record b
            # ingested from a's broadcast has the same CID a holds.
            for cid in a.web.nodes:
                assert b.web.get(cid) is not None
                assert c.web.get(cid) is not None

    run(scenario())


@pytest.mark.interop
def test_chain_catch_up_relays_provenance_across_a_hop():
    """A late joiner syncs from an intermediary that itself only synced.

    a weaves; b is a peer and converges by broadcast; c joins last and pulls from
    b (NOT a). c must still converge: the intermediary re-signs the snapshot under
    its own key, yet each record keeps a's content-addressed CID, so the three
    state_roots match across the relay hop.
    """
    async def scenario():
        a = FabricNode()
        b = FabricNode()
        c = FabricNode()
        async with a, b, c:
            a.add_peer("b", b.address)  # a → b by broadcast only
            cid1 = await a.weave({"kind": "knowledge", "title": "early", "body": "1", "author": a.pub})
            cid2 = await a.weave({"kind": "knowledge", "title": "later", "body": "2", "author": a.pub})

            assert a.state_root == b.state_root  # a and b already converged
            assert c.web.size == (0, 0)          # c knows nothing yet

            # c catches up from b — the relay hop, not the origin.
            added = await c.sync_from(b.address)
            assert added == 2
            assert c.web.get(cid1) is not None
            assert c.web.get(cid2) is not None
            assert _converged(a, b, c)

            # Idempotent across the hop: re-pulling from b weaves nothing new.
            assert await c.sync_from(b.address) == 0
            assert _converged(a, b, c)

    run(scenario())


@pytest.mark.interop
def test_two_weavers_distinct_sources_reach_one_root():
    """Order/source independence: two authors, every node lands on one root.

    a and b each author half the records into their own Web; then a third node c
    pulls from *both* and a/b cross-sync. Despite three different ingest orders
    and two distinct authoring keys, all three converge on the identical root —
    the Merkle root is over the sorted CID set, independent of arrival order.
    """
    async def scenario():
        a = FabricNode()
        b = FabricNode()
        c = FabricNode()
        async with a, b, c:
            await a.weave({"kind": "knowledge", "title": "a0", "body": "0", "author": a.pub})
            await a.weave({"kind": "knowledge", "title": "a1", "body": "1", "author": a.pub})
            await b.weave({"kind": "knowledge", "title": "b0", "body": "0", "author": b.pub})
            await b.weave({"kind": "knowledge", "title": "b1", "body": "1", "author": b.pub})

            # Diverged: each weaver holds only its own half.
            assert a.web.size == b.web.size == (2, 0)
            assert a.state_root != b.state_root

            # c pulls from both sources (a first, then b).
            assert await c.sync_from(a.address) == 2
            assert await c.sync_from(b.address) == 2
            # a and b cross-sync to pick up each other's half (opposite orders).
            assert await a.sync_from(b.address) == 2
            assert await b.sync_from(a.address) == 2

            # Three ingest orders, two authors → one identical root over 4 records.
            assert a.web.size == b.web.size == c.web.size == (4, 0)
            assert _converged(a, b, c)
            assert web_state_root(c.web) == a.state_root

    run(scenario())


@pytest.mark.interop
def test_anti_entropy_star_self_heals_after_one_peer_drops():
    """A star of healers re-converges on one weaver across a peer drop.

    Two healers (b, c) run the real ``start_anti_entropy`` loop against weaver a.
    Both converge; then a keeps weaving while c drops; c rejoins (fresh listener,
    same identity, empty Web) and its loop drives a full re-sync. The whole star
    settles on a's *post-churn* root — convergence holds across churn at fan-out,
    not just for a single healer.
    """
    async def scenario():
        clock = VirtualClock()
        a = FabricNode()  # the weaver / source of truth
        b = FabricNode()  # healer that stays up the whole time
        c = FabricNode()  # healer that drops mid-churn and rejoins

        async with a:
            await a.weave({"kind": "knowledge", "title": "t0", "body": "0", "author": a.pub})
            await a.weave({"kind": "knowledge", "title": "t1", "body": "1", "author": a.pub})

            await b.start()
            await c.start()
            assert b.state_root != a.state_root  # both healers diverged at boot
            assert c.state_root != a.state_root
            b.start_anti_entropy([a.address], interval=1, sleep=clock.sleep)
            c.start_anti_entropy([a.address], interval=1, sleep=clock.sleep)

            # Both healers re-sync and converge onto a's root (fan-out heal).
            assert await _settle(lambda: _converged(a, b, c))
            assert a.web.size == b.web.size == c.web.size == (2, 0)

            # --- c drops; a keeps weaving; b (still up) must track the growth ---
            await c.stop()  # cancels c's loop
            assert c._anti_entropy_task is None
            await a.weave({"kind": "knowledge", "title": "t2", "body": "2", "author": a.pub})
            await a.weave({"kind": "knowledge", "title": "t3", "body": "3", "author": a.pub})
            assert a.web.size == (4, 0)

            # b's live loop pulls the two new records without any restart.
            assert await _settle(lambda: b.state_root == a.state_root)
            assert b.web.size == (4, 0)

            # --- c rejoins: fresh listener, same identity, empty Web ---
            c2 = FabricNode(priv=c._priv)
            async with c2:
                assert c2.state_root != a.state_root  # behind: booted empty
                c2.start_anti_entropy([a.address], interval=1, sleep=clock.sleep)

                # The reborn healer drives a full re-sync to a's post-churn root;
                # the whole star is converged again.
                assert await _settle(lambda: _converged(a, b, c2))
                assert a.web.size == b.web.size == c2.web.size == (4, 0)
                assert not b._anti_entropy_task.done()  # loops still alive
                assert not c2._anti_entropy_task.done()

            await b.stop()

    run(scenario())


def _forged_envelope(author_pub: str, record: dict, sig: str) -> dict:
    """A `fabric-record` gossip envelope with whatever (author, record, sig)
    triple the caller chooses — the seam a malicious peer uses to lie."""
    return {"kind": "fabric-record", "author": author_pub, "record": record, "sig": sig}


@pytest.mark.interop
def test_byzantine_forged_record_is_excluded_and_honest_nodes_converge():
    """A malicious peer's FORGED / equivocating record is rejected by the gate,
    and the honest nodes still converge on the *honest* state_root.

    Topology: two honest nodes ``a`` (weaver) and ``b`` (peer) plus one malicious
    actor ``m`` running the genuine transport. ``m`` dials ``b``'s real gossip
    endpoint with three flavours of lie, none of which carries a valid author
    signature over the record bytes:

      1. **Bad author signature** — a record "authored" by ``m`` but with a
         signature that does not verify under ``m``'s key (random/empty sig).
      2. **Impersonation** — a record claiming ``a`` as author, signed by ``m``
         (so the claimed author never vouched for it).
      3. **Equivocation / tamper** — ``m`` validly signs record ``R0`` then ships
         the same signature over a *mutated* record ``R1`` (two conflicting
         payloads behind one signature); the bytes no longer match the sig.

    The honest node MUST reject every one (``error`` / ``bad-request`` response,
    nothing woven) and the honest pair MUST still converge on the root they reach
    over the honest broadcast path. This is the author-signature gate's
    load-bearing assertion: a mutation that disables :func:`crypto.verify` in
    ``FabricNode._ingest_signed`` would let one of these forgeries weave, flipping
    ``b``'s Web size / ``state_root`` (and the forged CID would appear), failing
    this test — which currently lets such a mutation pass.
    """
    async def scenario():
        a = FabricNode()   # honest weaver
        b = FabricNode()   # honest peer (the forgery target)
        m = FabricNode()   # malicious actor (real transport, forged payloads)
        async with a, b, m:
            a.add_peer("b", b.address)

            # Honest record propagates a -> b over the real broadcast path.
            good_cid = await a.weave(
                {"kind": "knowledge", "title": "honest", "body": "ok", "author": a.pub}
            )
            assert _converged(a, b)
            assert a.web.size == b.web.size == (1, 0)
            honest_root = b.state_root
            honest_size = b.web.size

            # The payload the attacker WANTS b to weave (it never should).
            forged_record = {
                "kind": "knowledge", "title": "forged", "body": "evil", "author": a.pub,
            }
            forged_cid = canonical.cid(forged_record)

            # --- Forgery 1: signature that does not verify under m's own key. ---
            env_badsig = _forged_envelope(m.pub, forged_record, sig="00" * 64)
            resp = await m.dialer.dial(b.address, env_badsig)
            assert resp.get("kind") == "error"
            assert resp.get("code") == "bad-request"

            # --- Forgery 2: impersonate a — record signed by m, author=a. ---
            sig_by_m = crypto.sign(m._priv, _RECORD_TAG + canonical.encode(forged_record))
            env_impersonate = _forged_envelope(a.pub, forged_record, sig=sig_by_m)
            resp = await m.dialer.dial(b.address, env_impersonate)
            assert resp.get("kind") == "error"
            assert resp.get("code") == "bad-request"

            # --- Forgery 3: equivocation/tamper — sign R0, ship over mutated R1. ---
            # m validly signs r0 then ships that same signature over a mutated r1
            # (two conflicting payloads behind one signature). The verify is over
            # the *received* r1 bytes, which the r0 signature does not cover, so the
            # gate rejects it exactly like an outright bad signature.
            r0 = {"kind": "knowledge", "title": "r0", "body": "a", "author": m.pub}
            r1 = {"kind": "knowledge", "title": "r0", "body": "MUTATED", "author": m.pub}
            sig_r0 = crypto.sign(m._priv, _RECORD_TAG + canonical.encode(r0))
            assert crypto.verify(m.pub, _RECORD_TAG + canonical.encode(r0), sig_r0)
            env_tamper = _forged_envelope(m.pub, r1, sig=sig_r0)  # sig is for r0, not r1
            resp = await m.dialer.dial(b.address, env_tamper)
            assert resp.get("kind") == "error"
            assert resp.get("code") == "bad-request"

            # The honest gate excluded EVERY forgery: b's Web is unchanged, no
            # forged/equivocating CID was woven, and the honest root still stands.
            assert b.web.size == honest_size == (1, 0)
            assert b.state_root == honest_root
            assert b.web.get(good_cid) is not None
            assert b.web.get(forged_cid) is None
            assert b.web.get(canonical.cid(r0)) is None
            assert b.web.get(canonical.cid(r1)) is None

            # Honest pair still converged on the honest root, forgeries excluded.
            assert _converged(a, b)
            assert web_state_root(b.web) == a.state_root

    run(scenario())
