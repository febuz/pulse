"""Kademlia DHT — k-buckets, XOR distance, FIND_NODE + iterative node lookup.

:mod:`knitweb.p2p.discovery`'s :class:`~knitweb.p2p.discovery.PeerDirectory` grows
the Web by *peer exchange* (PEX): a node merges whatever addresses its neighbours
happen to gossip. PEX is a fine bootstrap, but it has no *structure* — there is no
way to ask "who is close to this id?", so finding a specific peer (or, later, the
peers responsible for a specific content key) means flooding or waiting for a
random walk to stumble on it. Every mature P2P discovery layer (IPFS/libp2p-kad,
Ethereum discv4/discv5, BitTorrent's mainline DHT) instead organises peers in a
**Kademlia** structured overlay so any id can be located in O(log N) hops.

This module ports the **tractable core** of Kademlia faithfully but minimally to
pure-Python stdlib, integer-only and socket-free:

  * **256-bit node ids.** A node's id is ``sha256(pubkey_hex.encode())`` — derived
    from the same compressed secp256k1 public key the identity layer
    (:mod:`knitweb.p2p.identity`) already proves control of. Ids are *bytes*; the
    XOR metric is computed on their ``int.from_bytes`` integer view.
  * **XOR distance.** ``distance(a, b) = int(a) ^ int(b)`` — a symmetric, integer
    metric. "Closer" is simply a smaller integer. No floats, no wall-clock.
  * **k-buckets.** A routing table of 256 buckets; a peer whose XOR distance from
    *us* has its highest set bit at position ``i`` lands in bucket ``i`` (the
    classic prefix-length bucketing). Each bucket is a bounded LRU of at most ``k``
    entries with **test-before-evict** (addrbook lineage): a full bucket does not
    blindly drop its oldest peer for a newcomer — it surfaces the stalest entry so
    the caller can ping it, and only evicts it if it fails to respond. A live peer
    keeps its slot; the newcomer is dropped. This is exactly Kademlia's
    least-recently-seen eviction policy, which makes the table resist churn-based
    poisoning (long-lived honest peers are sticky).
  * **FIND_NODE.** Given a target id, return the ``k`` known peers with the
    smallest XOR distance to it — the local half of a lookup. Ties (equal
    distance, impossible for distinct ids but defended anyway) break on an injected
    comparator so the result is fully deterministic.
  * **Iterative node lookup.** A deterministic, ``alpha``-bounded state machine
    over a candidate *shortlist*: each round it picks the ``alpha`` closest
    not-yet-queried candidates, a (socket-free) **injected responder callback**
    returns each queried peer's ``k`` closest known peers, the results are merged,
    and the loop terminates when a full round produces no peer closer than the
    best already seen — Kademlia's convergence condition. Because the responder is
    injected, the lookup is pure logic the node adopts later behind a real
    FIND_NODE round-trip; no asyncio, no sockets, so no test can stall.

Wire shape. ``find_node`` / ``nodes`` frames carry the target/own **node-id hex**
plus discovery-shaped :class:`~knitweb.p2p.discovery` peer records (host/port/
transport/params) reused verbatim via :func:`knitweb.p2p.discovery.peers_from_records`
and a directory's ``to_records`` shape, so a peer learned over the DHT slots
straight into the existing :class:`~knitweb.p2p.discovery.PeerDirectory` with no
new record codec.

Out of scope (deliberately — the tractable core only): STORE / FIND_VALUE value
storage, bucket-refresh timers, republish/expiry, and RTT-based ordering. This
gives ``discovery.py``'s ``PeerDirectory`` a real structured-overlay behind the
same :class:`PeerAddress` shape **without editing discovery.py**.

Byte-identity. Node ids are a *local* routing construct hashed from a public key;
they never enter a canonical-CBOR record, a Knit, a signature, or a CID. Distances
and bucket math are integer-only. Nothing here perturbs a signed-record's bytes —
:mod:`tests.property.test_kademlia` asserts a fresh Knit CID is unchanged.

Determinism: ids/distances are bytes/int only; every tie-break comparator and any
sampling RNG is *injected*; there is no wall-clock anywhere. The same inputs always
produce the same buckets, the same FIND_NODE result, and the same lookup trace.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .transport import PeerAddress

__all__ = [
    "ID_BITS",
    "ID_BYTES",
    "DEFAULT_K",
    "DEFAULT_ALPHA",
    "FIND_NODE_KIND",
    "NODES_KIND",
    "node_id",
    "node_id_hex",
    "xor_distance",
    "bucket_index",
    "Contact",
    "KBucket",
    "RoutingTable",
    "find_node_message",
    "nodes_message",
    "handle_find_node",
    "contacts_from_records",
    "contacts_to_records",
    "LookupState",
    "iterative_lookup",
]

# A node id is a SHA-256 digest: 256 bits / 32 bytes. The whole metric space.
ID_BITS = 256
ID_BYTES = 32

# Default bucket width ``k`` (Kademlia's replication parameter): the max entries a
# single k-bucket holds and the number of closest peers FIND_NODE returns. 20 is
# the canonical libp2p/Ethereum value; it is a hard per-bucket cap so the whole
# table is bounded by ID_BITS * k entries regardless of churn.
DEFAULT_K = 20

# Default lookup concurrency ``alpha``: how many of the closest unqueried
# candidates a lookup round fans out to at once. 3 is the canonical value; it
# bounds the per-round compute and message count.
DEFAULT_ALPHA = 3

# Wire kinds for the DHT request/response pair (mirrors discovery's PEER_EXCHANGE).
FIND_NODE_KIND = "find-node"
NODES_KIND = "nodes"


def node_id(pubkey_hex: str) -> bytes:
    """The 256-bit Kademlia node id for a peer: ``sha256(pubkey_hex bytes)``.

    Derived from the same compressed secp256k1 public-key hex the identity layer
    proves control of, so a node's overlay position is bound to its crypto
    identity (an attacker cannot cheaply choose where it lands without grinding a
    key whose SHA-256 has a chosen prefix). Returns raw bytes — the XOR metric and
    bucket math operate on the integer view via :func:`int.from_bytes`.
    """
    if not isinstance(pubkey_hex, str):
        raise TypeError("pubkey_hex must be str")
    return hashlib.sha256(pubkey_hex.encode("utf-8")).digest()


def node_id_hex(pubkey_hex: str) -> str:
    """The node id as lower-case hex (the wire form for ``find_node`` frames)."""
    return node_id(pubkey_hex).hex()


def _as_id_bytes(value: "bytes | str") -> bytes:
    """Coerce a node id given as raw bytes or hex string to exactly 32 bytes."""
    if isinstance(value, str):
        try:
            value = bytes.fromhex(value)
        except ValueError as exc:
            raise ValueError("node id hex is malformed") from exc
    elif not isinstance(value, (bytes, bytearray)):
        raise TypeError("node id must be bytes or hex str")
    value = bytes(value)
    if len(value) != ID_BYTES:
        raise ValueError(f"node id must be exactly {ID_BYTES} bytes")
    return value


def xor_distance(a: "bytes | str", b: "bytes | str") -> int:
    """Integer XOR distance between two node ids — Kademlia's metric.

    Symmetric (``d(a,b) == d(b,a)``), zero iff the ids are equal, and obeys the
    XOR triangle property. A *smaller* integer means *closer*. Accepts bytes or
    hex; computes on the ``int.from_bytes`` big-endian integer view. Integer-only.
    """
    ai = int.from_bytes(_as_id_bytes(a), "big")
    bi = int.from_bytes(_as_id_bytes(b), "big")
    return ai ^ bi


def bucket_index(self_id: "bytes | str", other_id: "bytes | str") -> int:
    """The k-bucket index for ``other_id`` relative to ``self_id``.

    The index is the position of the highest set bit of the XOR distance — i.e.
    ``ID_BITS - 1 - (number of leading zero bits of the distance)`` — so peers
    sharing a longer prefix with us land in lower-index buckets (they are closer).
    Returns ``-1`` for ``self_id`` itself (distance 0 has no set bit); a node never
    stores itself. Range otherwise is ``[0, ID_BITS - 1]``.
    """
    d = xor_distance(self_id, other_id)
    if d == 0:
        return -1
    return d.bit_length() - 1


@dataclass(frozen=True)
class Contact:
    """A routing-table entry: a node id bound to a reachable :class:`PeerAddress`.

    ``node_id`` is the 32-byte id (``sha256`` of the peer's pubkey hex);
    ``address`` is the discovery-shaped endpoint the node can dial. Frozen + the
    id is hashable bytes so contacts live in dicts/sets keyed by id.
    """

    node_id: bytes
    address: PeerAddress

    @property
    def id_hex(self) -> str:
        return self.node_id.hex()


class KBucket:
    """A bounded, least-recently-seen k-bucket with test-before-evict.

    Entries are held newest-last (an explicit list acting as an LRU): touching a
    known peer moves it to the tail, so the head is always the *least recently
    seen* peer — exactly the candidate Kademlia pings before admitting a newcomer.
    The bucket never silently drops a live peer: when full, :meth:`offer` reports
    the stale head for the caller to probe and refuses the newcomer; the caller
    re-offers after a confirmed failure (:meth:`evict_then_add`).
    """

    def __init__(self, k: int = DEFAULT_K) -> None:
        if not isinstance(k, int) or isinstance(k, bool) or k < 1:
            raise ValueError("k must be a positive int")
        self.k = k
        self._entries: list[Contact] = []

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries)

    def __contains__(self, nid: bytes) -> bool:
        return any(c.node_id == nid for c in self._entries)

    def contacts(self) -> list[Contact]:
        """Entries oldest-first (head = least recently seen)."""
        return list(self._entries)

    def _index_of(self, nid: bytes) -> int:
        for i, c in enumerate(self._entries):
            if c.node_id == nid:
                return i
        return -1

    def offer(self, contact: Contact) -> "Contact | None":
        """Admit/refresh ``contact``. Returns ``None`` on success, else the stale
        head to probe.

        Three cases (Kademlia's bucket update rule):
          * **Known peer** — move it to the tail (most-recently-seen) and succeed.
          * **Room free** — append it at the tail and succeed.
          * **Bucket full** — do *not* evict; return the least-recently-seen head
            so the caller can ping it. On a confirmed ping failure the caller calls
            :meth:`evict_then_add`; on success it calls :meth:`touch` on the head
            (which keeps it and drops the newcomer).
        """
        i = self._index_of(contact.node_id)
        if i != -1:
            # Known: refresh address (it may have moved) and bump to tail.
            self._entries.pop(i)
            self._entries.append(contact)
            return None
        if len(self._entries) < self.k:
            self._entries.append(contact)
            return None
        return self._entries[0]

    def touch(self, nid: bytes) -> bool:
        """Mark ``nid`` most-recently-seen (move to tail). Returns whether present.

        Called when a probed stale head *responds*: it keeps its slot and the
        newcomer that triggered the probe is discarded (live peers are sticky).
        """
        i = self._index_of(nid)
        if i == -1:
            return False
        c = self._entries.pop(i)
        self._entries.append(c)
        return True

    def evict_then_add(self, contact: Contact) -> bool:
        """Drop the stale head and admit ``contact`` (called after a failed probe).

        Returns ``True`` if the newcomer was admitted. No-op + ``True`` if the
        contact is somehow already present; ``False`` only if the bucket emptied
        out from under us and there is nothing to evict yet there is no room — which
        cannot happen for a positive ``k``, but the guard keeps it total.
        """
        if contact.node_id in self:
            self.offer(contact)
            return True
        if self._entries:
            self._entries.pop(0)
        self._entries.append(contact)
        return True

    def remove(self, nid: bytes) -> bool:
        """Remove ``nid`` if present (e.g. a peer proven dead). Returns hit/miss."""
        i = self._index_of(nid)
        if i == -1:
            return False
        self._entries.pop(i)
        return True


class RoutingTable:
    """A node's Kademlia routing table: 256 k-buckets indexed by XOR prefix.

    Pure logic + bounded state. The table is bounded by ``ID_BITS * k`` contacts
    and never stores the node's own id. ``self_id`` is fixed at construction (from
    the node's pubkey hex); all distances are measured from it.
    """

    def __init__(self, self_id: "bytes | str", *, k: int = DEFAULT_K) -> None:
        self.self_id = _as_id_bytes(self_id)
        self.k = k
        self._buckets: list[KBucket] = [KBucket(k) for _ in range(ID_BITS)]

    @classmethod
    def from_pubkey(cls, pubkey_hex: str, *, k: int = DEFAULT_K) -> "RoutingTable":
        """Build a table for the node owning ``pubkey_hex`` (id = sha256(pubkey))."""
        return cls(node_id(pubkey_hex), k=k)

    def bucket_for(self, nid: "bytes | str") -> "KBucket | None":
        i = bucket_index(self.self_id, nid)
        if i < 0:
            return None
        return self._buckets[i]

    def __len__(self) -> int:
        return sum(len(b) for b in self._buckets)

    def __contains__(self, nid: bytes) -> bool:
        b = self.bucket_for(nid)
        return b is not None and nid in b

    def contacts(self) -> list[Contact]:
        """Every known contact (bucket-order, then within-bucket LRU order)."""
        out: list[Contact] = []
        for b in self._buckets:
            out.extend(b.contacts())
        return out

    def offer(self, contact: Contact) -> "Contact | None":
        """Route ``contact`` to its bucket and offer it (see :meth:`KBucket.offer`).

        Returns ``None`` on admit/refresh, or the stale head the caller must probe.
        Silently ignores the node's own id (a node never routes to itself).
        """
        b = self.bucket_for(contact.node_id)
        if b is None:
            return None
        return b.offer(contact)

    def add(self, contact: Contact) -> bool:
        """Convenience admit ignoring the test-before-evict probe (tests/bootstrap).

        Admits when there is room or the peer is known; if the bucket is full it
        does *not* evict (returns ``False``), preserving the live-peer-sticky
        policy. Production wiring uses :meth:`offer` + a real ping instead.
        """
        return self.offer(contact) is None

    def remove(self, nid: bytes) -> bool:
        b = self.bucket_for(nid)
        return b is not None and b.remove(nid)

    def closest(
        self,
        target: "bytes | str",
        count: "int | None" = None,
        *,
        tie_break=None,
    ) -> list[Contact]:
        """The ``count`` known contacts with the smallest XOR distance to ``target``.

        This is the local half of FIND_NODE. ``count`` defaults to ``k``. Sorted by
        ``(xor_distance, tie_break(contact))`` — distance is the primary key;
        ``tie_break`` (default: the contact id hex, a total deterministic order)
        only ever decides exact-distance ties, which for distinct ids cannot occur
        but is defended so the result is *always* deterministic regardless of
        insertion order. No RNG, no clock.
        """
        tgt = _as_id_bytes(target)
        if count is None:
            count = self.k
        if tie_break is None:
            # Stable tie-breaker keeps nearest-contact ordering deterministic.
            tie_break = lambda c: c.id_hex
        ranked = sorted(
            self.contacts(),
            key=lambda c: (xor_distance(c.node_id, tgt), tie_break(c)),
        )
        return ranked[:count]


# -- wire frames ----------------------------------------------------------------
#
# Reuse the discovery peer-record shape so a DHT-learned peer drops straight into
# discovery.PeerDirectory. A contact record is just a discovery peer record plus
# the contact's node-id hex.


def _contact_to_record(contact: Contact) -> dict:
    rec: dict = {
        "id": contact.id_hex,
        "host": contact.address.host,
        "port": contact.address.port,
    }
    if contact.address.transport != "tcp":
        rec["transport"] = contact.address.transport
    if contact.address.params:
        rec["params"] = dict(contact.address.params)
    return rec


def contacts_to_records(contacts) -> list[dict]:
    """Encode contacts as canonical-CBOR-friendly records (id hex + peer fields).

    Integer/string only, so the frame canonical-encodes with no float and no
    custom codec. The peer fields mirror :meth:`PeerDirectory.to_records` exactly.
    """
    return [_contact_to_record(c) for c in contacts]


def contacts_from_records(records) -> list[Contact]:
    """Reconstruct contacts from wire records; raises ValueError on malformed input.

    Each record needs an ``id`` hex (decoded to a 32-byte node id) plus the
    discovery host/port (+ optional transport/params). Reuses the same strict field
    typing as :func:`knitweb.p2p.discovery.peers_from_records`.
    """
    if not isinstance(records, list):
        raise ValueError("contacts must be a list")
    out: list[Contact] = []
    for r in records:
        if not isinstance(r, dict) or "id" not in r or "host" not in r or "port" not in r:
            raise ValueError("each contact record needs id + host + port")
        if not isinstance(r["id"], str):
            raise ValueError("contact id must be hex str")
        nid = _as_id_bytes(r["id"])
        if not isinstance(r["host"], str) or not isinstance(r["port"], int) or isinstance(r["port"], bool):
            raise ValueError("contact host must be str, port must be int")
        transport = r.get("transport", "tcp")
        if not isinstance(transport, str):
            raise ValueError("contact transport must be str")
        params = r.get("params", {})
        if not isinstance(params, dict) or not all(
            isinstance(kk, str) and isinstance(vv, str) for kk, vv in params.items()
        ):
            raise ValueError("contact params must be a str->str map")
        out.append(
            Contact(
                node_id=nid,
                address=PeerAddress(
                    host=r["host"], port=r["port"], transport=transport, params=dict(params)
                ),
            )
        )
    return out


def find_node_message(target: "bytes | str", *, sender_id: "bytes | str | None" = None) -> dict:
    """Build a ``find-node`` request for ``target`` (node-id hex on the wire)."""
    msg: dict = {"kind": FIND_NODE_KIND, "target": _as_id_bytes(target).hex()}
    if sender_id is not None:
        msg["sender"] = _as_id_bytes(sender_id).hex()
    return msg


def nodes_message(contacts) -> dict:
    """Build a ``nodes`` response carrying the k closest contacts as records."""
    return {"kind": NODES_KIND, "contacts": contacts_to_records(contacts)}


def handle_find_node(table: RoutingTable, msg: dict, *, count: "int | None" = None) -> dict:
    """Answer a ``find-node`` against ``table`` with the k closest known contacts.

    Pure: no sockets. Raises ValueError on a non-find-node / malformed message.
    The sender (if present + well-formed) is *not* auto-added here — admission goes
    through :meth:`RoutingTable.offer`'s test-before-evict at the node layer, which
    owns the ping; this keeps the responder side free of side effects.
    """
    if not isinstance(msg, dict) or msg.get("kind") != FIND_NODE_KIND:
        raise ValueError("not a find-node message")
    target = msg.get("target")
    if not isinstance(target, str):
        raise ValueError("find-node target must be hex str")
    return nodes_message(table.closest(target, count))


# -- iterative node lookup ------------------------------------------------------
#
# A deterministic, alpha-bounded state machine over a candidate shortlist. The
# responder is *injected*: a callable mapping a queried Contact -> the list of
# Contacts it knows closest to the target (in production, the decoded ``nodes``
# reply to a ``find_node`` frame). No asyncio, no sockets — pure logic that
# terminates when a full round surfaces no peer closer than the best seen so far.


@dataclass
class LookupState:
    """The evolving state of an iterative Kademlia node lookup (deterministic).

    ``target`` is the id being located. ``known`` maps id-hex -> Contact for every
    candidate ever discovered. ``queried`` is the set of id-hex already asked.
    ``rounds`` counts completed query rounds (bounded by the shortlist size).
    """

    target: bytes
    k: int = DEFAULT_K
    alpha: int = DEFAULT_ALPHA
    tie_break: object = None
    known: dict = field(default_factory=dict)
    queried: set = field(default_factory=set)
    rounds: int = 0

    def _tb(self):
        if self.tie_break is None:
            return lambda c: c.id_hex
        return self.tie_break

    def add(self, contacts) -> int:
        """Merge discovered ``contacts`` into the shortlist; return how many new.

        A lookup never excludes any id (the target itself may legitimately be a
        peer we are trying to reach), so every distinct contact is kept.
        """
        learned = 0
        for c in contacts:
            h = c.id_hex
            if h not in self.known:
                self.known[h] = c
                learned += 1
        return learned

    def shortlist(self) -> list[Contact]:
        """All known candidates sorted closest-first to the target (deterministic)."""
        tb = self._tb()
        return sorted(
            self.known.values(),
            key=lambda c: (xor_distance(c.node_id, self.target), tb(c)),
        )

    def closest_seen(self) -> "int | None":
        """The smallest XOR distance among known candidates, or ``None`` if empty."""
        sl = self.shortlist()
        if not sl:
            return None
        return xor_distance(sl[0].node_id, self.target)

    def next_batch(self) -> list[Contact]:
        """The up-to-``alpha`` closest *unqueried* candidates to query this round."""
        batch: list[Contact] = []
        for c in self.shortlist():
            if c.id_hex in self.queried:
                continue
            batch.append(c)
            if len(batch) >= self.alpha:
                break
        return batch

    def result(self) -> list[Contact]:
        """The ``k`` closest discovered contacts — the lookup's answer."""
        return self.shortlist()[: self.k]


def iterative_lookup(
    target: "bytes | str",
    seeds,
    responder,
    *,
    k: int = DEFAULT_K,
    alpha: int = DEFAULT_ALPHA,
    tie_break=None,
    max_rounds: "int | None" = None,
) -> LookupState:
    """Run a deterministic, alpha-bounded iterative Kademlia node lookup.

    Args:
      target: the id to locate (bytes or hex).
      seeds: the initial shortlist of :class:`Contact` (e.g.
        ``RoutingTable.closest(target)``).
      responder: an **injected** callable ``responder(contact, target) ->
        Iterable[Contact]`` returning the contacts ``contact`` knows closest to
        ``target``. In production this is the decoded ``nodes`` reply to a
        ``find_node`` frame; in tests it is a pure function over a simulated
        network — so the lookup never touches a socket and cannot stall.
      k / alpha: replication width and per-round concurrency.
      tie_break: injected deterministic tie-break comparator (default: id hex).
      max_rounds: hard cap on rounds. When ``None`` (the default) a concrete,
        adversary-independent safe bound of ``(alpha + 1) * ID_BITS`` is applied
        so a misbehaving responder that keeps minting fresh strictly-closer
        contacts cannot force unbounded rounds. An honest lookup converges in
        ~log2(N) rounds, far below this cap.

    Returns the terminal :class:`LookupState`; ``state.result()`` is the ``k``
    closest contacts found.

    Termination (Kademlia's convergence rule): each round queries the ``alpha``
    closest *unqueried* candidates and merges their results. The loop stops when a
    full round discovers **no** candidate strictly closer than the best already
    seen *and* there are no unqueried candidates closer than that best — i.e. the
    frontier has stopped improving. Every round marks at least one new candidate
    queried (or the batch is empty and we stop), so the loop runs at most as many
    rounds as there are distinct candidates: bounded compute, no clock, no RNG.
    """
    state = LookupState(
        target=_as_id_bytes(target), k=k, alpha=alpha, tie_break=tie_break
    )
    state.add(list(seeds))

    # Absolute safety bound: a lookup can never run more rounds than there are
    # distinct ids it could possibly query. Recomputed lazily below, but also
    # honoured as an explicit cap so a misbehaving responder cannot loop forever.
    #
    # The `state.rounds >= len(state.known)` check below is NOT sufficient on its
    # own: an adversarial responder that returns exactly one fresh, strictly
    # closer contact per query keeps len(known) == rounds + |seeds| + 1 forever,
    # so that bound never trips and `improved` stays True every round. A single
    # malicious peer would then drive unbounded FIND_NODE rounds (and unbounded
    # `known` growth) on the looking-up node. When the caller does not pass an
    # explicit `max_rounds`, materialise the documented safe default: an
    # O(log N)-scale, integer-only, adversary-independent cap. ID_BITS rounds
    # already suffices for any honest lookup (which converges in ~log2(N) << 256
    # rounds); the alpha factor leaves slack for the per-round concurrency.
    if max_rounds is None:
        hard_cap = (alpha + 1) * ID_BITS
    else:
        hard_cap = max_rounds

    while True:
        best_before = state.closest_seen()
        batch = state.next_batch()
        if not batch:
            break  # nothing left unqueried — converged.

        for contact in batch:
            state.queried.add(contact.id_hex)
            replies = list(responder(contact, state.target))
            state.add(replies)

        state.rounds += 1

        best_after = state.closest_seen()
        # Convergence: the closest candidate did not improve this round AND no
        # unqueried candidate is at least as close as the best — the frontier is
        # exhausted of anything that could move us closer.
        improved = (
            best_before is None
            or (best_after is not None and best_after < best_before)
        )
        if not improved:
            # Is there still an unqueried candidate that beats the best seen?
            # If the next batch is empty, the outer loop will terminate anyway;
            # if it is non-empty but no closer, Kademlia still probes it once more
            # to fill the result set — but only while rounds keep improving. Since
            # this round did not improve, stop.
            break

        if hard_cap is not None and state.rounds >= hard_cap:
            break
        # Implicit bound: distinct candidates is finite and queried grows by >=1
        # each round, so even without an explicit cap this terminates.
        if state.rounds >= len(state.known):
            break

    return state
