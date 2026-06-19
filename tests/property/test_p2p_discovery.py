"""Proofs for peer-exchange discovery: dedup, merge accounting, and gossip convergence."""

import pytest

from knitweb.core import canonical
from knitweb.p2p.discovery import (
    MAX_DIR_SIZE,
    MAX_PEX_INBOUND,
    PEER_EXCHANGE_KIND,
    PeerDirectory,
    handle_peer_exchange,
    peer_exchange_message,
    peers_from_records,
    records_from_peers,
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


# ---------------------------------------------------------------------------
# PEX inbound cap (#40): truncation at MAX_PEX_INBOUND
# ---------------------------------------------------------------------------


def _flood_msg(n: int) -> dict:
    """A peer-exchange message carrying ``n`` distinct addresses."""
    peers = [PeerAddress(f"10.{(i // 256) % 256}.{i % 256}.1", 20000 + i) for i in range(n)]
    return {"kind": PEER_EXCHANGE_KIND, "peers": records_from_peers(peers)}


@pytest.mark.property
def test_handle_peer_exchange_truncates_at_max_pex_inbound():
    """A PEX message with more than MAX_PEX_INBOUND addresses is silently truncated."""
    n_flood = MAX_PEX_INBOUND * 3   # 3x the cap — all excess must be dropped
    d = PeerDirectory()
    handle_peer_exchange(d, _flood_msg(n_flood))
    # Exactly MAX_PEX_INBOUND peers accepted, none beyond.
    assert len(d) == MAX_PEX_INBOUND


@pytest.mark.property
def test_handle_peer_exchange_accepts_exactly_cap():
    """Exactly MAX_PEX_INBOUND peers in a message: all accepted, none dropped."""
    d = PeerDirectory()
    handle_peer_exchange(d, _flood_msg(MAX_PEX_INBOUND))
    assert len(d) == MAX_PEX_INBOUND


@pytest.mark.property
def test_handle_peer_exchange_below_cap_unchanged():
    """Fewer than MAX_PEX_INBOUND peers: all accepted (no spurious truncation)."""
    n = MAX_PEX_INBOUND // 2
    d = PeerDirectory()
    handle_peer_exchange(d, _flood_msg(n))
    assert len(d) == n


@pytest.mark.property
def test_inbound_cap_bounds_flat_dir_per_message():
    """Multiple successive floods each capped: total grows bounded by cap * rounds."""
    d = PeerDirectory()
    rounds = 5
    for r in range(rounds):
        # Each round sends a *different* set of addresses so they are all new.
        peers = [
            PeerAddress(f"192.{r}.{(i // 256) % 256}.{i % 256}", 30000 + i)
            for i in range(MAX_PEX_INBOUND * 2)
        ]
        handle_peer_exchange(d, {"kind": PEER_EXCHANGE_KIND, "peers": records_from_peers(peers)})
    # Each round added at most MAX_PEX_INBOUND new entries.
    assert len(d) <= MAX_PEX_INBOUND * rounds


# ---------------------------------------------------------------------------
# Static-peer floor (#40): seeds survive a PEX flood
# ---------------------------------------------------------------------------


@pytest.mark.property
def test_static_peers_survive_dir_flood():
    """Static/seed peers are never evicted even when the directory is flooded past MAX_DIR_SIZE."""
    # Two hand-configured seeds marked static at construction.
    seed_a = PeerAddress("10.0.0.1", 9001)
    seed_b = PeerAddress("10.0.0.2", 9002)
    d = PeerDirectory([seed_a, seed_b])         # __init__ marks them static

    # Flood MAX_DIR_SIZE extra learned peers through merge.
    flood = [PeerAddress(f"172.{(i // 256) % 256}.{i % 256}.1", 40000 + i) for i in range(MAX_DIR_SIZE)]
    d.merge(flood)

    # Static seeds must still be present regardless of directory size pressure.
    assert seed_a in d
    assert seed_b in d


@pytest.mark.property
def test_static_floor_keeps_dir_memory_bounded():
    """Even with repeated floods the flat directory stays at or below MAX_DIR_SIZE."""
    d = PeerDirectory([_p(9001)])               # one static seed

    # Send multiple floods — each with fresh addresses — through merge.
    total_injected = 0
    for batch in range(10):
        peers = [
            PeerAddress(f"172.{batch}.{(i // 256) % 256}.{i % 256}", 50000 + i)
            for i in range(MAX_DIR_SIZE // 2)
        ]
        d.merge(peers)
        total_injected += len(peers)

    # The directory must never grow past the hard cap.
    assert len(d) <= MAX_DIR_SIZE
    assert total_injected > MAX_DIR_SIZE          # sanity: we actually tried to overflow


@pytest.mark.property
def test_static_peer_added_after_construction_protected():
    """A peer explicitly marked static (via mark_static) survives eviction pressure."""
    d = PeerDirectory()
    special = PeerAddress("198.51.100.99", 7777)
    d.add(special, static=True)

    # Flood enough to trigger eviction of non-static entries.
    flood = [PeerAddress(f"172.{(i // 256) % 256}.{i % 256}.1", 40000 + i) for i in range(MAX_DIR_SIZE)]
    d.merge(flood)

    # The explicitly-static peer is never evicted.
    assert special in d
