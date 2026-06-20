"""End-to-end: define a campaign, enrol real pledgers via the personhood gate, make gated+signed
pledges, weave them, collect from the fabric, and certify the outcome. Proves crowdfunding
composes with real pledges and that a person may pledge repeatedly while a revoked one cannot."""

import pytest

from knitweb.core import crypto
from knitweb.fabric.web import Web
from knitweb.knitwebs.crowdfunding import (
    Campaign,
    CrowdfundingCampaign,
    CrowdfundingKnitweb,
    Pledge,
    audit_outcome,
    collect_pledges,
)
from knitweb.personhood.errors import RevokedError
from knitweb.personhood.gate import AnchorIndex, enroll, require_personhood
from knitweb.personhood.pairwise import derive_pairwise_keypair, pairwise_address
from knitweb.personhood.records import ISSUER_CLASS_EUDI_PID
from knitweb.personhood.revocation import RevocationLog
from knitweb.personhood.verifier import Presentation, TrustedRPVerifier

EUDI_ENTRY = b"eu-trusted-list:NL:pid-issuer"
SCOPE = "campaign-2026"
NOW = 1500


class World:
    def __init__(self):
        self.verifier = TrustedRPVerifier.from_issuer_entries({EUDI_ENTRY: ISSUER_CLASS_EUDI_PID})
        self.rp_priv, _ = crypto.generate_keypair()
        self.index = AnchorIndex()
        self.revlog = RevocationLog(self.rp_priv, scope=SCOPE)
        self.authority = CrowdfundingCampaign(crypto.generate_keypair()[0], SCOPE)

    def _presentation(self, secret):
        return Presentation(holder_secret=secret, issuer_entry=EUDI_ENTRY, age_over_18=True,
                            is_unique_person=True, not_before=1000, not_after=2000,
                            transcript=b"openid4vp-redacted")

    def enrol(self, secret):
        priv, pub = derive_pairwise_keypair(secret, SCOPE)
        anchor = enroll(SCOPE, self._presentation(secret), verifier=self.verifier,
                        anchor_index=self.index, rp_priv=self.rp_priv, holder_pairwise_priv=priv,
                        revocation_pointer=crypto.sha256(b"rev" + secret).hex())
        return priv, pairwise_address(pub), anchor

    def ticket(self, secret):
        return require_personhood(SCOPE, self._presentation(secret), verifier=self.verifier,
                                  anchor_index=self.index, now=NOW, revocation=self.revlog, epoch=1)


def _secret(tag: bytes) -> bytes:
    return crypto.sha256(tag)


@pytest.mark.property
def test_full_crowdfunding_flow_real_signed_pledges():
    w = World()
    cf = CrowdfundingKnitweb(SCOPE)
    web = Web()
    campaign = w.authority.define(Campaign(scope=SCOPE, goal=1000, opens_at=1000, closes_at=2000))

    # three pledgers; the first pledges twice (allowed — pledges are not deduped)
    plan = [(_secret(b"alice"), [300]), (_secret(b"bob"), [400, 100]), (_secret(b"carol"), [500])]
    for secret, amounts in plan:
        priv, addr, _ = w.enrol(secret)
        ticket = w.ticket(secret)
        for amount in amounts:
            att = cf.weave(Pledge(scope=SCOPE, amount=amount, pledger=addr,
                                  scope_nullifier=ticket.scope_nullifier, pledged_at=NOW), ticket, priv, web)[1]
            assert att.verify(author_field="actor")

    pledges = collect_pledges(web, SCOPE)
    assert len(pledges) == 4  # 1 + 2 + 1 pledges

    outcome = w.authority.certify_outcome(campaign.record, pledges)
    assert audit_outcome(outcome, campaign.record, pledges)
    assert outcome.record["total_raised"] == 1300   # 300 + 400 + 100 + 500
    assert outcome.record["pledge_count"] == 4
    assert outcome.record["pledger_count"] == 3      # three distinct verified people
    assert outcome.record["goal_met"] is True
    # certification over the collected set is order-independent
    assert w.authority.certify_outcome(campaign.record, list(reversed(pledges))).cid == outcome.cid


@pytest.mark.property
def test_revoked_pledger_cannot_pledge():
    w = World()
    secret = _secret(b"revoked-pledger")
    _priv, _addr, anchor = w.enrol(secret)
    w.revlog.revoke(anchor.record["revocation_pointer"], revoked_at=NOW)
    with pytest.raises(RevokedError):
        w.ticket(secret)  # no ticket -> cannot pledge
