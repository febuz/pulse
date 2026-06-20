"""Proofs for crowdfunding campaign aggregation: signed definitions + audited outcomes."""

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.web import Web
from knitweb.knitwebs.crowdfunding import (
    CAMPAIGN_KIND,
    OUTCOME_KIND,
    PLEDGE_KIND,
    SETTLEMENT_KIND,
    Campaign,
    CrowdfundingCampaign,
    audit_outcome,
    audit_settlement,
    collect_pledges,
    verify_outcome,
    verify_settlement,
)

BENEFICIARY = crypto.address(crypto.generate_keypair()[1])

SCOPE = "campaign-7"


def _authority():
    priv, _ = crypto.generate_keypair()
    return priv, CrowdfundingCampaign(priv, SCOPE)


def _campaign(authority: CrowdfundingCampaign, goal: int = 1000, beneficiary: str = ""):
    return authority.define(Campaign(scope=SCOPE, goal=goal, opens_at=1000, closes_at=2000,
                                     beneficiary=beneficiary))


def _nf(i: int) -> str:
    return crypto.sha256(f"pledger-{i}".encode()).hex()


def _pledge(nullifier: str, amount: int, pledged_at: int = 1500) -> dict:
    return {
        "kind": PLEDGE_KIND, "scope": SCOPE, "amount": amount,
        "actor": "pls1" + nullifier[:16], "scope_nullifier": nullifier, "pledged_at": pledged_at,
    }


@pytest.mark.property
@pytest.mark.parametrize("bad", [
    {"goal": 0, "opens_at": 0, "closes_at": 10},
    {"goal": 100, "opens_at": 10, "closes_at": 5},
])
def test_invalid_campaign_rejected(bad):
    with pytest.raises((ValueError, TypeError)):
        Campaign(scope=SCOPE, **bad)


@pytest.mark.property
def test_campaign_definition_is_signed():
    priv, authority = _authority()
    att = _campaign(authority, goal=1000)
    assert att.verify(author_field="authority")
    assert att.record["kind"] == CAMPAIGN_KIND
    assert att.record["goal"] == 1000


@pytest.mark.property
def test_outcome_sums_in_window_pledges_and_checks_goal():
    priv, authority = _authority()
    campaign = _campaign(authority, goal=1000)
    pledges = [_pledge(_nf(0), 300), _pledge(_nf(1), 400), _pledge(_nf(2), 500)]
    out = authority.certify_outcome(campaign.record, pledges)
    assert out.verify(author_field="authority")
    assert out.record["kind"] == OUTCOME_KIND
    assert out.record["total_raised"] == 1200
    assert out.record["goal_met"] is True
    assert out.record["pledger_count"] == 3
    assert out.record["pledge_count"] == 3
    assert out.record["campaign_cid"] == canonical.cid(campaign.record)


@pytest.mark.property
def test_repeat_pledges_count_but_pledgers_are_distinct():
    priv, authority = _authority()
    campaign = _campaign(authority, goal=1000)
    pledges = [_pledge(_nf(0), 200), _pledge(_nf(0), 300), _pledge(_nf(1), 400)]  # nf0 twice
    out = authority.certify_outcome(campaign.record, pledges)
    assert out.record["total_raised"] == 900
    assert out.record["pledge_count"] == 3      # all pledges count
    assert out.record["pledger_count"] == 2     # but two distinct verified people
    assert out.record["goal_met"] is False


@pytest.mark.property
def test_out_of_window_pledges_excluded():
    priv, authority = _authority()
    campaign = _campaign(authority, goal=500)  # window [1000, 2000)
    pledges = [
        _pledge(_nf(0), 600, pledged_at=1500),   # counts
        _pledge(_nf(1), 999, pledged_at=999),    # before opens -> excluded
        _pledge(_nf(2), 999, pledged_at=2000),   # at close (exclusive) -> excluded
    ]
    out = authority.certify_outcome(campaign.record, pledges)
    assert out.record["total_raised"] == 600
    assert out.record["pledge_count"] == 1
    assert out.record["goal_met"] is True


@pytest.mark.property
def test_only_defining_authority_can_certify():
    _, authority_a = _authority()
    _, authority_b = _authority()
    campaign = _campaign(authority_a, goal=100)
    with pytest.raises(ValueError):
        authority_b.certify_outcome(campaign.record, [_pledge(_nf(0), 100)])


@pytest.mark.property
def test_independent_audit_and_tamper_detection():
    priv, authority = _authority()
    campaign = _campaign(authority, goal=500)
    pledges = [_pledge(_nf(0), 300), _pledge(_nf(1), 300)]
    out = authority.certify_outcome(campaign.record, pledges)
    assert verify_outcome(out.record, campaign.record, pledges)
    assert audit_outcome(out, campaign.record, pledges)
    # a different pledge set must not verify against this outcome
    assert not verify_outcome(out.record, campaign.record, pledges + [_pledge(_nf(2), 100)])
    # a tampered outcome record must not verify
    assert not verify_outcome(dict(out.record, total_raised=99999), campaign.record, pledges)


