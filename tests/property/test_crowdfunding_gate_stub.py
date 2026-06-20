"""Proves the personhood foundation anchors crowdfunding too: a pledge needs a ticket, carries
no identity, and (unlike a vote) the same verified person may pledge repeatedly."""

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.web import Web
from knitweb.knitwebs.crowdfunding import CrowdfundingKnitweb, Pledge
from knitweb.personhood.gate import AnchorIndex, enroll, require_personhood
from knitweb.personhood.nullifier import new_holder_secret, scope_nullifier
from knitweb.personhood.pairwise import derive_pairwise_keypair, pairwise_address
from knitweb.personhood.records import ISSUER_CLASS_EUDI_PID
from knitweb.personhood.revocation import RevocationLog
from knitweb.personhood.verifier import Presentation, TrustedRPVerifier

EUDI_ENTRY = b"eu-trusted-list:NL:pid-issuer"
SCOPE = "campaign-42"


def _world():
    verifier = TrustedRPVerifier.from_issuer_entries({EUDI_ENTRY: ISSUER_CLASS_EUDI_PID})
    rp_priv, _ = crypto.generate_keypair()
    return verifier, rp_priv, AnchorIndex(), RevocationLog(rp_priv, scope=SCOPE)


def _presentation(secret):
    return Presentation(holder_secret=secret, issuer_entry=EUDI_ENTRY, age_over_18=True,
                        is_unique_person=True, not_before=1000, not_after=2000,
                        transcript=b"redacted")


def _enrolled_ticket(verifier, rp_priv, index, revlog, secret, now=1500):
    priv, addr = derive_pairwise_keypair(secret, SCOPE)[0], pairwise_address(
        derive_pairwise_keypair(secret, SCOPE)[1]
    )
    enroll(SCOPE, _presentation(secret), verifier=verifier, anchor_index=index,
           rp_priv=rp_priv, holder_pairwise_priv=priv,
           revocation_pointer=crypto.sha256(b"rev" + secret).hex())
    ticket = require_personhood(SCOPE, _presentation(secret), verifier=verifier,
                                anchor_index=index, now=now, revocation=revlog, epoch=1)
    return ticket, priv, addr


@pytest.mark.property
def test_gated_pledge_emits_and_verifies():
    verifier, rp_priv, index, revlog = _world()
    secret = new_holder_secret()
    ticket, priv, addr = _enrolled_ticket(verifier, rp_priv, index, revlog, secret)
    cf = CrowdfundingKnitweb(SCOPE)
    pledge = Pledge(scope=SCOPE, amount=500, pledger=addr, scope_nullifier=ticket.scope_nullifier)
    att = cf.emit(pledge, ticket, priv)
    assert att.verify(author_field="actor")
    assert att.cid == canonical.cid(att.record)


@pytest.mark.property
def test_pledge_record_carries_no_identity():
    verifier, rp_priv, index, revlog = _world()
    secret = new_holder_secret()
    ticket, priv, addr = _enrolled_ticket(verifier, rp_priv, index, revlog, secret)
    cf = CrowdfundingKnitweb(SCOPE)
    record = cf.to_record(
        Pledge(scope=SCOPE, amount=500, pledger=addr, scope_nullifier=ticket.scope_nullifier),
        ticket,
    )
    assert set(record) == {"kind", "scope", "amount", "actor", "scope_nullifier", "pledged_at"}
    assert record["scope_nullifier"] == scope_nullifier(secret, SCOPE)


@pytest.mark.property
def test_pledge_without_matching_ticket_is_refused():
    verifier, rp_priv, index, revlog = _world()
    secret = new_holder_secret()
    ticket, priv, addr = _enrolled_ticket(verifier, rp_priv, index, revlog, secret)
    cf = CrowdfundingKnitweb(SCOPE)
    forged = Pledge(scope=SCOPE, amount=500, pledger=addr,
                    scope_nullifier=crypto.sha256(b"not-mine").hex())
    with pytest.raises(ValueError):
        cf.emit(forged, ticket, priv)


@pytest.mark.property
@pytest.mark.parametrize("bad_amount", [0, -1])
def test_non_positive_amount_rejected(bad_amount):
    with pytest.raises((ValueError, TypeError)):
        Pledge(scope=SCOPE, amount=bad_amount, pledger="x", scope_nullifier="y")


@pytest.mark.property
def test_float_amount_rejected():
    with pytest.raises(TypeError):
        Pledge(scope=SCOPE, amount=1.5, pledger="x", scope_nullifier="y")


@pytest.mark.property
def test_same_person_may_pledge_repeatedly():
    # Contrast with voting: pledges are NOT deduped on the nullifier; the foundation gates
    # personhood, not pledge-count. Two pledges by one person both verify, sharing a nullifier.
    verifier, rp_priv, index, revlog = _world()
    web = Web()
    secret = new_holder_secret()
    ticket, priv, addr = _enrolled_ticket(verifier, rp_priv, index, revlog, secret)
    cf = CrowdfundingKnitweb(SCOPE)
    cid1, a1 = cf.weave(Pledge(SCOPE, 100, addr, ticket.scope_nullifier), ticket, priv, web)
    cid2, a2 = cf.weave(Pledge(SCOPE, 250, addr, ticket.scope_nullifier), ticket, priv, web)
    assert a1.verify(author_field="actor") and a2.verify(author_field="actor")
    assert cid1 != cid2  # different amounts -> different records
    assert a1.record["scope_nullifier"] == a2.record["scope_nullifier"]  # same verified person


@pytest.mark.property
def test_pledge_signed_by_non_pledger_key_fails():
    verifier, rp_priv, index, revlog = _world()
    secret = new_holder_secret()
    ticket, _priv, addr = _enrolled_ticket(verifier, rp_priv, index, revlog, secret)
    cf = CrowdfundingKnitweb(SCOPE)
    pledge = Pledge(scope=SCOPE, amount=500, pledger=addr, scope_nullifier=ticket.scope_nullifier)
    other_priv, _ = crypto.generate_keypair()
    with pytest.raises(ValueError):
        cf.emit(pledge, ticket, other_priv)
