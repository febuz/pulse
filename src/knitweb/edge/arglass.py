"""Python interface for an AR glass / humanoid head unit.

This is the device-side glue an AR glass runs. The glass knows where it is (a
geohash) and receives signed synaptic bytecode bundles over BLE / 5G / Wi-Fi /
satellite. ``ARGlass``:

  * **verifies** every incoming bundle (a forged relation is refused — acting on
    bad data is a safety problem, not a UI glitch),
  * keeps only bundles **anchored near the wearer** (geohash proximity),
  * exposes **overlays()** — what to draw in the field of view — and **features()**
    — the compact dict that augments the glass's inner (software) model.

It is deliberately transport-agnostic: BLE/5G/satellite code calls ``receive``
with the bytes it got; this class owns verification, spatial filtering, and
projection.
"""

from __future__ import annotations

from ..fabric.spatial import geohash, proximate
from .runtime import EdgeBundle

__all__ = ["ARGlass"]


class ARGlass:
    """A wearer's view onto location-relevant, verified shared memory."""

    def __init__(self, lat: float, lon: float, precision: int = 7) -> None:
        self.precision = precision
        self.geohash = geohash(lat, lon, precision)
        self._bundles: list[EdgeBundle] = []

    # -- location ----------------------------------------------------------

    def move(self, lat: float, lon: float) -> None:
        """Update the wearer's position (recomputes the proximity cell)."""
        self.geohash = geohash(lat, lon, self.precision)

    # -- ingest (verify + spatial filter) ---------------------------------

    def receive(
        self,
        data: bytes,
        originator_pub: str,
        signature: str,
        anchor_geohash: str | None = None,
    ) -> bool:
        """Verify a bundle and keep it if it's near the wearer.

        Returns True if accepted, False if it was for somewhere else. Raises
        ``EdgeVerifyError`` (from the runtime) if the originator signature is bad.
        """
        bundle = EdgeBundle.load(data, originator_pub=originator_pub, signature=signature)
        if anchor_geohash is not None and not proximate(
            self.geohash, anchor_geohash, self.precision
        ):
            return False
        self._bundles.append(bundle)
        return True

    # -- projection --------------------------------------------------------

    def overlays(self, subject: str | None = None) -> list[dict]:
        """What to render in the field of view: verified (source, originator) pairs."""
        out: list[dict] = []
        for b in self._bundles:
            relations = b.query(subject=subject) if subject is not None else b.relations
            for r in relations:
                out.append({
                    "subject": r.subject,
                    "source_type": r.source_type,
                    "url": r.obj,
                    "originator": b.originator,
                })
        return out

    def features(self) -> dict[str, dict[str, list[str]]]:
        """Merged ``subject -> {source_type: [objects]}`` to augment the inner model."""
        merged: dict[str, dict[str, list[str]]] = {}
        for b in self._bundles:
            for subj, by_type in b.to_feature_dict().items():
                dst = merged.setdefault(subj, {})
                for st, objs in by_type.items():
                    dst[st] = sorted(set(dst.get(st, [])) | set(objs))
        return merged

    @property
    def bundle_count(self) -> int:
        return len(self._bundles)
