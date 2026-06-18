"""Integration proofs: collateral sizing (pouw/collateral.py) enforced by the dispute ledger.

#32's dispute ledger tracks a per-submission ``collateral`` field but never checked it was
adequate; #51 defines the invariant (stake must cover the cumulative escrow a worker could
collect-then-flee). This wires the two together via the opt-in ``enforce_collateral`` flag —
default off, so existing dispute-window behaviour is unchanged.
"""

import pytest

from knitweb.pouw.collateral import Margin
from knitweb.pouw.dispute import DisputeWindowLedger, UnderCollateralizedError

W = "did:key:worker"
C = "did:key:consumer"


def _led(enforce=True, margin=None):
    return DisputeWindowLedger(
        dispute_window=5, release_delay=8, enforce_collateral=enforce, margin=margin
    )


# ── 1. Default is unchanged (back-compat) ─────────────────────────────────────

def test_enforcement_off_by_default():
    led = DisputeWindowLedger(dispute_window=5, release_delay=8)
    assert led.enforce_collateral is False
    # an absurdly under-collateralized submission is accepted when enforcement is off
    sub = led.submit("s1", W, C, escrow=1000, collateral=0, submit_beat=10)
    assert sub.escrow == 1000 and sub.collateral == 0


# ── 2. Single-submission sufficiency (margin 1:1) ────────────────────────────

def test_sufficient_collateral_accepted():
    led = _led()
    sub = led.submit("s1", W, C, escrow=100, collateral=100, submit_beat=10)  # 100 >= 100
    assert sub.sid == "s1"


def test_under_collateralized_rejected():
    led = _led()
    with pytest.raises(UnderCollateralizedError):
        led.submit("s1", W, C, escrow=100, collateral=99, submit_beat=10)     # 99 < 100
    assert led.get("s1") is None                                              # not recorded


# ── 3. Cumulative across a worker's open windows (the #51 point) ─────────────

def test_cumulative_risk_is_enforced():
    led = _led()
    # First job: stake exactly covers it.
    led.submit("s1", W, C, escrow=100, collateral=100, submit_beat=10)
    # Second job from the SAME worker: its own collateral covers its own escrow, but the
    # worker could flee with BOTH escrows (200) while having staked only 100+100=200 — ok here.
    led.submit("s2", W, C, escrow=100, collateral=100, submit_beat=11)        # cumulative 200<=200
    # Third job under-stakes the cumulative risk: total escrow 250 > total stake 200+40=240.
    with pytest.raises(UnderCollateralizedError):
        led.submit("s3", W, C, escrow=50, collateral=40, submit_beat=12)      # 240 < 250


def test_resolved_submissions_drop_out_of_the_risk_pool():
    led = _led()
    led.submit("s1", W, C, escrow=100, collateral=100, submit_beat=10)
    # Slash s1 (resolves it) — it no longer counts toward the worker's open risk.
    ok, _ = led.dispute("s1", beat=12)
    assert ok
    # Now a fresh job only needs to cover itself, not the resolved s1.
    sub = led.submit("s2", W, C, escrow=100, collateral=100, submit_beat=20)
    assert sub.sid == "s2"


def test_different_workers_have_independent_risk_pools():
    led = _led()
    led.submit("s1", W, C, escrow=100, collateral=100, submit_beat=10)
    # A different worker's risk is its own — unaffected by W's pending stake.
    sub = led.submit("s2", "did:key:worker2", C, escrow=80, collateral=80, submit_beat=10)
    assert sub.sid == "s2"


# ── 4. Margin > 1 (over-collateralization) ───────────────────────────────────

def test_margin_requires_overcollateralization():
    led = _led(margin=Margin(3, 2))                 # 1.5x
    with pytest.raises(UnderCollateralizedError):
        led.submit("s1", W, C, escrow=100, collateral=140, submit_beat=10)    # need ceil(150)
    led.submit("s1", W, C, escrow=100, collateral=150, submit_beat=10)        # exactly 150 ok


# ── 5. Constructor validation ────────────────────────────────────────────────

def test_constructor_validates_flags():
    with pytest.raises(TypeError):
        DisputeWindowLedger(enforce_collateral="yes")
    with pytest.raises(TypeError):
        DisputeWindowLedger(enforce_collateral=True, margin=(3, 2))           # not a Margin
