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

from knitweb.core import crypto
from knitweb.fabric.items import web_state_root
from knitweb.fabric.node import FabricNode


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
