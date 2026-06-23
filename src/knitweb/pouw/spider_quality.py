"""Spider work-quality reputation — relevance / selection-quality scoring.

Separate from :mod:`knitweb.p2p.reputation` (which is a WIRE-LEVEL peer DoS
ban-score) and from :mod:`knitweb.pouw.collateral` (which sizes economic
slash-at-risk for detected fabrication).

This module tracks the *selection quality* of a spider's distill output over
time: how often its relevance challenges are upheld (bad quality) vs. overturned
(good quality).  The score is advisory — economic consequences (e.g. tier
demotion, reduced job assignment priority) are left to the caller.

All arithmetic is exact integer; no floats, no wall-clock, no canonical-hash
surface (advisory only — never part of a signed record).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

__all__ = [
    "DEFAULT_QUALITY_PENALTY",
    "DEFAULT_QUALITY_REWARD",
    "MIN_QUALITY_SCORE",
    "SpiderQualityRecord",
    "SpiderQualityReputation",
]

#: Points deducted from a spider's quality score when a relevance challenge
#: is upheld (the spider delivered irrelevant results).
DEFAULT_QUALITY_PENALTY: int = 10

#: Points added to a spider's quality score when a relevance challenge is
#: overturned (the spider's result was vindicated by the committee).
DEFAULT_QUALITY_REWARD: int = 2

#: Quality score floor — a spider's score never goes below this value.
MIN_QUALITY_SCORE: int = 0


def _require_positive_int(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int")
    if value <= 0:
        raise ValueError(f"{name} must be positive (got {value})")


def _require_str(name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty str")


@dataclass
class SpiderQualityRecord:
    """Running quality totals for one spider."""

    spider_id: str
    score: int = 100          # starts at 100 (neutral); clamped to MIN_QUALITY_SCORE
    challenges_upheld: int = 0
    challenges_overturned: int = 0

    def penalize(self, points: int = DEFAULT_QUALITY_PENALTY) -> int:
        """Deduct ``points`` from the quality score (floor at MIN_QUALITY_SCORE).

        Returns the new score.
        """
        _require_positive_int("points", points)
        self.score = max(MIN_QUALITY_SCORE, self.score - points)
        self.challenges_upheld += 1
        return self.score

    def reward(self, points: int = DEFAULT_QUALITY_REWARD) -> int:
        """Add ``points`` to the quality score.  No ceiling.

        Returns the new score.
        """
        _require_positive_int("points", points)
        self.score += points
        self.challenges_overturned += 1
        return self.score


class SpiderQualityReputation:
    """Per-spider work-quality reputation store.

    Acts as a write-through dict of :class:`SpiderQualityRecord` objects.
    Thread-safety: not provided — single-process / single-threaded use.

    This is intentionally separate from the wire-level DoS ban-score
    (``p2p.reputation.PeerReputation``) and from the economic collateral
    sizing (``pouw.collateral``).  Relevance penalties live here; fabrication
    slashing lives in ``pouw.dispute.DisputeWindowLedger``.
    """

    def __init__(
        self,
        *,
        penalty: int = DEFAULT_QUALITY_PENALTY,
        reward: int = DEFAULT_QUALITY_REWARD,
    ) -> None:
        _require_positive_int("penalty", penalty)
        _require_positive_int("reward", reward)
        self._penalty = penalty
        self._reward = reward
        self._records: Dict[str, SpiderQualityRecord] = {}

    def _get_or_create(self, spider_id: str) -> SpiderQualityRecord:
        _require_str("spider_id", spider_id)
        if spider_id not in self._records:
            self._records[spider_id] = SpiderQualityRecord(spider_id=spider_id)
        return self._records[spider_id]

    def penalize(self, spider_id: str, points: int | None = None) -> int:
        """Apply a relevance-challenge-upheld penalty.  Returns new score."""
        return self._get_or_create(spider_id).penalize(
            points if points is not None else self._penalty
        )

    def reward(self, spider_id: str, points: int | None = None) -> int:
        """Apply a relevance-challenge-overturned reward.  Returns new score."""
        return self._get_or_create(spider_id).reward(
            points if points is not None else self._reward
        )

    def score(self, spider_id: str) -> int:
        """Quality score for ``spider_id`` (100 if never seen)."""
        _require_str("spider_id", spider_id)
        rec = self._records.get(spider_id)
        return rec.score if rec is not None else 100

    def record(self, spider_id: str) -> SpiderQualityRecord | None:
        """Full record for ``spider_id``, or None if never seen."""
        return self._records.get(spider_id)

    def tracked(self) -> int:
        """Number of spiders with a quality record."""
        return len(self._records)
