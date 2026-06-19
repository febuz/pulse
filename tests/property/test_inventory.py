"""Proofs for the inventory relay — announce/want CID dedup over full flood.

The inventory relay ports Bitcoin Core's ``inv -> getdata`` relay (and
gossipsub's IHAVE/IWANT lazy push) to the knitweb wire: a node announces a
record's *canonical CID* first, peers request (``getdata``) only the CIDs they
lack, and a bounded integer-LRU ``SeenSet`` dedups both directions. These tests
pin the security/robustness property the module provides:

  * **dedup** — a CID already seen is never re-announced or re-requested, so a
    full N-peer flood collapses to O(diff) traffic;
  * **bounded** — the SeenSet evicts least-recently-used and never exceeds its
    integer capacity (no memory-exhaustion vector on a long-lived node);
  * **deterministic** — insertion-order LRU only, no clock and no randomness;
  * **byte-identity sacred** — a record relayed via getdata is returned as the
    *verbatim stored frame*, so a fresh Knit's CID is byte-for-byte unchanged
    across a relay hop (asserted directly).
"""

import pytest

from knitweb.core import canonical, crypto
from knitweb.ledger import knit as knit_mod
from knitweb.p2p import wire
from knitweb.p2p import inventory as inventory_mod
from knitweb.p2p.inventory import (
    GETDATA,
    INV,
    MAX_GETDATA_BATCH,
    SERVE_BYTES_PER_WINDOW,
    SERVE_WINDOW_SECONDS,
    InventoryError,
    InventoryRelay,
    SeenSet,
    ServeBudget,
    build_getdata_frame,
    build_inv_frame,
    parse_getdata_frame,
    parse_inv_frame,
    record_cid,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _record(i: int) -> dict:
    """A small deterministic fabric record (integer-only, float-free)."""
    return {"kind": "demo", "seq": i, "payload": f"r{i}"}


def _fresh_knit_record():
    """A signed Knit as a fabric record + its canonical CID, for byte-identity."""
    priv, pub = crypto.generate_keypair()
    _priv2, pub2 = crypto.generate_keypair()
    knit = knit_mod.Knit(
        from_pub=pub,
        to_pub=pub2,
        symbol="PLS",
        amount=1000,
        from_nonce=0,
        timestamp=1,
        network=1,
    )
    record = wire.knit_to_record(knit)
    return record, canonical.cid(record)


# ── 1. SeenSet: dedup + bounded LRU + determinism ─────────────────────────────

def test_seenset_add_reports_newness_and_dedups():
    s = SeenSet(capacity=10)
    assert s.add("a") is True
    assert s.add("a") is False  # repeat is not new
    assert s.add("b") is True
    assert len(s) == 2
    assert "a" in s and "b" in s and "c" not in s


def test_seenset_filter_unseen_is_readonly_and_dedups_input():
    s = SeenSet(capacity=10)
    s.add("a")
    # "a" already seen -> dropped; "b" twice in input -> emitted once.
    assert s.filter_unseen(["a", "b", "b", "c"]) == ["b", "c"]
    # filter_unseen must NOT have inserted anything.
    assert "b" not in s and "c" not in s
    assert len(s) == 1


def test_seenset_is_bounded_and_evicts_lru():
    s = SeenSet(capacity=3)
    for cid in ["a", "b", "c"]:
        s.add(cid)
    # touch "a" so it is most-recently-used; "b" is now the LRU.
    assert s.add("a") is False
    s.add("d")  # evicts the LRU, which is "b"
    assert len(s) == 3
    assert "b" not in s
    assert "a" in s and "c" in s and "d" in s


def test_seenset_never_exceeds_capacity_under_flood():
    s = SeenSet(capacity=64)
    for i in range(10_000):
        s.add(f"cid-{i}")
    assert len(s) == 64
    # The 64 survivors are exactly the most-recent insertions (deterministic).
    assert list(s) == [f"cid-{i}" for i in range(9_936, 10_000)]


def test_seenset_eviction_is_deterministic_across_replays():
    def replay():
        s = SeenSet(capacity=4)
        for cid in ["a", "b", "c", "a", "d", "e", "f"]:
            s.add(cid)
        return list(s)

    assert replay() == replay()


def test_seenset_rejects_bad_input():
    s = SeenSet()
    with pytest.raises(TypeError):
        s.add(123)  # type: ignore[arg-type]
    with pytest.raises(InventoryError):
        s.add("")
    with pytest.raises(ValueError):
        SeenSet(capacity=0)
    with pytest.raises(TypeError):
        SeenSet(capacity=True)  # type: ignore[arg-type]


# ── 2. Frame codec: shares wire framing, round-trips, bounded ─────────────────

def test_inv_frame_roundtrips_and_uses_wire_framing():
    cids = ["bcid1", "bcid2", "bcid3"]
    frame = build_inv_frame(cids)
    # It IS a standard wire frame (4-byte length prefix + canonical CBOR).
    assert wire.read_frame_bytes(frame)["kind"] == INV
    assert parse_inv_frame(frame) == cids


def test_getdata_frame_roundtrips():
    cids = ["bcidX", "bcidY"]
    frame = build_getdata_frame(cids)
    assert wire.read_frame_bytes(frame)["kind"] == GETDATA
    assert parse_getdata_frame(frame) == cids


def test_parse_rejects_wrong_kind():
    inv = build_inv_frame(["a"])
    with pytest.raises(InventoryError):
        parse_getdata_frame(inv)
    getdata = build_getdata_frame(["a"])
    with pytest.raises(InventoryError):
        parse_inv_frame(getdata)


def test_frame_rejects_overlarge_cid_list():
    from knitweb.p2p.inventory import MAX_CIDS_PER_FRAME

    too_many = [f"c{i}" for i in range(MAX_CIDS_PER_FRAME + 1)]
    with pytest.raises(InventoryError):
        build_inv_frame(too_many)


def test_frame_rejects_non_str_cid():
    with pytest.raises(InventoryError):
        build_inv_frame(["ok", 5])  # type: ignore[list-item]


# ── 3. record_cid is exactly the Web's content address ────────────────────────

def test_record_cid_matches_canonical_cid():
    rec = _record(7)
    assert record_cid(rec) == canonical.cid(rec)


def test_record_cid_rejects_non_map():
    with pytest.raises(InventoryError):
        record_cid([1, 2, 3])  # type: ignore[arg-type]


# ── 4. InventoryRelay: announce/want dedup -> O(diff) traffic ─────────────────

class FrameStore:
    """A tiny CID -> verbatim-frame store standing in for a node's record store."""

    def __init__(self) -> None:
        self.frames: dict[str, bytes] = {}

    def put_record(self, record: dict) -> tuple[str, bytes]:
        cid = record_cid(record)
        frame = wire.write_frame_bytes({"kind": "fabric-record", "record": record})
        self.frames[cid] = frame
        return cid, frame

    def lookup(self, cid: str):
        return self.frames.get(cid)


def test_announce_dedups_already_seen_cids():
    relay = InventoryRelay(lambda cid: None)
    f1 = relay.announce(["a", "b"])
    assert parse_inv_frame(f1) == ["a", "b"]
    # Re-announcing the same CIDs yields nothing to send.
    assert relay.announce(["a", "b"]) is None
    # Only the genuinely-new CID is announced.
    f2 = relay.announce(["a", "c"])
    assert parse_inv_frame(f2) == ["c"]


def test_on_inv_wants_only_missing_cids():
    # Receiver already holds "a"; lacks "b" and "c".
    store = FrameStore()
    cid_a, _ = store.put_record(_record(1))
    relay = InventoryRelay(store.lookup)

    inv = build_inv_frame([cid_a, "missing-b", "missing-c"])
    getdata = relay.on_inv(inv)
    assert parse_getdata_frame(getdata) == ["missing-b", "missing-c"]

    # A second identical inv (e.g. from another peer) produces NO new want:
    # the wanted CIDs were marked seen. This is the O(diff) collapse.
    assert relay.on_inv(inv) is None


def test_on_inv_returns_none_when_nothing_missing():
    store = FrameStore()
    cid_a, _ = store.put_record(_record(1))
    relay = InventoryRelay(store.lookup)
    assert relay.on_inv(build_inv_frame([cid_a])) is None


def test_full_announce_want_record_exchange_converges():
    # Sender holds two records; receiver holds neither.
    sender_store = FrameStore()
    cid1, frame1 = sender_store.put_record(_record(1))
    cid2, frame2 = sender_store.put_record(_record(2))
    sender = InventoryRelay(sender_store.lookup)

    receiver_store = FrameStore()
    receiver = InventoryRelay(receiver_store.lookup)

    # 1) sender announces.
    inv = sender.announce([cid1, cid2])
    # 2) receiver wants what it lacks (both).
    getdata = receiver.on_inv(inv)
    assert set(parse_getdata_frame(getdata)) == {cid1, cid2}
    # 3) sender returns the stored frames verbatim.
    record_frames = sender.on_getdata(getdata)
    assert set(record_frames) == {frame1, frame2}
    # 4) receiver stores + marks seen; a re-announce now wants nothing.
    for fr in record_frames:
        rec = wire.read_frame_bytes(fr)["record"]
        c, _ = receiver_store.put_record(rec)
        receiver.on_record(c)
    assert receiver.on_inv(sender.announce([cid1, cid2]) or build_inv_frame([cid1, cid2])) is None


def test_on_getdata_skips_unheld_cids():
    store = FrameStore()
    cid1, frame1 = store.put_record(_record(1))
    relay = InventoryRelay(store.lookup)
    frames = relay.on_getdata(build_getdata_frame([cid1, "not-held"]))
    assert frames == [frame1]


def test_on_getdata_rejects_non_bytes_lookup():
    relay = InventoryRelay(lambda cid: "not-bytes")  # type: ignore[return-value]
    with pytest.raises(InventoryError):
        relay.on_getdata(build_getdata_frame(["x"]))


# ── 5. byte-identity is sacred: a relayed Knit's CID is unchanged ─────────────

def test_relayed_record_frame_preserves_signed_byte_identity():
    """A fresh Knit relayed via inv->getdata->record keeps its EXACT CID.

    The sender stores the verbatim signed frame and ``on_getdata`` returns those
    same bytes — no decode/re-encode — so the receiver re-derives the identical
    CID. If a relay hop ever perturbed a single byte, this assertion breaks.
    """
    record, cid_before = _fresh_knit_record()

    sender_store = FrameStore()
    cid_stored, original_frame = sender_store.put_record(record)
    assert cid_stored == cid_before  # store indexes by the canonical CID

    sender = InventoryRelay(sender_store.lookup)
    receiver = InventoryRelay(lambda cid: None)

    inv = sender.announce([cid_before])
    getdata = receiver.on_inv(inv)
    [relayed_frame] = sender.on_getdata(getdata)

    # The frame bytes are byte-for-byte identical (verbatim relay).
    assert relayed_frame == original_frame

    # The record decoded on the far side re-derives the identical CID, and the
    # embedded Knit's signable bytes are unchanged -> signature still verifies.
    relayed_record = wire.read_frame_bytes(relayed_frame)["record"]
    assert canonical.cid(relayed_record) == cid_before
    assert relayed_record == record


# ── 6. driveable through the anti-entropy SyncRound pattern (no core edits) ───

def test_relay_drives_as_a_sync_round_callback():
    """The relay's announce step plugs into the anti_entropy SyncRound shape.

    A SyncRound is ``Callable[[], Awaitable[int]]`` returning integer progress.
    Here we prove the relay produces a clean integer 'new announcements' count
    that such a round can return, without importing or editing any core node file.
    """
    import asyncio

    store = FrameStore()
    cid1, _ = store.put_record(_record(1))
    cid2, _ = store.put_record(_record(2))
    relay = InventoryRelay(store.lookup)
    pending = [cid1, cid2]

    async def sync_round() -> int:
        frame = relay.announce(pending)
        if frame is None:
            return 0
        return len(parse_inv_frame(frame))

    assert asyncio.run(sync_round()) == 2
    # Second round: everything already announced -> zero progress (deterministic).
    assert asyncio.run(sync_round()) == 0


# ── 7. anti-amplification: per-request batch cap + per-peer byte budget (#91) ──
#
# A single inv-getdata / mesh-IWANT can name tens of thousands of CIDs; the serve
# path returns the FULL stored body for each, with (pre-#91) no per-peer cap on
# count or bytes. A ~2 MiB request could elicit hundreds of GiB served. These
# tests pin the two caps that kill that amplification while leaving an honest
# (small/moderate) diff fully servable.


class _VirtualClock:
    """A deterministic, injectable monotonic integer-second clock for the budget.

    No wall-clock, no randomness: ``advance`` is the ONLY way time moves, so the
    budget's window boundary is fully replayable.
    """

    def __init__(self, t: int = 0) -> None:
        self._t = t

    def __call__(self) -> int:
        return self._t

    def advance(self, secs: int) -> None:
        self._t += secs


def _bulk_store(n: int) -> "tuple[FrameStore, list[str]]":
    """A store of ``n`` distinct records; return it and the ordered CID list."""
    store = FrameStore()
    cids = []
    for i in range(n):
        cid, _ = store.put_record(_record(i))
        cids.append(cid)
    return store, cids


def test_getdata_serves_at_most_the_batch_cap_not_the_whole_store():
    """A getdata for FAR more than the batch cap serves AT MOST the cap.

    LOAD-BEARING: the store holds 3x the batch cap and the peer asks for ALL of
    them in one request, yet at most ``MAX_GETDATA_BATCH`` bodies come back — the
    whole store does NOT reflect back. Reverting the cap (serving every wanted
    CID) makes this serve 3x the cap, so the assertion is load-bearing.
    """
    n = MAX_GETDATA_BATCH * 3
    store, cids = _bulk_store(n)
    # No per-peer key here -> only the per-request COUNT cap applies (an
    # unidentified carrier still cannot bypass the hard ceiling).
    relay = InventoryRelay(store.lookup)

    served = relay.on_getdata(build_getdata_frame(cids))

    assert len(served) == MAX_GETDATA_BATCH
    # Dramatically less than the whole store (the amplification that #91 kills).
    assert len(served) < n
    # Sanity: had there been NO cap, every one of the n held CIDs would serve.
    # (We prove the un-capped count directly off the store to keep the contrast.)
    assert sum(1 for c in cids if store.lookup(c) is not None) == n


def test_per_peer_byte_budget_throttles_a_hammering_peer_then_refills():
    """A peer hammering getdata is throttled to a fixed bytes/window ceiling.

    The byte bucket grants at most ``SERVE_BYTES_PER_WINDOW`` body bytes per
    integer window. We size a tiny budget and oversize bodies so a single request
    of many bodies exhausts the window; further requests in the SAME window serve
    nothing; crossing into the NEXT window refills and serves again.
    """
    store, cids = _bulk_store(20)
    body_len = len(store.lookup(cids[0]))
    assert body_len > 0
    # Budget allows exactly 3 bodies per window for this peer.
    per_window = body_len * 3
    clock = _VirtualClock(0)
    budget = ServeBudget(
        bytes_per_window=per_window, window_seconds=5, clock=clock
    )
    relay = InventoryRelay(store.lookup, budget=budget)
    frame = build_getdata_frame(cids)

    # First request in window 0: served up to the byte budget (3 bodies), then the
    # bucket is exhausted and the remaining wanted bodies are deferred, NOT served.
    first = relay.on_getdata(frame, peer="peerX")
    assert len(first) == 3

    # Hammering again in the SAME window: budget exhausted -> nothing served.
    again = relay.on_getdata(frame, peer="peerX")
    assert again == []

    # A DIFFERENT peer has its own independent bucket (per-peer, not global).
    other = relay.on_getdata(frame, peer="peerY")
    assert len(other) == 3

    # Crossing into the next integer window refills peerX's bucket -> serves again.
    clock.advance(5)
    refilled = relay.on_getdata(frame, peer="peerX")
    assert len(refilled) == 3


def test_byte_budget_never_partial_serves_a_body_preserving_identity():
    """The budget stops on a whole-body boundary: a served body is never truncated.

    A peer with budget for 2.5 bodies serves exactly 2 WHOLE bodies (never half a
    third) — so every served frame is byte-identical to what was stored, and the
    cap can never corrupt a signed record's bytes.
    """
    store, cids = _bulk_store(10)
    body_len = len(store.lookup(cids[0]))
    clock = _VirtualClock(0)
    budget = ServeBudget(
        bytes_per_window=body_len * 2 + body_len // 2,  # 2.5 bodies
        window_seconds=5,
        clock=clock,
    )
    relay = InventoryRelay(store.lookup, budget=budget)

    served = relay.on_getdata(build_getdata_frame(cids), peer="p")
    assert len(served) == 2
    # Each served frame is the verbatim stored frame (byte-identity sacred).
    for cid, fr in zip(cids, served):
        assert fr == store.lookup(cid)


def test_byte_budget_under_honest_moderate_diff_serves_the_whole_diff():
    """An honest moderate diff is served IN FULL under the generous prod budget.

    Sizes the diff at the prod batch cap and confirms the default prod byte budget
    (256 MiB/window) is generous enough that an honest reconcile of that diff is
    served completely in ONE window — the cap must not starve honest reconcile.
    """
    n = MAX_GETDATA_BATCH  # a full honest batch
    store, cids = _bulk_store(n)
    body_len = len(store.lookup(cids[0]))
    # The prod byte budget must comfortably cover a full honest batch.
    assert SERVE_BYTES_PER_WINDOW > body_len * n
    clock = _VirtualClock(0)
    budget = ServeBudget(clock=clock)  # prod-default byte/window caps
    relay = InventoryRelay(store.lookup, budget=budget)

    served = relay.on_getdata(build_getdata_frame(cids), peer="honest")
    # Served fully (count cap == batch size, byte budget not the limiter).
    assert len(served) == n


def test_serve_budget_is_deterministic_across_replays():
    """Two budgets driven by identical virtual clocks debit identically.

    No wall-clock, no randomness: replaying the same request/clock sequence yields
    byte-for-byte identical serve decisions.
    """
    store, cids = _bulk_store(12)
    body_len = len(store.lookup(cids[0]))
    frame = build_getdata_frame(cids)

    def replay():
        clk = _VirtualClock(100)
        relay = InventoryRelay(
            store.lookup,
            budget=ServeBudget(
                bytes_per_window=body_len * 4, window_seconds=3, clock=clk
            ),
        )
        out = []
        out.append(len(relay.on_getdata(frame, peer="q")))
        out.append(len(relay.on_getdata(frame, peer="q")))
        clk.advance(3)
        out.append(len(relay.on_getdata(frame, peer="q")))
        return out

    assert replay() == replay()


def test_serve_budget_rejects_bad_construction_and_input():
    with pytest.raises(TypeError):
        ServeBudget(bytes_per_window=1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ServeBudget(window_seconds=0)
    with pytest.raises(TypeError):
        ServeBudget(clock="nope")  # type: ignore[arg-type]
    b = ServeBudget(clock=_VirtualClock(0))
    with pytest.raises(InventoryError):
        b.take("", 10)
    with pytest.raises(ValueError):
        b.take("p", -1)
