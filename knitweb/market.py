"""
MarketCap — mathematical bounds of the knitweb economy.

The maximum possible market cap is determined by three independent 256-bit
address spaces:

  Dimension      | Math graph concept | knitweb element | Max elements
  ───────────────┼────────────────────┼─────────────────┼──────────────
  Fiber space    | Node / Vertex      | Spider           | 2^256
  Dot space      | Edge / Arc         | Connection       | 2^256
  Knot space     | Content unit       | Post             | 2^256

Total addressable elements = 3 × 2^256

PLS max mintable supply: each of the 2^256 possible knots can mint at most
  POSTER_REWARD + (VALIDATORS_REQUIRED × VALIDATOR_REWARD) µPLS
on confirmation. Practical supply is set by actual participation — there is no
premine and no reserve; PLS circulates or it burns.
"""

from __future__ import annotations

from dataclasses import dataclass

from .addressing import ADDR_BITS
from .pulse import (
    PULSE_POSTER_REWARD,
    PULSE_VALIDATOR_REWARD,
    VALIDATORS_REQUIRED,
    BURN_AFTER_DAYS,
)


ADDR_SPACE: int = 2 ** ADDR_BITS   # elements per dimension

DIMENSIONS: int = 3                  # fiber + dot + knot

MAX_ELEMENTS: int = DIMENSIONS * ADDR_SPACE

_PLS_PER_KNOT: int = PULSE_POSTER_REWARD + VALIDATORS_REQUIRED * PULSE_VALIDATOR_REWARD
MAX_PLS_SUPPLY: int = ADDR_SPACE * _PLS_PER_KNOT


@dataclass(frozen=True)
class MarketCap:
    """
    Immutable summary of the knitweb's mathematical capacity bounds.
    """

    addr_bits: int        = ADDR_BITS
    dimensions: int       = DIMENSIONS
    fiber_space: int      = ADDR_SPACE
    dot_space: int        = ADDR_SPACE
    knot_space: int       = ADDR_SPACE
    total_elements: int   = MAX_ELEMENTS
    max_pls_supply: int   = MAX_PLS_SUPPLY
    pls_per_knot: int     = _PLS_PER_KNOT
    burn_after_days: int  = BURN_AFTER_DAYS

    def summary(self) -> dict:
        return {
            "addr_bits": self.addr_bits,
            "dimensions": {
                "fiber": "node  (graph vertex) — spider / participant",
                "dot":   "edge  (graph arc)    — connection between elements",
                "knot":  "content unit         — 2-line post (SHA-256 addressed)",
            },
            "capacity": {
                "per_dimension_hex": hex(self.fiber_space),
                "per_dimension_approx": f"~1.16 × 10^77",
                "total_elements_hex": hex(self.total_elements),
                "total_elements_approx": f"~3.47 × 10^77",
            },
            "pls_token": {
                "max_supply_hex": hex(self.max_pls_supply),
                "max_supply_approx": f"~{_PLS_PER_KNOT} × 10^77 µPLS",
                "micro_pls_per_confirmed_knot": self.pls_per_knot,
                "poster_reward": PULSE_POSTER_REWARD,
                "validator_reward": PULSE_VALIDATOR_REWARD,
                "validators_required": VALIDATORS_REQUIRED,
                "burn_after_days": self.burn_after_days,
                "note": (
                    "Practical supply << theoretical max. "
                    "PLS circulates or burns — no pre-mine, no reserve."
                ),
            },
        }

    def utilisation(self, fibers: int, dots: int, knots: int) -> dict:
        return {
            "fiber_utilisation": (fibers, self.fiber_space),
            "dot_utilisation":   (dots,   self.dot_space),
            "knot_utilisation":  (knots,  self.knot_space),
            "combined_elements": fibers + dots + knots,
            "combined_vs_max":   (fibers + dots + knots, self.total_elements),
        }
