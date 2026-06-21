# Knitweb Pulse — Architecture

> A navigable overview of the pulse engine: what it is, the layered architecture as it
> stands, the end-to-end value flow, and the known gaps + continuation roadmap. Compiled
> from a full read of the tree; keep it updated as subsystems mature. Test state and exact
> line refs are point-in-time. Since compilation: **R1 "epoch-bound issuance" is shipped**
> (PR #196 — the Pulse now governs per-epoch supply), and **G2/R2 (the RED Erlay
> byte-budget test) is resolved** (main is green). Treat §4–§5 as a live gap/roadmap log.

---

## 1. What it is

Knitweb Pulse is a **pure-Python, stdlib-only P2P crypto "web"**: a content-addressed graph (the *Web*) of canonical-CBOR records, replicated over a Byzantine-resistant gossip mesh, whose economic substrate is an integer-only two-party settlement ledger (*Braid*/*Knit*/*Fiber*) and whose money supply is minted **only** as a demand-gated reward for *verified useful work* (PoUW). Every value that touches a hash, a signature, or a balance is an integer; canonical CBOR bytes are the single source of identity (CIDv1, dag-cbor/sha2-256); crypto is secp256k1+SHA-256. On top of this sit domain plugins (*knitwebs* — vBank governance, crowdfunding), a Sybil-resistant *personhood* gate, and an *interpret/Lens* reasoning lobe that distills the Web graph into signed relation bundles which themselves become PoUW jobs. The vocabulary is non-negotiable: **Web / Knit / Pulse / Fiber / knitweb** — never "network" or "loom".

---

## 2. The layered architecture as it actually stands

```
L6  accounting/token      token/mint.py (Treasury, EmissionPolicy, Issuance)
L5  knitwebs (plugins)    vbank/{poll,ranked,liquid,tally}, crowdfunding/{campaign,settlement}, {chemistry,finance,operational,supplychain}=stubs
L4  pouw                  job, verify, quorum, committee, sampling, challenge, dispute, collateral, escrow, scheduler, marketplace, digest
L3  fabric (the Web)      web, node(FabricNode), items, feed(+proof/multiproof), provenance, spatial(+index), attest, equivocation, jsonld
L2  p2p                   base_node, node(AsyncioP2PNode), transport, wire, kademlia, discovery, reconcile, anti_entropy, mesh, inventory,
                          reputation, identity, peer_identity_gate, policing, relay, addrbook, metrics
L1  ledger               blob, fiber, braid, knit, knitweb, node(AccountNode)
L0  core                 canonical, crypto, pulse
─── cross-cutting ───
    personhood (Lens/personhood)  gate, verifier, anchor, nullifier, pairwise, revocation, status_tree, records, errors
    interpret  (Lens)             retrieve, distill, quantize
    synaptic / anchor / edge / store / gateway / app
```

### Maturity heatmap (1 = stub, 5 = production-hardened)

