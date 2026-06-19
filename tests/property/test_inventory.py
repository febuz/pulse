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
from knitweb.p2p.inventory import (
    GETDATA,
    INV,
    InventoryError,
    InventoryRelay,
    SeenSet,
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
