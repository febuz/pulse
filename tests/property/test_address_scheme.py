"""Property tests for the versioned PLS address scheme (CRYPTO_CORPUS_STUDY §3).

Every address commits to a 1-byte signature scheme version under the ``pls1``
prefix, so a post-quantum scheme can be added later by soft-fork. These proofs
pin the encode/decode round-trip, the version byte, and graceful rejection of
malformed or unknown-scheme addresses.
"""

import base64

import pytest

from knitweb.core import crypto


@pytest.mark.property
def test_address_is_deterministic_and_prefixed():
    _, pub = crypto.generate_keypair()
    addr = crypto.address(pub)
    assert addr == crypto.address(pub)          # deterministic
    assert addr.startswith("pls1")


@pytest.mark.property
def test_address_carries_scheme_byte_zero_by_default():
    _, pub = crypto.generate_keypair()
    addr = crypto.address(pub)
    scheme, fingerprint = crypto.decode_address(addr)
    assert scheme == crypto.SCHEME_SECP256K1_ECDSA == 0
    # the fingerprint is exactly the double-SHA-256[:20] of the pubkey
    expected = crypto.sha256(crypto.sha256(bytes.fromhex(pub)))[:20]
    assert fingerprint == expected
    assert crypto.address_scheme(addr) == 0


@pytest.mark.property
def test_round_trip_decode_recovers_payload():
    _, pub = crypto.generate_keypair()
    addr = crypto.address(pub)
    scheme, fingerprint = crypto.decode_address(addr)
    assert len(fingerprint) == 20
    # re-encoding the same payload reproduces the address byte-for-byte
    payload = bytes([scheme]) + fingerprint
    reencoded = "pls1" + base64.b32encode(payload).decode().lower().rstrip("=")
    assert reencoded == addr


@pytest.mark.property
def test_distinct_keys_give_distinct_addresses():
    _, pub_a = crypto.generate_keypair()
    _, pub_b = crypto.generate_keypair()
    assert crypto.address(pub_a) != crypto.address(pub_b)


@pytest.mark.property
def test_minting_an_unknown_scheme_is_refused():
    _, pub = crypto.generate_keypair()
    with pytest.raises(ValueError):
        crypto.address(pub, scheme=1)           # reserved but not yet blessed


@pytest.mark.property
def test_is_valid_address_accepts_real_and_rejects_garbage():
    _, pub = crypto.generate_keypair()
    assert crypto.is_valid_address(crypto.address(pub))
    assert not crypto.is_valid_address("pls1")              # no payload
    assert not crypto.is_valid_address("xyz1abcdef")        # wrong prefix
    assert not crypto.is_valid_address("pls1!!!notbase32")  # bad body
    assert not crypto.is_valid_address("pls1aa")            # wrong payload length


@pytest.mark.property
def test_decode_rejects_wrong_payload_length():
    # A correct HRP with a valid-base32 body that decodes to != 21 bytes must be
    # refused by the explicit _ADDR_PAYLOAD_LEN check (not just garbage base32).
    too_long = bytes([0]) + b"x" * 21          # 22 bytes, scheme byte + 21
    addr = "pls1" + base64.b32encode(too_long).decode().lower().rstrip("=")
    with pytest.raises(ValueError):
        crypto.decode_address(addr)
    too_short = bytes([0]) + b"x" * 5           # 6 bytes
    addr2 = "pls1" + base64.b32encode(too_short).decode().lower().rstrip("=")
    with pytest.raises(ValueError):
        crypto.decode_address(addr2)


@pytest.mark.property
def test_decode_reports_unknown_scheme_without_crashing():
    # Hand-craft an address with scheme byte 9 (unknown): decode must still parse
    # it and surface the scheme, but is_valid_address must reject it.
    payload = bytes([9]) + crypto.sha256(b"x")[:20]
    addr = "pls1" + base64.b32encode(payload).decode().lower().rstrip("=")
    scheme, fp = crypto.decode_address(addr)
    assert scheme == 9 and len(fp) == 20
    assert not crypto.is_valid_address(addr)
