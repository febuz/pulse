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
    "NONCE_LEN",
    "Challenge",
    "Proof",
    "issue_challenge",
    "make_proof",
    "verify_proof",
]

# Domain-separation tag prefixed onto every byte string signed by this protocol.
# It is ASCII and so can never alias the leading byte of a canonical CBOR record
# (a map), which keeps an identity signature and a Knit/record signature in
# provably disjoint message spaces. Bump the ``:vN`` suffix to rotate the scheme.
DOMAIN_TAG = b"knitweb-p2p-identity:v1"

# Server challenge nonce length in bytes. 32 bytes (256 bits) makes a collision or
# a precomputed-reply attack against a single challenge cryptographically hopeless.
NONCE_LEN = 32


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
