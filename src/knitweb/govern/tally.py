"""Recency-weighted vote tally — recent votes weigh exponentially more.

The governance principle: *when agents vote, more recent votes weigh exponentially more
than older ones.* A vote cast long ago reflects a stale belief; the collective's current
will is dominated by its freshest signals. So a vote's weight decays exponentially with its
**age** (how many Pulse beats before the tally instant ``now`` it was cast).

Doing this **float-free** (the project bans floats anywhere near canonical/value math) uses
an integer *compound decay*: a configurable ratio ``num/den < 1`` applied once per beat of
age, starting from a fixed-point ``scale``::

    weight(age) = scale;   repeat age times:   weight = weight * num // den

This is exact integer arithmetic, deterministic across nodes, and monotonically
non-increasing in age — a vote one beat older is worth ``num/den`` of a vote, two beats
``(num/den)²``, i.e. a true geometric (exponential) decay. Beyond an optional ``horizon`` the
weight is floored to 0 (very old votes stop counting), which also bounds the work.

The tally enforces **one vote per subject** (one person, one vote) and returns the
exponentially-weighted sum per choice plus the deterministic winner. It is pure / advisory:
it only counts votes some upstream produced (e.g. drawn from
:class:`~knitweb.govern.votebank.VoteBank`), and changes no signed record.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

__all__ = ["Decay", "Vote", "WeightedTally", "tally"]

# Fixed-point unit for an age-0 (just-cast) vote. Large enough that several beats of
# decay still leave meaningful integer resolution before flooring to 0.
DEFAULT_SCALE = 1 << 20


def _require_int(name: str, value: int, *, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int, not {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum} (got {value})")
    return value


@dataclass(frozen=True)
class Decay:
    """An integer geometric decay applied per beat of a vote's age.

    ``num/den`` is the per-beat retention factor (strict decay needs ``0 <= num < den``).
    ``scale`` is the weight of a just-cast vote. ``horizon`` (beats) is an optional hard
    cut-off past which a vote weighs 0; ``None`` means decay forever (until it floors to 0).
    The default halves a vote's weight every beat (``num/den = 1/2``).
    """

    num: int = 1
    den: int = 2
    scale: int = DEFAULT_SCALE
    horizon: Optional[int] = None

    def __post_init__(self) -> None:
        _require_int("num", self.num, minimum=0)
        _require_int("den", self.den, minimum=1)
        _require_int("scale", self.scale, minimum=1)
        if self.num >= self.den:
            raise ValueError("decay must shrink: require num < den")
        if self.horizon is not None:
            _require_int("horizon", self.horizon, minimum=0)

    def weight(self, age: int) -> int:
        """Exponentially-decayed integer weight of a vote ``age`` beats old."""
        _require_int("age", age, minimum=0)
        if self.horizon is not None and age > self.horizon:
            return 0
        w = self.scale
        for _ in range(age):
            w = w * self.num // self.den
            if w == 0:
                break
        return w


@dataclass(frozen=True)
class Vote:
    """One agent's vote: a ``choice``, the ``subject`` who cast it, and the ``beat`` cast."""

    choice: str
    subject: str
    beat: int

    def __post_init__(self) -> None:
        if not isinstance(self.choice, str) or not self.choice:
            raise TypeError("choice must be a non-empty str")
        if not isinstance(self.subject, str) or not self.subject:
            raise TypeError("subject must be a non-empty str")
        _require_int("beat", self.beat, minimum=0)


@dataclass(frozen=True)
class WeightedTally:
    """The exponentially-weighted outcome (all integer weights)."""

    weights: Dict[str, int]   # choice -> summed recency-weighted weight
    winner: Optional[str]     # highest-weight choice; None iff no vote carried weight
    total_weight: int
    n: int                    # number of (unique-subject) votes counted

    def margin(self) -> int:
        """Weight gap between the winner and the runner-up (0 if < 2 weighted choices)."""
        if self.winner is None:
            return 0
        ordered = sorted(self.weights.values(), reverse=True)
        return ordered[0] - (ordered[1] if len(ordered) > 1 else 0)


def tally(votes: Iterable[Vote], *, now: int, decay: Optional[Decay] = None) -> WeightedTally:
    """Aggregate ``votes`` at tally instant ``now``, weighting recent votes exponentially.

    Each vote's weight is ``decay.weight(now - beat_cast)`` — so a vote cast at ``now`` carries
    full weight and older votes shrink geometrically. Enforces **one vote per subject** (a
    duplicate subject is a double-vote and is rejected). A vote with ``beat > now`` (cast in the
    future) is rejected. The winner is the highest summed weight; ties break to the
    lexicographically smallest choice so the result is deterministic across nodes.
    """
    decay = decay or Decay()
    _require_int("now", now, minimum=0)

    weights: Dict[str, int] = {}
    seen_subjects: set[str] = set()
    n = 0
    for v in votes:
        if not isinstance(v, Vote):
            raise TypeError(f"each vote must be a Vote, got {type(v).__name__}")
        if v.beat > now:
            raise ValueError(f"vote beat {v.beat} is in the future relative to now {now}")
        if v.subject in seen_subjects:
            raise ValueError(f"subject {v.subject} voted more than once (one vote per person)")
        seen_subjects.add(v.subject)
        n += 1
        w = decay.weight(now - v.beat)
        if w:
            weights[v.choice] = weights.get(v.choice, 0) + w

    if weights:
        # Highest weight wins; ties resolve to the smallest choice for determinism.
        winner = min((c for c in weights if weights[c] == max(weights.values())))
        total = sum(weights.values())
    else:
        winner = None
        total = 0

    return WeightedTally(weights=weights, winner=winner, total_weight=total, n=n)
