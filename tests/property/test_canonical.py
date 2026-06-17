"""Property/parity tests for canonical CBOR encoding and content addressing.

The network's soundness depends on canonical bytes being deterministic, stable,
float-free, and round-trippable. These tests pin that contract.
"""

import pytest

from knitweb.core import canonical


@pytest.mark.property
def test_encode_is_deterministic_and_key_order_independent():
    a = {"b": 2, "a": 1, "z": [3, 2, 1], "nested": {"y": 1, "x": 2}}
    b = {"z": [3, 2, 1], "nested": {"x": 2, "y": 1}, "a": 1, "b": 2}
    # Same logical content, different insertion order -> identical bytes.
    assert canonical.encode(a) == canonical.encode(b)
    # Stable across repeated calls.
    assert canonical.encode(a) == canonical.encode(a)


@pytest.mark.property
def test_round_trip_preserves_value():
    value = {
        "kind": "knit",
        "amount": 1234567890123456789,
        "neg": -42,
        "flag": True,
        "missing": None,
        "list": [1, "two", b"\x03\x04", {"k": "v"}],
        "bytes": b"\x00\xff\x10",
        "text": "fiber/vezel — üñîçødé",
    }
    assert canonical.decode(canonical.encode(value)) == value


@pytest.mark.property
def test_floats_are_rejected():
    with pytest.raises(canonical.CanonicalError):
        canonical.encode({"balance": 1.5})
    with pytest.raises(canonical.CanonicalError):
        canonical.encode([1, 2, 3.0])


@pytest.mark.property
def test_cid_is_stable_and_content_addressed():
    rec = {"kind": "fiber", "value": 100, "owner": "fbr1abc"}
    same = {"owner": "fbr1abc", "value": 100, "kind": "fiber"}
    different = {"kind": "fiber", "value": 101, "owner": "fbr1abc"}
    cid1 = canonical.cid(rec)
    assert cid1 == canonical.cid(rec)          # stable across runs
    assert cid1 == canonical.cid(same)         # order-independent
    assert cid1 != canonical.cid(different)    # value change -> new CID
    # CIDv1 base32 multibase prefix.
    assert cid1.startswith("b")


@pytest.mark.property
def test_shortest_form_integer_encoding():
    # 23 fits in the head byte (major 0, minor 23); 24 needs one extra byte.
    assert canonical.encode(23) == bytes([0x17])
    assert canonical.encode(24) == bytes([0x18, 24])
    assert canonical.encode(0) == bytes([0x00])
    assert canonical.encode(-1) == bytes([0x20])
