"""Proofs for ``verify_anchor`` — the provenance-explorer verify primitive (issue #13).

``verify_anchor`` answers, for an independent auditor, both halves of a provenance
claim at once: the receipt is an authentic notary signature, *and* its ``state_root``
still matches the root recomputed from the thing it anchored — either the live
``Web`` (re-derived now) or a recorded ``FabricCheckpoint``. It returns a structured
result rather than a bare bool, and never raises on a mismatch.
"""

import pytest

from knitweb.anchor import LocalAnchorBackend, Notary, verify_anchor
from knitweb.anchor.origintrail import OriginTrailAnchorBackend
from knitweb.core import crypto
from knitweb.core.pulse import Pulse
from knitweb.fabric.items import checkpoint, web_state_root
from knitweb.fabric.web import Web


def _web(payload=None):
    web = Web()
    web.weave(payload or {"x": 1})
    return web


def _checkpoint(web, epoch_ts=120):
    pulse = Pulse(interval_s=60, genesis_ts=0)
    beat = pulse.beat(timestamp=epoch_ts, state_root=web_state_root(web))
    return checkpoint(web, beat)


@pytest.mark.property
def test_verify_anchor_against_live_web_passes():
    priv, _ = crypto.generate_keypair()
    web = _web()
    cp = _checkpoint(web)
    receipt = Notary(priv).anchor(cp, LocalAnchorBackend(), timestamp=200)

    result = verify_anchor(receipt, web)
    assert result["verified"] is True
    assert result["signature_ok"] is True
    assert result["root_match"] is True
    assert result["state_root"] == cp.state_root == web_state_root(web)
    assert result["covered_root"] == receipt.state_root
    assert result["target"] == "local"
    assert result["external_ref"] == receipt.external_ref


@pytest.mark.property
def test_verify_anchor_against_checkpoint_passes():
    priv, _ = crypto.generate_keypair()
    web = _web()
    cp = _checkpoint(web)
    receipt = Notary(priv).anchor(cp, OriginTrailAnchorBackend(), timestamp=200)

    result = verify_anchor(receipt, cp)
    assert result["verified"] is True
    assert result["state_root"] == cp.state_root
    assert result["target"] == "origintrail"


@pytest.mark.property
def test_tampering_the_web_after_anchoring_fails_root_match():
    priv, _ = crypto.generate_keypair()
    web = _web()
    cp = _checkpoint(web)
    receipt = Notary(priv).anchor(cp, LocalAnchorBackend(), timestamp=200)
    assert verify_anchor(receipt, web)["verified"] is True

    # Mutate the web after the anchor was taken: the live root moves on.
    web.weave({"injected": "after-the-fact"})

    result = verify_anchor(receipt, web)
    assert result["verified"] is False
    assert result["signature_ok"] is True        # the receipt itself is still authentic
    assert result["root_match"] is False         # but it no longer covers this web
    assert result["state_root"] == web_state_root(web)
    assert result["state_root"] != result["covered_root"]


@pytest.mark.property
def test_tampered_receipt_fails_signature():
    priv, _ = crypto.generate_keypair()
    web = _web()
    cp = _checkpoint(web)
    receipt = Notary(priv).anchor(cp, LocalAnchorBackend(), timestamp=200)

    forged = receipt.__class__(**{**receipt.__dict__, "state_root": "deadbeef"})
    result = verify_anchor(forged, web)
    assert result["verified"] is False
    assert result["signature_ok"] is False       # signature no longer matches the record


@pytest.mark.property
def test_receipt_does_not_cover_a_different_checkpoint():
    priv, _ = crypto.generate_keypair()
    web_a = _web({"a": 1})
    web_b = _web({"b": 2})
    cp_a = _checkpoint(web_a, epoch_ts=120)
    cp_b = _checkpoint(web_b, epoch_ts=180)
    receipt = Notary(priv).anchor(cp_a, LocalAnchorBackend(), timestamp=200)

    assert verify_anchor(receipt, cp_a)["verified"] is True
    assert verify_anchor(receipt, cp_b)["verified"] is False  # different root + binding


@pytest.mark.property
def test_verify_anchor_rejects_unsupported_target_type():
    priv, _ = crypto.generate_keypair()
    cp = _checkpoint(_web())
    receipt = Notary(priv).anchor(cp, LocalAnchorBackend(), timestamp=200)
    with pytest.raises(TypeError, match="Web or a FabricCheckpoint"):
        verify_anchor(receipt, "not-a-web")
