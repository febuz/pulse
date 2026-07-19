# Pulse AR — augmented reality over the bitchat BLE mesh

**Pay-token:** PLS (pulses) · **Vocabulary:** Web · Knit · Pulse · Fiber · Knitweb

Pulse AR is the physical-object companion to the Synaptic Web edge path
(`knitweb.edge.runtime` / `knitweb.edge.arglass`, which stream verified *relation*
bytecode). A pair of smartglasses runs a vision stack, turns what it sees into
signed, content-addressed **object observations**, and exchanges them with nearby
wearers over a **bitchat** Bluetooth Low Energy mesh — no cell, no Wi-Fi, no
infrastructure. Every peer verifies an observation *before* it can act on it.

The design answers, for each object in view, the five questions the request names:

| Field | Question | Where it comes from |
|---|---|---|
| **WHAT** | what is it? | YOLO detection → CNN fine-grained class + taxonomy id + confidence |
| **WHO** | whose is it, who made it? | LLM enrichment: `owner` + `maker` (PLS addresses) |
| **WHERE** | where is it? | geohash cell + integer altitude band (`fabric.spatial`) |
| **HOW** | how big is it? | LLM/priors: `width_mm × height_mm × depth_mm` (integer millimetres) |
| **DEVICE** | which device saw / exchanged it? | the observing glass's PLS address, bound to the signature |

## The pipeline (`knitweb.edge.pulse_ar.vision`)

Perception is three pluggable stages behind tiny `Protocol`s, so the value path
stays dependency-free and deterministic while real weights drop in unchanged:

```
frame bytes
   │  Detector.detect      →  YOLO head: coarse boxes (Detection: label, conf_bps, bbox)
   ▼
   │  Classifier.refine     →  CNN: (label, taxonomy_id, sharper conf)
   ▼
   │  Enricher.enrich       →  LLM: owner, maker, mm dimensions, fabric fiber_cid
   ▼
ObjectObservation  (tagged with WHERE = wearer geohash, DEVICE = wearer address)
```

Real integrations swap the stubs for `ultralytics` YOLO, a torch CNN, and a hosted
or on-device LLM (à la LeCun-style world models); `VisionPipeline` and the
canonical record do not change. A frame is passed as raw `bytes` so the core needs
no numpy/torch to move it, and the deterministic stubs hash those bytes so the same
frame always yields the same observations — which is what makes the proofs
reproducible.

## The observation record (`…pulse_ar.observation`)

`ObjectObservation` is a frozen dataclass grouped exactly as WHAT / WHO / WHERE /
HOW / DEVICE. It obeys the project non-negotiables:

- **No floats near the hash.** Confidence is integer basis points (0..10000),
  dimensions are integer millimetres, the bbox is integer pixels. The `geohash`
  helper uses floats only transiently to derive the stored *string*.
- **Content-addressed.** `to_record()` → canonical float-free CBOR → a CIDv1
  (`cid`). `from_record()` is its exact inverse.
- **Verify-before-trust.** `SignedObservation` carries the observation, the
  originator public key, and a secp256k1/SHA-256 signature. `verify()` is strict:
  the signature must be valid **and** the signing key must hash to the `device`
  address the observation claims — so a peer can neither forge a `device` it has no
  key for nor relabel someone else's observation as its own.

## The bitchat mesh (`…pulse_ar.bitchat`)

A faithful-but-minimal BLE mesh transport carrying opaque signed payloads:

- **Fragmentation** — a payload is split into `BitchatFrame`s ≤ the BLE MTU
  (~180 B) and reassembled by `(msg_id, index, total)`. `msg_id` is
  `sha256(origin | payload)`, so every fragment of a message is bound to its
  content and reassembly is checked against it.
- **TTL-bounded store-and-forward flood** — each node relays a fragment to its
  other peers, decrementing a hop `ttl`. A message crosses many hops with no
  routing table; `ttl` bounds how far it travels.
- **Dedup** — each node relays a given `(msg_id, index)` exactly once, so a cyclic
  topology can never start a broadcast storm; the flood always terminates,
  independent of `ttl`.

The mesh is deliberately dumb: it neither signs nor inspects the payload. Trust is
the envelope's job and the decision happens where the data is consumed. The
in-memory `MeshNode` links deliver synchronously for tests/demos; a real BLE driver
overrides `MeshNode._broadcast` to write `frame.to_bytes()` to a GATT
characteristic — the relay/dedup/TTL logic is transport-agnostic and unchanged.

## The glass (`…pulse_ar.glass`)

`PulseARGlass` closes the loop, mirroring `edge.arglass.ARGlass` for the richer
object schema and adding the publish side:

1. **See + share** — `observe_and_share(frame)` runs the pipeline, signs each
   observation, and floods it over the mesh.
2. **Fuse** — the mesh callback verifies each incoming observation, drops the ones
   not anchored near the wearer (a coarse geohash-prefix test), and keeps the rest.
3. **Project** — `overlays()` returns the full WHAT/WHO/WHERE/HOW/DEVICE per object
   for the field of view; `features()` returns a compact, deterministic
   `label → {count, taxonomies, makers, owners, devices}` view that augments the
   inner world-model — the same collective-intelligence loop the edge runtime does
   for relations, now for physical objects.

## Where PLS fits

Pulse AR is the *consumption* edge of the Synaptic Web: the `taxonomy`/`fiber_cid`
links point back at fabric knowledge fibers a spider was paid **PLS** to weave and
verify. Sharing observations over BLE is free and infrastructure-less; paying for
the verified knowledge that enriches them (maker provenance, canonical dimensions)
is the metered, demand-gated work the token settles. No premine; value tracks use.

## Real YOLO + a headset

The `Detector` protocol takes a real model with no other change:
`knitweb.edge.pulse_ar.vision_ultralytics.UltralyticsYOLODetector` wraps
`ultralytics` YOLO (optional `vision` extra) and returns the same `Detection`s the
stub does, quantising YOLO's float confidence/box to basis points and integer
pixels at the boundary. `service.ObservationService` wraps a `PulseARGlass` in a
JSON request/response, and `examples/pulse_ar_server.py` serves it over HTTP for a
thin headset client. See [`QUEST3S_AR.md`](QUEST3S_AR.md) for the full Meta Quest 3S
case (Passthrough Camera API → node → world-anchored labels) and the browser webcam
demo at `examples/pulse_ar_web/`.

## Run it

```bash
PYTHONPATH=src python3 -m pytest tests/property/test_pulse_ar.py tests/property/test_pulse_ar_ultralytics.py -q
PYTHONPATH=src python3 examples/pulse_ar_demo.py                      # two-glass mesh demo

pip install 'knitweb[vision]'                                        # real YOLO
PYTHONPATH=src python3 examples/pulse_ar_server.py --host 0.0.0.0 --port 8008
# then open http://<ip>:8008 in a browser, or point clients/quest3s/ at it
```
