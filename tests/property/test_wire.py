"""Single-shot wire-framing guards — :func:`knitweb.p2p.wire.read_frame_bytes`.

``read_frame_bytes`` is the decode half of the on-wire framing (a 4-byte big-endian
length prefix in front of the float-free canonical CBOR body). It is the *only* size
guard for carriers that decode an already-buffered frame in one shot, bypassing the
streaming :func:`read_frame`: the HTTP relay carrier (see the ``write_frame_bytes``
docstring — "any alternative carrier ... emits the same bytes"), and the reconcile /
inventory decoders that call ``read_frame_bytes`` directly on buffered bytes.

The streaming path's oversized-header guard is pinned elsewhere (``test_metrics``,
``test_node_reputation``), but every existing call into ``read_frame_bytes`` feeds it a
*valid* frame. This module pins the single-shot path's own guards and, crucially, their
**order**: an oversized *declared* length must be rejected as "too large" before the
payload-length comparison, so a tiny frame that merely *declares* a multi-GiB body is
rejected cheaply, without the decoder ever requiring those bytes to be present.
"""
from __future__ import annotations

import pytest

from knitweb.p2p import wire
from knitweb.p2p.wire import MAX_FRAME_BYTES, WireError


def _framed(declared_len: int, body: bytes) -> bytes:
    """A raw frame: a 4-byte big-endian length prefix `declared_len` over `body`.

    `declared_len` is intentionally decoupled from `len(body)` so a tiny buffer can
    *declare* an enormous payload without anyone having to allocate it.
    """
    return declared_len.to_bytes(4, "big") + body


def test_oversized_declared_length_is_rejected_as_too_large_before_payload_check():
    # Declared n = MAX+1 with an empty body. The size guard must fire ("too large")
    # before the payload-length comparison ("does not match") — proving an oversized
    # header is rejected without the declared bytes ever being present.
    frame = _framed(MAX_FRAME_BYTES + 1, b"")
    with pytest.raises(WireError) as ei:
        wire.read_frame_bytes(frame)
    assert "too large" in str(ei.value)
    assert "does not match" not in str(ei.value)


def test_declared_length_equal_to_max_is_not_too_large():
    # n == MAX_FRAME_BYTES is allowed by the size guard (the bound is a strict `>`).
    # Use a short body so the frame still fails — but for the *payload-mismatch*
    # reason, not "too large" — which pins the boundary without allocating MAX bytes.
    frame = _framed(MAX_FRAME_BYTES, b"\xa0")
    with pytest.raises(WireError) as ei:
        wire.read_frame_bytes(frame)
    assert "does not match" in str(ei.value)
    assert "too large" not in str(ei.value)


def test_truncated_header_under_four_bytes_is_rejected():
    with pytest.raises(WireError, match="truncated frame"):
        wire.read_frame_bytes(b"\x00\x00")


def test_zero_declared_length_is_rejected_as_empty():
    with pytest.raises(WireError, match="empty frame"):
        wire.read_frame_bytes(_framed(0, b""))


def test_length_prefix_payload_mismatch_is_rejected():
    # Declares 10 bytes, carries 3.
    with pytest.raises(WireError, match="does not match"):
        wire.read_frame_bytes(_framed(10, b"abc"))


def test_write_then_read_frame_bytes_roundtrips():
    msg = {"kind": "ping", "n": 7}
    assert wire.read_frame_bytes(wire.write_frame_bytes(msg)) == msg
