"""Proofs for the PresentationVerifier seam: trusted-RP admission + ZK seam + anchor bridge."""

import pytest

from knitweb.core import crypto
from knitweb.personhood.anchor import anchor_from_admission
from knitweb.personhood.errors import NotPersonError
from knitweb.personhood.nullifier import new_holder_secret, scope_nullifier
from knitweb.personhood.pairwise import derive_pairwise_keypair, pairwise_address
from knitweb.personhood.records import (
    ISSUER_CLASS_EUDI_PID,
    ISSUER_CLASS_NON_EUDI_FALLBACK,
)
from knitweb.personhood.verifier import (
    Presentation,
    PresentationVerifier,
    TrustedRPVerifier,
    ZkVerifier,
)

EUDI_ENTRY = b"eu-trusted-list:NL:pid-issuer"
FALLBACK_ENTRY = b"notary:fallback-issuer:0001"


def _presentation(secret, *, issuer=EUDI_ENTRY, age=True, unique=True, nb=1000, na=2000):
    return Presentation(
        holder_secret=secret,
        issuer_entry=issuer,
        age_over_18=age,
        is_unique_person=unique,
        not_before=nb,
        not_after=na,
        transcript=b"openid4vp-redacted-transcript",
    )


def _verifier():
    return TrustedRPVerifier.from_issuer_entries({
        EUDI_ENTRY: ISSUER_CLASS_EUDI_PID,
        FALLBACK_ENTRY: ISSUER_CLASS_NON_EUDI_FALLBACK,
    })


@pytest.mark.property
def test_trusted_rp_is_a_presentation_verifier():
    assert isinstance(_verifier(), PresentationVerifier)


@pytest.mark.property
def test_admission_derives_expected_nullifier_and_pairwise():
    secret = new_holder_secret()
    adm = _verifier().verify_presentation("vbank", _presentation(secret))
    assert adm.scope_nullifier == scope_nullifier(secret, "vbank")
    _, pub = derive_pairwise_keypair(secret, "vbank")
    assert adm.holder_pairwise == pairwise_address(pub)
    assert adm.pairwise_did == f"did:pls:{pairwise_address(pub)}"
    assert adm.issuer_class == ISSUER_CLASS_EUDI_PID


@pytest.mark.property
def test_admission_carries_no_pii_fields():
    adm = _verifier().verify_presentation("vbank", _presentation(new_holder_secret()))
    fields = set(vars(adm))
    forbidden = {"name", "full_name", "dob", "date_of_birth", "national_id", "pid"}
    assert fields.isdisjoint(forbidden)


@pytest.mark.property
def test_non_eudi_fallback_issuer_is_accepted():
    secret = new_holder_secret()
    adm = _verifier().verify_presentation("vbank", _presentation(secret, issuer=FALLBACK_ENTRY))
    assert adm.issuer_class == ISSUER_CLASS_NON_EUDI_FALLBACK


@pytest.mark.property
def test_unregistered_issuer_rejected():
    with pytest.raises(NotPersonError):
        _verifier().verify_presentation(
            "vbank", _presentation(new_holder_secret(), issuer=b"rogue-issuer")
        )


@pytest.mark.property
@pytest.mark.parametrize("kwargs", [{"age": False}, {"unique": False}, {"nb": 5, "na": 5}])
def test_failed_personhood_claims_rejected(kwargs):
    with pytest.raises(NotPersonError):
        _verifier().verify_presentation("vbank", _presentation(new_holder_secret(), **kwargs))


@pytest.mark.property
def test_zk_backend_is_dependency_gated():
    with pytest.raises(NotImplementedError):
        ZkVerifier().verify_presentation("vbank", _presentation(new_holder_secret()))


@pytest.mark.property
def test_constructor_rejects_unknown_issuer_class():
    with pytest.raises(ValueError):
        TrustedRPVerifier({crypto.sha256(b"x").hex(): 99})
    with pytest.raises(ValueError):
        TrustedRPVerifier.from_issuer_entries({b"x": 99})


@pytest.mark.property
def test_admission_bridges_into_a_co_signed_anchor():
    secret = new_holder_secret()
    verifier_priv, _ = crypto.generate_keypair()
    adm = _verifier().verify_presentation("vbank", _presentation(secret))
    holder_priv, _ = derive_pairwise_keypair(secret, "vbank")
    revptr = crypto.sha256(b"random-revocation-commitment").hex()
    anchor = anchor_from_admission(adm, verifier_priv, holder_priv, revocation_pointer=revptr)
    assert anchor.verify()
    assert anchor.record["scope_nullifier"] == scope_nullifier(secret, "vbank")
    assert anchor.record["revocation_pointer"] == revptr
