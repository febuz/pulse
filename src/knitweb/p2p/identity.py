"""Node-identity proof primitive — prove control of a node secp256k1 key.

A Knitweb peer needs to prove, at connection time, that it controls the private
key behind the node public key it claims — *without* revealing that key and
without a signature that could be lifted and replayed elsewhere. This module is
the cryptographic core of that handshake: a challenge–response over the node's
secp256k1 key (reusing :mod:`knitweb.core.crypto`), expressed as **pure functions
over small frozen dataclasses with ZERO networking**. Wiring it into a live
transport handshake is a separate concern; keeping the proof itself socket-free
means it is deterministic, trivially testable, and cannot deadlock.

Domain separation
-----------------
Every signature here is taken over ``DOMAIN_TAG || nonce``, never over a bare
nonce. :data:`DOMAIN_TAG` (``b"knitweb-p2p-identity:v1"``) is an ASCII prefix that
no canonical CBOR record begins with — a canonical record is a CBOR map and
starts with a major-type-5 header byte (``0xa0``–``0xbb``), not ``0x6b`` (``'k'``).
So an identity proof's signed bytes can never coincide with a Knit's (or any
signed-record's) signing bytes, and vice-versa: a proof can never be mistaken for
a value-transfer signature, and a stolen Knit signature can never satisfy
:func:`verify_proof`. The trailing ``:v1`` reserves room to rotate the scheme.

This module is transport-level only. It produces and checks signatures over a
freshly-minted, ephemeral nonce; it touches NO canonical/signed-record bytes, so
no Knit's CID changes because of anything here. Values are bytes/str/int only.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from ..core import crypto

__all__ = [
    "DOMAIN_TAG",
    "PIGGYBACK_TAG",
    "NETWORK_ID_TAG",
    "NONCE_LEN",
    "NODE_PEER_PREFIX",
    "DEFAULT_PROOF_WINDOW_S",
    "SECP256K1_ORDER",
    "Challenge",
    "Proof",
    "PiggybackProof",
    "issue_challenge",
    "make_proof",
    "verify_proof",
    "node_peer_id",
    "network_signing_key",
    "make_id_proof",
    "verify_id_proof",
    "id_proof_to_record",
    "id_proof_from_record",
    "SeenProofCache",
]

# The order N of the secp256k1 group. A private scalar is a non-zero integer in
# ``[1, N-1]``; any derived network scalar is reduced into exactly that range so
# :func:`crypto.public_from_private` never rejects it. This is the standardised
# secp256k1 constant (SEC2), not a secret.
SECP256K1_ORDER = (
    0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
)

# Domain-separation tag prefixed onto every byte string signed by this protocol.
# It is ASCII and so can never alias the leading byte of a canonical CBOR record
# (a map), which keeps an identity signature and a Knit/record signature in
# provably disjoint message spaces. Bump the ``:vN`` suffix to rotate the scheme.
DOMAIN_TAG = b"knitweb-p2p-identity:v1"

# Domain-separation tag for the **piggybacked** (no-round-trip) proof variant
# (step 2 of #58). It is a *distinct* tag from :data:`DOMAIN_TAG` because the two
# proofs sign different message shapes: a :class:`Challenge` proof signs a single
# server-issued nonce, whereas a :class:`PiggybackProof` signs a client-chosen
# nonce *and* a coarse timestamp (so it is bounded-freshness rather than tied to a
# specific live exchange). Keeping the tags disjoint means a piggyback proof can
# never be lifted and replayed as a challenge proof or vice-versa — and, like
# :data:`DOMAIN_TAG`, it is ASCII so it can never alias a canonical CBOR record's
# leading byte (a Knit/record signature stays in a provably disjoint space).
PIGGYBACK_TAG = b"knitweb-p2p-identity-piggyback:v1"

# Domain-separation tag for deriving a node's **network** identity scalar from its
# financial/Knit-signing private key (#89). It is fed, with the financial private
# key bytes, through a one-way hash to produce the network private scalar — so the
# network public key that ships in every dispatched envelope is cryptographically
# UNLINKABLE to the financial public key: recovering the financial key (or even
# proving the two pubkeys share an owner) requires the financial *private* key,
# which never leaves the node. The ``:v1`` suffix reserves room to rotate the KDF.
# It is ASCII for the same disjoint-message-space reason as the tags above.
NETWORK_ID_TAG = b"knitweb-network-id:v1|"

# Server challenge nonce length in bytes. 32 bytes (256 bits) makes a collision or
# a precomputed-reply attack against a single challenge cryptographically hopeless.
NONCE_LEN = 32

# Reputation-key prefix for a peer proven by its node public key. It mirrors the
# carrier prefixes (``tcp:`` / ``relay:``) so a proven-identity key can never
# collide with an IP- or mailbox-derived key in the reputation ledger.
NODE_PEER_PREFIX = "node:"

# Default freshness window, in **integer** seconds, a verifier accepts a
# piggybacked proof's coarse timestamp within (|now - timestamp| <= window). Wide
# enough to absorb clock skew + relay queueing, narrow enough that a captured
# proof stops being replayable shortly after it is seen. A pure integer policy
# knob; it touches no canonical/hashed/signed-record bytes.
DEFAULT_PROOF_WINDOW_S = 60


@dataclass(frozen=True)
class Challenge:
    """A fresh, single-use server challenge: the random ``nonce`` to be signed.

    Immutable so a verifier can hold the exact challenge it issued and check a
    proof against *that* nonce — replaying a proof minted for any other challenge
    fails (see :func:`verify_proof`).
    """

    nonce: bytes

    def message(self) -> bytes:
        """The exact bytes a proof signs: the domain tag followed by the nonce."""
        return DOMAIN_TAG + self.nonce


@dataclass(frozen=True)
class Proof:
    """A client's response to a :class:`Challenge`.

    ``pubkey`` is the 33-byte compressed secp256k1 point (hex); ``sig`` is the
    DER-encoded ECDSA signature (hex) over ``DOMAIN_TAG || nonce``.
    """

    pubkey: str
    sig: str


def issue_challenge(*, nonce: bytes | None = None) -> Challenge:
    """Mint a fresh server challenge.

    ``nonce`` defaults to ``os.urandom(NONCE_LEN)`` in production but is injectable
    for deterministic tests. An explicitly supplied nonce must be exactly
    :data:`NONCE_LEN` bytes so a short/oversized nonce can never weaken a
    challenge by accident.
    """
    if nonce is None:
        nonce = os.urandom(NONCE_LEN)
    elif not isinstance(nonce, (bytes, bytearray)):
        raise TypeError("nonce must be bytes")
    elif len(nonce) != NONCE_LEN:
        raise ValueError(f"nonce must be exactly {NONCE_LEN} bytes")
    return Challenge(nonce=bytes(nonce))


def make_proof(challenge: Challenge, signing_key: str) -> Proof:
    """Sign ``challenge`` with the node's secp256k1 private key (hex).

    Returns a :class:`Proof` carrying the compressed public key derived from
    ``signing_key`` and the DER signature over ``DOMAIN_TAG || nonce``.
    """
    pubkey = crypto.public_from_private(signing_key)
    sig = crypto.sign(signing_key, challenge.message())
    return Proof(pubkey=pubkey, sig=sig)


def verify_proof(challenge: Challenge, proof: Proof) -> str | None:
    """Check ``proof`` against ``challenge``.

    Returns the proven node public-key hex iff ``proof.sig`` is a valid signature
    by ``proof.pubkey`` over *this* challenge's ``DOMAIN_TAG || nonce``; otherwise
    returns ``None``. This rejects a proof replayed against a different nonce, a
    forged/tampered signature, and a proof whose ``pubkey`` did not produce
    ``sig`` (pubkey/sig mismatch). A malformed pubkey or signature hex is treated
    as a failed verification (``crypto.verify`` returns False), not an exception.
    """
    if crypto.verify(proof.pubkey, challenge.message(), proof.sig):
        return proof.pubkey
    return None


# ---------------------------------------------------------------------------
# Piggybacked proof — no extra round-trip (step 2 of #58)
# ---------------------------------------------------------------------------
#
# The challenge/response above needs a server-issued nonce, hence a round-trip.
# To key reputation on a proven node identity *without* adding a handshake that
# could stall or deadlock, a dialing peer instead attaches a self-minted proof to
# the request it was already sending: it signs a fresh client-chosen nonce plus a
# coarse integer timestamp, and the receiver accepts it iff the signature verifies
# *and* the timestamp is within a bounded freshness window. There is no second
# await, no separate exchange — so this path cannot introduce a deadlock.


def node_peer_id(pubkey: str) -> str:
    """Stable reputation key for a peer that proved control of ``pubkey``.

    Namespaced under :data:`NODE_PEER_PREFIX` so a proven-identity key can never
    collide with a ``tcp:<ip>`` or ``relay:<mailbox>`` carrier key — keying on the
    node *key* (not the shared IP) is exactly what removes the NAT collateral-ban.
    """
    return f"{NODE_PEER_PREFIX}{pubkey}"


def network_signing_key(financial_priv: str) -> str:
    """Derive the node's stable NETWORK identity private key from its financial key.

    Fix for #89 (deanonymization). The piggybacked identity proof must NOT be
    signed with — nor ship — the node's financial/Knit-signing public key, or a
    passive observer beside the TCP source IP permanently links
    ``IP -> pubkey -> Knit-signer`` (the wallet). This derives a SEPARATE secp256k1
    private scalar used ONLY for the network identity proof:

        network_priv = H(NETWORK_ID_TAG || financial_priv_bytes) reduced into [1, N-1]

    where ``H`` is SHA-256. Properties this gives us:

      * **Unlinkable.** The network public key is the image of a one-way hash of the
        financial *private* key; no observer holding only public material can tie it
        back to the financial pubkey. The financial pubkey therefore need never (and
        must never) appear in a dispatched network envelope.
      * **Stable per node.** The derivation is deterministic, so a node presents the
        SAME network key across reconnects/IP rotations — which is exactly what keeps
        the NAT collateral-ban fix (#58) intact: reputation keys on a stable
        ``node:<network-pubkey>`` and a forger behind shared NAT cannot collateral-ban
        an honest neighbour.
      * **Byte-identity untouched.** This touches no canonical/signed-record bytes;
        Knits are still signed by the financial key, so a fresh Knit's CID and signed
        bytes are unchanged.

    ``financial_priv`` is the 32-byte private-scalar hex (as produced by
    :func:`knitweb.core.crypto.generate_keypair`). Returns the network private-scalar
    hex (32 bytes), suitable for :func:`make_id_proof` / :func:`crypto.sign`.
    """
    if not isinstance(financial_priv, str):
        raise TypeError("financial_priv must be a private-scalar hex str")
    priv_bytes = bytes.fromhex(financial_priv)
    digest = hashlib.sha256(NETWORK_ID_TAG + priv_bytes).digest()
    # Reduce into [1, N-1]: map the 256-bit hash into [0, N-2] then shift by 1, so
    # the scalar is always a valid (non-zero) secp256k1 private key.
    scalar = (int.from_bytes(digest, "big") % (SECP256K1_ORDER - 1)) + 1
    return scalar.to_bytes(32, "big").hex()


def _piggyback_message(nonce: bytes, timestamp: int, binding: bytes = b"") -> bytes:
    """The exact bytes a piggybacked proof signs.

    ``PIGGYBACK_TAG || timestamp(8 big-endian bytes) || len(binding)(4 big-endian
    bytes) || binding || nonce``. The timestamp is *inside* the signed bytes, so a
    proof cannot be reused with a different claimed time. The ``binding`` is a
    caller-chosen connection/body context (#90): folding it (with an explicit length
    prefix, so it is unambiguous and cannot be confused with the trailing nonce)
    into the signed bytes means a captured proof cannot be lifted onto a different
    connection or a different first-message body — the verifier recomputes the same
    binding from what it actually received, and a mismatch breaks the signature.
    An empty ``binding`` reproduces the pre-#90 unbound message shape.
    """
    return (
        PIGGYBACK_TAG
        + int(timestamp).to_bytes(8, "big")
        + len(binding).to_bytes(4, "big")
        + binding
        + nonce
    )


@dataclass(frozen=True)
class PiggybackProof:
    """A self-minted, no-round-trip identity proof riding on a request.

    ``pubkey`` is the 33-byte compressed secp256k1 point (hex); ``nonce`` is a
    fresh client-chosen value; ``timestamp`` is a coarse integer (seconds);
    ``binding`` is the connection/body context the proof is tied to (#90; empty
    bytes for an unbound proof); ``sig`` is the DER-encoded ECDSA signature (hex)
    over ``PIGGYBACK_TAG || timestamp || len(binding) || binding || nonce``.
    Immutable, integers/bytes/str only.
    """

    pubkey: str
    nonce: bytes
    timestamp: int
    sig: str
    binding: bytes = b""

    def message(self) -> bytes:
        """The exact bytes this proof signs (tag, timestamp, binding, nonce)."""
        return _piggyback_message(self.nonce, self.timestamp, self.binding)


def make_id_proof(
    signing_key: str,
    *,
    nonce: bytes | None = None,
    timestamp: int,
    binding: bytes = b"",
) -> PiggybackProof:
    """Mint a piggybacked proof of control of ``signing_key`` at ``timestamp``.

    ``signing_key`` should be the node's NETWORK identity scalar (see
    :func:`network_signing_key`), NOT its financial/Knit-signing key — the network
    key is what may safely ship in a dispatched envelope (#89). ``nonce`` defaults
    to ``os.urandom(NONCE_LEN)`` in production but is injectable for deterministic
    tests (and must be exactly :data:`NONCE_LEN` bytes when supplied). ``timestamp``
    is a coarse integer-seconds clock reading the caller chooses (injected in tests
    for determinism). ``binding`` (#90) is an optional connection/body context the
    proof is tied to — when non-empty, the verifier must recompute the identical
    binding or the proof fails, so a captured proof cannot be lifted elsewhere.

    The returned proof carries the compressed public key derived from
    ``signing_key`` and the signature over
    ``PIGGYBACK_TAG || timestamp || len(binding) || binding || nonce``.
    """
    if nonce is None:
        nonce = os.urandom(NONCE_LEN)
    elif not isinstance(nonce, (bytes, bytearray)):
        raise TypeError("nonce must be bytes")
    elif len(nonce) != NONCE_LEN:
        raise ValueError(f"nonce must be exactly {NONCE_LEN} bytes")
    if not isinstance(timestamp, int) or isinstance(timestamp, bool):
        raise TypeError("timestamp must be int")
    if not isinstance(binding, (bytes, bytearray)):
        raise TypeError("binding must be bytes")
    nonce = bytes(nonce)
    binding = bytes(binding)
    pubkey = crypto.public_from_private(signing_key)
    sig = crypto.sign(signing_key, _piggyback_message(nonce, timestamp, binding))
    return PiggybackProof(
        pubkey=pubkey, nonce=nonce, timestamp=timestamp, sig=sig, binding=binding
    )


def verify_id_proof(
    proof: PiggybackProof,
    *,
    now: int,
    window: int = DEFAULT_PROOF_WINDOW_S,
    binding: bytes = b"",
) -> str | None:
    """Check a piggybacked ``proof`` against the verifier's clock and binding.

    Returns ``node_peer_id(proof.pubkey)`` iff (a) the proof's ``binding`` is the
    one the verifier expects (``proof.binding == binding``, #90), (b) ``proof.sig``
    is a valid signature by ``proof.pubkey`` over
    ``PIGGYBACK_TAG || timestamp || len(binding) || binding || nonce``, and (c) the
    proof's ``timestamp`` is within ``window`` seconds of ``now``
    (``|now - timestamp| <= window``); otherwise ``None``. A wrong binding, a
    tampered/forged signature, or a stale/future timestamp all fall to ``None`` —
    the caller then falls back to its carrier (IP/mailbox) key. Malformed pubkey/sig
    hex is a failed verification (``crypto.verify`` returns False), never an
    exception.

    Replay protection (#90) has two layers and this function owns the *binding*
    layer: a captured proof carries the connection/body context of the exchange it
    was minted for, so lifting it onto a *different* connection or first-message
    body — where the verifier recomputes a different ``binding`` — is rejected here
    (the binding mismatch short-circuits before the signature, and even a forged
    binding field breaks the signature). The *replay-within-window* layer (a proof
    accepted at most once even on its own binding) is the seen-proof cache owned by
    :class:`SeenProofCache` / the connection gate, which is stateful and so lives
    outside this pure function.
    """
    if not isinstance(now, int) or isinstance(now, bool):
        raise TypeError("now must be int")
    if not isinstance(binding, (bytes, bytearray)):
        raise TypeError("binding must be bytes")
    if proof.binding != bytes(binding):
        return None
    if abs(now - proof.timestamp) > window:
        return None
    if crypto.verify(proof.pubkey, proof.message(), proof.sig):
        return node_peer_id(proof.pubkey)
    return None


def id_proof_to_record(proof: PiggybackProof) -> dict:
    """Encode a :class:`PiggybackProof` as a transport-envelope map.

    Integers/bytes/str only, so it rides inside a transport-envelope key without
    any custom codec. It is *never* part of a signed/canonical record — it lives in
    the stripped ``_relay_*`` envelope namespace (see
    :data:`knitweb.p2p.relay.ENVELOPE_ID_PROOF_KEY`) and is dropped before any
    business/hash path, so it cannot change a Knit's CID.

    The optional ``bind`` field carries the connection/body binding (#90) so the
    proof round-trips faithfully; it is only emitted when non-empty, keeping the
    encoding of a legacy unbound proof byte-identical.
    """
    record = {
        "pubkey": proof.pubkey,
        "nonce": proof.nonce,
        "ts": proof.timestamp,
        "sig": proof.sig,
    }
    if proof.binding:
        record["bind"] = proof.binding
    return record


def id_proof_from_record(record) -> PiggybackProof | None:
    """Decode a transport-envelope map back into a :class:`PiggybackProof`.

    Returns ``None`` for anything that is not a well-shaped proof map (so a
    malformed or absent ``_relay_id_proof`` simply falls back to the carrier key
    rather than raising). Strictly type-checks each field — integers/bytes/str
    only — so a junk envelope can never reach :func:`crypto.verify` with the wrong
    type and surface as an exception.

    The optional ``bind`` field (#90) is accepted as bytes when present and absent
    decodes to an unbound proof, so a legacy proof map still round-trips. A present
    but non-bytes ``bind`` is malformed and decodes to ``None``.
    """
    if not isinstance(record, dict):
        return None
    pubkey = record.get("pubkey")
    nonce = record.get("nonce")
    timestamp = record.get("ts")
    sig = record.get("sig")
    binding = record.get("bind", b"")
    if not isinstance(pubkey, str) or not isinstance(sig, str):
        return None
    if not isinstance(nonce, (bytes, bytearray)):
        return None
    if not isinstance(timestamp, int) or isinstance(timestamp, bool):
        return None
    if not isinstance(binding, (bytes, bytearray)):
        return None
    return PiggybackProof(
        pubkey=pubkey,
        nonce=bytes(nonce),
        timestamp=timestamp,
        sig=sig,
        binding=bytes(binding),
    )


# ---------------------------------------------------------------------------
# Replay-within-window cache (#90)
# ---------------------------------------------------------------------------

#: Hard cap on distinct proofs remembered at once. Bounds memory against a flood of
#: distinct one-shot proofs: at capacity the oldest first-seen entry is evicted
#: (integer-LRU). A proof whose first-seen entry has been evicted can be replayed
#: again, but only after the cache has churned through this many *newer* proofs —
#: the freshness window already discards it long before that under any sane rate.
DEFAULT_SEEN_PROOF_CAP = 4096


class SeenProofCache:
    """A bounded, deterministic 'has this exact proof been accepted before?' cache.

    The binding layer in :func:`verify_id_proof` stops a captured proof being
    *moved* to another connection/body; this closes the remaining replay leg (#90):
    a proof — even on its own correct binding — is accepted **at most once** within
    its freshness window, so a passive observer cannot capture an honest peer's
    proof and re-present it verbatim to get that honest ``node:<pubkey>`` blamed.

    Identity of a proof is ``(pubkey, nonce, timestamp, binding)`` — the same tuple
    the signature commits to — so two genuinely independent proofs (fresh nonces)
    never collide, and a verbatim replay always does. Entries expire ``window``
    seconds after their bound ``timestamp`` (they can never be replayed past then
    anyway), and the cache is hard-capped at ``capacity`` with oldest-first
    (integer-LRU) eviction. Pure and deterministic: the clock is injected per call,
    there is no wall-clock or RNG, and insertion order is the only ordering used.
    """

    def __init__(self, *, capacity: int = DEFAULT_SEEN_PROOF_CAP) -> None:
        if not isinstance(capacity, int) or isinstance(capacity, bool):
            raise TypeError("capacity must be int")
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        # insertion-ordered dict: key -> expiry second (timestamp + window).
        self._seen: "dict[tuple, int]" = {}

    @staticmethod
    def _key(proof: PiggybackProof) -> tuple:
        return (proof.pubkey, proof.nonce, proof.timestamp, proof.binding)

    def _evict_expired(self, now: int) -> None:
        # Drop every entry whose expiry is at/under ``now``; insertion order means
        # we can stop at the first still-live entry only if we tracked monotonic
        # expiries, which we do not (window is constant but timestamps vary), so we
        # sweep — bounded by ``capacity`` so it stays O(capacity).
        dead = [k for k, exp in self._seen.items() if exp <= now]
        for k in dead:
            del self._seen[k]

    def check_and_record(self, proof: PiggybackProof, *, now: int, window: int) -> bool:
        """Record ``proof`` as seen; return True iff this is its FIRST sighting.

        Returns ``False`` for a verbatim replay of a proof already recorded and not
        yet expired — the caller must then treat the proof as unproven (carrier
        fallback). ``now``/``window`` are the verifier's injected clock and freshness
        window; an entry is kept only until ``proof.timestamp + window`` (past which
        the freshness check rejects it regardless), so the cache never has to hold a
        proof longer than it could possibly be replayed.
        """
        if not isinstance(now, int) or isinstance(now, bool):
            raise TypeError("now must be int")
        if not isinstance(window, int) or isinstance(window, bool):
            raise TypeError("window must be int")
        self._evict_expired(now)
        key = self._key(proof)
        if key in self._seen:
            return False
        # Cap: evict the oldest (first-inserted) entry to make room.
        while len(self._seen) >= self._capacity:
            oldest = next(iter(self._seen))
            del self._seen[oldest]
        self._seen[key] = proof.timestamp + window
        return True

    def __len__(self) -> int:
        return len(self._seen)
