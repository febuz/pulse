"""Peer discovery via gossip exchange — grow the web beyond hand-configured peers.

The Phase-3 MVP node bootstraps from a ``StaticPeerBook`` (peers you typed in). For
the web to actually *grow*, peers must learn about each other: a node tells a peer the
addresses it knows, the peer merges them and replies with its own, and over a few
rounds the whole component converges on the same peer set — classic peer-exchange (PEX),
the same bootstrap Bitcoin/libp2p use before a full DHT.

This module is the **transport-free core**: a :class:`PeerDirectory` (dedup + merge +
deterministic sample) plus the canonical-CBOR ``peer-exchange`` message and a pure
``handle_peer_exchange`` that merges-and-replies. Wiring it into ``AsyncioP2PNode`` as a
request handler is a thin follow-up; keeping the logic pure makes convergence provable
without sockets. A real Kademlia DHT can later replace PEX behind the same directory.
"""

from __future__ import annotations

from .node import PeerAddress

__all__ = [
    "PEER_EXCHANGE_KIND",
    "PeerDirectory",
    "peers_from_records",
    "peer_exchange_message",
    "handle_peer_exchange",
]

PEER_EXCHANGE_KIND = "peer-exchange"


def _key(peer: PeerAddress) -> str:
    return f"{peer.host}:{peer.port}"


class PeerDirectory:
    """A deduplicated, mergeable set of known peers (keyed by host:port)."""

    def __init__(self, seeds: "list[PeerAddress] | tuple[PeerAddress, ...]" = ()) -> None:
        self._peers: dict[str, PeerAddress] = {}
        for p in seeds:
            self.add(p)

    def add(self, peer: PeerAddress) -> None:
        self._peers[_key(peer)] = peer

    def __len__(self) -> int:
        return len(self._peers)

    def __contains__(self, peer: PeerAddress) -> bool:
        return _key(peer) in self._peers

    def known(self) -> list[PeerAddress]:
        """All known peers in deterministic (host:port-sorted) order."""
        return [self._peers[k] for k in sorted(self._peers)]

    def merge(self, peers: "list[PeerAddress] | tuple[PeerAddress, ...]") -> int:
        """Add any peers not already known; return how many were newly learned."""
        learned = 0
        for p in peers:
            if _key(p) not in self._peers:
                self._peers[_key(p)] = p
                learned += 1
        return learned

    def sample(self, k: int | None = None) -> list[PeerAddress]:
        """A deterministic subset of known peers to share (first ``k`` by sort order)."""
        peers = self.known()
        return peers if k is None else peers[:k]

    def to_records(self, k: int | None = None) -> list[dict]:
        """Canonical-CBOR-friendly peer records (integer/string only)."""
        return [{"host": p.host, "port": p.port} for p in self.sample(k)]


def peers_from_records(records: list) -> list[PeerAddress]:
    """Reconstruct PeerAddresses from wire records; raises on malformed input."""
    out: list[PeerAddress] = []
    if not isinstance(records, list):
        raise ValueError("peers must be a list")
    for r in records:
        if not isinstance(r, dict) or "host" not in r or "port" not in r:
            raise ValueError("each peer record needs host + port")
        if not isinstance(r["host"], str) or not isinstance(r["port"], int) or isinstance(r["port"], bool):
            raise ValueError("peer host must be str, port must be int")
        out.append(PeerAddress(host=r["host"], port=r["port"]))
    return out


def peer_exchange_message(directory: PeerDirectory, k: int | None = None) -> dict:
    """Build a ``peer-exchange`` message advertising (a sample of) our known peers."""
    return {"kind": PEER_EXCHANGE_KIND, "peers": directory.to_records(k)}


def handle_peer_exchange(directory: PeerDirectory, msg: dict, share_k: int | None = None) -> dict:
    """Merge the peers in ``msg`` into ``directory`` and return a reply sharing ours.

    Pure: no sockets. Raises ValueError on a non-peer-exchange or malformed message.
    """
    if not isinstance(msg, dict) or msg.get("kind") != PEER_EXCHANGE_KIND:
        raise ValueError("not a peer-exchange message")
    directory.merge(peers_from_records(msg.get("peers") or []))
    return peer_exchange_message(directory, share_k)
