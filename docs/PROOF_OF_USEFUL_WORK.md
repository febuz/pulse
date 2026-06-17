# Proof-of-Useful-Work — the economic-security theory of Knitweb

> Coin: **Fiber** · Pay-token: **PLS** ("pulses") · Layer: **L4 (`pouw/`)**
>
> This is the authoritative design note for how Knitweb pays spiders for *real*
> work without trusting them. It consolidates the DePIN lessons mined in
> [`CRYPTO_CORPUS_STUDY.md`](CRYPTO_CORPUS_STUDY.md) §1 into a single security
> model, marks what is **implemented** vs **designed**, and is the spec the
> Sprint 2 PoUW PRs build against (see [`ROADMAP.md`](ROADMAP.md)).

## 1. Why this is the hard part

Knitweb's thesis (see [`COLLECTIVE_INTELLIGENCE.md`](COLLECTIVE_INTELLIGENCE.md))
is that PLS is an **access right to real hardware capacity**, not a speculative
instrument. That only holds if the web can *verify* a spider did the work it
claims — cheaply, without re-doing all of it, and without a trusted oracle. Every
other layer (ledger, feed, fabric) is determinism-critical but **local**: a node
can prove its own braid in isolation. PoUW is the one place where the web must
adjudicate a *remote* party's behaviour and move collateral on the verdict. Get
it wrong and you either slash honest workers (the network dies of attrition) or
pay fraudulent ones (the network dies of looting).

The model is **optimistic sampled re-execution + PLS escrow, slash on mismatch**.
No new issuance is involved (see §6): settlement is a conservation-preserving
Knit transfer of escrowed pulses, which is the sound subset we can prove today.

## 2. Soundness rests on determinism

Sampled re-execution means: a verifier independently redoes a *sample* of the
work and checks the result matches what the worker committed. This is only a real
check if the same input deterministically yields the same output. Two regimes:

- **Deterministic jobs** (e.g. the synaptic-compile job, `pouw/job.py`): compiling
  the same OriginTrail asset yields byte-identical bytecode. Exact-match on the
  content digest is correct and is what ships today. The heavy work stays off the
  ledger; only the integer verdict (`match? signature valid?`) touches settlement.
- **Non-deterministic jobs** (float/GPU kernels, future): raw-float digest
  equality is **fatal** — two honest GPUs produce bit-different floats for the
  same job, so exact-match silently slashes honest work. This is the #1
  existential risk in the corpus study.

## 3. Threat model

