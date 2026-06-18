"""Proofs for peer-exchange discovery: dedup, merge accounting, and gossip convergence."""

import pytest

from knitweb.core import canonical
from knitweb.p2p.discovery import (
    PEER_EXCHANGE_KIND,
    PeerDirectory,
    handle_peer_exchange,
    peer_exchange_message,
    peers_from_records,
)
from knitweb.p2p.node import PeerAddress


def _p(port: int) -> PeerAddress:
    return PeerAddress("127.0.0.1", port)


@pytest.mark.property
def test_dedup_and_deterministic_order():
    d = PeerDirectory([_p(9002), _p(9001), _p(9001)])
    assert len(d) == 2                                   # dup collapsed
    assert d.known() == [_p(9001), _p(9002)]             # sorted by host:port


@pytest.mark.property
def test_merge_accounting():
    d = PeerDirectory([_p(9001)])
    assert d.merge([_p(9001), _p(9002), _p(9003)]) == 2  # only 9002/9003 are new
    assert d.merge([_p(9002)]) == 0                      # already known
    assert len(d) == 3


@pytest.mark.property
def test_message_round_trips_canonically():
    d = PeerDirectory([_p(9001), _p(9002)])
    msg = peer_exchange_message(d)
    assert msg["kind"] == PEER_EXCHANGE_KIND
    assert canonical.decode(canonical.encode(msg)) == msg     # wire-safe
    assert peers_from_records(msg["peers"]) == [_p(9001), _p(9002)]


@pytest.mark.property
def test_exchange_makes_both_sides_learn():
    a = PeerDirectory([_p(9001)])
    b = PeerDirectory([_p(9002)])
    # A sends its peers to B; B merges + replies; A merges the reply.
    reply = handle_peer_exchange(b, peer_exchange_message(a))
    handle_peer_exchange(a, reply)
    assert _p(9002) in a and _p(9001) in b               # both learned the other


@pytest.mark.property
def test_gossip_converges_across_a_component():
    # three nodes, disjoint seeds; pairwise exchanges spread every address to all.
    a = PeerDirectory([_p(9001)])
    b = PeerDirectory([_p(9002)])
    c = PeerDirectory([_p(9003)])
    for _ in range(2):                                   # a<->b, b<->c per round
        handle_peer_exchange(a, handle_peer_exchange(b, peer_exchange_message(a)))
        handle_peer_exchange(b, handle_peer_exchange(c, peer_exchange_message(b)))
    everyone = {_p(9001), _p(9002), _p(9003)}
    for d in (a, b, c):
        assert set(d.known()) == everyone               # full convergence


@pytest.mark.property
def test_handle_rejects_bad_message():
    d = PeerDirectory()
    with pytest.raises(ValueError):
        handle_peer_exchange(d, {"kind": "not-pex", "peers": []})
    with pytest.raises(ValueError):
        peers_from_records([{"host": "x"}])              # missing port
