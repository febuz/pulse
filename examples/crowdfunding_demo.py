"""Crowdfunding — end-to-end fundraising demo on the personhood foundation.

Runs the whole crowdfunding loop in one script and asserts every invariant, so a green run is
the feature's definition-of-done:

    define a campaign (goal + window) → enrol real pledgers via the personhood gate (eIDAS
    trusted-RP, zero PII) → each makes a gated, signed pledge → weave into the fabric → collect
    the pledges → certify an audited outcome (total raised, goal met, distinct pledger count) →
    show a revoked pledger cannot contribute.

Run:  PYTHONPATH=src python examples/crowdfunding_demo.py   (exit 0 ⇒ crowdfunding works)

Everything is stdlib + `cryptography` only — integers throughout, no identity on the fabric.
A pledge models a donation/reward (investment/lending need regulatory review, out of scope).
"""

from __future__ import annotations

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


def _presentation(secret: bytes) -> Presentation:
    return Presentation(holder_secret=secret, issuer_entry=EUDI_ENTRY, age_over_18=True,
                        is_unique_person=True, not_before=1000, not_after=2000,
                        transcript=b"openid4vp-redacted")


def main() -> None:
    verifier = TrustedRPVerifier.from_issuer_entries({EUDI_ENTRY: ISSUER_CLASS_EUDI_PID})
    rp_priv, _ = crypto.generate_keypair()
    index = AnchorIndex()
    revlog = RevocationLog(rp_priv, scope=SCOPE)
    authority = CrowdfundingCampaign(crypto.generate_keypair()[0], SCOPE)
    cf = CrowdfundingKnitweb(SCOPE)
    web = Web()

    campaign = authority.define(Campaign(scope=SCOPE, goal=1000, opens_at=1000, closes_at=2000))
    print(f"campaign defined: {SCOPE} (goal 1000 PLS-wei), signed by {campaign.record['authority']}")

    # three pledgers; the first pledges twice (pledges are not deduped)
    plan = [(crypto.sha256(b"alice"), [300]),
            (crypto.sha256(b"bob"), [400, 100]),
            (crypto.sha256(b"carol"), [500])]
    for secret, amounts in plan:
        priv, pub = derive_pairwise_keypair(secret, SCOPE)
        addr = pairwise_address(pub)
        enroll(SCOPE, _presentation(secret), verifier=verifier, anchor_index=index,
               rp_priv=rp_priv, holder_pairwise_priv=priv,
               revocation_pointer=crypto.sha256(b"rev" + secret).hex())
        ticket = require_personhood(SCOPE, _presentation(secret), verifier=verifier,
                                    anchor_index=index, now=NOW, revocation=revlog, epoch=1)
        for amount in amounts:
            _cid, att = cf.weave(Pledge(scope=SCOPE, amount=amount, pledger=addr,
                                        scope_nullifier=ticket.scope_nullifier, pledged_at=NOW),
                                 ticket, priv, web)
            assert att.verify(author_field="actor")
            # the on-fabric pledge carries no identity
            assert set(att.record) == {"kind", "scope", "amount", "actor",
                                       "scope_nullifier", "pledged_at"}

    pledges = collect_pledges(web, SCOPE)
    outcome = authority.certify_outcome(campaign.record, pledges)
    assert audit_outcome(outcome, campaign.record, pledges)
    o = outcome.record
    assert o["total_raised"] == 1300 and o["pledge_count"] == 4 and o["pledger_count"] == 3
    assert o["goal_met"] is True
    # order-independence: shuffling the pledge set gives the identical certified outcome
    assert authority.certify_outcome(campaign.record, list(reversed(pledges))).cid == outcome.cid
    print(f"outcome certified: raised {o['total_raised']} from {o['pledge_count']} pledges by "
          f"{o['pledger_count']} verified people (goal_met={o['goal_met']}); "
          f"pledge_root={o['pledge_root'][:16]}…")

    # a revoked pledger cannot obtain a ticket, so cannot pledge
    secret = crypto.sha256(b"revoked-pledger")
    priv, pub = derive_pairwise_keypair(secret, SCOPE)
    enroll(SCOPE, _presentation(secret), verifier=verifier, anchor_index=index,
           rp_priv=rp_priv, holder_pairwise_priv=priv,
           revocation_pointer=crypto.sha256(b"rev" + secret).hex())
    revlog.revoke(crypto.sha256(b"rev" + secret).hex(), revoked_at=NOW)
    try:
        require_personhood(SCOPE, _presentation(secret), verifier=verifier,
                           anchor_index=index, now=NOW, revocation=revlog, epoch=2)
        raise AssertionError("revoked pledger should not get a ticket")
    except RevokedError:
        print("revoked pledger correctly blocked from contributing")

    print("\ncrowdfunding demo OK ✓")


if __name__ == "__main__":
    main()
