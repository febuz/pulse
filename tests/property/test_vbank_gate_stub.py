"""Acceptance: a vBank vote is impossible without a valid personhood ticket, and the
ballot never carries identity (mirrors roadmap line 53)."""

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.attest import verify_record
from knitweb.fabric.web import Web
from knitweb.knitwebs.vbank import Ballot, VbankKnitweb
from knitweb.personhood.gate import AnchorIndex, enroll, require_personhood
from knitweb.personhood.nullifier import new_holder_secret, scope_nullifier
from knitweb.personhood.pairwise import derive_pairwise_keypair, pairwise_address
from knitweb.personhood.records import ISSUER_CLASS_EUDI_PID
from knitweb.personhood.revocation import RevocationLog
from knitweb.personhood.verifier import Presentation, TrustedRPVerifier

EUDI_ENTRY = b"eu-trusted-list:NL:pid-issuer"
SCOPE = "vbank"


def _world():
    verifier = TrustedRPVerifier.from_issuer_entries({EUDI_ENTRY: ISSUER_CLASS_EUDI_PID})
    rp_priv, _ = crypto.generate_keypair()
    return verifier, rp_priv, AnchorIndex(), RevocationLog(rp_priv, scope=SCOPE)


def _presentation(secret):
    return Presentation(holder_secret=secret, issuer_entry=EUDI_ENTRY, age_over_18=True,
                        is_unique_person=True, not_before=1000, not_after=2000,
                        transcript=b"redacted")


def _voter(secret):
    priv, pub = derive_pairwise_keypair(secret, SCOPE)
    return priv, pairwise_address(pub)


def _enrolled_ticket(verifier, rp_priv, index, revlog, secret, now=1500):
    priv, addr = _voter(secret)
    enroll(SCOPE, _presentation(secret), verifier=verifier, anchor_index=index,
           rp_priv=rp_priv, holder_pairwise_priv=priv,
           revocation_pointer=crypto.sha256(b"rev" + secret).hex())
    ticket = require_personhood(SCOPE, _presentation(secret), verifier=verifier,
                                anchor_index=index, now=now, revocation=revlog, epoch=1)
    return ticket, priv, addr


@pytest.mark.property
def test_gated_ballot_emits_and_verifies():
    verifier, rp_priv, index, revlog = _world()
    secret = new_holder_secret()
    ticket, priv, addr = _enrolled_ticket(verifier, rp_priv, index, revlog, secret)
    vb = VbankKnitweb(SCOPE)
    ballot = Ballot(scope=SCOPE, poll_id="p1", choice=1, voter=addr,
                    scope_nullifier=ticket.scope_nullifier)
    att = vb.emit(ballot, ticket, priv)
    assert att.verify(author_field="actor")
    assert att.cid == canonical.cid(att.record)


@pytest.mark.property
def test_ballot_record_carries_no_identity():
    verifier, rp_priv, index, revlog = _world()
    secret = new_holder_secret()
    ticket, priv, addr = _enrolled_ticket(verifier, rp_priv, index, revlog, secret)
    vb = VbankKnitweb(SCOPE)
    record = vb.to_record(
        Ballot(scope=SCOPE, poll_id="p1", choice=1, voter=addr, scope_nullifier=ticket.scope_nullifier),
        ticket,
    )
    assert set(record) == {"kind", "scope", "poll_id", "choice", "actor", "scope_nullifier", "seq", "cast_at"}
    # the only identity-like fields are the per-scope pairwise address and the nullifier
    assert record["scope_nullifier"] == scope_nullifier(secret, SCOPE)


@pytest.mark.property
def test_ballot_with_mismatched_ticket_is_refused():
    verifier, rp_priv, index, revlog = _world()
    secret = new_holder_secret()
    ticket, priv, addr = _enrolled_ticket(verifier, rp_priv, index, revlog, secret)
    vb = VbankKnitweb(SCOPE)
    # forge a ballot with a different nullifier than the ticket authorises
    forged = Ballot(scope=SCOPE, poll_id="p1", choice=1, voter=addr,
                    scope_nullifier=crypto.sha256(b"not-mine").hex())
    with pytest.raises(ValueError):
        vb.emit(forged, ticket, priv)


@pytest.mark.property
def test_one_person_one_vote_nullifier_is_stable():
    # The tally dedups on scope_nullifier; the same person always presents the same one.
    secret = new_holder_secret()
    assert scope_nullifier(secret, SCOPE) == scope_nullifier(secret, SCOPE)


@pytest.mark.property
def test_two_voters_have_distinct_nullifiers_and_can_both_weave():
    verifier, rp_priv, index, revlog = _world()
    web = Web()
    cids = set()
    for tag in (b"alice", b"bob"):
        secret = crypto.sha256(tag) + crypto.sha256(tag + b"!")  # 64 bytes -> take 32
        secret = secret[:32]
        ticket, priv, addr = _enrolled_ticket(verifier, rp_priv, index, revlog, secret)
        vb = VbankKnitweb(SCOPE)
        ballot = Ballot(scope=SCOPE, poll_id="p1", choice=1, voter=addr,
                        scope_nullifier=ticket.scope_nullifier)
        cid, att = vb.weave(ballot, ticket, priv, web)
        assert att.verify(author_field="actor")
        cids.add(ticket.scope_nullifier)
    assert len(cids) == 2  # distinct people -> distinct nullifiers


@pytest.mark.property
def test_a_ballot_signed_by_a_non_voter_key_fails():
    verifier, rp_priv, index, revlog = _world()
    secret = new_holder_secret()
    ticket, _priv, addr = _enrolled_ticket(verifier, rp_priv, index, revlog, secret)
    vb = VbankKnitweb(SCOPE)
    ballot = Ballot(scope=SCOPE, poll_id="p1", choice=1, voter=addr,
                    scope_nullifier=ticket.scope_nullifier)
    other_priv, _ = crypto.generate_keypair()
    # signing key's address != ballot.voter -> attest refuses (content signature is bound)
    with pytest.raises(ValueError):
        vb.emit(ballot, ticket, other_priv)
