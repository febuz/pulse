"""Proofs for ledger-wired settlement execution: real PLS escrow->payee transfers, conserved."""

import pytest

from knitweb.core import crypto
from knitweb.knitwebs.crowdfunding import (
    PLEDGE_KIND,
    Campaign,
    CrowdfundingCampaign,
    EscrowError,
    execute_settlement,
)
from knitweb.ledger.node import AccountNode

SCOPE = "campaign-X"


def _nf(name: str) -> str:
    return crypto.sha256(name.encode()).hex()


def _pledge(actor_addr: str, nf: str, amount: int, pledged_at: int = 5) -> dict:
    return {
        "kind": PLEDGE_KIND, "scope": SCOPE, "amount": amount, "actor": actor_addr,
        "scope_nullifier": nf, "pledged_at": pledged_at,
    }


def _authority():
    priv, _ = crypto.generate_keypair()
    return CrowdfundingCampaign(priv, SCOPE)


@pytest.mark.property
def test_release_moves_escrow_to_beneficiary():
    escrow, beneficiary = AccountNode(), AccountNode()
    p0 = AccountNode(genesis_balances={"PLS": 1000})
    p1 = AccountNode(genesis_balances={"PLS": 1000})
    p0.transfer_to(escrow, "PLS", 300, timestamp=1)   # pledge-time escrow funding
    p1.transfer_to(escrow, "PLS", 400, timestamp=2)
    assert escrow.balance("PLS") == 700

    authority = _authority()
    campaign = authority.define(Campaign(scope=SCOPE, goal=500, opens_at=0, closes_at=10,
                                         beneficiary=beneficiary.address))
    pledges = [_pledge(p0.address, _nf("p0"), 300), _pledge(p1.address, _nf("p1"), 400)]
    outcome = authority.certify_outcome(campaign.record, pledges)
    assert outcome.record["goal_met"] is True
    settlement = authority.settle(outcome.record, campaign.record, pledges)

    knits = execute_settlement(settlement, outcome.record, campaign.record, pledges,
                               escrow, {beneficiary.address: beneficiary}, timestamp=100)
    assert len(knits) == 2
    assert beneficiary.balance("PLS") == 700
    assert escrow.balance("PLS") == 0


@pytest.mark.property
def test_refund_returns_to_pledgers_when_goal_missed():
    escrow, beneficiary = AccountNode(), AccountNode()
    p0 = AccountNode(genesis_balances={"PLS": 1000})
    p1 = AccountNode(genesis_balances={"PLS": 1000})
    p0.transfer_to(escrow, "PLS", 300, timestamp=1)
    p1.transfer_to(escrow, "PLS", 200, timestamp=2)

    authority = _authority()
    campaign = authority.define(Campaign(scope=SCOPE, goal=5000, opens_at=0, closes_at=10,
                                         beneficiary=beneficiary.address))
    pledges = [_pledge(p0.address, _nf("p0"), 300), _pledge(p1.address, _nf("p1"), 200)]
    outcome = authority.certify_outcome(campaign.record, pledges)
    assert outcome.record["goal_met"] is False
    settlement = authority.settle(outcome.record, campaign.record, pledges)

    execute_settlement(settlement, outcome.record, campaign.record, pledges,
                       escrow, {p0.address: p0, p1.address: p1}, timestamp=100)
    assert escrow.balance("PLS") == 0
    assert p0.balance("PLS") == 1000   # 1000 - 300 pledged + 300 refunded
    assert p1.balance("PLS") == 1000


@pytest.mark.property
def test_underfunded_escrow_raises():
    escrow = AccountNode(genesis_balances={"PLS": 100})
    beneficiary = AccountNode()
    authority = _authority()
    campaign = authority.define(Campaign(scope=SCOPE, goal=200, opens_at=0, closes_at=10,
                                         beneficiary=beneficiary.address))
    pledges = [_pledge(AccountNode().address, _nf("p0"), 300)]  # goal met, needs 300
    outcome = authority.certify_outcome(campaign.record, pledges)
    settlement = authority.settle(outcome.record, campaign.record, pledges)
    with pytest.raises(EscrowError):
        execute_settlement(settlement, outcome.record, campaign.record, pledges,
                           escrow, {beneficiary.address: beneficiary}, timestamp=1)


