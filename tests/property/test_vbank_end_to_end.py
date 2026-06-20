"""End-to-end: define a poll, enrol real voters via the personhood gate, cast gated+signed
ballots, certify the result. Proves personhood + vBank compose with *real* ballots (not
synthetic dicts) and that revocation blocks a voter before they can cast."""

import pytest

from knitweb.core import crypto
from knitweb.fabric.web import Web
from knitweb.knitwebs.vbank import Ballot, Poll, VbankKnitweb, VbankPoll
from knitweb.personhood.errors import RevokedError
from knitweb.personhood.gate import AnchorIndex, enroll, require_personhood
from knitweb.personhood.pairwise import derive_pairwise_keypair, pairwise_address
from knitweb.personhood.records import ISSUER_CLASS_EUDI_PID
from knitweb.personhood.revocation import RevocationLog
from knitweb.personhood.verifier import Presentation, TrustedRPVerifier

EUDI_ENTRY = b"eu-trusted-list:NL:pid-issuer"
SCOPE = "vbank"
POLL_ID = "referendum-2026"
NOW = 1500  # inside both the anchor validity [1000,2000) and the poll window [1000,2000)


class World:
    """The election infrastructure: an RP verifier, an anchor index, a revocation log,
    and a (separate) poll authority."""

    def __init__(self):
        self.verifier = TrustedRPVerifier.from_issuer_entries({EUDI_ENTRY: ISSUER_CLASS_EUDI_PID})
        self.rp_priv, _ = crypto.generate_keypair()
        self.index = AnchorIndex()
        self.revlog = RevocationLog(self.rp_priv, scope=SCOPE)
        authority_priv, _ = crypto.generate_keypair()
        self.authority = VbankPoll(authority_priv, SCOPE)

    def presentation(self, secret):
        return Presentation(holder_secret=secret, issuer_entry=EUDI_ENTRY, age_over_18=True,
                            is_unique_person=True, not_before=1000, not_after=2000,
                            transcript=b"openid4vp-redacted")

    def enrol(self, secret):
        priv, pub = derive_pairwise_keypair(secret, SCOPE)
        anchor = enroll(SCOPE, self.presentation(secret), verifier=self.verifier,
                        anchor_index=self.index, rp_priv=self.rp_priv,
                        holder_pairwise_priv=priv,
                        revocation_pointer=crypto.sha256(b"rev" + secret).hex())
        return priv, pairwise_address(pub), anchor

    def ticket(self, secret):
        return require_personhood(SCOPE, self.presentation(secret), verifier=self.verifier,
                                  anchor_index=self.index, now=NOW, revocation=self.revlog, epoch=1)


def _secret(tag: bytes) -> bytes:
    return crypto.sha256(tag)  # 32 bytes, deterministic per tag


@pytest.mark.property
def test_full_election_flow_real_signed_ballots():
    w = World()
    vb = VbankKnitweb(SCOPE)
    poll = w.authority.define(Poll(scope=SCOPE, poll_id=POLL_ID, options=3,
                                   opens_at=1000, closes_at=2000, quorum=2))

    # Five voters enrol and cast real gated, signed ballots: choices 0,0,1,2,0
    choices = [0, 0, 1, 2, 0]
    ballot_records = []
    for i, choice in enumerate(choices):
        secret = _secret(f"voter-{i}".encode())
        priv, addr, _anchor = w.enrol(secret)
        ticket = w.ticket(secret)
        ballot = Ballot(scope=SCOPE, poll_id=POLL_ID, choice=choice, voter=addr,
                        scope_nullifier=ticket.scope_nullifier, cast_at=NOW)
        att = vb.emit(ballot, ticket, priv)
        assert att.verify(author_field="actor")          # each ballot is really signed
        ballot_records.append(att.record)

    result = w.authority.certify_result(poll.record, ballot_records)
    assert result.verify(author_field="authority")
    assert result.record["total_voters"] == 5
    assert result.record["results"] == [[0, 3], [1, 1], [2, 1]]
    assert result.record["winner"] == 0 and result.record["winner_votes"] == 3
    assert result.record["quorum_met"] is True
    # certification is order-independent over the real records
    assert w.authority.certify_result(poll.record, list(reversed(ballot_records))).cid == result.cid


@pytest.mark.property
def test_revoked_voter_cannot_cast():
    w = World()
    secret = _secret(b"revoked-voter")
    _priv, _addr, anchor = w.enrol(secret)
    # revoke this voter's anchor before they vote
    w.revlog.revoke(anchor.record["revocation_pointer"], revoked_at=NOW)
    with pytest.raises(RevokedError):
        w.ticket(secret)  # no ticket -> cannot emit a ballot at all
