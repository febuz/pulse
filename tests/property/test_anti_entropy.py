"""Proofs for the self-healing anti-entropy convergence driver.

Anti-entropy is pure scheduling over injected reconciliation rounds: it must be
**deterministic** (same round outcomes → same delay schedule), **integer-only**
(no float, no wall-clock, no randomness on any path), and **socket-free** (it
drives opaque callbacks, never a real peer). These tests pin the backoff
arithmetic, prove a peer that "disconnects" (rounds raise) backs off and then
*recovers* (resets) when the peer returns, and prove that wiring an anti-entropy
loop around a node leaves a fresh Knit's CID untouched — convergence machinery
must never perturb a signed record's bytes.
"""

import asyncio

import pytest

from knitweb.core import crypto
from knitweb.ledger import knit as knit_mod
from knitweb.p2p.anti_entropy import (
    AntiEntropy,
    Backoff,
    BackoffState,
    RoundResult,
)


def run(coro):
    return asyncio.run(coro)


class VirtualClock:
    """An injected sleep that advances an integer virtual clock — no real time."""

    def __init__(self) -> None:
        self.now = 0
        self.delays: list[int] = []

    async def sleep(self, delay: int) -> None:
        assert isinstance(delay, int) and not isinstance(delay, bool)
        assert delay >= 0
        self.delays.append(delay)
        self.now += delay


# ── 1. Backoff: deterministic integer exponential schedule ───────────────────

def test_backoff_is_exponential_and_capped():
    b = Backoff(base=1, ceiling=64)
    assert [b.delay(a) for a in range(9)] == [1, 2, 4, 8, 16, 32, 64, 64, 64]


def test_backoff_base_scales_the_schedule():
    b = Backoff(base=3, ceiling=100)
    assert b.delay(0) == 3
    assert b.delay(1) == 6
    assert b.delay(2) == 12
    # Capped at ceiling, never overshoots.
    assert b.delay(50) == 100


def test_backoff_delay_is_a_pure_function_of_attempt():
    b = Backoff(base=2, ceiling=1000)
    # Determinism: re-evaluating the same attempt yields byte-identical ints.
    assert [b.delay(a) for a in range(20)] == [b.delay(a) for a in range(20)]


def test_backoff_rejects_floats_and_bools():
    with pytest.raises(TypeError):
        Backoff(base=True)  # bool is not an honest int here
    with pytest.raises(TypeError):
        Backoff(ceiling=1.0)
    b = Backoff()
    with pytest.raises(TypeError):
        b.delay(1.0)
    with pytest.raises(TypeError):
        b.delay(True)


def test_backoff_validates_bounds():
    with pytest.raises(ValueError):
        Backoff(base=0)
    with pytest.raises(ValueError):
        Backoff(base=10, ceiling=5)
    with pytest.raises(ValueError):
        Backoff().delay(-1)


def test_backoff_never_overflows_for_huge_attempts():
    # bit_length short-circuit means a giant attempt is O(1) and returns ceiling,
    # never building a 2**huge intermediate.
    b = Backoff(base=1, ceiling=64)
    assert b.delay(10_000_000) == 64


# ── 2. BackoffState: failure climbs, progress resets ─────────────────────────

def test_state_climbs_on_failure_and_resets_on_progress():
    st = BackoffState(Backoff(base=1, ceiling=8))
    assert st.next_delay() == 1
    st.record_failure()
    assert st.next_delay() == 2
    st.record_failure()
    assert st.next_delay() == 4
    st.record_progress()
    assert st.attempt == 0
    assert st.next_delay() == 1


def test_state_attempt_is_bounded_at_the_cap():
    st = BackoffState(Backoff(base=1, ceiling=4))
    for _ in range(1000):
        st.record_failure()
    # Counter stops climbing once the delay is pinned at the ceiling.
    assert st.next_delay() == 4
    assert st.attempt <= 4


# ── 3. AntiEntropy driver: scheduling over injected rounds ───────────────────

def test_a_converging_cycle_stays_at_steady_interval():
    clock = VirtualClock()

    async def good():
        return 3

    ae = AntiEntropy([good], sleep=clock.sleep, backoff=Backoff(base=1, ceiling=64))
    total = run(ae.run(cycles=5))
    assert total == 15
    # Always reachable → never escalates → every wait is the base interval.
    assert clock.delays == [1, 1, 1, 1, 1]
    assert ae.attempt == 0


def test_a_disconnected_peer_backs_off_then_recovers():
    clock = VirtualClock()
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        # First three rounds raise (peer "down"), then it comes back and converges.
        if calls["n"] <= 3:
            raise ConnectionError("peer unreachable")
        return 7

    ae = AntiEntropy([flaky], sleep=clock.sleep, backoff=Backoff(base=1, ceiling=64))
    total = run(ae.run(cycles=5))

    # Each failed cycle climbs the next delay: cycle0..2 fail at attempts 0,1,2
    # (delays 1,2,4), cycle3 is scheduled at attempt 3 (delay 8) and *succeeds*,
    # resetting the count so cycle4 is back at the base interval (delay 1).
    assert clock.delays == [1, 2, 4, 8, 1]
    assert total == 14  # cycles 3 and 4 each pulled 7 once the peer was back
    assert ae.attempt == 0  # fully healed