@pytest.mark.property
def test_audit_fails_on_broken_signature():
    from knitweb.fabric.attest import Attestation
    priv, authority = _authority()
    campaign = _campaign(authority, goal=500)
    pledges = [_pledge(_nf(0), 600)]
    out = authority.certify_outcome(campaign.record, pledges)
    forged = Attestation(record=dict(out.record, total_raised=1), author_pub=out.author_pub, sig=out.sig)
    assert not audit_outcome(forged, campaign.record, pledges)


@pytest.mark.property
def test_collect_pledges_from_web():
    web = Web()
    web.weave(_pledge(_nf(0), 100))
    web.weave(_pledge(_nf(1), 200))
    web.weave({"kind": "knowledge-item", "scope": SCOPE})              # noise
    web.weave(dict(_pledge(_nf(2), 300), scope="other-campaign"))      # other scope
    got = collect_pledges(web, SCOPE)
    assert len(got) == 2


@pytest.mark.property
def test_settlement_release_when_goal_met():
    priv, authority = _authority()
    campaign = _campaign(authority, goal=500, beneficiary=BENEFICIARY)
    pledges = [_pledge(_nf(0), 300), _pledge(_nf(1), 400)]  # 700 >= 500
    outcome = authority.certify_outcome(campaign.record, pledges)
    assert outcome.record["goal_met"] is True
    settlement = authority.settle(outcome.record, campaign.record, pledges)
    assert settlement.record["kind"] == SETTLEMENT_KIND
    assert settlement.record["mode"] == "release"
    assert settlement.record["total_amount"] == 700
    assert settlement.record["entry_count"] == 2
    assert settlement.record["outcome_cid"] == canonical.cid(outcome.record)
    assert audit_settlement(settlement, outcome.record, campaign.record, pledges)


@pytest.mark.property
def test_settlement_refund_when_goal_missed():
    priv, authority = _authority()
    campaign = _campaign(authority, goal=5000, beneficiary=BENEFICIARY)
    pledges = [_pledge(_nf(0), 300), _pledge(_nf(1), 400)]  # 700 < 5000
    outcome = authority.certify_outcome(campaign.record, pledges)
    assert outcome.record["goal_met"] is False
    settlement = authority.settle(outcome.record, campaign.record, pledges)
    assert settlement.record["mode"] == "refund"
    assert settlement.record["total_amount"] == 700  # everyone gets their pledge back
    assert verify_settlement(settlement.record, outcome.record, campaign.record, pledges)


@pytest.mark.property
def test_release_without_beneficiary_is_rejected():
    priv, authority = _authority()
    campaign = _campaign(authority, goal=500)  # no beneficiary
    pledges = [_pledge(_nf(0), 600)]
    outcome = authority.certify_outcome(campaign.record, pledges)
    assert outcome.record["goal_met"] is True
    with pytest.raises(ValueError):
        authority.settle(outcome.record, campaign.record, pledges)


@pytest.mark.property
def test_only_authority_can_settle():
    _, authority_a = _authority()
    _, authority_b = _authority()
    campaign = _campaign(authority_a, goal=100, beneficiary=BENEFICIARY)
    pledges = [_pledge(_nf(0), 200)]
    outcome = authority_a.certify_outcome(campaign.record, pledges)
    with pytest.raises(ValueError):
        authority_b.settle(outcome.record, campaign.record, pledges)


@pytest.mark.property
def test_settlement_audit_detects_tamper_and_mismatch():
    priv, authority = _authority()
    campaign = _campaign(authority, goal=500, beneficiary=BENEFICIARY)
    pledges = [_pledge(_nf(0), 300), _pledge(_nf(1), 400)]
    outcome = authority.certify_outcome(campaign.record, pledges)
    settlement = authority.settle(outcome.record, campaign.record, pledges)
    # tampered settlement record
    assert not verify_settlement(dict(settlement.record, mode="refund"), outcome.record, campaign.record, pledges)
    # settlement built for an outcome that doesn't match the presented pledges
    assert not verify_settlement(settlement.record, outcome.record, campaign.record, pledges + [_pledge(_nf(2), 100)])


@pytest.mark.property
def test_verify_outcome_and_settlement_reject_non_dict():
    priv, authority = _authority()
    campaign = _campaign(authority, goal=100, beneficiary=BENEFICIARY)
    assert verify_outcome([1, 2], campaign.record, []) is False
    assert verify_outcome({"kind": OUTCOME_KIND}, "not-a-dict", []) is False
    assert verify_settlement("x", {"kind": OUTCOME_KIND}, campaign.record, []) is False


@pytest.mark.property
def test_outcome_is_order_independent():
    priv, authority = _authority()
    campaign = _campaign(authority, goal=500)
    pledges = [_pledge(_nf(i), 100 * (i + 1)) for i in range(4)]
    a = authority.certify_outcome(campaign.record, pledges)
    b = authority.certify_outcome(campaign.record, list(reversed(pledges)))
    assert a.cid == b.cid
