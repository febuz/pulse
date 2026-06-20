"""Proofs for the personhood-anchor schema: anti-PII whitelist + co-signature.

The anchor is the irreversible part of the design — the *only* identity data the fabric
ever carries. These tests pin the two properties that make it safe: a deny-by-default
whitelist that rejects any field that could be PII, and a co-signature (verifier +
holder pairwise key) kept outside the content id.
"""

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.attest import verify_record
from knitweb.personhood import records
from knitweb.personhood.anchor import co_sign_anchor
from knitweb.personhood.records import (
    ANCHOR_KIND,
    ISSUER_CLASS_EUDI_PID,
    PersonhoodSchemaError,
    assert_personhood_record_shape,
    build_anchor_record,
)


def _hex32(seed: bytes) -> str:
    return crypto.sha256(seed).hex()


def _valid_anchor():
    """Return (verifier_priv, holder_priv, record) for a well-formed anchor."""
    verifier_priv, verifier_pub = crypto.generate_keypair()
    holder_priv, holder_pub = crypto.generate_keypair()
    record = build_anchor_record(
        verifier=crypto.address(verifier_pub),
        holder_pairwise=crypto.address(holder_pub),
        issuer_trust_anchor=_hex32(b"eu-trusted-list-entry"),
        issuer_class=ISSUER_CLASS_EUDI_PID,
        scope="vbank",
        scope_nullifier=_hex32(b"nullifier"),
        not_before=1_000,
        not_after=2_000,
        revocation_pointer=_hex32(b"revptr"),
        proof_digest=_hex32(b"presentation"),
    )
    return verifier_priv, holder_priv, record


@pytest.mark.property
def test_valid_anchor_co_signs_and_verifies():
    verifier_priv, holder_priv, record = _valid_anchor()
    anchor = co_sign_anchor(record, verifier_priv, holder_priv)
    assert anchor.verify()
    # both signatures live outside the content id
    assert anchor.cid == canonical.cid(record)


@pytest.mark.property
@pytest.mark.parametrize("pii_field", ["full_name", "date_of_birth", "national_id", "address_line"])
def test_pii_field_is_hard_rejected(pii_field):
    _, _, record = _valid_anchor()
    leaked = dict(record)
    leaked[pii_field] = "Jane Doe"
    with pytest.raises(PersonhoodSchemaError):
        assert_personhood_record_shape(leaked, kind=ANCHOR_KIND)


@pytest.mark.property
def test_float_in_int_field_is_rejected():
    verifier_priv, verifier_pub = crypto.generate_keypair()
    _, holder_pub = crypto.generate_keypair()
    with pytest.raises(PersonhoodSchemaError):
        build_anchor_record(
            verifier=crypto.address(verifier_pub),
            holder_pairwise=crypto.address(holder_pub),
            issuer_trust_anchor=_hex32(b"a"),
            issuer_class=ISSUER_CLASS_EUDI_PID,
            scope="vbank",
            scope_nullifier=_hex32(b"n"),
            not_before=1.0,  # float — refused before any signature
            not_after=2_000,
            revocation_pointer=_hex32(b"r"),
            proof_digest=_hex32(b"p"),
        )


@pytest.mark.property
def test_bool_is_not_accepted_as_enum_int():
    _, _, record = _valid_anchor()
    bad = dict(record, issuer_class=True)  # bool is not a valid enum int
    with pytest.raises(PersonhoodSchemaError):
        assert_personhood_record_shape(bad, kind=ANCHOR_KIND)


@pytest.mark.property
def test_pairwise_did_must_match_holder_key():
    _, _, record = _valid_anchor()
    bad = dict(record, pairwise_did="did:pls:somethingelse")
    with pytest.raises(PersonhoodSchemaError):
        assert_personhood_record_shape(bad, kind=ANCHOR_KIND)


@pytest.mark.property
def test_missing_required_field_rejected():
    _, _, record = _valid_anchor()
    incomplete = {k: v for k, v in record.items() if k != "scope_nullifier"}
    with pytest.raises(PersonhoodSchemaError):
        assert_personhood_record_shape(incomplete, kind=ANCHOR_KIND)


@pytest.mark.property
def test_attestation_envelope_key_rejected():
    _, _, record = _valid_anchor()
    leaked = dict(record, sig="deadbeef")  # signatures belong outside the record
    with pytest.raises(PersonhoodSchemaError):
        assert_personhood_record_shape(leaked, kind=ANCHOR_KIND)


@pytest.mark.property
def test_cannot_co_sign_under_someone_elses_verifier_address():
    _, other_verifier_pub = crypto.generate_keypair()
    signer_priv, _ = crypto.generate_keypair()
    holder_priv, holder_pub = crypto.generate_keypair()
    record = build_anchor_record(
        verifier=crypto.address(other_verifier_pub),  # not the signer
        holder_pairwise=crypto.address(holder_pub),
        issuer_trust_anchor=_hex32(b"a"),
        issuer_class=ISSUER_CLASS_EUDI_PID,
        scope="vbank",
        scope_nullifier=_hex32(b"n"),
        not_before=1,
        not_after=2,
        revocation_pointer=_hex32(b"r"),
        proof_digest=_hex32(b"p"),
    )
    with pytest.raises(ValueError):
        co_sign_anchor(record, signer_priv, holder_priv)


@pytest.mark.property
def test_tampered_record_fails_co_signature():
    verifier_priv, holder_priv, record = _valid_anchor()
    anchor = co_sign_anchor(record, verifier_priv, holder_priv)
    forged = dict(anchor.record, scope_nullifier=_hex32(b"swapped"))
    assert not verify_record(forged, anchor.verifier_att.author_pub, anchor.verifier_att.sig, "verifier")
    assert not verify_record(forged, anchor.holder_att.author_pub, anchor.holder_att.sig, "holder_pairwise")


@pytest.mark.property
def test_anchor_cid_is_field_order_independent():
    _, _, record = _valid_anchor()
    reordered = {k: record[k] for k in reversed(list(record.keys()))}
    assert canonical.cid(reordered) == canonical.cid(record)


@pytest.mark.property
def test_verifier_and_holder_must_be_distinct_keys():
    # A self-co-signed anchor (one key in both roles) would not prove holder consent.
    priv, pub = crypto.generate_keypair()
    addr = crypto.address(pub)
    with pytest.raises(PersonhoodSchemaError):
        build_anchor_record(
            verifier=addr,
            holder_pairwise=addr,  # same key for both roles -> refused at the schema layer
            issuer_trust_anchor=_hex32(b"a"),
            issuer_class=ISSUER_CLASS_EUDI_PID,
            scope="vbank",
            scope_nullifier=_hex32(b"n"),
            not_before=1,
            not_after=2,
            revocation_pointer=_hex32(b"r"),
            proof_digest=_hex32(b"p"),
        )


@pytest.mark.property
def test_revoke_record_round_trips_and_rejects_pii():
    verifier_priv, verifier_pub = crypto.generate_keypair()
    revoke = records.build_revoke_record(
        verifier=crypto.address(verifier_pub),
        scope="vbank",
        revocation_pointer=_hex32(b"revptr"),
        revoked_at=1_234,
        reason_code=records.REASON_ART17_ERASURE,
    )
    assert revoke["kind"] == records.REVOKE_KIND
    leaked = dict(revoke, reason_text="person deceased")
    with pytest.raises(PersonhoodSchemaError):
        assert_personhood_record_shape(leaked, kind=records.REVOKE_KIND)
