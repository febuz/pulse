"""
knitweb — Python core of the fiber/dot/knot graph layer

Three 256-bit address spaces define the mathematical bounds of the knitweb:
  Fiber  — node  in graph theory (spider / participant)
  Dot    — edge  in graph theory (connection between fibers)
  Knot   — content unit          (2-line post, SHA-256 addressed)

FBR (Fiber) is the free silk token, earned by participating in the knitweb.
"""

from .addressing import addr256, is_valid_addr
from .fiber import Fiber, FiberRegistry
from .dot import Dot, DotRegistry
from .knot import Knot, KnotRegistry
from .fbr import FBRWallet, FBRLedger
from .graph import KnitweaveGraph
from .market import MarketCap
from .risk import RiskKnotLedger, RiskKnot, RiskStake, RiskVote

__all__ = [
    "addr256",
    "is_valid_addr",
    "Fiber",
    "FiberRegistry",
    "Dot",
    "DotRegistry",
    "Knot",
    "KnotRegistry",
    "FBRWallet",
    "FBRLedger",
    "KnitweaveGraph",
    "MarketCap",
    "RiskKnotLedger",
    "RiskKnot",
    "RiskStake",
    "RiskVote",
]
