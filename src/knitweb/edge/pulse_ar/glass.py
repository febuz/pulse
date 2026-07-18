"""PulseARGlass — the device that closes the Pulse AR loop.

One object binds the three halves together:

  1. **See** — run the YOLO→CNN→LLM :class:`VisionPipeline` on a camera frame to
     produce WHAT/WHO/WHERE/HOW/DEVICE observations of the objects in view.
  2. **Share** — sign each observation with the device key and flood it to nearby
     peers over the bitchat BLE mesh.
  3. **Fuse** — ingest peers' observations, **verify before trusting** (a forged or
     mis-attributed observation is refused), keep only the ones anchored near the
     wearer, and expose them two ways: ``overlays()`` for the field of view and
     ``features()`` to augment the inner (world-model / CNN) representation.

This is the AR-glass sibling of :class:`knitweb.edge.arglass.ARGlass`, but for the
richer physical-object schema and with the publish side wired in, so a cluster of
wearers weave a shared, verified, location-anchored picture of the objects around
them with no infrastructure.
"""

from __future__ import annotations

from ...core import crypto
from ...fabric.spatial import altitude_band, geohash, proximate
from .bitchat import MAX_TTL, MeshNode
from .observation import ObjectObservation, SignedObservation
from .vision import VisionPipeline

__all__ = ["PulseARGlass"]

_ANCHOR_PRECISION = 9   # geohash precision stored on each observation (WHERE)


class PulseARGlass:
    """A wearer's Pulse AR device: sees objects, shares them, fuses peers'."""

    def __init__(
        self,
        *,
        priv: str,
        pub: str,
        lat: float,
        lon: float,
        pipeline: VisionPipeline,
        precision: int = 7,
        altitude_m: float | None = None,
        mesh: MeshNode | None = None,
    ) -> None:
        self.priv = priv
        self.pub = pub
        self.device = crypto.address(pub)
        self.pipeline = pipeline
        self.precision = precision                       # proximity-filter cell size
        self._anchor = geohash(lat, lon, _ANCHOR_PRECISION)
        self.alt_band = altitude_band(altitude_m) if altitude_m is not None else 0
        self.mesh = mesh if mesh is not None else MeshNode(self.device)
        self.mesh.on_message(self._ingest)
        # cid -> verified, near observation (own + peers')
        self._observations: dict[str, ObjectObservation] = {}

    # -- location ----------------------------------------------------------

    def move(self, lat: float, lon: float, altitude_m: float | None = None) -> None:
        """Update the wearer's position (recomputes the WHERE anchor + alt band)."""
        self._anchor = geohash(lat, lon, _ANCHOR_PRECISION)
        if altitude_m is not None:
            self.alt_band = altitude_band(altitude_m)

    @property
    def cell(self) -> str:
        """The wearer's proximity cell (coarse geohash used to filter peers)."""
        return self._anchor[: self.precision]

    # -- see + share -------------------------------------------------------

    def observe_and_share(
        self,
        frame: bytes,
        *,
        observed_at: int = 0,
        owner: str = "",
        maker: str = "",
        ttl: int = MAX_TTL,
    ) -> list[SignedObservation]:
        """Run the vision stack on ``frame``, then sign + flood each observation."""
        observations = self.pipeline.observe(
            frame,
            device=self.device,
            geohash=self._anchor,
            alt_band=self.alt_band,
            observed_at=observed_at,
            owner=owner,
            maker=maker,
        )
        shared: list[SignedObservation] = []
        for obs in observations:
            signed = SignedObservation.sign(obs, self.priv, self.pub)
            self._observations[obs.cid] = obs            # own observations are trusted
            self.mesh.publish(signed.to_wire(), ttl=ttl)
            shared.append(signed)
        return shared

    # -- fuse peers (verify-before-trust + spatial filter) -----------------

    def _ingest(self, payload: bytes, origin: str) -> bool:
        """Mesh callback: accept a peer observation iff it verifies and is near.

        Returns True if kept. A bad signature, a device/key mismatch, or a
        malformed envelope is refused silently — acting on a forged physical-object
        claim is a safety problem, so untrusted data never reaches the overlays.
        """
        try:
            signed = SignedObservation.from_wire(payload)
        except (ValueError, KeyError, TypeError):
            return False
        if not signed.verify():
            return False
        obs = signed.observation
        if not proximate(self.cell, obs.geohash, self.precision):
            return False                                  # for somewhere else
        self._observations[obs.cid] = obs
        return True

    # -- projection --------------------------------------------------------

    def overlays(self, label: str | None = None) -> list[dict]:
        """What to render in the field of view — the full WHAT/WHO/WHERE/HOW/DEVICE.

        Deterministically ordered so two wearers with the same verified set draw the
        same overlay stack.
        """
        out: list[dict] = []
        for obs in self._observations.values():
            if label is not None and obs.label != label:
                continue
            out.append({
                "what": obs.label,
                "taxonomy": obs.taxonomy,
                "confidence_bps": obs.confidence_bps,
                "owner": obs.owner,
                "maker": obs.maker,
                "where": obs.geohash,
                "alt_band": obs.alt_band,
                "dimensions_mm": (obs.width_mm, obs.height_mm, obs.depth_mm),
                "device": obs.device,
                "cid": obs.cid,
            })
        out.sort(key=lambda o: o["cid"])
        return out

    def features(self) -> dict[str, dict]:
        """Compact ``label -> {...}`` view to augment the inner world-model.

        Deterministic (all lists sorted, counts integer) so the same verified set
        augments every wearer's model identically — the collective-intelligence
        loop the edge runtime already does for relations, now for physical objects.
        """
        feats: dict[str, dict] = {}
        for obs in self._observations.values():
            slot = feats.setdefault(obs.label, {
                "count": 0, "taxonomies": set(), "makers": set(),
                "owners": set(), "devices": set(),
            })
            slot["count"] += 1
            slot["taxonomies"].add(obs.taxonomy)
            if obs.maker:
                slot["makers"].add(obs.maker)
            if obs.owner:
                slot["owners"].add(obs.owner)
            slot["devices"].add(obs.device)
        return {
            label: {
                "count": slot["count"],
                "taxonomies": sorted(slot["taxonomies"]),
                "makers": sorted(slot["makers"]),
                "owners": sorted(slot["owners"]),
                "devices": sorted(slot["devices"]),
            }
            for label, slot in feats.items()
        }

    @property
    def observation_count(self) -> int:
        return len(self._observations)
