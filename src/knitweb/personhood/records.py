"""Personhood record schemas — the *only* identity data allowed on the fabric.

The vBank guardrail (``docs/DOMAIN_KNITWEB_INTERFACE.md``) is absolute: the
append-only, replicated fabric must never carry raw identity data. So personhood is
represented by exactly two record kinds, both **integer/bytes only** and both passed
through a **deny-by-default field whitelist** — any field outside the whitelist is a
hard error, the same teeth ``core.canonical`` already applies to floats. A planted
``full_name``/``dob``/``document_number`` cannot survive ``assert_personhood_record_shape``.

  * ``personhood-anchor`` — a revocable proof "this is a verified unique EU natural
    person", scoped to one context. It stores a **scope nullifier** (the
    one-person-one-scope sybil key), a **pairwise DID** (per-scope identity), a hash
    of the issuer's eIDAS Trusted-List entry, a validity window, and a **revocation
    pointer** that is deliberately *decoupled* from the nullifier so a published
    revocation never reveals which person was revoked. It carries **no PII** — not a
    name, not a date of birth (only an ``age_over_18`` boolean is proven at admission,
    off-fabric, and never stored), not a national identifier.
  * ``personhood-revoke`` — a feed entry that revokes an anchor by its random
    ``revocation_pointer`` (never by its nullifier).

The anchor is **co-signed** (verifier RP + holder's pairwise key). Both signatures live
outside the record in :mod:`knitweb.personhood.anchor`, so the CID stays a pure content
hash (the same separation ``fabric.attest`` uses).

Design notes locked here (irreversible once data exists — see the plan):
  * the nullifier is ``sha256`` over a *high-entropy holder secret* framed by
    ``canonical.encode`` (never raw ``secret || scope`` concatenation, never derived
    from PID material), and is **scheme-versioned** so a future non-custodial EC-VRF
    nullifier can be added as a *new* scheme without a record-format migration;
  * ``issuer_trust_anchor`` is paired with an ``issuer_class`` enum so EUDI is the
    primary issuer but not the only one — a non-EUDI fallback enrollment path is
    representable from day one (no single-issuer monopoly);
  * every key/nullifier reference carries an explicit scheme byte
    (``key_scheme`` / ``nullifier_scheme``) so a post-quantum or ZK upgrade is a
    soft-fork, not a rewrite.
"""

from __future__ import annotations

from ..core import canonical, crypto

# Keys that belong in the attestation/transport envelope, never inside a signed record.
# Defined locally so this foundation depends only on committed core/fabric primitives (and
# does not reach up into the L5 knitweb plugin layer); they mirror the same envelope keys the
# domain-knitweb contract reserves.
RESERVED_RECORD_KEYS = frozenset({"sig", "signature", "author_pub"})
RESERVED_TRANSPORT_PREFIXES = ("_relay_",)

__all__ = [
    "PersonhoodSchemaError",
    "ANCHOR_KIND",
    "REVOKE_KIND",
    "CRED_TYPE",
    "DID_PREFIX",
    "ANCHOR_WHITELIST",
    "REVOKE_WHITELIST",
    "NULLIFIER_SCHEME_SHA256",
    "NULLIFIER_SCHEME_ECVRF",
    "KNOWN_NULLIFIER_SCHEMES",
    "ISSUER_CLASS_EUDI_PID",
    "ISSUER_CLASS_NON_EUDI_FALLBACK",
    "KNOWN_ISSUER_CLASSES",
    "REASON_UNSPECIFIED",
    "REASON_CREDENTIAL_WITHDRAWN",
    "REASON_SUSPENDED",
    "REASON_ART17_ERASURE",
    "KNOWN_REASON_CODES",
    "assert_personhood_record_shape",
    "build_anchor_record",
    "build_revoke_record",
]


class PersonhoodSchemaError(ValueError):
    """Raised when a personhood record violates the shape or anti-PII whitelist."""


ANCHOR_KIND = "personhood-anchor"
REVOKE_KIND = "personhood-revoke"
CRED_TYPE = "eu-unique-natural-person/v1"
DID_PREFIX = "did:pls:"

# Nullifier construction schemes. Only scheme 0 is blessed today; scheme 1 reserves a
# slot for a non-custodial discrete-log (EC-VRF over secp256k1) nullifier added later.
NULLIFIER_SCHEME_SHA256 = 0   # sha256(canonical.encode([domain, scope, holder_secret]))
NULLIFIER_SCHEME_ECVRF = 1    # reserved: secret * H(scope) + Chaum-Pedersen proof
KNOWN_NULLIFIER_SCHEMES = frozenset({NULLIFIER_SCHEME_SHA256})