| Attack | What the adversary does | Defence | Status |
|---|---|---|---|
| **Honest-noise slash** | Two honest workers differ by float ULPs; exact-match flags one as fraud | Quantize to an `eps`-grid, digest the integers (no float reaches the hash) | ✅ `pouw/digest.py` (#24) |
| **Precompute** | Worker computes only the blocks it expects to be challenged | Fresh per-challenge salt drawn *after* the commit; indices unpredictable at commit time | ✅ `pouw/challenge.py` (#24) |
| **Retroactive work-swap** | Worker swaps in cheaper output after seeing which blocks are sampled | Domain-separated Merkle commitment published *before* the salt; sampled blocks must prove membership | ✅ `pouw/challenge.py` (#24) |
| **Second-preimage / tree forgery** | Forge a Merkle path by confusing leaf and internal nodes | `\x00`/`\x01` leaf/node domain tags (avoids the CVE-2012-2459 shape) | ✅ `pouw/challenge.py` (#24) |
| **Withdraw-before-dispute** | Worker is paid, then withdraws before a verifier can re-execute and slash | Escrow release delay **strictly exceeds** the dispute window; slashing reaches pending withdrawals | 📐 designed (Sprint 2) |
| **Single corrupt verifier** | One verifier whitewashes fraud or steals stake | **k-of-n** verifier quorum (~55% confirm, tolerate ~33% adversary) | 📐 designed (Sprint 2) |
| **Faked-digest batch** | Worker submits many fake proofs hoping few are sampled | Collateral ≥ one settlement window's payout-at-risk; faked batches are never net-profitable | 📐 designed (Sprint 2) |
| **Salt grinding** | A colluding verifier grinds the salt to a favourable sample | Beacon-seeded / commit-revealed salt rather than free verifier choice | 📐 designed (Sprint 2) |
| **Cross-web replay** | Replay a settlement Knit on another PLS web | `network` id bound into the signed Knit (EIP-155-style) | ✅ `ledger/knit.py` |

## 4. Layer by layer

### 4.1 Deterministic verification (shipped)
`pouw/digest.py` — `tolerance_digest(values, eps)` snaps each value to its integer
bucket `round(value/eps)` and hashes the **integers** via canonical CBOR. Outputs
within `eps` share a digest; genuinely different work mismatches. No float ever
reaches the hash. Bucket-boundary straddle is the inherent residual; for chaotic
kernels, fall back to hardware attestation. (Not yet wired into `job.py` — the
synaptic compile job is float-free and correctly stays exact-match.)

### 4.2 Commit-before-sample challenge (shipped)
`pouw/challenge.py` implements the four-step protocol, all CPU-deterministic with
O(k) verifier cost:

1. `commit(blocks)` → domain-separated Merkle `root` at submit time (output fixed
   before any salt exists).
2. `sample_indices(salt, n, k)` → k distinct indices from a SHA-256 counter stream
   over a fresh verifier salt.
3. `respond(blocks, salt, k)` → reveals the sampled blocks with Merkle membership
   proofs and salted digests `sha256(salt‖index‖block)`.
4. `verify_response(...)` → recompute indices, check positional order, salted
   digest (no precompute), and membership in the committed root (no work-swap).

### 4.3 Dispute, quorum & collateral (designed — Sprint 2)
The challenge protocol yields a *verdict*; this layer turns a verdict into safe
settlement timing and slashing.

- **Dispute window** (`pouw/dispute.py`). `slashable_until = submit_beat + dispute_window`.
  No escrow release before the window closes; a detected mismatch within the
  window slashes the worker's collateral *and* any pending withdrawal. The
  release delay must strictly exceed the dispute window (EigenLayer's 14-day rule
  in miniature).
- **k-of-n verifier quorum** (`pouw/quorum.py`). A job's verdict is the
  aggregate of n independent verifiers; settlement requires ≥ k confirmations.
  Sized so a ~33% adversarial minority can neither force a false slash nor
  whitewash fraud. Borrow the *declared-vs-detected* asymmetry: a worker may
  self-declare "can't finish slot X" for a small fee; full slash is reserved for
  verifier-detected silent mismatch.
- **Collateral sizing & winning-ticket escrow** (`pouw/escrow` extension).
  Settle only a random ~1/N of jobs on-chain (Livepeer-style probabilistic
  micropayments) to cut verifier cost; size collateral ≥ one settlement window's
  payout-at-risk so a faked-digest batch is never net-profitable.

## 5. The settlement boundary

Everything expensive (resolve, compile, GPU kernels, sampling) lives **off** the
settlement path. The Loom only ever sees integers and booleans: a verdict, a
collateral amount, a Knit. `pouw/escrow.py`'s `settle_on_verify` already enforces
this — it pays `pulses` from consumer to worker via a conservation-preserving
Knit **iff** the proof verifies, and pays nothing otherwise. This keeps the
trusted surface tiny and auditable (Szabo principle 82: code bugs destroy more
value than 51% attacks).

## 6. What is deliberately deferred

- **PLS issuance (mint).** No new PLS is minted by PoUW today; settlement only
  *transfers* escrowed pulses. A demand-gated, per-epoch-bounded mint
  (`mintable=false`, `premine=0`) is a separate L6 decision (see
  [`ROADMAP.md`](ROADMAP.md), Sprint 3 `token/pls-mint`). Escrow settlement is the
  sound subset provable now, independent of emission policy.
- **GPU producer.** `wgpu`/`juliacall` are not installable here
  ([`DEPENDENCY_READINESS.md`](DEPENDENCY_READINESS.md)); the proof model is proven
  CPU-deterministic first and GPU is a later producer plugin kept off the
  settlement path.

## 7. Status map

| Component | Module | Proof | State |
|---|---|---|---|
| Deterministic verify | `pouw/job.py` | `tests/property/test_pouw.py` | ✅ |
| Escrow settle-on-verify | `pouw/escrow.py` | `tests/property/test_pouw.py` | ✅ |
| Tolerance digest | `pouw/digest.py` | `tests/property/test_pouw_determinism.py` | ✅ #24 |
| Commit-before-sample | `pouw/challenge.py` | `tests/property/test_pouw_determinism.py` | ✅ #24 |
| Dispute window | `pouw/dispute.py` | _planned_ | 📐 Sprint 2 |
| k-of-n quorum | `pouw/quorum.py` | _planned_ | 📐 Sprint 2 |
| Collateral / winning-ticket | `pouw/escrow.py` (ext) | _planned_ | 📐 Sprint 2 |
| Synaptic compile as job class | `pouw/job.py` (ext) | _planned_ | 📐 Sprint 3 |
| PLS mint policy | `token/` | _planned_ | 📐 Sprint 3 |
