"""Proofs for the resumable, payee-validated escrow-push SettlementSession (distributed Phase 1)."""

import pytest

from knitweb.core import crypto
from knitweb.knitwebs.crowdfunding import (
    PLEDGE_KIND,
    Campaign,
    CrowdfundingCampaign,
    EscrowError,
    SettlementSession,
)
from knitweb.ledger.node import AccountNode

SCOPE = "campaign-Z"


def _nf(name: str) -> str:
    return crypto.sha256(name.encode()).hex()


def _pledge(actor: str, nf: str, amount: int, pledged_at: int = 5) -> dict:
    return {"kind": PLEDGE_KIND, "scope": SCOPE, "amount": amount, "actor": actor,
            "scope_nullifier": nf, "pledged_at": pledged_at}


def _authority():
    priv, _ = crypto.generate_keypair()
    return CrowdfundingCampaign(priv, SCOPE)


def _refund_world():
    escrow, beneficiary = AccountNode(), AccountNode()
    p0 = AccountNode(genesis_balances={"PLS": 1000})
    p1 = AccountNode(genesis_balances={"PLS": 1000})
    p0.transfer_to(escrow, "PLS", 300, timestamp=1)
    p1.transfer_to(escrow, "PLS", 200, timestamp=2)
    authority = _authority()
    campaign = authority.define(Campaign(scope=SCOPE, goal=5000, opens_at=0, closes_at=10,
                                         beneficiary=beneficiary.address))
    pledges = [_pledge(p0.address, _nf("p0"), 300), _pledge(p1.address, _nf("p1"), 200)]
    outcome = authority.certify_outcome(campaign.record, pledges)  # goal missed -> refund
    settlement = authority.settle(outcome.record, campaign.record, pledges)
    payees = {p0.address: p0, p1.address: p1}
    return escrow, p0, p1, campaign, pledges, outcome, settlement, payees


@pytest.mark.property
def test_session_run_refunds_everyone_conserved():
    escrow, p0, p1, campaign, pledges, outcome, settlement, payees = _refund_world()
    session = SettlementSession(settlement, outcome.record, campaign.record, pledges, escrow, payees)
    knits = session.run(100)
    assert len(knits) == 2 and session.is_complete()
    assert escrow.balance("PLS") == 0
    assert p0.balance("PLS") == 1000 and p1.balance("PLS") == 1000


@pytest.mark.property
def test_session_resumes_from_cursor():
    escrow, p0, p1, campaign, pledges, outcome, settlement, payees = _refund_world()
    session = SettlementSession(settlement, outcome.record, campaign.record, pledges, escrow, payees)
    session.step(100)                       # process one entry
    assert session.cursor == 1 and not session.is_complete()
    assert escrow.balance("PLS") < 500      # one payout has gone out
    session.run(200)                        # resume the remainder
    assert session.is_complete() and escrow.balance("PLS") == 0
    assert p0.balance("PLS") == 1000 and p1.balance("PLS") == 1000


@pytest.mark.property
def test_session_idempotent_with_applied_set():
    escrow, p0, p1, campaign, pledges, outcome, settlement, payees = _refund_world()
    applied: set = set()
    SettlementSession(settlement, outcome.record, campaign.record, pledges,
                      escrow, payees, applied=applied).run(100)
    with pytest.raises(EscrowError):  # a fresh session for the same settlement is refused
        SettlementSession(settlement, outcome.record, campaign.record, pledges,
                          escrow, payees, applied=applied)


@pytest.mark.property
def test_session_underfunded_escrow_raises_before_any_payout():
    escrow, beneficiary = AccountNode(), AccountNode()  # escrow unfunded
    authority = _authority()
    campaign = authority.define(Campaign(scope=SCOPE, goal=100, opens_at=0, closes_at=10,
                                         beneficiary=beneficiary.address))
    pledges = [_pledge(AccountNode().address, _nf("p0"), 300)]
    outcome = authority.certify_outcome(campaign.record, pledges)  # release, needs 300
    settlement = authority.settle(outcome.record, campaign.record, pledges)
    with pytest.raises(EscrowError):
        SettlementSession(settlement, outcome.record, campaign.record, pledges,
                          escrow, {beneficiary.address: beneficiary})
