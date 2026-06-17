"""Proofs for checkpoint anchoring: notary-signed receipts that bind a checkpoint.

A receipt must verify against the checkpoint it anchored, be unforgeable (tampering
breaks the signature), and be impossible to re-point at a different checkpoint.
"""

import pytest

from knitweb.anchor import LocalAnchorBackend, Notary, verify_receipt
from knitweb.core import crypto
from knitweb.core.pulse import Pulse
from knitweb.fabric.items import checkpoint, web_state_root
from knitweb.fabric.web import Web


def _checkpoint(epoch_ts: int = 120, payload=None):
    web = Web()
    web.weave(payload or {"x": 1})
    pulse = Pulse(interval_s=60, genesis_ts=0)
    beat = pulse.beat(timestamp=epoch_ts, state_root=web_state_root(web))
    return checkpoint(web, beat)


@pytest.mark.property
def test_anchor_produces_a_verifiable_receipt():
    priv, _ = crypto.generate_keypair()
    notary = Notary(priv)
    cp = _checkpoint()
    receipt = notary.anchor(cp, LocalAnchorBackend(), timestamp=200)
    assert receipt.target == "local"
    assert receipt.notary == notary.address
    assert receipt.verify()
    assert verify_receipt(receipt, cp)
    assert receipt.state_root == cp.state_root and receipt.epoch == cp.epoch


@pytest.mark.property
def test_local_backend_ref_is_deterministic():
    backend = LocalAnchorBackend()
    cp = _checkpoint()
    r1 = backend.submit(cp.state_root, 200)
    r2 = backend.submit(cp.state_root, 200)
    assert r1 == r2                                  # reproducible external ref
    assert backend.submit(cp.state_root, 201) != r1  # timestamp-sensitive


@pytest.mark.property
def test_tampered_receipt_fails_verification():
    priv, _ = crypto.generate_keypair()
    cp = _checkpoint()
    receipt = Notary(priv).anchor(cp, LocalAnchorBackend(), timestamp=200)
    forged = receipt.__class__(**{**receipt.__dict__, "state_root": "deadbeef"})
    assert not forged.verify()                       # signature no longer matches
    assert not verify_receipt(forged, cp)


@pytest.mark.property
def test_receipt_cannot_be_repointed_to_another_checkpoint():
    priv, _ = crypto.generate_keypair()
    cp_a = _checkpoint(epoch_ts=120, payload={"a": 1})
    cp_b = _checkpoint(epoch_ts=180, payload={"b": 2})
    receipt = Notary(priv).anchor(cp_a, LocalAnchorBackend(), timestamp=200)
    assert verify_receipt(receipt, cp_a)             # binds its own checkpoint
    assert not verify_receipt(receipt, cp_b)         # not another one


@pytest.mark.property
def test_forged_notary_identity_is_rejected():
    priv, _ = crypto.generate_keypair()
    cp = _checkpoint()
    receipt = Notary(priv).anchor(cp, LocalAnchorBackend(), timestamp=200)
    _, other_pub = crypto.generate_keypair()
    relabeled = receipt.__class__(**{**receipt.__dict__, "notary_pub": other_pub})
    assert not relabeled.verify()                    # notary addr no longer derives


@pytest.mark.property
def test_receipt_record_is_canonical():
    from knitweb.core import canonical
    priv, _ = crypto.generate_keypair()
    cp = _checkpoint()
    receipt = Notary(priv).anchor(cp, LocalAnchorBackend(), timestamp=200)
    assert canonical.decode(canonical.encode(receipt.to_record())) == receipt.to_record()
