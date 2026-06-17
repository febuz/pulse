"""
PLS ("pulses") — the pay-token of the knitweb Silk tier.

PLS is earned by participating in the silk layer:
  • Posting a knot that gets confirmed by 3 validators → poster earns PLS
  • Validating a knot that subsequently reaches confirmation → validator earns PLS
  • Validators earn a smaller share than the poster to incentivise content creation

PLS is the single pay-token of the web (see CLAUDE.md): you spend PLS for
activity and earn it via proof-of-useful-work. There is no premine. The ticker
FBR is *reserved* for a possible separate/regional token later and is never the
active token here.

Burn rule: PLS untouched for 90 days is burned, keeping it a pure utility token
with natural circulation pressure (deflation).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ── Constants ──────────────────────────────────────────────────────────────────

PULSE_SCHEMA          = "vpc.pulse/1"
PULSE_POSTER_REWARD    = 5     # micro-PLS per confirmed knot (poster)
PULSE_VALIDATOR_REWARD = 2     # micro-PLS per validator on a confirmed knot
VALIDATORS_REQUIRED   = 3
BURN_AFTER_DAYS       = 90
BURN_AFTER_SECONDS    = BURN_AFTER_DAYS * 24 * 3600
MIN_PULSE_TO_VOTE     = 1     # any balance ≥ 1 µPLS → voting-eligible on silk


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


# ── Wallet ─────────────────────────────────────────────────────────────────────

@dataclass
class PulseWallet:
    """PLS balance for a single fiber (spider)."""

    did: str
    balance: int = 0          # current µPLS
    earned_total: int = 0     # lifetime earned (never decremented)
    burned_total: int = 0     # lifetime burned
    created_at: str = field(default_factory=_now_iso)
    last_activity_at: str = field(default_factory=_now_iso)

    @property
    def voting_eligible(self) -> bool:
        return self.balance >= MIN_PULSE_TO_VOTE

    def earn(self, amount: int) -> None:
        self.balance         += amount
        self.earned_total    += amount
        self.last_activity_at = _now_iso()

    def _burn(self) -> int:
        """Internal: zero balance and record. Returns amount burned."""
        burned          = self.balance
        self.burned_total += burned
        self.balance     = 0
        return burned

    def __repr__(self) -> str:
        return f"PulseWallet(did={self.did[:20]}…, balance={self.balance} µPLS)"


# ── Knot validation tracker ────────────────────────────────────────────────────

@dataclass
class KnotValidationRecord:
    knot_addr: str
    poster_did: str
    validators: List[str] = field(default_factory=list)
    confirmed: bool = False
    confirmed_at: Optional[str] = None
    reward_paid: bool = False


# ── Pulse Ledger ─────────────────────────────────────────────────────────────

class PulseLedger:
    """
    Manages PLS wallets and knot-validation records on the Silk tier.

    The ledger is the single source of truth for:
      - Who has earned PLS
      - Which knots are confirmed
      - Burn sweeps
    """

    def __init__(self) -> None:
        self._wallets: Dict[str, PulseWallet] = {}      # did → wallet
        self._knots:   Dict[str, KnotValidationRecord] = {}   # knot_addr → record
        self._total_burned: int = 0

    # ── Wallet ─────────────────────────────────────────────────────────────────

    def wallet(self, did: str) -> PulseWallet:
        if did not in self._wallets:
            self._wallets[did] = PulseWallet(did=did)
        return self._wallets[did]

    def get_wallet(self, did: str) -> Optional[PulseWallet]:
        return self._wallets.get(did)

    def is_voting_eligible(self, did: str) -> bool:
        w = self._wallets.get(did)
        return bool(w and w.voting_eligible)

    # ── Validation / mining ────────────────────────────────────────────────────

    def validate(
        self,
        knot_addr: str,
        poster_did: str,
        validator_did: str,
    ) -> Tuple[bool, str]:
        """
        Record a spider's validation vote on a knot.

        Returns (success: bool, event: str) where event is one of:
          'validated'  — vote recorded, not yet confirmed
          'confirmed'  — this vote triggered confirmation; PLS minted
          error string — validation rejected
        """
        if validator_did == poster_did:
            return False, "cannot validate your own knot"

        if knot_addr not in self._knots:
            self._knots[knot_addr] = KnotValidationRecord(
                knot_addr=knot_addr,
                poster_did=poster_did,
            )

        rec = self._knots[knot_addr]

        if rec.confirmed:
            return False, "knot already confirmed"
        if validator_did in rec.validators:
            return False, "already validated this knot"

        rec.validators.append(validator_did)

        if len(rec.validators) >= VALIDATORS_REQUIRED:
            rec.confirmed    = True
            rec.confirmed_at = _now_iso()
            rec.reward_paid  = True
            # Mint PLS
            self.wallet(poster_did).earn(PULSE_POSTER_REWARD)
            for v in rec.validators:
                self.wallet(v).earn(PULSE_VALIDATOR_REWARD)
            return True, "confirmed"

        return True, "validated"

    def validation_status(self, knot_addr: str) -> dict:
        rec = self._knots.get(knot_addr)
        if rec is None:
            return {
                "knot_addr": knot_addr,
                "validations": 0,
                "confirmed": False,
                "needed": VALIDATORS_REQUIRED,
            }
        return {
            "knot_addr":    rec.knot_addr,
            "poster_did":   rec.poster_did,
            "validations":  len(rec.validators),
            "validators":   rec.validators,
            "confirmed":    rec.confirmed,
            "confirmed_at": rec.confirmed_at,
            "reward_paid":  rec.reward_paid,
            "needed":       max(0, VALIDATORS_REQUIRED - len(rec.validators)),
        }

    # ── Burn sweep ─────────────────────────────────────────────────────────────

    def run_burn(self) -> dict:
        """
        Burn PLS in wallets inactive for ≥ BURN_AFTER_DAYS.
        Returns a summary dict.
        """
        cutoff = _now() - datetime.timedelta(seconds=BURN_AFTER_SECONDS)
        wallets_affected = 0
        pls_burned       = 0

        for w in self._wallets.values():
            if w.balance > 0:
                last = datetime.datetime.fromisoformat(w.last_activity_at)
                if last < cutoff:
                    pls_burned       += w._burn()
                    wallets_affected += 1

        self._total_burned += pls_burned
        return {
            "wallets_affected": wallets_affected,
            "pls_burned": pls_burned,
            "total_burned_all_time": self._total_burned,
        }

    # ── Stats ──────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        circulating = sum(w.balance for w in self._wallets.values())
        earned_all  = sum(w.earned_total for w in self._wallets.values())
        return {
            "schema": PULSE_SCHEMA,
            "token": "PLS",
            "wallets": len(self._wallets),
            "circulating_micro_pls": circulating,
            "earned_all_time": earned_all,
            "burned_all_time": self._total_burned,
            "validators_required": VALIDATORS_REQUIRED,
            "poster_reward": PULSE_POSTER_REWARD,
            "validator_reward": PULSE_VALIDATOR_REWARD,
            "burn_after_days": BURN_AFTER_DAYS,
        }

    def leaderboard(self, top: int = 50) -> list[dict]:
        return [
            {
                "did": w.did,
                "balance": w.balance,
                "earned_total": w.earned_total,
                "voting_eligible": w.voting_eligible,
            }
            for w in sorted(self._wallets.values(), key=lambda x: -x.balance)[:top]
        ]
