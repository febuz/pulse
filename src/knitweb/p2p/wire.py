"""Canonical-CBOR wire helpers for the stdlib asyncio P2P transport.

The Phase 3 MVP deliberately keeps the transport boring: a message is canonical
CBOR, prefixed by a 4-byte big-endian length, then read over an asyncio stream.
The interesting security properties stay in the feed and ledger primitives; the
wire layer only preserves their bytes without adding a dependency.
"""

from __future__ import annotations

import asyncio

from ..core import canonical
from ..fabric.equivocation import EquivocationReport
from ..fabric.feed import FeedHead
from ..fabric.feed_multiproof import RangeMultiProof
from ..ledger.knit import Knit

__all__ = [
    "MAX_FRAME_BYTES",
    "WIRE_VERSION",
    "WireError",
    "WireVersionError",
    "feed_head_to_record",
    "feed_head_from_record",
    "multiproof_to_record",
    "multiproof_from_record",
    "knit_to_record",
    "knit_from_record",
    "equivocation_report_to_record",
    "equivocation_report_from_record",
    "read_frame",
    "write_frame",
    "read_frame_bytes",
    "write_frame_bytes",
]

# Hard per-frame byte ceiling for every wire envelope. LIVENESS COUPLING: this MUST
# stay <= ``inventory.SERVE_BYTES_PER_WINDOW``. The all-or-nothing serve budget (#189)
# defers any body larger than one serve window forever, so raising this above the
# serve window would silently starve large-record fetches (see #195 / inventory.py).
MAX_FRAME_BYTES = 8 * 1024 * 1024

# Current wire protocol version.  Bumping this is a breaking change; peers that
# only know a lower version will reject frames from this version if they are strict.
# Version 0 = legacy (no version byte in prefix).
WIRE_VERSION: int = 1


class WireError(ValueError):
    """Raised for malformed or unsafe wire data."""


class WireVersionError(WireError):
    """Raised when a received frame carries an unsupported wire version."""

    def __init__(self, got: int, want: int) -> None:
        super().__init__(f"unsupported wire version: got {got}, max supported {want}")
        self.got = got
        self.want = want


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


def _require_str_list(record: dict, key: str) -> list[str]:
    value = record.get(key)
    if not isinstance(value, list):
        raise WireError(f"{key} must be a list")
    for item in value:
        if not isinstance(item, str):
            raise WireError(f"{key} entries must be str")
    return list(value)


def multiproof_to_record(proof: RangeMultiProof) -> dict:
    """Return the canonical wire map for a contiguous-range Merkle multiproof.

    ``siblings`` is the ordered list of out-of-range sibling hashes (hex); both
    sides re-derive the consume order from ``(start, count, length)`` so only the
    hashes themselves travel.
    """
    return {
        "start": proof.start,
        "count": proof.count,
        "length": proof.length,
        "siblings": list(proof.siblings),
    }


def multiproof_from_record(record: dict) -> RangeMultiProof:
    """Parse a range-multiproof wire map."""
    record = _require_dict(record)
    return RangeMultiProof(
        start=_require_int(record, "start"),
        count=_require_int(record, "count"),
        length=_require_int(record, "length"),
        siblings=_require_str_list(record, "siblings"),
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


def write_frame_bytes(message: dict, *, version: int = 0) -> bytes:
    """Encode one length-prefixed canonical-CBOR frame to bytes.

    This is the single source of truth for the on-the-wire framing: a 4-byte
    big-endian length prefix in front of the float-free canonical CBOR encoding.
    Both the asyncio stream writer and any alternative carrier (e.g. the HTTP
    relay) emit the *same bytes* from the same map, so signed-record byte-identity
    is preserved regardless of which transport carries the frame.

    When ``version > 0`` a single version byte is prepended *inside* the length
    prefix (i.e. the 4-byte length covers the version byte + CBOR payload).
    Version 0 emits no version byte for backward-compatibility with legacy peers.
    """
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise WireError("version must be a non-negative integer")
    raw = canonical.encode(message)
    if version > 0:
        body = bytes([version]) + raw
    else:
        body = raw
    if len(body) > MAX_FRAME_BYTES:
        raise WireError(f"frame too large: {len(body)} > {MAX_FRAME_BYTES}")
    return len(body).to_bytes(4, "big") + body


def read_frame_bytes(frame: bytes, *, max_version: int = 0) -> dict:
    """Decode one complete length-prefixed canonical-CBOR frame from bytes.

    Reads the optional version byte when the payload starts with a byte value in
    range [1, ``max_version``].  A version byte > ``max_version`` raises
    :class:`WireVersionError`.  A frame with a leading byte of 0 is treated as
    legacy (no version byte) for backward-compatibility.
    """
    if len(frame) < 4:
        raise WireError("truncated frame")
    n = int.from_bytes(frame[:4], "big")
    if n <= 0:
        raise WireError("empty frame")
    if n > MAX_FRAME_BYTES:
        raise WireError(f"frame too large: {n} > {MAX_FRAME_BYTES}")
    body = frame[4:]
    if len(body) != n:
        raise WireError("frame length prefix does not match payload")
    if body and body[0] != 0 and body[0] <= 127:
        # Heuristic: first byte is a small positive int → treat as version byte.
        # CBOR maps start with 0xa0..0xbf; ints > 127 need multi-byte CBOR major.
        # Version 0 (legacy) has no version byte; skip only for version ∈ [1..127].
        version = body[0]
        if version > max_version:
            raise WireVersionError(got=version, want=max_version)
        raw = body[1:]
    else:
        raw = body
    if not raw:
        raise WireError("empty CBOR payload after version byte")
    try:
        msg = canonical.decode(raw)
    except canonical.CanonicalError as exc:
        raise WireError(f"non-canonical frame: {exc}") from exc
    return _require_dict(msg)


_EQ_HEAD_FIELDS = ("root", "length", "fork", "sig")


def equivocation_report_to_record(report: EquivocationReport) -> dict:
    """Return the canonical wire map for a gossiped equivocation report.

    This is the existing ``equivocation-report`` record kind (see
    :mod:`knitweb.fabric.equivocation`); the wire layer only relays its bytes.
    """
    return report.to_record()


def _require_head_fields(record: dict, key: str) -> dict:
    head = _require_dict(record.get(key))
    out: dict = {}
    for field in _EQ_HEAD_FIELDS:
        if field == "length" or field == "fork":
            out[field] = _require_int(head, field)
        else:
            out[field] = _require_str(head, field)
    return out


def equivocation_report_from_record(record: dict) -> EquivocationReport:
    """Parse a gossiped ``equivocation-report`` wire map into a report.

    Structural validation only — the cryptographic check (both heads conflict
    under ``feed``) is :func:`knitweb.fabric.equivocation.verify_equivocation_report`,
    which the policing layer re-runs from these bytes before any consequence.
    """
    record = _require_dict(record)
    if record.get("kind") != "equivocation-report":
        raise WireError("not an equivocation-report record")
    return EquivocationReport(
        feed=_require_str(record, "feed"),
        head_a=_require_head_fields(record, "head_a"),
        head_b=_require_head_fields(record, "head_b"),
        reporter=_require_str(record, "reporter"),
    )


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