# Issuer classes — a registry, not a single mandated issuer (anti-monopoly).
ISSUER_CLASS_EUDI_PID = 0
ISSUER_CLASS_NON_EUDI_FALLBACK = 1
KNOWN_ISSUER_CLASSES = frozenset({ISSUER_CLASS_EUDI_PID, ISSUER_CLASS_NON_EUDI_FALLBACK})

# Revocation reason codes (conceptually aligned with IETF Token Status List statuses).
REASON_UNSPECIFIED = 0
REASON_CREDENTIAL_WITHDRAWN = 1
REASON_SUSPENDED = 2
REASON_ART17_ERASURE = 3
KNOWN_REASON_CODES = frozenset(
    {REASON_UNSPECIFIED, REASON_CREDENTIAL_WITHDRAWN, REASON_SUSPENDED, REASON_ART17_ERASURE}
)

# Deny-by-default whitelists. Anything not listed is rejected as a possible PII leak.
ANCHOR_WHITELIST = frozenset({
    "kind",
    "verifier",
    "holder_pairwise",
    "cred_type",
    "issuer_trust_anchor",
    "issuer_class",
    "scope",
    "nullifier_scheme",
    "scope_nullifier",
    "pairwise_did",
    "key_scheme",
    "not_before",
    "not_after",
    "revocation_pointer",
    "proof_digest",
})

REVOKE_WHITELIST = frozenset({
    "kind",
    "verifier",
    "scope",
    "revocation_pointer",
    "revoked_at",
    "reason_code",
})


# ---------------------------------------------------------------------------
# Field validators (all bytes/int/str; never a float on the canonical path)
# ---------------------------------------------------------------------------

