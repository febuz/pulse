"""Proofs for payee-side payout validation (the distributed-settlement security primitive)."""

import pytest

from knitweb.core import crypto
from knitweb.knitwebs.crowdfunding import (
    PLEDGE_KIND,
    Campaign,
    CrowdfundingCampaign,
    validate_payout,
)
from knitweb.ledger.node import AccountNode

SCOPE = "campaign-Y"


def _nf(name: str) -> str:
    return crypto.sha256(name.encode()).hex()


def _pledge(actor: str, nf: str, amount: int, pledged_at: int = 5) -> dict:
    return {"kind": PLEDGE_KIND, "scope": SCOPE, "amount": amount, "actor": actor,
            "scope_nullifier": nf, "pledged_at": pledged_at}


def _authority():
    priv, _ = crypto.generate_keypair()
    return CrowdfundingCampaign(priv, SCOPE)


def _release_setup():
    escrow, beneficiary = AccountNode(), AccountNode()
    authority = _authority()
    campaign = authority.define(Campaign(scope=SCOPE, goal=500, opens_at=0, closes_at=10,
                                         beneficiary=beneficiary.address))
    pledges = [_pledge(AccountNode().address, _nf("p0"), 300),
               _pledge(AccountNode().address, _nf("p1"), 400)]
    outcome = authority.certify_outcome(campaign.record, pledges)  # 700 >= 500 -> release
    settlement = authority.settle(outcome.record, campaign.record, pledges)
    return escrow, beneficiary, campaign, pledges, outcome, settlement


@pytest.mark.property
def test_valid_payout_is_accepted():
    escrow, ben, campaign, pledges, outcome, settlement = _release_setup()
    knit = escrow.propose(ben.pub, "PLS", 300, timestamp=1)  # an owed amount
    assert validate_payout(knit, settlement, outcome.record, campaign.record, pledges, ben.pub)


@pytest.mark.property
def test_wrong_amount_rejected():
    escrow, ben, campaign, pledges, outcome, settlement = _release_setup()
    knit = escrow.propose(ben.pub, "PLS", 999, timestamp=1)  # not owed
    assert not validate_payout(knit, settlement, outcome.record, campaign.record, pledges, ben.pub)


@pytest.mark.property
def test_payee_not_in_settlement_rejected():
    escrow, ben, campaign, pledges, outcome, settlement = _release_setup()
    attacker = AccountNode()
    knit = escrow.propose(attacker.pub, "PLS", 300, timestamp=1)
    assert not validate_payout(knit, settlement, outcome.record, campaign.record, pledges, attacker.pub)


@pytest.mark.property
def test_wrong_symbol_rejected():
    escrow, ben, campaign, pledges, outcome, settlement = _release_setup()
    knit = escrow.propose(ben.pub, "OTHER", 300, timestamp=1)
    assert not validate_payout(knit, settlement, outcome.record, campaign.record, pledges, ben.pub)


@pytest.mark.property
def test_non_auditing_settlement_rejected():
    escrow, ben, campaign, pledges, outcome, settlement = _release_setup()
    knit = escrow.propose(ben.pub, "PLS", 300, timestamp=1)
    tampered = pledges + [_pledge(AccountNode().address, _nf("p2"), 10)]  # outcome no longer matches
    assert not validate_payout(knit, settlement, outcome.record, campaign.record, tampered, ben.pub)


@pytest.mark.property
def test_refund_payee_validates_own_entry():
    escrow, ben = AccountNode(), AccountNode()
    p0 = AccountNode()
    authority = _authority()
    campaign = authority.define(Campaign(scope=SCOPE, goal=5000, opens_at=0, closes_at=10,
                                         beneficiary=ben.address))
    pledges = [_pledge(p0.address, _nf("p0"), 300)]
    outcome = authority.certify_outcome(campaign.record, pledges)  # goal missed -> refund
    settlement = authority.settle(outcome.record, campaign.record, pledges)
    good = escrow.propose(p0.pub, "PLS", 300, timestamp=1)
    assert validate_payout(good, settlement, outcome.record, campaign.record, pledges, p0.pub)
    bad = escrow.propose(p0.pub, "PLS", 301, timestamp=1)
    assert not validate_payout(bad, settlement, outcome.record, campaign.record, pledges, p0.pub)
