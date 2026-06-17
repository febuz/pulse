# Knitweb roadmap — backlog & sprint plan

Live status of the build, derived from the theory docs and the seeded backlog in
[`CRYPTO_CORPUS_STUDY.md`](CRYPTO_CORPUS_STUDY.md), [`SYNAPTIC_WEB.md`](SYNAPTIC_WEB.md)
and [`COLLECTIVE_INTELLIGENCE.md`](COLLECTIVE_INTELLIGENCE.md). This is the single
*live* tracker; the study docs remain the rationale. Work follows
[`MULTI_AGENT_WORKFLOW.md`](MULTI_AGENT_WORKFLOW.md): one branch + one reviewable
PR per increment, off the current `main`, in a disjoint lane.

## Layer status

| Layer | Module | State |
|---|---|---|
| L0 core | `core/{canonical,crypto,pulse}.py` | ✅ implemented + property-tested |
| L1 ledger | `ledger/{blob,fiber,loom,knit,braid,node}.py` | ✅ incl. network-id anti-replay |
| L2 p2p | `p2p/{node,wire}.py` | ✅ stdlib-`asyncio` MVP |
| L3 fabric | `fabric/{web,items,feed,attest,spatial}.py` | ✅ signed feed + attestation |
| L4 pouw | `pouw/{job,escrow,digest,challenge}.py` | 🟡 determinism foundations done; economics next |
| L5 looms | `looms/` | 🟡 partial |
| L6 token | `token/` | ❌ PLS mint not started |
| anchor | `anchor/` | ❌ not started |

## Consolidated backlog

| # | Item | Source | Sprint | PR |
|---|---|---|---|---|
| B1 | Versioned address/key scheme byte (PQ soft-fork hedge) | CCS §3 | 1 | [#22](https://github.com/febuz/pulse/pull/22) ✅ |
| B2 | chainID/network in signed Knit | CCS §3 | — | already shipped (`ledger/knit.py`) |
| B3 | PoUW digest-determinism: tolerance digest + commit-before-sample + salt | CCS §1 | 1 | [#24](https://github.com/febuz/pulse/pull/24) ✅ |
| B4 | PoUW dispute window (release-delay > dispute, slash pending) | CCS §1 | 2 | `pouw/dispute-window` |
| B5 | k-of-n verifier quorum (+ declared-vs-detected asymmetry) | CCS §1 | 2 | `pouw/verifier-quorum` |
| B6 | Collateral sizing + winning-ticket/streaming escrow | CCS §1 | 2 | `pouw/escrow-economics` |
| B7 | Register synaptic compile/serve as a PoUW job class | SYNAPTIC_WEB | 3 | `pouw/synaptic-job-class` |
| B8 | PLS mint: demand-gated, per-epoch bounded, `mintable=false`/`premine=0` | tokens note | 3 | `token/pls-mint` |
| B9 | Wire PLS access payment to bundle delivery (1 pulse/bundle) | SYNAPTIC_WEB | 3 | `token/pls-mint` |
| B10 | Partial-range Merkle proofs over the wire (Hypercore-style) | CCS §2 | 3 | `p2p/partial-range-merkle` |
| B11 | py-libp2p / DHT / pubsub optional backend | CCS §2, DEP | later | _gated on sanctioned install_ |

_CCS = `CRYPTO_CORPUS_STUDY.md`; DEP = `DEPENDENCY_READINESS.md`._

## Sprint 1 — pre-mainnet hardening + close the existential PoUW gap ✅

| PR | Lane | Status |
|---|---|---|
| [#22](https://github.com/febuz/pulse/pull/22) versioned address scheme | core | review-approved |
| [#24](https://github.com/febuz/pulse/pull/24) PoUW determinism foundations | pouw | review-approved |

B2 (chainID) was found already implemented and dropped from the sprint. See
[`PROOF_OF_USEFUL_WORK.md`](PROOF_OF_USEFUL_WORK.md) for the theory #24 implements.

## Sprint 2 — PoUW verification economics (the DePIN heart)

Builds on #24's challenge verdict. Spec: [`PROOF_OF_USEFUL_WORK.md`](PROOF_OF_USEFUL_WORK.md) §4.3.

| PR | Lane | Scope | Depends on |
|---|---|---|---|
| `pouw/dispute-window` | pouw | `slashable_until = submit_beat + dispute_window`; release-delay > window; slash reaches pending withdrawals | #24 |
| `pouw/verifier-quorum` | pouw | k-of-n aggregate verdict (~55% confirm, ~33%-adversary tolerant); declared-vs-detected fault asymmetry | dispute-window |
| `pouw/escrow-economics` | pouw | collateral ≥ one settlement window's payout-at-risk; winning-ticket/streaming probabilistic settlement | quorum |

## Sprint 3 — close the economic loop + L6

| PR | Lane | Scope | Depends on |
|---|---|---|---|
| `pouw/synaptic-job-class` | pouw | register synaptic compile/serve as a PoUW job class (re-execute deterministic compile) | Sprint 2 |
| `token/pls-mint` | token | demand-gated, per-epoch-bounded PLS mint; wire 1-pulse-per-bundle access payment | Sprint 2 |
| `p2p/partial-range-merkle` | feed | partial-range Merkle proofs over the wire; DHT/pubsub stays an optional backend | — |

## Conventions

- Every PR: what/why + the proof (`PYTHONPATH=src pytest -q`, green count) + explicit
  review asks for the equal-level reviewer.
- Refresh `tools/loc_report.py` → [`LOC_BY_LANGUAGE.md`](LOC_BY_LANGUAGE.md) when adding source files.
- No floats on the hash/balance/canonical path; integers (wei-style) only.
- Crypto stays secp256k1 ECDSA + SHA-256 via `cryptography`; canonical bytes via `core.canonical`.
