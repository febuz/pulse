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

from collections import deque

from .addrbook import AddrBook
from .transport import PeerAddress

__all__ = [
    "PEER_EXCHANGE_KIND",
    "DEFAULT_SHARE_K",
    "MAX_PEX_INBOUND",
    "MAX_DIR_SIZE",
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

# Maximum number of peer addresses accepted from a single inbound PEX message.
# Any addresses beyond this cap are silently truncated: a peer that sends more
# gets only the first MAX_PEX_INBOUND merged, so a flooded PEX message cannot
# grow the flat directory by more than this many entries per message.
MAX_PEX_INBOUND = 64

# Hard ceiling on the number of entries the flat PeerDirectory may hold.
# When a merge would push past this cap the oldest learned entries (in insertion
# order, non-static first) are dropped to make room — static/seed peers that
# were seeded at construction are NEVER evicted (the static-peer floor).
MAX_DIR_SIZE = 4096


def _key(peer: PeerAddress) -> str:
    # Carrier-aware key. A relay:// peer routes by its ``params`` mailbox (its
    # host/port are empty), while a tcp:// peer routes by host:port — so both the
    # transport tag *and* the sorted params participate in dedup. Two endpoints that
    # differ only by carrier, or only by relay mailbox, stay distinct.
    suffix = "".join(f";{k}={v}" for k, v in sorted(peer.params.items()))
    return f"{peer.transport}://{peer.host}:{peer.port}{suffix}"


class PeerDirectory:
    """A deduplicated, mergeable set of known peers (keyed by host:port).

    Peers are split into two tiers:

    * **Static peers** — seeded at construction (from the hand-configured
      ``StaticPeerBook``).  They are marked at ``add`` time (or via
      :meth:`mark_static`) and are *never* evicted by the size cap.  The
      static-peer floor ensures that a PEX flood cannot displace the
      pre-configured seeds a node operator typed in.

    * **Learned peers** — merged in from PEX replies.  When the directory
      reaches :data:`MAX_DIR_SIZE` the oldest learned entries are dropped to
      make room; static peers are always kept.
    """

    def __init__(self, seeds: "list[PeerAddress] | tuple[PeerAddress, ...]" = ()) -> None:
        self._peers: dict[str, PeerAddress] = {}
        # Keys of peers that were explicitly seeded at construction: never evicted.
        self._static: set[str] = set()
        # Insertion-ordered queue of learned (non-static) keys for LRU eviction.
        # A deque gives O(1) ``popleft`` eviction at the MAX_DIR_SIZE cap (a list's
        # ``pop(0)`` is O(n) and would dominate under a sustained PEX flood).
        self._learned_order: deque[str] = deque()
        for p in seeds:
            self.add(p, static=True)

    def add(self, peer: PeerAddress, *, static: bool = False) -> None:
        k = _key(peer)
        is_new = k not in self._peers
        self._peers[k] = peer
        if static:
            self._static.add(k)
            # If a previously-learned peer is promoted to static, remove it from
            # the eviction queue so it is never accidentally dropped.
            try:
                self._learned_order.remove(k)
            except ValueError:
                pass
        elif is_new and k not in self._static:
            # Brand-new learned entry: track insertion order for eviction.
            self._learned_order.append(k)

    def mark_static(self, peer: PeerAddress) -> None:
        """Promote ``peer`` to the static tier so it is never evicted."""
        k = _key(peer)
        self._static.add(k)
        try:
            self._learned_order.remove(k)
        except ValueError:
            pass

    def __len__(self) -> int:
        return len(self._peers)

    def __contains__(self, peer: PeerAddress) -> bool:
        return _key(peer) in self._peers

    def known(self) -> list[PeerAddress]:
        """All known peers in deterministic (host:port-sorted) order."""
        return [self._peers[k] for k in sorted(self._peers)]

    def merge(
        self,
        peers: "list[PeerAddress] | tuple[PeerAddress, ...]",
        *,
        max_size: int = MAX_DIR_SIZE,
    ) -> int:
        """Add any peers not already known; return how many were newly learned.

        When the directory would exceed ``max_size`` after adding a new entry, the
        oldest *learned* (non-static) peer is evicted to make room — static/seed
        peers are NEVER removed regardless of directory size (the static-peer floor).
        If the directory is already at or above ``max_size`` and consists entirely of
        static peers, the incoming peer is silently skipped.
        """
        learned = 0
        for p in peers:
            k = _key(p)
            if k in self._peers:
                # LRU refresh: a re-advertised learned peer moves to the back of the
                # eviction queue, so actively-gossiped peers outlive stale ones under
                # the size cap. Static peers aren't in the queue, so skip them.
                if k not in self._static:
                    try:
                        self._learned_order.remove(k)
                    except ValueError:
                        pass
                    self._learned_order.append(k)
                continue  # already known — dedup
            # Enforce the size cap before inserting: evict the oldest learned entry
            # if needed. If nothing is evictable (all static), skip this peer.
            while len(self._peers) >= max_size and self._learned_order:
                evict_key = self._learned_order.popleft()
                del self._peers[evict_key]
            if len(self._peers) >= max_size:
                # Directory is full and all entries are static — cannot make room.
                continue
            self._peers[k] = p
            self._learned_order.append(k)
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
    directory: PeerDirectory,
    msg: dict,
    share_k: "int | None" = DEFAULT_SHARE_K,
    *,
    inbound_cap: int = MAX_PEX_INBOUND,
) -> dict:
    """Merge the peers in ``msg`` into ``directory`` and return a reply sharing ours.

    Pure: no sockets. Raises ValueError on a non-peer-exchange or malformed message.

    At most ``inbound_cap`` addresses (default :data:`MAX_PEX_INBOUND`) are accepted
    per call — any excess records are silently truncated.  This bounds the per-message
    memory growth: no single PEX message can grow the flat directory by more than
    ``inbound_cap`` entries regardless of how many the remote peer sends.
    """
    if not isinstance(msg, dict) or msg.get("kind") != PEER_EXCHANGE_KIND:
        raise ValueError("not a peer-exchange message")
    raw = msg.get("peers") or []
    # Truncate before parsing: reject the tail so even malformed excess records
    # are never touched.
    truncated = raw[:inbound_cap]
    directory.merge(peers_from_records(truncated))
    return peer_exchange_message(directory, share_k)


