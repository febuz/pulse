# Dependency readiness — what's actually installable on this box

Evidence-based check of the third-party packages the roadmap assumes, run against
the live environment (system CPython 3.12.3). **Bottom line: the heavy networking
and GPU deps the plan named are NOT available and cannot be casually installed, so
the next phases must lean on the standard library — the same "hand-rolled over heavy
deps" discipline already used in the core.**

## Probe result (2026-06-17)

| Package | Needed for | Status |
|---|---|---|
| `cryptography` | core signing (secp256k1/SHA-256) | **OK** 48.0.1 |
| `anyio` | async transport | **OK** |
| `asyncio` (stdlib) | async transport | **OK** |
| `grpc` | (optional RPC) | OK 1.76.0 |
| `libp2p` (py-libp2p) | **Phase 3** P2P | **MISSING** |
| `multiaddr` | Phase 3 addressing | **MISSING** |
| `trio` | py-libp2p's async backend | **MISSING** |
| `kademlia` | DHT discovery | **MISSING** |
| `noise` | transport encryption | **MISSING** |
| `wgpu` | **Phase 4** GPU producer (WGSL) | **MISSING** |
| `juliacall` | Phase 4 Julia/DLPack GPU | **MISSING** |
| `cbor2`, `coincurve`, `fastecdsa` | (already replaced) | MISSING — by design |

## Install constraint

The interpreter is **PEP 668 externally-managed** (`/usr/lib/python3.12/EXTERNALLY-MANAGED`):
even `pip install --user libp2p` fails with `externally-managed-environment`. Project
policy is **system Python, no venv**. So pulling in py-libp2p (which also drags in
`trio`, `multiaddr`, `noise`, `fastecdsa`, …) would require `--break-system-packages`
on a shared 96-core box — not acceptable. py-libp2p is also young and API-unstable.

## Recommendations (roadmap impact)

### Phase 3 — build the wire on **stdlib `asyncio`**, not py-libp2p
The feed core (`fabric/feed.py`, PR #10) is deliberately **transport-agnostic**: it
signs the Merkle tree-head and verifies entries against it regardless of how bytes
move. So Phase 3's MVP should be:

- **Transport:** `asyncio` streams (`asyncio.start_server` / `open_connection`) —
  always present, zero install. Frame messages as length-prefixed canonical CBOR
  (`core/canonical.py`).
- **Wire protocol:** `request{feed, range}` → `data{head, entries, merkle_nodes}`,
  verified with `feed.verify_entries` / `check_conflict` (already proven).
- **Discovery:** a minimal in-process / static-peer registry first; a real DHT is a
  later optional backend.
- **py-libp2p stays an *optional* backend** behind a transport interface, adopted
  only once a wheel install is validated in a sanctioned way. Don't block Phase 3 on it.

This keeps the `-m interop` proof (two Python nodes replicate a feed, a Knit
completes over the wire, both braids validate) achievable **today** with stdlib only.

**Implemented MVP:** `src/knitweb/p2p/` now provides this stdlib-`asyncio` proof
path: length-prefixed canonical-CBOR frames, full-feed sync against signed
Merkle heads, static peers, conflict quarantine, and a two-party Knit handshake.
py-libp2p remains an optional future backend rather than a blocker.

### Phase 4 — GPU producer needs an install decision or a CPU-deterministic proof
`wgpu`/`juliacall` are missing and PEP-668-blocked too. Options, in order of
preference for the *proof*:
1. Make the PoUW proof **CPU-deterministic** first (the synaptic-compile job already
   is — it re-executes a deterministic compile, no GPU needed). Ship the proof model
   end-to-end on CPU; treat GPU as a *producer plugin* added later.
2. If a real GPU kernel is required, validate a sanctioned install path (a dedicated
   venv for the GPU worker process only, kept off the settlement path) before
   committing the plan to `wgpu`/`juliacall`.

### General
Continue the **zero-heavy-dep** posture on the hash/signature/settlement path
(already: `cryptography` + hand-rolled CBOR). Any new dependency must be justified
against "can the stdlib do this deterministically?" — for canonical bytes, signing,
and async I/O, it can.
