"""Governance — the VoteBank, its demographic supply, and recency-weighted tallies.

Three cohesive pieces, all integer / hash only (no premine, no floats, no signed-record
changes):

  * :mod:`~knitweb.govern.registry` — registers persons (national identity **or** freedom
    freeport: IMEI + email + ad-hoc proof) per world, and derives the demographic
    ``max_vote_supply`` = registered persons worldwide + this year's expected births.
  * :mod:`~knitweb.govern.votebank` — the :class:`VoteBank` that keeps that vote supply in
    treasury and issues it one-vote-per-person, never past the demographic cap.
  * :mod:`~knitweb.govern.tally` — aggregates cast votes so **more recent votes weigh
    exponentially more**, via an integer geometric decay.
"""

from .registry import (
    Registration,
    RegistrationKind,
    WorldRegistry,
    register_freeport,
    register_national,
)
from .tally import Decay, Vote, WeightedTally, tally
from .votebank import VoteBank, VoteIssuance

__all__ = [
    "Registration",
    "RegistrationKind",
    "WorldRegistry",
    "register_national",
    "register_freeport",
    "VoteBank",
    "VoteIssuance",
    "Decay",
    "Vote",
    "WeightedTally",
    "tally",
]
