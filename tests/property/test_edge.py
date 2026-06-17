"""Proofs for the edge runtime: verify-before-trust, AR queries, model features."""

import pytest

from knitweb import sdk
from knitweb.core import crypto
from knitweb.edge.runtime import EdgeBundle, EdgeVerifyError


def _signed_bundle():
    priv, pub = crypto.generate_keypair()
    asset = {
        "origintrail_id": 42,
        "originator": "Global Finance & Media Corp",
        "linked_sources": [
            {"type": "IFRS_File", "url": "https://ifrs.org"},
            {"type": "YouTube_Video", "url": "https://youtube.com/x"},
            {"type": "Youku_Video", "url": "https://youku.com/y"},
        ],
    }
    data, sig = sdk.compile_asset(asset, priv)
    return data, sig, pub


@pytest.mark.property
def test_load_verifies_and_exposes_relations():
    data, sig, pub = _signed_bundle()
    bundle = EdgeBundle.load(data, originator_pub=pub, signature=sig)
    assert bundle.originator == "Global Finance & Media Corp"
    assert len(bundle) == 3


@pytest.mark.property
def test_tampered_bundle_is_refused():
    data, sig, pub = _signed_bundle()
    tampered = bytes(data[:-1]) + bytes([data[-1] ^ 0x01])
    with pytest.raises(EdgeVerifyError):
        EdgeBundle.load(tampered, originator_pub=pub, signature=sig)


@pytest.mark.property
def test_ar_query_by_source_type():
    data, sig, pub = _signed_bundle()
    bundle = EdgeBundle.load(data, originator_pub=pub, signature=sig)
    youku = bundle.query(source_type="Youku_Video")
    assert len(youku) == 1 and youku[0].obj == "https://youku.com/y"
    # sources_for is deterministic + complete
    subject = bundle.relations[0].subject
    srcs = bundle.sources_for(subject)
    assert srcs == sorted(srcs)
    assert len(srcs) == 3


@pytest.mark.property
def test_feature_dict_is_deterministic():
    data, sig, pub = _signed_bundle()
    b1 = EdgeBundle.load(data, originator_pub=pub, signature=sig)
    b2 = EdgeBundle.load(data, originator_pub=pub, signature=sig)
    # same bundle -> identical augmentation features for every agent
    assert b1.to_feature_dict() == b2.to_feature_dict()


@pytest.mark.property
def test_unverified_load_is_allowed_but_opt_in():
    data, _sig, _pub = _signed_bundle()
    # no key/sig -> loads unverified (trusted-local path only)
    bundle = EdgeBundle.load(data)
    assert len(bundle) == 3
