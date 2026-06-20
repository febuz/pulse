"""Pairwise DIDs — a distinct secp256k1 identity per (holder, scope).

For unlinkability, a person presents a *different* key in every scope. The holder signs
their votes/pledges in scope S with a keypair derived deterministically from their
long-term secret and S:

    priv(secret, scope) = sha256(canonical.encode([DOMAIN, scope, secret]))

Because the keys for two scopes are independent (no shared public component, no derivable
relationship), an observer cannot correlate a person across scopes from their fabric
identities. The DID method reuses knitweb's existing versioned ``pls1`` address
(``did:pls:<address>``) so it inherits the post-quantum scheme-byte soft-fork path,
consistent with the repo's existing ``did:key:*`` / ``did:dkg:*`` usage. This mirrors the
idiomatic deterministic derivation in :meth:`ledger.node.AccountNode.from_seed`.
"""

from __future__ import annotations

from ..core import canonical, crypto
from .records import DID_PREFIX

__all__ = [
    "PAIRWISE_DOMAIN",
    "derive_pairwise_keypair",
    "pairwise_address",
    "pairwise_did",
]

PAIRWISE_DOMAIN = b"knitweb-personhood-pairwise:v1"
_SECRET_BYTES = 32
# secp256k1 group order n: a valid private scalar is in [1, n).
_SECP256K1_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def derive_pairwise_keypair(secret: bytes, scope: str) -> tuple[str, str]:
    """Return the deterministic (priv_hex, pub_hex) pairwise keypair for ``(secret, scope)``.

    The scalar is rejection-sampled into ``[1, n)``: on the ~2**-128 chance the bare digest is
    0 or >= the curve order, it rehashes with a counter so a legitimate (secret, scope) is
    always enrollable. ``counter == 0`` keeps the common-case derivation identical to a bare
    hash of the framed inputs.
    """
    if not isinstance(secret, (bytes, bytearray)) or len(secret) != _SECRET_BYTES:
        raise ValueError(f"holder secret must be {_SECRET_BYTES} bytes")
    if not isinstance(scope, str) or not scope:
        raise ValueError("scope must be a non-empty string")
    for counter in range(256):
        material = [PAIRWISE_DOMAIN, scope, bytes(secret)]
        if counter:
            material.append(counter)
        priv = crypto.sha256(canonical.encode(material)).hex()
        if 0 < int(priv, 16) < _SECP256K1_ORDER:
            return priv, crypto.public_from_private(priv)
    raise ValueError("could not derive a valid pairwise scalar")  # unreachable in practice


def pairwise_address(pub_hex: str) -> str:
    """The ``pls1`` address of a pairwise public key."""
    return crypto.address(pub_hex)


def pairwise_did(pub_hex: str) -> str:
    """The ``did:pls:<address>`` identifier for a pairwise public key."""
    return f"{DID_PREFIX}{crypto.address(pub_hex)}"
