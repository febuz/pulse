"""Proofs for the Knitweb SDK facade (Wallet + synaptic helpers)."""

import pytest

from knitweb import sdk
from knitweb.core import crypto


@pytest.mark.property
def test_wallet_create_and_identity():
    w = sdk.Wallet.create(genesis_pulses=100)
    assert w.balance("PLS") == 100
    assert w.address.startswith("pls1")
    # round-trip the key
    w2 = sdk.Wallet.from_key(w.private_key)
    assert w2.public_key == w.public_key
    assert w2.address == w.address


@pytest.mark.property
def test_wallet_pay_moves_pulses_and_conserves():
    a = sdk.Wallet.create(genesis_pulses=10)
    b = sdk.Wallet.create()
    a.pay(b, 3, timestamp=1)
    assert a.balance() == 7
    assert b.balance() == 3
    assert a.balance() + b.balance() == 10  # conserved


@pytest.mark.property
def test_wallet_overpay_rejected():
    a = sdk.Wallet.create(genesis_pulses=2)
    b = sdk.Wallet.create()
    with pytest.raises(ValueError):
        a.pay(b, 3, timestamp=1)
    assert a.balance() == 2 and b.balance() == 0


@pytest.mark.property
def test_compile_verify_decode_round_trip():
    priv, pub = crypto.generate_keypair()
    asset = {
        "origintrail_id": 1,
        "originator": "Acme",
        "linked_sources": [
            {"type": "IFRS_File", "url": "https://ifrs.org"},
            {"type": "News_Article", "url": "https://news.example/a"},
        ],
    }
    data, sig = sdk.compile_asset(asset, priv)
    assert sdk.verify_bundle(pub, data, sig)
    decoded = sdk.decode_bundle(data)
    assert decoded["originator"] == "Acme"
    assert len(decoded["relations"]) == 2


@pytest.mark.property
def test_tampered_bundle_fails_verification():
    priv, pub = crypto.generate_keypair()
    asset = {"origintrail_id": 1, "originator": "Acme",
             "linked_sources": [{"type": "IFRS_File", "url": "https://ifrs.org"}]}
    data, sig = sdk.compile_asset(asset, priv)
    tampered = bytearray(data)
    tampered[-1] ^= 0x01
    assert not sdk.verify_bundle(pub, bytes(tampered), sig)
