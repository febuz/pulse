"""#235: k-bucket source-diversity cap.

A same-source grinder cannot dominate a bucket; honest peers from other source
groups still get in; known-contact refresh is never blocked; and the local-only
``source`` metadata never enters the canonical wire record (CID bytes untouched).
"""

from knitweb.p2p.kademlia import Contact, KBucket, contacts_to_records
from knitweb.p2p.transport import PeerAddress


def _id(n: int) -> bytes:
    return n.to_bytes(32, "big")


def test_same_source_grinder_cannot_dominate_a_bucket():
    cap = 2
    bucket = KBucket(k=8, source_cap=cap)
    grinder = PeerAddress("203.0.113.7", 5000)  # one source group (/16)
    # Six distinct ground node-ids, all advertised by the SAME source group.
    for n in range(6):
        bucket.offer(
            Contact(_id(n + 1), PeerAddress("10.0.0.1", 9000 + n), source=grinder)
        )
    assert len(bucket) == cap  # capped despite the bucket still having room

    # Honest peers from DISTINCT source groups are still admitted.
    bucket.offer(
        Contact(
            _id(100),
            PeerAddress("10.0.0.2", 8000),
            source=PeerAddress("198.51.100.9", 5000),
        )
    )
    bucket.offer(
        Contact(
            _id(101),
            PeerAddress("10.0.0.3", 8001),
            source=PeerAddress("192.0.2.9", 5000),
        )
    )
    assert len(bucket) == cap + 2


def test_none_source_contacts_share_group_and_are_capped():
    cap = 2
    bucket = KBucket(k=8, source_cap=cap)

    # Multiple contacts with source=None should all belong to the same source group
    # via addrbook.source_group(None), and thus be collectively capped.
    for n in range(6):
        bucket.offer(
            Contact(
                _id(n + 1),
                PeerAddress("10.0.0.1", 9000 + n),
                source=None,
            )
        )

    # Despite room in the bucket, locally-heard (source=None) contacts are capped
    # by source_cap for their shared source group.
    assert len(bucket) == cap

    # Contacts from another (non-None) source group are still admitted beyond that cap.
    other_source = PeerAddress("198.51.100.9", 5000)
    bucket.offer(
        Contact(
            _id(100),
            PeerAddress("10.0.0.2", 8000),
            source=other_source,
        )
    )
    bucket.offer(
        Contact(
            _id(101),
            PeerAddress("10.0.0.3", 8001),
            source=other_source,
        )
    )

    # We should have the capped number of None-source contacts plus the others.
    assert len(bucket) == cap + 2


def test_known_contact_refresh_is_not_capped():
    bucket = KBucket(k=8, source_cap=1)
    src = PeerAddress("203.0.113.7", 5000)
    assert bucket.offer(Contact(_id(1), PeerAddress("10.0.0.1", 9000), source=src)) is None
    # Re-offering the SAME id refreshes (address may have moved) — never capped.
    assert bucket.offer(Contact(_id(1), PeerAddress("10.0.0.9", 9999), source=src)) is None
    assert len(bucket) == 1
    assert bucket.contacts()[0].address.port == 9999


def test_no_cap_is_backward_compatible():
    bucket = KBucket(k=3)  # source_cap=None → original fill-to-k behaviour
    src = PeerAddress("203.0.113.7", 5000)
    for n in range(3):
        assert bucket.offer(Contact(_id(n + 1), PeerAddress("10.0.0.1", 9000 + n), source=src)) is None
    assert len(bucket) == 3


def test_source_is_not_serialised_so_wire_shape_is_untouched():
    src = PeerAddress("203.0.113.7", 5000)
    with_src = Contact(_id(1), PeerAddress("10.0.0.1", 9000), source=src)
    without = Contact(_id(1), PeerAddress("10.0.0.1", 9000))
    assert contacts_to_records([with_src]) == contacts_to_records([without])
    assert "source" not in contacts_to_records([with_src])[0]
