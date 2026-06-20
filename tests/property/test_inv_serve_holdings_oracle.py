"""#146 — the inv-announce reply must NOT be an unbudgeted holdings oracle.

An inbound ``inv-announce`` reply (``FabricNode._serve_inv``) is a deterministic
function of the node's holdings: the announced CIDs it LACKS come back as the
``inv-getdata`` want list, so the announcer learns the EXACT held/lacked
partition of every CID it named. The #91 getdata BYTE budget does not gate this
reply (no body travels), so without a probe budget a single peer can enumerate
the node's whole holdings set for free by announcing arbitrary candidate CIDs.

These tests pin the proportionate fix: a per-peer probe budget (the existing
:class:`~knitweb.p2p.inventory.ServeBudget` primitive, one token per probed CID)
that

  * CUTS OFF mass enumeration — once a prober exhausts its CIDs-per-window
    budget, the reply is withheld (a non-discriminating ``inv-ack``), so it can no
    longer read out the held/lacked partition of the over-budget CIDs; and
  * PRESERVES legitimate exchange — an honest normal-volume announce stays under
    the cap and still gets its exact want list, so inv -> getdata -> serve still
    completes.

Time is an injected monotonic integer-second virtual clock (no wall-clock, no
randomness), so the window boundary is fully replayable.
"""

import asyncio

import pytest

from knitweb.core import canonical
from knitweb.fabric.node import FabricNode
from knitweb.p2p.inventory import INV, GETDATA, ServeBudget
from knitweb.p2p.relay import ENVELOPE_PEER_KEY


class _VirtualClock:
    """A deterministic injectable monotonic integer-second clock (no wall-clock)."""

    def __init__(self, t: int = 0) -> None:
        self._t = t

    def __call__(self) -> int:
        return self._t

    def advance(self, secs: int) -> None:
        self._t += secs


def _unknown_cid(tag: str) -> str:
    """A canonical CID of a record the node never wove / does not hold."""
    return canonical.cid({"kind": "knowledge", "title": tag, "body": "y", "author": "z"})


async def _weave_held(node: FabricNode, n: int) -> list[str]:
    held = []
    for i in range(n):
        held.append(
            await node.weave(
                {"kind": "knowledge", "title": f"held-{i}", "body": "x", "author": node.pub}
            )
        )
    return held


async def _probe(node: FabricNode, cids: list[str], peer: str):
    """Drive one inbound inv-announce from ``peer``; return (kind, lacked-set)."""
    resp = await node._dispatch({"kind": INV, "cids": list(cids), ENVELOPE_PEER_KEY: peer})
    kind = resp.get("kind")
    if kind == GETDATA:
        return kind, set(resp["cids"])
    if kind == "inv-ack":
        return kind, set()
    raise AssertionError(f"unexpected reply kind {kind!r}")


def test_enumeration_is_bounded_after_budget_exhausts():
    """A prober flooding candidate CIDs is cut off once its window budget is spent.

    With a tiny probe budget, the FIRST batch (within budget) leaks the exact
    held/lacked partition, but every SUBSEQUENT over-budget batch in the same
    window comes back as a non-discriminating inv-ack — so the prober can NO
    LONGER read out the holdings of the CIDs it floods. This is the enumeration
    bound: the held/lacked partition is withheld beyond the budget.
    """

    async def run():
        clock = _VirtualClock(0)
        # Budget: 12 probed CIDs / window. First batch of 12 fits; the rest do not.
        budget = ServeBudget(bytes_per_window=12, window_seconds=10, clock=clock)
        node = FabricNode(inv_probe_budget=budget)
        held = await _weave_held(node, 4)

        leaked_rounds = 0
        withheld_rounds = 0
        for r in range(20):
            unknown = [_unknown_cid(f"r{r}-u{j}") for j in range(8)]
            batch = held + unknown  # 12 CIDs per batch
            kind, lacked = await _probe(node, batch, peer="prober")
            inferred_held = {c for c in batch if c not in lacked}
            inferred_lacked = {c for c in batch if c in lacked}
            partition_exact = (
                inferred_held == set(held) and inferred_lacked == set(unknown)
            )
            if kind == GETDATA and partition_exact:
                leaked_rounds += 1
            else:
                # inv-ack within the SAME window: the partition is withheld.
                assert kind == "inv-ack"
                withheld_rounds += 1

        # Exactly ONE batch (the first 12 CIDs) leaked before the budget ran out;
        # the prober is then capped for the rest of the window.
        assert leaked_rounds == 1, leaked_rounds
        assert withheld_rounds == 19, withheld_rounds

    asyncio.run(run())


