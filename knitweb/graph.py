"""
KnitweaveGraph — the full 3D graph of fibers, dots, and knots.

Brings together FiberRegistry, DotRegistry, KnotRegistry and FBRLedger
into a single coordinated object.  This is the entry point for the Python
knitweb service.
"""

from __future__ import annotations

from typing import Optional

from .addressing import addr256
from .fiber import Fiber, FiberRegistry
from .dot import Dot, DotRegistry, DotType
from .knot import Knot, KnotRegistry, compute_knot_addr
from .fbr import FBRLedger
from .market import MarketCap


class KnitweaveGraph:
    """
    The knitweb as a living 3D graph.

    Public interface mirrors the REST API that will wrap it:
      register_fiber    — spider joins or heartbeats
      post_knot         — spider posts a 2-line knot
      validate_knot     — spider votes a knot valid (may trigger FBR mint)
      get_knot          — look up a knot by address
      stats             — combined statistics
    """

    def __init__(self) -> None:
        self.fibers   = FiberRegistry()
        self.dots     = DotRegistry()
        self.knots    = KnotRegistry()
        self.ledger   = FBRLedger()
        self.market   = MarketCap()

    # ── Fiber ─────────────────────────────────────────────────────────────────

    def register_fiber(
        self,
        did: str,
        label: str = "",
        silk: bool = True,
    ) -> Fiber:
        return self.fibers.register(did, label=label, silk=silk)

    # ── Knot ──────────────────────────────────────────────────────────────────

    def post_knot(
        self,
        line1: str,
        line2: str = "",
        author: str = "did:silk:anonymous",
        signature: str = "",
        ts: Optional[str] = None,
    ) -> dict:
        """
        Post a new knot.  The author fiber is registered if not seen before.
        Returns the add result and the knot's address.
        """
        fiber = self.fibers.register(author)
        knot  = Knot.create(line1=line1, line2=line2, author=author,
                            signature=signature, ts=ts)
        result = self.knots.add(knot)
        if result["ok"]:
            fiber.knot_count += 1
        return {**result, "addr": knot.addr, "knot": knot}

    # ── Validation ────────────────────────────────────────────────────────────

    def validate_knot(
        self,
        knot_addr: str,
        validator_did: str,
    ) -> dict:
        """
        Spider validates a knot.  On confirmation (3rd unique validator):
          - Poster earns FBR_POSTER_REWARD µFBR
          - Each validator earns FBR_VALIDATOR_REWARD µFBR
          - A VALIDATES dot is added from validator fiber to knot
          - Knot confirmation_count is updated
        """
        knot = self.knots.get(knot_addr)
        if knot is None:
            return {"ok": False, "reason": "knot not found"}

        ok, event = self.ledger.validate(knot_addr, knot.author, validator_did)
        if not ok:
            return {"ok": False, "reason": event}

        # Register validator fiber if not seen
        self.fibers.register(validator_did)
        validator_fiber = self.fibers.get_by_did(validator_did)
        if validator_fiber:
            validator_fiber.validation_count += 1

        # Add VALIDATES dot: validator_fiber → knot
        validator_addr = addr256(validator_did)
        self.dots.connect(validator_addr, knot_addr, DotType.VALIDATES)

        if event == "confirmed":
            knot.confirmed        = True
            knot.validation_count = 3  # VALIDATORS_REQUIRED

        status = self.ledger.validation_status(knot_addr)
        return {
            "ok":          True,
            "event":       event,
            "knot_addr":   knot_addr,
            "validations": status["validations"],
            "confirmed":   status["confirmed"],
            "needed":      status["needed"],
        }

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_knot(self, addr: str) -> Optional[Knot]:
        return self.knots.get(addr)

    def list_knots(self, limit: int = 50, offset: int = 0) -> list[Knot]:
        return self.knots.list(limit=limit, offset=offset)

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        fbr  = self.ledger.stats()
        util = self.market.utilisation(
            fibers=len(self.fibers),
            dots=len(self.dots),
            knots=len(self.knots),
        )
        return {
            "graph": {
                "fibers": len(self.fibers),
                "dots":   len(self.dots),
                "knots":  len(self.knots),
            },
            "fbr": fbr,
            "market": self.market.summary(),
            "utilisation": {
                "fibers": util["fiber_utilisation"],
                "dots":   util["dot_utilisation"],
                "knots":  util["knot_utilisation"],
            },
        }
