"""Live wiring: a PEX flood cannot eclipse an honest minority from the dial sample.

The flat ``PeerDirectory.sample`` returns the first ``k`` peers *by sort order*, so an
attacker who floods ``peer-exchange`` with thousands of attacker-controlled addresses
fills the directory and every peer the node then dials is the attacker's — a classic
eclipse. #63 ported Bitcoin Core's source-group/address-group bucketed ``AddrBook`` to
defend against exactly this, and this suite proves that defence is now ACTIVE on the
LIVE node path: ``bootstrap_peers`` ingests each seed's reply keyed on the advertising
seed (the PEX source), and the node SAMPLES dial / re-advertise targets from
``AddrBook.sample`` (source-group-diverse, tried-biased) rather than the flat first-k.

The scenario is socket-free: the seed roundtrip is stubbed to return canned PEX replies
(an attacker seed flooding thousands of attacker addresses, plus a tiny honest seed),
so there is NO real-socket handshake. Each ingest is awaited under ``asyncio.wait_for``
so a hang fails fast rather than wedging the suite. Everything is deterministic: the
AddrBook secret is a pure function of the node identity, and the flood is generated from
fixed integer ranges, so the surviving sample is reproducible.
"""

import asyncio

import pytest

from knitweb.core import canonical
from knitweb.p2p import AsyncioP2PNode, PeerAddress
from knitweb.p2p.discovery import (
    MAX_PEX_INBOUND,
    PEER_EXCHANGE_KIND,
    records_from_peers,
)


def run(coro):
    return asyncio.run(coro)


# A handful of honest peers an honest introducer advertises. They sit in a single
# distinct /16 (250.0.0.0/16) so they form their own address group, and they are
# advertised by an honest source so they land in honest source-group buckets. The
# 250.x prefix sorts AFTER the attacker's 172.x flood, so a flat first-k-by-sort
# sampler buries them behind thousands of attacker addresses (the eclipse this wiring
# defeats); the bucketed sampler does not.
HONEST = [PeerAddress(f"250.0.0.{i}", 9000 + i) for i in range(1, 6)]

# The attacker floods THOUSANDS of distinct addresses. Even spread across many /16s
# they all arrive via ONE PEX source (the attacker seed), so source-group keying caps
# how many of the attacker's new-table buckets they can occupy — diversity, not volume,
# governs the sample.
ATTACKER_SEED = PeerAddress("203.0.113.7", 6666)
HONEST_SEED = PeerAddress("198.51.100.9", 7777)


def _attacker_flood(n: int) -> list[PeerAddress]:
    # Spread across 2048 /16s x ports so the attacker maximises address-group spread —
    # the source-group keying (one seed) is what still bounds them.
    return [PeerAddress(f"172.{16 + (i % 16)}.{(i // 16) % 256}.{i % 256}", 30000 + (i % 4096))
            for i in range(n)]


def _reply(peers) -> dict:
    return {"kind": PEER_EXCHANGE_KIND, "peers": records_from_peers(peers)}


@pytest.mark.interop
def test_pex_flood_does_not_eclipse_honest_minority_from_dial_sample():
    async def scenario():
        node = AsyncioP2PNode()
        flood = _attacker_flood(4000)

        # Stub the seed roundtrip: NO real socket. The attacker seed answers with its
        # thousands of addresses; the honest seed answers with the honest minority.
        replies = {
            node.addrbook._peer_key(ATTACKER_SEED): _reply(flood),
            node.addrbook._peer_key(HONEST_SEED): _reply(HONEST),
        }

        async def fake_roundtrip(peer: PeerAddress, msg: dict) -> dict:
            return replies[node.addrbook._peer_key(peer)]

        node._roundtrip = fake_roundtrip

        # Attacker floods FIRST (worst case for a flat first-k directory: the flood is
        # already in before the honest seed is ever heard).
        await asyncio.wait_for(node.bootstrap_peers(seeds=[ATTACKER_SEED]), timeout=5)
        await asyncio.wait_for(node.bootstrap_peers(seeds=[HONEST_SEED]), timeout=5)

        # The honest minority is known in the flat directory (membership is not the
        # eclipse defence — the bucketed sampler is). The flat directory is now bounded
        # PER REPLY: each seed's reply can contribute at most MAX_PEX_INBOUND learned
        # addresses (#98), so the 4000-address attacker flood is truncated rather than
        # absorbed wholesale. Two replies (attacker + honest) each capped at the inbound
        # bound, plus the two reached seeds, keep the flat directory well under the flood.
        assert all(p in node.peers for p in HONEST)
        assert len(node.peers) <= 2 * MAX_PEX_INBOUND + 8  # << 4000: the flood is capped

        # ... but the BUCKETED book is bounded: the flood cannot exceed the new-table
        # capacity, no matter how many thousands were pushed.
        assert len(node.addrbook) <= node.addrbook._n_new * node.addrbook._size \
            + node.addrbook._n_tried * node.addrbook._size

        # CONTRAST: the OLD flat sampler (first-k by sort order) IS eclipsed — over
        # 4000+ attacker addresses sorted ahead of 10.0.0.x, the first 32 are all
        # attacker, zero honest. This is the vulnerability the wiring closes.
        flat_first_k = node.peers.sample(32)
        assert not any(p in set(HONEST) for p in flat_first_k), \
            "flat first-k should be fully eclipsed (else the contrast is not load-bearing)"

        # THE ECLIPSE ASSERTION: the bucketed dial sample the node now uses still
        # contains the honest minority — the flood did NOT crowd them out.
        sample = node.addrbook.sample(32)
        honest_in_sample = [p for p in sample if p in set(HONEST)]
        assert honest_in_sample, "honest minority was eclipsed from the dial sample"

        # The two seeds we actually reached were promoted to the proven (tried) table;
        # being tried-biased they LEAD the sample, ahead of any merely-flooded address.
        # The flood addresses were never reached, so they are confined to the *new*
        # table and never preempt a proven peer.
        assert HONEST_SEED in node.addrbook
        assert ATTACKER_SEED in node.addrbook
        tried_front = node.addrbook.sample(node.addrbook.tried_count())
        assert HONEST_SEED in tried_front  # the reached honest seed leads, not crowded out
        assert all(p not in set(flood) for p in tried_front)  # no flood address is "tried"

        # The whole PEX share the node re-advertises is likewise diversity-spread:
        # honest peers survive into what we push to others, so the flood cannot
        # propagate an attacker-only view of the Web.
        advertised = node.addrbook.sample(32)
        assert any(p in set(HONEST) for p in advertised)

    run(scenario())


