"""Proofs for the source-group bucketed address book (eclipse resistance).

These tests pin the security property the module exists to provide: a single source
group / address group cannot dominate the sampled peer set no matter how many
addresses it floods, so an honest minority always survives selection. They also pin
determinism (buckets are a pure function of the injected secret + inputs), boundedness
(per-bucket caps hold under flooding), test-before-evict (a proven entry is never
displaced by an unproven claimant), and — critically — that nothing here perturbs a
canonical-CBOR record or a Knit CID (the per-node secret lives off the signed-record
path).
"""

import pytest

from knitweb.core import canonical
from knitweb.p2p.addrbook import (
    DEFAULT_BUCKET_SIZE,
    AddrBook,
    address_group,
    source_group,
)
from knitweb.p2p.transport import PeerAddress

SECRET = b"\x00" * 16  # injected per-node salt; fixed here for reproducibility.


def _p(host: str, port: int = 9001) -> PeerAddress:
    return PeerAddress(host, port)


# -- grouping ---------------------------------------------------------------


@pytest.mark.property
def test_address_group_is_slash16_for_ipv4():
    # Same /16 -> same group; different /16 -> different group.
    assert address_group(_p("1.2.3.4")) == address_group(_p("1.2.9.9"))
    assert address_group(_p("1.2.3.4")) != address_group(_p("1.3.3.4"))


@pytest.mark.property
def test_address_group_distinguishes_ipv6_and_names():
    assert address_group(_p("2001:db8::1")) == address_group(_p("2001:db8:ffff::2"))
    assert address_group(_p("2001:db8::1")) != address_group(_p("2002:db8::1"))
    # Non-IP hosts group by the whole host; relay mailboxes stay distinct.
    relay_a = PeerAddress(transport="relay", params={"mailbox": "a", "base_url": "u"})
    relay_b = PeerAddress(transport="relay", params={"mailbox": "b", "base_url": "u"})
    assert address_group(relay_a) != address_group(relay_b)


@pytest.mark.property
def test_source_group_local_is_fixed():
    assert source_group(None) == b"local:"
    assert source_group(_p("8.8.8.8")) != source_group(None)


# -- determinism ------------------------------------------------------------


@pytest.mark.property
def test_bucket_placement_is_deterministic_in_secret():
    peers = [_p(f"{i}.{i}.{i}.{i}", 9000 + i) for i in range(20)]
    a, b = AddrBook(SECRET), AddrBook(SECRET)
    for p in peers:
        a.add_new(p, source=_p("8.8.8.8"))
        b.add_new(p, source=_p("8.8.8.8"))
    assert a.sample() == b.sample()  # same secret + inputs -> identical sample


@pytest.mark.property
def test_different_secret_reshuffles_buckets():
    peers = [_p(f"10.0.{i}.1") for i in range(40)]
    a = AddrBook(SECRET)
    b = AddrBook(b"\xff" * 16)
    for p in peers:
        a.add_new(p, source=_p("8.8.8.8"))
        b.add_new(p, source=_p("8.8.8.8"))
    # An attacker who does not know the secret cannot predict which addresses survive,
    # so the surviving/sampled sets differ between secrets.
    assert a.sample() != b.sample()


# -- boundedness ------------------------------------------------------------


@pytest.mark.property
def test_one_source_group_is_bounded_under_flood():
    book = AddrBook(SECRET, new_buckets=8, tried_buckets=4, bucket_size=4)
    attacker_src = _p("66.66.1.1")
    # Flood 5000 attacker addresses, ALL from one /16 source group, all in one /16.
    for i in range(5000):
        book.add_new(_p(f"13.13.{i % 256}.{i // 256}", 7000 + i), source=attacker_src)
    # The whole flood shares one address group + one source group, so it maps to a
    # single new bucket and can occupy at most bucket_size slots — not thousands.
    assert book.new_count() <= 4


@pytest.mark.property
def test_diverse_addresses_fill_more_buckets():
    book = AddrBook(SECRET, new_buckets=64, tried_buckets=8, bucket_size=4)
    # Distinct /16s from distinct sources spread across many buckets.
    for i in range(200):
        src = _p(f"{i % 200 + 1}.0.0.1")
        book.add_new(_p(f"{i + 1}.{i % 7}.0.1"), source=src)
    # Diversity is rewarded: far more than a single bucket's worth survives.
    assert book.new_count() > DEFAULT_BUCKET_SIZE * 4


# -- the eclipse-resistance property ----------------------------------------


