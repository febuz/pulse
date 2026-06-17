"""Spatial binding — geohash anchors that bind physical location to the fabric.

A humanoid / AR glass knows where it is (latitude, longitude, and roughly its
height). To pull *location-relevant* context out of shared memory without scanning
everything, we bind knowledge to the physical world with a **geohash**: lat/lon
folded into a short base32 string — a "Spatial Fiber". Two key properties:

  * **Proximity is a string-prefix test.** Nearby points share a geohash prefix,
    so "what's near me?" needs no geo-database — just compare prefixes.
  * **No floats in the stored record.** The geohash *string* is the canonical
    anchor (and an integer altitude band gives crude 3D / "physics"), so spatial
    anchors hash deterministically like every other fabric record.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import canonical

__all__ = ["geohash", "common_prefix_len", "proximate", "altitude_band",
           "SpatialAnchor", "bind"]

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
_BITS = (16, 8, 4, 2, 1)


def geohash(lat: float, lon: float, precision: int = 9) -> str:
    """Encode (lat, lon) as a base32 geohash of ``precision`` characters.

    Floats are used only transiently to derive the string; the returned geohash
    is what gets stored/hashed, so the canonical record stays float-free.
    """
    if not -90.0 <= lat <= 90.0:
        raise ValueError("latitude out of range [-90, 90]")
    if not -180.0 <= lon <= 180.0:
        raise ValueError("longitude out of range [-180, 180]")
    if precision <= 0:
        raise ValueError("precision must be positive")

    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    out: list[str] = []
    bit = 0
    ch = 0
    even = True
    while len(out) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if lon >= mid:
                ch |= _BITS[bit]
                lon_lo = mid
            else:
                lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if lat >= mid:
                ch |= _BITS[bit]
                lat_lo = mid
            else:
                lat_hi = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            out.append(_BASE32[ch])
            bit = 0
            ch = 0
    return "".join(out)


def common_prefix_len(a: str, b: str) -> int:
    """Length of the shared leading geohash prefix (coarse 'how close')."""
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return n


def proximate(a: str, b: str, precision: int) -> bool:
    """True if two geohashes fall in the same cell at ``precision`` chars."""
    return a[:precision] == b[:precision]


def altitude_band(altitude_m: float, band_m: int = 3) -> int:
    """Fold an altitude in metres into an integer band (crude vertical 'physics')."""
    if band_m <= 0:
        raise ValueError("band_m must be positive")
    return int(altitude_m // band_m)


@dataclass(frozen=True)
class SpatialAnchor:
    """Binds a content CID to a physical cell (geohash + integer altitude band)."""

    geohash: str
    target: str          # CID of the anchored knowledge / relation
    alt_band: int = 0    # integer altitude band (0 = ground / unknown)

    def to_record(self) -> dict:
        return {
            "kind": "spatial-anchor",
            "geohash": self.geohash,
            "target": self.target,
            "alt_band": self.alt_band,
        }

    @property
    def cid(self) -> str:
        return canonical.cid(self.to_record())

    def weave(self, web) -> str:
        """Weave this anchor into *web*; return its CID."""
        return web.weave(self.to_record())


def bind(lat: float, lon: float, target: str, precision: int = 9,
         altitude_m: float | None = None) -> SpatialAnchor:
    """Create a SpatialAnchor binding ``target`` to (lat, lon[, altitude])."""
    band = altitude_band(altitude_m) if altitude_m is not None else 0
    return SpatialAnchor(geohash=geohash(lat, lon, precision), target=target,
                         alt_band=band)
