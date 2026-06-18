"""Proofs for PoUW sample-size sizing (the audit-side soundness bound).

If a worker corrupts ``corrupt`` of ``n`` blocks, a k-sample (no replacement) misses all of them
with hypergeometric probability ``C(n-corrupt,k)/C(n,k)``. We size ``k`` so the miss probability
is at or below a target. All exact rational arithmetic — no floats.
"""

from fractions import Fraction

import pytest

from knitweb.pouw.sampling import (
    catch_probability,
    miss_probability,
    required_samples,
)


# ── 1. Exact miss/catch probabilities ────────────────────────────────────────

def test_single_corrupt_block_telescopes_to_linear():
    # corrupt=1: miss(k) = (n-k)/n exactly (the product telescopes).
    n = 10
    for k in range(0, n + 1):
        assert miss_probability(n, 1, k) == Fraction(n - k, n)


def test_catch_is_one_minus_miss():
    for k in range(0, 11):
        assert catch_probability(10, 3, k) == Fraction(1) - miss_probability(10, 3, k)


def test_no_corruption_is_never_caught():
    # Nothing to detect ⇒ miss is 1 for any k.
    assert miss_probability(8, 0, 5) == Fraction(1)
    assert catch_probability(8, 0, 5) == Fraction(0)


def test_sampling_everything_always_catches():
    assert miss_probability(7, 2, 7) == Fraction(0)
    assert catch_probability(7, 2, 7) == Fraction(1)


def test_more_corruption_is_easier_to_catch():
    # miss decreases as corruption rises (fixed n, k)
    assert miss_probability(20, 1, 3) > miss_probability(20, 5, 3) > miss_probability(20, 10, 3)


def test_miss_is_monotone_nonincreasing_in_k():
    prev = Fraction(2)  # > 1 sentinel
    for k in range(0, 13):
        m = miss_probability(12, 4, k)
        assert m <= prev
        prev = m


# ── 2. required_samples — exact, minimal k ───────────────────────────────────

def test_required_samples_single_corrupt_exact():
    # corrupt=1, miss(k)=(n-k)/n. miss<=1/2  ⇔  k>=n/2.
    assert required_samples(100, 1, Fraction(1, 2)) == 50
    # miss<=1/100  ⇔  (100-k)/100<=1/100  ⇔  k>=99
    assert required_samples(100, 1, Fraction(1, 100)) == 99


def test_required_samples_is_minimal():
    # the returned k meets the target and k-1 does not (true minimum), across cases.
    cases = [(50, 1), (50, 5), (200, 2), (30, 11), (64, 64)]
    target = Fraction(1, 20)  # catch >= 95%
    for n, corrupt in cases:
        k = required_samples(n, corrupt, target)
        assert miss_probability(n, corrupt, k) <= target
        if k > 0:
            assert miss_probability(n, corrupt, k - 1) > target


def test_target_one_needs_no_samples_target_zero_forces_a_hit():
    assert required_samples(10, 3, Fraction(1)) == 0          # miss<=1 trivially
    # max_miss == 0 ⇒ must guarantee a hit ⇒ k = n - corrupt + 1
    assert required_samples(10, 3, Fraction(0)) == 10 - 3 + 1


def test_easy_when_corruption_is_heavy():
    # half the blocks corrupt: one sample already catches with p=1/2.
    assert required_samples(10, 5, Fraction(1, 2)) == 1
    assert miss_probability(10, 5, 1) == Fraction(1, 2)


def test_result_never_exceeds_n():
    for n in (1, 5, 50, 137):
        for corrupt in (1, max(1, n // 3), n):
            k = required_samples(n, corrupt, Fraction(1, 1000))
            assert 0 <= k <= n
            assert miss_probability(n, corrupt, k) <= Fraction(1, 1000)


# ── 3. Validation guards ─────────────────────────────────────────────────────

def test_corrupt_cannot_exceed_n():
    with pytest.raises(ValueError):
        miss_probability(5, 6, 2)
    with pytest.raises(ValueError):
        required_samples(5, 6, Fraction(1, 2))


def test_k_cannot_exceed_n():
    with pytest.raises(ValueError):
        miss_probability(5, 2, 6)


def test_required_samples_needs_a_hypothesised_fraud():
    with pytest.raises(ValueError):
        required_samples(10, 0, Fraction(1, 2))   # corrupt must be >= 1


def test_max_miss_must_be_fraction_in_unit_interval():
    with pytest.raises(TypeError):
        required_samples(10, 1, 0.5)               # float is rejected (must be exact)
    with pytest.raises(ValueError):
        required_samples(10, 1, Fraction(3, 2))    # > 1
    with pytest.raises(ValueError):
        required_samples(10, 1, Fraction(-1, 10))  # < 0


def test_counts_reject_bool_and_negative():
    with pytest.raises(TypeError):
        miss_probability(True, 1, 1)
    with pytest.raises(ValueError):
        miss_probability(0, 0, 0)                  # n must be >= 1
