"""Canonical, deterministic CBOR encoding + content addressing (CIDv1).

The whole web's soundness rests on every node — Python, Julia, or a browser
client — producing *identical bytes* for the same logical object, so that hashes
and signatures agree. We therefore use a strict, deterministic subset of CBOR
(RFC 8949 §4.2 "Core Deterministic Encoding") and forbid floats entirely:

  * integers           — shortest-form major type 0 / 1
  * byte strings       — major type 2
  * text strings       — UTF-8, major type 3
  * arrays / lists     — major type 4
  * maps / dicts       — major type 5, keys sorted by *encoded-key bytes*
  * bool / None        — major type 7 simple values 20 / 21 / 22

Floats are rejected: money and state are integers (PLS-wei), never floats, so
conservation is exact and cross-language agreement is guaranteed.

``decode`` is *strict*, not just permissive: it rejects any non-canonical input
— non-minimal integer/length heads, unsorted or duplicate map keys, indefinite-
length items, and trailing bytes. There is therefore exactly one byte-string per
logical object, so ``decode(encode(x))`` is canonical and an attacker cannot
forge alternate bytes that hash differently yet decode to the same value. This is
the same guarantee Ethereum RLP (ErrCanonInt) and Cosmos ADR-027 enforce.

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


# Maximum container-nesting depth for encode/decode. Real records are shallow; this is a
# hard guard so a deeply-nested input — especially attacker-controlled *bytes* on the
# decode path (every gossiped record, CID, and signature verify decodes untrusted input) —
# raises CanonicalError instead of exhausting the Python stack (RecursionError / DoS). It is
# well below CPython's default recursion limit, and changing it does NOT affect the bytes of
# any value within the limit, so it is not hash-critical (#145).
MAX_DEPTH = 64

# Maximum number of objects a single buffer may decode into. ``MAX_DEPTH`` bounds
# nesting *depth* but not *breadth*: one in-spec ~8 MiB frame of shallow
# containers/tiny leaves still decodes to ~8M Python objects (~64x heap
# amplification) on the event loop, even though it never nests past depth 64. The
# wire byte budget (#91/#102) caps bytes off the wire but not this post-decode
# object explosion. Every decoded value (container or leaf) costs one count; real
# records are tiny (hundreds of objects), so this ceiling sits orders of magnitude
# above any legitimate record while refusing the hostile fan-out. Reject-only and
# pure-integer — like MAX_DEPTH it never changes a within-limit value's bytes or
# round-trip, so it is NOT hash-critical (#169).
MAX_ITEMS = 1_048_576

# Maximum byte-length of a single string or bytes value in a decoded record.
# Prevents a single 8 GiB byte-string claim from allocating unbounded memory
# before the MAX_ITEMS guard fires (#169 follow-up, ARCHITECTURE.md R4).
# 1 MiB sits far above any legitimate signed record field while refusing
# attacker-crafted giant blobs. NOT hash-critical: encode() is unchanged.
MAX_STRING_LEN = 1_048_576  # 1 MiB

# Maximum number of items in a single array or map.
# Prevents a shallow-but-wide container from evading the MAX_ITEMS guard via
# claim-then-truncate (claim 2^32 items, provide 1 — MAX_ITEMS only fires on
# actually-decoded objects). This rejects the claimed length before iteration.
# NOT hash-critical: encode() is unchanged.
MAX_ARRAY_LEN = 4_000_000


def _encode(value: Any, depth: int = 0) -> bytes:
    if depth > MAX_DEPTH:
        raise CanonicalError(f"value nests deeper than MAX_DEPTH={MAX_DEPTH}")
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
            out += _encode(item, depth + 1)
        return out
    if isinstance(value, dict):
        # Deterministic map: keys encoded, then sorted bytewise by encoded key.
        encoded_pairs = []
        for k, v in value.items():
            if not isinstance(k, (str, int, bytes)) or isinstance(k, bool):
                raise CanonicalError(f"unsupported map key type: {type(k).__name__}")
            encoded_pairs.append((_encode(k, depth + 1), _encode(v, depth + 1)))
        encoded_pairs.sort(key=lambda pair: pair[0])
        out = _encode_head(5, len(encoded_pairs))
        for ek, ev in encoded_pairs:
            out += ek + ev
        return out
    if isinstance(value, float):
        raise CanonicalError(
            "floats are forbidden in canonical encoding; use integers (PLS-wei)"
        )
    raise CanonicalError(f"cannot canonically encode type: {type(value).__name__}")


def encode(value: Any) -> bytes:
    """Return the canonical, deterministic CBOR bytes for ``value``."""
    return _encode(value)


# ---------------------------------------------------------------------------
# Decoding (used for round-trip verification and reads)
# ---------------------------------------------------------------------------

def _decode(
    buf: bytes, pos: int, depth: int = 0, count: list[int] | None = None
) -> tuple[Any, int]:
    # ``count`` is a one-element mutable cell threaded through the whole decode so
    # the object-COUNT guard (#169) spans the entire buffer, not one container.
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > MAX_ITEMS:
        raise CanonicalError(f"input decodes more than MAX_ITEMS={MAX_ITEMS} objects")
    if depth > MAX_DEPTH:
        raise CanonicalError(f"input nests deeper than MAX_DEPTH={MAX_DEPTH}")
    if pos >= len(buf):
        raise CanonicalError("unexpected end of input")
    initial = buf[pos]
    major = initial >> 5
    minor = initial & 0x1F
    pos += 1

    def read_len(minor: int, pos: int) -> tuple[int, int]:
        # Deterministic decoding (RFC 8949 §4.2): an argument MUST use the
        # shortest possible head. We reject non-minimal encodings so there is
        # exactly one byte-string per value and decode(encode(x)) round-trips
        # are canonical — an attacker cannot craft alternate bytes for the same
        # object (this is the RLP ErrCanonInt / Cosmos ADR-027 guarantee).
        if minor < 24:
            return minor, pos
        # Length-byte counts per minor; verify they are present before reading so a
        # truncated head raises a typed CanonicalError, not IndexError. A decoder on
        # the wire must reject malformed/adversarial input cleanly.
        width = {24: 1, 25: 2, 26: 4, 27: 8}.get(minor)
        if width is None:
            raise CanonicalError(f"unsupported minor value: {minor}")
        if pos + width > len(buf):
            raise CanonicalError("truncated length: not enough bytes for head")
        n = int.from_bytes(buf[pos:pos + width], "big")
        # Reject non-minimal heads (RLP ErrCanonInt / Cosmos ADR-027): an argument
        # MUST use the shortest form, so there is exactly one byte-string per value.
        minimum = {1: 24, 2: 0x100, 4: 0x10000, 8: 0x100000000}[width]
        if n < minimum:
            raise CanonicalError("non-minimal integer: value fits a shorter head")
        return n, pos + width

    if major == 0:
        n, pos = read_len(minor, pos)
        return n, pos
    if major == 1:
        n, pos = read_len(minor, pos)
        return -1 - n, pos
    if major == 2:
        n, pos = read_len(minor, pos)
        if n > MAX_STRING_LEN:
            raise CanonicalError(
                f"byte string length {n} exceeds MAX_STRING_LEN={MAX_STRING_LEN}"
            )
        if pos + n > len(buf):
            raise CanonicalError("truncated byte string: not enough body bytes")
        return buf[pos:pos + n], pos + n
    if major == 3:
        n, pos = read_len(minor, pos)
        if n > MAX_STRING_LEN:
            raise CanonicalError(
                f"text string length {n} exceeds MAX_STRING_LEN={MAX_STRING_LEN}"
            )
        if pos + n > len(buf):
            raise CanonicalError("truncated text string: not enough body bytes")
        return buf[pos:pos + n].decode("utf-8"), pos + n
    if major == 4:
        n, pos = read_len(minor, pos)
        if n > MAX_ARRAY_LEN:
            raise CanonicalError(
                f"array length {n} exceeds MAX_ARRAY_LEN={MAX_ARRAY_LEN}"
            )
        items = []
        for _ in range(n):
            item, pos = _decode(buf, pos, depth + 1, count)
            items.append(item)
        return items, pos
    if major == 5:
        n, pos = read_len(minor, pos)
        if n > MAX_ARRAY_LEN:
            raise CanonicalError(
                f"map length {n} exceeds MAX_ARRAY_LEN={MAX_ARRAY_LEN}"
            )
        out: dict[Any, Any] = {}
        prev_key_bytes: bytes | None = None
        for _ in range(n):
            key_start = pos
            k, pos = _decode(buf, pos, depth + 1, count)
            key_bytes = buf[key_start:pos]
            # Keys MUST appear in strictly ascending encoded-key byte order
            # (the same order encode() emits). This rejects both unsorted maps
            # and duplicate keys in one check, so a map has exactly one
            # canonical serialization.
            if prev_key_bytes is not None and key_bytes <= prev_key_bytes:
                if key_bytes == prev_key_bytes:
                    raise CanonicalError("duplicate map key in canonical CBOR")
                raise CanonicalError("map keys not in canonical (ascending) order")
            prev_key_bytes = key_bytes
            v, pos = _decode(buf, pos, depth + 1, count)
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
