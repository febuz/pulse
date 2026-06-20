"""Transport framing is byte-identical regardless of carrier.

The whole point of the pluggable transport split is that a frame's bytes do not
depend on *which* carrier moves it: a record signed once must verify after a
round-trip over TCP or over the HTTP relay. These tests pin that invariant at the
framing layer (the byte-identity gate) plus the PeerAddress URI round-trip.
"""

import pytest

from knitweb.core import canonical
from knitweb.fabric.feed import Feed
from knitweb.p2p.transport import PeerAddress, parse_peer_uri
from knitweb.p2p.wire import read_frame_bytes, write_frame_bytes


@pytest.mark.property
def test_frame_bytes_are_canonical_and_length_prefixed():
    msg = {"kind": "feed-request", "feed": "abc", "start": 0, "end": None}
    frame = write_frame_bytes(msg)
    raw = canonical.encode(msg)
    # 4-byte big-endian length prefix, then the exact canonical encoding.
    assert frame == len(raw).to_bytes(4, "big") + raw
    assert read_frame_bytes(frame) == msg


@pytest.mark.property
def test_frame_round_trip_is_byte_identical():
    msg = {"z": 1, "a": 2, "kind": "x", "nested": {"b": [1, 2, 3]}}
    frame = write_frame_bytes(msg)
    # Re-encoding the decoded map yields the same frame bytes (canonical order).
    assert write_frame_bytes(read_frame_bytes(frame)) == frame


@pytest.mark.property
def test_signed_feed_head_survives_framing_unchanged():
    # A signed feed head's signature must still verify after a frame round-trip:
    # the carrier never re-encodes the payload, so the signed bytes are intact.
    feed = Feed.create()
    feed.append({"kind": "knowledge", "title": "alpha"})
    head = feed.append({"kind": "resource", "capacity": 4, "price": 9})
    from knitweb.p2p.wire import feed_head_from_record, feed_head_to_record

    msg = {"kind": "feed-data", "head": feed_head_to_record(head)}
    restored = read_frame_bytes(write_frame_bytes(msg))
    head2 = feed_head_from_record(restored["head"])
    assert head2 == head
    assert head2.verify()


@pytest.mark.property
def test_bad_length_prefix_is_rejected():
    raw = canonical.encode({"kind": "x"})
    from knitweb.p2p.wire import WireError

    # Prefix claims more bytes than present.
    bad = (len(raw) + 5).to_bytes(4, "big") + raw
    with pytest.raises(WireError):
        read_frame_bytes(bad)
    with pytest.raises(WireError):
        read_frame_bytes(b"\x00")  # truncated header


@pytest.mark.property
@pytest.mark.parametrize(
    "addr",
    [
        PeerAddress(host="10.0.0.4", port=8765, transport="tcp"),
        PeerAddress(
            transport="relay",
            params={"mailbox": "mb0", "base_url": "https://5mart.ml"},
        ),
    ],
)
def test_peer_uri_round_trips(addr):
    assert parse_peer_uri(addr.uri()) == addr


@pytest.mark.property
def test_peer_address_is_hashable_with_params():
    a = PeerAddress(transport="relay", params={"mailbox": "m", "base_url": "u"})
    b = PeerAddress(transport="relay", params={"base_url": "u", "mailbox": "m"})
    # Equal addresses (order-independent params) hash equal and dedupe in a set.
    assert a == b
    assert len({a, b}) == 1


def _valid_knit_record() -> dict:
    return {
        "from": "a",
        "to": "b",
        "symbol": "s",
        "amount": 1,
        "from_nonce": 0,
        "timestamp": 0,
        "network": 0,
        "from_sig": "x",
        "to_sig": "y",
    }


def _valid_feed_head_record() -> dict:
    return {"feed": "f", "root": "r", "length": 1, "fork": 0, "sig": "s"}


def _valid_multiproof_record() -> dict:
    return {"start": 0, "count": 1, "length": 1, "siblings": ["h"]}


def _valid_equivocation_record() -> dict:
    return {
        "kind": "equivocation-report",
        "feed": "f",
        "reporter": "rep",
        "head_a": {"root": "ra", "length": 1, "fork": 0, "sig": "sa"},
        "head_b": {"root": "rb", "length": 1, "fork": 1, "sig": "sb"},
    }


@pytest.mark.property
def test_valid_records_parse_as_baseline():
    # Confirm the minimal baselines are accepted before flipping int fields to bool.
    from knitweb.p2p.wire import (
        equivocation_report_from_record,
        feed_head_from_record,
        knit_from_record,
        multiproof_from_record,
    )

    assert knit_from_record(_valid_knit_record()).amount == 1
    assert feed_head_from_record(_valid_feed_head_record()).length == 1
    assert multiproof_from_record(_valid_multiproof_record()).count == 1
    assert equivocation_report_from_record(_valid_equivocation_record()).feed == "f"


@pytest.mark.property
@pytest.mark.parametrize("field", ["amount", "from_nonce", "network"])
def test_knit_from_record_rejects_bool_int_field(field):
    # bool is an int subclass; _require_int must still reject it on int fields.
    from knitweb.p2p.wire import WireError, knit_from_record

    record = _valid_knit_record()
    record[field] = True
    with pytest.raises(WireError, match=f"{field} must be int"):
        knit_from_record(record)


@pytest.mark.property
@pytest.mark.parametrize("field", ["length", "fork"])
def test_feed_head_from_record_rejects_bool_int_field(field):
    from knitweb.p2p.wire import WireError, feed_head_from_record

    record = _valid_feed_head_record()
    record[field] = True
    with pytest.raises(WireError, match=f"{field} must be int"):
        feed_head_from_record(record)


@pytest.mark.property
@pytest.mark.parametrize("field", ["start", "count", "length"])
def test_multiproof_from_record_rejects_bool_int_field(field):
    from knitweb.p2p.wire import WireError, multiproof_from_record

    record = _valid_multiproof_record()
    record[field] = True
    with pytest.raises(WireError, match=f"{field} must be int"):
        multiproof_from_record(record)


@pytest.mark.property
@pytest.mark.parametrize("field", ["length", "fork"])
def test_equivocation_report_from_record_rejects_bool_head_int_field(field):
    # head_a is parsed via _require_head_fields, whose int fields are length/fork.
    from knitweb.p2p.wire import WireError, equivocation_report_from_record

    record = _valid_equivocation_record()
    record["head_a"][field] = True
    with pytest.raises(WireError, match=f"{field} must be int"):
        equivocation_report_from_record(record)
