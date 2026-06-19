"""Crowdfunding on the votebank — fund proposals by breadth of backers, not weight of whales.

Ordinary token crowdfunding is plutocratic: whoever brings the most capital decides. The
votebank already solves the matching problem for governance — **one registered person, one
vote** (national identity *or* freedom-freeport), demographically capped — so this module
applies that same principle to *funding*: **one person, one backing**. A campaign therefore
measures two things at once:

  * **Breadth** — how many *distinct registered people* back it (the votebank's one-per-person
    rule; a whale cannot manufacture support, only register once like everyone else); and
  * **Capital** — the PLS each backer pledges behind their single backing.

A campaign succeeds only when it clears **both** a capital ``goal`` and a ``min_backers``
breadth threshold by its ``deadline``. Settlement is **all-or-nothing** (Kickstarter-style):
goal+breadth met ⇒ the escrow releases to the beneficiary; otherwise every backer is refunded.
Nothing is minted (no premine) — the pool is exactly what real backers pledged — and, like
``pouw/dispute.py``, this layer is **advisory integer accounting**: it decides *who is owed
what* in PLS-wei; the caller moves the value with ordinary Knits.

Recency matters too: :meth:`Campaign.momentum` reuses the governance tally so **recent backing
weighs exponentially more** — a campaign gaining backers *now* reads hotter than one that
stalled, without changing the all-or-nothing settlement. Integer / hash only; no floats.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Set, Tuple

from ..core import canonical
from .proximity import ProximityProof
from .registry import Registration
from .tally import Decay, Vote, tally
from .votebank import VoteBank

__all__ = ["CampaignStatus", "Pledge", "CampaignResult", "Campaign"]


class CampaignStatus(Enum):
    """Lifecycle of a crowdfunding campaign."""

    OPEN = "open"          # accepting pledges, before deadline / resolution
    FUNDED = "funded"      # goal + breadth met at resolution → escrow released to beneficiary
    EXPIRED = "expired"    # not met at resolution → every backer refunded


def _require_int(name: str, value: int, *, minimum: int, maximum: Optional[int] = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int, not {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum} (got {value})")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum} (got {value})")
    return value


def _require_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{name} must be a non-empty str")
    return value


@dataclass(frozen=True)
class Pledge:
    """One person's single backing of a campaign: their ``subject`` + PLS-wei ``amount``."""

    backer: str        # the registry dedup key — one backing per person (votebank principle)
    amount: int        # PLS-wei pledged behind this backing
    beat: int          # Pulse beat the pledge was made

    def __post_init__(self) -> None:
        _require_text("backer", self.backer)
        _require_int("amount", self.amount, minimum=1)
        _require_int("beat", self.beat, minimum=0)

    def to_record(self) -> dict:
        return {"kind": "govern-pledge", "backer": self.backer,
                "amount": self.amount, "beat": self.beat}

    @property
    def cid(self) -> str:
        return canonical.cid(self.to_record())


@dataclass(frozen=True)
class CampaignResult:
    """The all-or-nothing settlement decision (advisory; caller moves the PLS)."""

    status: CampaignStatus
    raised: int                          # total PLS-wei pledged
    backers: int                         # distinct backers
    release_to_beneficiary: int          # PLS-wei to the beneficiary (FUNDED) else 0
    refunds: Tuple[Tuple[str, int], ...] # (backer, amount) pairs to return (EXPIRED) else ()

    @property
    def funded(self) -> bool:
        return self.status is CampaignStatus.FUNDED


