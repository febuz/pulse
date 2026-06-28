"""Proofs for Bluetooth local backing: proximity-attested, present backers gate a local campaign."""

import pytest

from knitweb.govern import (
    Campaign,
    ProximityProof,
    VoteBank,
    WorldRegistry,
    attest,
    register_freeport,
    register_national,
)


def _bank_with(people):
    reg = WorldRegistry(year=2026)
    for p in people:
        reg.register(p)
    return VoteBank(reg)


def _person(i):
    return register_national("earth", f"E-{i}", timestamp=i)


@pytest.mark.property
def test_proximity_proof_validation_and_range():
    p = attest("beacon-sq", "subjectA", beat=5, rssi_dbm=-50)
    assert p.cid.startswith("b")
    assert p.is_within_range(-90) and p.is_within_range(-50)
    assert not p.is_within_range(-40)
    with pytest.raises(ValueError):
        ProximityProof(backer="a", beacon="b", beat=1, rssi_dbm=10)
    with pytest.raises(TypeError):
        ProximityProof(backer="a", beacon="b", beat=1, rssi_dbm=True)


@pytest.mark.property
def test_local_pledge_counts_when_present():
    p = _person(1)
    bank = _bank_with([p])
    c = Campaign(bank, beneficiary="pls1b", goal=10, deadline=10,
                 beacon="beacon-sq", min_local_backers=1, proximity_window=2, min_rssi_dbm=-90)
    proof = attest("beacon-sq", p.subject, beat=4, rssi_dbm=-60)
    c.pledge(p, 10, beat=5, proximity=proof)
    assert c.local_backers() == 1 and c.is_goal_met()
    assert c.resolve(now=10).funded


@pytest.mark.property
def test_capital_met_but_no_local_presence_expires():
    people = [_person(1), _person(2)]
    bank = _bank_with(people)
    c = Campaign(bank, beneficiary="pls1b", goal=20, deadline=10,
                 beacon="beacon-sq", min_local_backers=1, proximity_window=1)
    c.pledge(people[0], 10, beat=1)
    c.pledge(people[1], 10, beat=2)
    assert c.total_raised() == 20 >= c.goal and c.backers() == 2
    assert c.local_backers() == 0 and not c.is_goal_met()
    assert c.resolve(now=10).status.value == "expired"


@pytest.mark.property
def test_out_of_range_or_stale_proof_is_non_local_not_an_error():
    p = _person(1)
    bank = _bank_with([p])
    c = Campaign(bank, beneficiary="pls1b", goal=5, deadline=10,
                 beacon="beacon-sq", min_local_backers=1, proximity_window=1, min_rssi_dbm=-70)
    far = attest("beacon-sq", p.subject, beat=5, rssi_dbm=-95)
    c.pledge(p, 5, beat=5, proximity=far)
    assert c.backers() == 1 and c.local_backers() == 0
    assert not c.is_goal_met()


@pytest.mark.property
def test_wrong_backer_or_beacon_proof_rejected():
    p = _person(1)
    bank = _bank_with([p])
    c = Campaign(bank, beneficiary="pls1b", goal=5, deadline=10, beacon="beacon-sq")
    with pytest.raises(ValueError, match="different backer"):
        c.pledge(p, 5, beat=5, proximity=attest("beacon-sq", "someone-else", beat=5, rssi_dbm=-50))
    with pytest.raises(ValueError, match="different beacon"):
        c.pledge(p, 5, beat=5, proximity=attest("other-beacon", p.subject, beat=5, rssi_dbm=-50))


@pytest.mark.property
def test_proximity_on_beaconless_campaign_rejected():
    p = _person(1)
    bank = _bank_with([p])
    c = Campaign(bank, beneficiary="pls1b", goal=5, deadline=10)
    with pytest.raises(ValueError, match="no beacon"):
        c.pledge(p, 5, beat=5, proximity=attest("beacon-sq", p.subject, beat=5, rssi_dbm=-50))


@pytest.mark.property
def test_min_local_backers_requires_beacon():
    bank = _bank_with([_person(1)])
    with pytest.raises(ValueError, match="requires a beacon"):
        Campaign(bank, beneficiary="pls1b", goal=5, deadline=10, min_local_backers=2)


@pytest.mark.property
def test_freeport_device_can_be_a_local_backer():
    fp = register_freeport("earth", imei="IMEI-7", email="x@fp", ad_hoc_proof="vow", timestamp=1)
    bank = _bank_with([fp])
    c = Campaign(bank, beneficiary="pls1b", goal=3, deadline=10,
                 beacon="market-beacon", min_local_backers=1, proximity_window=0, min_rssi_dbm=-90)
    c.pledge(fp, 3, beat=7, proximity=attest("market-beacon", fp.subject, beat=7, rssi_dbm=-55))
    assert c.local_backers() == 1 and c.resolve(now=10).funded


@pytest.mark.property
def test_non_local_campaign_unaffected():
    people = [_person(1), _person(2)]
    bank = _bank_with(people)
    c = Campaign(bank, beneficiary="pls1b", goal=2, deadline=10, min_backers=2)
    c.pledge(people[0], 1, beat=1)
    c.pledge(people[1], 1, beat=2)
    assert c.local_backers() == 0 and c.is_goal_met() and c.resolve(now=10).funded
