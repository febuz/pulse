"""Dispute window — safe settlement timing + slashing around the PoUW verdict.

The challenge protocol (``pouw/challenge.py``) produces a *verdict* that a worker did
the job honestly. But that verdict can arrive late — a verifier re-executes a sample
and may only detect a mismatch some beats after submission. So escrow must **not**
settle immediately, or a fraudulent worker would be paid and gone before the fraud is
caught (the "withdraw-before-dispute" attack in ``docs/PROOF_OF_USEFUL_WORK.md`` §3).

This module is the settlement-timing layer (§4.4). A worker *submits* a proof at an
integer Pulse-**beat** with **collateral** staked alongside the consumer's escrow:

  * ``slashable_until = submit_beat + dispute_window`` — until this beat any verifier
    may file a detected-mismatch ``dispute``, which **slashes** the worker's
    collateral and refunds the escrow to the consumer (fraud is never net-profitable).
  * ``release_beat = submit_beat + release_delay`` — only at/after this beat may the
    escrow ``release`` to the worker (and the collateral return).

The core safety invariant is ``release_delay > dispute_window`` (enforced in the
constructor): the release beat therefore lies strictly *after* the dispute window
closes, so a paid worker can never withdraw inside a window where a dispute could
still land. EigenLayer's "withdrawal delay must exceed the dispute window", in
miniature.

Everything here is integer beats and integer PLS-wei — no floats touch the path. This
layer decides *timing and slashing*; the actual PLS movement is a conservation-
preserving Knit/escrow transfer (``pouw/escrow.py``) the caller drives on the verdict.
Declared-vs-detected fault asymmetry and the k-of-n verifier quorum are the next
increment (``pouw/verifier-quorum``); here a dispute is a single detected mismatch.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

from typing import Iterable

from .collateral import Margin, is_sufficiently_collateralized, payout_at_risk
from .quorum import Outcome, Verdict, tally

__all__ = [
    "DEFAULT_DISPUTE_WINDOW",
    "DEFAULT_RELEASE_DELAY",
    "Submission",
    "DisputeWindowLedger",
    "UnderCollateralizedError",
]


class UnderCollateralizedError(ValueError):
    """Raised when ``enforce_collateral`` is on and a worker's stake can't cover its risk."""

DEFAULT_DISPUTE_WINDOW = 10   # beats a detected-mismatch dispute may still land
DEFAULT_RELEASE_DELAY = 11    # beats until escrow may release (must exceed the window)


def _require_int(name: str, value: int, *, minimum: int = 0) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")


@dataclass
class Submission:
    """A submitted proof awaiting its dispute window, with escrow + staked collateral."""

    sid: str
    worker: str
    consumer: str
    escrow: int           # PLS-wei paid to the worker on a clean release
    collateral: int       # PLS-wei the worker staked; slashed (burned) on detected fraud
    submit_beat: int
    status: str = "pending"          # "pending" | "slashed" | "released"
    resolved_beat: Optional[int] = None


class DisputeWindowLedger:
    """Tracks submissions and enforces dispute-then-release timing + slashing.

    All amounts are integer PLS-wei; all times are integer beats. The ledger records the
    settlement *decisions* and their integer effects (paid / refunded / slashed /
    returned) so a caller can drive the matching Knit transfers, and so the outcome is
    auditable and conservation-checkable.
    """

    def __init__(
        self,
        dispute_window: int = DEFAULT_DISPUTE_WINDOW,
        release_delay: int = DEFAULT_RELEASE_DELAY,
        *,
        enforce_collateral: bool = False,
        margin: Optional[Margin] = None,
    ) -> None:
        _require_int("dispute_window", dispute_window, minimum=1)
        _require_int("release_delay", release_delay, minimum=1)
        if release_delay <= dispute_window:
            raise ValueError(
                "release_delay must strictly exceed dispute_window so escrow can never "
                f"release while a dispute could still land (got release_delay={release_delay}, "
                f"dispute_window={dispute_window})"
            )
        if not isinstance(enforce_collateral, bool):
            raise TypeError("enforce_collateral must be bool")
        if margin is not None and not isinstance(margin, Margin):
            raise TypeError("margin must be a pouw.collateral.Margin")
        self.dispute_window = dispute_window
        self.release_delay = release_delay
        # When on, submit() rejects a worker whose staked collateral can't cover the
        # cumulative escrow at risk across its open windows (pouw/collateral.py invariant).
        self.enforce_collateral = enforce_collateral
        self.margin = margin or Margin(1, 1)
        self._subs: Dict[str, Submission] = {}
        # Audit totals (PLS-wei)
        self.escrow_paid = 0        # released to workers
        self.escrow_refunded = 0    # returned to consumers on a slash
        self.collateral_slashed = 0  # burned on detected fraud
        self.collateral_returned = 0  # returned to workers on a clean release

    # ── Submit ──────────────────────────────────────────────────────────────

    def submit(
        self,
        sid: str,
        worker: str,
        consumer: str,
        escrow: int,
        collateral: int,
        submit_beat: int,
    ) -> Submission:
        """Register a submitted proof; its dispute window opens at ``submit_beat``."""
        if sid in self._subs:
            raise ValueError(f"duplicate submission id: {sid}")
        _require_int("escrow", escrow)
        _require_int("collateral", collateral)
        _require_int("submit_beat", submit_beat)
        if worker == consumer:
            raise ValueError("worker and consumer must differ")
        if self.enforce_collateral:
            # Cumulative: a worker's total stake must cover the total escrow it could
            # collect-then-flee across all its still-open windows (this new one included).
            pending = [s for s in self._subs.values()
                       if s.worker == worker and s.status == "pending"]
            at_risk = payout_at_risk([s.escrow for s in pending] + [escrow])
            total_stake = sum(s.collateral for s in pending) + collateral
            if not is_sufficiently_collateralized(total_stake, at_risk, self.margin):
                raise UnderCollateralizedError(
                    f"worker {worker}: staked {total_stake} cannot cover payout-at-risk "
                    f"{at_risk} at margin {self.margin.num}/{self.margin.den}"
                )
        sub = Submission(
            sid=sid,
            worker=worker,
            consumer=consumer,
            escrow=escrow,
            collateral=collateral,
            submit_beat=submit_beat,
        )
        self._subs[sid] = sub
        return replace(sub)

    # ── Timing ────────────────────────────────────────────────────────────

    def slashable_until(self, sid: str) -> int:
        """Last beat (inclusive) at which a dispute may still slash this submission."""
        return self._sub(sid).submit_beat + self.dispute_window

    def release_beat(self, sid: str) -> int:
        """First beat at which the escrow may release to the worker."""
        return self._sub(sid).submit_beat + self.release_delay

    # ── Dispute (detected mismatch) ───────────────────────────────────────────

    def dispute(self, sid: str, beat: int) -> Tuple[bool, str]:
        """File a detected-mismatch dispute at ``beat``.

        Succeeds only while the submission is pending and ``beat`` is within
        ``[submit_beat, slashable_until]``. On success the worker's collateral is
        slashed (burned) and the escrow refunded to the consumer — fraud earns nothing.
        """
        _require_int("beat", beat)
        sub = self._subs.get(sid)
        if sub is None:
            return False, "unknown submission"
        if sub.status != "pending":
            return False, f"already {sub.status}"
        if beat < sub.submit_beat:
            return False, "dispute precedes submission"
        if beat > sub.submit_beat + self.dispute_window:
            return False, "dispute window closed"
        sub.status = "slashed"
        sub.resolved_beat = beat
        self.collateral_slashed += sub.collateral
        self.escrow_refunded += sub.escrow
        return True, "slashed"

    def dispute_by_quorum(
        self,
        sid: str,
        verdicts: Iterable[Verdict],
        beat: int,
        *,
        worker_declared_fault: bool = False,
        threshold: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """Decide a dispute from a **committee of verifier verdicts**, not a single mismatch.

        Aggregates ``verdicts`` (``pouw/quorum.tally``) and slashes only on a genuine
        ``DETECTED_FAULT`` — a quorum of mismatches against a worker that claimed success —
        reusing :meth:`dispute`'s timing + slashing. A ``CONFIRMED``/``INCONCLUSIVE``/
        ``DECLARED_FAULT`` outcome is **not** a slashable detected fault, so nothing is slashed
        (the submission stays pending for the caller to ``release`` once the window closes, or to
        refund on a declared fault — a separate settlement path). This replaces the trusting
        single-verifier trigger so no lone verifier can slash honest work.
        """
        result = tally(
            verdicts, worker_declared_fault=worker_declared_fault, threshold=threshold
        )
        counts = f"{result.confirms}c/{result.mismatches}m/{result.abstains}a of {result.n}, k={result.threshold}"
        if result.outcome is Outcome.DETECTED_FAULT:
            slashed, reason = self.dispute(sid, beat)
            return slashed, f"quorum detected fault ({counts}): {reason}"
        return False, f"no slash — quorum {result.outcome.value} ({counts})"

    # ── Release (clean settlement) ─────────────────────────────────────────

    def release(self, sid: str, beat: int) -> Tuple[bool, str]:
        """Release escrow to the worker at ``beat``.

        Succeeds only while pending and ``beat >= release_beat`` — which, because
        ``release_delay > dispute_window``, is strictly after the dispute window has
        closed. On success the escrow is paid to the worker and the collateral returned.
        """
        _require_int("beat", beat)
        sub = self._subs.get(sid)
        if sub is None:
            return False, "unknown submission"
        if sub.status != "pending":
            return False, f"already {sub.status}"
        if beat < sub.submit_beat + self.release_delay:
            return False, "within dispute window — escrow still locked"
        sub.status = "released"
        sub.resolved_beat = beat
        self.escrow_paid += sub.escrow
        self.collateral_returned += sub.collateral
        return True, "released"

    # ── Queries ───────────────────────────────────────────────────────────

    def _sub(self, sid: str) -> Submission:
        sub = self._subs.get(sid)
        if sub is None:
            raise KeyError(f"unknown submission: {sid}")
        return sub

    def get(self, sid: str) -> Optional[Submission]:
        sub = self._subs.get(sid)
        return replace(sub) if sub is not None else None

    def pending(self) -> List[Submission]:
        return [replace(s) for s in self._subs.values() if s.status == "pending"]

    def stats(self) -> dict:
        return {
            "dispute_window": self.dispute_window,
            "release_delay": self.release_delay,
            "submissions": len(self._subs),
            "pending": sum(1 for s in self._subs.values() if s.status == "pending"),
            "slashed": sum(1 for s in self._subs.values() if s.status == "slashed"),
            "released": sum(1 for s in self._subs.values() if s.status == "released"),
            "escrow_paid": self.escrow_paid,
            "escrow_refunded": self.escrow_refunded,
            "collateral_slashed": self.collateral_slashed,
            "collateral_returned": self.collateral_returned,
        }
