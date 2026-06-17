# Knitweb roadmap — backlog & sprint plan

Live status of the build, derived from the theory docs and the seeded backlog in
[`CRYPTO_CORPUS_STUDY.md`](CRYPTO_CORPUS_STUDY.md), [`SYNAPTIC_WEB.md`](SYNAPTIC_WEB.md)
and [`COLLECTIVE_INTELLIGENCE.md`](COLLECTIVE_INTELLIGENCE.md). This is the single
*live* tracker; the study docs remain the rationale. Work follows
[`MULTI_AGENT_WORKFLOW.md`](MULTI_AGENT_WORKFLOW.md): one branch + one reviewable
PR per increment, off the current `main`, in a disjoint lane.

## Build gate — essential features are research-gated

A backlog item classified **essential** ("Ess." = ★ below) MUST be preceded by a
**competing-environment research report** in [`docs/research/`](research/) that surveys how
existing blockchain / DePIN environments solve the same problem and concludes
**build vs adopt vs bridge** — so we don't build what already exists or isn't needed.
The report merges before (or with) the feature's first implementation PR.

- Essential = on the critical economic-security / credible-neutrality / settlement path.
- Non-essential = infra/perf/optional backends; no gate (still reviewed normally).
- Reports already on file: [`CRYPTO_CORPUS_STUDY.md`](CRYPTO_CORPUS_STUDY.md) covers the
  PoUW line (B3–B7, B9 — DePIN proof/escrow/quorum); [`research/09-finance-settlement.md`](research/09-finance-settlement.md)
  covers finance (B12, B13).

## Layer status