def test_budget_refills_next_window_but_stays_per_peer_capped():
    """The cap is per-peer-per-window: it refills next window but never unbounded.

    Across many windows the prober gets at most one leaking batch PER window, so
    the per-window enumeration rate is hard-capped — it cannot read out an
    arbitrarily large holdings set within a single window.
    """

    async def run():
        clock = _VirtualClock(0)
        budget = ServeBudget(bytes_per_window=12, window_seconds=10, clock=clock)
        node = FabricNode(inv_probe_budget=budget)
        held = await _weave_held(node, 4)

        def fresh_batch(tag):
            return held + [_unknown_cid(f"{tag}-u{j}") for j in range(8)]

        # Window 0: first batch leaks, second withheld.
        k1, l1 = await _probe(node, fresh_batch("w0a"), "prober")
        k2, _ = await _probe(node, fresh_batch("w0b"), "prober")
        assert k1 == GETDATA and l1 == set(fresh_batch("w0a")[4:])
        assert k2 == "inv-ack"
        # Cross into window 1: budget refills, one batch leaks again.
        clock.advance(10)
        k3, l3 = await _probe(node, fresh_batch("w1a"), "prober")
        k4, _ = await _probe(node, fresh_batch("w1b"), "prober")
        assert k3 == GETDATA and l3 == set(fresh_batch("w1a")[4:])
        assert k4 == "inv-ack"

    asyncio.run(run())


def test_legit_normal_volume_inv_exchange_still_completes():
    """An honest normal-volume announce is UNDER the prod cap and answered in full.

    The default prod probe budget admits a full honest batch, so a legitimate
    peer announcing a normal set of fresh CIDs still gets the exact want list back
    (inv -> getdata path preserved). The fix bounds enumeration ABUSE, not flow.
    """

    async def run():
        node = FabricNode()  # prod-default INV_PROBE_CIDS_PER_WINDOW budget
        held = await _weave_held(node, 4)
        unknown = [_unknown_cid(f"legit-u{j}") for j in range(6)]
        kind, lacked = await _probe(node, held + unknown, peer="honest")
        # The honest peer gets back EXACTLY the CIDs it lacks (so it can getdata
        # them) — the want list is served, not withheld.
        assert kind == GETDATA
        assert lacked == set(unknown)

    asyncio.run(run())


def test_distinct_peers_each_get_their_own_budget():
    """The budget is keyed per peer: one prober's exhaustion never blocks another.

    A flooding prober burning its own window budget must not deny an honest peer's
    legitimate announce — the cap is per-peer, so honest service is preserved even
    under a concurrent flood.
    """

    async def run():
        clock = _VirtualClock(0)
        budget = ServeBudget(bytes_per_window=12, window_seconds=10, clock=clock)
        node = FabricNode(inv_probe_budget=budget)
        held = await _weave_held(node, 4)

        def fresh_batch(tag):
            return held + [_unknown_cid(f"{tag}-u{j}") for j in range(8)]

        # Prober burns its window budget (1 leak then withheld).
        await _probe(node, fresh_batch("pa"), "prober")
        k_block, _ = await _probe(node, fresh_batch("pb"), "prober")
        assert k_block == "inv-ack"
        # Honest peer, SAME window, still served its want list in full.
        k_ok, lacked = await _probe(node, fresh_batch("ha"), "honest")
        assert k_ok == GETDATA
        assert lacked == set(fresh_batch("ha")[4:])

    asyncio.run(run())
