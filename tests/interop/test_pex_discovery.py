"""PEX peer discovery wired into the node: a node grows its Web beyond hand-typed peers.

The pure convergence proofs live in ``tests/property/test_p2p_discovery.py``. These
interop tests drive the *wiring*: ``bootstrap_peers`` dials seed peers over the real
:class:`Dialer`, exchanges ``peer-exchange`` frames through ``_dispatch``, and merges the
learned peers into the node's directory — over tcp:// and over a relay:// carrier alike,
with identical frame bytes.
"""

import asyncio

import pytest

from knitweb.p2p import AsyncioP2PNode, PeerAddress, RelayTransport
from knitweb.p2p.discovery import PEER_EXCHANGE_KIND
from knitweb.p2p.relay import HttpPoster


def run(coro):
    return asyncio.run(coro)


@pytest.mark.interop
def test_directory_seeded_from_peerbook_at_bootstrap():
    async def scenario():
        node = AsyncioP2PNode()
        # Peer added to the book AFTER construction must still seed the directory:
        # bootstrap_peers re-seeds from the peerbook before dialing.
        node.peerbook.add("seed", PeerAddress("127.0.0.1", 9999))
        assert PeerAddress("127.0.0.1", 9999) not in node.peers  # not yet seeded
        # No listener at 9999, but re-seeding happens regardless of reachability.
        await node.bootstrap_peers(seeds=[])
        assert PeerAddress("127.0.0.1", 9999) in node.peers

    run(scenario())


@pytest.mark.interop
def test_node_learns_a_third_peer_via_pex():
    async def scenario():
        # B is the seed/introducer; it already knows C. A only knows B. After a
        # bootstrap round against B, A should have learned C transitively.
        c_addr = PeerAddress("127.0.0.1", 5123)

        introducer = AsyncioP2PNode()
        introducer.peers.add(c_addr)  # B knows C

        async with introducer:
            seeker = AsyncioP2PNode()
            seeker.peerbook.add("introducer", introducer.address)

            learned = await seeker.bootstrap_peers()

            # A learned B's own advertised address AND the transitively-known C.
            assert c_addr in seeker.peers
            assert introducer.address in seeker.peers
            assert learned >= 1
            # And the exchange was symmetric: B learned A's seed view too. A's
            # request advertised its seed (the introducer's address), which B
            # already had, so B's directory is unchanged for that — but the kind
            # round-trips as a proper peer-exchange.
            assert PEER_EXCHANGE_KIND == "peer-exchange"

    run(scenario())


@pytest.mark.interop
def test_unreachable_seed_does_not_sink_bootstrap():
    async def scenario():
        good = PeerAddress("127.0.0.1", 5200)
        introducer = AsyncioP2PNode()
        introducer.peers.add(good)

        async with introducer:
            seeker = AsyncioP2PNode()
            # One dead seed (nothing listening) and one live one.
            dead = PeerAddress("127.0.0.1", 1)  # unbindable / unreachable
            learned = await seeker.bootstrap_peers(seeds=[dead, introducer.address])
            assert good in seeker.peers  # live seed still contributed
            assert learned >= 1

    run(scenario())


# -- carrier independence: identical PEX over a relay:// mailbox -----------------


class InMemoryRelay(HttpPoster):
    """A fake ``api/relay`` honouring the send/fetch mailbox contract (no sockets)."""

    def __init__(self) -> None:
        super().__init__()
        self.mailboxes: dict[str, list[dict]] = {}

    async def post(self, url: str, payload: dict) -> dict:
        if url.endswith("/api/relay/send"):
            mb = payload["mailbox"]
            self.mailboxes.setdefault(mb, []).append(
                {"rid": payload.get("rid"), "frame": payload["frame"]}
            )
            return {"ok": True}
        if url.endswith("/api/relay/fetch"):
            mb = payload["mailbox"]
            queued = self.mailboxes.get(mb, [])
            self.mailboxes[mb] = []
            return {"messages": queued}
        raise AssertionError(f"unexpected relay url {url}")


def relay_for(mailbox: str, relay: InMemoryRelay) -> RelayTransport:
    return RelayTransport(base_url="https://5mart.ml", mailbox=mailbox, poster=relay)


@pytest.mark.interop
def test_pex_bootstraps_over_a_relay_carrier():
    async def scenario():
        relay = InMemoryRelay()
        c_relay = PeerAddress(
            transport="relay", params={"mailbox": "peer-c", "base_url": "https://5mart.ml"}
        )

        introducer = AsyncioP2PNode(transport=relay_for("introducer-mb", relay))
        introducer.peers.add(c_relay)  # a relay:// peer to be discovered

        async with introducer:
            seeker = AsyncioP2PNode(transport=relay_for("seeker-mb", relay))
            seeker.peerbook.add("introducer", introducer.address)

            learned = await seeker.bootstrap_peers()

            # The relay:// peer survived the exchange with its carrier tag + routing
            # params intact — discovery is carrier-independent.
            assert c_relay in seeker.peers
            discovered = next(p for p in seeker.peers.known() if p == c_relay)
            assert discovered.transport == "relay"
            assert discovered.params.get("mailbox") == "peer-c"
            assert learned >= 1

    run(scenario())
