"""Tests for knitweb/risk.py — risk-knot staking on the PLS layer."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import datetime
import pytest

from knitweb.pulse import PulseLedger, VALIDATORS_REQUIRED
from knitweb.risk import (
    RiskKnotLedger,
    LOCK_LEVELS,
    RISK_VOTE_THRESHOLD,
    RISK_CONSENSUS_FRACTION,
    BURN_FRACTION,
)

KNOT_ADDR = "k" * 64
OPENER    = "did:key:opener"
SPIDER_A  = "did:key:spiderA"
SPIDER_B  = "did:key:spiderB"
SPIDER_C  = "did:key:spiderC"
SPIDER_D  = "did:key:spiderD"
SPIDER_E  = "did:key:spiderE"


def make_ledger(funded: dict | None = None) -> tuple[PulseLedger, RiskKnotLedger]:
    ledger = PulseLedger()
    if funded:
        for did, amount in funded.items():
            ledger.wallet(did).earn(amount)
    return ledger, RiskKnotLedger(ledger)


def open_risk(rkl: RiskKnotLedger, opener=OPENER, level=1) -> str:
    ok, result = rkl.open(KNOT_ADDR, "Is this correct?", opener, level)
    assert ok, result
    return result


def drive_to_resolution(rkl: RiskKnotLedger, risk_id: str, outcome: str) -> None:
    voters = [SPIDER_A, SPIDER_B, SPIDER_C, SPIDER_D, SPIDER_E]
    for v in voters:
        rkl.vote(risk_id, v, outcome)


class TestOpen:
    def test_open_locks_stake(self):
        ledger, rkl = make_ledger({OPENER: 100})
        rid = open_risk(rkl)
        w = ledger.wallet(OPENER)
        assert w.balance == 100 - LOCK_LEVELS[1]

    def test_open_fails_insufficient_balance(self):
        ledger, rkl = make_ledger({OPENER: 1})
        ok, reason = rkl.open(KNOT_ADDR, "test?", OPENER, 1)
        assert not ok
        assert "insufficient" in reason

    def test_open_fails_blank_question(self):
        ledger, rkl = make_ledger({OPENER: 100})
        ok, reason = rkl.open(KNOT_ADDR, "   ", OPENER, 1)
        assert not ok

    def test_open_fails_invalid_level(self):
        ledger, rkl = make_ledger({OPENER: 100})
        ok, reason = rkl.open(KNOT_ADDR, "test?", OPENER, 99)
        assert not ok

    def test_opener_auto_staked_yes(self):
        ledger, rkl = make_ledger({OPENER: 100})
        rid = open_risk(rkl)
        rk = rkl.get(rid)
        assert rk.yes_pool == LOCK_LEVELS[1]
        assert rk.no_pool == 0
        assert rk.stakes[0].position == "yes"


class TestStake:
    def test_stake_no_adds_to_no_pool(self):
        ledger, rkl = make_ledger({OPENER: 100, SPIDER_A: 100})
        rid = open_risk(rkl)
        ok, _ = rkl.stake(rid, SPIDER_A, "no", 1)
        assert ok
        assert rkl.get(rid).no_pool == LOCK_LEVELS[1]

    def test_stake_deducts_from_balance(self):
        ledger, rkl = make_ledger({OPENER: 100, SPIDER_A: 100})
        rid = open_risk(rkl)
        rkl.stake(rid, SPIDER_A, "no", 1)
        assert ledger.wallet(SPIDER_A).balance == 100 - LOCK_LEVELS[1]

    def test_stake_fails_insufficient(self):
        ledger, rkl = make_ledger({OPENER: 100, SPIDER_A: 1})
        rid = open_risk(rkl)
        ok, reason = rkl.stake(rid, SPIDER_A, "yes", 1)
        assert not ok
        assert "insufficient" in reason

    def test_stake_rejected_on_resolved_knot(self):
        ledger, rkl = make_ledger({OPENER: 100, SPIDER_A: 100})
        rid = open_risk(rkl)
        drive_to_resolution(rkl, rid, "yes")
        ok, reason = rkl.stake(rid, SPIDER_A, "no", 1)
        assert not ok
        assert "resolved" in reason


class TestVote:
    def test_vote_recorded(self):
        ledger, rkl = make_ledger({OPENER: 100})
        rid = open_risk(rkl)
        ok, event = rkl.vote(rid, SPIDER_A, "yes")
        assert ok
        assert event == "voted"
        assert len(rkl.get(rid).votes) == 1

    def test_duplicate_vote_rejected(self):
        ledger, rkl = make_ledger({OPENER: 100})
        rid = open_risk(rkl)
        rkl.vote(rid, SPIDER_A, "yes")
        ok, reason = rkl.vote(rid, SPIDER_A, "no")
        assert not ok
        assert "already" in reason

    def test_resolves_at_threshold_with_consensus(self):
        ledger, rkl = make_ledger({OPENER: 100})
        rid = open_risk(rkl)
        drive_to_resolution(rkl, rid, "yes")
        rk = rkl.get(rid)
        assert rk.status == "resolved"
        assert rk.outcome == "yes"

    def test_no_resolution_below_threshold(self):
        ledger, rkl = make_ledger({OPENER: 100})
        rid = open_risk(rkl)
        rkl.vote(rid, SPIDER_A, "yes")
        rkl.vote(rid, SPIDER_B, "yes")
        assert rkl.get(rid).status == "open"

    def test_no_resolution_without_consensus(self):
        ledger, rkl = make_ledger({OPENER: 100})
        rid = open_risk(rkl)
        rkl.vote(rid, SPIDER_A, "yes")
        rkl.vote(rid, SPIDER_B, "yes")
        rkl.vote(rid, SPIDER_C, "yes")
        rkl.vote(rid, SPIDER_D, "no")
        rkl.vote(rid, SPIDER_E, "no")
        assert rkl.get(rid).status == "open"


class TestResolution:
    def test_correct_staker_gets_stake_back(self):
        ledger, rkl = make_ledger({OPENER: 100})
        rid = open_risk(rkl)
        drive_to_resolution(rkl, rid, "yes")
        assert ledger.wallet(OPENER).balance >= 100

    def test_wrong_staker_loses_stake(self):
        ledger, rkl = make_ledger({OPENER: 100, SPIDER_A: 100})
        rid = open_risk(rkl)
        rkl.stake(rid, SPIDER_A, "no", 1)
        drive_to_resolution(rkl, rid, "yes")
        w = ledger.wallet(SPIDER_A)
        assert w.burned_total == LOCK_LEVELS[1]
        assert w.balance == 100 - LOCK_LEVELS[1]

    def test_multiplier_gte_1(self):
        ledger, rkl = make_ledger({OPENER: 100})
        rid = open_risk(rkl)
        drive_to_resolution(rkl, rid, "yes")
        assert rkl.get(rid).multiplier >= 1.0

    def test_multiplier_equals_votes_over_required(self):
        ledger, rkl = make_ledger({OPENER: 100})
        rid = open_risk(rkl)
        drive_to_resolution(rkl, rid, "yes")
        expected = RISK_VOTE_THRESHOLD / VALIDATORS_REQUIRED
        assert abs(rkl.get(rid).multiplier - expected) < 0.01

    def test_two_knots_independent(self):
        ledger, rkl = make_ledger({OPENER: 200, SPIDER_A: 100})
        rid1 = rkl.open("a" * 64, "Question 1?", OPENER, 1)[1]
        rid2 = rkl.open("b" * 64, "Question 2?", OPENER, 1)[1]
        drive_to_resolution(rkl, rid1, "yes")
        drive_to_resolution(rkl, rid2, "no")
        assert rkl.get(rid1).outcome == "yes"
        assert rkl.get(rid2).outcome == "no"

    def test_resolved_rejects_further_votes(self):
        ledger, rkl = make_ledger({OPENER: 100})
        rid = open_risk(rkl)
        drive_to_resolution(rkl, rid, "yes")
        ok, reason = rkl.vote(rid, "did:key:late", "yes")
        assert not ok
        assert "resolved" in reason


class TestStats:
    def test_locked_by_tracks_open_stakes(self):
        ledger, rkl = make_ledger({OPENER: 100})
        open_risk(rkl)
        assert rkl.locked_by(OPENER) == LOCK_LEVELS[1]

    def test_locked_by_zero_after_resolution(self):
        ledger, rkl = make_ledger({OPENER: 100})
        rid = open_risk(rkl)
        drive_to_resolution(rkl, rid, "yes")
        assert rkl.locked_by(OPENER) == 0

    def test_stats_structure(self):
        ledger, rkl = make_ledger({OPENER: 100})
        open_risk(rkl)
        s = rkl.stats()
        assert s["open"] == 1
        assert s["resolved"] == 0
        assert s["total_staked_micro_pls"] == LOCK_LEVELS[1]


class TestConstants:
    def test_lock_levels_ascending(self):
        assert LOCK_LEVELS[1] < LOCK_LEVELS[2] < LOCK_LEVELS[3]

    def test_burn_fraction_between_0_and_1(self):
        assert 0 < BURN_FRACTION < 1

    def test_consensus_fraction_above_half(self):
        assert RISK_CONSENSUS_FRACTION > 0.5
