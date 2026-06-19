"""Proofs for votebank crowdfunding: one-person-one-backing breadth + PLS capital, all-or-nothing.

Principles under test:
  * No premine — the pool is exactly what real backers pledged.
  * One backing per registered person (the votebank rule applied to funding); whales can't stuff.
  * Success needs BOTH a capital goal AND a breadth (min_backers) threshold by the deadline.
  * All-or-nothing: met ⇒ release to beneficiary; not met ⇒ refund everyone.
  * Recent backing weighs exponentially more (momentum), without changing settlement.
"""

import pytest

from knitweb.govern import (
    Campaign,
    CampaignStatus,
    Decay,
    VoteBank,
    WorldRegistry,
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
def test_fresh_campaign_holds_nothing():
    bank = _bank_with([_person(1)])
    c = Campaign(bank, beneficiary="pls1beneficiary", goal=100, deadline=10)
    assert c.status is CampaignStatus.OPEN
    assert c.total_raised() == 0 and c.backers() == 0
    assert c.cid.startswith("b")


@pytest.mark.property
def test_only_registered_people_may_back():
    bank = _bank_with([])  # empty registry
    c = Campaign(bank, beneficiary="pls1b", goal=10, deadline=10)
    with pytest.raises(ValueError, match="not registered"):
        c.pledge(_person(99), 5, beat=1)


@pytest.mark.property
def test_one_backing_per_person():
    p = _person(1)
    bank = _bank_with([p])
    c = Campaign(bank, beneficiary="pls1b", goal=10, deadline=10)
    first = c.pledge(p, 5, beat=1)
    assert first is not None and first.amount == 5
    assert c.pledge(p, 50, beat=2) is None        # same person can't back again (no whale stuffing)
    assert c.total_raised() == 5 and c.backers() == 1


@pytest.mark.property
def test_pledge_after_deadline_rejected():
    p = _person(1)
    bank = _bank_with([p])
    c = Campaign(bank, beneficiary="pls1b", goal=10, deadline=5)
    with pytest.raises(ValueError, match="deadline"):
        c.pledge(p, 5, beat=6)


@pytest.mark.property
def test_funded_releases_all_to_beneficiary():
    people = [_person(i) for i in range(3)]
    bank = _bank_with(people)
    c = Campaign(bank, beneficiary="pls1b", goal=30, deadline=10, min_backers=3)
    for i, p in enumerate(people):
        c.pledge(p, 10, beat=i + 1)
    assert c.is_goal_met()
    res = c.resolve(now=10)
    assert res.funded and res.status is CampaignStatus.FUNDED
    assert res.release_to_beneficiary == 30 and res.refunds == ()
    assert res.backers == 3


@pytest.mark.property
def test_capital_met_but_breadth_missing_expires():
    # Two people pledge enough capital, but the campaign demands 3 distinct backers ⇒ refund.
    people = [_person(1), _person(2)]
    bank = _bank_with(people)
    c = Campaign(bank, beneficiary="pls1b", goal=20, deadline=10, min_backers=3)
    c.pledge(people[0], 15, beat=1)
    c.pledge(people[1], 15, beat=2)
    assert c.total_raised() == 30 >= c.goal      # capital cleared
    assert not c.is_goal_met()                    # but breadth (3 backers) not
    res = c.resolve(now=10)
    assert res.status is CampaignStatus.EXPIRED
    assert res.release_to_beneficiary == 0
    assert sorted(res.refunds) == sorted([(people[0].subject, 15), (people[1].subject, 15)])


@pytest.mark.property
def test_underfunded_refunds_everyone():
    people = [_person(i) for i in range(2)]
    bank = _bank_with(people)
    c = Campaign(bank, beneficiary="pls1b", goal=100, deadline=5)
    for i, p in enumerate(people):
        c.pledge(p, 10, beat=i + 1)
    res = c.resolve(now=5)
    assert res.status is CampaignStatus.EXPIRED
    assert res.release_to_beneficiary == 0
    assert dict(res.refunds) == {people[0].subject: 10, people[1].subject: 10}


@pytest.mark.property
def test_cannot_resolve_before_deadline():
    bank = _bank_with([_person(1)])
    c = Campaign(bank, beneficiary="pls1b", goal=10, deadline=10)
    with pytest.raises(ValueError, match="still open"):
        c.resolve(now=9)


@pytest.mark.property
def test_resolve_is_idempotent_and_closes_pledging():
    p = _person(1)
    bank = _bank_with([p])
    c = Campaign(bank, beneficiary="pls1b", goal=1, deadline=5)
    c.pledge(p, 5, beat=1)
    first = c.resolve(now=5)
    second = c.resolve(now=99)
    assert first is second                        # idempotent — same decision object
    with pytest.raises(ValueError, match="not open"):
        c.pledge(register_national("earth", "late", timestamp=1), 5, beat=5)


@pytest.mark.property
def test_no_premine_conservation():
    # Pool out == pool in: released amount (funded) equals total pledged; refunds sum to total.
    people = [_person(i) for i in range(4)]
    bank = _bank_with(people)
    c = Campaign(bank, beneficiary="pls1b", goal=40, deadline=10, min_backers=4)
    for i, p in enumerate(people):
        c.pledge(p, 10, beat=i + 1)
    res = c.resolve(now=10)
    assert res.release_to_beneficiary == c.total_raised() == 40   # nothing minted, nothing lost


@pytest.mark.property
def test_momentum_weights_recent_backing_more():
    # Two campaigns, identical capital & backers, but one's support is fresh ⇒ higher momentum.
    people = [_person(i) for i in range(2)]
    bank = _bank_with(people)
    decay = Decay(num=1, den=2)

    fresh = Campaign(bank, beneficiary="pls1b", goal=20, deadline=10, min_backers=2)
    fresh.pledge(people[0], 10, beat=10)
    fresh.pledge(people[1], 10, beat=10)

    stalled = Campaign(bank, beneficiary="pls1b", goal=20, deadline=10, min_backers=2)
    stalled.pledge(people[0], 10, beat=2)
    stalled.pledge(people[1], 10, beat=2)

    now = 10
    assert fresh.momentum(now=now, decay=decay) > stalled.momentum(now=now, decay=decay)
    # Settlement is unaffected by momentum: both are equally funded.
    assert fresh.resolve(now=now).funded and stalled.resolve(now=now).funded


@pytest.mark.property
def test_bool_amount_rejected():
    p = _person(1)
    bank = _bank_with([p])
    c = Campaign(bank, beneficiary="pls1b", goal=10, deadline=10)
    with pytest.raises(TypeError):
        c.pledge(p, True, beat=1)


@pytest.mark.property
def test_freeport_backers_count_for_breadth():
    # The freeport on-ramp lets the unbanked back campaigns too — breadth includes them.
    nat = register_national("earth", "citizen", timestamp=1)
    fp = register_freeport("earth", imei="I-1", email="a@fp", ad_hoc_proof="vow", timestamp=2)
    bank = _bank_with([nat, fp])
    c = Campaign(bank, beneficiary="pls1b", goal=2, deadline=10, min_backers=2)
    c.pledge(nat, 1, beat=1)
    c.pledge(fp, 1, beat=2)
    assert c.is_goal_met() and c.resolve(now=10).funded
