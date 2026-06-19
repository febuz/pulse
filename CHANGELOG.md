# Changelog

All notable changes to Knitweb. Versions are representative of implemented layers
(L0–L6), not a release cadence.

## Unreleased

### Added — governance: VoteBank, demographic supply, recency-weighted voting (`govern/`)
- **Demographic vote supply** (`govern/registry.py`) — one vote per registered person,
  counted **per world**: `max_vote_supply = Σ_world (registered_persons + expected_births)`.
  Two registration paths, both counted in the cap: **national identity** and a **freedom
  freeport** on-ramp (IMEI + email + ad-hoc proof of identity) for the unbanked/stateless.
  One-vote-per-person dedup worldwide; raw PII never stored (digests only).
- **VoteBank** (`govern/votebank.py`) — keeps the vote supply in treasury and issues it with
  **no premine**, bounded by the demographic cap, one-vote-per-person, fully auditable
  (`VoteIssuance` CIDs). Mirrors the native-PLS `Treasury` discipline.
- **Recency-weighted tally** (`govern/tally.py`) — when agents vote, **more recent votes
  weigh exponentially more**, via a float-free integer compound decay
  (`weight = weight * num // den` per beat of age, optional `horizon`). Enforces
  one-vote-per-subject, rejects future-dated votes, deterministic tie-break.
- Docs: `docs/GOVERNANCE_VOTEBANK.md`. Proofs: `tests/property/test_govern_votebank.py`
  (20 tests).

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

273 property/interop/loom proofs green.

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
- A repo-wide `loom → knitweb` rename is scheduled as a dedicated follow-up PR (see
  `docs/ROADMAP.md`); it is identifier/docs-only with no signed-record impact.

## 0.0.x — pre-history

Initial scaffolding and the phased L0–L3 build (see git history / merged PRs).
