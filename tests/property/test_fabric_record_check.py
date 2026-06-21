"""Lens-facing full record audit (knitweb/pulse#154).

``check_record`` is the single helper an external interpret/retrieval tool calls
to validate a signed, content-addressed record off the wire. It must accept a
genuine record and reject — with a precise, stable ``reason`` — a tampered record
(CID recomputation), a wrong author key, a bad signature, and a non-canonical
(float) field, never raising on hostile input.
"""

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.attest import attest, check_record
from knitweb.fabric.items import KnowledgeItem


def _signed_record():
    priv, pub = crypto.generate_keypair()
    addr = crypto.address(pub)
    record = KnowledgeItem(title="fibers conserve mass", body="...",
                           author=addr, tags=("physics", "ledger")).to_record()
    att = attest(record, priv, author_field="author")
    return priv, addr, record, att, canonical.cid(record)


@pytest.mark.property
def test_genuine_record_passes_and_is_truthy():
    _, _, record, att, cid = _signed_record()
    res = check_record(record, cid, att.author_pub, att.sig)
    assert res.ok is True
    assert res.reason == "ok"
    assert bool(res) is True


@pytest.mark.property
def test_tampered_record_fails_on_cid_recomputation():
    _, _, record, att, cid = _signed_record()
    forged = dict(record, body="rewritten")          # same advertised cid, different bytes
    res = check_record(forged, cid, att.author_pub, att.sig)
    assert not res
    assert res.reason == "cid-mismatch"


@pytest.mark.property
def test_wrong_author_key_is_rejected():
    _, _, record, att, cid = _signed_record()
    _, other_pub = crypto.generate_keypair()
    res = check_record(record, cid, other_pub, att.sig)
    assert not res
    assert res.reason == "author-mismatch"


@pytest.mark.property
def test_bad_signature_is_rejected():
    priv, _, record, att, cid = _signed_record()
    wrong_sig = crypto.sign(priv, b"a different message")  # valid sig, wrong bytes
    res = check_record(record, cid, att.author_pub, wrong_sig)
    assert not res
    assert res.reason == "bad-signature"


@pytest.mark.property
def test_float_field_is_rejected_as_non_canonical():
    _, addr, _, att, _ = _signed_record()
    floaty = {"author": addr, "amount": 1.5}           # floats are forbidden
    res = check_record(floaty, "bany-cid", att.author_pub, att.sig)
    assert not res
    assert res.reason == "non-canonical-record"


@pytest.mark.property
def test_malformed_inputs_never_raise():
    _, _, record, att, cid = _signed_record()
    assert check_record([1, 2, 3], cid, att.author_pub, att.sig).reason == "record-not-a-dict"
    assert check_record(record, cid, "not-hex!!", att.sig).reason == "bad-author-pub"
    assert check_record(record, cid, "abc", att.sig).reason == "bad-author-pub"  # odd-length hex
