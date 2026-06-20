"""#159 — the anti-entropy reconcile responder must NOT be an unbudgeted CPU sink.

``FabricNode._serve_recon`` drives a bisection session whose per-probe cost is
O(in-range inventory): every reconcile probe SHA-256-hashes each held CID in its
range. It was the one serve path with no budget — only ``wire.MAX_FRAME_BYTES``
bounded the envelope, so a single 8 MiB request could carry ~95k full-keyspace
probes and burn minutes of CPU (a HIGH CPU-amplification DoS), with no per-peer
debit (unlike getdata #91/#102 and inv-probe #146).

These tests pin the proportionate fix:

  * the inbound batch is parsed with the ``MAX_RECON_FRAMES`` cap enforced (so an
    8 MiB envelope can't smuggle ~95k probes); and
  * a per-peer reconcile-frame :class:`~knitweb.p2p.inventory.ServeBudget` debit
    runs BEFORE the costly session — a probe flood exhausts its window and is
    answered with an empty (converged) result, the expensive hashing never run,
    while an honest near-synced reconcile stays under the cap and is served.

Time is an injected monotonic integer-second virtual clock (no wall-clock, no
randomness), so the window boundary is fully replayable.
"""

import asyncio

import pytest

from knitweb.fabric.node import FabricNode
from knitweb.p2p import inventory
from knitweb.p2p.inventory import (
    RECON_REQ,
    RECON_RESULT,
    MAX_RECON_FRAMES,
    InventoryError,
    ServeBudget,
    parse_recon_batch,
)
from knitweb.p2p.reconcile import ReconcileSession
from knitweb.p2p.relay import ENVELOPE_PEER_KEY

_RECON_SESSION_KEY = "session"


class _VirtualClock:
    """A deterministic injectable monotonic integer-second clock (no wall-clock)."""

    def __init__(self, t: int = 0) -> None:
        self._t = t

    def __call__(self) -> int:
        return self._t

    def advance(self, secs: int) -> None:
        self._t += secs


# --- inbound cap (parse) ---------------------------------------------------- #


def test_parse_recon_batch_enforces_frame_cap():
    # At the cap is fine; one over the cap is rejected so an 8 MiB envelope can't
    # smuggle ~95k probes past the responder.
    assert parse_recon_batch([b"x"] * 4) == [b"x"] * 4
    with pytest.raises(InventoryError):
        parse_recon_batch([b""] * (MAX_RECON_FRAMES + 1))
    with pytest.raises(InventoryError):
        parse_recon_batch("not-a-list")
    with pytest.raises(InventoryError):
        parse_recon_batch([b"ok", "not-bytes"])


# --- responder budget ------------------------------------------------------- #


async def _open_batch(cids):
    """A valid opening reconcile probe batch from an initiator over ``cids``."""
    return ReconcileSession(list(cids)).open()


async def _recon(node, frames, peer, session_id):
    return await node._dispatch(
        {
            "kind": RECON_REQ,
            _RECON_SESSION_KEY: session_id,
            "frames": list(frames),
            ENVELOPE_PEER_KEY: peer,
        }
    )


def test_recon_responder_throttles_probe_flood_within_window():
    """A peer flooding reconcile probes is cut off once its window budget is spent.

    With a tiny budget, the first few one-frame requests are served (a real
    RECON_RESULT), but every subsequent over-budget request in the same window
    returns an empty result — the O(probes × inventory) hashing is never run.
    """

    async def run():
        clock = _VirtualClock(0)
        # 2 frames / window. The opening probe is one frame, so two requests fit.
        budget = ServeBudget(bytes_per_window=2, window_seconds=10, clock=clock)
        node = FabricNode(recon_budget=budget)
        # Give the responder some held inventory to reconcile against.
        for i in range(5):
            await node.weave(
                {"kind": "knowledge", "title": f"h{i}", "body": "x", "author": node.pub}
            )
        before = node.metrics.snapshot().get("recon_throttled", 0)

        served, throttled = 0, 0
        for r in range(6):
            batch = await _open_batch([f"cid-{r}-{j}" for j in range(3)])
            resp = await _recon(node, batch, peer="flooder", session_id=f"s{r}")
            assert resp["kind"] == RECON_RESULT
            # A throttled reply is empty; a served reply ran the session.
            if resp["frames"] == [] and r >= 2:
                throttled += 1
            else:
                served += 1

        # Exactly the first two one-frame requests fit the 2-frame window; the rest
        # are throttled (empty) — the per-peer window cap holds.
        assert served == 2, served
        assert throttled == 4, throttled
        after = node.metrics.snapshot().get("recon_throttled", 0)
        assert after - before == 4

    asyncio.run(run())


def test_recon_budget_refills_next_window():
    """The cap is per-peer-per-window: a throttled peer is served again next window."""

    async def run():
        clock = _VirtualClock(0)
        budget = ServeBudget(bytes_per_window=1, window_seconds=10, clock=clock)
        node = FabricNode(recon_budget=budget)
        await node.weave(
            {"kind": "knowledge", "title": "h", "body": "x", "author": node.pub}
        )

        b1 = await _open_batch(["a", "b"])
        r1 = await _recon(node, b1, peer="p", session_id="s1")  # fits (1 frame)
        b2 = await _open_batch(["a", "b"])
        r2 = await _recon(node, b2, peer="p", session_id="s2")  # over budget -> empty
        assert r2["frames"] == []

        clock.advance(10)  # cross into the next window -> bucket refills
        b3 = await _open_batch(["a", "b"])
        r3 = await _recon(node, b3, peer="p", session_id="s3")
        # Served again (ran the session): not a throttled empty reply.
        assert r3["kind"] == RECON_RESULT
        assert r1["kind"] == RECON_RESULT

    asyncio.run(run())


def test_honest_reconcile_under_default_budget_is_unbudgeted_in_practice():
    """A normal near-synced reconcile (a handful of frames) is served unchanged.

    Under the default budget (RECON_FRAMES_PER_WINDOW), an honest opening probe is
    answered with a real RECON_RESULT — the budget never bites legitimate use.
    """

    async def run():
        node = FabricNode()  # default recon budget
        for i in range(4):
            await node.weave(
                {"kind": "knowledge", "title": f"k{i}", "body": "y", "author": node.pub}
            )
        batch = await _open_batch(["x", "y", "z"])
        resp = await _recon(node, batch, peer="honest", session_id="s")
        assert resp["kind"] == RECON_RESULT
        # Default cap is one getdata batch — far above one opening probe.
        assert inventory.RECON_FRAMES_PER_WINDOW == inventory.MAX_GETDATA_BATCH

    asyncio.run(run())
