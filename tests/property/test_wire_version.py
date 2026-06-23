"""B1: Wire protocol version negotiation tests."""

from __future__ import annotations

import pytest

from knitweb.p2p.wire import (
    WIRE_VERSION,
    WireError,
    WireVersionError,
    read_frame_bytes,
    write_frame_bytes,
)


@pytest.mark.property
def test_wire_version_is_integer():
    assert isinstance(WIRE_VERSION, int)
    assert not isinstance(WIRE_VERSION, bool)
    assert WIRE_VERSION >= 1


@pytest.mark.property
def test_version_1_round_trips():
    msg = {"kind": "test", "value": 42}
    frame = write_frame_bytes(msg, version=1)
    decoded = read_frame_bytes(frame, max_version=1)
    assert decoded == msg


@pytest.mark.property
def test_version_0_legacy_round_trips():
    msg = {"kind": "test", "value": 7}
    frame = write_frame_bytes(msg, version=0)
    decoded = read_frame_bytes(frame)
    assert decoded == msg


@pytest.mark.property
def test_default_version_is_legacy_zero():
    msg = {"kind": "ping"}
    frame_default = write_frame_bytes(msg)
    frame_v0 = write_frame_bytes(msg, version=0)
    assert frame_default == frame_v0


@pytest.mark.property
def test_unknown_high_version_raises():
    msg = {"kind": "future"}
    frame = write_frame_bytes(msg, version=WIRE_VERSION + 1)
    with pytest.raises(WireVersionError) as exc_info:
        read_frame_bytes(frame, max_version=WIRE_VERSION - 1)
    err = exc_info.value
    assert err.got == WIRE_VERSION + 1
    assert err.want == WIRE_VERSION - 1


@pytest.mark.property
def test_wire_version_error_is_wire_error():
    assert issubclass(WireVersionError, WireError)


@pytest.mark.property
def test_version_0_decoded_as_legacy():
    msg = {"kind": "legacy", "seq": 0}
    frame = write_frame_bytes(msg, version=0)
    decoded = read_frame_bytes(frame, max_version=0)
    assert decoded == msg
