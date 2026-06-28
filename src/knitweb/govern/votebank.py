"""The VoteBank — keeps the vote supply in treasury and issues it one-per-person.

Mirrors the native-PLS :class:`~knitweb.token.mint.Treasury` discipline, but for governance
votes instead of money:

  * **No premine.** A fresh :class:`VoteBank` has issued nothing; the whole supply sits in
    the bank's *treasury*. Votes come into a person's hands only by registering first.
  * **Demographically bounded.** The bank can never issue more votes than the
    :class:`~knitweb.govern.registry.WorldRegistry`'s :meth:`max_vote_supply` — registered
    persons worldwide (national **and** freeport) plus this year's expected births. So the
    treasury is sized by real demographics, not by fiat.
  * **One vote per person.** A given ``subject`` (the registry's worldwide dedup key) draws
    its single vote at most once; a second draw is rejected (no double-issue).
  * **Conserved + auditable.** Every draw is a content-addressed :class:`VoteIssuance`, so the
    set of issued votes is replay-detectable and the treasury balance is exact:
    ``treasury_remaining = max_vote_supply - issued``.

The votes this bank issues are then cast and aggregated by
:mod:`knitweb.govern.tally` (where more recent votes weigh exponentially more). All integer /
hash only; no floats, no canonical-encoding changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Set

from ..core import canonical
from .registry import Registration, WorldRegistry

__all__ = ["VoteIssuance", "VoteBank"]


@dataclass(frozen=True)
class VoteIssuance:
    """An auditable record of one vote drawn from the bank's treasury to a person."""

    subject: str       # the registry dedup key of the person who received the vote
    world: str
    beat: int          # Pulse beat at which the vote was drawn
    supply_at_issue: int  # max_vote_supply when this vote was drawn (audit context)

    def to_record(self) -> dict:
        return {
            "kind": "govern-vote-issuance",
            "subject": self.subject,
            "world": self.world,
            "beat": self.beat,
            "supply_at_issue": self.supply_at_issue,
        }

    @property
    def cid(self) -> str:
        return canonical.cid(self.to_record())


class VoteBank:
    """Issuer of governance votes, bounded by a world registry's demographic cap.

    There is intentionally **no** raw, ungated way to create a vote: the only way one enters
    circulation is :meth:`issue`, which checks the person is registered, has not already drawn
    their vote, and that the demographic supply is not exhausted.
    """

    def __init__(self, registry: WorldRegistry) -> None:
        if not isinstance(registry, WorldRegistry):
            raise TypeError("registry must be a WorldRegistry")
        self.registry = registry
        self.issued = 0
        self.issuances: List[VoteIssuance] = []
        self._issued_subjects: Set[str] = set()  # one vote per person (anti-replay)

    def has_issued(self, subject: str) -> bool:
        return subject in self._issued_subjects

    def treasury_remaining(self) -> int:
        """Votes still held in the bank (the demographic cap minus what's been issued)."""
        return self.registry.max_vote_supply() - self.issued

    def issue(self, registration: Registration, *, beat: int) -> VoteIssuance | None:
        """Draw this person's single vote from the treasury. None if already drawn / capped.

        1. The person must be **registered** in the bound registry (national or freeport).
        2. **One vote per person**: a subject that already drew its vote ⇒ None (no double).
        3. **Demographic bound**: never issue past ``max_vote_supply`` ⇒ None when exhausted.
        """
        if not isinstance(registration, Registration):
            raise TypeError("registration must be a Registration")
        if not isinstance(beat, int) or isinstance(beat, bool):
            raise TypeError("beat must be int")
        if beat < 0:
            raise ValueError("beat must be non-negative")

        subject = registration.subject
        if not self.registry.is_registered(subject):
            raise ValueError("subject is not registered — register before issuing a vote")
        if subject in self._issued_subjects:
            return None  # this person already holds their one vote — no double-issue
        if self.issued >= self.registry.max_vote_supply():
            return None  # demographic supply exhausted — nothing left in the treasury

        issuance = VoteIssuance(
            subject=subject,
            world=registration.world,
            beat=beat,
            supply_at_issue=self.registry.max_vote_supply(),
        )
        self._issued_subjects.add(subject)
        self.issued += 1
        self.issuances.append(issuance)
        return issuance
