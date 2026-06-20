"""Proofs for Erlay-style bounded set reconciliation over CIDs.

The reconcile module ports Bitcoin's Erlay (BIP-330) set reconciliation to the
knitweb wire — minus the non-stdlib PinSketch decoder — as recursive range
bisection over the lexically-sorted CID set: a peer summarizes a CID range by an
integer ``(count, xor-fingerprint)``, a matching summary prunes the range as
identical, a mismatch bisects into bounded children and recurses, and a small
range exchanges raw CID lists directly. These tests pin the property the module
exists to provide:

  * **converge** — two CID sets reconcile to their *exact* symmetric difference,
    even with huge overlap, even disjoint, even one side empty;
  * **O(diff)** — the number of frames grows with the size of the *difference*,
    not the inventory: a tiny diff over a large shared inventory stays cheap;
  * **bounded** — recursion depth and children-per-level are integer-capped, and
    the driver always terminates inside its round bound (no stall possible);
  * **deterministic** — lexical order + integer XOR only, no clock, no
    randomness: replaying the same two sets yields the identical frame sequence;
  * **integer-only / float-free** — fingerprints are XOR-of-SHA256 integers,
    carried as fixed-width bytes; counts/depths are ints;
  * **byte-identity sacred** — reconciliation moves only CIDs, never bodies, so
    a fresh Knit's CID is byte-for-byte unchanged (asserted directly).
"""

import hashlib

import pytest

