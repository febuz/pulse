"""Proofs for the Fiber Synaptic Compiler: determinism, round-trip, provenance.

These pin the USP: relations compile to compact, deterministic, reversible,
signed bytecode that is much smaller than the source JSON.
"""

import json

import pytest

from knitweb.core import crypto
from knitweb.synaptic import bytecode as bc
from knitweb.synaptic.origintrail import resolve_asset


def _sample_relations():
    return [
        bc.Relation("asset:99482", "hasSource:IFRS_File", "https://ifrs.org", "IFRS_File"),
        bc.Relation("asset:99482", "hasSource:YouTube_Video", "https://youtube.com/x", "YouTube_Video"),
        bc.Relation("asset:99482", "hasSource:Youku_Video", "https://youku.com/y", "Youku_Video"),
        bc.Relation("asset:99482", "hasSource:RuTube_Video", "https://rutube.ru/z", "RuTube_Video"),
    ]


@pytest.mark.property
def test_compile_is_deterministic_regardless_of_order():
    rels = _sample_relations()
    a = bc.compile_bundle("bcid", "Global Finance Corp", rels)
    b = bc.compile_bundle("bcid", "Global Finance Corp", list(reversed(rels)))
    assert a == b                       # order-independent -> content-addressable
    assert bc.bundle_digest(a) == bc.bundle_digest(b)


@pytest.mark.property
def test_round_trip_reconstructs_relations():
    rels = _sample_relations()
    data = bc.compile_bundle("bcid", "Global Finance Corp", rels)
    decoded = bc.decode_bundle(data)
    assert decoded["asset_cid"] == "bcid"
    assert decoded["originator"] == "Global Finance Corp"
    # Same set of relations (order is canonicalized on compile).
    assert set((r.subject, r.predicate, r.obj, r.source_type, r.weight)
               for r in decoded["relations"]) == \
           set((r.subject, r.predicate, r.obj, r.source_type, r.weight)
               for r in rels)


@pytest.mark.property
def test_bytecode_is_much_smaller_than_json():
    rels = _sample_relations()
    data = bc.compile_bundle("bcid", "Global Finance Corp", rels)
    json_size = len(json.dumps([r.__dict__ for r in rels]).encode("utf-8"))
    # Compact enough for BLE/edge transmission: strictly smaller than the JSON.
    assert len(data) < json_size


@pytest.mark.property
def test_provenance_signature_round_trip():
    priv, pub = crypto.generate_keypair()
    data = bc.compile_bundle("bcid", "Verified Originator", _sample_relations())
    sig = bc.sign_bundle(priv, data)
    assert bc.verify_bundle(pub, data, sig)
    # Tampered bytecode fails verification (edge device would refuse to execute).
    tampered = bytearray(data)
    tampered[-1] ^= 0x01
    assert not bc.verify_bundle(pub, bytes(tampered), sig)


@pytest.mark.property
def test_bad_magic_is_rejected():
    with pytest.raises(bc.BytecodeError):
        bc.decode_bundle(b"XXXX\x01")


@pytest.mark.property
def test_origintrail_symbiosis_linked_sources():
    asset = {
        "origintrail_id": 99482,
        "originator": "Global Finance & Media Corp",
        "linked_sources": [
            {"type": "IFRS_File", "url": "https://ifrs.org"},
            {"type": "YouTube_Video", "url": "https://youtube.com"},
            {"type": "Youku_Video", "url": "https://youku.com"},
            {"type": "RuTube_Video", "url": "https://rutube.ru"},
        ],
    }
    asset_id, originator, relations = resolve_asset(asset)
    assert asset_id == "99482"
    assert originator == "Global Finance & Media Corp"
    assert len(relations) == 4
    data = bc.compile_bundle(asset_id, originator, relations)
    assert bc.decode_bundle(data)["originator"] == originator


