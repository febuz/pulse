"""Canonical-CBOR wire helpers for the stdlib asyncio P2P transport.

The Phase 3 MVP deliberately keeps the transport boring: a message is canonical
CBOR, prefixed by a 4-byte big-endian length, then read over an asyncio stream.
The interesting security properties stay in the feed and ledger primitives; the
wire layer only preserves their bytes without adding a dependency.
"""

from __future__ import annotations

import asyncio

from ..core import canonical
from ..fabric.feed import FeedHead
from ..ledger.knit import Knit

__all__ = [
    "MAX_FRAME_BYTES",
    "WireError",
    "feed_head_to_record",
    "feed_head_from_record",
    "knit_to_record",
    "knit_from_record",
    "read_frame",
    "write_frame",
    "read_frame_bytes",
    "write_frame_bytes",
]

MAX_FRAME_BYTES = 8 * 1024 * 1024


class WireError(ValueError):
    """Raised for malformed or unsafe wire data."""


def _require_dict(value) -> dict:
    if not isinstance(value, dict):
        raise WireError(f"expected map, got {type(value).__name__}")
    return value


def _require_str(record: dict, key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str):
        raise WireError(f"{key} must be str")
    return value


def _require_int(record: dict, key: str) -> int:
    value = record.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise WireError(f"{key} must be int")
    return value


def _optional_str(record: dict, key: str) -> str | None:
    value = record.get(key)
    if value is None or isinstance(value, str):
        return value
    raise WireError(f"{key} must be str or null")


def feed_head_to_record(head: FeedHead) -> dict:
    """Return the canonical wire map for a signed feed head."""
    return {
        "feed": head.feed,
        "root": head.root,
        "length": head.length,
        "fork": head.fork,
        "sig": head.sig,
    }


def feed_head_from_record(record: dict) -> FeedHead:
    """Parse a feed-head wire map."""
    record = _require_dict(record)
    return FeedHead(
        feed=_require_str(record, "feed"),
        root=_require_str(record, "root"),
        length=_require_int(record, "length"),
        fork=_require_int(record, "fork"),
        sig=_require_str(record, "sig"),
    )


def knit_to_record(knit: Knit) -> dict:
    """Return the canonical wire map for a Knit, including signatures."""
    out = knit.to_record()
    out["from_sig"] = knit.from_sig
    out["to_sig"] = knit.to_sig
    return out


def knit_from_record(record: dict) -> Knit:
    """Parse a Knit wire map."""
    record = _require_dict(record)
    return Knit(
        from_pub=_require_str(record, "from"),
        to_pub=_require_str(record, "to"),
        symbol=_require_str(record, "symbol"),
        amount=_require_int(record, "amount"),
        from_nonce=_require_int(record, "from_nonce"),
        timestamp=_require_int(record, "timestamp"),
        network=_require_int(record, "network"),
        from_sig=_optional_str(record, "from_sig"),
        to_sig=_optional_str(record, "to_sig"),
    )


def write_frame_bytes(message: dict) -> bytes:
    """Encode one length-prefixed canonical-CBOR frame to bytes.

    This is the single source of truth for the on-the-wire framing: a 4-byte
    big-endian length prefix in front of the float-free canonical CBOR encoding.
    Both the asyncio stream writer and any alternative carrier (e.g. the HTTP
    relay) emit the *same bytes* from the same map, so signed-record byte-identity
    is preserved regardless of which transport carries the frame.
    """
    raw = canonical.encode(message)
    if len(raw) > MAX_FRAME_BYTES:
        raise WireError(f"frame too large: {len(raw)} > {MAX_FRAME_BYTES}")
    return len(raw).to_bytes(4, "big") + raw


def read_frame_bytes(frame: bytes) -> dict:
    """Decode one complete length-prefixed canonical-CBOR frame from bytes."""
    if len(frame) < 4:
        raise WireError("truncated frame")
    n = int.from_bytes(frame[:4], "big")
    if n <= 0:
        raise WireError("empty frame")
    if n > MAX_FRAME_BYTES:
        raise WireError(f"frame too large: {n} > {MAX_FRAME_BYTES}")
    raw = frame[4:]
    if len(raw) != n:
        raise WireError("frame length prefix does not match payload")
    try:
        msg = canonical.decode(raw)
    except canonical.CanonicalError as exc:
        raise WireError(f"non-canonical frame: {exc}") from exc
    return _require_dict(msg)


async def read_frame(reader: asyncio.StreamReader) -> dict:
    """Read one length-prefixed canonical-CBOR message from a stream."""
    try:
        header = await reader.readexactly(4)
        n = int.from_bytes(header, "big")
        if n <= 0:
            raise WireError("empty frame")
        if n > MAX_FRAME_BYTES:
            raise WireError(f"frame too large: {n} > {MAX_FRAME_BYTES}")
        raw = await reader.readexactly(n)
    except asyncio.IncompleteReadError as exc:
        raise WireError("truncated frame") from exc
    return read_frame_bytes(header + raw)


async def write_frame(writer: asyncio.StreamWriter, message: dict) -> None:
    """Write one length-prefixed canonical-CBOR message to a stream."""
    writer.write(write_frame_bytes(message))
    await writer.drain()
