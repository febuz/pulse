"""Peer reputation — accumulate misbehavior and evict provably-bad peers.

The web *detects* misbehavior — a malformed wire frame, a feed conflict
(``fabric/feed.check_conflict``), a stale/forged inclusion proof, a proven equivocation
(``fabric/equivocation``) — but detection without a **consequence** is toothless: a malicious
peer could misbehave forever. This is the missing consequence layer: the standard DoS ban-score
accounting every production P2P stack carries (Bitcoin Core's ``nMisbehavior`` / ``Misbehaving``,
libp2p's connection gating). Each peer holds an integer misbehavior score; provable offenses add
points; at or above a threshold the peer is **banned** and should be disconnected and refused.

Offense weights are graded by how *objective* and *severe* the offense is. An
:class:`Offense.EQUIVOCATION` or :class:`Offense.FEED_CONFLICT` is cryptographically provable and
unambiguously malicious, so it carries a full-threshold penalty — a one-shot ban. A merely
malformed frame is cheap noise and barely moves the needle.

Determinism is the point: **no wall-clock and no randomness**. Decay (rehabilitation over time) is
driven by explicit ``decay`` calls a caller makes per Pulse epoch, so two honest nodes that
observe the same offense stream reach the *same* ban verdict — reputation is reproducible, not
node-local guesswork. Pure integer policy; it touches no canonical/hash path and no signed record.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Union

__all__ = [
    "Offense",
    "DEFAULT_BAN_THRESHOLD",
    "DEFAULT_REPUTATION_DECAY_PER_ROUND",
    "PeerReputation",
]

DEFAULT_BAN_THRESHOLD = 100

#: Misbehavior points bled off every tracked peer once per maintenance epoch (one
#: anti-entropy round). This is the *rehabilitation* rate the class docstring promises:
#: a transiently-noisy honest peer heals over time, while a peer that offends faster than
#: it decays still bans. Kept small so it can never outpace a real offense stream — a
#: sustained attacker still crosses the threshold; ``0`` disables decay. Integer, no clock.
DEFAULT_REPUTATION_DECAY_PER_ROUND = 1


class Offense(Enum):
    """Provable peer offenses and their default misbehavior points (out of a 100 ban score)."""

    MALFORMED_FRAME = 10        # undecodable / non-canonical wire frame — cheap noise
    OVERSIZED_FRAME = 20        # frame exceeds the wire size cap
    UNSOLICITED_MESSAGE = 20    # a response to nothing / protocol violation
    INVALID_SIGNATURE = 50      # a signature that does not verify
    STALE_OR_FORGED_PROOF = 50  # an inclusion/range proof that fails against the signed head
    FEED_CONFLICT = 100         # two signed heads that conflict — provable, instant ban
    EQUIVOCATION = 100          # a verified equivocation report — provable, instant ban


def _require_int(name: str, value: int, *, minimum: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int, not {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum} (got {value})")


def _require_peer(peer: str) -> None:
    if not isinstance(peer, str) or not peer:
        raise TypeError("peer must be a non-empty str")


class PeerReputation:
    """Tracks per-peer misbehavior scores and bans peers at/above the threshold.

    All scores are integers; a peer never seen has score 0 (and is not banned). ``ban_threshold``
    is the score at which a peer becomes banned. Bans are score-based (not sticky): explicit
    :meth:`decay` can rehabilitate a peer whose score falls back below the threshold.
    """

    def __init__(self, ban_threshold: int = DEFAULT_BAN_THRESHOLD) -> None:
        _require_int("ban_threshold", ban_threshold, minimum=1)
        self.ban_threshold = ban_threshold
        self._score: Dict[str, int] = {}

    # ── Mutations ────────────────────────────────────────────────────────────

    def penalize(self, peer: str, offense: Union[Offense, int]) -> bool:
        """Add misbehavior points for ``peer``; returns whether the peer is now banned.

        ``offense`` is an :class:`Offense` (uses its weight) or an explicit positive int of points.
        """
        _require_peer(peer)
        if isinstance(offense, Offense):
            points = offense.value
        else:
            _require_int("points", offense, minimum=1)
            points = offense
        self._score[peer] = self._score.get(peer, 0) + points
        return self.is_banned(peer)

    def decay(self, peer: str, points: int) -> None:
        """Reduce ``peer``'s score by ``points`` (floored at 0) — rehabilitation over time."""
        _require_peer(peer)
        _require_int("points", points, minimum=0)
        if peer in self._score:
            self._score[peer] = max(0, self._score[peer] - points)

    def decay_all(self, points: int) -> None:
        """Decay every tracked peer by ``points`` — call once per Pulse epoch for time-decay."""
        _require_int("points", points, minimum=0)
        for peer in list(self._score):
            self._score[peer] = max(0, self._score[peer] - points)

    def forgive(self, peer: str) -> None:
        """Clear a peer's score entirely (e.g. an operator override)."""
        _require_peer(peer)
        self._score.pop(peer, None)

    # ── Queries ──────────────────────────────────────────────────────────────

    def score(self, peer: str) -> int:
        """The peer's current misbehavior score (0 if never seen)."""
        _require_peer(peer)
        return self._score.get(peer, 0)

    def is_banned(self, peer: str) -> bool:
        """True iff the peer's score is at or above the ban threshold."""
        return self.score(peer) >= self.ban_threshold

    def banned(self) -> List[str]:
        """All currently-banned peers, sorted (deterministic ordering)."""
        return sorted(p for p, s in self._score.items() if s >= self.ban_threshold)

    def tracked(self) -> int:
        """How many peers have a non-default (non-zero, ever-set) score record."""
        return len(self._score)
