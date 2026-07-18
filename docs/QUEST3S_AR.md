# Pulse AR on Meta Quest 3S — real YOLO, end to end

This is the working AR case for Pulse AR: a Meta Quest 3S sees the room, a real
**ultralytics YOLO** model names the objects, and the headset draws each one's
**WHAT / WHO / WHERE / HOW / DEVICE** anchored in passthrough — every observation
signed, content-addressed, and shared on the bitchat mesh.

## The split, and why it is the honest one

```
  Quest 3S  (thin glass)                     Edge node / spider  (real compute)
  ┌───────────────────────┐                  ┌────────────────────────────────────┐
  │ PCA passthrough camera │ ── JPEG frame ─▶ │ UltralyticsYOLODetector            │
  │ (Passthrough Cam API)  │                  │   → VisionPipeline (YOLO→CNN→LLM)  │
  │                        │                  │   → PulseARGlass.observe_and_share │
  │ world-anchored labels  │ ◀── JSON ─────── │   → sign + publish on bitchat mesh │
  │ WHAT/WHO/WHERE/HOW/DEV │  detections      │   (paid in PLS — useful work)      │
  └───────────────────────┘                  └────────────────────────────────────┘
```

Two hard platform facts shape this:

1. **YOLO can't run on the headset.** ultralytics is Python + PyTorch; the Quest's
   standalone Android runtime can't host it. So inference belongs on a nearby node —
   a laptop, phone, or edge box. In Knitweb terms that node is a **spider** selling
   verifiable GPU compute, and the headset pays **PLS** for it. The offload is not a
   workaround; it *is* the DePIN model.
2. **Only the Passthrough Camera API can read the Quest cameras.** PCA is a
   Unity/Android API (Quest 3 / 3S, Horizon OS v74+). WebXR in the Quest Browser
   cannot access passthrough pixels, so the headset client is native Unity, not a
   web page. (A browser page *can* still render the node's output — it just can't
   supply Quest-camera frames.)

## The pieces in this repo

| Piece | Path | Role |
|---|---|---|
| Real YOLO detector | `knitweb.edge.pulse_ar.vision_ultralytics` | ultralytics behind the `Detector` protocol; integer bbox + basis-point confidence |
| Service core | `knitweb.edge.pulse_ar.service` | frame → sign → publish → JSON, transport-agnostic + unit-tested |
| Edge node | `examples/pulse_ar_server.py` | stdlib HTTP server around the service; real YOLO with a stub fallback |
| Webcam client | `examples/pulse_ar_web/index.html` | phone/laptop demo of the same loop — works immediately |
| Quest 3S client | `clients/quest3s/` | Unity + PCA: camera in, world-anchored labels out |

## Run it

```bash
# 1. Edge node (the spider) — real detection needs the vision extra:
pip install 'knitweb[vision]'                    # ultralytics + pillow
PYTHONPATH=src python3 examples/pulse_ar_server.py --host 0.0.0.0 --port 8008

# 2a. See it now: open http://<node-ip>:8008 in a phone/laptop browser (webcam).
# 2b. On the headset: build clients/quest3s/ (see its README) and point it at the node.
```

Without the `vision` extra the node still runs, on the deterministic text-frame stub
detector, so the HTTP contract and the mesh path are exercisable anywhere; install
the extra to detect real camera frames.

## What stays true to Knitweb

- **Integers near the hash.** YOLO's float confidence and box are quantised at the
  boundary to basis points and source pixels; the signed observation is float-free.
- **Verify-before-trust.** Observations are secp256k1-signed and bound to the
  observing device's PLS address; a peer refuses any it can't verify before it ever
  reaches the field of view.
- **The mesh is dumb, the edge is smart.** bitchat just moves signed bytes; trust
  and meaning are decided where the data is consumed.

See [`PULSE_AR.md`](PULSE_AR.md) for the observation schema, the vision pipeline
protocols, and the bitchat mesh internals.
