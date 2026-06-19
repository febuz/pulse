"""Proofs that a signed Knit is bound to its network (EIP-155-style anti-replay).

A transfer signed on one PLS web must never settle on another. Two defenses,
both proven here:
  1. the network id is inside the signed bytes, so flipping it breaks the signature;
  2. a validator only accepts Knits bearing its own network id, so even a validly
     re-signed cross-network Knit is refused.
"""

import pytest

from knitweb.ledger import knitweb as kw
from knitweb.ledger.knit import MAINNET, build, sign_from, sign_to
from knitweb.ledger.node import AccountNode

TESTNET = 2


@pytest.mark.property
def test_network_is_part_of_signed_bytes():
    a = AccountNode(genesis_balances={"PLS": 100})
    b = AccountNode()
    knit = sign_to(a.propose(b.pub, "PLS", 10, timestamp=1), b.priv)
    # default network is mainnet and the signed knit validates on mainnet
    assert knit.network == MAINNET
    ok, _ = kw.validate_knit(knit, expected_network=MAINNET)
    assert ok


@pytest.mark.property
def test_tampering_network_breaks_signature():
    a = AccountNode(genesis_balances={"PLS": 100})
    b = AccountNode()
    knit = sign_to(a.propose(b.pub, "PLS", 10, timestamp=1), b.priv)
    forged = knit.__class__(**{**knit.__dict__, "network": TESTNET})  # flip after signing
    ok, reason = kw.validate_knit(forged, expected_network=TESTNET)
    assert not ok and "signature" in reason  # sig no longer covers the new network


@pytest.mark.property
def test_validly_signed_foreign_network_knit_is_refused():
    # A Knit correctly signed by both parties for the TESTNET cannot settle on a
    # validator that expects MAINNET — pure cross-web replay protection.
    a = AccountNode(genesis_balances={"PLS": 100}, network=TESTNET)
    b = AccountNode(network=TESTNET)
    knit = sign_to(a.propose(b.pub, "PLS", 10, timestamp=1), b.priv)
    ok_home, _ = kw.validate_knit(knit, expected_network=TESTNET)
    assert ok_home                                   # valid on its own web
    ok_foreign, reason = kw.validate_knit(knit, expected_network=MAINNET)
    assert not ok_foreign and "wrong network" in reason


@pytest.mark.property
def test_cross_network_transfer_is_rejected():
    a = AccountNode(genesis_balances={"PLS": 100}, network=MAINNET)
    b = AccountNode(network=TESTNET)
    with pytest.raises(ValueError, match="network mismatch"):
        a.transfer_to(b, "PLS", 10, timestamp=1)
    assert a.balance("PLS") == 100 and b.balance("PLS") == 0  # state unchanged


@pytest.mark.property
def test_same_network_transfer_still_works():
    a = AccountNode(genesis_balances={"PLS": 100}, network=TESTNET)
    b = AccountNode(network=TESTNET)
    a.transfer_to(b, "PLS", 40, timestamp=1)
    assert a.balance("PLS") == 60 and b.balance("PLS") == 40
