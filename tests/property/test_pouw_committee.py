"""Proofs for verifier committee selection.

A committee must be deterministic + reproducible (anyone recomputes it from seed + eligible set),
order-independent in its input, drawn without duplicates, exclude the worker, and clamp to the
pool size. Unpredictability is structural: the draw is a SHA-256 stream over a fresh seed.
"""

import pytest

from knitweb.pouw.committee import select_committee

POOL = [f"did:key:v{i}" for i in range(20)]
SEED = b"job-commit-root||epoch-beacon"


# ── 1. Determinism & reproducibility ─────────────────────────────────────────

def test_same_inputs_same_committee():
    a = select_committee(SEED, POOL, 5)
    b = select_committee(SEED, POOL, 5)
    assert a == b


def test_independent_of_eligible_input_order():
    a = select_committee(SEED, POOL, 7)
    b = select_committee(SEED, list(reversed(POOL)), 7)
    c = select_committee(SEED, sorted(POOL, key=lambda s: s[::-1]), 7)
    assert a == b == c                       # canonicalised by sorting the set internally


def test_duplicates_in_eligible_do_not_matter():
    a = select_committee(SEED, POOL, 5)
    b = select_committee(SEED, POOL + POOL[:5], 5)   # dupes ignored
    assert a == b


# ── 2. Membership properties ─────────────────────────────────────────────────

def test_members_are_distinct_and_from_the_pool():
    c = select_committee(SEED, POOL, 8)
    assert len(c) == 8
    assert len(set(c)) == 8                  # no duplicates
    assert set(c) <= set(POOL)


def test_worker_is_excluded_from_its_own_jury():
    worker = POOL[3]
    c = select_committee(SEED, POOL, 19, exclude=worker)
    assert worker not in c
    assert len(c) == 19                      # 20 eligible − the excluded worker


def test_clamps_to_pool_size():
    c = select_committee(SEED, POOL[:4], 10)  # ask 10 from a pool of 4
    assert len(c) == 4
    assert set(c) == set(POOL[:4])


def test_k_zero_and_empty_pool_give_empty():
    assert select_committee(SEED, POOL, 0) == []
    assert select_committee(SEED, [], 5) == []
    assert select_committee(SEED, [POOL[0]], 5, exclude=POOL[0]) == []


# ── 3. Seed sensitivity (unpredictability is seed-driven) ────────────────────

def test_different_seed_changes_the_committee():
    a = select_committee(b"seed-A", POOL, 6)
    b = select_committee(b"seed-B", POOL, 6)
    assert a != b                            # selection follows the seed


def test_full_pool_selection_is_a_permutation():
    # drawing the whole pool yields every member exactly once (a seeded shuffle)
    c = select_committee(SEED, POOL, len(POOL))
    assert sorted(c) == sorted(POOL)
    assert len(c) == len(set(c)) == len(POOL)


# ── 4. Validation guards ─────────────────────────────────────────────────────

def test_seed_must_be_nonempty_bytes():
    with pytest.raises(TypeError):
        select_committee("not-bytes", POOL, 3)
    with pytest.raises(ValueError):
        select_committee(b"", POOL, 3)


def test_k_must_be_nonnegative_int():
    with pytest.raises(TypeError):
        select_committee(SEED, POOL, True)
    with pytest.raises(ValueError):
        select_committee(SEED, POOL, -1)


def test_eligible_entries_must_be_nonempty_str():
    with pytest.raises(TypeError):
        select_committee(SEED, ["ok", ""], 1)
    with pytest.raises(TypeError):
        select_committee(SEED, ["ok", 123], 1)