def _require_int(record: dict, key: str) -> int:
    value = record[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise PersonhoodSchemaError(f"{key!r} must be an int (got {type(value).__name__})")
    return value


def _require_enum(record: dict, key: str, allowed: frozenset) -> int:
    value = _require_int(record, key)
    if value not in allowed:
        raise PersonhoodSchemaError(f"{key!r}={value} not in {sorted(allowed)}")
    return value


def _require_hex32(record: dict, key: str) -> str:
    value = record[key]
    if not isinstance(value, str) or not crypto.is_valid_hex(value, 32):
        raise PersonhoodSchemaError(f"{key!r} must be 32-byte hex")
    return value


def _require_address(record: dict, key: str) -> str:
    value = record[key]
    if not isinstance(value, str) or not crypto.is_valid_address(value):
        raise PersonhoodSchemaError(f"{key!r} must be a current PLS address")
    return value


def _require_nonempty_str(record: dict, key: str) -> str:
    value = record[key]
    if not isinstance(value, str) or not value:
        raise PersonhoodSchemaError(f"{key!r} must be a non-empty string")
    return value


def _validate_anchor(record: dict) -> None:
    _require_address(record, "verifier")
    holder = _require_address(record, "holder_pairwise")
    # Two distinct signers are the whole point of the co-signature (verifier RP + a human
    # holder). If one key could fill both roles, a colluding RP could mint consent-less
    # anchors. Refuse it at the schema layer so construction and verification both reject it.
    if record["verifier"] == holder:
        raise PersonhoodSchemaError("verifier and holder_pairwise must be distinct keys")
    if record["cred_type"] != CRED_TYPE:
        raise PersonhoodSchemaError(f"cred_type must be {CRED_TYPE!r}")
    _require_hex32(record, "issuer_trust_anchor")
    _require_enum(record, "issuer_class", KNOWN_ISSUER_CLASSES)
    _require_nonempty_str(record, "scope")
    _require_enum(record, "nullifier_scheme", KNOWN_NULLIFIER_SCHEMES)
    _require_hex32(record, "scope_nullifier")
    did = record["pairwise_did"]
    # The DID is bound to the co-signing holder key, so it cannot name a different key.
    if not isinstance(did, str) or did != f"{DID_PREFIX}{holder}":
        raise PersonhoodSchemaError("pairwise_did must equal did:pls:<holder_pairwise>")
    key_scheme = _require_int(record, "key_scheme")
    if key_scheme not in crypto.KNOWN_SCHEMES:
        raise PersonhoodSchemaError(f"key_scheme={key_scheme} not a blessed crypto scheme")
    not_before = _require_int(record, "not_before")
    not_after = _require_int(record, "not_after")
    if not_after <= not_before:
        raise PersonhoodSchemaError("not_after must be strictly after not_before")
    _require_hex32(record, "revocation_pointer")
    _require_hex32(record, "proof_digest")


def _validate_revoke(record: dict) -> None:
    _require_address(record, "verifier")
    _require_nonempty_str(record, "scope")
    _require_hex32(record, "revocation_pointer")
    _require_int(record, "revoked_at")
    _require_enum(record, "reason_code", KNOWN_REASON_CODES)


def assert_personhood_record_shape(record: dict, *, kind: str) -> None:
    """Validate a personhood record's shape and enforce the anti-PII whitelist.

    Guards (in order): it is a dict with only string keys; the kind matches; no
    attestation-envelope or transport keys leaked in; **no field outside the kind's
    whitelist** (the anti-PII teeth); all required fields present; per-field types are
    integer/bytes/str (never a float); and the whole record is canonically encodable.
    """
    if not isinstance(record, dict):
        raise TypeError("personhood record must be a dict")
    if kind == ANCHOR_KIND:
        whitelist = ANCHOR_WHITELIST
    elif kind == REVOKE_KIND:
        whitelist = REVOKE_WHITELIST
    else:
        raise PersonhoodSchemaError(f"unknown personhood record kind: {kind!r}")

    non_string = [k for k in record if not isinstance(k, str)]
    if non_string:
        raise PersonhoodSchemaError(f"record keys must be strings; got {non_string!r}")
    if record.get("kind") != kind:
        raise PersonhoodSchemaError(f"record kind must be {kind!r}")

    for key in record:
        if key in RESERVED_RECORD_KEYS:
            raise PersonhoodSchemaError(f"{key!r} belongs in the attestation, not the record")
        if any(key.startswith(prefix) for prefix in RESERVED_TRANSPORT_PREFIXES):
            raise PersonhoodSchemaError(f"{key!r} is a transport envelope key")

    extra = set(record) - whitelist
    if extra:
        # The single most important rule: an unknown field could be PII. Refuse it.
        raise PersonhoodSchemaError(
            f"non-whitelisted field(s) rejected (possible PII leak): {sorted(extra)}"
        )
    missing = whitelist - set(record)
    if missing:
        raise PersonhoodSchemaError(f"missing required field(s): {sorted(missing)}")

    if kind == ANCHOR_KIND:
        _validate_anchor(record)
    else:
        _validate_revoke(record)

    canonical.encode(record)  # fail fast on any non-canonical content (also rejects floats)


# ---------------------------------------------------------------------------
# Record builders (construct + validate; never accept a free-form **kwargs dump)
# ---------------------------------------------------------------------------

def build_anchor_record(
    *,
    verifier: str,
    holder_pairwise: str,
    issuer_trust_anchor: str,
    issuer_class: int,
    scope: str,
    scope_nullifier: str,
    not_before: int,
    not_after: int,
    revocation_pointer: str,
    proof_digest: str,
    nullifier_scheme: int = NULLIFIER_SCHEME_SHA256,
    key_scheme: int = crypto.SCHEME_SECP256K1_ECDSA,
    pairwise_did: str | None = None,
) -> dict:
    """Build a validated ``personhood-anchor`` record (no PII can be passed in)."""
    if pairwise_did is None:
        pairwise_did = f"{DID_PREFIX}{holder_pairwise}"
    record = {
        "kind": ANCHOR_KIND,
        "verifier": verifier,
        "holder_pairwise": holder_pairwise,
        "cred_type": CRED_TYPE,
        "issuer_trust_anchor": issuer_trust_anchor,
        "issuer_class": issuer_class,
        "scope": scope,
        "nullifier_scheme": nullifier_scheme,
        "scope_nullifier": scope_nullifier,
        "pairwise_did": pairwise_did,
        "key_scheme": key_scheme,
        "not_before": not_before,
        "not_after": not_after,
        "revocation_pointer": revocation_pointer,
        "proof_digest": proof_digest,
    }
    assert_personhood_record_shape(record, kind=ANCHOR_KIND)
    return record


def build_revoke_record(
    *,
    verifier: str,
    scope: str,
    revocation_pointer: str,
    revoked_at: int,
    reason_code: int = REASON_UNSPECIFIED,
) -> dict:
    """Build a validated ``personhood-revoke`` record (keyed by the random pointer)."""
    record = {
        "kind": REVOKE_KIND,
        "verifier": verifier,
        "scope": scope,
        "revocation_pointer": revocation_pointer,
        "revoked_at": revoked_at,
        "reason_code": reason_code,
    }
    assert_personhood_record_shape(record, kind=REVOKE_KIND)
    return record
