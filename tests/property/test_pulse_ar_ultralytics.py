"""Proofs for the real-YOLO adapter and the ObservationService.

The heavy ultralytics/torch stack is optional, so these tests never import it:
the result→Detection conversion is exercised with a duck-typed fake ``Results``
(mirroring ultralytics' tensor-ish ``boxes``), and the service is driven by the
deterministic stub pipeline.
"""

import importlib.util

import pytest

from knitweb.core import crypto
from knitweb.edge.pulse_ar import (
    ObservationService,
    PriorsLLM,
    PulseARGlass,
    StubYOLODetector,
    TaxonomyCNN,
    UltralyticsYOLODetector,
    VisionPipeline,
    detections_from_result,
)

_HAS_ULTRALYTICS = importlib.util.find_spec("ultralytics") is not None


# --- result → Detection conversion ---------------------------------------

class _FakeBox:
    """Mimics one ultralytics box: cls/conf as 1-elem tensors, xyxy as (1,4)."""

    def __init__(self, cls, conf, xyxy):
        self.cls = cls
        self.conf = conf
        self.xyxy = xyxy


class _FakeResult:
    def __init__(self, names, boxes):
        self.names = names
        self.boxes = boxes


@pytest.mark.property
def test_detections_from_result_quantises_to_integers():
    result = _FakeResult(
        names={0: "person", 56: "chair"},
        boxes=[
            _FakeBox(cls=[0.0], conf=[0.9], xyxy=[[10.2, 20.8, 110.0, 220.0]]),
            _FakeBox(cls=[56], conf=[0.5], xyxy=[[0.0, 0.0, 50.0, 60.0]]),
        ],
    )
    dets = detections_from_result(result)
    assert [d.label for d in dets] == ["person", "chair"]
    # confidence quantised to integer basis points (no floats reach the hash)
    assert [d.confidence_bps for d in dets] == [9000, 5000]
    # xyxy → integer (x, y, w, h) source pixels
    assert dets[0].bbox == (10, 21, 100, 199)
    assert dets[1].bbox == (0, 0, 50, 60)


@pytest.mark.property
def test_detections_from_result_filters_low_confidence():
    result = _FakeResult(
        names={0: "person", 56: "chair"},
        boxes=[
            _FakeBox(cls=[0], conf=[0.90], xyxy=[[1, 1, 9, 9]]),
            _FakeBox(cls=[56], conf=[0.40], xyxy=[[1, 1, 9, 9]]),
        ],
    )
    dets = detections_from_result(result, min_confidence_bps=6000)
    assert [d.label for d in dets] == ["person"]


@pytest.mark.property
def test_detections_from_result_handles_empty():
    assert detections_from_result(_FakeResult(names={}, boxes=[])) == []
    assert detections_from_result(_FakeResult(names={}, boxes=None)) == []


@pytest.mark.property
@pytest.mark.skipif(_HAS_ULTRALYTICS, reason="only meaningful when the optional dep is absent")
def test_ultralytics_detector_reports_missing_dependency_clearly():
    det = UltralyticsYOLODetector()          # construction is cheap + import-safe
    with pytest.raises(ImportError) as exc:
        det.detect(b"not-a-real-frame")
    assert "vision" in str(exc.value)         # points at the right extra


# --- ObservationService over the stub pipeline ---------------------------

def _stub_glass():
    priv, pub = crypto.generate_keypair()
    pipeline = VisionPipeline(
        StubYOLODetector({"chair": (10, 20, 100, 200)}),
        TaxonomyCNN({"chair": ("office_chair", "otkg:furniture/chair")}),
        PriorsLLM({"otkg:furniture/chair": {
            "width_mm": 600, "height_mm": 1100, "depth_mm": 620, "maker": "pls1maker",
        }}),
    )
    return PulseARGlass(priv=priv, pub=pub, lat=52.37, lon=4.89, pipeline=pipeline, precision=5)


@pytest.mark.property
def test_service_observe_returns_full_ar_schema():
    svc = ObservationService(_stub_glass())
    resp = svc.observe(b"a chair", lat=52.37, lon=4.89, owner="pls1owner")
    assert resp["count"] == 1
    d = resp["detections"][0]
    assert d["what"] == "office_chair"
    assert d["dimensions_mm"] == [600, 1100, 620]      # HOW
    assert d["owner"] == "pls1owner" and d["maker"] == "pls1maker"  # WHO
    assert d["bbox"] == [10, 20, 100, 200]             # for headset overlay placement
    assert d["cid"] and d["device"].startswith("pls1")


@pytest.mark.property
def test_service_overlays_and_features_track_stored_observations():
    svc = ObservationService(_stub_glass())
    svc.observe(b"a chair", lat=52.37, lon=4.89)
    assert svc.overlays()["count"] == 1
    feats = svc.features()["features"]
    assert feats["office_chair"]["count"] == 1
