"""Bluetooth co-presence attestation — proof that a backer was *physically local* to a campaign.

Some crowdfunding is inherently local: a neighbourhood repair, a village solar array, a venue
fix-up. For those, “anyone on the internet can pledge” is the wrong gate — you want **backers who
are actually there**. This module is the local-first primitive for that: a campaign advertises a
**beacon** (a BLE anchor at the place), and a backer's device records a :class:`ProximityProof`
that it was within Bluetooth range of that beacon at a given Pulse beat.

It dovetails with the votebank's existing on-ramps: a person is already one-vote/one-backing via
the registry (national or freeport-by-IMEI), and the *device* (the same IMEI that freeport
registration keys on) is what carries the BLE encounter. Proximity adds a second, orthogonal
gate — *were you here?* — that capital cannot fake from afar.

Integer / hash only, like the rest of the Knitweb value-path: signal strength is an integer dBm,
the beat is an integer, and the proof is content-addressed. (Production hardening — the beacon
co-signing the encounter so it can't be fabricated — is a noted follow-up; the structure here is
the attestation that signing would wrap.)
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import canonical

__all__ = ["ProximityProof", "attest"]

_MIN_DBM = -120
_MAX_DBM = 0


def _require_int(name: str, value: int, *, minimum: int, maximum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int, not {type(value).__name__}")
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name} out of range (got {value})")
    return value


def _require_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{name} must be a non-empty str")
    return value


@dataclass(frozen=True)
class ProximityProof:
    """A device's record that ``backer`` was within BLE range of ``beacon`` at ``beat``."""

    backer: str
    beacon: str
    beat: int
    rssi_dbm: int

    def __post_init__(self) -> None:
        _require_text("backer", self.backer)
        _require_text("beacon", self.beacon)
        _require_int("beat", self.beat, minimum=0)
        _require_int("rssi_dbm", self.rssi_dbm, minimum=_MIN_DBM, maximum=_MAX_DBM)

    def is_within_range(self, min_rssi_dbm: int) -> bool:
        _require_int("min_rssi_dbm", min_rssi_dbm, minimum=_MIN_DBM, maximum=_MAX_DBM)
        return self.rssi_dbm >= min_rssi_dbm

    def to_record(self) -> dict:
        return {
            "kind": "govern-proximity",
            "backer": self.backer,
            "beacon": self.beacon,
            "beat": self.beat,
            "rssi_dbm": self.rssi_dbm,
        }

    @property
    def cid(self) -> str:
        return canonical.cid(self.to_record())


def attest(beacon: str, backer: str, *, beat: int, rssi_dbm: int) -> ProximityProof:
    return ProximityProof(backer=backer, beacon=beacon, beat=beat, rssi_dbm=rssi_dbm)
