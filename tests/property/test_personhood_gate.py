"""Proofs for the personhood gate: enroll once, gate actions, window + revocation."""

import pytest

from knitweb.core import crypto
from knitweb.personhood.errors import (
    AlreadyRegisteredError,
    ExpiredError,
    NotPersonError,
    RevokedError,
)
from knitweb.personhood.gate import AnchorIndex, enroll, require_personhood
from knitweb.personhood.nullifier import new_holder_secret, scope_nullifier
from knitweb.personhood.pairwise import derive_pairwise_keypair
from knitweb.personhood.records import ISSUER_CLASS_EUDI_PID
from knitweb.personhood.revocation import RevocationLog
from knitweb.personhood.verifier import Presentation, TrustedRPVerifier

EUDI_ENTRY = b"eu-trusted-list:NL:pid-issuer"
SCOPE = "vbank"


def _setup():
    verifier = TrustedRPVerifier.from_issuer_entries({EUDI_ENTRY: ISSUER_CLASS_EUDI_PID})
    rp_priv, _ = crypto.generate_keypair()
    index = AnchorIndex()
    return verifier, rp_priv, index


def _presentation(secret, *, nb=1000, na=2000):
    return Presentation(
        holder_secret=secret, issuer_entry=EUDI_ENTRY, age_over_18=True,
        is_unique_person=True, not_before=nb, not_after=na,
        transcript=b"redacted",
    )


def _holder_priv(secret):
    priv, _ = derive_pairwise_keypair(secret, SCOPE)
    return priv


def _enroll(verifier, rp_priv, index, secret, tag=b"rev"):
    return enroll(
        SCOPE, _presentation(secret), verifier=verifier, anchor_index=index,
        rp_priv=rp_priv, holder_pairwise_priv=_holder_priv(secret),
        revocation_pointer=crypto.sha256(tag + secret).hex(),
    )


@pytest.mark.property
def test_enroll_then_require_personhood_happy_path():
    verifier, rp_priv, index = _setup()
    secret = new_holder_secret()
    _enroll(verifier, rp_priv, index, secret)
    ticket = require_personhood(SCOPE, _presentation(secret), verifier=verifier,
                                anchor_index=index, now=1500)
    assert ticket.scope_nullifier == scope_nullifier(secret, SCOPE)
    assert ticket.pairwise_did.startswith("did:pls:")


@pytest.mark.property
def test_double_enroll_same_person_is_rejected():
    verifier, rp_priv, index = _setup()
    secret = new_holder_secret()
    _enroll(verifier, rp_priv, index, secret)
    with pytest.raises(AlreadyRegisteredError):
        _enroll(verifier, rp_priv, index, secret, tag=b"rev2")


@pytest.mark.property
def test_require_personhood_without_enrolment_rejected():
    verifier, _, index = _setup()
    secret = new_holder_secret()
    with pytest.raises(NotPersonError):
        require_personhood(SCOPE, _presentation(secret), verifier=verifier,
                           anchor_index=index, now=1500)


@pytest.mark.property
def test_bad_presentation_propagates_not_person():
    verifier, rp_priv, index = _setup()
    secret = new_holder_secret()
    _enroll(verifier, rp_priv, index, secret)
    bad = Presentation(holder_secret=secret, issuer_entry=EUDI_ENTRY, age_over_18=False,
                       is_unique_person=True, not_before=1000, not_after=2000, transcript=b"x")
    with pytest.raises(NotPersonError):
        require_personhood(SCOPE, bad, verifier=verifier, anchor_index=index, now=1500)


@pytest.mark.property
@pytest.mark.parametrize("now", [999, 2000, 2500])
def test_outside_validity_window_is_expired(now):
    verifier, rp_priv, index = _setup()
    secret = new_holder_secret()
    _enroll(verifier, rp_priv, index, secret)
    with pytest.raises(ExpiredError):
        require_personhood(SCOPE, _presentation(secret), verifier=verifier,
                           anchor_index=index, now=now)


@pytest.mark.property
def test_forged_wider_presentation_window_cannot_outlive_the_anchor():
    # Expiry must come from the stored anchor, not the holder-controlled presentation.
    verifier, rp_priv, index = _setup()
    secret = new_holder_secret()
    _enroll(verifier, rp_priv, index, secret)  # enrolled with window [1000, 2000)
    # holder re-presents the SAME secret (same nullifier) but a forged, much wider window
    forged = _presentation(secret, nb=0, na=1_000_000)
    with pytest.raises(ExpiredError):
        require_personhood(SCOPE, forged, verifier=verifier, anchor_index=index, now=5000)


@pytest.mark.property
def test_revoked_anchor_is_rejected_but_unrevoked_passes():
    verifier, rp_priv, index = _setup()
    revlog = RevocationLog(rp_priv, scope=SCOPE)
    secret = new_holder_secret()
    anchor = _enroll(verifier, rp_priv, index, secret)
    revptr = anchor.record["revocation_pointer"]

    # not revoked yet -> ticket issued against the epoch-pinned commitment
    ticket = require_personhood(SCOPE, _presentation(secret), verifier=verifier,
                                anchor_index=index, now=1500, revocation=revlog, epoch=1)
    assert ticket.scope_nullifier == scope_nullifier(secret, SCOPE)

    # revoke it; a fresh gate call at a later epoch must reject
    revlog.revoke(revptr, revoked_at=1600)
    with pytest.raises(RevokedError):
        require_personhood(SCOPE, _presentation(secret), verifier=verifier,
                           anchor_index=index, now=1700, revocation=revlog, epoch=2)
