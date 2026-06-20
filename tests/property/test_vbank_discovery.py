"""Proofs for poll/campaign discovery + status read-models (what a client/indexer needs)."""

import pytest

from knitweb.core import crypto
from knitweb.fabric.web import Web
from knitweb.knitwebs.crowdfunding import (
    Campaign,
    CrowdfundingCampaign,
    campaign_status,
    collect_campaigns,
    is_campaign_open,
)
from knitweb.knitwebs.vbank import (
    Poll,
    VbankPoll,
    collect_polls,
    is_poll_open,
    poll_status,
)


@pytest.mark.property
def test_collect_polls_and_status():
    priv, _ = crypto.generate_keypair()
    authority = VbankPoll(priv, "townhall")
    web = Web()
    p1 = authority.define(Poll(scope="townhall", poll_id="a", options=2, opens_at=100, closes_at=200))
    p2 = authority.define(Poll(scope="townhall", poll_id="b", options=3, opens_at=300, closes_at=400))
    web.weave(p1.record)
    web.weave(p2.record)
    web.weave({"kind": "vbank-ballot", "scope": "townhall"})  # noise: not a poll

    assert len(collect_polls(web)) == 2
    assert len(collect_polls(web, "townhall")) == 2
    assert collect_polls(web, "other") == []

    assert poll_status(p1.record, 50) == "upcoming" and not is_poll_open(p1.record, 50)
    assert poll_status(p1.record, 150) == "open" and is_poll_open(p1.record, 150)
    assert poll_status(p1.record, 200) == "closed"  # closes_at is exclusive
    assert poll_status(p1.record, 250) == "closed" and not is_poll_open(p1.record, 250)


@pytest.mark.property
def test_collect_campaigns_and_status():
    priv, _ = crypto.generate_keypair()
    authority = CrowdfundingCampaign(priv, "fund")
    web = Web()
    c = authority.define(Campaign(scope="fund", goal=100, opens_at=10, closes_at=20))
    web.weave(c.record)
    web.weave({"kind": "crowdfunding-pledge", "scope": "fund"})  # noise

    assert len(collect_campaigns(web)) == 1
    assert len(collect_campaigns(web, "fund")) == 1
    assert collect_campaigns(web, "other") == []

    assert campaign_status(c.record, 5) == "upcoming"
    assert campaign_status(c.record, 15) == "open" and is_campaign_open(c.record, 15)
    assert campaign_status(c.record, 20) == "closed"
    assert campaign_status(c.record, 25) == "closed" and not is_campaign_open(c.record, 25)
