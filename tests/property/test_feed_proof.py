"""Proofs for feed inclusion proofs (backlog B10 — Hypercore-style partial replication).

A peer must verify one entry (or a contiguous range) of a signed feed against the head's
Merkle root using only an O(log n) sibling path — never the whole log. The proof must match
``core.crypto.merkle_root`` exactly, which we pin by cross-checking every reconstructed root
against a real ``Feed.root()``.
"""

import pytest

from knitweb.core import crypto
from knitweb.fabric.feed import Feed
from knitweb.fabric.feed_proof import (
    InclusionProof,
    prove_inclusion,
    verify_inclusion,
    verify_range,
)


def _feed(n, tag="entry"):
    """A feed with ``n`` deterministic entries (no randomness — reproducible).

    ``tag`` distinguishes the content of otherwise-same-shape feeds, so two feeds can
    be given genuinely different Merkle roots when a test needs that.
    """
    priv, _ = crypto.generate_keypair()
    f = Feed(priv)
    for i in range(n):
        f.append({"i": i, "payload": f"{tag}-{i}"})
    return f


# ── 1. Every index of every feed size verifies against the signed head ───────

def test_inclusion_verifies_for_all_indices_and_sizes():
    for n in range(1, 18):                       # exercises odd/even/duplicate-last shapes
        f = _feed(n)
        head = f.head()
        for i in range(n):
            proof = prove_inclusion(f.entries, i)
            assert verify_inclusion(head, f.entry(i), proof), f"n={n} i={i}"


def test_proof_matches_real_merkle_root():
    # The reconstructed root MUST equal crypto.merkle_root over the same leaves
    # (i.e. exactly what Feed.head() signed) — pins the tree construction.
    for n in (1, 2, 3, 5, 8, 13):
        f = _feed(n)
        head = f.head()
        assert head.root == f.root()             # sanity: head commits to feed root
        for i in range(n):
            proof = prove_inclusion(f.entries, i)
            assert verify_inclusion(head, f.entry(i), proof)


# ── 2. Tampering is rejected ─────────────────────────────────────────────────

def test_wrong_entry_rejected():
    f = _feed(6)
    head = f.head()
    proof = prove_inclusion(f.entries, 2)
    assert not verify_inclusion(head, {"i": 2, "payload": "TAMPERED"}, proof)


def test_wrong_index_proof_rejected():
    f = _feed(6)
    head = f.head()
    proof_for_2 = prove_inclusion(f.entries, 2)
    # claim entry 3 with entry-2's proof
    assert not verify_inclusion(head, f.entry(3), proof_for_2)


def test_tampered_path_rejected():
    f = _feed(7)
    head = f.head()
    proof = prove_inclusion(f.entries, 4)
    if proof.path:
        sib_hex, is_right = proof.path[0]
        flipped = ("00" * (len(sib_hex) // 2), is_right)
        bad = InclusionProof(index=proof.index, length=proof.length, path=[flipped] + proof.path[1:])
        assert not verify_inclusion(head, f.entry(4), bad)


def test_proof_from_other_feed_rejected():
    a, b = _feed(5, tag="alpha"), _feed(5, tag="beta")   # distinct content => distinct roots
    head_a = a.head()
    proof_b = prove_inclusion(b.entries, 1)
    # b's entry+proof must not verify against a's signed head (different root)
    assert not verify_inclusion(head_a, b.entry(1), proof_b)


def test_length_mismatch_rejected():
    f = _feed(5)
    head = f.head()
    proof = prove_inclusion(f.entries, 0)
    forged = InclusionProof(index=0, length=99, path=proof.path)
    assert not verify_inclusion(head, f.entry(0), forged)


def test_head_with_bad_signature_rejected():
    f = _feed(4)
    head = f.head()
    from dataclasses import replace
    bad_head = replace(head, sig="00" * 70)
    proof = prove_inclusion(f.entries, 0)
    assert not verify_inclusion(bad_head, f.entry(0), proof)


# ── 3. Equivocation safety: a proof for one history can't move to another ────

def test_proof_does_not_transfer_across_appends():
    f = _feed(4)
    head4 = f.head()
    proof0_at4 = prove_inclusion(f.entries, 0)
    f.append({"i": 4, "payload": "entry-4"})     # feed grows; root + head change
    head5 = f.head()
    # the OLD proof (built for length 4) must not verify against the NEW head (length 5)
    assert not verify_inclusion(head5, f.entry(0), proof0_at4)
    # but a freshly built proof for the new length does
    assert verify_inclusion(head5, f.entry(0), prove_inclusion(f.entries, 0))


# ── 4. Contiguous range verification ─────────────────────────────────────────

def test_verify_range_contiguous_slice():
    f = _feed(10)
    head = f.head()
    start, end = 3, 7
    sl = [f.entry(i) for i in range(start, end)]
    proofs = [prove_inclusion(f.entries, i) for i in range(start, end)]
    assert verify_range(head, start, sl, proofs)


def test_verify_range_empty_is_vacuously_true():
    f = _feed(5)
    assert verify_range(f.head(), 2, [], [])


def test_verify_range_rejects_non_contiguous_or_misplaced():
    f = _feed(10)
    head = f.head()
    sl = [f.entry(3), f.entry(5)]                 # not contiguous
    proofs = [prove_inclusion(f.entries, 3), prove_inclusion(f.entries, 5)]
    assert not verify_range(head, 3, sl, proofs)


def test_verify_range_rejects_out_of_bounds():
    f = _feed(5)
    head = f.head()
    sl = [f.entry(4)]
    proofs = [prove_inclusion(f.entries, 4)]
    assert not verify_range(head, 4, sl + [{"i": 5}], proofs + [prove_inclusion(f.entries, 4)])


def test_verify_range_length_mismatch_raises():
    f = _feed(5)
    with pytest.raises(ValueError):
        verify_range(f.head(), 0, [f.entry(0)], [])


# ── 5. Validation guards ─────────────────────────────────────────────────────

def test_prove_inclusion_out_of_range_raises():
    f = _feed(3)
    with pytest.raises(IndexError):
        prove_inclusion(f.entries, 3)


def test_prove_inclusion_bool_index_rejected():
    f = _feed(3)
    with pytest.raises(TypeError):
        prove_inclusion(f.entries, True)


def test_single_entry_feed_has_empty_path():
    f = _feed(1)
    proof = prove_inclusion(f.entries, 0)
    assert proof.path == []                       # root == leaf, no siblings
    assert verify_inclusion(f.head(), f.entry(0), proof)
