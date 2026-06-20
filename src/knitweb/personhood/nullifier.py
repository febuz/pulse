"""Scope nullifier — the one-person-one-scope sybil key (scheme 0).

A nullifier reconciles two requirements that look contradictory: a person must be
**unique** within a scope (so they cannot register or vote twice), yet **unlinkable**
across scopes (so their activity in a referendum cannot be correlated with a crowdfund).
The resolution is a *scope-scoped* value derived from a long-term holder secret:

    nullifier(secret, scope) = sha256(canonical.encode([DOMAIN, scope, secret]))

Same ``(secret, scope)`` always yields the same nullifier (double-registration in a
scope is detectable), but two different scopes yield uncorrelated nullifiers. This is
the "scope-rate-limited pseudonym" shape the EUDI ARF itself describes.

Two deliberate hardening choices (see the plan's threat model):

  * ``secret`` is a **256-bit CSPRNG value generated holder-side and NEVER derived from
    PID material** — so even an adversary who sees an on-fabric nullifier cannot grind a
    candidate-PID list to deanonymize it.
  * the inputs are framed by :func:`core.canonical.encode` (length-prefixed, deterministic)
    rather than raw ``secret || scope`` concatenation, so two different ``(secret, scope)``
    pairs can never alias to one nullifier.

Trust scope of scheme 0 (honest labelling): this is the *trusted-RP* construction — the
verifier sees ``secret`` at admission and must handle it ephemerally. The public
nullifier is unlinkable without the secret (what EDPB 02/2025 requires for crypto-shredding
to satisfy GDPR Art.17), but trustless uniqueness needs the reserved EC-VRF scheme 1
(non-custodial), added later without a record-format migration.
"""

from __future__ import annotations

import os

from ..core import canonical, crypto

__all__ = ["NULLIFIER_DOMAIN", "SECRET_BYTES", "new_holder_secret", "scope_nullifier"]

NULLIFIER_DOMAIN = b"knitweb-personhood-nullifier:v1"
SECRET_BYTES = 32


def new_holder_secret() -> bytes:
    """Return a fresh 256-bit CSPRNG holder secret (generated holder-side)."""
    return os.urandom(SECRET_BYTES)


def _check_inputs(secret: bytes, scope: str) -> None:
    if not isinstance(secret, (bytes, bytearray)) or len(secret) != SECRET_BYTES:
        raise ValueError(f"holder secret must be {SECRET_BYTES} bytes")
    if not isinstance(scope, str) or not scope:
        raise ValueError("scope must be a non-empty string")


def scope_nullifier(secret: bytes, scope: str) -> str:
    """Return the 32-byte hex scheme-0 nullifier for ``(secret, scope)``."""
    _check_inputs(secret, scope)
    return crypto.sha256(
        canonical.encode([NULLIFIER_DOMAIN, scope, bytes(secret)])
    ).hex()
