"""
Risk-Knot staking — PLS locked on uncertain knowledge claims (Python layer).

A risk knot represents an uncertain claim.  Fibers stake PLS on YES or NO.
Stakes are locked (not burned) while the knot is open.

Resolution fires when:
  - votes ≥ RISK_VOTE_THRESHOLD, AND
  - one side holds ≥ RISK_CONSENSUS_FRACTION of the vote count

Lock levels:
  L1 =    5 µPLS
  L2 =   50 µPLS
  L3 =  500 µPLS
"""

from __future__ import annotations

import datetime
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Tuple

from .pulse import PulseLedger, VALIDATORS_REQUIRED

# ── Constants ──────────────────────────────────────────────────────────────────

RISK_SCHEMA            = "vpc.risk-knot/1"

LOCK_LEVELS: Dict[int, int] = {
    1:   5,    # µPLS
    2:  50,
    3: 500,
}

RISK_VOTE_THRESHOLD    = 5
RISK_CONSENSUS_FRACTION = 2 / 3
BURN_FRACTION          = 0.10

Position = Literal["yes", "no"]


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _risk_id(knot_addr: str, opened_at: str) -> str:
    return hashlib.sha256(f"{knot_addr}:{opened_at}".encode()).hexdigest()


@dataclass
class RiskStake:
    staker_did: str
    position: Position
    amount: int
    level: int
    staked_at: str = field(default_factory=_now_iso)


@dataclass
class RiskVote:
    voter_did: str
    position: Position
    ts: str = field(default_factory=_now_iso)


@dataclass
class RiskKnot:
    id: str
    knot_addr: str
    question: str
    opened_by: str
    opened_at: str
    status: str = "open"
    outcome: Optional[Position] = None
    closed_at: Optional[str] = None
    multiplier: Optional[float] = None
    yes_pool: int = 0
    no_pool: int = 0
    stakes: List[RiskStake] = field(default_factory=list)
    votes: List[RiskVote] = field(default_factory=list)


