"""Demographic vote-supply registry — who is a person, and how many votes a world owes.

The governance question this answers: *how large is the vote supply allowed to be?*
The principle is **one vote per registered person**, and the population is counted
**per world** (earth, moon, …) — a credibly-neutral DePIN should not let any operator
mint governance weight out of thin air, so the cap is anchored to real, registered
humans plus a bounded forward allowance for the people expected to be born this year.

So the demographic max vote supply is, summed over every world::

    max_vote_supply = Σ_world ( registered_persons(world) + expected_births(world, year) )

e.g. *1 000 000 registered inhabitants on the moon ⇒ 1 000 000 votes for the moon, plus
the moon's expected births for the year.* The birth allowance is what lets newly-born
(and newly-registering) people receive a vote within the year without re-capping mid-year.

A **person** registers exactly once (one-vote-per-person, worldwide) by one of two paths —
and the cap **includes both**:

  * **National identity** (``RegistrationKind.NATIONAL``) — a national-registry identifier.
  * **Freedom freeport** (``RegistrationKind.FREEPORT``) — for the unbanked / stateless /
    sovereign, an IMEI + email-address pair with an *ad-hoc proof of identity*. This is the
    "freeport" on-ramp; **freeport registrations count toward max supply too**.

Privacy: raw PII (national id, IMEI, email, the proof document) is **never stored**. Each
registration keeps only content-addressed digests — a ``subject`` digest (the dedup key, so
the same human cannot register twice and double their vote) and a ``proof`` digest (evidence
that the ad-hoc/identity proof was presented). Everything is integer / hash only; no floats,
no signed-record or canonical-encoding changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

from ..core import canonical, crypto

__all__ = [
    "RegistrationKind",
    "Registration",
    "register_national",
    "register_freeport",
    "WorldRegistry",
]


class RegistrationKind(Enum):
    """How a person proved they are one person owed one vote."""

    NATIONAL = "national"    # a national-identity registry id
    FREEPORT = "freeport"    # freedom-freeport: IMEI + email + ad-hoc proof of identity


def _require_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{name} must be a non-empty str")
    return value


def _require_int(name: str, value: int, *, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int, not {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum} (got {value})")
    return value


def _digest(domain: str, *parts: str) -> str:
    """A domain-separated SHA-256 hex digest over ``parts`` — never stores the raw input."""
    payload = ("\x1f".join((domain, *parts))).encode("utf-8")
    return crypto.sha256_hex(payload)


@dataclass(frozen=True)
class Registration:
    """One registered person owed one vote — only digests, never raw PII.

    ``subject`` is the worldwide dedup key (same human ⇒ same subject ⇒ at most one vote).
    ``proof`` attests that the identity/ad-hoc proof was presented, without storing it.
    """

    world: str
    kind: RegistrationKind
    subject: str       # SHA-256 hex of the identity — the one-vote-per-person dedup key
    proof: str         # SHA-256 hex attesting the presented proof of identity
    timestamp: int

    def __post_init__(self) -> None:
        _require_text("world", self.world)
        if not isinstance(self.kind, RegistrationKind):
            raise TypeError("kind must be a RegistrationKind")
        if not crypto.is_valid_hex(self.subject, 32):
            raise ValueError("subject must be a 32-byte hex digest")
        if not crypto.is_valid_hex(self.proof, 32):
            raise ValueError("proof must be a 32-byte hex digest")
        _require_int("timestamp", self.timestamp, minimum=0)

    def to_record(self) -> dict:
        return {
            "kind": "govern-registration",
            "world": self.world,
            "registration_kind": self.kind.value,
            "subject": self.subject,
            "proof": self.proof,
            "timestamp": self.timestamp,
        }

    @property
    def cid(self) -> str:
        """Content-addressed id of this registration (auditable, replay-detectable)."""
        return canonical.cid(self.to_record())


def register_national(world: str, national_id: str, *, timestamp: int) -> Registration:
    """Register a person via a national-identity id (raw id is hashed, never stored)."""
    _require_text("national_id", national_id)
    world = _require_text("world", world)
    subject = _digest("govern:subject:national", national_id)
    proof = _digest("govern:proof:national", national_id)
    return Registration(world=world, kind=RegistrationKind.NATIONAL,
                        subject=subject, proof=proof, timestamp=timestamp)


def register_freeport(
    world: str,
    imei: str,
    email: str,
    ad_hoc_proof: str,
    *,
    timestamp: int,
) -> Registration:
    """Register a person via the freedom-freeport path: IMEI + email + ad-hoc proof.

    The ``ad_hoc_proof`` (a free-form identity attestation for the unbanked/stateless) is
    **required** and hashed into ``proof``; the (IMEI, email) pair is hashed into the
    one-vote-per-person ``subject``. None of the raw values are retained.
    """
    _require_text("imei", imei)
    _require_text("email", email)
    _require_text("ad_hoc_proof", ad_hoc_proof)
    world = _require_text("world", world)
    subject = _digest("govern:subject:freeport", imei, email)
    proof = _digest("govern:proof:freeport", imei, email, ad_hoc_proof)
    return Registration(world=world, kind=RegistrationKind.FREEPORT,
                        subject=subject, proof=proof, timestamp=timestamp)


class WorldRegistry:
    """Per-world census of registered persons + the demographic vote-supply cap.

    Holds at most one :class:`Registration` per ``subject`` (one vote per person, worldwide)
    and a per-world ``expected_births`` projection for the current ``year``. The cap it
    exposes — :meth:`max_vote_supply` — is what the :class:`~knitweb.govern.votebank.VoteBank`
    is allowed to issue against; it can only grow as real people register (no premine).
    """

    def __init__(self, *, year: int) -> None:
        self.year = _require_int("year", year, minimum=0)
        self._subjects: Dict[str, str] = {}        # subject -> world (dedup + assignment)
        self._kinds: Dict[str, RegistrationKind] = {}
        self._births: Dict[str, int] = {}          # world -> expected births this year

    # -- registration -----------------------------------------------------------------

    def register(self, registration: Registration) -> bool:
        """Add ``registration``. Returns False if this person is already registered.

        Enforces one-vote-per-person **across both registration paths and all worlds**:
        a ``subject`` already present is rejected (no double-registration to double a vote).
        """
        if not isinstance(registration, Registration):
            raise TypeError("registration must be a Registration")
        if registration.subject in self._subjects:
            return False
        self._subjects[registration.subject] = registration.world
        self._kinds[registration.subject] = registration.kind
        return True

    def is_registered(self, subject: str) -> bool:
        return subject in self._subjects

    def world_of(self, subject: str) -> Optional[str]:
        return self._subjects.get(subject)

    # -- demographic projection -------------------------------------------------------

    def set_expected_births(self, world: str, expected_births: int) -> None:
        """Set a world's expected births for ``self.year`` (the birth-rate allowance)."""
        world = _require_text("world", world)
        _require_int("expected_births", expected_births, minimum=0)
        self._births[world] = expected_births

    def registered_persons(self, world: Optional[str] = None) -> int:
        """Count of registered persons (all worlds, or one ``world``)."""
        if world is None:
            return len(self._subjects)
        return sum(1 for w in self._subjects.values() if w == world)

    def expected_births(self, world: Optional[str] = None) -> int:
        """Expected births this year (all worlds, or one ``world``)."""
        if world is None:
            return sum(self._births.values())
        return self._births.get(world, 0)

    def worlds(self) -> list[str]:
        """Every world that has either a registered person or a birth projection."""
        return sorted(set(self._subjects.values()) | set(self._births.keys()))

    def max_vote_supply(self, world: Optional[str] = None) -> int:
        """The demographic vote cap: registered persons + expected births this year.

        Summed over all worlds (or restricted to one ``world``). Freeport registrations are
        included in the person count, so the cap covers the unbanked/stateless on-ramp too.
        """
        return self.registered_persons(world) + self.expected_births(world)
