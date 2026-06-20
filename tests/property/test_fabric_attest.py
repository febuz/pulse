"""Proofs for fabric item attestation (signed, attributable claims)."""

import pytest

from knitweb.core import crypto
from knitweb.fabric.attest import attest, verify_record
from knitweb.fabric.items import KnowledgeItem, ResourceItem, web_state_root
from knitweb.fabric.web import Web


def _authored_knowledge():
    priv, pub = crypto.generate_keypair()
    addr = crypto.address(pub)
    item = KnowledgeItem(title="fibers conserve mass", body="...", author=addr,
                         tags=("physics", "ledger"))
    return priv, pub, item


@pytest.mark.property
def test_attest_and_verify_knowledge_item():
    priv, pub, item = _authored_knowledge()
    att = attest(item.to_record(), priv, author_field="author")
    assert att.verify(author_field="author")
    assert att.cid == item.cid            # signature is outside the content id


@pytest.mark.property
def test_tampered_record_fails_verification():
    priv, pub, item = _authored_knowledge()
    att = attest(item.to_record(), priv, author_field="author")
    forged = dict(att.record, body="rewritten")
    assert not verify_record(forged, att.author_pub, att.sig, "author")


@pytest.mark.property
def test_cannot_attest_under_someone_elses_address():
    # author field points at a different key than the signer -> refused
    _, other_pub = crypto.generate_keypair()
    signer_priv, _ = crypto.generate_keypair()
    record = KnowledgeItem(title="t", body="b",
                           author=crypto.address(other_pub)).to_record()
    with pytest.raises(ValueError):
        attest(record, signer_priv, author_field="author")


@pytest.mark.property
def test_resource_item_provider_attestation():
    priv, pub = crypto.generate_keypair()
    res = ResourceItem(resource_kind="gpu", capacity=24, price_per_epoch=5,
                       provider=crypto.address(pub))
    att = attest(res.to_record(), priv, author_field="provider")
    assert att.verify(author_field="provider")
    # wrong field name -> address mismatch -> fails
    assert not verify_record(res.to_record(), pub, att.sig, "author")


@pytest.mark.property
def test_verify_record_returns_false_on_malformed_pubkey_or_record():
    # must reject (not raise) so audit/boolean callers over wire envelopes get a clean False
    priv, pub, item = _authored_knowledge()
    att = attest(item.to_record(), priv, author_field="author")
    assert verify_record(att.record, "not-hex!!", att.sig, "author") is False
    assert verify_record(att.record, "abc", att.sig, "author") is False     # odd-length hex
    assert verify_record([1, 2, 3], att.author_pub, att.sig, "author") is False  # non-dict


@pytest.mark.property
def test_web_state_root_leaves_are_fixed_width():
    web = Web()
    web.weave({"n": "only"})
    root = web_state_root(web)
    # 32-byte digest -> 64 hex chars even for a single node (the fix)
    assert len(root) == 64
