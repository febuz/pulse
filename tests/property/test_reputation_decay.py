"""Reputation decay is actually driven — bans rehabilitate over maintenance epochs.

The :mod:`knitweb.p2p.reputation` ledger documents *rehabilitation over time* via explicit
``decay``/``decay_all`` calls "per Pulse epoch", but nothing drove them, so a transiently-noisy
honest peer that crossed the ban threshold stayed banned forever and scores only ever rose. The
anti-entropy maintenance round is that epoch tick; these tests pin both the rehabilitation
behavior and the *wiring* (``BaseNode._anti_entropy_run`` decays once per completed round), so
deleting the driver call breaks the build. All integer, no wall-clock — verdicts stay reproducible.
"""
import asyncio
from types import SimpleNamespace

import pytest

from knitweb.p2p.base_node import BaseNode
from knitweb.p2p.reputation import (
    DEFAULT_REPUTATION_DECAY_PER_ROUND,
    Offense,
    PeerReputation,
)


class _StopLoop(Exception):
    """Sentinel to break ``_anti_entropy_run``'s ``while True`` after a fixed cycle count.

    Not a ``CancelledError`` so it propagates straight through the loop's
    ``except asyncio.CancelledError`` guard and out to the test."""


class _DriverStub:
    """A driver whose ``run_cycle`` succeeds ``ok`` times, then stops the loop."""

    def __init__(self, ok: int) -> None:
        self.ok = ok
        self.calls = 0

    async def run_cycle(self) -> None:
        self.calls += 1
        if self.calls > self.ok:
            raise _StopLoop


def test_default_decay_is_a_positive_int():
    assert type(DEFAULT_REPUTATION_DECAY_PER_ROUND) is int
    assert DEFAULT_REPUTATION_DECAY_PER_ROUND >= 1


def test_anti_entropy_round_drives_one_decay_per_completed_cycle():
    rep = PeerReputation()
    rep.penalize("noisy", 30)
    fake = SimpleNamespace(reputation=rep, _reputation_decay_per_round=2)
    driver = _DriverStub(ok=5)
    with pytest.raises(_StopLoop):
        asyncio.run(BaseNode._anti_entropy_run(fake, driver))
    assert driver.calls == 6                 # 5 completed rounds + the 1 that stops the loop
    assert rep.score("noisy") == 30 - 5 * 2  # decayed exactly once per completed round


def test_disabled_decay_leaves_scores_untouched():
    rep = PeerReputation()
    rep.penalize("p", 40)
    fake = SimpleNamespace(reputation=rep, _reputation_decay_per_round=0)
    driver = _DriverStub(ok=5)
    with pytest.raises(_StopLoop):
        asyncio.run(BaseNode._anti_entropy_run(fake, driver))
    assert rep.score("p") == 40              # rate 0 => true no-op


def test_soft_offense_ban_rehabilitates_after_an_epoch():
    rep = PeerReputation()
    for _ in range(10):                      # 10 × MALFORMED_FRAME(10) = 100 = ban threshold
        rep.penalize("flaky", Offense.MALFORMED_FRAME)
    assert rep.is_banned("flaky")
    rep.decay_all(DEFAULT_REPUTATION_DECAY_PER_ROUND)   # one maintenance epoch
    assert not rep.is_banned("flaky")        # 99 < 100 — rehabilitated
    assert rep.score("flaky") == 100 - DEFAULT_REPUTATION_DECAY_PER_ROUND


def test_decay_never_outpaces_a_sustained_offender():
    rep = PeerReputation()
    rate = 3
    for _ in range(20):                      # +10 per round, −3 decay ⇒ net +7/round
        rep.penalize("attacker", Offense.MALFORMED_FRAME)
        rep.decay_all(rate)
    assert rep.is_banned("attacker")         # real abuse still crosses the threshold


def test_decay_floors_at_zero_and_clears_ban():
    rep = PeerReputation()
    rep.penalize("p", 5)
    rep.decay_all(1000)                      # over-decay clamps, never negative
    assert rep.score("p") == 0
    assert not rep.is_banned("p")
