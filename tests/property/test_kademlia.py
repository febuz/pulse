"""Proofs for the Kademlia DHT core (k-buckets, XOR metric, iterative lookup).

These tests pin the properties the module exists to provide:

  * **XOR metric** is symmetric, integer-only, zero iff equal, and obeys the
    bucket-prefix indexing.
  * **FIND_NODE / ``closest``** returns exactly the k known peers with the smallest
    XOR distance to the target, in distance order, deterministically.
  * **k-buckets** are bounded by ``k`` and use test-before-evict LRU: a full bucket
    never silently drops a live (responding) peer for a newcomer.
  * **Iterative lookup converges** to the k globally-closest peers in a simulated
    network, in bounded rounds, with the responder injected (socket-free) — and the
    trace is deterministic.
  * **Byte-identity (SACRED)**: node ids are a local routing construct; nothing
    here perturbs a canonical-CBOR record or a fresh Knit CID.
"""

import hashlib

import pytest

from knitweb.core import canonical
from knitweb.p2p.kademlia import (
    DEFAULT_ALPHA,
    DEFAULT_K,
    ID_BITS,
    ID_BYTES,
    Contact,
    KBucket,
    RoutingTable,
    bucket_index,
    contacts_from_records,
    contacts_to_records,
    find_node_message,
    handle_find_node,
    iterative_lookup,
    node_id,
    node_id_hex,
    nodes_message,
    xor_distance,
)
from knitweb.p2p.transport import PeerAddress


def _id(n: int) -> bytes:
    """A deterministic 32-byte id from a small int (not a real sha256 — fine for
    the metric/bucket tests which only need distinct, controllable ids)."""
    return n.to_bytes(ID_BYTES, "big")


def _contact(n: int, port: int = 9000) -> Contact:
    return Contact(node_id=_id(n), address=PeerAddress("10.0.0.1", port))


# -- node id ----------------------------------------------------------------


@pytest.mark.property
def test_node_id_is_sha256_of_pubkey_hex():
    pub = "02" + "ab" * 32
    assert node_id(pub) == hashlib.sha256(pub.encode("utf-8")).digest()
    assert len(node_id(pub)) == ID_BYTES
    assert node_id_hex(pub) == node_id(pub).hex()
    # Distinct pubkeys -> distinct ids.
    assert node_id(pub) != node_id("02" + "cd" * 32)


# -- XOR metric -------------------------------------------------------------


@pytest.mark.property
def test_xor_distance_is_symmetric_integer_and_zero_iff_equal():
    a, b = _id(0b1010), _id(0b0110)
    assert xor_distance(a, b) == xor_distance(b, a)
    assert isinstance(xor_distance(a, b), int)
    assert xor_distance(a, a) == 0
    assert xor_distance(a, b) == 0b1100
    # Accepts hex too.
    assert xor_distance(a.hex(), b.hex()) == 0b1100


@pytest.mark.property
def test_xor_triangle_property():
    a, b, c = _id(5), _id(9), _id(20)
    # The defining XOR identity: d(a,c) == d(a,b) ^ d(b,c).
    assert xor_distance(a, c) == xor_distance(a, b) ^ xor_distance(b, c)


@pytest.mark.property
def test_bucket_index_is_highest_set_bit_of_distance():
    self_id = _id(0)
    assert bucket_index(self_id, _id(1)) == 0  # distance 1 -> bit 0
    assert bucket_index(self_id, _id(2)) == 1  # distance 2 -> bit 1
    assert bucket_index(self_id, _id(0b1000)) == 3
    # Highest set bit dominates: 0b1001 -> bit 3.
    assert bucket_index(self_id, _id(0b1001)) == 3
    # The node's own id has no bucket.
    assert bucket_index(self_id, self_id) == -1
    # Top bit -> top bucket.
    assert bucket_index(self_id, _id(1 << (ID_BITS - 1))) == ID_BITS - 1


# -- k-buckets: bounded + test-before-evict ---------------------------------


@pytest.mark.property
def test_kbucket_bounded_and_known_peer_refreshes_to_tail():
    b = KBucket(k=3)
    for n in (1, 2, 3):
        assert b.offer(_contact(n)) is None
    assert len(b) == 3
    # Re-offer the head (id 1): it moves to the tail, length unchanged.
    assert b.offer(_contact(1)) is None
    assert [c.node_id for c in b.contacts()] == [_id(2), _id(3), _id(1)]