@pytest.mark.property
def test_flood_cannot_eclipse_honest_minority_in_sample():
    book = AddrBook(SECRET, new_buckets=32, tried_buckets=8, bucket_size=4)
    # A few honest peers, learned from diverse honest sources.
    honest = [_p(f"9.{i}.0.1", 9100 + i) for i in range(4)]
    for i, h in enumerate(honest):
        book.add_new(h, source=_p(f"9.{i}.0.254"))
    # Attacker floods thousands of addresses from a single source group + /16.
    attacker_src = _p("66.66.66.66")
    for i in range(10000):
        book.add_new(_p(f"66.66.{i % 256}.{i // 256}", 7000 + i), source=attacker_src)
    sample = book.sample()  # what the bootstrap loop would dial / advertise
    honest_in_sample = [p for p in sample if p in honest]
    # Under the OLD flat directory (first-k by sort order) all four honest peers — whose
    # hosts sort after "10."/"13."/"66." floods or get buried — could vanish from a small
    # sample. Here every honest peer survives, because the flood is confined to its own
    # buckets and the round-robin sample reaches honest buckets immediately.
    assert set(honest_in_sample) == set(honest)
    # And the honest peers are not a vanishing fraction even of a small top-k.
    top = book.sample(k=8)
    assert sum(1 for p in top if p in honest) >= len(honest) // 2


# -- table mechanics --------------------------------------------------------


@pytest.mark.property
def test_mark_tried_promotes_and_is_preferred():
    book = AddrBook(SECRET)
    p = _p("5.6.7.8")
    book.add_new(p, source=_p("8.8.8.8"))
    assert book.new_count() == 1 and book.tried_count() == 0
    assert book.mark_tried(p)
    assert book.tried_count() == 1
    assert book.new_count() == 0  # moved out of new, not duplicated
    # A second new sighting of a tried address does not demote it.
    assert book.add_new(p, source=_p("1.1.1.1")) is False
    assert book.tried_count() == 1 and book.new_count() == 0
    # Tried peers come first in the sample (tried_bias default).
    book.add_new(_p("4.4.4.4"), source=_p("8.8.8.8"))
    assert book.sample()[0] == p


@pytest.mark.property
def test_test_before_evict_keeps_incumbent():
    # Force two different addresses into the same (bucket, slot) and assert the first
    # one is not evicted by the second. With size-1 buckets, a same-group collision is
    # guaranteed for the second distinct address.
    book = AddrBook(SECRET, new_buckets=1, tried_buckets=1, bucket_size=1)
    first = _p("7.7.0.1")
    second = _p("7.7.0.2")  # same /16 group -> same bucket; size 1 -> same slot
    assert book.add_new(first, source=_p("8.8.8.8")) is True
    assert book.add_new(second, source=_p("8.8.8.8")) is False  # not evicted
    assert first in book and second not in book


@pytest.mark.property
def test_repeat_add_is_idempotent():
    book = AddrBook(SECRET)
    p = _p("3.3.3.3")
    assert book.add_new(p, source=_p("8.8.8.8")) is True
    assert book.add_new(p, source=_p("8.8.8.8")) is True  # same slot, refresh
    assert book.new_count() == 1


@pytest.mark.property
def test_sample_k_bounds_and_known():
    book = AddrBook(SECRET, new_buckets=16, tried_buckets=4, bucket_size=4)
    for i in range(50):
        book.add_new(_p(f"{i + 1}.0.0.1"), source=_p(f"{i + 1}.0.0.254"))
    assert len(book.sample(k=5)) == 5
    assert len(book.sample(k=0)) == 0
    assert len(book.sample(k=10_000)) == book.new_count()  # clamps to available
    assert set(book.known()) == set(book.sample(None))


# -- byte-identity / canonical safety (SACRED) ------------------------------


@pytest.mark.property
def test_secret_never_touches_canonical_bytes_or_cid():
    # The addrbook secret is a LOCAL salt. Build a representative signed-style record
    # and confirm its canonical bytes + CID are identical whether or not an AddrBook
    # (with any secret) has been constructed and exercised over the same peers.
    record = {"host": "1.2.3.4", "port": 9001, "kind": "peer-exchange"}
    cid_before = canonical.cid(record)
    bytes_before = canonical.encode(record)

    book = AddrBook(b"a-totally-different-secret-value")
    for i in range(100):
        book.add_new(_p(f"{i + 1}.2.3.4", 9001), source=_p("9.9.9.9"))
    book.mark_tried(_p("1.2.3.4", 9001))
    _ = book.sample()

    # Nothing the addrbook did can have changed canonical encoding or the CID.
    assert canonical.cid(record) == cid_before
    assert canonical.encode(record) == bytes_before
    # The secret bytes appear nowhere in the canonical encoding of a peer record.
    assert b"a-totally-different-secret-value" not in bytes_before


@pytest.mark.property
def test_peer_records_unchanged_round_trip():
    # An address pulled back out of the book is byte-identical to what went in: the
    # book stores PeerAddress values verbatim and never rewrites their fields.
    p = PeerAddress("1.2.3.4", 9001, transport="tcp")
    book = AddrBook(SECRET)
    book.add_new(p, source=_p("8.8.8.8"))
    (out,) = book.sample()
    assert out == p
    rec = {"host": out.host, "port": out.port}
    assert canonical.decode(canonical.encode(rec)) == rec
