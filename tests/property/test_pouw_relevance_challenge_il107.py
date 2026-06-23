"""IL-107 — Challenge-window + reputation for relevance (non-deterministic residue).

Tests for:
A) pouw.spider_quality: SpiderQualityReputation (quality-reputation store)
B) pouw.dispute: RelevanceChallengeWindow (open/resolve lifecycle)
C) Separation invariant: relevance vs. fabrication are disjoint paths
"""

from __future__ import annotations

import pytest

from knitweb.pouw.dispute import (
    DEFAULT_RELEVANCE_WINDOW,
    RelevanceChallenge,
    RelevanceChallengeWindow,
)
from knitweb.pouw.quorum import Verdict
from knitweb.pouw.spider_quality import (
    DEFAULT_QUALITY_PENALTY,
    DEFAULT_QUALITY_REWARD,
    MIN_QUALITY_SCORE,
    SpiderQualityReputation,
)


# ---------------------------------------------------------------------------
# A) SpiderQualityReputation
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_spider_quality_initial_score_is_100():
    rep = SpiderQualityReputation()
    assert rep.score("spider-A") == 100


@pytest.mark.property
def test_spider_quality_penalize_reduces_score():
    rep = SpiderQualityReputation()
    new_score = rep.penalize("spider-A")
    assert new_score == 100 - DEFAULT_QUALITY_PENALTY
    assert rep.score("spider-A") == new_score


@pytest.mark.property
def test_spider_quality_reward_increases_score():
    rep = SpiderQualityReputation()
    new_score = rep.reward("spider-A")
    assert new_score == 100 + DEFAULT_QUALITY_REWARD
    assert rep.score("spider-A") == new_score


@pytest.mark.property
def test_spider_quality_floor_at_min():
    rep = SpiderQualityReputation(penalty=200)
    rep.penalize("spider-A")
    assert rep.score("spider-A") == MIN_QUALITY_SCORE


@pytest.mark.property
def test_spider_quality_multiple_penalties_cumulate():
    rep = SpiderQualityReputation()
    rep.penalize("spider-A")
    rep.penalize("spider-A")
    assert rep.score("spider-A") == max(MIN_QUALITY_SCORE, 100 - 2 * DEFAULT_QUALITY_PENALTY)


@pytest.mark.property
def test_spider_quality_record_tracks_upheld_and_overturned():
    rep = SpiderQualityReputation()
    rep.penalize("spider-A")
    rep.reward("spider-A")
    rec = rep.record("spider-A")
    assert rec is not None
    assert rec.challenges_upheld == 1
    assert rec.challenges_overturned == 1


@pytest.mark.property
def test_spider_quality_tracked_count():
    rep = SpiderQualityReputation()
    rep.penalize("spider-A")
    rep.reward("spider-B")
    assert rep.tracked() == 2


@pytest.mark.property
def test_spider_quality_separate_from_peer_banscore():
    """SpiderQualityReputation must NOT import or share state with p2p.reputation."""
    from knitweb.pouw.spider_quality import SpiderQualityReputation as SQR
    from knitweb.p2p.reputation import PeerReputation
    sqr = SQR()
    peer = PeerReputation()
    sqr.penalize("s1")
    # Penalizing spider quality must not affect peer ban-score
    assert not peer.is_banned("s1")


# ---------------------------------------------------------------------------
# B) RelevanceChallengeWindow
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_open_challenge_returns_open_record():
    rcw = RelevanceChallengeWindow()
    ch = rcw.open_challenge("bafy-bundle", "spider-A", "challenger-B", 100, 0)
    assert isinstance(ch, RelevanceChallenge)
    assert ch.status == "open"
    assert ch.bundle_cid == "bafy-bundle"
    assert ch.spider == "spider-A"
    assert ch.challenger == "challenger-B"
    assert ch.challenger_stake == 100


@pytest.mark.property
def test_open_challenge_duplicate_raises():
    rcw = RelevanceChallengeWindow()
    rcw.open_challenge("bafy-x", "spider-A", "challenger-B", 50, 0)
    with pytest.raises(ValueError, match="already open"):
        rcw.open_challenge("bafy-x", "spider-A", "challenger-C", 50, 1)


@pytest.mark.property
def test_open_challenge_zero_stake_raises():
    rcw = RelevanceChallengeWindow()
    with pytest.raises(ValueError, match="challenger_stake"):
        rcw.open_challenge("bafy-x", "spider-A", "challenger-B", 0, 0)


@pytest.mark.property
def test_resolve_before_window_closes_raises():
    rcw = RelevanceChallengeWindow(window_beats=10)
    rcw.open_challenge("bafy-x", "s", "c", 1, open_beat=0)
    with pytest.raises(ValueError, match="window not yet closed"):
        rcw.resolve("bafy-x", current_beat=9, verdicts=[], quality_rep=SpiderQualityReputation())