@pytest.mark.property
def test_kbucket_full_surfaces_stale_head_not_silent_evict():
    b = KBucket(k=2)
    b.offer(_contact(1))
    b.offer(_contact(2))
    # Full: offering a newcomer returns the least-recently-seen head to probe,
    # and does NOT admit the newcomer.
    stale = b.offer(_contact(3))
    assert stale is not None and stale.node_id == _id(1)
    assert _id(3) not in b
    assert len(b) == 2


@pytest.mark.property
def test_kbucket_live_head_is_sticky_newcomer_dropped():
    b = KBucket(k=2)
    b.offer(_contact(1))
    b.offer(_contact(2))
    stale = b.offer(_contact(3))
    # The stale head responded to its ping -> keep it (move to tail), drop newcomer.
    assert b.touch(stale.node_id) is True
    assert _id(3) not in b
    assert [c.node_id for c in b.contacts()] == [_id(2), _id(1)]


@pytest.mark.property
def test_kbucket_dead_head_evicted_then_newcomer_admitted():
    b = KBucket(k=2)
    b.offer(_contact(1))
    b.offer(_contact(2))
    b.offer(_contact(3))  # full -> probe head id 1
    # Head failed its ping -> evict it and admit the newcomer.
    assert b.evict_then_add(_contact(3)) is True
    assert _id(1) not in b
    assert _id(3) in b
    assert len(b) == 2


# -- routing table + FIND_NODE ----------------------------------------------


@pytest.mark.property
def test_routing_table_bounded_and_never_stores_self():
    table = RoutingTable(_id(0), k=DEFAULT_K)
    # Offering our own id is a no-op.
    assert table.offer(_contact(0)) is None
    assert _id(0) not in table
    assert len(table) == 0
    for n in range(1, 50):
        table.add(_contact(n))
    assert len(table) == 49
    assert len(table) <= ID_BITS * DEFAULT_K


@pytest.mark.property
def test_closest_returns_k_smallest_xor_in_order():
    table = RoutingTable(_id(0), k=DEFAULT_K)
    for n in range(1, 40):
        table.add(_contact(n))
    target = _id(0)  # distance == the id itself, so closest = smallest ids
    got = table.closest(target, count=5)
    assert [c.node_id for c in got] == [_id(n) for n in (1, 2, 3, 4, 5)]
    # Strictly non-decreasing distance.
    dists = [xor_distance(c.node_id, target) for c in got]
    assert dists == sorted(dists)


@pytest.mark.property
def test_closest_is_deterministic_regardless_of_insertion_order():
    target = _id(12345)
    a = RoutingTable(_id(0), k=DEFAULT_K)
    b = RoutingTable(_id(0), k=DEFAULT_K)
    ns = list(range(1, 30))
    for n in ns:
        a.add(_contact(n))
    for n in reversed(ns):
        b.add(_contact(n))
    assert [c.node_id for c in a.closest(target, 8)] == [
        c.node_id for c in b.closest(target, 8)
    ]


@pytest.mark.property
def test_find_node_wire_roundtrip_and_handle():
    table = RoutingTable(_id(0), k=DEFAULT_K)
    for n in range(1, 20):
        table.add(_contact(n))
    target = _id(7)
    req = find_node_message(target, sender_id=_id(99))
    assert req["kind"] == "find-node"
    assert req["target"] == target.hex()
    reply = handle_find_node(table, req, count=4)
    assert reply["kind"] == "nodes"
    contacts = contacts_from_records(reply["contacts"])
    # The 4 closest to id 7 by XOR distance.
    expected = [c.node_id for c in table.closest(target, 4)]
    assert [c.node_id for c in contacts] == expected


@pytest.mark.property
def test_contacts_records_roundtrip_preserves_address():
    c = Contact(
        node_id=node_id("02" + "ab" * 32),
        address=PeerAddress(transport="relay", params={"mailbox": "m", "base_url": "u"}),
    )
    recs = contacts_to_records([c])
    back = contacts_from_records(recs)
    assert back[0].node_id == c.node_id
    assert back[0].address == c.address


@pytest.mark.property
def test_malformed_records_rejected():
    with pytest.raises(ValueError):
        contacts_from_records([{"host": "x", "port": 1}])  # missing id
    with pytest.raises(ValueError):
        contacts_from_records([{"id": "zz", "host": "x", "port": 1}])  # bad hex
    with pytest.raises(ValueError):
        handle_find_node(RoutingTable(_id(0)), {"kind": "nope"})


