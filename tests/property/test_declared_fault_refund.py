"""Proofs for the declared-fault refund — the third settlement outcome.

quorum produces three verdicts: DETECTED_FAULT (slash), CONFIRMED (release), and DECLARED_FAULT
(an honest self-report → refund, no slash). The ledger had settlements for the first two only;
refund_declared_fault closes the gap: consumer made whole, worker's stake returned unslashed.
"""

import pytest

from knitweb.pouw.dispute import DisputeWindowLedger
from knitweb.pouw.quorum import Verdict

M = Verdict.MISMATCH
W, CON = "did:key:worker", "did:key:consumer"


def _led():
    return DisputeWindowLedger(dispute_window=5, release_delay=8)


def _submit(led, sid="s1", escrow=100, collateral=100, beat=10):
    return led.submit(sid, W, CON, escrow=escrow, collateral=collateral, submit_beat=beat)


# ── 1. Refund mechanics: consumer whole, stake returned, no slash ────────────

def test_refund_returns_escrow_and_collateral_without_slashing():
    led = _led()
    _submit(led, escrow=100, collateral=100)
    ok, reason = led.refund_declared_fault("s1", beat=12)
    assert ok and reason == "refunded"
    sub = led.get("s1")
    assert sub.status == "refunded" and sub.resolved_beat == 12
    assert led.escrow_refunded == 100            # consumer made whole
    assert led.collateral_returned == 100        # stake returned…
    assert led.collateral_slashed == 0           # …NOT slashed
    assert led.escrow_paid == 0                  # worker paid nothing


# ── 2. Outcome-space completeness + conservation across all three ────────────

def test_three_terminal_outcomes_are_distinct_and_conserving():
    led = _led()
    # one slashed, one released, one refunded — each fully resolved
    led.submit("slash", W, CON, escrow=10, collateral=10, submit_beat=10)
    led.submit("rel", W, CON, escrow=20, collateral=20, submit_beat=10)
    led.submit("ref", W, CON, escrow=30, collateral=30, submit_beat=10)
    assert led.dispute("slash", beat=12)[0]
    assert led.release("rel", beat=10 + 8)[0]
    assert led.refund_declared_fault("ref", beat=12)[0]

    st = led.stats()
    assert (st["slashed"], st["released"], st["refunded"], st["pending"]) == (1, 1, 1, 0)
    # every escrow is accounted for exactly once: paid(20) + refunded(10 slash + 30 declared) == 60
    assert st["escrow_paid"] == 20
    assert st["escrow_refunded"] == 10 + 30
    # every collateral accounted once: returned(20 release + 30 refund) + slashed(10) == 60
    assert st["collateral_returned"] == 20 + 30
    assert st["collateral_slashed"] == 10


# ── 3. Pairs with a DECLARED_FAULT quorum verdict (the caller flow) ──────────

def test_declared_fault_quorum_then_refund():
    led = _led()
    _submit(led)
    # quorum says declared fault → dispute_by_quorum does NOT slash (just signals)…
    ok, reason = led.dispute_by_quorum("s1", [M, M, M], beat=12, worker_declared_fault=True)
    assert not ok and "declared_fault" in reason
    assert led.get("s1").status == "pending" and led.collateral_slashed == 0
    # …the caller then settles the declared fault as a refund.
    ok2, _ = led.refund_declared_fault("s1", beat=12)
    assert ok2 and led.get("s1").status == "refunded"


# ── 4. Guards & terminal-state safety ────────────────────────────────────────

def test_cannot_refund_unknown_or_already_resolved():
    led = _led()
    assert led.refund_declared_fault("nope", beat=12) == (False, "unknown submission")
    _submit(led)
    led.dispute("s1", beat=12)                    # now slashed
    ok, reason = led.refund_declared_fault("s1", beat=13)
    assert not ok and reason == "already slashed"  # no double-settlement


def test_refund_then_release_or_dispute_is_blocked():
    led = _led()
    _submit(led)
    led.refund_declared_fault("s1", beat=11)
    assert led.release("s1", beat=20) == (False, "already refunded")
    assert led.dispute("s1", beat=12) == (False, "already refunded")


def test_refund_before_submission_rejected():
    led = _led()
    _submit(led, beat=10)
    ok, reason = led.refund_declared_fault("s1", beat=9)
    assert not ok and "precedes submission" in reason