class Campaign:
    """A votebank-gated crowdfunding campaign: breadth (one person, one backing) + PLS capital.

    Bound to a :class:`~knitweb.govern.votebank.VoteBank` purely for its registry: only
    registered people may back, and each backs at most once (the votebank's one-per-person rule
    applied to funding). Succeeds at resolution iff it cleared **both** the capital ``goal`` and
    the ``min_backers`` breadth threshold by ``deadline``.
    """

    def __init__(
        self,
        bank: VoteBank,
        beneficiary: str,
        goal: int,
        deadline: int,
        *,
        min_backers: int = 1,
        created: int = 0,
        beacon: Optional[str] = None,
        min_local_backers: int = 0,
        proximity_window: int = 0,
        min_rssi_dbm: int = -90,
    ) -> None:
        if not isinstance(bank, VoteBank):
            raise TypeError("bank must be a VoteBank")
        self.bank = bank
        self.beneficiary = _require_text("beneficiary", beneficiary)
        self.goal = _require_int("goal", goal, minimum=1)
        self.created = _require_int("created", created, minimum=0)
        self.deadline = _require_int("deadline", deadline, minimum=0)
        if self.deadline < self.created:
            raise ValueError("deadline must be >= created")
        self.min_backers = _require_int("min_backers", min_backers, minimum=1)

        # Bluetooth local backing: optionally require N backers physically present at a beacon.
        self.beacon = _require_text("beacon", beacon) if beacon is not None else None
        self.min_local_backers = _require_int("min_local_backers", min_local_backers, minimum=0)
        self.proximity_window = _require_int("proximity_window", proximity_window, minimum=0)
        self.min_rssi_dbm = _require_int("min_rssi_dbm", min_rssi_dbm, minimum=-120, maximum=0)
        if self.min_local_backers > 0 and self.beacon is None:
            raise ValueError("min_local_backers requires a beacon to attest presence against")

        self.status = CampaignStatus.OPEN
        self._pledges: List[Pledge] = []
        self._backers: Set[str] = set()
        self._local_backers: Set[str] = set()
        self._result: Optional[CampaignResult] = None

    # -- backing ----------------------------------------------------------------------

    def pledge(
        self,
        registration: Registration,
        amount: int,
        *,
        beat: int,
        proximity: Optional[ProximityProof] = None,
    ) -> Optional[Pledge]:
        """Back this campaign once with ``amount`` PLS-wei. None if this person already backed.

        Requires the campaign OPEN, the person **registered** in the bank's registry, and the
        pledge to land within ``[created, deadline]``. One backing per person (votebank rule).

        An optional ``proximity`` proof marks the pledge as a **local** (Bluetooth-present)
        backing: the proof must be for this ``subject`` and the campaign's ``beacon``, in range
        (``rssi_dbm >= min_rssi_dbm``) and co-timed within ``proximity_window`` beats of the
        pledge. A proof that names the wrong backer/beacon is rejected; one merely out of
        range/stale is accepted as an ordinary (non-local) pledge.
        """
        if not isinstance(registration, Registration):
            raise TypeError("registration must be a Registration")
        if self.status is not CampaignStatus.OPEN:
            raise ValueError("campaign is not open for pledges")
        _require_int("amount", amount, minimum=1)
        _require_int("beat", beat, minimum=0)
        if beat < self.created:
            raise ValueError("pledge beat precedes the campaign's creation")
        if beat > self.deadline:
            raise ValueError("pledge beat is past the campaign deadline")

        subject = registration.subject
        if not self.bank.registry.is_registered(subject):
            raise ValueError("backer is not registered — register before backing")
        if subject in self._backers:
            return None  # one backing per person — no whale stuffing the ballot

        is_local = self._verify_local(subject, beat, proximity)

        pledge = Pledge(backer=subject, amount=amount, beat=beat)
        self._backers.add(subject)
        if is_local:
            self._local_backers.add(subject)
        self._pledges.append(pledge)
        return pledge

    def _verify_local(self, subject: str, beat: int, proximity: Optional[ProximityProof]) -> bool:
        """Validate a Bluetooth proximity proof; return whether the pledge counts as local."""
        if proximity is None:
            return False
        if not isinstance(proximity, ProximityProof):
            raise TypeError("proximity must be a ProximityProof")
        if self.beacon is None:
            raise ValueError("campaign has no beacon — it does not accept local proximity proofs")
        if proximity.backer != subject:
            raise ValueError("proximity proof is for a different backer")
        if proximity.beacon != self.beacon:
            raise ValueError("proximity proof is for a different beacon")
        in_time = abs(proximity.beat - beat) <= self.proximity_window
        return proximity.is_within_range(self.min_rssi_dbm) and in_time

    # -- views ------------------------------------------------------------------------

    def total_raised(self) -> int:
        """Total PLS-wei held in escrow for this campaign."""
        return sum(p.amount for p in self._pledges)

    def backers(self) -> int:
        """Distinct registered people backing the campaign (its breadth)."""
        return len(self._backers)

    def local_backers(self) -> int:
        """Distinct backers who attested Bluetooth presence at the campaign's beacon."""
        return len(self._local_backers)

    def is_goal_met(self) -> bool:
        """True iff the capital, breadth, and local-presence thresholds are all satisfied."""
        return (
            self.total_raised() >= self.goal
            and self.backers() >= self.min_backers
            and self.local_backers() >= self.min_local_backers
        )

    def momentum(self, *, now: int, decay: Optional[Decay] = None) -> int:
        """Recency-weighted backing: recent pledges weigh exponentially more (governance tally).

        Reuses :func:`knitweb.govern.tally.tally` (one-vote-per-subject already holds, since
        backers are unique), so a campaign gaining support *now* reads hotter than a stalled one.
        Advisory only — it does not change the all-or-nothing settlement.
        """
        votes = [Vote(choice="back", subject=p.backer, beat=p.beat) for p in self._pledges]
        return tally(votes, now=now, decay=decay).total_weight

    # -- settlement -------------------------------------------------------------------

    def resolve(self, *, now: int) -> CampaignResult:
        """Settle at/after the deadline: release to beneficiary if met, else refund all. Idempotent."""
        _require_int("now", now, minimum=0)
        if self._result is not None:
            return self._result
        if now < self.deadline:
            raise ValueError("campaign is still open — cannot resolve before the deadline")

        raised = self.total_raised()
        if self.is_goal_met():
            self.status = CampaignStatus.FUNDED
            result = CampaignResult(
                status=CampaignStatus.FUNDED,
                raised=raised,
                backers=self.backers(),
                release_to_beneficiary=raised,
                refunds=(),
            )
        else:
            self.status = CampaignStatus.EXPIRED
            result = CampaignResult(
                status=CampaignStatus.EXPIRED,
                raised=raised,
                backers=self.backers(),
                release_to_beneficiary=0,
                refunds=tuple((p.backer, p.amount) for p in self._pledges),
            )
        self._result = result
        return result

    def to_record(self) -> dict:
        return {
            "kind": "govern-campaign",
            "beneficiary": self.beneficiary,
            "goal": self.goal,
            "deadline": self.deadline,
            "min_backers": self.min_backers,
            "created": self.created,
            "beacon": self.beacon,
            "min_local_backers": self.min_local_backers,
        }

    @property
    def cid(self) -> str:
        """Content-addressed id of the campaign's immutable terms (auditable)."""
        return canonical.cid(self.to_record())
