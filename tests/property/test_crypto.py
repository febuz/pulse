"""Property tests for FBR crypto: secp256k1 ECDSA + SHA-256, addresses, merkle."""

import pytest

from knitweb.core import crypto


@pytest.mark.property
def test_keypair_and_public_derivation():
    priv, pub = crypto.generate_keypair()
    assert crypto.is_valid_hex(priv, 32)
    assert crypto.is_valid_hex(pub, 33)          # compressed SEC1 point
    assert crypto.public_from_private(priv) == pub


@pytest.mark.property
def test_sign_verify_round_trip():
    priv, pub = crypto.generate_keypair()
    msg = b"transfer 100 FBR from A to B"
    sig = crypto.sign(priv, msg)
    assert crypto.verify(pub, msg, sig)


@pytest.mark.property
def test_verify_rejects_tampered_message_and_wrong_key():
    priv, pub = crypto.generate_keypair()
    _, other_pub = crypto.generate_keypair()
    msg = b"genuine"
    sig = crypto.sign(priv, msg)
    assert not crypto.verify(pub, b"tampered", sig)     # wrong message
    assert not crypto.verify(other_pub, msg, sig)       # wrong key
    assert not crypto.verify(pub, msg, "deadbeef")      # malformed sig


@pytest.mark.property
def test_address_is_deterministic_and_prefixed():
    _, pub = crypto.generate_keypair()
    addr = crypto.address(pub)
    assert addr == crypto.address(pub)
    assert addr.startswith("fbr1")


@pytest.mark.property
def test_merkle_root_properties():
    h = lambda s: crypto.sha256(s)
    leaves = [h(b"a"), h(b"b"), h(b"c")]
    root = crypto.merkle_root(leaves)
    assert isinstance(root, bytes) and len(root) == 32
    assert crypto.merkle_root(leaves) == root            # deterministic
    # order-sensitive
    assert crypto.merkle_root(list(reversed(leaves))) != root
    # empty is well-defined
    assert crypto.merkle_root([]) == crypto.sha256(b"")
    # single leaf
    assert crypto.merkle_root([h(b"only")]) == h(b"only")