from knitweb.core import canonical, crypto
from knitweb.ledger import knit as knit_mod
from knitweb.p2p import wire
from knitweb.p2p.reconcile import (
    FANOUT,
    FULL_HI,
    FULL_LO,
    LEAF_MAX,
    RECONCILE_LEAF,
    RECONCILE_PROBE,
    Reconciler,
    ReconcileError,
    build_leaf_frame,
    build_probe_frame,
    cid_fingerprint,
    parse_leaf_frame,
    parse_probe_frame,
    range_summary,
    reconcile_pair,
    split_range,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _cid(i: int) -> str:
    """A deterministic CID-shaped string (base32-ish text multihash)."""
    return "b" + hashlib.sha256(str(i).encode()).hexdigest()[:40]


def _cids(spec) -> list:
    return [_cid(i) for i in spec]


def _fresh_knit_record():
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


# ── 1. fingerprint: order-independent integer XOR-fold ────────────────────────

def test_fingerprint_is_order_independent():
    cids = _cids(range(20))
    import random as _r
    shuffled = list(cids)
    _r.Random(0).shuffle(shuffled)
    assert cid_fingerprint(cids) == cid_fingerprint(shuffled)


def test_fingerprint_empty_is_zero_and_is_int():
    fp = cid_fingerprint([])
    assert fp == 0
    assert isinstance(fp, int) and not isinstance(fp, bool)


def test_fingerprint_distinguishes_sets():
    assert cid_fingerprint(_cids(range(10))) != cid_fingerprint(_cids(range(11)))


def test_fingerprint_rejects_bad_cid():
    with pytest.raises(ReconcileError):
        cid_fingerprint([""])
    with pytest.raises(ReconcileError):
        cid_fingerprint([123])  # type: ignore[list-item]


# ── 2. range_summary over a sorted list ───────────────────────────────────────

def test_range_summary_full_range_covers_everything():
    cids = sorted(_cids(range(50)))
    count, fp = range_summary(cids, FULL_LO, FULL_HI)
    assert count == 50
    assert fp == cid_fingerprint(cids)


def test_range_summary_subrange_is_half_open():
    cids = sorted(["a", "b", "c", "d"])
    count, fp = range_summary(cids, "b", "d")  # [b, d) -> {b, c}
    assert count == 2
    assert fp == cid_fingerprint(["b", "c"])


def test_range_summary_empty_range():
    cids = sorted(_cids(range(20)))
    count, fp = range_summary(cids, FULL_HI, "￿￿")
    assert count == 0
    assert fp == 0


# ── 3. split_range: bounded, deterministic, covers keyspace ───────────────────

def test_split_range_fanout_and_bounds():
    children = split_range(FULL_LO, FULL_HI, FANOUT)
    assert 1 <= len(children) <= FANOUT
    # Children tile [lo, hi) contiguously: each child's hi is the next child's lo.
    assert children[0][0] == FULL_LO
    assert children[-1][1] == FULL_HI
    for (lo, hi), (nlo, nhi) in zip(children, children[1:]):
        assert hi == nlo
        assert lo < hi


def test_split_range_is_deterministic():
    assert split_range(FULL_LO, FULL_HI) == split_range(FULL_LO, FULL_HI)


def test_split_range_rejects_fanout_lt_2():
    with pytest.raises(ReconcileError):
        split_range(FULL_LO, FULL_HI, 1)


def test_split_range_partitions_population_without_loss():
    # Every CID in the parent falls into exactly one child range.
    cids = sorted(_cids(range(500)))
    children = split_range(FULL_LO, FULL_HI, FANOUT)
    total = sum(range_summary(cids, lo, hi)[0] for lo, hi in children)
    assert total == len(cids)


# ── 4. frame codec round-trips + integer-only / byte fingerprint ──────────────

def test_probe_frame_roundtrip():
    cids = sorted(_cids(range(30)))
    count, fp = range_summary(cids, FULL_LO, FULL_HI)
    frame = build_probe_frame(FULL_LO, FULL_HI, count, fp, 3)
    lo, hi, c, f, d = parse_probe_frame(frame)
    assert (lo, hi, c, f, d) == (FULL_LO, FULL_HI, count, fp, 3)


def test_probe_frame_is_canonical_cbor_with_byte_fingerprint():
    # The fingerprint is a 256-bit value; it must travel as bytes (CBOR caps ints
    # at 64 bit), and the frame must be a valid canonical frame.
    frame = build_probe_frame("a", "z", 7, (1 << 200) | 5, 0)
    msg = wire.read_frame_bytes(frame)
    assert msg["kind"] == RECONCILE_PROBE
    assert isinstance(msg["fp"], bytes) and len(msg["fp"]) == 32
    assert isinstance(msg["count"], int) and isinstance(msg["depth"], int)
    _, _, _, fp, _ = parse_probe_frame(frame)
    assert fp == (1 << 200) | 5  # exact integer round-trips through bytes


def test_leaf_frame_roundtrip():
    cids = sorted(_cids(range(5)))
    frame = build_leaf_frame("a", "z", cids)
    lo, hi, got = parse_leaf_frame(frame)
    assert (lo, hi) == ("a", "z")
    assert got == cids


def test_frame_parsers_reject_wrong_kind():
    probe = build_probe_frame("a", "z", 0, 0, 0)
    leaf = build_leaf_frame("a", "z", [])
    with pytest.raises(ReconcileError):
        parse_leaf_frame(probe)
    with pytest.raises(ReconcileError):
        parse_probe_frame(leaf)


def test_probe_frame_rejects_bad_bounds_and_negatives():
    with pytest.raises(ReconcileError):
        build_probe_frame("z", "a", 0, 0, 0)  # lo !< hi
    with pytest.raises(ReconcileError):
        build_probe_frame("a", "z", -1, 0, 0)
    with pytest.raises(ReconcileError):
        build_probe_frame("a", "z", 0, 1 << 300, 0)  # fp too wide


def test_parse_probe_frame_rejects_bad_fp_width_and_type():
    # A malicious peer can hand-roll a RECONCILE_PROBE frame straight via
    # write_frame_bytes, bypassing build_probe_frame's outbound fp validation.
    # parse_probe_frame is the inbound guard: it must reject any fp that is not
    # exactly FINGERPRINT_BYTES bytes, regardless of width or type, and that
    # guard fires before the bounds/count/depth values matter (here they are all
    # valid so only the fp check can trip).
    fp_int = cid_fingerprint(sorted(_cids(range(12))))
    expected_fp = fp_int

    # (a) wrong width: 16 bytes instead of 32.
    too_short = wire.write_frame_bytes(
        {
            "kind": RECONCILE_PROBE,
            "lo": FULL_LO,
            "hi": FULL_HI,
            "count": 12,
            "fp": b"\x00" * 16,
            "depth": 0,
        }
    )
    with pytest.raises(ReconcileError, match="fp must be 32 bytes"):
        parse_probe_frame(too_short)

    # (b) wrong type: a non-bytes value (an int) for fp.
    non_bytes = wire.write_frame_bytes(
        {
            "kind": RECONCILE_PROBE,
            "lo": FULL_LO,
            "hi": FULL_HI,
            "count": 12,
            "fp": 7,
            "depth": 0,
        }
    )
    with pytest.raises(ReconcileError, match="fp must be 32 bytes"):
        parse_probe_frame(non_bytes)

    # Round-trip sanity: a well-formed 32-byte fp parses back to the exact int.
    good = wire.write_frame_bytes(
        {
            "kind": RECONCILE_PROBE,
            "lo": FULL_LO,
            "hi": FULL_HI,
            "count": 12,
            "fp": expected_fp.to_bytes(32, "big"),
            "depth": 0,
        }
    )
    lo, hi, count, fp, depth = parse_probe_frame(good)
    assert (lo, hi, count, depth) == (FULL_LO, FULL_HI, 12, 0)
    assert fp == expected_fp


# ── 5. convergence: exact symmetric difference, all topologies ────────────────

def _check_converge(a_spec, b_spec, **kw):
    a = list(a_spec)
    b = list(b_spec)
    res = reconcile_pair(a, b, **kw)
    sa, sb = set(a), set(b)
    assert set(res["a_missing"]) == sb - sa, "a must learn exactly what it lacks"
    assert set(res["b_missing"]) == sa - sb, "b must learn exactly what it lacks"
    return res


def test_converge_large_overlap_small_diff():
    common = _cids(range(2000))
    a = common + _cids(range(900000, 900005))
    b = common + _cids(range(800000, 800007))
    _check_converge(a, b)


def test_converge_identical_sets_is_one_round_no_diff():
    common = _cids(range(2000))
    res = _check_converge(common, common)
    assert res["a_missing"] == set() and res["b_missing"] == set()
    # An identical inventory prunes at the root: a single probe, no leaf exchange.
    assert res["rounds"] == 1
    assert res["frames"] == 1


def test_converge_disjoint_sets():
    _check_converge(_cids(range(40)), _cids(range(100, 140)))


def test_converge_one_side_empty():
    common = _cids(range(300))
    res = _check_converge([], common)
    assert set(res["a_missing"]) == set(common)
    assert res["b_missing"] == set()


def test_converge_both_empty():
    res = _check_converge([], [])
    assert res["a_missing"] == set() and res["b_missing"] == set()


def test_converge_subset():
    full = _cids(range(500))
    _check_converge(full[:480], full)


def test_converge_with_tighter_params():
    common = _cids(range(1000))
    a = common + _cids(range(700000, 700010))
    b = common + _cids(range(600000, 600010))
    _check_converge(a, b, fanout=2, leaf_max=2, max_depth=80)


# ── 6. O(diff): frame cost grows with diff, not inventory ─────────────────────

def test_frames_scale_with_diff_not_inventory():
    base = _cids(range(4000))

    def frames_for(diff):
        a = base + _cids(range(900000, 900000 + diff))
        b = base + _cids(range(800000, 800000 + diff))
        return reconcile_pair(a, b)["frames"]

    f1 = frames_for(1)
    f50 = frames_for(50)
    # More difference => strictly more frames (the diff drives the cost).
    assert f50 > f1


def test_small_diff_over_huge_overlap_is_far_cheaper_than_inventory():
    # 6000 shared CIDs, 2 differing each side. A full inv-flood would announce
    # ~6000 CIDs; reconciliation must cost dramatically fewer frames than that.
    base = _cids(range(6000))
    a = base + _cids(range(910000, 910002))
    b = base + _cids(range(820000, 820002))
    res = reconcile_pair(a, b)
    assert set(res["a_missing"]) == set(b) - set(a)
    assert set(res["b_missing"]) == set(a) - set(b)
    assert res["frames"] < 600  # << the 6000-CID inventory it reconciles


# ── 7. bounded + terminating: no stall possible ───────────────────────────────

def test_reconciliation_always_terminates_within_round_bound():
    # Adversarial-ish: many scattered differences. Must still converge, never
    # raise the "did not converge" guard, never loop forever.
    base = _cids(range(1500))
    a = base + _cids(range(500000, 500030))
    b = base + _cids(range(400000, 400030))
    res = reconcile_pair(a, b, fanout=2, leaf_max=1, max_depth=90)
    assert set(res["a_missing"]) == set(b) - set(a)
    assert set(res["b_missing"]) == set(a) - set(b)


def test_reconciler_rejects_bad_construction():
    with pytest.raises(ReconcileError):
        Reconciler([""])
    with pytest.raises(ReconcileError):
        Reconciler([123])  # type: ignore[list-item]
    with pytest.raises(ReconcileError):
        Reconciler([], fanout=1)
    with pytest.raises(ReconcileError):
        Reconciler([], leaf_max=0)
    with pytest.raises(ReconcileError):
        Reconciler([], max_depth=0)


def test_on_frame_rejects_unknown_kind():
    r = Reconciler(_cids(range(3)))
    bad = wire.write_frame_bytes({"kind": "not-a-reconcile-frame"})
    with pytest.raises(ReconcileError):
        r.on_frame(bad)


# ── 8. determinism: identical frame sequence on replay ────────────────────────

def test_reconciliation_is_deterministic():
    common = _cids(range(800))
    a = common + _cids(range(300000, 300005))
    b = common + _cids(range(200000, 200005))
    r1 = reconcile_pair(a, b)
    r2 = reconcile_pair(a, b)
    assert r1["frames"] == r2["frames"]
    assert r1["rounds"] == r2["rounds"]
    assert set(r1["a_missing"]) == set(r2["a_missing"])
    assert set(r1["b_missing"]) == set(r2["b_missing"])


def test_input_order_does_not_change_result():
    common = _cids(range(800))
    a = common + _cids(range(300000, 300005))
    b = common + _cids(range(200000, 200005))
    import random as _r
    a2 = list(a)
    _r.Random(1).shuffle(a2)
    b2 = list(b)
    _r.Random(2).shuffle(b2)
    r1 = reconcile_pair(a, b)
    r2 = reconcile_pair(a2, b2)
    # Sorting/dedup inside the reconciler makes the result order-invariant.
    assert r1["frames"] == r2["frames"]
    assert set(r1["a_missing"]) == set(r2["a_missing"])
    assert set(r1["b_missing"]) == set(r2["b_missing"])


# ── 9. byte-identity sacred: reconcile moves only CIDs, never bodies ──────────

def test_reconcile_carries_only_cids_and_preserves_knit_cid():
    # A reconciled CID is the *fresh* canonical CID of a signed Knit. The whole
    # exchange moves only this string; the record body never travels, so its CID
    # is byte-for-byte unchanged — handed straight to the inventory getdata path.
    record, cid = _fresh_knit_record()
    # Peer B holds the Knit; peer A lacks it. After reconciliation A's missing
    # set is exactly that CID.
    a = _cids(range(100))
    b = _cids(range(100)) + [cid]
    res = reconcile_pair(a, b)
    assert res["a_missing"] == {cid}
    # The CID A learned re-derives byte-identically from the original record.
    (only_cid,) = res["a_missing"]
    assert only_cid == canonical.cid(record)
    # And no record body / signature ever appears in a reconcile frame: a leaf
    # frame's payload is a flat list of CID strings only.
    frame = build_leaf_frame(FULL_LO, FULL_HI, [cid])
    msg = wire.read_frame_bytes(frame)
    assert msg["kind"] == RECONCILE_LEAF
    assert msg["cids"] == [cid]
    assert "sig" not in msg and "from_sig" not in msg
