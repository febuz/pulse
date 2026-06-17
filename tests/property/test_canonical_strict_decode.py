"""Proofs that canonical CBOR decoding is *strict*.

Determinism is Knitweb's hardest invariant: every hash and signature assumes a
single canonical byte-string per object. A permissive decoder breaks that — an
attacker could craft alternate bytes that decode to the same value but hash
differently (or sign a benign-looking object that re-encodes to something else).

These tests feed hand-built NON-canonical byte sequences and assert the decoder
refuses them, mirroring Ethereum RLP's ErrCanonInt and Cosmos ADR-027. The
positive control confirms the canonical form of the very same value still decodes.
"""

import pytest

from knitweb.core import canonical
from knitweb.core.canonical import CanonicalError


@pytest.mark.property
def test_canonical_roundtrip_still_works():
    # Positive control: canonical encodings of representative values round-trip.
    for value in [0, 23, 24, 255, 256, 65535, 65536, -1, -300,
                  "fiber", b"\x00\x01", [1, 2, 3], {"a": 1, "z": 2},
                  {"nested": {"k": [True, False, None]}}]:
        assert canonical.decode(canonical.encode(value)) == value


@pytest.mark.property
def test_nonminimal_integer_one_byte_head_rejected():
    # 5 encoded as 0x05 is canonical; 0x18 0x05 (1-byte head for a value < 24)
    # is the same integer in a non-minimal head -> must be refused.
    assert canonical.decode(b"\x05") == 5
    with pytest.raises(CanonicalError, match="non-minimal"):
        canonical.decode(b"\x18\x05")


@pytest.mark.property
def test_nonminimal_integer_two_byte_head_rejected():
    # 200 fits a 1-byte head (0x18 0xC8); a 2-byte head (0x19 0x00 0xC8) is non-minimal.
    assert canonical.decode(b"\x18\xc8") == 200
    with pytest.raises(CanonicalError, match="non-minimal"):
        canonical.decode(b"\x19\x00\xc8")


@pytest.mark.property
def test_nonminimal_four_and_eight_byte_heads_rejected():
    # 300 (fits 2-byte) padded into a 4-byte head, and into an 8-byte head.
    with pytest.raises(CanonicalError, match="non-minimal"):
        canonical.decode(b"\x1a\x00\x00\x01\x2c")            # major0, 4-byte, 300
    with pytest.raises(CanonicalError, match="non-minimal"):
        canonical.decode(b"\x1b\x00\x00\x00\x00\x00\x00\x01\x2c")  # 8-byte, 300


@pytest.mark.property
def test_unsorted_map_keys_rejected():
    # Map {"b":1,"a":2} with keys in declaration (unsorted) order. Canonical
    # order is "a" before "b" (encoded-key bytewise). Build the bad bytes by hand:
    #   A2                      map(2)
    #   61 62  01               "b" -> 1
    #   61 61  02               "a" -> 2     <- key "a" < previous "b": not ascending
    bad = bytes([0xA2, 0x61, 0x62, 0x01, 0x61, 0x61, 0x02])
    with pytest.raises(CanonicalError, match="ascending"):
        canonical.decode(bad)
    # The canonically-ordered version of the same map decodes fine.
    good = canonical.encode({"b": 1, "a": 2})
    assert canonical.decode(good) == {"a": 2, "b": 1}


@pytest.mark.property
def test_duplicate_map_keys_rejected():
    #   A2  61 61 01  61 61 02   map{ "a":1, "a":2 } -> duplicate key
    bad = bytes([0xA2, 0x61, 0x61, 0x01, 0x61, 0x61, 0x02])
    with pytest.raises(CanonicalError, match="duplicate"):
        canonical.decode(bad)


@pytest.mark.property
def test_indefinite_length_and_trailing_bytes_rejected():
    # Indefinite-length array (0x9F ... 0xFF) is non-deterministic -> refused.
    with pytest.raises(CanonicalError):
        canonical.decode(b"\x9f\x01\x02\xff")
    # Trailing garbage after a complete value -> refused.
    with pytest.raises(CanonicalError, match="trailing"):
        canonical.decode(b"\x01\x01")


@pytest.mark.property
def test_nonminimal_key_inside_map_is_caught():
    # A map whose *key* uses a non-minimal head must also be rejected, because
    # read_len fires on the key's head before the ordering check.
    #   A1  18 61 01            map{ <"a" as non-minimal int? no> }  -> use int key
    # int key 5 encoded non-minimally as 0x18 0x05, value 0 -> 0xA1 18 05 00
    with pytest.raises(CanonicalError, match="non-minimal"):
        canonical.decode(bytes([0xA1, 0x18, 0x05, 0x00]))


@pytest.mark.property
def test_truncated_input_raises_typed_error_not_indexerror():
    # A decoder on the wire must reject malformed/adversarial input with a typed
    # CanonicalError — never a raw IndexError or a silent mis-parse.
    truncated = [
        b"\x18",          # major0 minor24: promises a length byte, none present
        b"\x19\x01",      # minor25: promises 2 length bytes, only 1
        b"\x1a\x00\x01",  # minor26: promises 4 length bytes, only 2
        b"\x42\x00",      # bytes(2): only 1 body byte present
        b"\x63ab",        # text(3): only 2 body bytes present
        b"\x82\x01",      # array(2): only 1 item present
        b"\xa1\x01",      # map(1): key present, value missing
    ]
    for buf in truncated:
        with pytest.raises(CanonicalError):
            canonical.decode(buf)
