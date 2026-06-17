"""Canonical, deterministic CBOR encoding + content addressing (CIDv1).

The whole network's soundness rests on every node — Python, Julia, or a browser
client — producing *identical bytes* for the same logical object, so that hashes
and signatures agree. We therefore use a strict, deterministic subset of CBOR
(RFC 8949 §4.2 "Core Deterministic Encoding") and forbid floats entirely:

  * integers           — shortest-form major type 0 / 1
  * byte strings       — major type 2
  * text strings       — UTF-8, major type 3
  * arrays / lists     — major type 4
  * maps / dicts       — major type 5, keys sorted by *encoded-key bytes*
  * bool / None        — major type 7 simple values 20 / 21 / 22

Floats are rejected: money and state are integers (FBR-wei), never floats, so
conservation is exact and cross-language agreement is guaranteed.

Content identity is a real CIDv1: codec dag-cbor (0x71), multihash sha2-256.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

__all__ = ["encode", "decode", "cid", "DAG_CBOR_CODEC"]

DAG_CBOR_CODEC = 0x71  # IPLD dag-cbor multicodec
_SHA2_256 = 0x12       # multihash code for sha2-256


class CanonicalError(ValueError):
    """Raised when a value cannot be canonically encoded (e.g. a float)."""


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def _encode_head(major: int, length: int) -> bytes:
    """Encode a CBOR head (major type + argument) in shortest deterministic form."""
    if length < 0:
        raise CanonicalError("length must be non-negative")
    mt = major << 5
    if length < 24:
        return bytes([mt | length])
    if length < 0x100:
        return bytes([mt | 24, length])
    if length < 0x10000:
        return bytes([mt | 25]) + length.to_bytes(2, "big")
    if length < 0x100000000:
        return bytes([mt | 26]) + length.to_bytes(4, "big")
    if length < 0x10000000000000000:
        return bytes([mt | 27]) + length.to_bytes(8, "big")
    raise CanonicalError("integer too large for CBOR (>64 bit)")


def _encode(value: Any) -> bytes:
    # bool must be checked before int (bool is a subclass of int)
    if value is None:
        return bytes([0xF6])  # major 7, simple 22
    if value is True:
        return bytes([0xF5])  # major 7, simple 21
    if value is False:
        return bytes([0xF4])  # major 7, simple 20
    if isinstance(value, int):
        if value >= 0:
            return _encode_head(0, value)
        return _encode_head(1, -1 - value)
    if isinstance(value, bytes):
        return _encode_head(2, len(value)) + value
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return _encode_head(3, len(raw)) + raw
    if isinstance(value, (list, tuple)):
        out = _encode_head(4, len(value))
        for item in value:
            out += _encode(item)
        return out
    if isinstance(value, dict):
        # Deterministic map: keys encoded, then sorted bytewise by encoded key.
        encoded_pairs = []
        for k, v in value.items():
            if not isinstance(k, (str, int, bytes)) or isinstance(k, bool):
                raise CanonicalError(f"unsupported map key type: {type(k).__name__}")
            encoded_pairs.append((_encode(k), _encode(v)))
        encoded_pairs.sort(key=lambda pair: pair[0])
        out = _encode_head(5, len(encoded_pairs))
        for ek, ev in encoded_pairs:
            out += ek + ev
        return out
    if isinstance(value, float):
        raise CanonicalError(
            "floats are forbidden in canonical encoding; use integers (FBR-wei)"
        )
    raise CanonicalError(f"cannot canonically encode type: {type(value).__name__}")


def encode(value: Any) -> bytes:
    """Return the canonical, deterministic CBOR bytes for ``value``."""
    return _encode(value)


# ---------------------------------------------------------------------------
# Decoding (used for round-trip verification and reads)
# ---------------------------------------------------------------------------

def _decode(buf: bytes, pos: int) -> tuple[Any, int]:
    if pos >= len(buf):
        raise CanonicalError("unexpected end of input")
    initial = buf[pos]
    major = initial >> 5
    minor = initial & 0x1F
    pos += 1

    def read_len(minor: int, pos: int) -> tuple[int, int]:
        if minor < 24:
            return minor, pos
        if minor == 24:
            return buf[pos], pos + 1
        if minor == 25:
            return int.from_bytes(buf[pos:pos + 2], "big"), pos + 2
        if minor == 26:
            return int.from_bytes(buf[pos:pos + 4], "big"), pos + 4
        if minor == 27:
            return int.from_bytes(buf[pos:pos + 8], "big"), pos + 8
        raise CanonicalError(f"unsupported minor value: {minor}")

    if major == 0:
        n, pos = read_len(minor, pos)
        return n, pos
    if major == 1:
        n, pos = read_len(minor, pos)
        return -1 - n, pos
    if major == 2:
        n, pos = read_len(minor, pos)
        return buf[pos:pos + n], pos + n
    if major == 3:
        n, pos = read_len(minor, pos)
        return buf[pos:pos + n].decode("utf-8"), pos + n
    if major == 4:
        n, pos = read_len(minor, pos)
        items = []
        for _ in range(n):
            item, pos = _decode(buf, pos)
            items.append(item)
        return items, pos
    if major == 5:
        n, pos = read_len(minor, pos)
        out: dict[Any, Any] = {}
        for _ in range(n):
            k, pos = _decode(buf, pos)
            v, pos = _decode(buf, pos)
            out[k] = v
        return out, pos
    if major == 7:
        if minor == 20:
            return False, pos
        if minor == 21:
            return True, pos
        if minor == 22:
            return None, pos
        raise CanonicalError(f"unsupported simple value: {minor}")
    raise CanonicalError(f"unsupported major type: {major}")


def decode(buf: bytes) -> Any:
    """Decode canonical CBOR bytes back into a Python value."""
    value, pos = _decode(buf, 0)
    if pos != len(buf):
        raise CanonicalError("trailing bytes after decode")
    return value


# ---------------------------------------------------------------------------
# Content identity (CIDv1, dag-cbor, sha2-256)
# ---------------------------------------------------------------------------

def _base32_lower_nopad(data: bytes) -> str:
    return base64.b32encode(data).decode("ascii").lower().rstrip("=")


def cid(value: Any) -> str:
    """Return a CIDv1 string (multibase base32, dag-cbor, sha2-256) for ``value``."""
    body = encode(value)
    digest = hashlib.sha256(body).digest()
    multihash = bytes([_SHA2_256, len(digest)]) + digest
    cid_bytes = bytes([0x01, DAG_CBOR_CODEC]) + multihash  # 0x01 = CIDv1
    return "b" + _base32_lower_nopad(cid_bytes)
