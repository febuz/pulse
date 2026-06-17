"""Cryptographic primitives for PLS: secp256k1 ECDSA + SHA-256.

PLS keypairs and balance-keeping use Bitcoin-style cryptography — the secp256k1
curve with ECDSA signatures over SHA-256 — implemented on the standard
``cryptography`` library (no native build step required).

Public keys are serialized as 33-byte compressed SEC1 points (hex). Private keys
are 32-byte scalars (hex). Signatures are DER-encoded (hex). A PLS address is a
short, content-addressed fingerprint of the public key (``pls1`` prefix), carrying
a 1-byte *scheme version* so the signature algorithm an address commits to is
explicit and a post-quantum scheme can be added later by soft-fork (see
``docs/CRYPTO_CORPUS_STUDY.md`` §3).
"""

from __future__ import annotations

import base64
import binascii
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
    "decode_address",
    "address_scheme",
    "is_valid_address",
    "is_valid_hex",
    "SCHEME_SECP256K1_ECDSA",
    "KNOWN_SCHEMES",
    "ADDRESS_HRP",
]

_CURVE = ec.SECP256K1()

# ---------------------------------------------------------------------------
# Address scheme registry
# ---------------------------------------------------------------------------
#
# Every PLS address commits to the signature scheme of the key behind it via a
# leading version byte. secp256k1-ECDSA is Shor-breakable once a pubkey is
# revealed, so it is a *deprecation-track* primitive: reserving the byte now lets
# a stateless post-quantum scheme (prefer SPHINCS+ / ML-DSA — never stateful XMSS
# for user keys) occupy a distinct value via soft-fork without re-deriving any
# existing address.

ADDRESS_HRP = "pls1"

SCHEME_SECP256K1_ECDSA = 0   # current/only blessed scheme
# Reserved (not yet blessed): 1 = SPHINCS+ , 2 = ML-DSA , 3 = hybrid co-sign.
KNOWN_SCHEMES = frozenset({SCHEME_SECP256K1_ECDSA})

_FINGERPRINT_LEN = 20            # bytes of double-SHA-256 kept (cf. Bitcoin hash160)
_ADDR_PAYLOAD_LEN = 1 + _FINGERPRINT_LEN  # scheme byte + fingerprint


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


def _base32_decode_nopad(text: str) -> bytes:
    """Inverse of :func:`_base32_lower_nopad`. Raises on bad input."""
    up = text.upper()
    pad = (-len(up)) % 8
    return base64.b32decode(up + ("=" * pad))


def address(pub_hex: str, scheme: int = SCHEME_SECP256K1_ECDSA) -> str:
    """Derive a short, versioned PLS address from a public key.

    address = "pls1" + base32( scheme_byte || sha256(sha256(pubkey))[:20] )

    Double-SHA-256 mirrors Bitcoin's hash160 step without depending on RIPEMD-160
    (which is disabled in some OpenSSL 3 builds). The leading ``scheme`` byte makes
    the signature algorithm explicit so a post-quantum scheme can be added by
    soft-fork. Only schemes in :data:`KNOWN_SCHEMES` may be minted today.
    """
    if scheme not in KNOWN_SCHEMES:
        raise ValueError(f"unknown address scheme: {scheme}")
    if not 0 <= scheme <= 255:
        raise ValueError("scheme must be a single byte (0..255)")
    pub_bytes = bytes.fromhex(pub_hex)
    fingerprint = sha256(sha256(pub_bytes))[:_FINGERPRINT_LEN]
    payload = bytes([scheme]) + fingerprint
    return ADDRESS_HRP + _base32_lower_nopad(payload)


def decode_address(addr: str) -> tuple[int, bytes]:
    """Decode a PLS address into ``(scheme, fingerprint_bytes)``.

    Raises ValueError if the human-readable prefix is wrong, the base32 body is
    malformed, or the payload is not exactly ``scheme || 20-byte fingerprint``.
    The scheme is returned even when unknown, so callers can reject an
    unrecognised scheme deliberately rather than misread the fingerprint.
    """
    if not isinstance(addr, str) or not addr.startswith(ADDRESS_HRP):
        raise ValueError("address must start with the pls1 prefix")
    body = addr[len(ADDRESS_HRP):]
    if not body:
        raise ValueError("address has no payload")
    try:
        payload = _base32_decode_nopad(body)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"address body is not valid base32: {exc}") from exc
    if len(payload) != _ADDR_PAYLOAD_LEN:
        raise ValueError(
            f"address payload must be {_ADDR_PAYLOAD_LEN} bytes, got {len(payload)}"
        )
    return payload[0], payload[1:]


def address_scheme(addr: str) -> int:
    """Return the scheme version byte of ``addr`` (raises ValueError if malformed)."""
    return decode_address(addr)[0]


def is_valid_address(addr: str) -> bool:
    """True iff ``addr`` is well-formed *and* carries a blessed (known) scheme."""
    try:
        scheme, _ = decode_address(addr)
    except ValueError:
        return False
    return scheme in KNOWN_SCHEMES


def is_valid_hex(value: str, n_bytes: int | None = None) -> bool:
    """True if ``value`` is valid lowercase/uppercase hex (optionally of n_bytes)."""
    try:
        raw = bytes.fromhex(value)
    except (ValueError, TypeError):
        return False
    if n_bytes is not None and len(raw) != n_bytes:
        return False
    return True
