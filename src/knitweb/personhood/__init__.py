"""Personhood — the sybil-resistance foundation for vBank (voting + crowdfunding).

This layer (L3.5, between the L3 fabric and the L5 domain knitwebs) turns an off-fabric
eIDAS / EUDI-Wallet personhood check into a **revocable, privacy-preserving proof** that
voting and crowdfunding consume as their one-person-one-scope gate. It stores *only* a
proof — never PII — so the privacy model is built in from day one rather than retrofitted
onto an append-only fabric (which is impossible).

Dependency rule: domain apps import ``personhood``; ``personhood`` never imports an app.
It depends only on committed ``core`` and ``fabric`` primitives — never on the L5 knitweb
plugin layer.
"""

from __future__ import annotations

from . import anchor, errors, gate, nullifier, pairwise, records, revocation, status_tree, verifier
from .anchor import CoSignedAnchor, anchor_from_admission, co_sign_anchor
from .gate import AnchorIndex, PersonhoodTicket, enroll, require_personhood
from .errors import (
    AlreadyRegisteredError,
    ExpiredError,
    NotPersonError,
    PersonhoodError,
    RevokedError,
)
from .verifier import (
    Admission,
    Presentation,
    PresentationVerifier,
    TrustedRPVerifier,
    ZkVerifier,
)
from .nullifier import new_holder_secret, scope_nullifier
from .pairwise import derive_pairwise_keypair, pairwise_address, pairwise_did
from .status_tree import (
    StatusTree,
    verify_membership,
    verify_non_membership,
)
from .revocation import (
    RevocationLog,
    StatusCommitment,
    check_non_revocation,
)
from .records import (
    ANCHOR_KIND,
    CRED_TYPE,
    PersonhoodSchemaError,
    REVOKE_KIND,
    assert_personhood_record_shape,
    build_anchor_record,
    build_revoke_record,
)

__all__ = [
    "records",
    "anchor",
    "nullifier",
    "pairwise",
    "status_tree",
    "revocation",
    "StatusTree",
    "verify_membership",
    "verify_non_membership",
    "RevocationLog",
    "StatusCommitment",
    "check_non_revocation",
    "PersonhoodSchemaError",
    "ANCHOR_KIND",
    "REVOKE_KIND",
    "CRED_TYPE",
    "assert_personhood_record_shape",
    "build_anchor_record",
    "build_revoke_record",
    "CoSignedAnchor",
    "co_sign_anchor",
    "anchor_from_admission",
    "errors",
    "verifier",
    "gate",
    "AnchorIndex",
    "PersonhoodTicket",
    "enroll",
    "require_personhood",
    "PersonhoodError",
    "NotPersonError",
    "AlreadyRegisteredError",
    "RevokedError",
    "ExpiredError",
    "Admission",
    "Presentation",
    "PresentationVerifier",
    "TrustedRPVerifier",
    "ZkVerifier",
    "new_holder_secret",
    "scope_nullifier",
    "derive_pairwise_keypair",
    "pairwise_address",
    "pairwise_did",
]
