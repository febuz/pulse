# Knitweb — project guide for agents

Knitweb is a Python **P2P crypto web** (never a "network"/"net"). It is a
credibly-neutral DePIN: p2p web-workers ("spiders") sell **verifiable GPU
compute** and weave a knowledge + resource **fabric**, complementing
[OriginTrail](https://github.com/origintrail)'s Decentralised Knowledge Graph and
anchoring/bridging to the major blockchains.

## Tokens (read this first — the old "FBR" framing is retired)

- **PLS ("pulses") is the pay-token.** You spend PLS for *activity* (compute,
  relay, storage, curation) — not for fibers or knits. Spiders earn PLS via
  proof-of-useful-work. No premine; mint is **demand-gated and bounded by the
  escrow it settles, plus an optional `max_supply` cap** (`token.mint`,
  `premine=0`). Binding a mint cap to a Pulse Beat/epoch is a planned future
  wiring, not yet implemented.
- **"Fiber" is the brand coin / value unit — but the `Fiber` *primitive* is not a
  transferable coin.** A `Fiber` is an immutable, content-addressed **account-state
  commitment** (one link in a `Braid`). The transferable value is an integer
  balance of a *symbol* (native symbol = `"PLS"`) moved between accounts by a
  `Knit`; Fibers themselves are never transferred. Value-unit slang is a *fiber*
  (NL *vezel*); never reintroduce the name "FIBRE".
- **FBR is reserved and not active** (a possible separate/regional token later). Do
  not use it as the active token in new code or docs.

## Non-negotiables

- **Crypto = secp256k1 ECDSA + SHA-256** (`knitweb.core.crypto`, via the
  `cryptography` lib). No Ed25519/BLAKE2b in the value path.
- **Money & state are integers** (wei-style base units). **No floats** anywhere
  near hashing, balances, or canonical encoding — `knitweb.core.canonical`
  rejects them.
- **Canonical bytes are sacred.** All hashing/signing goes through
  `core.canonical.encode` (float-free deterministic CBOR) + `core.canonical.cid`
  (CIDv1 dag-cbor sha2-256). Changing it changes every hash and signature.
- **Core primitives (seven):** `Blob`, `Fiber`, `Knitweb`, `Knit`, `Braid`, **`Web`**,
  **`Pulse`**. Workers are **spiders**. Vocabulary is **Web · Knit · Pulse
  · Fiber · knitweb** — never "network"/"net" (and never "loom").
- **The one allowed technical use of "network":** the `network` *id field* inside a
  signed `Knit` (an EIP-155-style chain id that namespaces a PLS web for replay
  protection). It is **hash-critical — never rename it**. Everywhere else in prose,
  say *web* / *fabric*, never "network"/"net".
  - *Also legitimate — do **not** flag these in a consistency sweep:* naming
    **external** networks in a comparison (the Akash/Filecoin/Livepeer/EigenLayer
    table in `docs/CRYPTO_CORPUS_STUDY.md`); the economics term "net" (e.g.
    "net-profitable"); the vocabulary **rule statement** itself; and
    identifiers/branch names such as `ledger-network-id`. The violation is only
    Knitweb-the-project described as "a network"/"net".
- **No founder premine.** Founders earn PLS like anyone and monetize only via side
  projects and the first user-issued tokens on the fabric.
- **Proofs-first.** Every phase ends with a runnable test + a commit + an
  `experiments/ledger.py` record (MLflow mirror best-effort). One pipeline; reuse
  files; delete superseded scripts.
- **Compute guardrail.** GPU work goes through `pouw/scheduler.py`; keep single
  experiments bounded (minutes, not hours) on whatever box is in use.
- **LOC record is generated, not tracked.** `python3 tools/loc_report.py` writes
  `docs/LOC_BY_LANGUAGE.md` on demand (gitignored, public-OSS-relevant files). Do
  not commit it — feature PRs must not touch it (it was a recurring merge-conflict
  source when each PR hand-edited the same generated lines).

## Layout & layers

See `README.md`. Layers: L0 core → L1 ledger → L2 p2p → L3 fabric (Web) →
L4 pouw → L5 knitwebs → L6 token. Domain knitwebs (incl. MOLGANG chemistry) are L5
plugins, never in core.

## Docs

- `docs/SYNAPTIC_WEB.md` — Fiber, the Synaptic Web, the edge bytecode compiler,
  and the OriginTrail symbiosis (the authoritative token note lives here too).
- `docs/research/08-knitweb.md` — the **KnitWeb concept paper**: the coined word
  *knitweb* (beside blockchain and hashgraph), the pulses/draft compute layer over
  donated GPU/RAM, the blockchain + hashgraph + knitweb cooperation for the MOLGANG
  P2P game, and the OriginTrail interlock.
- The per-language LOC record is generated on demand by `tools/loc_report.py`
  (`docs/LOC_BY_LANGUAGE.md`, gitignored — not version-controlled).

## Test

```bash
PYTHONPATH=src python3 -m pytest tests/property -q   # core proofs (fast)
```

An earlier JS prototype exists and is *reference-only* (read for semantics; do not
port byte-for-byte).