@pytest.mark.property
def test_origintrail_explicit_triples():
    asset = {
        "@id": "ual:123",
        "originator": "News Desk",
        "@graph": [
            {"subject": "story:1", "predicate": "reportedBy", "object": "agency:reuters",
             "type": "News_Article"},
            {"subject": "story:1", "predicate": "depicts", "object": "img:42",
             "type": "Image_Library"},
        ],
    }
    asset_id, originator, relations = resolve_asset(asset)
    assert asset_id == "ual:123"
    assert len(relations) == 2
    assert {r.predicate for r in relations} == {"reportedBy", "depicts"}


@pytest.mark.property
def test_compile_bundle_rejects_missing_asset_or_originator_metadata():
    rels = _sample_relations()
    with pytest.raises(bc.BytecodeError):
        bc.compile_bundle("", "Acme", rels)
    with pytest.raises(bc.BytecodeError):
        bc.compile_bundle("asset-id", "", rels)


# ---------------------------------------------------------------------------
# Regression lock (#131): the signed-bundle presence guard.
#
# The compiler must refuse to produce a bundle whose provenance fields are
# missing or non-string. These tests pin that guard so a future refactor that
# silently drops it is caught, and pin the canonical byte identity of a fixed
# bundle so the content-addressed digest can never drift for a given record.
# ---------------------------------------------------------------------------

# Fixed byte-identity fixture: fixed asset_cid, fixed originator, and FIXED
# INTEGER relation weights (independent of any quantize derivation), so the
# canonical compiled bytes — and therefore the digest — are pinned forever.
_PIN_ASSET_CID = "bcid"
_PIN_ORIGINATOR = "Global Finance Corp"
_PIN_RELATIONS = [
    bc.Relation("asset:99482", "hasSource:IFRS_File", "https://ifrs.org", "IFRS_File", 3),
    bc.Relation("asset:99482", "hasSource:YouTube_Video", "https://youtube.com/x", "YouTube_Video", 5),
    bc.Relation("asset:99482", "hasSource:Youku_Video", "https://youku.com/y", "Youku_Video", 2),
    bc.Relation("asset:99482", "hasSource:RuTube_Video", "https://rutube.ru/z", "RuTube_Video", 7),
]
_PIN_DIGEST = "bd18ba8776eee25b81a7446d99cc506f262aa62869f85eef05e1c85487f0357e"


@pytest.mark.property
def test_compile_bundle_rejects_empty_asset_cid():
    with pytest.raises(bc.BytecodeError):
        bc.compile_bundle("", _PIN_ORIGINATOR, _sample_relations())


@pytest.mark.property
def test_compile_bundle_rejects_none_asset_cid():
    # None is "not isinstance(asset_cid, str)" -> guard raises BytecodeError,
    # never an unguarded TypeError leaking out of the compiler.
    with pytest.raises(bc.BytecodeError):
        bc.compile_bundle(None, _PIN_ORIGINATOR, _sample_relations())


@pytest.mark.property
def test_compile_bundle_rejects_empty_originator():
    with pytest.raises(bc.BytecodeError):
        bc.compile_bundle(_PIN_ASSET_CID, "", _sample_relations())


@pytest.mark.property
def test_compile_bundle_rejects_none_originator():
    with pytest.raises(bc.BytecodeError):
        bc.compile_bundle(_PIN_ASSET_CID, None, _sample_relations())


@pytest.mark.property
def test_compile_bundle_byte_identity_pin():
    # Byte identity: the canonical compiled bytes of this fixed record must
    # never change. Compiling is order-independent, so even reversed input must
    # yield the same content-addressed digest, and that digest is pinned.
    data = bc.compile_bundle(_PIN_ASSET_CID, _PIN_ORIGINATOR, _PIN_RELATIONS)
    data_rev = bc.compile_bundle(
        _PIN_ASSET_CID, _PIN_ORIGINATOR, list(reversed(_PIN_RELATIONS))
    )
    assert data == data_rev
    assert bc.bundle_digest(data) == _PIN_DIGEST

    # A signature over those exact bytes verifies — the signed record commits
    # to the pinned canonical bytes.
    priv, pub = crypto.generate_keypair()
    sig = bc.sign_bundle(priv, data)
    assert bc.verify_bundle(pub, data, sig)