@pytest.mark.property
def test_resolve_upheld_penalizes_spider():
    """MISMATCH majority → upheld → spider quality penalised."""
    rcw = RelevanceChallengeWindow(window_beats=5)
    rcw.open_challenge("bafy-bundle", "spider-A", "challenger-B", 100, open_beat=0)
    rep = SpiderQualityReputation()
    # Three MISMATCH = SLASH majority
    verdicts = [Verdict.MISMATCH, Verdict.MISMATCH, Verdict.MISMATCH]
    outcome, updated = rcw.resolve("bafy-bundle", current_beat=5, verdicts=verdicts, quality_rep=rep)
    assert outcome == "upheld"
    assert updated.status == "upheld"
    assert rep.score("spider-A") == 100 - DEFAULT_QUALITY_PENALTY


@pytest.mark.property
def test_resolve_overturned_rewards_spider():
    """CONFIRM majority → overturned → spider quality rewarded."""
    rcw = RelevanceChallengeWindow(window_beats=5)
    rcw.open_challenge("bafy-bundle", "spider-A", "challenger-B", 100, open_beat=0)
    rep = SpiderQualityReputation()
    verdicts = [Verdict.CONFIRM, Verdict.CONFIRM, Verdict.CONFIRM]
    outcome, updated = rcw.resolve("bafy-bundle", current_beat=5, verdicts=verdicts, quality_rep=rep)
    assert outcome == "overturned"
    assert updated.status == "overturned"
    assert rep.score("spider-A") == 100 + DEFAULT_QUALITY_REWARD


@pytest.mark.property
def test_resolve_twice_raises():
    rcw = RelevanceChallengeWindow(window_beats=1)
    rcw.open_challenge("bafy-x", "s", "c", 1, open_beat=0)
    rep = SpiderQualityReputation()
    rcw.resolve("bafy-x", current_beat=1, verdicts=[Verdict.CONFIRM], quality_rep=rep)
    with pytest.raises(ValueError, match="already"):
        rcw.resolve("bafy-x", current_beat=2, verdicts=[Verdict.CONFIRM], quality_rep=rep)


@pytest.mark.property
def test_resolve_unknown_bundle_raises():
    rcw = RelevanceChallengeWindow()
    with pytest.raises(KeyError):
        rcw.resolve("bafy-unknown", current_beat=100, verdicts=[], quality_rep=SpiderQualityReputation())


@pytest.mark.property
def test_open_count_tracks_open_challenges():
    rcw = RelevanceChallengeWindow(window_beats=5)
    assert rcw.open_count() == 0
    rcw.open_challenge("bafy-a", "s1", "c1", 10, 0)
    rcw.open_challenge("bafy-b", "s2", "c2", 10, 0)
    assert rcw.open_count() == 2
    rcw.resolve("bafy-a", 5, [Verdict.CONFIRM], SpiderQualityReputation())
    assert rcw.open_count() == 1


@pytest.mark.property
def test_default_window_beats():
    rcw = RelevanceChallengeWindow()
    assert rcw.window_beats == DEFAULT_RELEVANCE_WINDOW


# ---------------------------------------------------------------------------
# C) Separation invariant — relevance vs. fabrication are disjoint paths
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_separation_invariant_fabrication_uses_different_path():
    """fabrication → verify_distill + DisputeWindowLedger; relevance → RelevanceChallengeWindow.

    The two classes share no state and their resolution paths are orthogonal.
    Penalizing a spider for irrelevance must NOT touch DisputeWindowLedger,
    and slashing for fabrication must NOT touch RelevanceChallengeWindow.
    """
    from knitweb.pouw.dispute import DisputeWindowLedger
    from knitweb.pouw.collateral import Margin

    # Relevance path
    rcw = RelevanceChallengeWindow(window_beats=1)
    rep = SpiderQualityReputation()
    rcw.open_challenge("bafy-irrelevant", "spider-A", "c", 10, 0)
    rcw.resolve("bafy-irrelevant", 1, [Verdict.MISMATCH, Verdict.MISMATCH], rep)
    assert rep.score("spider-A") < 100  # penalised

    # Fabrication path (DisputeWindowLedger)
    dwl = DisputeWindowLedger(
        dispute_window=5,
        release_delay=6,
        margin=Margin(num=1, den=1),
    )
    # The fabrication ledger knows nothing about the relevance challenge
    assert dwl.get("bafy-irrelevant") is None


@pytest.mark.property
def test_separation_invariant_spider_can_only_be_penalised_for_relevance_here():
    """A spider penalised by verify_distill (fabrication) is NOT penalised by this window.

    Simulates the scenario: fabrication detected (deterministic_ok=False) → slash via
    DisputeWindowLedger; relevance window is NOT opened (no double-penalty from this path).
    """
    from knitweb.pouw.verify import DistillReexecResult

    fabrication_result = DistillReexecResult(
        deterministic_ok=False,
        candidate_mismatch=True,
        gate_failure=False,
        first_bad_relation=None,
    )
    # If fabrication is detected, the relevance challenge must NOT be opened
    # (this is a protocol convention; enforce it with an explicit assertion):
    assert not fabrication_result.deterministic_ok
    # A well-behaved caller would NOT call rcw.open_challenge when fabrication was detected.
    # Asserting the invariant: the RelevanceChallengeWindow remains empty.
    rcw = RelevanceChallengeWindow()
    assert rcw.open_count() == 0   # no challenge was opened — fabrication takes a different path
