# Changelog

All notable changes to Knitweb. Versions are representative of implemented layers
(L0–L6), not a release cadence.

## 0.6.0 — L0–L6 implemented

The crypto is built and operable end to end. Highlights:

- **L0 core** — secp256k1 ECDSA + SHA-256; strict float-free canonical CBOR + CIDv1
  (decode rejects non-canonical/truncated input); Pulse heartbeat; versioned `pls1`
  address scheme (PQ-migration-ready).
- **L1 ledger** — integer settlement core (blob/fiber/loom/knit/braid/node); PLS-wei
  balances; nonce + EIP-155-style `network`-id replay protection; conservation.
- **L2 p2p** — stdlib-`asyncio` signed-feed replication, conflict quarantine, two-party
  Knit handshakes, and peer-exchange discovery. (py-libp2p/DHT remain optional backends.)
- **L3 fabric** — Web (typed-edge graph), items, attestation, Hypercore-style signed feed,
  spatial index; **provenance queries** (origin→processing closure traversal).
- **L4 pouw** — sampled re-execution, commit-before-sample challenge, tolerance digests,
  escrow, and a compute guardrail (`pouw/scheduler.py`).
- **L5 looms** — four domain looms: chemistry, supply-chain, operational, finance.
- **L6 token** — native PLS demand-gated bounded mint (no premine, anti-replay); user
  tokens; OriginTrail anchor backend + checkpoint anchoring.
- **App** — `knitweb` CLI (wallet/node/pay/compile/verify-bundle/edge-load) with durable,
  restart-safe persistence and daemon auto-persist.
- **USP** — the OriginTrail read↔write symbiosis proven end to end.

~250 property/interop/loom proofs green.

### Notes
- Repository home migrated to `github.com/knitweb/knitweb` (org = repo = package = `knitweb`).
- The active token is **PLS**; the ticker **FBR is reserved and not active**.
- A repo-wide `loom → knitweb` rename is scheduled as a dedicated follow-up PR (see
  `docs/ROADMAP.md`); it is identifier/docs-only with no signed-record impact.

## 0.0.x — pre-history

Initial scaffolding and the phased L0–L3 build (see git history / merged PRs).
