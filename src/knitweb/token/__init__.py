"""Native PLS token economics — demand-gated, bounded issuance via PoUW.

No premine, no admin mint: native PLS comes into existence only as a bounded reward
for verified useful work (see :mod:`knitweb.token.mint`).
"""

from .mint import NATIVE, EmissionPolicy, Issuance, Treasury

__all__ = ["NATIVE", "EmissionPolicy", "Issuance", "Treasury"]
