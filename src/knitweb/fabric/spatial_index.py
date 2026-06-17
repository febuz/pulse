"""Spatial index — answer "what verified targets are near here?" by geohash prefix.

Spatial anchors woven into the Web bind content CIDs to physical cells. This index
lets a humanoid / AR glass at a location pull just the *nearby* targets without
scanning the whole graph: proximity is a geohash-prefix match, optionally narrowed
to a vertical altitude band. It's the query primitive that turns a pile of anchors
into location-relevant context.
"""

from __future__ import annotations

from .spatial import SpatialAnchor, proximate

__all__ = ["SpatialIndex"]


class SpatialIndex:
    """An in-memory index of spatial anchors, queryable by geohash proximity."""

    def __init__(self) -> None:
        self._anchors: list[SpatialAnchor] = []

    def add(self, anchor: SpatialAnchor) -> None:
        self._anchors.append(anchor)

    @classmethod
    def from_web(cls, web) -> "SpatialIndex":
        """Build an index from every ``spatial-anchor`` node woven into ``web``."""
        idx = cls()
        for record in web.nodes.values():
            if record.get("kind") == "spatial-anchor":
                idx.add(
                    SpatialAnchor(
                        geohash=record["geohash"],
                        target=record["target"],
                        alt_band=record.get("alt_band", 0),
                    )
                )
        return idx

    def near(
        self,
        geohash: str,
        precision: int,
        alt_band: int | None = None,
    ) -> list[str]:
        """Return the distinct target CIDs anchored within the same cell.

        ``precision`` controls the radius (more chars ⇒ tighter cell). ``alt_band``,
        if given, restricts to anchors on the same vertical band.
        """
        out: set[str] = set()
        for a in self._anchors:
            if not proximate(a.geohash, geohash, precision):
                continue
            if alt_band is not None and a.alt_band != alt_band:
                continue
            out.add(a.target)
        return sorted(out)

    def __len__(self) -> int:
        return len(self._anchors)
