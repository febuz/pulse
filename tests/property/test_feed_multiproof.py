"""Proofs for feed range multiproofs (B10 — Hypercore-style, bandwidth-optimal half).

A contiguous range of m entries must be authenticated against the signed feed head with
O(log n) *shared* sibling hashes, not m independent paths. Correctness is pinned by
reconstructing the root and comparing it to a real ``Feed.root()`` across many sizes and
ranges (odd/even/duplicate-last shapes); the bandwidth win is asserted directly.
"""

from dataclasses import replace

import pytest

from knitweb.core import crypto
from knitweb.fabric.feed import Feed
from knitweb.fabric.feed_multiproof import (
    RangeMultiProof,
    prove_range,
    verify_range_multiproof,
)


def _feed(n, tag="entry"):
    priv, _ = crypto.generate_keypair()
    f = Feed(priv)
    for i in range(n):
        f.append({"i": i, "payload": f"{tag}-{i}"})
    return f


# ── 1. Every contiguous range of every feed size verifies ────────────────────

def test_all_ranges_all_sizes_verify():
    for n in range(1, 20):                       # exercises odd/even/duplicate-last shapes
        f = _feed(n)
        head = f.head()
        for start in range(n):
            for count in range(1, n - start + 1):
                proof = prove_range(f.entries, start, count)
                ents = [f.entry(start + j) for j in range(count)]
                assert verify_range_multiproof(head, ents, proof), f"n={n} [{start},{start+count})"


def test_full_range_reconstructs_root():
    for n in (1, 2, 3, 5, 8, 16, 17):
        f = _feed(n)
        head = f.head()
        assert head.root == f.root()
        proof = prove_range(f.entries, 0, n)
        assert verify_range_multiproof(head, f.entries, proof)


# ── 2. The bandwidth win: O(log n), not O(m·log n) ──────────────────────────

def test_multiproof_is_logarithmic_not_linear():
    f = _feed(64)
    head = f.head()
    start, count = 10, 40                          # a wide range
    proof = prove_range(f.entries, start, count)
    ents = [f.entry(start + j) for j in range(count)]
    assert verify_range_multiproof(head, ents, proof)
    height = 6                                      # log2(64)
    # shared-path proof carries at most ~2 boundary siblings per level…
    assert len(proof.siblings) <= 2 * height + 2
    # …and is dramatically smaller than one path per entry would be.
    assert len(proof.siblings) < count


# ── 3. Tampering is rejected ─────────────────────────────────────────────────

def test_wrong_entry_in_range_rejected():
    f = _feed(12)
    head = f.head()
    proof = prove_range(f.entries, 3, 4)
    ents = [f.entry(3), {"i": 4, "payload": "TAMPERED"}, f.entry(5), f.entry(6)]
    assert not verify_range_multiproof(head, ents, proof)


def test_tampered_sibling_rejected():
    f = _feed(13)
    head = f.head()
    proof = prove_range(f.entries, 2, 3)
    ents = [f.entry(2), f.entry(3), f.entry(4)]
    if proof.siblings:
        bad = replace(proof, siblings=["00" * 32] + proof.siblings[1:])
        assert not verify_range_multiproof(head, ents, bad)


def test_wrong_start_rejected():
    f = _feed(10)
    head = f.head()
    proof = prove_range(f.entries, 4, 3)           # proof is for [4,7)
    ents = [f.entry(4), f.entry(5), f.entry(6)]
    moved = replace(proof, start=3)                # claim it covers [3,6)
    assert not verify_range_multiproof(head, ents, moved)


def test_out_of_bounds_rejected():
    f = _feed(6)
    head = f.head()
    proof = prove_range(f.entries, 4, 2)
    ents = [f.entry(4), f.entry(5)]
    over = replace(proof, count=3)                 # would run off the end
    assert not verify_range_multiproof(head, ents[:] + [{"i": 6}], over)


def test_length_mismatch_rejected():
    f = _feed(8)
    head = f.head()
    proof = prove_range(f.entries, 0, 3)
    forged = replace(proof, length=99)
    assert not verify_range_multiproof(head, [f.entry(0), f.entry(1), f.entry(2)], forged)


def test_extra_or_short_siblings_rejected():
    f = _feed(15)
    head = f.head()
    proof = prove_range(f.entries, 5, 3)
    ents = [f.entry(5), f.entry(6), f.entry(7)]
    assert verify_range_multiproof(head, ents, proof)            # baseline ok
    too_many = replace(proof, siblings=proof.siblings + ["00" * 32])
    assert not verify_range_multiproof(head, ents, too_many)     # leftover sibling
    if proof.siblings:
        too_few = replace(proof, siblings=proof.siblings[:-1])
        assert not verify_range_multiproof(head, ents, too_few)  # ran out


def test_bad_head_signature_rejected():
    f = _feed(9)
    head = replace(f.head(), sig="00" * 70)
    proof = prove_range(f.entries, 0, 4)
    assert not verify_range_multiproof(head, [f.entry(i) for i in range(4)], proof)


def test_proof_from_other_feed_rejected():
    a, b = _feed(8, tag="alpha"), _feed(8, tag="beta")
    head_a = a.head()
    proof_b = prove_range(b.entries, 2, 3)
    ents_b = [b.entry(2), b.entry(3), b.entry(4)]
    assert not verify_range_multiproof(head_a, ents_b, proof_b)


def test_stale_proof_after_append_rejected():
    f = _feed(7)
    proof_at7 = prove_range(f.entries, 1, 3)
    ents = [f.entry(1), f.entry(2), f.entry(3)]
    assert verify_range_multiproof(f.head(), ents, proof_at7)    # ok at length 7
    f.append({"i": 7, "payload": "entry-7"})                     # feed grows
    assert not verify_range_multiproof(f.head(), ents, proof_at7)  # stale vs new head


# ── 4. Validation guards ─────────────────────────────────────────────────────

def test_prove_range_out_of_bounds_raises():
    f = _feed(5)
    with pytest.raises(IndexError):
        prove_range(f.entries, 3, 3)               # [3,6) > 5
    with pytest.raises(IndexError):
        prove_range(f.entries, -1, 2)


def test_prove_range_nonpositive_count_raises():
    f = _feed(5)
    with pytest.raises(ValueError):
        prove_range(f.entries, 0, 0)


def test_prove_range_bool_index_rejected():
    f = _feed(5)
    with pytest.raises(TypeError):
        prove_range(f.entries, True, 2)
