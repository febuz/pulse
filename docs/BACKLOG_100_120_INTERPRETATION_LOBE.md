# Knitweb / Pulse Backlog #100–#120

This file materializes the backlog passed for the next implementation wave.

All stories follow the required conventions:
- Vocabulary stays on **web / knit / pulse / fiber / knitweb / spider / PLS**
- `settlement` / `mining` stage tagging in every PR description
- `originator + asset_cid` required on hash-critical outputs
- determinism as the core invariant for settlement stages

## Epic A — Interpretation lobe

### #100 — Deterministic structured retrieve
- Stage: `settlement`
- Scope/Lands: `interpret/retrieve.py` (new), `fabric/web.py`, `fabric/provenance.py`, `fabric/spatial_index.py`
- AC:
  - `retrieve(query, subscription) -> CandidateSet` uses only `Web.traverse`, `Web.neighbors`, `provenance.ancestry`, `SpatialIndex`.
  - subscription restricts all returned CIDs to subscribed scopes.
  - deterministic output order and contents for same `(query, subscription, web_state_cid)`.
  - returns minimal CIDs/records and lazy full-record fetch.

### #101 — Recursive distill controller
- Stage: `mining`
- Scope/Lands: `interpret/distill.py` (new)
- AC:
  - `distill(candidate_set, query, *, max_iters, mode="reflect")` bounded loop.
  - no prompt-concat path; uses programmatic candidate addressing.
  - produces `Selection` list[Relation] + source CIDs.
  - bounded by `max_iters`; logs loop/sub-call counts.
  - pluggable memory backend (default in-memory).

### #102 — Provenance-gate for distiller output
- Stage: `settlement`
- Scope/Lands: `interpret/distill.py`, `fabric/attest.py`
- AC:
  - emitted relations without proven provenance are dropped.
  - fabricated relation injection test rejects non-attested/unknown CIDs.
  - `asset_cid` + `originator` required/validated in bytecode guard.

### #103 — Distilled intermediates as content-addressed Web nodes
- Stage: `settlement`
- Lands: `interpret/distill.py`, `fabric/web.py`
- AC:
  - each sub-result woven via `Web.weave`, referenced by CID.
  - identical slice ⇒ identical intermediate CID (cache hit).
  - `distilled-from` edge added for ancestry/replay.

### #104 — Compile verified distill answer
- Stage: `settlement`
- Lands: `sdk/__init__.py`, `synaptic/bytecode.py`, `synaptic/origintrail.py`
- AC:
  - new `sdk.distill_bundle(query, subscription, originator_priv)` path.
  - existing `resolve_asset` remains unchanged.
  - decode/verify round-trip over gated relation-set.
  - deterministic digest independent of relation insertion order.

## Epic B — Split-verification PoUW

### #105 — Register distill PoUW job class
- Stage: `settlement`
- Lands: `pouw/job.py`, `pouw/scheduler.py`, `pouw/marketplace.py`
- AC: split verification policy + manifest (query/subscription/web_state_cid/bundle_cid/originator) + reward conditions.

### #106 — Re-execute retrieve + gate
- Stage: `settlement`
- Lands: `pouw/sampling.py`, `pouw/verify.py`, `pouw/digest.py`
- AC: sampled re-run of deterministic pieces, fail/slash on mismatch.

### #107 — Challenge window + relevance reputation
- Stage: `settlement`
- Lands: `pouw/challenge.py`, `pouw/dispute.py`, `p2p/reputation.py`
- AC: open/close challenge, vote resolve via quorum, split slash vs challenge loss, relevance-only punishments.

### #108 — Bounded self-reflective distill mode
- Stage: `mining`
- Lands: `interpret/distill.py`, `pouw/job.py`
- AC: `reflect` bounded default, `recurse` local-only, explicit budget exhaustion flag.

## Epic C — Reputation + weight + flywheel

### #109 — Reputation metadata as web annotations
- Stage: `mining`
- Lands: `fabric/jsonld.py`, `interpret/*`
- AC: statement-level metadata on relations/edges, non-provenance query ranking, no PII.

### #110 — Canonical reputation quantization
- Stage: `settlement`
- Lands: `interpret/quantize.py`, `synaptic/bytecode.py`
- AC: deterministic `quantize_weight(...) -> int`, bounded range, integer-only bytecode.

### #111 — Flywheel confirmation feedback
- Stage: `settlement`
- Lands: `interpret/*`, `fabric/attest.py`, `p2p/reputation.py`
- AC: confirmed/overturned outcomes mutate weights via signed feedback; replayable.

## Epic D — Settlement / mining boundary

### #112 — Stage tagging across pipeline
- Stage: mixed (`settlement/mining`)
- Lands: `interpret/*`, `pouw/*`, `stages.py`
- AC: each stage tagged; tests enforce exact settlement stage list.

### #113 — Enforce mining→settlement crossing contract
- Stage: `settlement`
- Lands: `interpret/*`, `store.py`, `fabric/attest.py`
- AC: raw model output cannot cross; only CID+originator+verdict crossing.

### #114 — Off-wire mining producers
- Stage: `mining`
- Lands: `interpret/producers/*`, `synaptic/*`, `anchor/origintrail.py`
- AC: adapter for arbitrary off-wire compute + example producer.

### #115 — One boundary: settlement = replication = PII
- Stage: `settlement`
- Lands: `p2p/*`, `store.py`, `docs/*`
- AC: non-settlement not replicated; PII blocks replication.

## Epic E — Backends + memory tiering

### #116 — Pluggable trap-1 backends
- Stage: `mining`
- Lands: `interpret/backends/*`
- AC: `RetrieveBackend` interface + InMemory/DAS/Vector, re-validation, no fabric writes.

### #117 — STM/LTM tiering with signed intent
- Stage: `settlement`
- Lands: `interpret/*`, `store.py`, `ledger/*`, `p2p/identity.py`
- AC: scratchpad non-persistent; signed intent promotes durable LTM.

## Epic F — Edge / AR / overlay

### #118 — Pluggable recognition resolver to CID
- Stage: `mining`
- Lands: `edge/arglass.py`, `edge/runtime.py`
- AC: `marker`, `scene`, `embedding` resolvers, no core/ledger logic.

### #119 — Verifiable AR overlays from bundles
- Stage: `settlement`
- Lands: `edge/arglass.py`, `synaptic/bytecode.py`, `sdk/*`
- AC: overlay rendered only after `verify_bundle`, tamper-rejection test.

### #120 — Durable anchor binding + ephemeral events
- Stage: `settlement`
- Lands: `fabric/spatial.py`, `fabric/spatial_index.py`, `store.py`
- AC: durable anchor binding only; recognition events non-persistent and non-replicated.
