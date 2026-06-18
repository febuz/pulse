"""Proofs for the k-of-n verifier quorum (PROOF_OF_USEFUL_WORK.md §4.4).

A quorum aggregates many independent challenge verdicts so that no minority of corrupt
verifiers can force a false confirm or a false slash, and so an honestly self-declared
fault is never slashed. The sound default threshold is the BFT supermajority
``k = ⌊2n/3⌋ + 1``, tolerant to ``f = ⌊(n-1)/3⌋`` adversaries. Everything is integer
counting — no floats, no crypto-path or signed-record changes.
"""

import pytest

from knitweb.pouw.quorum import (
    Outcome,
    QuorumResult,
    Verdict,
    default_threshold,
    max_faulty,
    tally,
)

C = Verdict.CONFIRM
M = Verdict.MISMATCH
A = Verdict.ABSTAIN


# ── 1. Default threshold values (BFT supermajority ⌊2n/3⌋+1) ─────────────────

def test_default_threshold_known_values():
    assert default_threshold(1) == 1
    assert default_threshold(2) == 2
    assert default_threshold(3) == 3
    assert default_threshold(4) == 3
    assert default_threshold(5) == 4
    assert default_threshold(7) == 5
    assert default_threshold(10) == 7


def test_max_faulty_known_values():
    # n ≥ 3f+1  →  f = ⌊(n-1)/3⌋
    assert max_faulty(1) == 0
    assert max_faulty(4) == 1
    assert max_faulty(7) == 2
    assert max_faulty(10) == 3


# ── 2. Confirm / detect / inconclusive on the default quorum ─────────────────

def test_confirmed_when_confirms_reach_quorum():
    # n=4, k=3
    r = tally([C, C, C, M])
    assert r.outcome is Outcome.CONFIRMED
    assert r.releases and not r.slashes and not r.refunds
    assert (r.confirms, r.mismatches, r.abstains, r.n, r.threshold) == (3, 1, 0, 4, 3)


def test_detected_fault_when_mismatches_reach_quorum():
    r = tally([M, M, M, C])           # n=4, k=3
    assert r.outcome is Outcome.DETECTED_FAULT
    assert r.slashes and not r.releases


def test_inconclusive_when_neither_quorum():
    r = tally([C, C, M, M])           # n=4, k=3: 2 vs 2, neither reaches 3
    assert r.outcome is Outcome.INCONCLUSIVE
    assert not r.releases and not r.slashes and not r.refunds


def test_abstentions_count_toward_neither_quorum():
    r = tally([C, C, A, A])           # n=4, k=3: only 2 confirms
    assert r.outcome is Outcome.INCONCLUSIVE
    assert r.abstains == 2


# ── 3. Declared-vs-detected fault asymmetry ──────────────────────────────────

def test_declared_fault_refunds_without_slash_even_with_mismatches():
    # Worker owned up to the fault: never slashed, even though verifiers also see mismatches.
    r = tally([M, M, M, M], worker_declared_fault=True)
    assert r.outcome is Outcome.DECLARED_FAULT
    assert r.refunds and not r.slashes


def test_declared_fault_overrides_a_confirm_quorum_too():
    r = tally([C, C, C, C], worker_declared_fault=True)
    assert r.outcome is Outcome.DECLARED_FAULT
    assert not r.releases


def test_undeclared_mismatch_quorum_slashes():
    r = tally([M, M, M], worker_declared_fault=False)   # n=3, k=3
    assert r.outcome is Outcome.DETECTED_FAULT
    assert r.slashes


# ── 4. The core safety properties, proven across a range of n ────────────────

def test_confirm_and_mismatch_quorums_are_mutually_exclusive():
    # 2k > n for every n, so a result can never both release and slash.
    for n in range(1, 50):
        k = default_threshold(n)
        assert 2 * k > n
        # An adversary cannot simultaneously satisfy confirms>=k and mismatches>=k
        # because that needs >= 2k > n verdicts.
        for confirms in range(0, n + 1):
            mismatches = n - confirms
            assert not (confirms >= k and mismatches >= k)


def test_adversary_minority_cannot_force_either_verdict():
    # f adversaries voting in unison cannot reach the quorum alone.
    for n in range(1, 50):
        f = max_faulty(n)
        k = default_threshold(n)
        assert f < k                                  # adversaries alone < quorum
        # f all-CONFIRM (fake a pass) — with the rest abstaining, no confirm quorum:
        votes = [C] * f + [A] * (n - f)
        assert tally(votes).outcome is not Outcome.CONFIRMED
        # f all-MISMATCH (frame an honest worker) — no slash quorum:
        votes = [M] * f + [A] * (n - f)
        assert tally(votes).outcome is not Outcome.DETECTED_FAULT


def test_honest_supermajority_always_reaches_the_true_verdict():
    # The n-f honest verifiers can always reach quorum for whichever side is true,
    # even with all f adversaries voting the opposite way.
    for n in range(1, 50):
        f = max_faulty(n)
        honest = n - f
        k = default_threshold(n)
        assert honest >= k
        # true = honest, adversaries lie the other way:
        assert tally([C] * honest + [M] * f).outcome is Outcome.CONFIRMED
        assert tally([M] * honest + [C] * f).outcome is Outcome.DETECTED_FAULT


# ── 5. Custom threshold + validation ─────────────────────────────────────────

def test_custom_threshold_unanimity():
    r = tally([C, C, C, C, C], threshold=5)
    assert r.outcome is Outcome.CONFIRMED
    r2 = tally([C, C, C, C, M], threshold=5)
    assert r2.outcome is Outcome.INCONCLUSIVE   # one dissenter blocks unanimity


def test_threshold_below_strict_majority_is_rejected():
    with pytest.raises(ValueError):
        tally([C, C, M, M], threshold=2)        # 2k <= n → ambiguous, refused


def test_threshold_exceeding_n_is_rejected():
    with pytest.raises(ValueError):
        tally([C, C, C], threshold=4)


def test_empty_verdicts_rejected():
    with pytest.raises(ValueError):
        tally([])


def test_non_verdict_member_rejected():
    with pytest.raises(TypeError):
        tally([C, "confirm", C])                # raw strings are not Verdicts


def test_bool_is_not_a_valid_count_for_threshold_helpers():
    with pytest.raises(TypeError):
        default_threshold(True)                 # bool must not pose as int n


def test_declared_flag_must_be_bool():
    with pytest.raises(TypeError):
        tally([C, C, C], worker_declared_fault=1)


def test_result_is_immutable():
    r = tally([C, C, C])
    with pytest.raises(Exception):
        r.outcome = Outcome.INCONCLUSIVE        # frozen dataclass
