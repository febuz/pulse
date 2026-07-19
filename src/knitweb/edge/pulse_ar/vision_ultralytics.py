"""Real YOLO detection — ultralytics behind the :class:`Detector` protocol.

This is the production detector: it wraps an `ultralytics <https://docs.ultralytics.com>`_
YOLO model (YOLO11 by default) and returns the same :class:`Detection` objects the
deterministic stub does, so it drops straight into :class:`VisionPipeline` with no
other change.

Heavy dependencies (``ultralytics`` → torch, ``pillow`` for frame decoding) are
**optional and lazily imported**: importing this module — and the whole
``knitweb.edge.pulse_ar`` package — never pulls in torch. Only constructing an
:class:`UltralyticsYOLODetector` and calling :meth:`~UltralyticsYOLODetector.detect`
touches them, and a missing dependency raises a clear, actionable ``ImportError``
pointing at the ``vision`` extra (``pip install 'knitweb[vision]'``).

The credibly-neutral core stays dependency-free; GPU inference lives out here at the
edge, exactly where a **spider** sells verifiable compute for PLS.
"""

from __future__ import annotations

import io

from .observation import CONF_FULL, Detection

__all__ = [
    "UltralyticsYOLODetector",
    "detections_from_result",
    "COCO_TO_TAXONOMY",
]

# A small, illustrative map from common COCO-80 labels to fabric taxonomy ids.
# Real deployments resolve these against OriginTrail / the Web; this is enough to
# give the CNN/LLM stages a stable class id to hang WHO/HOW priors on.
COCO_TO_TAXONOMY: dict[str, str] = {
    "person": "otkg:agent/person",
    "bicycle": "otkg:vehicle/bicycle",
    "car": "otkg:vehicle/car",
    "motorcycle": "otkg:vehicle/motorcycle",
    "bus": "otkg:vehicle/bus",
    "truck": "otkg:vehicle/truck",
    "traffic light": "otkg:infrastructure/traffic_light",
    "bench": "otkg:furniture/bench",
    "backpack": "otkg:gear/backpack",
    "bottle": "otkg:container/bottle",
    "cup": "otkg:container/cup",
    "chair": "otkg:furniture/chair",
    "couch": "otkg:furniture/couch",
    "potted plant": "otkg:plant/potted",
    "bed": "otkg:furniture/bed",
    "dining table": "otkg:furniture/table",
    "tv": "otkg:device/tv",
    "laptop": "otkg:device/laptop",
    "mouse": "otkg:device/mouse",
    "keyboard": "otkg:device/keyboard",
    "cell phone": "otkg:device/phone",
    "book": "otkg:media/book",
    "clock": "otkg:device/clock",
}


# ---------------------------------------------------------------------------
# Result → Detection conversion (pure; unit-testable without torch)
# ---------------------------------------------------------------------------

def _scalar(value) -> float:
    """Extract a python scalar from a torch/numpy 0-d or 1-elem value, or a number."""
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return float(item())
        except (ValueError, TypeError):
            pass
    try:
        return float(list(value)[0])   # 1-element sequence / tensor
    except (TypeError, IndexError, ValueError):
        return float(value)


def _row4(value) -> list[float]:
    """Extract [x1, y1, x2, y2] from an (N,4)-row, a (4,) vector, or a nested seq."""
    try:
        first = value[0]
        seq = [float(x) for x in first]      # value is (1,4) / (N,4): first row
        if len(seq) >= 4:
            return seq[:4]
    except (TypeError, IndexError, ValueError):
        pass
    return [float(x) for x in list(value)[:4]]   # value is already (4,)


def _label_for(names, cls: int) -> str:
    if isinstance(names, dict):
        return str(names.get(cls, cls))
    try:
        return str(names[cls])
    except (IndexError, KeyError, TypeError):
        return str(cls)


def detections_from_result(result, *, min_confidence_bps: int = 0) -> list[Detection]:
    """Convert one ultralytics ``Results`` object into :class:`Detection` list.

    Confidence is quantised to integer basis points (``conf * 10000``) and the box
    to integer source pixels — the observation layer forbids floats near the hash,
    so the conversion happens here, at the boundary, once.
    """
    names = getattr(result, "names", {}) or {}
    boxes = getattr(result, "boxes", None)
    out: list[Detection] = []
    if not boxes:
        return out
    for box in boxes:
        cls = int(_scalar(box.cls))
        conf = _scalar(box.conf)
        bps = max(0, min(CONF_FULL, int(round(conf * CONF_FULL))))
        if bps < min_confidence_bps:
            continue
        x1, y1, x2, y2 = _row4(box.xyxy)
        x, y = max(0, int(round(x1))), max(0, int(round(y1)))
        w, h = max(0, int(round(x2 - x1))), max(0, int(round(y2 - y1)))
        out.append(Detection(label=_label_for(names, cls), confidence_bps=bps, bbox=(x, y, w, h)))
    return out


# ---------------------------------------------------------------------------
# The detector
# ---------------------------------------------------------------------------

class UltralyticsYOLODetector:
    """A YOLO detector that satisfies the :class:`Detector` protocol.

    ``model`` is anything ``ultralytics.YOLO`` accepts (a shipped weight name like
    ``"yolo11n.pt"``, a local ``.pt``/``.onnx`` path, …). The model is loaded lazily
    on the first :meth:`detect` so construction is cheap and import-safe.
    """

    def __init__(
        self,
        model: str = "yolo11n.pt",
        *,
        conf: float = 0.25,
        imgsz: int = 640,
        device: str | None = None,
        min_confidence_bps: int = 0,
    ) -> None:
        self.model_name = model
        self.conf = conf
        self.imgsz = imgsz
        self.device = device
        self.min_confidence_bps = min_confidence_bps
        self._model = None  # lazily constructed

    # -- lazy heavy deps ---------------------------------------------------

    def _ensure_model(self):
        if self._model is None:
            try:
                from ultralytics import YOLO
            except ImportError as exc:  # pragma: no cover - depends on optional dep
                raise ImportError(
                    "UltralyticsYOLODetector needs the 'vision' extra: "
                    "pip install 'knitweb[vision]'  (ultralytics + pillow)"
                ) from exc
            self._model = YOLO(self.model_name)
        return self._model

    @staticmethod
    def _decode(frame: bytes):
        """Decode encoded image bytes (JPEG/PNG) into an RGB PIL image."""
        try:
            from PIL import Image
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise ImportError(
                "decoding camera frames needs pillow: pip install 'knitweb[vision]'"
            ) from exc
        return Image.open(io.BytesIO(frame)).convert("RGB")

    # -- Detector protocol -------------------------------------------------

    def detect(self, frame: bytes) -> list[Detection]:
        """Run YOLO on an encoded frame (JPEG/PNG bytes) → detections."""
        model = self._ensure_model()
        image = self._decode(frame)
        results = model.predict(
            image, conf=self.conf, imgsz=self.imgsz, device=self.device, verbose=False
        )
        dets: list[Detection] = []
        for r in results:
            dets.extend(detections_from_result(r, min_confidence_bps=self.min_confidence_bps))
        return dets
