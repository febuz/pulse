"""Integration proofs: quorum verdicts drive the dispute (pouw/quorum.py → pouw/dispute.py).

A single verifier should not be able to slash honest work. dispute_by_quorum aggregates a
committee's verdicts and slashes only on a genuine DETECTED_FAULT (a mismatch quorum), reusing the
dispute window's timing + slashing. CONFIRMED / INCONCLUSIVE / DECLARED_FAULT never slash.
"""

import pytest

from knitweb.pouw.dispute import DisputeWindowLedger
from knitweb.pouw.quorum import Verdict

C, M, A = Verdict.CONFIRM, Verdict.MISMATCH, Verdict.ABSTAIN
W, CON = "did:key:worker", "did:key:consumer"


def _led():
    return DisputeWindowLedger(dispute_window=5, release_delay=8)


def _submit(led, sid="s1", beat=10):
    return led.submit(sid, W, CON, escrow=100, collateral=100, submit_beat=beat)


# ── 1. A mismatch quorum slashes ─────────────────────────────────────────────

def test_mismatch_quorum_slashes():
    led = _led()
    _submit(led)
    ok, reason = led.dispute_by_quorum("s1", [M, M, M, C], beat=12)   # n=4,k=3: 3 mismatches
    assert ok and "detected fault" in reason
    assert led.get("s1").status == "slashed"
    assert led.collateral_slashed == 100 and led.escrow_refunded == 100


# ── 2. Non-fault quorums never slash ─────────────────────────────────────────

def test_confirm_quorum_does_not_slash():
    led = _led()
    _submit(led)
    ok, reason = led.dispute_by_quorum("s1", [C, C, C, M], beat=12)
    assert not ok and "confirmed" in reason
    assert led.get("s1").status == "pending"          # honest work survives
    assert led.collateral_slashed == 0


def test_inconclusive_quorum_does_not_slash():
    led = _led()
    _submit(led)
    ok, reason = led.dispute_by_quorum("s1", [C, C, M, M], beat=12)   # 2/2, no quorum
    assert not ok and "inconclusive" in reason
    assert led.get("s1").status == "pending"


def test_declared_fault_does_not_slash():
    led = _led()
    _submit(led)
    ok, reason = led.dispute_by_quorum("s1", [M, M, M], beat=12, worker_declared_fault=True)
    assert not ok and "declared_fault" in reason
    assert led.get("s1").status == "pending"          # owned-up fault is not slashed here
    assert led.collateral_slashed == 0


# ── 3. A lone malicious verifier cannot slash honest work ────────────────────

def test_single_mismatch_among_confirms_cannot_slash():
    led = _led()
    _submit(led)
    ok, _ = led.dispute_by_quorum("s1", [M, C, C, C, C], beat=12)     # 1 mismatch, 4 confirm
    assert not ok
    assert led.get("s1").status == "pending"


# ── 4. Quorum slashing still respects the dispute-window timing ──────────────

def test_quorum_slash_after_window_closes_is_rejected():
    led = _led()                                       # window = 5
    _submit(led, beat=10)
    ok, reason = led.dispute_by_quorum("s1", [M, M, M], beat=20)      # 20 > 10+5
    assert not ok and "window closed" in reason
    assert led.get("s1").status == "pending"


def test_custom_threshold_unanimity_blocks_a_split():
    led = _led()
    _submit(led)
    # require all 5 to agree; 4 mismatches is not enough → no slash
    ok, _ = led.dispute_by_quorum("s1", [M, M, M, M, C], beat=12, threshold=5)
    assert not ok
    assert led.get("s1").status == "pending"