@pytest.mark.property
def test_missing_payee_account_raises():
    escrow = AccountNode(genesis_balances={"PLS": 1000})
    beneficiary = AccountNode()
    authority = _authority()
    campaign = authority.define(Campaign(scope=SCOPE, goal=100, opens_at=0, closes_at=10,
                                         beneficiary=beneficiary.address))
    pledges = [_pledge(AccountNode().address, _nf("p0"), 300)]
    outcome = authority.certify_outcome(campaign.record, pledges)
    settlement = authority.settle(outcome.record, campaign.record, pledges)
    with pytest.raises(EscrowError):
        execute_settlement(settlement, outcome.record, campaign.record, pledges,
                           escrow, {}, timestamp=1)  # no beneficiary node provided


@pytest.mark.property
def test_double_execute_blocked_by_applied_set():
    escrow, beneficiary = AccountNode(), AccountNode()
    funder = AccountNode(genesis_balances={"PLS": 5000})
    funder.transfer_to(escrow, "PLS", 600, timestamp=1)
    funder.transfer_to(escrow, "PLS", 600, timestamp=2)   # over-fund: escrow=1200, payout=600
    authority = _authority()
    campaign = authority.define(Campaign(scope=SCOPE, goal=500, opens_at=0, closes_at=10,
                                         beneficiary=beneficiary.address))
    pledges = [_pledge(funder.address, _nf("p0"), 600)]
    outcome = authority.certify_outcome(campaign.record, pledges)
    settlement = authority.settle(outcome.record, campaign.record, pledges)

    applied: set = set()
    execute_settlement(settlement, outcome.record, campaign.record, pledges,
                       escrow, {beneficiary.address: beneficiary}, timestamp=100, applied=applied)
    assert beneficiary.balance("PLS") == 600
    # replaying the SAME settlement must not pay again, even though the escrow still has 600
    with pytest.raises(EscrowError):
        execute_settlement(settlement, outcome.record, campaign.record, pledges,
                           escrow, {beneficiary.address: beneficiary}, timestamp=200, applied=applied)
    assert beneficiary.balance("PLS") == 600  # not double-paid


@pytest.mark.property
def test_self_transfer_payee_rejected_before_value_moves():
    escrow, beneficiary = AccountNode(), AccountNode()
    funder = AccountNode(genesis_balances={"PLS": 1000})
    funder.transfer_to(escrow, "PLS", 300, timestamp=1)
    authority = _authority()
    campaign = authority.define(Campaign(scope=SCOPE, goal=5000, opens_at=0, closes_at=10,
                                         beneficiary=beneficiary.address))
    # a (signed-by-escrow) pledge whose actor is the escrow itself -> refund would self-transfer
    pledges = [_pledge(escrow.address, _nf("x"), 300)]
    outcome = authority.certify_outcome(campaign.record, pledges)  # goal missed -> refund
    settlement = authority.settle(outcome.record, campaign.record, pledges)
    with pytest.raises(EscrowError):
        execute_settlement(settlement, outcome.record, campaign.record, pledges,
                           escrow, {escrow.address: escrow}, timestamp=100)
    assert escrow.balance("PLS") == 300  # nothing moved


@pytest.mark.property
def test_non_auditing_settlement_is_refused_before_value_moves():
    escrow = AccountNode(genesis_balances={"PLS": 1000})
    beneficiary = AccountNode()
    authority = _authority()
    campaign = authority.define(Campaign(scope=SCOPE, goal=100, opens_at=0, closes_at=10,
                                         beneficiary=beneficiary.address))
    pledges = [_pledge(AccountNode().address, _nf("p0"), 300)]
    outcome = authority.certify_outcome(campaign.record, pledges)
    settlement = authority.settle(outcome.record, campaign.record, pledges)
    tampered_pledges = pledges + [_pledge(AccountNode().address, _nf("p1"), 50)]
    with pytest.raises(ValueError):
        execute_settlement(settlement, outcome.record, campaign.record, tampered_pledges,
                           escrow, {beneficiary.address: beneficiary}, timestamp=1)
    assert escrow.balance("PLS") == 1000  # nothing moved
