"""PresentationVerifier — the seam that lets the ZK backend swap in with no migration.

A verifier turns an off-fabric eIDAS / EUDI-Wallet presentation into an :class:`Admission`
— the non-``kind``/``verifier`` content of a ``personhood-anchor``. Because the on-fabric
schema only ever sees an ``Admission``, the *backend* can change (trusted-RP today,
zero-knowledge tomorrow) **without a record-format migration**.

Phase-1 backend (:class:`TrustedRPVerifier`, pure-Python now): the node is a registered
eIDAS Relying Party. In a real deployment ``verify_presentation`` is where the OpenID4VP /
SD-JWT-VC signature checks run (proving a valid PID + ``age_over_18`` from a trusted issuer);
here it validates the presentation's structure, enforces the **multi-issuer trust registry**
(EUDI primary + a non-EUDI fallback issuer class, so no single-issuer monopoly), and derives
the scope nullifier + pairwise DID from the holder secret. Honest trust statement: the RP
sees the holder secret at admission and must handle it ephemerally; trustless uniqueness
needs the phase-2 ZK backend.

Phase-2 backend (:class:`ZkVerifier`, dependency-gated): verifies a BBS+/SD-JWT-VC/SNARK
proof that a hidden valid EU-PID backs the nullifier, so the RP never learns the PID. Fenced
behind a lazy import so importing :mod:`knitweb.personhood` never pulls a SNARK/pairing
toolchain (unavailable on this PEP-668 box — see ``docs/DEPENDENCY_READINESS.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Dict, Protocol, runtime_checkable

from ..core import crypto
from . import nullifier as nullmod
from . import pairwise as pwmod
from . import records
from .errors import NotPersonError

__all__ = [
    "Admission",
    "Presentation",
    "PresentationVerifier",
    "TrustedRPVerifier",
    "ZkVerifier",
]


@dataclass(frozen=True)
class Admission:
    """The verified, PII-free result of a personhood check — the stable backend seam.

    These fields are exactly the non-``kind``/``verifier`` content of a personhood-anchor,
    so swapping the verifier backend changes nothing on the fabric.
    """

    scope: str
    scope_nullifier: str
    pairwise_did: str
    holder_pairwise: str   # pls1 address of the holder's pairwise key (the anchor co-signer)
    issuer_trust_anchor: str
    issuer_class: int
    nullifier_scheme: int
    key_scheme: int
    not_before: int
    not_after: int
    proof_digest: str


@dataclass(frozen=True)
class Presentation:
    """A holder's admission payload to the RP (models the off-fabric OpenID4VP exchange).

    ``holder_secret`` is the 256-bit holder-side value; in the trusted-RP backend the RP
    sees it at admission (and must not retain it). ``age_over_18``/``is_unique_person`` are
    the results of the selective-disclosure checks a real RP performs cryptographically;
    ``transcript`` is the redacted (PII-free) presentation transcript hashed into the
    proof digest.
    """

    holder_secret: bytes
    issuer_entry: bytes      # the eIDAS Trusted-List entry (or fallback issuer entry) bytes
    age_over_18: bool
    is_unique_person: bool
    not_before: int
    not_after: int
    transcript: bytes


@runtime_checkable
class PresentationVerifier(Protocol):
    """Structural interface every verifier backend satisfies."""

    backend: ClassVar[str]

    def verify_presentation(self, scope: str, presentation: Presentation) -> Admission:
        """Validate a presentation and return a PII-free :class:`Admission` (or raise)."""


class TrustedRPVerifier:
    """Phase-1 trusted Relying-Party backend (pure-Python; no heavy deps)."""

    backend = "trusted-rp/v1"

    def __init__(self, trust_registry: Dict[str, int] | None = None) -> None:
        # issuer_trust_anchor (sha256(entry) hex) -> issuer_class
        registry: Dict[str, int] = dict(trust_registry or {})
        for anchor_hex, issuer_class in registry.items():
            if issuer_class not in records.KNOWN_ISSUER_CLASSES:
                raise ValueError(f"unknown issuer_class {issuer_class} for {anchor_hex}")
        self._registry = registry

    @classmethod
    def from_issuer_entries(cls, entries: Dict[bytes, int]) -> "TrustedRPVerifier":
        return cls({crypto.sha256(entry).hex(): cls_ for entry, cls_ in entries.items()})

    def register_issuer(self, issuer_entry: bytes, issuer_class: int) -> str:
        """Register an accepted issuer (EUDI PID or non-EUDI fallback); return its anchor hex."""
        if issuer_class not in records.KNOWN_ISSUER_CLASSES:
            raise ValueError(f"unknown issuer_class {issuer_class}")
        anchor = crypto.sha256(issuer_entry).hex()
        self._registry[anchor] = issuer_class
        return anchor

    def verify_presentation(self, scope: str, presentation: Presentation) -> Admission:
        if not isinstance(scope, str) or not scope:
            raise NotPersonError("scope must be a non-empty string")

        # 1. Issuer must be in the trust registry (multi-issuer; EUDI + non-EUDI fallback).
        anchor = crypto.sha256(presentation.issuer_entry).hex()
        issuer_class = self._registry.get(anchor)
        if issuer_class is None:
            raise NotPersonError("issuer is not in the trust registry")

        # 2. Personhood claims. (A real RP verifies the OpenID4VP/SD-JWT signatures here.)
        if not presentation.age_over_18:
            raise NotPersonError("age_over_18 not satisfied")
        if not presentation.is_unique_person:
            raise NotPersonError("uniqueness of the natural person not established")

        # 3. Validity window.
        nb, na = presentation.not_before, presentation.not_after
        if not isinstance(nb, int) or isinstance(nb, bool):
            raise NotPersonError("not_before must be an int")
        if not isinstance(na, int) or isinstance(na, bool):
            raise NotPersonError("not_after must be an int")
        if na <= nb:
            raise NotPersonError("not_after must be strictly after not_before")

        # 4. Derive the scope nullifier + pairwise DID from the holder secret
        #    (custodial at admission; the secret is handled ephemerally and never stored).
        try:
            scope_nullifier = nullmod.scope_nullifier(presentation.holder_secret, scope)
            _, holder_pub = pwmod.derive_pairwise_keypair(presentation.holder_secret, scope)
        except ValueError as exc:
            raise NotPersonError(str(exc)) from exc

        return Admission(
            scope=scope,
            scope_nullifier=scope_nullifier,
            pairwise_did=pwmod.pairwise_did(holder_pub),
            holder_pairwise=pwmod.pairwise_address(holder_pub),
            issuer_trust_anchor=anchor,
            issuer_class=issuer_class,
            nullifier_scheme=records.NULLIFIER_SCHEME_SHA256,
            key_scheme=crypto.SCHEME_SECP256K1_ECDSA,
            not_before=nb,
            not_after=na,
            proof_digest=crypto.sha256(presentation.transcript).hex(),
        )


class ZkVerifier:
    """Phase-2 zero-knowledge backend (dependency-gated; same Admission seam)."""

    backend = "zk-anoncreds/v1"

    def verify_presentation(self, scope: str, presentation: Presentation) -> Admission:
        # The ZK proof (BBS+/SD-JWT-VC/SNARK) that a hidden valid EU-PID backs the nullifier
        # needs a pairing/SNARK toolchain that is not installable on this PEP-668 box. The
        # lazy import keeps `import knitweb.personhood` free of that dependency.
        try:
            import knitweb_zk  # type: ignore  # noqa: F401  (future, optional dependency)
        except ImportError as exc:
            raise NotImplementedError(
                "ZkVerifier (phase-2 zero-knowledge backend) is unavailable: it requires a "
                "pairing/SNARK toolchain that is dependency-gated on this environment "
                "(see docs/DEPENDENCY_READINESS.md). The Admission seam is identical, so it "
                "swaps in without a record-format migration."
            ) from exc
        raise NotImplementedError("ZkVerifier wiring pending the knitweb_zk backend")
