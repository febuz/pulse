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