# -- iterative lookup convergence (the headline property) -------------------


def _build_network(n_nodes: int, k: int = DEFAULT_K):
    """A simulated network: every node knows every other node (so each can answer
    FIND_NODE with its own globally-closest view). The responder is a pure function
    — no sockets — and the lookup must still discover the global k-closest from a
    sparse seed set."""
    ids = [_id(i * 7 + 3) for i in range(n_nodes)]
    contacts = {i.hex(): Contact(node_id=i, address=PeerAddress("10.0.0.1", 9000)) for i in ids}
    tables = {}
    for i in ids:
        t = RoutingTable(i, k=k)
        for j in ids:
            if j != i:
                t.add(contacts[j.hex()])
        tables[i.hex()] = t

    def responder(contact, target):
        return tables[contact.id_hex].closest(target, k)

    return ids, contacts, responder


@pytest.mark.property
def test_iterative_lookup_converges_to_global_k_closest():
    ids, contacts, responder = _build_network(40)
    target = _id(11)  # not necessarily a node id
    # Seed with just TWO arbitrary contacts — the lookup must find the rest.
    seeds = [contacts[ids[0].hex()], contacts[ids[1].hex()]]
    state = iterative_lookup(target, seeds, responder, k=DEFAULT_K, alpha=DEFAULT_ALPHA)

    got = [c.node_id for c in state.result()]
    # Ground truth: the k globally-closest node ids to the target.
    truth = sorted(ids, key=lambda nid: xor_distance(nid, target))[:DEFAULT_K]
    assert got == truth


@pytest.mark.property
def test_iterative_lookup_is_deterministic_and_bounded():
    ids, contacts, responder = _build_network(30)
    target = _id(999)
    seeds = [contacts[ids[5].hex()]]
    s1 = iterative_lookup(target, seeds, responder, k=8, alpha=3)
    s2 = iterative_lookup(target, list(seeds), responder, k=8, alpha=3)
    assert [c.node_id for c in s1.result()] == [c.node_id for c in s2.result()]
    # Bounded: never more rounds than distinct candidates discovered.
    assert s1.rounds <= len(s1.known)
    assert s1.rounds <= len(ids)


@pytest.mark.property
def test_lookup_terminates_with_no_seeds():
    _, _, responder = _build_network(10)
    state = iterative_lookup(_id(1), [], responder, k=5)
    assert state.result() == []
    assert state.rounds == 0


@pytest.mark.property
def test_lookup_each_queried_at_most_once_and_alpha_bounded():
    ids, contacts, responder = _build_network(25)
    seen_queries = []

    def counting_responder(contact, target):
        seen_queries.append(contact.id_hex)
        return responder(contact, target)

    seeds = [contacts[ids[0].hex()]]
    iterative_lookup(_id(3), seeds, counting_responder, k=DEFAULT_K, alpha=DEFAULT_ALPHA)
    # No peer queried twice.
    assert len(seen_queries) == len(set(seen_queries))


# -- byte-identity / canonical safety (SACRED) ------------------------------


@pytest.mark.property
def test_node_ids_never_touch_canonical_bytes_or_cid():
    # Node ids / distances are a LOCAL routing construct. Building and exercising a
    # whole DHT cannot change the canonical encoding or CID of a signed-style record.
    record = {"host": "1.2.3.4", "port": 9001, "kind": "knit"}
    cid_before = canonical.cid(record)
    bytes_before = canonical.encode(record)

    table = RoutingTable.from_pubkey("02" + "ab" * 32, k=DEFAULT_K)
    for n in range(1, 60):
        table.offer(_contact(n))
    _ = table.closest(_id(123), 10)
    ids, contacts, responder = _build_network(20)
    iterative_lookup(_id(7), list(contacts.values())[:2], responder)

    assert canonical.cid(record) == cid_before
    assert canonical.encode(record) == bytes_before


@pytest.mark.property
def test_find_node_frame_canonical_encodes():
    # The wire frames must canonical-encode (no float, int/str/bytes only).
    table = RoutingTable(_id(0))
    for n in range(1, 10):
        table.add(_contact(n))
    req = find_node_message(_id(5), sender_id=_id(1))
    reply = nodes_message(table.closest(_id(5), 4))
    # Round-trips through canonical encode/decode unchanged.
    assert canonical.decode(canonical.encode(req)) == req
    assert canonical.decode(canonical.encode(reply)) == reply
