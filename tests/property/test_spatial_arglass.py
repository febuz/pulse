"""Proofs for geohash spatial binding and the AR-glass interface."""

import pytest

from knitweb import sdk
from knitweb.core import crypto
from knitweb.edge.arglass import ARGlass
from knitweb.edge.runtime import EdgeVerifyError
from knitweb.fabric import spatial
from knitweb.fabric.web import Web


# --- geohash --------------------------------------------------------------

@pytest.mark.property
def test_geohash_matches_known_reference():
    # The canonical geohash example: 57.64911, 10.40744 -> u4pruydqqvj
    assert spatial.geohash(57.64911, 10.40744, 11) == "u4pruydqqvj"


@pytest.mark.property
def test_geohash_proximity_by_prefix():
    a = spatial.geohash(52.3702, 4.8952, 9)   # Amsterdam
    b = spatial.geohash(52.3705, 4.8955, 9)   # ~40m away
    c = spatial.geohash(48.8566, 2.3522, 9)   # Paris
    assert spatial.common_prefix_len(a, b) >= 6   # share a coarse cell
    assert spatial.proximate(a, b, 5)
    assert not spatial.proximate(a, c, 5)


@pytest.mark.property
def test_geohash_rejects_out_of_range():
    with pytest.raises(ValueError):
        spatial.geohash(91.0, 0.0)
    with pytest.raises(ValueError):
        spatial.geohash(0.0, 200.0)


@pytest.mark.property
def test_spatial_anchor_weaves_and_is_content_addressed():
    web = Web()
    anchor = spatial.bind(52.3702, 4.8952, target="bcid-target", precision=9,
                          altitude_m=12.0)
    cid = anchor.weave(web)
    assert cid in web.nodes
    assert anchor.alt_band == 4               # 12m // 3m bands
    # deterministic id
    assert anchor.cid == spatial.bind(52.3702, 4.8952, "bcid-target", 9, 12.0).cid


# --- AR glass -------------------------------------------------------------

def _signed_bundle():
    priv, pub = crypto.generate_keypair()
    asset = {
        "origintrail_id": 1,
        "originator": "Acme",
        "linked_sources": [
            {"type": "IFRS_File", "url": "https://ifrs.org"},
            {"type": "Image_Library", "url": "https://img.example/1"},
        ],
    }
    data, sig = sdk.compile_asset(asset, priv)
    return data, sig, pub


@pytest.mark.property
def test_arglass_keeps_near_rejects_far_and_refuses_forged():
    data, sig, pub = _signed_bundle()
    here = spatial.geohash(52.3702, 4.8952, 9)
    far = spatial.geohash(48.8566, 2.3522, 9)
    glass = ARGlass(52.3702, 4.8952, precision=7)

    assert glass.receive(data, pub, sig, anchor_geohash=here) is True   # near -> kept
    assert glass.receive(data, pub, sig, anchor_geohash=far) is False   # far -> dropped
    assert glass.bundle_count == 1

    with pytest.raises(EdgeVerifyError):
        glass.receive(bytes(data[:-1]) + bytes([data[-1] ^ 1]), pub, sig,
                      anchor_geohash=here)


@pytest.mark.property
def test_arglass_overlays_and_features():
    data, sig, pub = _signed_bundle()
    glass = ARGlass(52.3702, 4.8952)
    glass.receive(data, pub, sig)             # no anchor -> always kept
    overlays = glass.overlays()
    assert len(overlays) == 2
    assert all("originator" in o and "url" in o for o in overlays)
    feats = glass.features()
    assert feats == glass.features()          # deterministic for the inner model
