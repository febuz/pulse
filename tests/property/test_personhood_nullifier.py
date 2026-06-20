"""Proofs for the scope nullifier + pairwise DID: determinism, uniqueness, unlinkability."""

import pytest

from knitweb.core import crypto
from knitweb.personhood import build_anchor_record
from knitweb.personhood.nullifier import (
    SECRET_BYTES,
    new_holder_secret,
    scope_nullifier,
)
from knitweb.personhood.pairwise import (
    derive_pairwise_keypair,
    pairwise_address,
    pairwise_did,
)
from knitweb.personhood.records import ISSUER_CLASS_EUDI_PID


@pytest.mark.property
def test_nullifier_is_deterministic_and_32_bytes():
    secret = b"\x01" * SECRET_BYTES
    n1 = scope_nullifier(secret, "vbank")
    n2 = scope_nullifier(secret, "vbank")
    assert n1 == n2
    assert crypto.is_valid_hex(n1, 32)


@pytest.mark.property
def test_same_person_different_scope_is_unlinkable():
    secret = new_holder_secret()
    a = scope_nullifier(secret, "referendum-2026")
    b = scope_nullifier(secret, "crowdfund-bridge")
    assert a != b  # cross-scope nullifiers do not correlate


@pytest.mark.property
def test_different_people_same_scope_differ():
    s1, s2 = new_holder_secret(), new_holder_secret()
    assert scope_nullifier(s1, "vbank") != scope_nullifier(s2, "vbank")


@pytest.mark.property
def test_double_register_in_scope_yields_same_nullifier():
    # The basis for gate-level AlreadyRegistered detection: one person, one nullifier/scope.
    secret = new_holder_secret()
    assert scope_nullifier(secret, "vbank") == scope_nullifier(secret, "vbank")


@pytest.mark.property
@pytest.mark.parametrize("bad_secret", [b"", b"\x00" * 31, b"\x00" * 33, "not-bytes"])
def test_bad_secret_length_rejected(bad_secret):
    with pytest.raises(ValueError):
        scope_nullifier(bad_secret, "vbank")


@pytest.mark.property
def test_empty_scope_rejected():
    with pytest.raises(ValueError):
        scope_nullifier(new_holder_secret(), "")


@pytest.mark.property
def test_pairwise_keypair_is_deterministic_per_scope():
    secret = new_holder_secret()
    p1 = derive_pairwise_keypair(secret, "vbank")
    p2 = derive_pairwise_keypair(secret, "vbank")
    assert p1 == p2


@pytest.mark.property
def test_pairwise_keys_distinct_across_scopes():
    secret = new_holder_secret()
    _, pub_a = derive_pairwise_keypair(secret, "scope-a")
    _, pub_b = derive_pairwise_keypair(secret, "scope-b")
    assert pub_a != pub_b
    assert pairwise_address(pub_a) != pairwise_address(pub_b)


@pytest.mark.property
def test_scope_a_signature_does_not_verify_under_scope_b_did():
    secret = new_holder_secret()
    priv_a, pub_a = derive_pairwise_keypair(secret, "scope-a")
    _, pub_b = derive_pairwise_keypair(secret, "scope-b")
    msg = b"ballot:choice=yes"
    sig = crypto.sign(priv_a, msg)
    assert crypto.verify(pub_a, msg, sig)
    assert not crypto.verify(pub_b, msg, sig)  # cross-scope identities are independent
    assert pairwise_did(pub_a) == f"did:pls:{pairwise_address(pub_a)}"


@pytest.mark.property
def test_pairwise_rejection_samples_out_of_range_scalar(monkeypatch):
    # Force the ~2^-128 edge: first digest is the curve order (invalid scalar) -> must retry.
    from knitweb.personhood import pairwise as pw

    n = pw._SECP256K1_ORDER
    calls = {"i": 0}
    real = crypto.sha256

    def fake(data):
        calls["i"] += 1
        if calls["i"] == 1:
            return n.to_bytes(32, "big")  # == order: not a valid scalar, forces a retry
        return real(data)

    monkeypatch.setattr(pw.crypto, "sha256", fake)
    priv, _pub = pw.derive_pairwise_keypair(b"\x07" * 32, "vbank")
    assert 0 < int(priv, 16) < n
    assert calls["i"] >= 2  # it rehashed past the invalid scalar


@pytest.mark.property
def test_nullifier_and_pairwise_compose_into_a_valid_anchor():
    # End-to-end: holder derivations feed straight into the anchor schema.
    verifier_priv, verifier_pub = crypto.generate_keypair()
    secret = new_holder_secret()
    scope = "vbank"
    _, holder_pub = derive_pairwise_keypair(secret, scope)
    record = build_anchor_record(
        verifier=crypto.address(verifier_pub),
        holder_pairwise=pairwise_address(holder_pub),
        issuer_trust_anchor=crypto.sha256(b"eu-tl").hex(),
        issuer_class=ISSUER_CLASS_EUDI_PID,
        scope=scope,
        scope_nullifier=scope_nullifier(secret, scope),
        not_before=1,
        not_after=2,
        revocation_pointer=crypto.sha256(b"r").hex(),
        proof_digest=crypto.sha256(b"p").hex(),
    )
    assert record["pairwise_did"] == pairwise_did(holder_pub)
