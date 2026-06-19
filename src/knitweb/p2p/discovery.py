"""Peer discovery via gossip exchange — grow the web beyond hand-configured peers.

The Phase-3 MVP node bootstraps from a ``StaticPeerBook`` (peers you typed in). For
the web to actually *grow*, peers must learn about each other: a node tells a peer the
addresses it knows, the peer merges them and replies with its own, and over a few
rounds the whole component converges on the same peer set — classic peer-exchange (PEX),
the same bootstrap Bitcoin/libp2p use before a full DHT.

This module is the **transport-free core**: a :class:`PeerDirectory` (dedup + merge +
deterministic sample) plus the canonical-CBOR ``peer-exchange`` message and a pure
``handle_peer_exchange`` that merges-and-replies. The node-facing glue
(:func:`directory_from_peerbook` seeds a directory from a node's ``StaticPeerBook``;
:func:`bootstrap_round` runs one pure exchange against a peer's reply) keeps the wiring
in ``AsyncioP2PNode`` carrier-agnostic — identical frame bytes travel over tcp:// or
relay://. Keeping the logic pure makes convergence provable without sockets, and a real
Kademlia DHT can later replace PEX behind the same directory.

``PeerAddress`` is imported from ``.transport`` (its home) rather than ``.node`` so the
node module can import this glue at top level without an import cycle.
"""

from __future__ import annotations

from .addrbook import AddrBook
from .transport import PeerAddress

__all__ = [
    "PEER_EXCHANGE_KIND",
    "DEFAULT_SHARE_K",
    "PeerDirectory",
    "peers_from_records",
    "peer_exchange_message",
    "handle_peer_exchange",
    "directory_from_peerbook",
    "bootstrap_round",
    "records_from_peers",
    "addrbook_share_message",
    "learn_peers",
]

PEER_EXCHANGE_KIND = "peer-exchange"

# Deterministic cap on how many peers a single exchange advertises, so a node's
# share never grows with the (unbounded) directory: a bounded frame and bounded
# compute regardless of how large the Web becomes. ``None`` anywhere means "all".
DEFAULT_SHARE_K = 32


def _key(peer: PeerAddress) -> str:
    # Carrier-aware key. A relay:// peer routes by its ``params`` mailbox (its
    # host/port are empty), while a tcp:// peer routes by host:port — so both the
    # transport tag *and* the sorted params participate in dedup. Two endpoints that
    # differ only by carrier, or only by relay mailbox, stay distinct.
    suffix = "".join(f";{k}={v}" for k, v in sorted(peer.params.items()))
    return f"{peer.transport}://{peer.host}:{peer.port}{suffix}"


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

    def sample(self, k: "int | None" = DEFAULT_SHARE_K) -> list[PeerAddress]:
        """A deterministic subset of known peers to share (first ``k`` by sort order).

        Defaults to :data:`DEFAULT_SHARE_K` so an advertised share is bounded even as
        the directory grows; pass ``k=None`` to share everything (small components).
        """
        peers = self.known()
        return peers if k is None else peers[:k]

    def to_records(self, k: "int | None" = DEFAULT_SHARE_K) -> list[dict]:
        """Canonical-CBOR-friendly peer records (integer/string only).

        Records carry the carrier tag (and any relay routing ``params``) so a
        relay:// peer survives the exchange intact; a bare host/port record (the
        original shape) still decodes to the tcp default.
        """
        return records_from_peers(self.sample(k))


def records_from_peers(peers: "list[PeerAddress] | tuple[PeerAddress, ...]") -> list[dict]:
    """Canonical-CBOR-friendly peer records for an explicit peer list.

    The wire shape PEX advertises, factored out so a flat :class:`PeerDirectory`
    sample *and* an :class:`~knitweb.p2p.addrbook.AddrBook`-diverse sample serialise
    to byte-identical records. The carrier tag / relay ``params`` ride along so a
    relay:// peer survives the exchange; a bare host/port record (the original
    shape) still decodes to the tcp default.
    """
    records: list[dict] = []
    for p in peers:
        rec: dict = {"host": p.host, "port": p.port}
        if p.transport != "tcp":
            rec["transport"] = p.transport
        if p.params:
            rec["params"] = dict(p.params)
        records.append(rec)
    return records


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
        transport = r.get("transport", "tcp")
        if not isinstance(transport, str):
            raise ValueError("peer transport must be str")
        params = r.get("params", {})
        if not isinstance(params, dict) or not all(
            isinstance(kk, str) and isinstance(vv, str) for kk, vv in params.items()
        ):
            raise ValueError("peer params must be a str->str map")
        out.append(
            PeerAddress(host=r["host"], port=r["port"], transport=transport, params=dict(params))
        )
    return out


