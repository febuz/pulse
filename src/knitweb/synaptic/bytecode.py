"""Fiber Synaptic Compiler — verified relations → ultralight, signed bytecode.

This is Fiber's differentiator. Knowledge-graph relations (typically sourced and
provenance-verified via OriginTrail) are compiled into a compact, deterministic
binary form that the smallest edge devices — IoT AI models, AR glasses — can
fetch over BLE / 5G / Wi-Fi / satellite and execute locally, without pulling
gigabytes of context.

Design goals (Szabo-style: make the artifact itself carry its guarantees):

  * **Compact** — string interning + LEB128 varints shrink repeated URIs/terms.
  * **Deterministic** — the dictionary is lexicographically sorted, so the same
    set of relations always compiles to identical bytes (content-addressable).
  * **Reversible** — ``decode_bundle`` reconstructs the exact relations.
  * **Provenance-bearing** — the bundle embeds the source asset's CID and the
    verified originator, and can be signed so any device can verify it came from
    the claimed originator before executing it.

The format is intentionally tiny and self-describing; it is data, not code, but it
is the executable "relation matrix" an edge model consumes.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import crypto

__all__ = [
    "Relation",
    "SOURCE_TYPES",
    "compile_bundle",
    "decode_bundle",
    "sign_bundle",
    "verify_bundle",
    "bundle_digest",
    "BytecodeError",
]

MAGIC = b"PLS1"   # Pulse synaptic bytecode, format v1
VERSION = 1

# Source-type tags (one byte each). Covers Western, Chinese, and Russian media
# variants plus the financial/IP sources OriginTrail commonly anchors.
SOURCE_TYPES: dict[str, int] = {
    "Unknown": 0x00,
    "IFRS_File": 0x01,
    "News_Article": 0x02,
    "YouTube_Video": 0x03,
    "Youku_Video": 0x04,       # Chinese
    "RuTube_Video": 0x05,      # Russian
    "Vimeo_Video": 0x06,
    "Bilibili_Video": 0x07,    # Chinese
    "Image_Library": 0x08,
    "Dataset": 0x09,
    "Patent": 0x0A,
    "Filing_SEC": 0x0B,
    "Webpage": 0x0C,
}
_SOURCE_BY_BYTE = {v: k for k, v in SOURCE_TYPES.items()}


class BytecodeError(ValueError):
    """Raised on malformed synaptic bytecode."""


@dataclass(frozen=True)
class Relation:
    """A provenance-tagged knowledge-graph edge."""

    subject: str
    predicate: str
    obj: str
    source_type: str = "Unknown"
    weight: int = 1


# ---------------------------------------------------------------------------
# LEB128 unsigned varints
# ---------------------------------------------------------------------------

def _put_varint(buf: bytearray, value: int) -> None:
    if value < 0:
        raise BytecodeError("varint must be non-negative")
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            buf.append(byte | 0x80)
        else:
            buf.append(byte)
            return


def _get_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise BytecodeError("truncated varint")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7


def _put_str(buf: bytearray, text: str) -> None:
    raw = text.encode("utf-8")
    _put_varint(buf, len(raw))
    buf.extend(raw)


def _get_str(data: bytes, pos: int) -> tuple[str, int]:
    n, pos = _get_varint(data, pos)
    end = pos + n
    if end > len(data):
        raise BytecodeError("truncated string")
    try:
        return data[pos:end].decode("utf-8"), end
    except UnicodeDecodeError as exc:
        raise BytecodeError("invalid utf-8 in interned string") from exc


# ---------------------------------------------------------------------------
# Compile / decode
# ---------------------------------------------------------------------------

def compile_bundle(
    asset_cid: str,
    originator: str,
    relations: list[Relation],
) -> bytes:
    """Compile relations into deterministic Fiber synaptic bytecode.

    Identical relation sets (regardless of ordering) compile to identical bytes:
    the string dictionary is sorted lexicographically and relations are emitted in
    a canonical order, so the bundle is content-addressable.
    """
    if not isinstance(asset_cid, str) or not asset_cid:
        raise BytecodeError("asset_cid is required")
    if not isinstance(originator, str) or not originator:
        raise BytecodeError("originator is required")

    # Build a sorted, de-duplicated term dictionary for interning.
    terms: set[str] = set()
    for r in relations:
        terms.update((r.subject, r.predicate, r.obj))
    sorted_terms = sorted(terms)
    index = {t: i for i, t in enumerate(sorted_terms)}

    # Canonical relation order: by interned (subject, predicate, obj, type, weight).
    def rel_key(r: Relation) -> tuple:
        return (
            index[r.subject],
            index[r.predicate],
            index[r.obj],
            SOURCE_TYPES.get(r.source_type, 0x00),
            r.weight,
        )

    ordered = sorted(relations, key=rel_key)

    buf = bytearray()
    buf.extend(MAGIC)
    buf.append(VERSION)
    _put_str(buf, asset_cid)
    _put_str(buf, originator)

    _put_varint(buf, len(sorted_terms))
    for t in sorted_terms:
        _put_str(buf, t)

    _put_varint(buf, len(ordered))
    for r in ordered:
        _put_varint(buf, index[r.subject])
        _put_varint(buf, index[r.predicate])
        _put_varint(buf, index[r.obj])
        buf.append(SOURCE_TYPES.get(r.source_type, 0x00))
        if not isinstance(r.weight, int) or isinstance(r.weight, bool) or r.weight < 0:
            raise BytecodeError("relation weight must be a non-negative int")
        _put_varint(buf, r.weight)

    return bytes(buf)


def decode_bundle(data: bytes) -> dict:
    """Decode synaptic bytecode back into {asset_cid, originator, relations}."""
    if data[:4] != MAGIC:
        raise BytecodeError("bad magic; not Fiber synaptic bytecode")
    pos = 4
    if pos >= len(data):
        raise BytecodeError("truncated; missing version byte")
    version = data[pos]
    pos += 1
    if version != VERSION:
        raise BytecodeError(f"unsupported version {version}")
    asset_cid, pos = _get_str(data, pos)
    originator, pos = _get_str(data, pos)

    dict_count, pos = _get_varint(data, pos)
    terms: list[str] = []
    for _ in range(dict_count):
        term, pos = _get_str(data, pos)
        terms.append(term)

    rel_count, pos = _get_varint(data, pos)
    relations: list[Relation] = []
    for _ in range(rel_count):
        si, pos = _get_varint(data, pos)
        pi, pos = _get_varint(data, pos)
        oi, pos = _get_varint(data, pos)
        if si >= len(terms) or pi >= len(terms) or oi >= len(terms):
            raise BytecodeError("relation references out-of-range term index")
        if pos >= len(data):
            raise BytecodeError("truncated; missing source-type byte")
        st_byte = data[pos]
        pos += 1
        weight, pos = _get_varint(data, pos)
        relations.append(
            Relation(
                subject=terms[si],
                predicate=terms[pi],
                obj=terms[oi],
                source_type=_SOURCE_BY_BYTE.get(st_byte, "Unknown"),
                weight=weight,
            )
        )
    if pos != len(data):
        raise BytecodeError("trailing bytes after bundle")
    return {"asset_cid": asset_cid, "originator": originator, "relations": relations}


# ---------------------------------------------------------------------------
# Provenance: content id + signing
# ---------------------------------------------------------------------------

def bundle_digest(data: bytes) -> str:
    """SHA-256 hex digest of a bundle — its content fingerprint."""
    return crypto.sha256_hex(data)


def sign_bundle(originator_priv: str, data: bytes) -> str:
    """Sign a bundle so edge devices can verify the originator before executing."""
    return crypto.sign(originator_priv, data)


def verify_bundle(originator_pub: str, data: bytes, signature_hex: str) -> bool:
    """Verify a bundle's signature against the claimed originator public key."""
    return crypto.verify(originator_pub, data, signature_hex)
