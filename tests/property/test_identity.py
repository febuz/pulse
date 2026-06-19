"""Property tests for the node-identity proof primitive (step 1 of #58).

Pure and deterministic: no sockets, no real handshake, no async. Every test is a
plain issue -> sign -> verify flow over in-memory dataclasses, so nothing here can
block or deadlock.
"""

import pytest

from knitweb.core import canonical, crypto
from knitweb.ledger import knit
from knitweb.p2p import identity

# A canonical CBOR map begins with major type 5 (0xa0..0xbb), never 'k' (0x6b),
# the first byte of the identity domain tag.
DOMAIN_PREFIX_BYTE = identity.DOMAIN_TAG[:1]


@pytest.mark.property
def test_round_trip_returns_signer_pubkey():
    priv, pub = crypto.generate_keypair()
    challenge = identity.issue_challenge(nonce=b"\x01" * identity.NONCE_LEN)
    proof = identity.make_proof(challenge, priv)
    assert proof.pubkey == pub
    assert identity.verify_proof(challenge, proof) == pub


@pytest.mark.property
def test_replay_against_different_nonce_is_rejected():
    priv, _ = crypto.generate_keypair()
    issued = identity.issue_challenge(nonce=b"\x02" * identity.NONCE_LEN)
    proof = identity.make_proof(issued, priv)
    # A verifier holding a *different* challenge must reject the replayed proof.
    other = identity.issue_challenge(nonce=b"\x03" * identity.NONCE_LEN)
    assert other.nonce != issued.nonce
    assert identity.verify_proof(other, proof) is None


@pytest.mark.property
def test_forged_or_tampered_signature_is_rejected():
    priv, pub = crypto.generate_keypair()
    challenge = identity.issue_challenge(nonce=b"\x04" * identity.NONCE_LEN)
    good = identity.make_proof(challenge, priv)
    # Flip a byte in the DER signature hex.
    flipped = "00" if good.sig[:2] != "00" else "01"
    tampered = identity.Proof(pubkey=pub, sig=flipped + good.sig[2:])
    assert identity.verify_proof(challenge, tampered) is None
    # Outright garbage signature hex must fail too (not raise).
    assert identity.verify_proof(challenge, identity.Proof(pubkey=pub, sig="deadbeef")) is None


@pytest.mark.property
def test_pubkey_not_equal_signer_is_rejected():
    priv, _ = crypto.generate_keypair()
    _, other_pub = crypto.generate_keypair()
    challenge = identity.issue_challenge(nonce=b"\x05" * identity.NONCE_LEN)
    proof = identity.make_proof(challenge, priv)
    # Claim a different pubkey while keeping the real signature: mismatch -> None.
    mismatched = identity.Proof(pubkey=other_pub, sig=proof.sig)
    assert identity.verify_proof(challenge, mismatched) is None


@pytest.mark.property
def test_injected_nonce_is_deterministic():
    nonce = b"\x06" * identity.NONCE_LEN
    a = identity.issue_challenge(nonce=nonce)
    b = identity.issue_challenge(nonce=nonce)
    assert a == b == identity.Challenge(nonce=nonce)
    assert a.message() == identity.DOMAIN_TAG + nonce


@pytest.mark.property
def test_issue_challenge_rejects_wrong_length_nonce():
    with pytest.raises(ValueError):
        identity.issue_challenge(nonce=b"too-short")
    with pytest.raises(ValueError):
        identity.issue_challenge(nonce=b"\x00" * (identity.NONCE_LEN + 1))


@pytest.mark.property
def test_default_nonce_is_fresh_and_correct_length():
    a = identity.issue_challenge()
    b = identity.issue_challenge()
    assert len(a.nonce) == identity.NONCE_LEN
    assert a.nonce != b.nonce  # os.urandom -> overwhelmingly distinct


@pytest.mark.property
def test_domain_tag_separates_identity_proofs_from_knit_signatures():
    """A proof can't be a valid Knit signature, and vice-versa.

    Both use the same secp256k1 key, but identity signs ``DOMAIN_TAG || nonce``
    while a Knit signs canonical CBOR record bytes — disjoint message spaces, so a
    signature from one protocol never verifies under the other.
    """
    priv, pub = crypto.generate_keypair()
    _, recv = crypto.generate_keypair()

    nonce = b"\x07" * identity.NONCE_LEN
    challenge = identity.issue_challenge(nonce=nonce)
    proof = identity.make_proof(challenge, priv)

    a_knit = knit.build(
        from_pub=pub, to_pub=recv, symbol="PLS", amount=100, from_nonce=1, timestamp=0
    )
    knit_bytes = a_knit.signing_bytes

    # The two signed message spaces are byte-disjoint: the identity message starts
    # with the ASCII tag; the canonical record starts with a CBOR map header.
    id_msg = challenge.message()
    assert id_msg.startswith(b"knitweb-p2p-identity:")
    assert not knit_bytes.startswith(DOMAIN_PREFIX_BYTE)
    assert id_msg != knit_bytes

    # The identity proof's signature does NOT verify as a Knit signature.
    assert not crypto.verify(pub, knit_bytes, proof.sig)
    # A real Knit signature does NOT satisfy verify_proof.
    knit_sig = crypto.sign(priv, knit_bytes)
    assert identity.verify_proof(challenge, identity.Proof(pubkey=pub, sig=knit_sig)) is None


@pytest.mark.property
def test_no_canonical_or_signed_record_bytes_touched():
    """Byte-identity: a fresh Knit's CID is unchanged — identity is transport-level.

    Importing/using the identity primitive must not alter how canonical records or
    Knits are encoded. We pin a deterministic Knit's CID across an issue/sign/verify
    cycle to prove the proof primitive touches no signed/canonical bytes.
    """
    priv, pub = crypto.generate_keypair()
    _, recv = crypto.generate_keypair()
    a_knit = knit.build(
        from_pub=pub, to_pub=recv, symbol="PLS", amount=42, from_nonce=7, timestamp=0
    )
    cid_before = a_knit.id
    record_bytes = canonical.encode(a_knit.to_record())

    challenge = identity.issue_challenge(nonce=b"\x08" * identity.NONCE_LEN)
    proof = identity.make_proof(challenge, priv)
    assert identity.verify_proof(challenge, proof) == pub

    # Rebuilding the identical Knit yields the identical CID and canonical bytes.
    again = knit.build(
        from_pub=pub, to_pub=recv, symbol="PLS", amount=42, from_nonce=7, timestamp=0
    )
    assert again.id == cid_before
    assert canonical.encode(again.to_record()) == record_bytes
