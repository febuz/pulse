"""Proofs for the OriginTrail anchor backend: content-derived UAL + verifiable receipt."""

import pytest

from knitweb.anchor import Notary, verify_receipt
from knitweb.anchor.origintrail import OriginTrailAnchorBackend, ual
from knitweb.core import crypto
from knitweb.core.pulse import Pulse
from knitweb.fabric.items import checkpoint, web_state_root
from knitweb.fabric.web import Web


def _checkpoint(epoch_ts=120, payload=None):
    web = Web()
    web.weave(payload or {"x": 1})
    pulse = Pulse(interval_s=60, genesis_ts=0)
    beat = pulse.beat(timestamp=epoch_ts, state_root=web_state_root(web))
    return checkpoint(web, beat)


@pytest.mark.property
def test_ual_is_deterministic_and_commits_the_root():
    cp = _checkpoint()
    backend = OriginTrailAnchorBackend()
    u1 = backend.submit(cp.state_root, 200)
    u2 = backend.submit(cp.state_root, 200)
    assert u1 == u2 == ual(cp.state_root, 200)         # content-derived, reproducible
    assert u1.startswith("did:dkg:knitweb/")
    # different root or time -> different UAL
    other = _checkpoint(payload={"y": 2})
    assert backend.submit(other.state_root, 200) != u1
    assert backend.submit(cp.state_root, 201) != u1


@pytest.mark.property
def test_published_assertion_is_resolvable_and_carries_the_root():
    cp = _checkpoint()
    backend = OriginTrailAnchorBackend()
    u = backend.submit(cp.state_root, 200)
    asset = backend.resolve(u)
    assert asset is not None
    assert asset["stateRoot"] == cp.state_root
    assert asset["@type"] == "KnitwebCheckpointAnchor"
    assert backend.resolve("did:dkg:knitweb/nonexistent") is None


@pytest.mark.property
def test_notary_anchors_to_origintrail_with_verifiable_receipt():
    priv, _ = crypto.generate_keypair()
    cp = _checkpoint()
    backend = OriginTrailAnchorBackend()
    receipt = Notary(priv).anchor(cp, backend, timestamp=200)
    assert receipt.target == "origintrail"
    assert receipt.external_ref == ual(cp.state_root, 200)   # verifier can recompute it
    assert receipt.verify()
    assert verify_receipt(receipt, cp)
    # the receipt's ref resolves to the published assertion committing this root
    assert backend.resolve(receipt.external_ref)["stateRoot"] == cp.state_root
