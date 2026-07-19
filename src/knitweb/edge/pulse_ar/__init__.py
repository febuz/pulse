"""Pulse AR — augmented reality over the bitchat BLE mesh.

A pair of smartglasses runs a YOLO→CNN→LLM vision stack that turns a camera frame
into signed, content-addressed **object observations** — the *what* (class), *who*
(owner + maker), *where* (geohash), *how* (integer-mm dimensions), and *which
device* — and exchanges them with nearby wearers over a **bitchat** Bluetooth Low
Energy mesh. Peers verify every observation before trusting it, keep the ones
anchored near them, and fuse them into both a field-of-view overlay and a compact
feature set that augments the inner world-model.

It is the physical-object companion to :mod:`knitweb.edge.runtime` /
:mod:`knitweb.edge.arglass` (which stream verified *relation* bytecode): same
verify-before-trust, geohash-anchored, integer-only, deterministic discipline —
now for real things in view.

Modules:
  * :mod:`~knitweb.edge.pulse_ar.observation` — the canonical observation record.
  * :mod:`~knitweb.edge.pulse_ar.vision`      — the YOLO/CNN/LLM pipeline + stubs.
  * :mod:`~knitweb.edge.pulse_ar.bitchat`     — the BLE mesh transport.
  * :mod:`~knitweb.edge.pulse_ar.glass`       — the ``PulseARGlass`` orchestrator.
"""

from __future__ import annotations

from .bitchat import DEFAULT_MTU, MAX_TTL, BitchatFrame, MeshNode, fragment
from .glass import PulseARGlass
from .observation import CONF_FULL, Detection, ObjectObservation, SignedObservation
from .service import ObservationService, observation_view
from .vision import (
    Classifier,
    Detector,
    Enricher,
    PriorsLLM,
    StubYOLODetector,
    TaxonomyCNN,
    VisionPipeline,
)
# Real YOLO lives in its own module so its lazy torch/pillow imports never load
# unless a caller actually constructs the detector.
from .vision_ultralytics import (
    COCO_TO_TAXONOMY,
    UltralyticsYOLODetector,
    detections_from_result,
)

__all__ = [
    # observation
    "Detection",
    "ObjectObservation",
    "SignedObservation",
    "CONF_FULL",
    # vision
    "VisionPipeline",
    "Detector",
    "Classifier",
    "Enricher",
    "StubYOLODetector",
    "TaxonomyCNN",
    "PriorsLLM",
    # real YOLO (optional 'vision' extra)
    "UltralyticsYOLODetector",
    "detections_from_result",
    "COCO_TO_TAXONOMY",
    # bitchat mesh
    "BitchatFrame",
    "MeshNode",
    "fragment",
    "MAX_TTL",
    "DEFAULT_MTU",
    # orchestrator + service
    "PulseARGlass",
    "ObservationService",
    "observation_view",
]
