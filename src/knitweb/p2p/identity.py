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

import os
from dataclasses import dataclass

from ..core import crypto

__all__ = [
    "DOMAIN_TAG",
    "PIGGYBACK_TAG",
    "NONCE_LEN",
    "NODE_PEER_PREFIX",
    "DEFAULT_PROOF_WINDOW_S",
    "Challenge",
    "Proof",
    "PiggybackProof",
    "issue_challenge",
    "make_proof",
    "verify_proof",
    "node_peer_id",
    "make_id_proof",
    "verify_id_proof",
    "id_proof_to_record",
    "id_proof_from_record",
]

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


def _piggyback_message(nonce: bytes, timestamp: int) -> bytes:
    """The exact bytes a piggybacked proof signs.

    ``PIGGYBACK_TAG || timestamp(8 big-endian bytes) || nonce``. The timestamp is
    *inside* the signed bytes, so a proof cannot be reused with a different claimed
    time — a verifier's freshness check is over the same integer the signer bound.
    """
    return PIGGYBACK_TAG + int(timestamp).to_bytes(8, "big") + nonce


@dataclass(frozen=True)
class PiggybackProof:
    """A self-minted, no-round-trip identity proof riding on a request.

    ``pubkey`` is the 33-byte compressed secp256k1 point (hex); ``nonce`` is a
    fresh client-chosen value; ``timestamp`` is a coarse integer (seconds);
    ``sig`` is the DER-encoded ECDSA signature (hex) over
    ``PIGGYBACK_TAG || timestamp || nonce``. Immutable, integers/bytes/str only.
    """

    pubkey: str
    nonce: bytes
    timestamp: int
    sig: str

    def message(self) -> bytes:
        """The exact bytes this proof signs (domain tag, timestamp, nonce)."""
        return _piggyback_message(self.nonce, self.timestamp)


def make_id_proof(
    signing_key: str, *, nonce: bytes | None = None, timestamp: int
) -> PiggybackProof:
    """Mint a piggybacked proof of control of ``signing_key`` at ``timestamp``.

    ``nonce`` defaults to ``os.urandom(NONCE_LEN)`` in production but is injectable
    for deterministic tests (and must be exactly :data:`NONCE_LEN` bytes when
    supplied). ``timestamp`` is a coarse integer-seconds clock reading the caller
    chooses (injected in tests for determinism). The returned proof carries the
    compressed public key derived from ``signing_key`` and the signature over
    ``PIGGYBACK_TAG || timestamp || nonce``.
    """
    if nonce is None:
        nonce = os.urandom(NONCE_LEN)
    elif not isinstance(nonce, (bytes, bytearray)):
        raise TypeError("nonce must be bytes")
    elif len(nonce) != NONCE_LEN:
        raise ValueError(f"nonce must be exactly {NONCE_LEN} bytes")
    if not isinstance(timestamp, int) or isinstance(timestamp, bool):
        raise TypeError("timestamp must be int")
    nonce = bytes(nonce)
    pubkey = crypto.public_from_private(signing_key)
    sig = crypto.sign(signing_key, _piggyback_message(nonce, timestamp))
    return PiggybackProof(pubkey=pubkey, nonce=nonce, timestamp=timestamp, sig=sig)


def verify_id_proof(
    proof: PiggybackProof,
    *,
    now: int,
    window: int = DEFAULT_PROOF_WINDOW_S,
) -> str | None:
    """Check a piggybacked ``proof`` against the verifier's clock.

    Returns ``node_peer_id(proof.pubkey)`` iff (a) ``proof.sig`` is a valid
    signature by ``proof.pubkey`` over ``PIGGYBACK_TAG || timestamp || nonce`` and
    (b) the proof's ``timestamp`` is within ``window`` seconds of ``now``
    (``|now - timestamp| <= window``); otherwise ``None``. A tampered/forged
    signature or a stale/future timestamp both fall to ``None`` — the caller then
    falls back to its carrier (IP/mailbox) key. Malformed pubkey/sig hex is a
    failed verification (``crypto.verify`` returns False), never an exception.

    Replay caveat: this proof is independently signed and bounded-fresh, but it is
    *not* bound to a specific live exchange (that is the cost of having no
    round-trip). A MITM that captures an honest peer's proof and replays it within
    the window — over its own connection — gets that honest ``node:<pubkey>``
    blamed for whatever the MITM then does on that connection. The blast radius is
    bounded by ``window`` and by the fact that every consequential record is itself
    independently signed (a replayed proof cannot forge a feed/Knit/record
    signature). Closing it fully needs the round-trip challenge/response variant
    (:func:`verify_proof`); that is a deliberate future option, not this step.
    """
    if not isinstance(now, int) or isinstance(now, bool):
        raise TypeError("now must be int")
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
    """
    return {
        "pubkey": proof.pubkey,
        "nonce": proof.nonce,
        "ts": proof.timestamp,
        "sig": proof.sig,
    }


def id_proof_from_record(record) -> PiggybackProof | None:
    """Decode a transport-envelope map back into a :class:`PiggybackProof`.

    Returns ``None`` for anything that is not a well-shaped proof map (so a
    malformed or absent ``_relay_id_proof`` simply falls back to the carrier key
    rather than raising). Strictly type-checks each field — integers/bytes/str
    only — so a junk envelope can never reach :func:`crypto.verify` with the wrong
    type and surface as an exception.
    """
    if not isinstance(record, dict):
        return None
    pubkey = record.get("pubkey")
    nonce = record.get("nonce")
    timestamp = record.get("ts")
    sig = record.get("sig")
    if not isinstance(pubkey, str) or not isinstance(sig, str):
        return None
    if not isinstance(nonce, (bytes, bytearray)):
        return None
    if not isinstance(timestamp, int) or isinstance(timestamp, bool):
        return None
    return PiggybackProof(
        pubkey=pubkey, nonce=bytes(nonce), timestamp=timestamp, sig=sig
    )
