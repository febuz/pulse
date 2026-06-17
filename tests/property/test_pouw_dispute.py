"""Proofs for the PoUW dispute window (PROOF_OF_USEFUL_WORK.md §4.4).

Escrow must not settle until a dispute window closes, and the release beat must lie
strictly after that window so a paid worker can never withdraw while a detected-
mismatch dispute could still slash them. All beats and amounts are integers.
"""

import pytest

from knitweb.pouw.dispute import (
    DEFAULT_DISPUTE_WINDOW,
    DEFAULT_RELEASE_DELAY,
    DisputeWindowLedger,
)

WORKER = "did:key:worker"
CONSUMER = "did:key:consumer"


def _ledger(dispute_window=5, release_delay=8):
    return DisputeWindowLedger(dispute_window=dispute_window, release_delay=release_delay)


def _submit(led, sid="s1", escrow=10, collateral=20, submit_beat=100):
    return led.submit(sid, WORKER, CONSUMER, escrow, collateral, submit_beat)


# ── 1. Constructor invariant ───────────────────────────────────────────────

@pytest.mark.property
def test_release_delay_must_exceed_dispute_window():
    DisputeWindowLedger(dispute_window=10, release_delay=11)  # ok
    for bad in (10, 9, 1):
        with pytest.raises(ValueError, match="strictly exceed"):
            DisputeWindowLedger(dispute_window=10, release_delay=bad)


@pytest.mark.property
def test_defaults_satisfy_the_invariant():
    assert DEFAULT_RELEASE_DELAY > DEFAULT_DISPUTE_WINDOW
    led = DisputeWindowLedger()
    assert led.release_delay > led.dispute_window


# ── 2. Timing geometry ──────────────────────────────────────────────────

@pytest.mark.property
def test_release_beat_is_strictly_after_dispute_window_closes():
    led = _ledger(dispute_window=5, release_delay=8)
    _submit(led, submit_beat=100)
    assert led.slashable_until("s1") == 105
    assert led.release_beat("s1") == 108
    assert led.release_beat("s1") > led.slashable_until("s1")  # the safety gap


# ── 3. Honest path ────────────────────────────────────────────────────

@pytest.mark.property
def test_clean_release_pays_worker_and_returns_collateral():
    led = _ledger(dispute_window=5, release_delay=8)
    _submit(led, escrow=10, collateral=20, submit_beat=100)
    ok, reason = led.release("s1", beat=108)
    assert ok and reason == "released"
    assert led.get("s1").status == "released"
    assert led.escrow_paid == 10 and led.collateral_returned == 20
    assert led.escrow_refunded == 0 and led.collateral_slashed == 0


@pytest.mark.property
def test_release_before_release_beat_is_refused():
    led = _ledger(dispute_window=5, release_delay=8)
    _submit(led, submit_beat=100)
    for beat in (100, 105, 107):           # window open or gap, never released early
        ok, reason = led.release("s1", beat=beat)
        assert not ok and "locked" in reason
    assert led.get("s1").status == "pending"
    assert led.escrow_paid == 0


# ── 4. Fraud path (detected mismatch) ─────────────────────────────────────

@pytest.mark.property
def test_dispute_within_window_slashes_collateral_and_refunds_escrow():
    led = _ledger(dispute_window=5, release_delay=8)
    _submit(led, escrow=10, collateral=20, submit_beat=100)
    ok, reason = led.dispute("s1", beat=104)      # inside [100, 105]
    assert ok and reason == "slashed"
    sub = led.get("s1")
    assert sub.status == "slashed" and sub.resolved_beat == 104
    assert led.collateral_slashed == 20           # burned
    assert led.escrow_refunded == 10              # back to consumer
    assert led.escrow_paid == 0                   # worker earns nothing


@pytest.mark.property
def test_dispute_at_window_boundary_succeeds():
    led = _ledger(dispute_window=5, release_delay=8)
    _submit(led, submit_beat=100)
    ok, _ = led.dispute("s1", beat=105)           # exactly slashable_until
    assert ok