def peer_exchange_message(directory: PeerDirectory, k: "int | None" = DEFAULT_SHARE_K) -> dict:
    """Build a ``peer-exchange`` message advertising (a sample of) our known peers."""
    return {"kind": PEER_EXCHANGE_KIND, "peers": directory.to_records(k)}


def handle_peer_exchange(
    directory: PeerDirectory, msg: dict, share_k: "int | None" = DEFAULT_SHARE_K
) -> dict:
    """Merge the peers in ``msg`` into ``directory`` and return a reply sharing ours.

    Pure: no sockets. Raises ValueError on a non-peer-exchange or malformed message.
    """
    if not isinstance(msg, dict) or msg.get("kind") != PEER_EXCHANGE_KIND:
        raise ValueError("not a peer-exchange message")
    directory.merge(peers_from_records(msg.get("peers") or []))
    return peer_exchange_message(directory, share_k)


# -- node-facing glue -----------------------------------------------------------


def directory_from_peerbook(peerbook, extra: "list[PeerAddress] | None" = None) -> PeerDirectory:
    """Seed a :class:`PeerDirectory` from a node's ``StaticPeerBook`` (the hand-typed
    peers) plus any ``extra`` addresses (e.g. the node's own advertised address).

    Pure glue: it only reads ``peerbook.all()`` (name -> :class:`PeerAddress`), so it
    works for any peerbook honouring that shape and stays socket-free.
    """
    directory = PeerDirectory(list(peerbook.all().values()))
    for peer in extra or ():
        directory.add(peer)
    return directory


def bootstrap_round(
    directory: PeerDirectory, reply: dict, share_k: "int | None" = DEFAULT_SHARE_K
) -> int:
    """Fold a peer's ``peer-exchange`` reply into ``directory``; return peers learned.

    The outbound half of a bootstrap exchange: the node has already sent its own
    :func:`peer_exchange_message` over the wire and received ``reply`` back; this
    merges the freshly-learned peers and reports how many were new. ``share_k`` is
    accepted for symmetry with :func:`handle_peer_exchange` (the caller's request was
    built with the same bound) and ignored here since merging shares nothing.
    """
    if not isinstance(reply, dict) or reply.get("kind") != PEER_EXCHANGE_KIND:
        raise ValueError("not a peer-exchange reply")
    return directory.merge(peers_from_records(reply.get("peers") or []))


# -- eclipse-resistant sampling (AddrBook live path) ----------------------------


def learn_peers(
    directory: PeerDirectory,
    book: AddrBook,
    peers: "list[PeerAddress] | tuple[PeerAddress, ...]",
    *,
    source: "PeerAddress | None" = None,
) -> int:
    """Fold ``peers`` into BOTH the flat ``directory`` and the bucketed ``book``.

    ``directory`` stays the dedup/membership truth (so ``addr in node.peers`` and
    ``node.peers.known()`` are unchanged), while ``book.add_new(addr, source)``
    records the *same* address keyed on who advertised it (``source``). That
    source-group keying is the eclipse defence: a peer that floods thousands of
    attacker addresses shares one source group, so its addresses compete for a
    bounded set of buckets and cannot crowd an honest minority out of
    :meth:`AddrBook.sample`. Returns the count newly learned by the flat directory
    (the historical ``merge`` return, so callers' learned-counts are unchanged).
    """
    learned = directory.merge(peers)
    for p in peers:
        book.add_new(p, source=source)
    return learned


def addrbook_share_message(book: AddrBook, k: "int | None" = DEFAULT_SHARE_K) -> dict:
    """A ``peer-exchange`` message whose advertised peers are an AddrBook sample.

    Drop-in for :func:`peer_exchange_message` on the live path: the share is drawn
    from :meth:`AddrBook.sample` (source-group-diverse, tried-biased) rather than
    the flat first-``k`` by sort order, so neither what the node *dials* nor what it
    *re-advertises* can be dominated by a flooded group. The records are built by the
    shared :func:`records_from_peers`, so the frame bytes are identical in shape to a
    flat-directory share.
    """
    return {"kind": PEER_EXCHANGE_KIND, "peers": records_from_peers(book.sample(k))}