# -- node-facing glue -----------------------------------------------------------


def directory_from_peerbook(peerbook, extra: "list[PeerAddress] | None" = None) -> PeerDirectory:
    """Seed a :class:`PeerDirectory` from a node's ``StaticPeerBook`` (the hand-typed
    peers) plus any ``extra`` addresses (e.g. the node's own advertised address).

    Pure glue: it only reads ``peerbook.all()`` (name -> :class:`PeerAddress`), so it
    works for any peerbook honouring that shape and stays socket-free.

    All peerbook peers and ``extra`` addresses are marked **static** in the resulting
    directory so the size-cap eviction logic in :meth:`PeerDirectory.merge` never
    displaces them — the static-peer floor guarantees hand-configured seeds survive
    any PEX flood.
    """
    directory = PeerDirectory(list(peerbook.all().values()))  # seeds → static via __init__
    for peer in extra or ():
        directory.add(peer, static=True)
    return directory


def bootstrap_round(
    directory: PeerDirectory,
    reply: dict,
    share_k: "int | None" = DEFAULT_SHARE_K,
    *,
    inbound_cap: int = MAX_PEX_INBOUND,
) -> int:
    """Fold a peer's ``peer-exchange`` reply into ``directory``; return peers learned.

    The outbound half of a bootstrap exchange: the node has already sent its own
    :func:`peer_exchange_message` over the wire and received ``reply`` back; this
    merges the freshly-learned peers and reports how many were new. ``share_k`` is
    accepted for symmetry with :func:`handle_peer_exchange` (the caller's request was
    built with the same bound) and ignored here since merging shares nothing.

    At most ``inbound_cap`` addresses (default :data:`MAX_PEX_INBOUND`) are merged —
    excess records in the reply are truncated *before* parsing, mirroring the inbound
    cap in :func:`handle_peer_exchange`. Without this a malicious *reply* could flood
    the directory even though the request side is capped (#87).
    """
    if not isinstance(reply, dict) or reply.get("kind") != PEER_EXCHANGE_KIND:
        raise ValueError("not a peer-exchange reply")
    truncated = (reply.get("peers") or [])[:inbound_cap]
    return directory.merge(peers_from_records(truncated))


# -- eclipse-resistant sampling (AddrBook live path) ----------------------------


def learn_peers(
    directory: PeerDirectory,
    book: AddrBook,
    peers: "list[PeerAddress] | tuple[PeerAddress, ...]",
    *,
    source: "PeerAddress | None" = None,
    static: bool = False,
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

    Pass ``static=True`` when seeding from a hand-configured peerbook so that the
    size-cap eviction in :meth:`PeerDirectory.merge` never displaces these peers
    (the static-peer floor).
    """
    if static:
        # Add individually so they are registered in the static tier.
        learned = 0
        for p in peers:
            if p not in directory:
                directory.add(p, static=True)
                learned += 1
            else:
                # Already known — still promote to static to protect it.
                directory.mark_static(p)
    else:
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
