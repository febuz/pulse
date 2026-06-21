"""Pulse — the web's heartbeat (one of the seven core primitives).

The Pulse is Knitweb's rhythmic clock. It divides time into **epochs** and emits
content-addressed **beats**, each anchoring an epoch to a state root and chaining
to the previous beat. Higher layers ride the Pulse to drive:

  * checkpoint propagation (a Merkle root of fabric state per epoch),
  * demand-gated PLS mint windows (the mint in ``token.mint`` is bounded by
    escrowed demand plus an optional ``max_supply`` cap; the signed Beat now also
    carries an optional per-epoch mint cap (``Beat.epoch_mint_cap``, read via
    ``cap_for_epoch``) which ``reward_verified_work`` prefers as the consensus-visible
    ceiling),
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
    # Consensus-visible per-epoch mint ceiling (PLS-wei). ``None`` ⇒ the Beat carries
    # no cap and is byte-identical to a pre-cap Beat: the field is conditionally
    # OMITTED from to_record()/cid when None (the byte-identity guard, mirroring
    # Issuance.epoch). When present, token minting prefers this Beat-carried cap over
    # the policy default, so the *signed heartbeat* — not runtime config — governs the
    # per-epoch money supply, auditable straight from the chain of Beats.
    epoch_mint_cap: int | None = None

    def __post_init__(self) -> None:
        _require_int("epoch", self.epoch)
        _require_int("timestamp", self.timestamp)
        _require_str("state_root", self.state_root)
        if self.prev_beat is not None:
            _require_str("prev_beat", self.prev_beat)
        if self.epoch_mint_cap is not None:
            if not isinstance(self.epoch_mint_cap, int) or isinstance(
                self.epoch_mint_cap, bool
            ):
                raise TypeError("epoch_mint_cap must be int")
            if self.epoch_mint_cap < 0:
                raise ValueError("epoch_mint_cap must be non-negative")

    def to_record(self) -> dict:
        record = {
            "kind": "pulse-beat",
            "epoch": self.epoch,
            "timestamp": self.timestamp,
            "state_root": self.state_root,
            "prev_beat": self.prev_beat,
        }
        # Conditional field: absent when None so capless Beats keep byte-identical
        # canonical bytes (and CID) to the pre-cap encoding.
        if self.epoch_mint_cap is not None:
            record["epoch_mint_cap"] = self.epoch_mint_cap
        return record

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

    def beat(
        self, timestamp: int, state_root: str, epoch_mint_cap: int | None = None
    ) -> Beat:
        """Emit the next heartbeat for ``timestamp`` anchoring ``state_root``.

        ``epoch_mint_cap`` (default ``None``) optionally binds a consensus-visible
        per-epoch mint ceiling to this Beat; ``None`` leaves the Beat byte-identical to
        the pre-cap encoding (a vBank may override the cap when it drives the heartbeat).

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
            epoch_mint_cap=epoch_mint_cap,
        )
        self._last = beat
        self.beats.append(beat)
        return beat

    def cap_for_epoch(self, epoch: int) -> int | None:
        """The per-epoch mint cap carried by the recorded Beat(s) for ``epoch``.

        Returns the ``epoch_mint_cap`` of the recorded Beat for ``epoch`` (the last one
        if several share the epoch), or ``None`` when no Beat for the epoch carries a
        cap. This lets token minting treat the signed heartbeat as the consensus-visible
        monetary governor rather than relying on runtime policy config.

        The "last matching Beat wins" branch is *defensive*: the public ``Pulse.beat()``
        rejects a non-advancing epoch, so two recorded Beats cannot share an epoch via
        the public API; the branch is kept only for direct/defensive use.
        """
        _require_int("epoch", epoch)
        cap: int | None = None
        for b in self.beats:  # ordered list; last matching Beat wins (deterministic)
            if b.epoch == epoch and b.epoch_mint_cap is not None:
                cap = b.epoch_mint_cap
        return cap

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
