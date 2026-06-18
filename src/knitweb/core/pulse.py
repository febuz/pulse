"""Pulse — the web's heartbeat (one of the seven core primitives).

The Pulse is Knitweb's rhythmic clock. It divides time into **epochs** and emits
content-addressed **beats**, each anchoring an epoch to a state root and chaining
to the previous beat. Higher layers ride the Pulse to drive:

  * checkpoint propagation (a Merkle root of fabric state per epoch),
  * demand-gated PLS mint windows (today the mint in ``token.mint`` is bounded by
    escrowed demand plus an optional ``max_supply`` cap; binding a mint cap to a
    Beat/epoch is a future wiring, not yet implemented),
  * liveness / availability probes and sampled re-execution scheduling.

Time is *injected* (the caller supplies the timestamp), never read from a global
clock, so epochs and beats are fully deterministic and reproducible in tests and
across peers. Beats carry integer fields only (canonical-encoding friendly).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import canonical

__all__ = ["Pulse", "Beat"]


def _require_int(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int")


def _require_str(name: str, value: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str")


@dataclass(frozen=True)
class Beat:
    """A single heartbeat: epoch N anchored to a state root, chained to prev."""

    epoch: int
    timestamp: int          # integer seconds (injected)
    state_root: str         # hex Merkle root of fabric state at this epoch
    prev_beat: str | None   # CID of the previous beat, or None for genesis

    def __post_init__(self) -> None:
        _require_int("epoch", self.epoch)
        _require_int("timestamp", self.timestamp)
        _require_str("state_root", self.state_root)
        if self.prev_beat is not None:
            _require_str("prev_beat", self.prev_beat)

    def to_record(self) -> dict:
        return {
            "kind": "pulse-beat",
            "epoch": self.epoch,
            "timestamp": self.timestamp,
            "state_root": self.state_root,
            "prev_beat": self.prev_beat,
        }

    @property
    def cid(self) -> str:
        return canonical.cid(self.to_record())


class Pulse:
    """A deterministic epoch heartbeat anchored at ``genesis_ts``.

    Epochs are ``(timestamp - genesis_ts) // interval_s``. ``beat`` produces the
    next chained Beat for a given timestamp and state root; beats must advance
    monotonically in epoch.
    """

    def __init__(self, interval_s: int, genesis_ts: int) -> None:
        _require_int("interval_s", interval_s)
        _require_int("genesis_ts", genesis_ts)
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        if genesis_ts < 0:
            raise ValueError("genesis_ts must be non-negative")
        self.interval_s = interval_s
        self.genesis_ts = genesis_ts
        self._last: Beat | None = None
        self.beats: list[Beat] = []

    def epoch_at(self, timestamp: int) -> int:
        """Return the epoch index for ``timestamp`` (clamped at genesis)."""
        _require_int("timestamp", timestamp)
        if timestamp < self.genesis_ts:
            return 0
        return (timestamp - self.genesis_ts) // self.interval_s

    @property
    def current_epoch(self) -> int:
        return self._last.epoch if self._last is not None else -1

    def beat(self, timestamp: int, state_root: str) -> Beat:
        """Emit the next heartbeat for ``timestamp`` anchoring ``state_root``.

        Raises if the resulting epoch does not strictly advance the last beat —
        the Pulse never goes backwards or stalls on the same epoch.
        """
        _require_str("state_root", state_root)
        epoch = self.epoch_at(timestamp)
        if self._last is not None and epoch <= self._last.epoch:
            raise ValueError(
                f"epoch {epoch} does not advance last beat epoch {self._last.epoch}"
            )
        prev = self._last.cid if self._last is not None else None
        beat = Beat(
            epoch=epoch,
            timestamp=timestamp,
            state_root=state_root,
            prev_beat=prev,
        )
        self._last = beat
        self.beats.append(beat)
        return beat

    def verify_chain(self) -> bool:
        """Verify the recorded beats form a strictly increasing, linked chain."""
        prev: Beat | None = None
        for b in self.beats:
            if prev is None:
                if b.prev_beat is not None:
                    return False
            else:
                if b.prev_beat != prev.cid:
                    return False
                if b.epoch <= prev.epoch:
                    return False
            prev = b
        return True