| Layer | Module | State |
|---|---|---|
| L0 core | `core/{canonical,crypto,pulse}.py` | ✅ implemented + property-tested |
| L1 ledger | `ledger/{blob,fiber,loom,knit,braid,node}.py` | ✅ incl. network-id anti-replay |
| L2 p2p | `p2p/{node,wire}.py` | ✅ stdlib-`asyncio` MVP |
| L3 fabric | `fabric/{web,items,feed,attest,spatial}.py` | ✅ signed feed + attestation |
| L4 pouw | `pouw/{job,escrow,digest,challenge,dispute}.py` | 🟡 determinism done; dispute window (#32); quorum/escrow-econ next |
| L5 looms | `looms/` | 🟡 chemistry + supply-chain + operational shipped; **finance is the focus** |
| L6 token | `token/mint.py` | 🟡 demand-gated bounded mint shipped (#17); per-epoch cap + access payment pending |
| app | `app/cli.py` | ✅ `knitweb` CLI + node daemon (#19) |
| store | `store.py` | ✅ durable node persistence (#18) |
| anchor | `anchor/` | ✅ notary-signed checkpoint receipts + local backend (#28) |

## Merged MVP milestones (parallel track)

Alongside the sprint plan below, an MVP integration track landed end-to-end on
`main` (each squash-merged, author-reported green; re-run the suite locally as the
repo has no CI):

| PR | Milestone | Lands |
|---|---|---|
| [#17](https://github.com/febuz/pulse/pull/17) | M4 token | `token/mint.py` — demand-gated bounded PLS mint |
| [#18](https://github.com/febuz/pulse/pull/18) | M3 store | `store.py` — durable canonical-CBOR persistence |
| [#19](https://github.com/febuz/pulse/pull/19) | M2 app | `app/cli.py` — runnable node + wallet CLI |
| [#20](https://github.com/febuz/pulse/pull/20) | M5 demo | `examples/mvp_demo.py` — end-to-end acceptance |
| [#25](https://github.com/febuz/pulse/pull/25) | L5 operational | `looms/operational` — signed capacity allocations |
| [#28](https://github.com/febuz/pulse/pull/28) | anchors | `anchor/` — notary-signed checkpoint receipts |

## Consolidated backlog

"Ess." ★ = essential (research-gated, see build gate above).

| # | Item | Ess. | Report | Sprint | PR |
|---|---|:--:|---|---|---|
| B1 | Versioned address/key scheme byte (PQ soft-fork hedge) | ★ | CCS §3 | 1 | [#22](https://github.com/febuz/pulse/pull/22) ✅ |
| B2 | chainID/network in signed Knit | ★ | CCS §3 | — | shipped (`ledger/knit.py`) |
| B3 | PoUW digest-determinism: tolerance digest + commit-before-sample | ★ | CCS §1 | 1 | [#24](https://github.com/febuz/pulse/pull/24) ✅ |
| B4 | PoUW dispute window (release-delay > dispute, slash pending) | ★ | CCS §1 | 2 | [#32](https://github.com/febuz/pulse/pull/32) |
| B5 | k-of-n verifier quorum (+ declared-vs-detected asymmetry) | ★ | CCS §1 | 2 | `pouw/verifier-quorum` |
| B6 | Collateral sizing + winning-ticket/streaming escrow | ★ | CCS §1 | 2 | `pouw/escrow-economics` |
| B7 | Register synaptic compile/serve as a PoUW job class | ★ | CCS §1 + SYNAPTIC_WEB | 3 | `pouw/synaptic-job-class` |
| B8 | PLS mint: demand-gated, bounded, no-premine | ★ | CCS §1 | — | shipped [#17](https://github.com/febuz/pulse/pull/17) ✅ |
| B9 | Per-epoch mint cap + 1-pulse/bundle access payment | ★ | CCS §1 + SYNAPTIC_WEB | 3 | `token/pls-mint` |
| B10 | Partial-range Merkle proofs over the wire (Hypercore-style) | · | CCS §2 | 3 | `p2p/partial-range-merkle` |
| B11 | py-libp2p / DHT / pubsub optional backend | · | CCS §2, DEP | later | _gated on sanctioned install_ |
| B12 | **Finance loom** — double-entry audit journal over existing settlement | ★ | [research/09](research/09-finance-settlement.md) ✅ | now | `looms/finance` |
| B13 | Bind operational allocation → priced `ResourceItem`/settlement CID | ★ | [research/09](research/09-finance-settlement.md) ✅ | next | _follow-up_ |

_CCS = `CRYPTO_CORPUS_STUDY.md`; DEP = `DEPENDENCY_READINESS.md`._

## Current focus — finance loom (B12)

Per the owner: everything except finance is covered, so finance is first focus. The
research gate is satisfied ([research/09](research/09-finance-settlement.md)) and concludes a
**minimal scope**: a `looms/finance` plugin that signs double-entry journal entries
(`sum(postings) == 0`, integer-only, signed, content-addressed) with an optional
`settles` reference to the Knit/escrow settlement and/or priced `ResourceItem` — reusing
all existing value-movement primitives, building only the missing audit layer.

## Sprint 1 — pre-mainnet hardening + close the existential PoUW gap ✅

| PR | Lane | Status |
|---|---|---|
| [#22](https://github.com/febuz/pulse/pull/22) versioned address scheme | core | merged |
| [#24](https://github.com/febuz/pulse/pull/24) PoUW determinism foundations | pouw | merged |

## Sprint 2 — PoUW verification economics (the DePIN heart)

Builds on #24's challenge verdict. Spec: [`PROOF_OF_USEFUL_WORK.md`](PROOF_OF_USEFUL_WORK.md) §4.4.
All three are essential; their research report is `CRYPTO_CORPUS_STUDY.md` §1.

| PR | Lane | Scope | Depends on |
|---|---|---|---|
| [#32](https://github.com/febuz/pulse/pull/32) `pouw/dispute-window` | pouw | `slashable_until = submit_beat + dispute_window`; release-delay > window; slash pending | #24 |
| `pouw/verifier-quorum` | pouw | k-of-n aggregate verdict (~55% confirm, ~33%-adversary tolerant); declared-vs-detected fault asymmetry | #32 |
| `pouw/escrow-economics` | pouw | collateral ≥ one settlement window's payout-at-risk; winning-ticket/streaming probabilistic settlement | quorum |

## Sprint 3 — close the economic loop + L6

| PR | Lane | Scope | Depends on |
|---|---|---|---|
| `pouw/synaptic-job-class` | pouw | register synaptic compile/serve as a PoUW job class (re-execute deterministic compile) | Sprint 2 |
| `token/pls-mint` | token | per-Pulse-epoch mint bounding + wire 1-pulse-per-bundle access payment (core mint shipped in #17) | #17, Sprint 2 |
| `p2p/partial-range-merkle` | feed | partial-range Merkle proofs over the wire; DHT/pubsub stays an optional backend | — |

## Conventions

- Every PR: what/why + the proof (`PYTHONPATH=src pytest -q`, green count) + explicit
  review asks for the equal-level reviewer.
- **Essential features are research-gated** — see the build gate above.
- **Do not commit `docs/LOC_BY_LANGUAGE.md`** — it is generated on demand by
  `tools/loc_report.py` and gitignored (not version-controlled; see #29).
- No floats on the hash/balance/canonical path; integers (wei-style) only.
- Crypto stays secp256k1 ECDSA + SHA-256 via `cryptography`; canonical bytes via `core.canonical`.