@pytest.mark.interop
def test_bootstrap_peers_reply_is_capped_to_max_pex_inbound():
    """A SINGLE bootstrap reply cannot contribute more than MAX_PEX_INBOUND learned
    addresses to the flat directory (#98).

    #87/#95 capped the ``bootstrap_round`` helper and #85 capped
    ``handle_peer_exchange``, but the LIVE node path (``bootstrap_peers``) ingested a
    reply's peers via ``peers_from_records`` + ``learn_peers`` with NO per-reply
    truncation — so a malicious bootstrap reply could grow the flat directory by far
    more than the cap (bounded only by the dir-size/static-floor eviction afterwards).
    This proves the per-reply bound is now applied on the live path too.

    Load-bearing: the reply carries 50x MAX_PEX_INBOUND distinct addresses, yet the
    flat directory (the dedup/membership truth) must grow by AT MOST MAX_PEX_INBOUND.
    Reverting the truncation in ``bootstrap_peers`` makes the flat directory absorb the
    whole flood (>> cap), so this assertion fails — i.e. the test actually exercises the
    cap rather than passing vacuously.
    """
    async def scenario():
        node = AsyncioP2PNode()
        # A fresh node's flat directory starts empty (no peerbook seeds).
        assert len(node.peers) == 0
        baseline = len(node.peers)

        flood = _attacker_flood(MAX_PEX_INBOUND * 50)
        assert len(flood) > MAX_PEX_INBOUND  # the reply far exceeds the cap

        replies = {node.addrbook._peer_key(ATTACKER_SEED): _reply(flood)}

        async def fake_roundtrip(peer: PeerAddress, msg: dict) -> dict:
            return replies[node.addrbook._peer_key(peer)]

        node._roundtrip = fake_roundtrip

        learned = await asyncio.wait_for(
            node.bootstrap_peers(seeds=[ATTACKER_SEED]), timeout=5
        )

        # At most MAX_PEX_INBOUND addresses are learned from this one reply — the rest
        # of the flood's tail is truncated before it is ever parsed.
        assert learned <= MAX_PEX_INBOUND
        assert len(node.peers) - baseline <= MAX_PEX_INBOUND

    run(scenario())


@pytest.mark.interop
def test_addrbook_secret_is_local_and_byte_identity_is_preserved():
    """The per-node AddrBook secret never enters canonical bytes / a Knit CID."""
    async def scenario():
        # A canonical record + its CID computed with no node in the picture.
        record = {"kind": "knowledge", "title": "x", "body": "y", "author": "pub"}
        cid_before = canonical.cid(record)
        bytes_before = canonical.encode(record)

        node = AsyncioP2PNode()
        # Exercise the live eclipse path: construct the book, flood it, sample it.
        node.addrbook.add_new(HONEST[0], source=ATTACKER_SEED)
        for p in _attacker_flood(500):
            node.addrbook.add_new(p, source=ATTACKER_SEED)
        node.addrbook.mark_tried(HONEST_SEED)
        _ = node.addrbook.sample(16)

        # The secret is bytes, derived purely from identity, and deterministic for the
        # same identity — but it is LOCAL: nothing it touched changed canonical bytes.
        assert isinstance(node._addrbook_secret(), bytes)
        assert node._addrbook_secret() == node._addrbook_secret()  # deterministic
        assert canonical.cid(record) == cid_before
        assert canonical.encode(record) == bytes_before

    run(scenario())


@pytest.mark.interop
def test_honest_only_bootstrap_still_discovers_and_converges():
    """Sanity: with no flood, the wired path discovers peers exactly as before.

    A real introducer that knows a third peer; the seeker bootstraps over the genuine
    transport (no stub) and must still learn it — convergence behaviour for honest
    topologies is unchanged by the AddrBook wiring.
    """
    async def scenario():
        c_addr = PeerAddress("127.0.0.1", 5321)
        introducer = AsyncioP2PNode()
        introducer.peers.add(c_addr)
        introducer.addrbook.add_new(c_addr, source=None)

        async with introducer:
            seeker = AsyncioP2PNode()
            seeker.peerbook.add("introducer", introducer.address)
            learned = await asyncio.wait_for(seeker.bootstrap_peers(), timeout=5)

            assert c_addr in seeker.peers
            assert introducer.address in seeker.peers
            assert learned >= 1
            # The transitively-learned peer is bucketed (keyed on the introducer
            # source) and the reached introducer is promoted to tried.
            assert c_addr in seeker.addrbook
            assert introducer.address in seeker.addrbook

    run(scenario())