| Subsystem | Layer | Maturity | Status | Evidence |
|---|---|---|---|---|
| **core.canonical** | L0 | **5** | ACTIVE | Strict RFC-8949 §4.2 decode; rejects non-minimal heads, unsorted/dup keys, floats, indefinite-length; `MAX_DEPTH=64`, `MAX_ITEMS=1_048_576` (`canonical.py:72,84`). `test_canonical_strict_decode.py`, `test_canonical_fuzz.py`. |
| **core.crypto** | L0 | **5** | ACTIVE | secp256k1 ECDSA + SHA-256, scheme-byte reserved for PQ, `pls1` base32 addresses. PQ path reserved-only. |
| **core.pulse** | L0 | **4** | ACTIVE (under-wired) | `Beat`/`Pulse` chained, injected-time, integer-only, `verify_chain()` (`pulse.py`). Not yet bound to mint windows or a consensus root — `pulse.py:7-11` says so explicitly. |
| **ledger** (blob/fiber/braid/knit/knitweb/node) | L1 | **5** | ACTIVE | Dual-sig Knit, EIP-155 network binding, conservation + overdraft + nonce-monotonic invariants, spent-knit guard. Local-only (no cross-peer fork detection). |
| **p2p transport/wire/base_node** | L2 | **5** | ACTIVE | `BaseNode` is the shared carrier seam for both node stacks (`base_node.py:51`); ban gate + sig penalty + frame-budget on one `_dispatch`. |
| **p2p reconcile/inventory/mesh/anti_entropy** | L2 | **4** | ACTIVE | Erlay set-reconciliation, gossipsub mesh, lazy inv→getdata→inv-data, anti-entropy backstop (`fabric/node.py:29-54`). 1 interop byte-budget test currently RED. |
| **p2p kademlia/discovery/identity/reputation/policing** | L2 | **4** | ACTIVE | DHT overlay, peer-identity gate, reputation decay per round. |
| **fabric.web** | L3 | **5** | ACTIVE | In-memory content-addressed graph, idempotent `weave`, typed `Edge`, deterministic `traverse` (`web.py`). |
| **fabric.node (FabricNode)** | L3 | **4** | ACTIVE | Live gossip peer; signed-record routing hardened against kind-flip partition (`node.py:127-154`, #163). Conflict-quarantine still out of scope here. |
| **fabric.feed/provenance/spatial/attest/equivocation** | L3 | **3–4** | ACTIVE (mixed) | feed proofs + provenance acyclicity used by distill; spatial index has tests; equivocation present but local. |
| **pouw.job/verify/quorum/committee/sampling/challenge** | L4 | **5** | ACTIVE | Sampled re-execution, BFT k-of-n quorum `⌊2n/3⌋+1` (`quorum.py:86-94`), committee selection, sample sizing. `test_pouw_quorum.py`. |
| **pouw.dispute/collateral/escrow** | L4 | **5** | ACTIVE | `release_delay > dispute_window` safety invariant enforced in ctor (`dispute.py:97-102`); slash/refund/release verdict space complete. |
| **pouw.scheduler/marketplace** | L4 | **3** | ADAPTER-mostly | Present + tested but not wired into a live epoch loop. |
| **token.mint (Treasury)** | L6 | **4** | ACTIVE (not epoch-bound) | No premine/no admin mint; coinbase Fiber + anti-replay digest set (`mint.py:104-172`). Bounded by escrow + optional `max_supply`, **NOT** by Pulse epoch. |
| **knitwebs/vbank** | L5 | **4** | ACTIVE | poll/ranked/liquid/tally; `test_vbank_end_to_end.py`, `test_vbank_liquid.py`. |
| **knitwebs/crowdfunding** | L5 | **4** | ACTIVE | campaign + settlement session; end-to-end tested. |
| **knitwebs/{chemistry,finance,operational,supplychain}** | L5 | **1** | STUB | `__init__.py` only — namespace placeholders. |
| **personhood** (gate/verifier/anchor/revocation/...) | Lens/PH | **4** | ACTIVE | One-person-one-scope nullifier, epoch-pinned non-revocation (`gate.py:97-145`); ticket decoupled from content sig (ZK seam). Verifier is an injected `PresentationVerifier` interface — adapter for a real VC/ZK backend. |
| **interpret** (retrieve/distill/quantize) | Lens | **3** | ACTIVE (float-quarantined) | Deterministic graph retrieval + gated distill (`retrieve.py`, `distill.py`); registered as SPLIT-verified PoUW job (`job.py:185-186`). Float touches `quantize.py`/`distill.py`/`web.py` — quarantined off the hash path (see Gap #3). |
| **synaptic/anchor/edge/store/gateway/app/sdk** | mixed | **2–3** | ADAPTER/SCAFFOLD | OriginTrail resolve+compile (deterministic, real); CLI/gateway thin; `sdk/__init__.py` near-empty. |

---

## 3. End-to-end data / value flow

A record from authoring to settlement to reasoning:

1. **Author → canonical CID.** A producer builds a record (Knit, Fiber, Beat, knowledge node, or `Edge`) as a plain dict. `core.canonical.encode` produces the sacred minimal bytes; `canonical.cid` yields the CIDv1. A Knit (`ledger/knit.py`) is **dual-signed** over `[from,to,symbol,amount,from_nonce,timestamp,network]` (signatures excluded from `to_record()`, EIP-155 network binding inside the signed bytes).

2. **Local settlement.** `AccountNode.propose/accept` runs the two-party handshake; `ledger.knitweb.validate_knit` checks positive-int amount, distinct parties, dual sigs, nonce match, no overdraft, exact value conservation. The Knit is recorded into a new `Fiber` appended to the sender's and receiver's `Braid` (`braid.weave` enforces seq+1, prev-CID link, spent-knit set, nonce monotonicity). Sender nonce increments; **receiver nonce does not** (`knitweb.py:98`).

3. **Authoring into the Web.** `FabricNode.weave(record)` content-addresses the record into the in-memory `Web` (idempotent), signs a domain-separated envelope (`_RECORD_TAG`, `node.py:91,944`), and stores the verbatim signed frame under the CID for byte-identical relay.

4. **p2p gossip / anti-entropy.** Propagation is lazy (#64): **inv-announce** (CID only) → peer replies **inv-getdata** with only the CIDs it lacks → announcer serves **inv-data** = the stored frame *verbatim*, so the inner record's CID is byte-identical across the hop. Target selection is the bounded gossipsub mesh (`_eager_targets`); reconnect uses **Erlay** set-reconciliation (`reconcile_with`, `ReconcileSession`) moving O(diff) not O(total); the **anti-entropy** `sync_from` loop is the unconditional convergence backstop. Every dial is ban-gated + per-peer byte/probe/recon-budgeted via `ServeBudget` on the single `BaseNode._dispatch` seam.

5. **Web ingest.** `FabricNode._ingest_signed` verifies the author signature over the signed bytes, then routes node-vs-edge **off the signed `record` itself** (`_is_edge_record`, `node.py:127-154`, #163) so a relayer cannot flip kind and partition the state root. A per-peer ingest budget throttles validly-signed floods before they consume memory. Convergence witness: identical `web_state_root`.

6. **PoUW verification.** A consumer escrows pulses for a job (`SynapticCompileJob`: compile an OriginTrail asset to signed bytecode). A worker `execute`s; verifiers re-execute. The **uniform** path (`verify`, `job.py:78-94`) re-compiles byte-for-byte and checks digest+signature. A committee is selected (`committee.select_committee`, worker excluded), sample size `k` is computed (`sampling.required_samples`), each verifier emits a `Verdict` (`verify.run_committee`), and `quorum.tally` aggregates to a BFT `⌊2n/3⌋+1` outcome (`quorum.py`). The **split** path (`distill`, `split_settles`, `job.py:332-351`) requires deterministic re-check AND a closed dispute window AND no upheld dispute.

7. **Settlement.** `DisputeWindowLedger.submit` opens a window with staked collateral; `dispute_by_quorum` slashes only on `DETECTED_FAULT`; `release` pays only at/after `release_beat` (strictly after the window, `dispute.py:97-102`). The actual PLS movement is a conservation-preserving Knit (`escrow.settle_on_verify`). **Issuance** then mints the bounded reward as a coinbase Fiber (`Treasury.reward_verified_work`, `mint.py:117-172`), anti-replayed by proof digest.

8. **Interpret / Lens reasoning.** `interpret.retrieve` does a deterministic, subscription-gated graph walk over the converged Web (`Web.traverse`/`neighbors` + optional spatial union + provenance ancestry). `interpret.distill` runs a bounded loop, **gates** every relation on attestation + acyclic provenance (`distill._gate_relation`), and emits a signed `Selection` → `DistillManifest` (`job.py:201-290`), which re-enters the PoUW flow as a SPLIT-verified job. Personhood (`personhood.gate.require_personhood`) is the orthogonal Sybil gate vBank/crowdfunding call before accepting a vote/pledge.

**The loop closes:** Web records → distilled into new signed relation bundles → become PoUW jobs → settle escrow → mint PLS → which is itself transacted as Knits woven back into the Web.

---

## 4. Top architectural gaps / tech-debt (ranked by severity)

**G1 — CRITICAL: Pulse is not bound to issuance or a consensus root.** The heartbeat exists and chains, but `token.mint` caps emission by escrow + optional `max_supply`, *not* by epoch. There is no epoch-scoped mint window, no per-epoch supply ceiling, and no Beat→fabric-state-root anchoring driving checkpoints. *Evidence:* `pulse.py:7-11` ("binding a mint cap to a Beat/epoch is a future wiring, not yet implemented"); `mint.py:68-76` reward bound has no epoch term. This is the load-bearing seam between "activity" and "money" and it is open.

**G2 — HIGH: One Erlay interop test is RED.** `test_live_byte_budget_throttles_a_hammering_peer` fails (`tests/interop/test_p2p_erlay_reconcile.py:538`). The per-peer byte-budget throttle on the live reconcile path does not currently enforce as asserted — a real anti-amplification regression in the L2 serve path. Must be triaged before any reconcile/mesh change lands. *(review-not-race — touches the parallel p2p session's surface.)*

**G3 — HIGH: Float in the interpret/Lens value path.** `quantize.quantize_weight` multiplies float `recency`/`pouw_score` by 1000 and truncates (`quantize.py:58-73`); `distill.py` and `web.py` accept floats. It is *deliberately quarantined* (the `int(x*1000)` boundary is the only float touch, and `Web._validate_metadata_value` even permits `float` edge metadata, `web.py:62`). But a float that reaches a *canonical* path is rejected at runtime, so this is a latent correctness/determinism cliff: a float leaking into a relation weight that gets canonicalised would raise mid-distill. The non-negotiable "no floats near canonical" is currently *defended by convention*, not by a type boundary.

**G4 — MEDIUM: Canonical decoder has no per-container length limits.** Only `MAX_DEPTH=64` and `MAX_ITEMS=1_048_576` exist (`canonical.py:72,84`). A shallow ~8 MiB frame of ~1M tiny objects decodes to a ~64× heap amplification on the event loop. The wire byte budget caps *input*, not *post-decode explosion*. No `MAX_STRING_LEN`/`MAX_ARRAY_LEN`.

**G5 — MEDIUM: Ledger fork/equivocation is local-only.** `Braid`/`Fiber` are acyclic histories with no `fork_height`/`equivocation_witness` field; a compromised key rewrites local history with no distributable proof. Fork detection is deferred to L2 but L0 carries no structure for it (`braid.py`, `fiber.py`).

**G6 — MEDIUM: Receiver-side has no flow control.** Receiver nonce never increments (`knitweb.py:98`), so an account can be spammed with incoming transfers with no L0–L1 rate limit.

**G7 — LOW: No EC-point validation.** `crypto.is_valid_hex` checks length+hex only, not curve membership; `Fiber.owner`/`Knit` pubkeys accept any well-formed 33-byte hex string.

**G8 — LOW: PQ migration is reserved-only.** Scheme-byte = 0 with `KNOWN_SCHEMES={0}`; no SPHINCS+/ML-DSA implementation, no rekey tooling, no timeline.

---

## 5. Continuation roadmap

Five coherent next increments. **LANE** = `solo` (core/canonical + pouw + interpret — buildable without racing the parallel p2p/fabric session) or `review-not-race` (p2p/fabric — coordinate/review only).

### ★ HIGHEST-LEVERAGE: R1 — Epoch-bound issuance (close the Pulse↔mint seam)
- **Rationale:** This is the one gap that severs the project's core thesis. Today "useful work" mints PLS, but the *heartbeat that defines activity* exerts zero control over supply. Binding `EmissionPolicy` to a `Beat(epoch)` — carry an `epoch_mint_cap` on the Beat, gate `Treasury.reward_verified_work` to per-epoch escrowed demand, settle mints atomically at the epoch boundary — turns Pulse from a decorative clock into the monetary governor. It unblocks every downstream economic story (vBank treasury policy, crowdfunding settlement cadence) and is the prerequisite for a Beat→state-root checkpoint. It is **buildable entirely within `core/pulse.py` + `token/mint.py` + `pouw`**, with no p2p surface, so it does not race the parallel session.
- **Layer:** L0 (pulse) + L6 (token), gated by L4 verdicts. **Effort:** medium. **LANE: solo.**
- *Why highest-leverage over the alternatives:* R2 fixes a test, R3/R4 harden defenses, R5 is future-proofing — all valuable, but only R1 completes the central architectural loop (activity→money) that the entire stack is built to express. Everything else is a guardrail around a loop that isn't closed yet.

### R2 — Fix the Erlay live byte-budget throttle
- **Rationale:** A RED interop test on an anti-amplification control is a correctness-and-security regression, not flakiness. Reconcile is the convergence path for large nets; an unbounded serve cost is a DoS. Triage `test_p2p_erlay_reconcile.py:538` against the `_recon_budget`/`ServeBudget` debit path in `fabric/node.py`.
- **Layer:** L2. **Effort:** low (diagnose-first). **LANE: review-not-race** — it sits squarely on the parallel p2p/fabric session's surface; review and hand off rather than racing edits.

### R3 — Make "no floats near canonical" a type boundary, not a convention
- **Rationale:** G3 is a determinism cliff defended only by discipline. Introduce an integer-only relation-weight type (or an explicit `to_canonical()` that rejects float at the interpret→fabric seam) so a float can never reach `canonical.encode` at runtime. Tighten `Web._validate_metadata_value` to drop `float` for any metadata that can be canonicalised. This protects the single most sacred invariant in the codebase at the exact layer (interpret/Lens) where floats legitimately originate.
- **Layer:** Lens (interpret) + L3 (web edge metadata). **Effort:** low–medium. **LANE: solo.**

### R4 — Per-container length budgets in canonical + EC-point validation
- **Rationale:** Two cheap, high-value hardening wins. Add `MAX_STRING_LEN`/`MAX_ARRAY_LEN` to `canonical._decode` (configurable; strict for untrusted wire) to kill the shallow-deep heap-amplification (G4). Add `crypto.validate_pubkey()` and call it in `Fiber.__post_init__`/`Knit.build` (G7). Both are pure-core, fully testable, and directly reduce the replication attack surface.
- **Layer:** L0. **Effort:** low. **LANE: solo.**

### R5 — Structural equivocation witness in the ledger (prep for distributed fork proofs)
- **Rationale:** G5 — give L0 the *shape* P2P needs: optional `fork_height` + `equivocation_witness` (CID of a conflicting Fiber at the same seq from the same owner) on `Fiber`, and a `Braid.weave` path that quarantines conflicting histories and persists a proof. Structuring this at L0 now lets the parallel p2p session gossip fork proofs deterministically later without a schema break. Ship the fields + local detection solo; defer the gossip wiring to the p2p lane.
- **Layer:** L1 (structure), consumed by L2. **Effort:** medium. **LANE: solo** for the L0 structure; the gossip half is **review-not-race**.

---

**ACTIVE vs adapter-only vs stubbed — quick reference:**
- **ACTIVE (production-grade):** core (canonical/crypto/pulse), full ledger, p2p base/transport/wire/reconcile/mesh/kademlia, fabric web/node, all pouw verification+dispute+collateral, token mint, vbank, crowdfunding, personhood gate.
- **ADAPTER-ONLY (real seam, pluggable backend):** `personhood.verifier.PresentationVerifier` (VC/ZK backend injected), `pouw.scheduler`/`marketplace` (present, not in a live epoch loop), synaptic/anchor/origintrail (deterministic resolve+compile, real but minimal), gateway/cli/sdk (thin).
- **STUBBED:** `knitwebs/{chemistry,finance,operational,supplychain}` (`__init__.py` only); PQ crypto (scheme-byte reserved, no implementation).

*Single highest-leverage next increment: **R1 — epoch-bound issuance**, because it is the only increment that closes the activity→money loop the whole stack exists to express, and it ships entirely in the solo (core/pouw/token) lane.*