class RiskKnotLedger:
    """
    Manages risk-knot staking on the PLS (silk) layer.
    """

    def __init__(self, ledger: PulseLedger) -> None:
        self._ledger = ledger
        self._knots: Dict[str, RiskKnot] = {}
        self._locks: Dict[Tuple[str, str], int] = {}

    def open(
        self,
        knot_addr: str,
        question: str,
        opened_by: str,
        level: int = 1,
    ) -> Tuple[bool, str]:
        if not question.strip():
            return False, "question is required"
        if len(question) > 280:
            return False, "question exceeds 280 chars"
        if level not in LOCK_LEVELS:
            return False, "level must be 1, 2 or 3"

        amount = LOCK_LEVELS[level]
        w = self._ledger.wallet(opened_by)
        if w.balance < amount:
            return False, f"insufficient balance — L{level} requires {amount} µPLS"

        opened_at = _now_iso()
        rid = _risk_id(knot_addr, opened_at)

        w.balance -= amount
        self._locks[(opened_by, rid)] = self._locks.get((opened_by, rid), 0) + amount

        rk = RiskKnot(
            id=rid,
            knot_addr=knot_addr,
            question=question.strip()[:280],
            opened_by=opened_by,
            opened_at=opened_at,
            yes_pool=amount,
        )
        rk.stakes.append(RiskStake(opened_by, "yes", amount, level))
        self._knots[rid] = rk
        return True, rid

    def stake(
        self,
        risk_id: str,
        staker_did: str,
        position: Position,
        level: int = 1,
    ) -> Tuple[bool, str]:
        rk = self._knots.get(risk_id)
        if rk is None:
            return False, "risk knot not found"
        if rk.status != "open":
            return False, "risk knot already resolved"
        if level not in LOCK_LEVELS:
            return False, "level must be 1, 2 or 3"

        amount = LOCK_LEVELS[level]
        w = self._ledger.wallet(staker_did)
        if w.balance < amount:
            return False, f"insufficient balance — L{level} requires {amount} µPLS"

        w.balance -= amount
        self._locks[(staker_did, risk_id)] = \
            self._locks.get((staker_did, risk_id), 0) + amount

        rk.stakes.append(RiskStake(staker_did, position, amount, level))
        if position == "yes":
            rk.yes_pool += amount
        else:
            rk.no_pool += amount

        return True, f"{amount} µPLS locked on {position}"

    def vote(
        self,
        risk_id: str,
        voter_did: str,
        position: Position,
    ) -> Tuple[bool, str]:
        rk = self._knots.get(risk_id)
        if rk is None:
            return False, "risk knot not found"
        if rk.status != "open":
            return False, "risk knot already resolved"
        if any(v.voter_did == voter_did for v in rk.votes):
            return False, "already voted on this risk knot"

        rk.votes.append(RiskVote(voter_did, position))

        total = len(rk.votes)
        if total >= RISK_VOTE_THRESHOLD:
            yes_count = sum(1 for v in rk.votes if v.position == "yes")
            no_count  = total - yes_count
            y_frac = yes_count / total
            n_frac = no_count  / total

            if y_frac >= RISK_CONSENSUS_FRACTION or n_frac >= RISK_CONSENSUS_FRACTION:
                outcome: Position = "yes" if y_frac >= RISK_CONSENSUS_FRACTION else "no"
                self._resolve(rk, outcome, total)
                return True, f"resolved:{outcome}"

        return True, "voted"

    def _resolve(self, rk: RiskKnot, outcome: Position, vote_count: int) -> None:
        rk.status    = "resolved"
        rk.outcome   = outcome
        rk.closed_at = _now_iso()

        multiplier = max(1.0, vote_count / VALIDATORS_REQUIRED)
        rk.multiplier = multiplier

        losing_pos: Position = "no" if outcome == "yes" else "yes"
        losing_pool = rk.no_pool if outcome == "yes" else rk.yes_pool
        winning_pool = rk.yes_pool if outcome == "yes" else rk.no_pool

        burn_amt   = int(losing_pool * BURN_FRACTION)
        distribute = losing_pool - burn_amt

        for s in (st for st in rk.stakes if st.position == losing_pos):
            lock_key = (s.staker_did, rk.id)
            held = self._locks.get(lock_key, 0)
            release = min(s.amount, held)
            self._locks[lock_key] = held - release
            w = self._ledger.wallet(s.staker_did)
            w.burned_total += release

        for s in (st for st in rk.stakes if st.position == outcome):
            lock_key = (s.staker_did, rk.id)
            held = self._locks.get(lock_key, 0)
            release = min(s.amount, held)
            self._locks[lock_key] = held - release

            w = self._ledger.wallet(s.staker_did)
            w.balance += release
            bonus = int(s.amount * (multiplier - 1))
            if bonus > 0:
                w.balance      += bonus
                w.earned_total += bonus
            if winning_pool > 0:
                share = int(distribute * (s.amount / winning_pool))
                if share > 0:
                    w.balance      += share
                    w.earned_total += share

        for s in rk.stakes:
            self._ledger.wallet(s.staker_did).last_activity_at = rk.closed_at

    def get(self, risk_id: str) -> Optional[RiskKnot]:
        return self._knots.get(risk_id)

    def list(self, status: Optional[str] = None) -> List[RiskKnot]:
        knots = list(self._knots.values())
        if status:
            knots = [k for k in knots if k.status == status]
        return knots

    def locked_by(self, did: str) -> int:
        return sum(v for (d, _), v in self._locks.items() if d == did)

    def stats(self) -> dict:
        open_count     = sum(1 for k in self._knots.values() if k.status == "open")
        resolved_count = len(self._knots) - open_count
        total_staked   = sum(k.yes_pool + k.no_pool for k in self._knots.values())
        return {
            "schema":         RISK_SCHEMA,
            "open":           open_count,
            "resolved":       resolved_count,
            "total_staked_micro_pls": total_staked,
            "lock_levels":    LOCK_LEVELS,
            "vote_threshold": RISK_VOTE_THRESHOLD,
            "consensus_frac": RISK_CONSENSUS_FRACTION,
            "burn_fraction":  BURN_FRACTION,
        }
