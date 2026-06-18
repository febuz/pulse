"""Proofs for PoUW collateral sizing (PROOF_OF_USEFUL_WORK.md §4.4, backlog B6).

A stake deters fraud only if it covers the *cumulative* escrow a worker could collect-then-
flee within its open dispute windows. The invariant: ``collateral >= payout_at_risk``
(margin 1:1) ⇒ a detected fraud loses at least what it could gain ⇒ fraud is never
net-positive. All amounts are integer PLS-wei; the margin is an exact integer ratio.
"""

import pytest

from knitweb.pouw.collateral import (
    Margin,
    fraud_is_profitable,
    is_sufficiently_collateralized,
    max_backed_payout,
    payout_at_risk,
    required_collateral,
)


# ── 1. Payout-at-risk is the cumulative pending escrow ───────────────────────

def test_payout_at_risk_sums_pending_escrows():
    assert payout_at_risk([10, 20, 30]) == 60
    assert payout_at_risk([]) == 0
    assert payout_at_risk([0, 0]) == 0


def test_payout_at_risk_rejects_negative_and_bool():
    with pytest.raises(ValueError):
        payout_at_risk([10, -1])
    with pytest.raises(TypeError):
        payout_at_risk([10, True])      # bool must not pose as an amount


# ── 2. Required collateral at the 1:1 minimum margin ─────────────────────────

def test_required_collateral_unit_margin_equals_payout():
    for payout in (0, 1, 7, 1000, 10**18):
        assert required_collateral(payout) == payout


def test_sufficient_iff_collateral_covers_payout():
    assert is_sufficiently_collateralized(100, 100)      # exactly enough
    assert is_sufficiently_collateralized(101, 100)
    assert not is_sufficiently_collateralized(99, 100)   # one wei short → insufficient


# ── 3. The core economic invariant: fraud is never net-positive ──────────────

def test_sufficient_collateral_makes_fraud_non_profitable():
    # collateral >= payout_at_risk  ⇒  slash(>=) covers gain  ⇒  net <= 0.
    for payout in range(0, 200, 7):
        col = required_collateral(payout)            # exactly the minimum
        assert is_sufficiently_collateralized(col, payout)
        assert not fraud_is_profitable(col, payout)
        # net from a detected fraud = escrow_gained - collateral_slashed <= 0
        assert payout - col <= 0


def test_under_collateralization_is_flagged_profitable():
    assert fraud_is_profitable(99, 100)
    assert not is_sufficiently_collateralized(99, 100)


# ── 4. Safety margin > 1 (over-collateralization), exact integer rounding ────

def test_margin_rounds_required_collateral_up():
    m = Margin(3, 2)                                  # 1.5×
    assert required_collateral(100, m) == 150
    assert required_collateral(101, m) == 152        # ⌈101*3/2⌉ = ⌈151.5⌉ = 152
    assert required_collateral(0, m) == 0


def test_margin_must_be_at_least_one():
    with pytest.raises(ValueError):
        Margin(1, 2)                                 # 0.5× would make fraud profitable
    with pytest.raises(ValueError):
        Margin(2, 3)
    # exactly 1 (in any reduced form) is allowed:
    assert required_collateral(50, Margin(2, 2)) == 50


def test_margin_components_reject_nonpositive_and_bool():
    with pytest.raises(ValueError):
        Margin(0, 1)
    with pytest.raises(ValueError):
        Margin(1, 0)
    with pytest.raises(TypeError):
        Margin(True, 1)


# ── 5. max_backed_payout is the inverse and is always actually covered ───────

def test_max_backed_payout_is_covered_by_the_stake():
    m = Margin(3, 2)
    for collateral in range(0, 500, 13):
        cap = max_backed_payout(collateral, m)
        # the bound it reports must itself be sufficiently collateralized…
        assert is_sufficiently_collateralized(collateral, cap, m)
        # …and one wei more must NOT be (it's a tight bound)
        assert not is_sufficiently_collateralized(collateral, cap + 1, m)


def test_max_backed_payout_unit_margin_equals_collateral():
    assert max_backed_payout(100) == 100
    assert max_backed_payout(0) == 0


# ── 6. Validation guards ─────────────────────────────────────────────────────

def test_negative_amounts_rejected():
    with pytest.raises(ValueError):
        required_collateral(-1)
    with pytest.raises(ValueError):
        is_sufficiently_collateralized(-1, 10)
    with pytest.raises(ValueError):
        max_backed_payout(-5)


def test_required_collateral_rejects_non_margin():
    with pytest.raises(TypeError):
        required_collateral(10, margin=(1, 1))       # raw tuple is not a Margin


def test_margin_is_immutable():
    m = Margin(1, 1)
    with pytest.raises(Exception):
        m.num = 2                                    # frozen dataclass