def test_partial_progress_in_a_cycle_resets_backoff():
    clock = VirtualClock()
    state = {"phase": 0}

    async def down():
        raise TimeoutError("still down")

    async def up():
        return 0  # reachable but nothing new — still counts as recovered

    ae = AntiEntropy([down, up], sleep=clock.sleep, backoff=Backoff(base=1, ceiling=32))
    run(ae.run(cycles=3))
    # `up` runs cleanly every cycle, so the cycle is never fully-failed → no climb.
    assert clock.delays == [1, 1, 1]
    assert ae.attempt == 0


def test_history_records_the_exact_schedule():
    clock = VirtualClock()
    seq = iter([0, 0])  # two failures then success-ish

    async def r():
        n = next(seq, None)
        if n is None:
            return 5
        raise OSError("down")

    ae = AntiEntropy([r], sleep=clock.sleep, backoff=Backoff(base=1, ceiling=64))
    run(ae.run(cycles=3))
    assert all(isinstance(h, RoundResult) for h in ae.history)
    # A cycle is scheduled at the *current* attempt, runs, then updates: two
    # failures climb 0→1→2, so the recovering 3rd cycle is still scheduled at
    # attempt 2 (delay 4) before its success resets the count for next time.
    assert [(h.attempt, h.delay, h.ok, h.progress) for h in ae.history] == [
        (0, 1, False, 0),
        (1, 2, False, 0),
        (2, 4, True, 5),
    ]
    assert ae.attempt == 0  # reset after the recovered cycle


def test_driver_runs_every_round_per_cycle():
    clock = VirtualClock()
    hits = {"a": 0, "b": 0}

    async def a():
        hits["a"] += 1
        return 1

    async def b():
        hits["b"] += 1
        return 2

    ae = AntiEntropy([a, b], sleep=clock.sleep)
    total = run(ae.run(cycles=4))
    assert hits == {"a": 4, "b": 4}
    assert total == 12


def test_driver_is_deterministic_across_replays():
    def build():
        clock = VirtualClock()
        seq = iter([0, 0, 1, 0])  # raise, raise, ok, raise

        async def r():
            if next(seq, 1) == 0:
                raise ConnectionError
            return 9

        ae = AntiEntropy([r], sleep=clock.sleep, backoff=Backoff(base=2, ceiling=50))
        run(ae.run(cycles=4))
        return clock.delays, [(h.attempt, h.delay) for h in ae.history]

    assert build() == build()


def test_round_must_return_nonnegative_int():
    clock = VirtualClock()

    async def bad():
        return -1

    ae = AntiEntropy([bad], sleep=clock.sleep)
    with pytest.raises(ValueError):
        run(ae.run(cycles=1))

    async def floaty():
        return 1.0

    ae2 = AntiEntropy([floaty], sleep=clock.sleep)
    with pytest.raises(TypeError):
        run(ae2.run(cycles=1))


def test_driver_validates_construction_and_args():
    clock = VirtualClock()
    with pytest.raises(ValueError):
        AntiEntropy([], sleep=clock.sleep)

    async def ok():
        return 0

    ae = AntiEntropy([ok], sleep=clock.sleep)
    with pytest.raises(ValueError):
        run(ae.run(cycles=-1))
    with pytest.raises(TypeError):
        run(ae.run(cycles=1.0))
    # cycles == 0 is a valid no-op.
    assert run(ae.run(cycles=0)) == 0


# ── 4. Byte-identity gate: healing never perturbs a signed Knit ──────────────

# A fixed sender/receiver keypair so the signed record — and thus the CID — is a
# deterministic constant the byte-identity gate can pin.
_FROM_PRIV = "11" * 32
_TO_PRIV = "22" * 32


def _fresh_knit_cid() -> str:
    from_pub = crypto.public_from_private(_FROM_PRIV)
    to_pub = crypto.public_from_private(_TO_PRIV)
    knit = knit_mod.build(
        from_pub=from_pub,
        to_pub=to_pub,
        symbol="PLS",
        amount=1000,
        from_nonce=0,
        timestamp=1,
    )
    knit = knit_mod.sign_from(knit, _FROM_PRIV)
    return knit.id


def test_anti_entropy_does_not_touch_signed_knit_bytes():
    # CID computed with no driver anywhere in the process.
    baseline = _fresh_knit_cid()

    # Now spin a full anti-entropy loop that "drives" sync rounds bound to the
    # very act of minting Knits, exercising the scheduler hard, then re-mint.
    clock = VirtualClock()

    async def mint_round():
        # Minting inside a driven round must not perturb canonical bytes.
        assert _fresh_knit_cid() == baseline
        return 1

    ae = AntiEntropy([mint_round], sleep=clock.sleep, backoff=Backoff(base=1, ceiling=64))
    run(ae.run(cycles=10))

    assert _fresh_knit_cid() == baseline


def test_snapshot_schedule_is_canonical_integer_only():
    # Every observable the driver emits is a plain int — directly canonical.
    clock = VirtualClock()

    async def r():
        return 2

    ae = AntiEntropy([r], sleep=clock.sleep)
    run(ae.run(cycles=3))
    for h in ae.history:
        for field in (h.index, h.attempt, h.delay, h.progress):
            assert isinstance(field, int) and not isinstance(field, bool)
        assert isinstance(h.ok, bool)
