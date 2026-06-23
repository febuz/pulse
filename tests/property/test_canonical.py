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
    rec = {"kind": "fiber", "value": 100, "owner": "pls1abc"}
    same = {"owner": "pls1abc", "value": 100, "kind": "fiber"}
    different = {"kind": "fiber", "value": 101, "owner": "pls1abc"}
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


@pytest.mark.property
def test_string_length_budget_rejects_oversized():
    """Strings longer than MAX_STRING_LEN raise CanonicalError on decode."""
    giant = "x" * (canonical.MAX_STRING_LEN + 1)
    raw = canonical.encode(giant)
    with pytest.raises(canonical.CanonicalError, match="MAX_STRING_LEN"):
        canonical.decode(raw)


@pytest.mark.property
def test_string_exactly_at_limit_round_trips():
    """Strings of exactly MAX_STRING_LEN bytes are accepted."""
    at_limit = "a" * canonical.MAX_STRING_LEN
    assert canonical.decode(canonical.encode(at_limit)) == at_limit


@pytest.mark.property
def test_array_length_budget_rejects_claim_larger_than_max():
    """An array claiming more than MAX_ARRAY_LEN items is rejected before iteration."""
    # Craft a truncated buffer: header claims MAX_ARRAY_LEN+1 items but provides 0.
    # The guard must fire on the claimed length, not after draining the items.
    import struct
    # CBOR major type 4, 4-byte length argument (minor 26)
    n = canonical.MAX_ARRAY_LEN + 1
    bad_buf = bytes([0x9A]) + struct.pack(">I", n)  # array of n items, no items follow
    with pytest.raises(canonical.CanonicalError, match="MAX_ARRAY_LEN"):
        canonical.decode(bad_buf)
