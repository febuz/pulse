"""Cryptographic primitives for FBR: secp256k1 ECDSA + SHA-256.

FBR keypairs and balance-keeping use Bitcoin-style cryptography — the secp256k1
curve with ECDSA signatures over SHA-256 — implemented on the standard
``cryptography`` library (no native build step required).

Public keys are serialized as 33-byte compressed SEC1 points (hex). Private keys
are 32-byte scalars (hex). Signatures are DER-encoded (hex). An FBR address is a
short, content-addressed fingerprint of the public key.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.exceptions import InvalidSignature

__all__ = [
    "generate_keypair",
    "public_from_private",
    "sign",
    "verify",
    "sha256",
    "sha256_hex",
    "merkle_root",
    "address",
    "is_valid_hex",
]

_CURVE = ec.SECP256K1()


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def sha256(data: bytes) -> bytes:
    """Return the 32-byte SHA-256 digest of ``data``."""
    return hashlib.sha256(data).digest()


def sha256_hex(data: bytes) -> str:
    """Return the SHA-256 digest of ``data`` as a hex string."""
    return hashlib.sha256(data).hexdigest()


def merkle_root(leaves: list[bytes]) -> bytes:
    """Compute a SHA-256 Merkle root over ``leaves`` (duplicating the last if odd).

    An empty list hashes to the SHA-256 of the empty string, so the root is
    always well-defined and deterministic.
    """
    if not leaves:
        return sha256(b"")
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [sha256(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def _load_private(priv_hex: str) -> ec.EllipticCurvePrivateKey:
    secret = int(priv_hex, 16)
    return ec.derive_private_key(secret, _CURVE)


def _load_public(pub_hex: str) -> ec.EllipticCurvePublicKey:
    return ec.EllipticCurvePublicKey.from_encoded_point(_CURVE, bytes.fromhex(pub_hex))


def _compressed_public_hex(pub: ec.EllipticCurvePublicKey) -> str:
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )

    raw = pub.public_bytes(Encoding.X962, PublicFormat.CompressedPoint)
    return raw.hex()


def generate_keypair() -> tuple[str, str]:
    """Generate a new (private_hex, public_hex) secp256k1 keypair.

    The private key is the 32-byte scalar (hex); the public key is the 33-byte
    compressed point (hex).
    """
    priv = ec.generate_private_key(_CURVE)
    priv_int = priv.private_numbers().private_value
    priv_hex = priv_int.to_bytes(32, "big").hex()
    pub_hex = _compressed_public_hex(priv.public_key())
    return priv_hex, pub_hex


def public_from_private(priv_hex: str) -> str:
    """Derive the compressed public-key hex from a private-key hex."""
    return _compressed_public_hex(_load_private(priv_hex).public_key())


# ---------------------------------------------------------------------------
# Signing / verification (sign over the SHA-256 of the message)
# ---------------------------------------------------------------------------

def sign(priv_hex: str, message: bytes) -> str:
    """Sign ``message`` with ECDSA(secp256k1, SHA-256). Returns DER signature hex."""
    priv = _load_private(priv_hex)
    digest = sha256(message)
    signature = priv.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256())))
    return signature.hex()


def verify(pub_hex: str, message: bytes, signature_hex: str) -> bool:
    """Verify a DER signature hex over ``message`` against a public key."""
    try:
        pub = _load_public(pub_hex)
        digest = sha256(message)
        pub.verify(
            bytes.fromhex(signature_hex),
            digest,
            ec.ECDSA(Prehashed(hashes.SHA256())),
        )
        return True
    except (InvalidSignature, ValueError):
        return False


# ---------------------------------------------------------------------------
# Addresses & validation
# ---------------------------------------------------------------------------

def _base32_lower_nopad(data: bytes) -> str:
    return base64.b32encode(data).decode("ascii").lower().rstrip("=")


def address(pub_hex: str) -> str:
    """Derive a short PLS address from a public key.

    address = "pls1" + base32( sha256(sha256(pubkey))[:20] )

    Double-SHA-256 mirrors Bitcoin's hash160 step without depending on RIPEMD-160
    (which is disabled in some OpenSSL 3 builds).
    """
    pub_bytes = bytes.fromhex(pub_hex)
    fingerprint = sha256(sha256(pub_bytes))[:20]
    return "pls1" + _base32_lower_nopad(fingerprint)


def is_valid_hex(value: str, n_bytes: int | None = None) -> bool:
    """True if ``value`` is valid lowercase/uppercase hex (optionally of n_bytes)."""
    try:
        raw = bytes.fromhex(value)
    except (ValueError, TypeError):
        return False
    if n_bytes is not None and len(raw) != n_bytes:
        return False
    return True
