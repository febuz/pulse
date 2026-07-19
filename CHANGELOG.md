# Changelog

All notable changes to Knitweb. Versions are representative of implemented layers
(L0–L6), not a release cadence.

## 0.6.0 — L0–L6 implemented

The crypto is built and operable end to end. Highlights:

- **L0 core** — secp256k1 ECDSA + SHA-256; strict float-free canonical CBOR + CIDv1
  (decode rejects non-canonical/truncated input); Pulse heartbeat; versioned `pls1`
  address scheme (PQ-migration-ready).
- **L1 ledger** — integer settlement core (blob/fiber/knitweb/knit/braid/node); PLS-wei
  balances; nonce + EIP-155-style `network`-id replay protection; conservation.
- **L2 p2p** — stdlib-`asyncio` signed-feed replication, conflict quarantine, two-party
  Knit handshakes, and peer-exchange discovery. (py-libp2p/DHT remain optional backends.)
- **L3 fabric** — Web (typed-edge graph), items, attestation, Hypercore-style signed feed,
  spatial index; **provenance queries** (origin→processing closure traversal).
- **L4 pouw** — sampled re-execution, commit-before-sample challenge, tolerance digests,
  escrow, and a compute guardrail (`pouw/scheduler.py`).
- **L5 knitwebs** — four domain knitwebs: chemistry, supply-chain, operational, finance.
- **L6 token** — native PLS demand-gated bounded mint (no premine, anti-replay); user
  tokens; OriginTrail anchor backend + checkpoint anchoring.
- **App** — `knitweb` CLI (wallet/node/pay/compile/verify-bundle/edge-load) with durable,
  restart-safe persistence and daemon auto-persist.
- **Gateway `/interpret`** — a strictly read-only delegation hook that forwards a query, a
  deep-copied `web_snapshot`, and the caller's `params` to a host-registered external Lens
  (`App.set_lens`), with no write path and **no** LLM/vector/graph-DB dependency added to Pulse.
  Answers `501` deterministically when no Lens is installed, and **contains any Lens exception**
  into a deterministic `502` `interpreter-error` contract (no leaked detail) — so the gateway
  keeps serving regardless of host-interpreter faults (see `docs/LENS_INTERPRET_ENDPOINT.md`).
- **USP** — the OriginTrail read↔write symbiosis proven end to end.

273 property/interop/knitweb proofs green.

### Consistency pass
- PoUW digest rule documented exactly as implemented: deterministic round-half-up
  `floor(value/eps + 0.5)` (was mis-stated as `round(value/eps)` in `pouw/digest.py`
  and `docs/PROOF_OF_USEFUL_WORK.md`).
- Synaptic bytecode compression claim corrected to the measured, reproducible ~58%
  vs `json.dumps` for the `tests/property/test_synaptic.py` toy (was "~24%").
- `pouw/job.py` issuance note updated: mint is shipped in `token/mint.py` (demand-gated,
  bounded), not "deferred"; this module only transfers escrow.

### Consistency pass — residual items closed
- The `### Net` heading in `docs/IDENTITY_AND_ACCOUNTS.md` (read against the "never
  net/network" rule) is now `### Bottom line` (#65).
- Sweep-coverage completed for the five docs the original pass skipped
  (`CRYPTO_CORPUS_STUDY`, `IDENTITY_AND_ACCOUNTS`, `COLLECTIVE_INTELLIGENCE`,
  `MULTI_AGENT_WORKFLOW`, `DEPENDENCY_READINESS`): checked against the PLS/Fiber/web/FBR
  + premine vocabulary gate — **no violations**. The remaining `network`/`net`/`FBR`
  hits are all legitimate (external-network comparison, the economics term, the rule
  statement, the `network` id field, branch names); these allowed uses are now spelled
  out in `CLAUDE.md` so future sweeps don't re-flag them.
- Repository home moving to `github.com/knitweb/pulse` (org `knitweb`, package `knitweb`);
  the `pulse` repo name is retained.
- The active token is **PLS**; the ticker **FBR is reserved and not active**.
- The repo-wide validator/plugin → `knitweb` rename has landed (see `docs/ROADMAP.md`);
  it was identifier/docs-only with no signed-record impact (parity suite byte-identical).

### Edge · Pulse AR — augmented reality over the bitchat BLE mesh
- New `knitweb.edge.pulse_ar` subpackage: smartglasses turn a camera frame into
  signed, content-addressed **object observations** and exchange them with nearby
  wearers over a **bitchat** Bluetooth Low Energy mesh — no infrastructure.
- Each observation answers **WHAT** (YOLO→CNN class + taxonomy + confidence),
  **WHO** (owner + maker PLS addresses), **WHERE** (geohash + alt band), **HOW**
  (integer-mm dimensions), and which **DEVICE** saw it — all integer/float-free,
  canonical CBOR, CIDv1, secp256k1-signed.
- `VisionPipeline` couples pluggable YOLO / CNN / LLM stages behind tiny protocols
  (deterministic dependency-free stubs ship; real weights drop in unchanged).
- `bitchat` mesh: BLE-MTU fragmentation + reassembly, TTL-bounded store-and-forward
  flood, `(msg_id, index)` dedup (no broadcast storms in cyclic topologies).
- `PulseARGlass` closes the loop: see + sign + share, then **verify-before-trust**,
  spatial-filter, and fuse peers' observations into field-of-view overlays and a
  deterministic feature set that augments the inner world-model. Complements the
  `edge.recognize` resolver (which maps inputs → CIDs) with the exchange layer.
- Proofs in `tests/property/test_pulse_ar.py`; demo in `examples/pulse_ar_demo.py`;
  design note in `docs/PULSE_AR.md`.

### Edge · Pulse AR — real YOLO + a Meta Quest 3S case
- `pulse_ar.vision_ultralytics.UltralyticsYOLODetector` wires real **ultralytics
  YOLO** behind the `Detector` protocol (optional `vision` extra); float confidence
  and boxes are quantised to basis points + integer pixels at the boundary, so the
  signed observation stays float-free. Heavy imports are lazy — the core never loads
  torch.
- `pulse_ar.service.ObservationService` wraps a `PulseARGlass` in a transport-
  agnostic JSON request/response (frame → sign → publish → detections).
- `examples/pulse_ar_server.py` — a stdlib-HTTP edge/spider node running real YOLO
  (graceful stub fallback); `examples/pulse_ar_web/` — a webcam browser client that
  runs the full loop immediately.
- `clients/quest3s/` — a native Unity client using Meta's **Passthrough Camera API**
  to feed the headset cameras to the node and render world-anchored WHAT/WHO/WHERE/
  HOW/DEVICE labels; architecture + deploy in `docs/QUEST3S_AR.md`.
- Proofs in `tests/property/test_pulse_ar_ultralytics.py` (result→Detection
  quantisation, confidence gating, missing-dep error, service schema).

## 0.0.x — pre-history

Initial scaffolding and the phased L0–L3 build (see git history / merged PRs).
