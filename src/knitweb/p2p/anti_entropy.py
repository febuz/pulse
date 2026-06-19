"""Self-healing anti-entropy — a churn-resilient convergence driver.

A live web is never static: peers crash, restart, partition, and rejoin. The
node stacks already expose every primitive needed to *recover* from that churn —
:meth:`AsyncioP2PNode.bootstrap_peers` re-grows the peer directory,
:meth:`AsyncioP2PNode.sync_feed` re-pulls a signed feed, and
:meth:`FabricNode.sync_from` re-pulls Web state — but nothing *drives* them on a
loop. Left alone, two peers that drift apart after a disconnect stay drifted.
Anti-entropy is the missing convergence engine: it periodically re-bootstraps and
re-syncs so a peer that fell out of the web climbs back in and the component
re-converges, the same background gossip/reconciliation loop every production P2P
stack carries (Cassandra/Dynamo anti-entropy, Bitcoin's reconnect loop).

This module is the **transport-free, socket-free core** of that loop, so the
convergence behaviour is provable without a real socket and so it can drive
*either* node stack without editing ``node.py``:

  * A :class:`SyncRound` is an injected callback — a coroutine that performs one
    reconciliation attempt (bootstrap a peer, sync a feed, pull Web state) and
    returns the integer amount of *progress* it made (peers learned, entries
    pulled). The driver knows nothing about sockets, feeds, or carriers; it only
    schedules rounds and reacts to their success/failure.

  * :class:`Backoff` is **integer attempt-count based** — no wall-clock and no
    randomness anywhere on the hashed/state path. A failed round increases the
    attempt count and the next delay grows as ``base * 2**attempt`` capped at
    ``ceiling``; a round that makes progress resets the count to ``0``. The delay
    is a deterministic function of the integer attempt count alone, so two peers
    replaying the same success/failure sequence schedule identically.

  * Sleeping is injected (``sleep`` is an ``async`` callback taking an integer
    delay), so tests advance a virtual clock and drive thousands of rounds
    deterministically with no real time elapsed.

It touches no signed record, no reputation gate, and no hash path: a fresh Knit's
CID is identical whether or not a node is being healed. It is a reusable
primitive that deliberately knows nothing about the fabric or the node stacks, so
either node layer can adopt it independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, List, Sequence

__all__ = [
    "Backoff",
    "BackoffState",
    "SyncRound",
    "RoundResult",
    "AntiEntropy",
]

# A SyncRound performs one reconciliation attempt and returns an integer
# "progress" amount (>= 0): peers learned, feed entries pulled, etc. A raised
# exception (an unreachable peer, a refused frame) is treated as a failed round
# and triggers backoff — exactly the disconnect anti-entropy exists to heal.
SyncRound = Callable[[], Awaitable[int]]

# An injected sleep: ``await sleep(delay)`` where ``delay`` is an integer number
# of abstract ticks. Real deployments pass a seconds-based asyncio sleep; tests
# pass a virtual-clock advance so convergence is provable with no real time.
Sleep = Callable[[int], Awaitable[None]]


@dataclass(frozen=True)
class Backoff:
    """Deterministic integer exponential backoff schedule.

    The delay before retry ``attempt`` (0-based) is ``base * 2**attempt`` clamped
    to ``ceiling``. Everything is integer arithmetic on the integer attempt count:
    no wall-clock, no randomness, no float. ``attempt == 0`` yields ``base`` (the
    steady-state re-sync interval when a peer is healthy), and the cap bounds the
    longest a recovering peer ever waits between retries.
    """

    base: int = 1
    ceiling: int = 64

    def __post_init__(self) -> None:
        if not isinstance(self.base, int) or isinstance(self.base, bool):
            raise TypeError("base must be int")
        if not isinstance(self.ceiling, int) or isinstance(self.ceiling, bool):
            raise TypeError("ceiling must be int")
        if self.base < 1:
            raise ValueError("base must be >= 1")
        if self.ceiling < self.base:
            raise ValueError("ceiling must be >= base")

    def delay(self, attempt: int) -> int:
        """Return the integer delay before the given 0-based retry ``attempt``."""
        if not isinstance(attempt, int) or isinstance(attempt, bool):
            raise TypeError("attempt must be int")
        if attempt < 0:
            raise ValueError("attempt must be >= 0")
        # Shift caps out quickly; compare against ceiling without overflowing into
        # an astronomically large intermediate by bounding the exponent.
        if attempt >= self.ceiling.bit_length():
            return self.ceiling
        scaled = self.base << attempt
        return scaled if scaled < self.ceiling else self.ceiling


@dataclass
class BackoffState:
    """Mutable per-round attempt counter driving a :class:`Backoff`.

    A failed round advances the attempt count (longer next delay); a round that
    makes progress resets it to ``0`` (back to the steady-state interval). The
    counter is a plain integer — the whole schedule is a pure function of it.
    """

    backoff: Backoff
    attempt: int = 0

    def next_delay(self) -> int:
        """Delay to wait *before* the upcoming round, given the current count."""
        return self.backoff.delay(self.attempt)

    def record_progress(self) -> None:
        """A round converged: reset to the steady-state interval."""
        self.attempt = 0

    def record_failure(self) -> None:
        """A round stalled or errored: lengthen the next delay (capped)."""
        # Stop climbing once the cap is reached so the counter stays bounded.
        if self.backoff.delay(self.attempt) < self.backoff.ceiling:
            self.attempt += 1


@dataclass(frozen=True)
class RoundResult:
    """Outcome of one driven reconciliation round.

    ``progress`` is the integer amount of state pulled (``0`` when the round ran
    but converged nothing). ``ok`` is ``False`` only when the round *raised*
    (an unreachable/refusing peer) — a healthy round that simply found nothing new
    is ``ok=True, progress=0``. ``delay`` is the integer delay the driver waited
    *before* this round, and ``attempt`` the backoff count it was scheduled at, so
    a test (or operator log) can replay the exact schedule.
    """

    index: int
    attempt: int
    delay: int
    progress: int
    ok: bool


class AntiEntropy:
    """A churn-resilient convergence loop over injected reconciliation rounds.

    Construct with a sequence of :data:`SyncRound` callbacks (e.g. one bound to
    ``node.bootstrap_peers``, one per feed bound to ``node.sync_feed``, one bound
    to ``fabric.sync_from``) and an injected :data:`Sleep`. Each *cycle* runs every
    round once; a cycle that makes any progress resets the backoff, a fully-stalled
    cycle lengthens it. The driver is deterministic: given the same round
    outcomes it schedules the same delays, so two peers heal identically.

    The driver never touches sockets, signed bytes, reputation, or any hash path —
    it is pure scheduling over opaque callbacks.
    """

    def __init__(
        self,
        rounds: Sequence[SyncRound],
        *,
        sleep: Sleep,
        backoff: Backoff | None = None,
    ) -> None:
        if not rounds:
            raise ValueError("anti-entropy needs at least one sync round")
        self._rounds: List[SyncRound] = list(rounds)
        self._sleep = sleep
        self._state = BackoffState(backoff or Backoff())
        self.history: List[RoundResult] = []
        self._index = 0

    @property
    def attempt(self) -> int:
        """Current backoff attempt count (0 == converged/steady state)."""
        return self._state.attempt

    async def run_cycle(self) -> int:
        """Sleep the scheduled backoff, then run every round once.

        Returns the total integer progress made across the cycle. Updates the
        backoff: any progress (or any cleanly-running round) keeps the peer in
        steady state; a cycle where *every* round raised lengthens the delay.
        """
        delay = self._state.next_delay()
        await self._sleep(delay)
        attempt = self._state.attempt
        total = 0
        any_ok = False
        for run_round in self._rounds:
            progress, ok = await self._run_one(run_round)
            self.history.append(
                RoundResult(
                    index=self._index,
                    attempt=attempt,
                    delay=delay,
                    progress=progress,
                    ok=ok,
                )
            )
            self._index += 1
            total += progress
            any_ok = any_ok or ok
        # A cycle counts as recovered if at least one round ran without raising:
        # the peer is reachable again even if nothing new was pulled. Only a
        # fully-failed cycle (every round raised) escalates the backoff.
        if any_ok:
            self._state.record_progress()
        else:
            self._state.record_failure()
        return total

    async def run(self, cycles: int) -> int:
        """Run ``cycles`` cycles back-to-back; return total progress made.

        ``cycles`` is an explicit bound rather than an unbounded ``while True`` so
        the loop is a bounded, testable computation. A long-lived node passes a
        large bound (or calls :meth:`run_cycle` from its own supervised task).
        """
        if not isinstance(cycles, int) or isinstance(cycles, bool):
            raise TypeError("cycles must be int")
        if cycles < 0:
            raise ValueError("cycles must be >= 0")
        total = 0
        for _ in range(cycles):
            total += await self.run_cycle()
        return total

    async def _run_one(self, run_round: SyncRound) -> tuple[int, bool]:
        """Execute one round, mapping a raise to a failed (0-progress) outcome."""
        try:
            progress = await run_round()
        except Exception:
            # A round that raised is exactly the disconnect/refusal anti-entropy
            # heals: swallow it, mark the round failed, and let backoff govern the
            # retry cadence. The driver must never crash on one bad peer.
            return 0, False
        if not isinstance(progress, int) or isinstance(progress, bool):
            raise TypeError("a sync round must return an int progress count")
        if progress < 0:
            raise ValueError("sync-round progress must be >= 0")
        return progress, True
