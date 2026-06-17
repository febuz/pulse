# Knitweb — project guide for agents

Knitweb is a Python P2P crypto network with native token **FBR**. Fresh build;
the JS prototype at `/media/knight2/EDS2/projects/knitnet/` is *reference only*
(read for semantics, do not port byte-for-byte).

## Non-negotiables

- **Token is FBR.** Value unit is a *fiber* (NL *vezel*). Never reintroduce the
  name "FIBRE". Core primitives: Blob, Fiber, Loom, Knit, Braid, **Web**, **Pulse**.
- **Crypto = secp256k1 ECDSA + SHA-256** (`knitweb.core.crypto`, via the
  `cryptography` lib). No Ed25519/BLAKE2b in the FBR path.
- **Money & state are integers** (FBR-wei). **No floats** anywhere near hashing,
  balances, or canonical encoding — `knitweb.core.canonical` rejects them.
- **Canonical bytes are sacred.** All hashing/signing goes through
  `core.canonical.encode` (float-free deterministic CBOR) + `core.canonical.cid`
  (CIDv1 dag-cbor sha2-256). Changing it changes every hash and signature.
- **No founder premine.** FBR genesis `mintable=false`, `premine=0`. Mint is
  demand-gated and bounded per Pulse epoch.
- **Proofs-first.** Every phase ends with a runnable test + a commit + an
  `experiments/ledger.py` record (MLflow mirror best-effort). One pipeline; reuse
  files; delete superseded scripts.
- **Compute guardrail.** Box is 96-core/640GB + 2×RTX 3090; no experiment may
  exceed ~3h. GPU work goes through `pouw/scheduler.py`.
- **Keep the LOC record current.** Run `python3 tools/loc_report.py` after adding
  source files; it writes `docs/LOC_BY_LANGUAGE.md` (public OSS-relevant files).

## Layout & layers

See `README.md`. Layers: L0 core → L1 ledger → L2 p2p → L3 fabric (Web) →
L4 pouw → L5 looms → L6 token. Domain looms are plugins, never in core.

## Test

```bash
PYTHONPATH=src python3 -m pytest tests/property -q   # core proofs (fast)
```

The plan of record is `/home/knight2/.claude/plans/now-look-at-all-indexed-hedgehog.md`.
