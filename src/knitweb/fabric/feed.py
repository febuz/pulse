"""Signed append-only feed — the local core of Knitweb's Phase 3 replication.

Every spider owns one feed: an append-only log of canonical-CBOR entries. The
design mirrors Hypercore (Holepunch) but uses Knitweb's own primitives —
secp256k1/SHA-256/CBOR instead of ed25519/blake2b — and deliberately contains
**no networking**: this module is a pure, locally-testable data structure. The
p2p layer (py-libp2p DHT + wire protocol) will later wrap it; keeping the signed
log provable on its own is the point.

Two ideas carry the whole design (both validated against Hypercore's source in
`docs/CRYPTO_CORPUS_STUDY.md`):

1. **Sign the tree head, not each entry.** The author signs a domain-separated
   commitment ``{ns, feed, root, length, fork}`` where ``root`` is the SHA-256
   Merkle root over the entry leaves. One signature then authenticates the whole
   feed (or any verified slice), so a reader can verify partial replication
   against a single signature instead of one-per-entry.

2. **A fork counter makes equivocation provable.** Equivocation — the author
   signing two *different* histories at the same position — is the core P2P
   attack. With a signed head it reduces to a trivial check: two validly-signed
   heads from the same feed at the same ``(length, fork)`` but different ``root``
   are an unforgeable proof of equivocation. The ``fork`` counter lets an honest
   author legitimately truncate-and-rewrite (it bumps ``fork``) without being
   mistaken for an attacker.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import canonical, crypto

__all__ = [
    "NAMESPACE",
    "FeedHead",
    "Feed",
    "verify_head",
    "verify_entries",
    "check_conflict",
    "check_prefix_conflict",
]

# Domain-separation tag: a signature over a feed head can never be replayed as a
# signature over anything else (a Knit, a bytecode bundle, ...).
NAMESPACE = "knit-feed:v1"


def _leaf(entry: dict) -> bytes:
    """The Merkle leaf for an entry: SHA-256 of its canonical bytes."""
    return crypto.sha256(canonical.encode(entry))


def _root(entries: list[dict]) -> str:
    return crypto.merkle_root([_leaf(e) for e in entries]).hex()


def _head_signable(feed: str, root: str, length: int, fork: int) -> bytes:
    """Canonical bytes the author signs to commit to a feed state."""
    return canonical.encode(
        {"ns": NAMESPACE, "feed": feed, "root": root, "length": length, "fork": fork}
    )


# ---------------------------------------------------------------------------
# FeedHead — a signed commitment to a feed's state (the only thing on the wire
# that carries authority; entries are verified against it).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FeedHead:
    """A signed commitment to the state of an append-only feed."""

    feed: str    # author's compressed secp256k1 public key (hex) — the feed identity
    root: str    # SHA-256 Merkle root over entry leaves (hex)
    length: int  # number of entries committed
    fork: int    # fork counter (bumped on a legitimate truncate+rewrite)
    sig: str     # DER signature (hex) over _head_signable(...)

    def signable(self) -> bytes:
        return _head_signable(self.feed, self.root, self.length, self.fork)

    def verify(self) -> bool:
        """True iff ``sig`` is a valid signature by ``feed`` over this head."""
        return crypto.verify(self.feed, self.signable(), self.sig)

    @property
    def address(self) -> str:
        """The PLS address of the feed author."""
        return crypto.address(self.feed)


# ---------------------------------------------------------------------------
# Feed — author-side: holds the private key + entries, mints signed heads.
# ---------------------------------------------------------------------------

class Feed:
    """An append-only log owned by one keypair."""

    def __init__(self, priv: str, fork: int = 0) -> None:
        self._priv = priv
        self.feed = crypto.public_from_private(priv)
        self.fork = fork
        self._entries: list[dict] = []

    @classmethod
    def create(cls) -> "Feed":
        """Create a feed under a fresh keypair."""
        priv, _ = crypto.generate_keypair()
        return cls(priv)

    @property
    def length(self) -> int:
        return len(self._entries)

    @property
    def address(self) -> str:
        return crypto.address(self.feed)

    @property
    def entries(self) -> list[dict]:
        """A copy of the committed entries (read-only view)."""
        return list(self._entries)

    def entry(self, index: int) -> dict:
        return self._entries[index]

    def root(self) -> str:
        return _root(self._entries)

    def head(self) -> FeedHead:
        """Mint a signed head over the current state."""
        root = self.root()
        sig = crypto.sign(self._priv, _head_signable(self.feed, root, self.length, self.fork))
        return FeedHead(feed=self.feed, root=root, length=self.length, fork=self.fork, sig=sig)

    def append(self, entry: dict) -> FeedHead:
        """Append ``entry`` and return the new signed head."""
        # Encode eagerly so a non-canonical-encodable entry is rejected at append
        # time, not later at root() — keeps the log always-serializable.
        canonical.encode(entry)
        self._entries.append(entry)
        return self.head()

    def truncate(self, length: int) -> FeedHead:
        """Legitimately drop back to ``length`` entries and bump the fork counter.

        Bumping ``fork`` is what distinguishes an honest rewrite from equivocation:
        a reader holding an old head at the previous fork will not mistake the new
        history for a conflicting one (see :func:`check_conflict`).
        """
        if not 0 <= length <= self.length:
            raise ValueError(f"cannot truncate to {length} (length is {self.length})")
        del self._entries[length:]
        self.fork += 1
        return self.head()


# ---------------------------------------------------------------------------
# Reader-side verification
# ---------------------------------------------------------------------------

def verify_head(head: FeedHead) -> bool:
    """True iff the head's signature is valid for its claimed feed key."""
    return head.verify()


