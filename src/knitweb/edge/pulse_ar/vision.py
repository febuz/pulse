"""Vision pipeline — couple YOLO object detection, a CNN, and an LLM into
canonical object observations.

This is the "what am I looking at?" half of Pulse AR. It mirrors the perception
stack a LeCun-style world-model smartglass would run, but keeps the value path
dependency-free and **deterministic** so the core stays credibly neutral and
property-testable. Heavy models plug in behind three tiny protocols:

  * :class:`Detector`   — a **YOLO** head: a frame → coarse boxes (``Detection``).
  * :class:`Classifier` — a fine-grained **CNN**: (frame, box) → a refined class +
                          taxonomy id + a sharper confidence.
  * :class:`Enricher`   — an **LLM**: a class → the structured priors a raw
                          detector can't give — normalised label, estimated
                          millimetre dimensions, and candidate owner/maker/fiber
                          links to attach to the fabric.

``VisionPipeline.observe`` runs detector → classifier → enricher and emits
:class:`ObjectObservation` records tagged with WHERE (the wearer's geohash) and
DEVICE (the wearer's PLS address). Real integrations swap the stubs for
``ultralytics`` YOLO, a torch CNN, and a hosted or on-device LLM — the pipeline and
the canonical record do not change.

A camera frame is passed as raw ``bytes`` on purpose: the core needs no numpy /
torch to move a frame around, and the deterministic stubs hash those bytes so the
same frame always yields the same detections (which is what makes the proofs
reproducible).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ...core import crypto
from .observation import CONF_FULL, Detection, ObjectObservation

__all__ = [
    "Detector",
    "Classifier",
    "Enricher",
    "VisionPipeline",
    "StubYOLODetector",
    "TaxonomyCNN",
    "PriorsLLM",
]


# ---------------------------------------------------------------------------
# Plug points (the real YOLO / CNN / LLM implement these)
# ---------------------------------------------------------------------------

@runtime_checkable
class Detector(Protocol):
    """A YOLO-style detector: a frame → coarse boxes."""

    def detect(self, frame: bytes) -> list[Detection]:
        ...


@runtime_checkable
class Classifier(Protocol):
    """A fine-grained CNN pass: sharpen one detection into (label, taxonomy, conf)."""

    def refine(self, frame: bytes, det: Detection) -> tuple[str, str, int]:
        ...


@runtime_checkable
class Enricher(Protocol):
    """An LLM pass: turn a class into structured fabric priors.

    Returns a dict that may carry any of: ``owner``, ``maker`` (PLS addresses),
    ``width_mm``, ``height_mm``, ``depth_mm`` (integer millimetres), ``fiber_cid``.
    Missing keys simply stay at their observation defaults.
    """

    def enrich(self, label: str, taxonomy: str, context: dict) -> dict:
        ...


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class VisionPipeline:
    """Detector → CNN → LLM → canonical observations."""

    def __init__(
        self,
        detector: Detector,
        classifier: Classifier | None = None,
        enricher: Enricher | None = None,
    ) -> None:
        self.detector = detector
        self.classifier = classifier
        self.enricher = enricher

    def observe(
        self,
        frame: bytes,
        *,
        device: str,
        geohash: str,
        alt_band: int = 0,
        observed_at: int = 0,
        owner: str = "",
        maker: str = "",
    ) -> list[ObjectObservation]:
        """Run the full stack on one frame, tagging each object with WHERE + DEVICE."""
        out: list[ObjectObservation] = []
        for det in self.detector.detect(frame):
            if self.classifier is not None:
                label, taxonomy, confidence = self.classifier.refine(frame, det)
            else:
                label, taxonomy, confidence = det.label, det.label, det.confidence_bps

            priors: dict = {}
            if self.enricher is not None:
                priors = self.enricher.enrich(label, taxonomy, {"bbox": det.bbox})

            out.append(
                ObjectObservation(
                    label=label,
                    taxonomy=taxonomy,
                    confidence_bps=confidence,
                    geohash=geohash,
                    device=device,
                    owner=priors.get("owner", owner),
                    maker=priors.get("maker", maker),
                    alt_band=alt_band,
                    width_mm=priors.get("width_mm", 0),
                    height_mm=priors.get("height_mm", 0),
                    depth_mm=priors.get("depth_mm", 0),
                    observed_at=observed_at,
                    bbox=det.bbox,
                    fiber_cid=priors.get("fiber_cid", ""),
                )
            )
        # Canonical order so the same frame always yields the same observation list.
        out.sort(key=lambda o: (o.label, o.bbox, o.taxonomy))
        return out


# ---------------------------------------------------------------------------
# Deterministic dependency-free stubs (real models plug in behind the protocols)
# ---------------------------------------------------------------------------

class StubYOLODetector:
    """A deterministic stand-in for a YOLO head.

    Given a fixed *scene* (label → bbox), it "detects" every object whose label
    appears in the frame bytes (UTF-8), with a confidence derived deterministically
    from the frame + label hash. No model weights, fully reproducible — the shape a
    real ``ultralytics`` detector would return is identical (a list of Detection).
    """

    def __init__(self, scene: dict[str, tuple[int, int, int, int]]) -> None:
        self._scene = dict(scene)

    def detect(self, frame: bytes) -> list[Detection]:
        text = frame.decode("utf-8", errors="ignore")
        dets: list[Detection] = []
        for label, bbox in self._scene.items():
            if label not in text:
                continue
            seed = crypto.sha256(frame + b"|" + label.encode("utf-8"))
            # Map the digest into a plausible 0.60..1.00 confidence, as integer bps.
            confidence = 6000 + (int.from_bytes(seed[:2], "big") % (CONF_FULL - 6000 + 1))
            dets.append(Detection(label=label, confidence_bps=confidence, bbox=bbox))
        return dets


class TaxonomyCNN:
    """A deterministic stand-in for a fine-grained CNN.

    Maps a coarse YOLO label to a refined (label, taxonomy) pair and nudges the
    confidence toward full certainty (a second, sharper look). Unknown labels pass
    through unchanged — the CNN never invents a class it has no mapping for.
    """

    def __init__(self, mapping: dict[str, tuple[str, str]]) -> None:
        self._mapping = dict(mapping)

    def refine(self, frame: bytes, det: Detection) -> tuple[str, str, int]:
        label, taxonomy = self._mapping.get(det.label, (det.label, det.label))
        # A confident second pass closes half the remaining gap to full certainty.
        confidence = det.confidence_bps + (CONF_FULL - det.confidence_bps) // 2
        return label, taxonomy, confidence


class PriorsLLM:
    """A deterministic stand-in for the LLM enrichment pass.

    A table of structured priors per taxonomy: the millimetre dimensions, and the
    owner/maker/fiber links an LLM would attach from the knowledge fabric. This is
    the WHO + HOW the raw detector cannot produce on its own.
    """

    def __init__(self, priors: dict[str, dict]) -> None:
        self._priors = {k: dict(v) for k, v in priors.items()}

    def enrich(self, label: str, taxonomy: str, context: dict) -> dict:
        # Prefer a taxonomy-keyed prior, fall back to a label-keyed one.
        return dict(self._priors.get(taxonomy, self._priors.get(label, {})))
