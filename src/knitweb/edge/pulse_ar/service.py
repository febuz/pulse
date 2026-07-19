"""ObservationService — the transport-agnostic core an AR server exposes.

A headset is a thin client: it captures a camera frame + its pose and wants back
the objects in view. All the real work — run the vision pipeline, sign the
observations, publish them on the bitchat mesh, fuse verified peers — belongs to a
:class:`~knitweb.edge.pulse_ar.glass.PulseARGlass`. This class wraps one glass in a
tiny request/response API returning plain JSON-able dicts, so an HTTP/WebSocket/BLE
front-end is a thin shell over it (and the logic is unit-testable with no server).

The dicts carry the full **WHAT / WHO / WHERE / HOW / DEVICE** answer plus the
source-pixel ``bbox`` a headset needs to place each label in the field of view.
"""

from __future__ import annotations

from .glass import PulseARGlass
from .observation import ObjectObservation

__all__ = ["ObservationService", "observation_view"]


def observation_view(obs: ObjectObservation) -> dict:
    """A JSON-able view of one observation for a headset to render."""
    return {
        "what": obs.label,
        "taxonomy": obs.taxonomy,
        "confidence_bps": obs.confidence_bps,
        "bbox": list(obs.bbox),
        "owner": obs.owner,
        "maker": obs.maker,
        "where": obs.geohash,
        "alt_band": obs.alt_band,
        "dimensions_mm": [obs.width_mm, obs.height_mm, obs.depth_mm],
        "device": obs.device,
        "cid": obs.cid,
    }


class ObservationService:
    """Wrap a :class:`PulseARGlass` in a JSON request/response API."""

    def __init__(self, glass: PulseARGlass) -> None:
        self.glass = glass

    def observe(
        self,
        frame: bytes,
        *,
        lat: float | None = None,
        lon: float | None = None,
        altitude_m: float | None = None,
        observed_at: int = 0,
        owner: str = "",
        maker: str = "",
    ) -> dict:
        """Ingest one frame (+ optional pose), share observations, return them.

        If ``lat``/``lon`` are given the wearer is moved there first, so a walking
        headset keeps its WHERE anchor current.
        """
        if lat is not None and lon is not None:
            self.glass.move(lat, lon, altitude_m)
        shared = self.glass.observe_and_share(
            frame, observed_at=observed_at, owner=owner, maker=maker
        )
        return {
            "device": self.glass.device,
            "count": len(shared),
            "detections": [observation_view(s.observation) for s in shared],
        }

    def overlays(self) -> dict:
        """The current fused field-of-view overlays (own + verified nearby peers)."""
        return {
            "device": self.glass.device,
            "overlays": self.glass.overlays(),
            "count": self.glass.observation_count,
        }

    def features(self) -> dict:
        """The compact inner-world-model feature set (deterministic)."""
        return {"device": self.glass.device, "features": self.glass.features()}
