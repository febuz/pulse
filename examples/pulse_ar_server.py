#!/usr/bin/env python3
"""Pulse AR edge/spider node — real YOLO over HTTP for a headset client.

This is the compute node a thin AR headset (Meta Quest 3S, phone, smartglass) talks
to. It runs a real **ultralytics YOLO** model behind the Pulse AR
:class:`VisionPipeline`, signs each detection into an :class:`ObjectObservation`,
publishes it to the bitchat mesh, and answers the headset with the objects in view —
WHAT / WHO / WHERE / HOW / DEVICE plus the source-pixel bbox to place each label.

Run:
    pip install 'knitweb[vision]'                     # ultralytics + pillow (first run downloads weights)
    PYTHONPATH=src python3 examples/pulse_ar_server.py --host 0.0.0.0 --port 8008

Then point a client at ``http://<this-machine-ip>:8008``:
  * open that URL in a phone/laptop browser for the built-in webcam demo, or
  * set it as the server URL in the Quest 3S client (see ``clients/quest3s/``).

Without the ``vision`` extra the node still starts, but falls back to the
deterministic text-frame stub detector (only the synthetic demo frames detect
anything) — install the extra for real camera detection.

Endpoints:
  GET  /            → the webcam browser client (examples/pulse_ar_web/index.html)
  GET  /health      → node status (detector, device address, position)
  POST /observe     → body: JPEG/PNG frame; query: lat, lon, alt → detections JSON
  GET  /overlays    → current fused field-of-view overlays
  GET  /features    → inner-world-model feature set
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from knitweb.core import crypto
from knitweb.edge.pulse_ar import (
    ObservationService,
    PriorsLLM,
    PulseARGlass,
    StubYOLODetector,
    VisionPipeline,
)

_WEB_CLIENT = Path(__file__).resolve().parent / "pulse_ar_web" / "index.html"

# A few illustrative real-world priors (integer millimetres + a maker address) an
# LLM/knowledge-fabric lookup would supply. Keyed by COCO label (taxonomy == label
# when no fine-grained CNN stage is wired). Extend freely.
_DEMO_PRIORS = {
    "chair": {"width_mm": 600, "height_mm": 1100, "depth_mm": 620, "maker": "pls1maker_demo_furniture"},
    "laptop": {"width_mm": 304, "height_mm": 16, "depth_mm": 212, "maker": "pls1maker_demo_compute"},
    "bottle": {"width_mm": 70, "height_mm": 230, "depth_mm": 70},
    "cup": {"width_mm": 85, "height_mm": 95, "depth_mm": 85},
    "cell phone": {"width_mm": 72, "height_mm": 147, "depth_mm": 8},
    "tv": {"width_mm": 1230, "height_mm": 710, "depth_mm": 60},
    "book": {"width_mm": 150, "height_mm": 230, "depth_mm": 25},
    "person": {"height_mm": 1700},
}


def build_detector(model: str, conf: float, force_stub: bool):
    """Return (detector, description). Real YOLO if available, else the stub."""
    if not force_stub and importlib.util.find_spec("ultralytics") is not None:
        from knitweb.edge.pulse_ar import UltralyticsYOLODetector

        return UltralyticsYOLODetector(model, conf=conf), f"ultralytics YOLO ({model})"
    scene = {label: (0, 0, 100, 100) for label in _DEMO_PRIORS}
    reason = "forced" if force_stub else "ultralytics not installed — pip install 'knitweb[vision]'"
    return StubYOLODetector(scene), f"stub text-frame detector ({reason})"


def make_service(args) -> tuple[ObservationService, str]:
    detector, detector_desc = build_detector(args.model, args.conf, args.no_yolo)
    priv, pub = crypto.generate_keypair()
    pipeline = VisionPipeline(detector, enricher=PriorsLLM(_DEMO_PRIORS))
    glass = PulseARGlass(
        priv=priv, pub=pub, lat=args.lat, lon=args.lon,
        pipeline=pipeline, precision=args.precision,
    )
    return ObservationService(glass), detector_desc


def make_handler(service: ObservationService, detector_desc: str, owner: str):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # -- helpers -------------------------------------------------------
        def _send_json(self, obj, status=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(self, body, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a):  # keep the console quiet
            pass

        # -- routes --------------------------------------------------------
        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                if _WEB_CLIENT.exists():
                    self._send_bytes(_WEB_CLIENT.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._send_json({"error": "web client not found"}, 404)
            elif path == "/health":
                self._send_json({
                    "device": service.glass.device,
                    "detector": detector_desc,
                    "cell": service.glass.cell,
                })
            elif path == "/overlays":
                with lock:
                    self._send_json(service.overlays())
            elif path == "/features":
                with lock:
                    self._send_json(service.features())
            else:
                self._send_json({"error": "not found"}, 404)

        def do_POST(self):
            path = urlparse(self.path).path
            if path != "/observe":
                self._send_json({"error": "not found"}, 404)
                return
            length = int(self.headers.get("Content-Length", 0))
            frame = self.rfile.read(length) if length else b""
            q = parse_qs(urlparse(self.path).query)

            def _f(name):
                return float(q[name][0]) if name in q else None

            try:
                with lock:
                    resp = service.observe(
                        frame,
                        lat=_f("lat"), lon=_f("lon"), altitude_m=_f("alt"),
                        observed_at=int(_f("t") or 0),
                        owner=owner,
                    )
                self._send_json(resp)
            except Exception as exc:  # never let one bad frame kill the node
                self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 400)

    return Handler


def main() -> None:
    p = argparse.ArgumentParser(description="Pulse AR edge/spider node (real YOLO over HTTP)")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8008)
    p.add_argument("--model", default="yolo11n.pt", help="ultralytics model name or path")
    p.add_argument("--conf", type=float, default=0.25, help="YOLO confidence threshold")
    p.add_argument("--lat", type=float, default=52.3702, help="initial wearer latitude")
    p.add_argument("--lon", type=float, default=4.8952, help="initial wearer longitude")
    p.add_argument("--precision", type=int, default=6, help="geohash proximity precision")
    p.add_argument("--owner", default="", help="PLS address to stamp as object owner")
    p.add_argument("--no-yolo", action="store_true", help="force the stub detector")
    args = p.parse_args()

    service, detector_desc = make_service(args)
    handler = make_handler(service, detector_desc, args.owner)
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Pulse AR node up on http://{args.host}:{args.port}")
    print(f"  device:   {service.glass.device}")
    print(f"  detector: {detector_desc}")
    print(f"  open the URL in a phone/laptop browser, or point the Quest 3S client at it")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        httpd.shutdown()


if __name__ == "__main__":
    main()