def verify_entries(head: FeedHead, entries: list[dict]) -> bool:
    """Verify a head *and* that ``entries`` reproduce its committed root+length.

    This is the full read path: a peer that received ``length`` entries plus a
    signed ``head`` confirms (a) the author signed the head, and (b) the entries
    hash to exactly the committed Merkle root. Either failure ⇒ reject.
    """
    if not head.verify():
        return False
    if len(entries) != head.length:
        return False
    return _root(entries) == head.root


# ---------------------------------------------------------------------------
# Equivocation / fork-conflict proofs
# ---------------------------------------------------------------------------

def check_conflict(a: FeedHead, b: FeedHead) -> bool:
    """True iff ``(a, b)`` prove the feed author equivocated.

    Equivocation = two validly-signed heads from the **same feed** at the **same
    (length, fork)** that commit to **different roots**. That is two signatures
    over two different histories at the same position — something an honest author
    never produces. The fork counter is what keeps a legitimate truncate+rewrite
    (which bumps ``fork``) from tripping this check.
    """
    if a.feed != b.feed:
        return False
    if not (a.verify() and b.verify()):
        return False
    return a.length == b.length and a.fork == b.fork and a.root != b.root


def check_prefix_conflict(
    short: FeedHead, longer: FeedHead, longer_entries: list[dict]
) -> bool:
    """Detect a rewritten *prefix* at the same fork (history tampering without a
    fork bump).

    Given a short head and a longer head from the same feed at the same fork, plus
    the longer feed's entries, recompute the Merkle root of the first
    ``short.length`` entries. If it differs from ``short.root`` — yet both heads are
    validly signed and the longer entries match the longer head — the author
    rewrote committed history while pretending it is the same fork. That is
    equivocation by another name.
    """
    if short.feed != longer.feed or short.fork != longer.fork:
        return False
    if short.length > longer.length:
        return False
    if not (short.verify() and longer.verify()):
        return False
    if not verify_entries(longer, longer_entries):
        return False
    prefix_root = _root(longer_entries[: short.length])
    return prefix_root != short.root