@pytest.mark.property
def test_dispute_after_window_is_refused():
    led = _ledger(dispute_window=5, release_delay=8)
    _submit(led, submit_beat=100)
    ok, reason = led.dispute("s1", beat=106)      # one beat too late
    assert not ok and "window closed" in reason
    assert led.get("s1").status == "pending"


@pytest.mark.property
def test_dispute_before_submission_is_refused():
    led = _ledger()
    _submit(led, submit_beat=100)
    ok, reason = led.dispute("s1", beat=99)
    assert not ok and "precedes" in reason


# ── 5. No double-resolution / state guards ──────────────────────────────────

@pytest.mark.property
def test_cannot_release_a_slashed_submission():
    led = _ledger(dispute_window=5, release_delay=8)
    _submit(led, submit_beat=100)
    assert led.dispute("s1", beat=102)[0]
    ok, reason = led.release("s1", beat=200)
    assert not ok and "slashed" in reason
    assert led.escrow_paid == 0


@pytest.mark.property
def test_cannot_dispute_a_released_submission():
    led = _ledger(dispute_window=5, release_delay=8)
    _submit(led, submit_beat=100)
    assert led.release("s1", beat=108)[0]
    ok, reason = led.dispute("s1", beat=108)      # window math aside, it's already released
    assert not ok and "released" in reason


@pytest.mark.property
def test_double_release_and_double_dispute_are_refused():
    led = _ledger(dispute_window=5, release_delay=8)
    _submit(led, "a", submit_beat=0)
    assert led.release("a", beat=8)[0]
    assert not led.release("a", beat=9)[0]
    _submit(led, "b", submit_beat=0)
    assert led.dispute("b", beat=1)[0]
    assert not led.dispute("b", beat=2)[0]


# ── 6. Validation ──────────────────────────────────────────────────────

@pytest.mark.property
def test_amounts_and_beats_reject_bool_float_and_negative():
    led = _ledger()
    for bad in (True, 1.5, -1):
        with pytest.raises((TypeError, ValueError)):
            led.submit("x", WORKER, CONSUMER, escrow=bad, collateral=1, submit_beat=0)
    with pytest.raises((TypeError, ValueError)):
        _submit(led, "y")
        led.dispute("y", beat=1.0)  # type: ignore[arg-type]


@pytest.mark.property
def test_duplicate_submission_id_is_rejected():
    led = _ledger()
    _submit(led, "dup")
    with pytest.raises(ValueError, match="duplicate"):
        _submit(led, "dup")


@pytest.mark.property
def test_worker_and_consumer_must_differ():
    led = _ledger()
    with pytest.raises(ValueError, match="differ"):
        led.submit("z", WORKER, WORKER, 10, 20, 0)


# ── 7. Conservation across a batch ───────────────────────────────────────

@pytest.mark.property
def test_every_submission_resolves_to_exactly_one_outcome():
    led = _ledger(dispute_window=5, release_delay=8)
    total_escrow = total_collateral = 0
    for i in range(20):
        escrow, collateral = 10 + i, 20 + i
        total_escrow += escrow
        total_collateral += collateral
        led.submit(f"s{i}", WORKER, CONSUMER, escrow, collateral, submit_beat=0)
        if i % 2 == 0:
            led.dispute(f"s{i}", beat=3)      # half slashed
        else:
            led.release(f"s{i}", beat=8)      # half released
    # escrow ends paid-to-worker or refunded-to-consumer, never both, never lost
    assert led.escrow_paid + led.escrow_refunded == total_escrow
    # collateral ends returned-to-worker or slashed, never both, never lost
    assert led.collateral_returned + led.collateral_slashed == total_collateral
    s = led.stats()
    assert s["slashed"] == 10 and s["released"] == 10 and s["pending"] == 0
