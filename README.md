# Knitweb

A peer-to-peer crypto **web** with native token **FBR** (coin: *Fiber*). Knitweb
is a credibly-neutral DePIN where p2p web-workers ("spiders") sell **verifiable
GPU compute** and weave a knowledge + resource **fabric** — a *Synaptic Web* whose
verified relations compile to edge-executable bytecode (see
[`docs/SYNAPTIC_WEB.md`](docs/SYNAPTIC_WEB.md)). Vocabulary is **Web · Loom · Knit
· Pulse · Fiber** — never "network"/"net". It complements
[OriginTrail](https://github.com/origintrail)'s Decentralised Knowledge Graph and
anchors/bridges to the major blockchains.

## What makes it unique — and sound

- **Resource coordination, not consensus-only.** The token is an *access right*
  to real hardware capacity (GPU compute first), not a speculative instrument.
- **Spiders weave the fabric.** P2P workers crawl the Web to find funded demand,
  perform useful work (GPU jobs, validation, curation), and earn FBR.
- **Proof-of-Useful-Work with sampled re-execution.** A fraction of every
  worker's proofs are independently re-run by peers; mismatches are slashed.
- **No founder premine.** FBR genesis is `mintable=false`, `premine=0`. Founders
  earn FBR like anyone and monetize only via side projects and the *first*
  user-issued ERC20-like tokens on the fabric.
- **Tiny deterministic surface.** Money and state are integers (FBR-wei),
  encoded as float-free canonical CBOR, so every client agrees byte-for-byte.

## The seven core primitives

`Blob` (account state) · `Fiber` (content-addressed value unit) · `Loom`
(validation) · `Knit` (two-party transfer) · `Braid` (local history) ·
**`Web`** (the woven global graph) · **`Pulse`** (the network heartbeat).

## Architecture (layers)

| Layer | Module | Language |
|---|---|---|
| L0 core | crypto (secp256k1 ECDSA + SHA-256), canonical CBOR, CID | Python |
| L1 ledger | blob / fiber / loom / knit / braid / node (integer FBR) | Python |
| L2 p2p | py-libp2p signed feeds + DHT | Python |
| L3 fabric | Web + items + agent / scorer / masterdata | Python |
| L4 pouw | proof-of-useful-work, sampled re-execution | Python + Julia + WGSL |
| L5 looms | finance / operational / supply-chain / chemistry | Python (+Julia) |
| L6 token | FBR access token + user LoomTokens + anchors | Python |

## Status

Phase 0 (core crypto + canonical encoding + Pulse + Web) is implemented and
property-tested. See [`docs/`](docs/) for the language-architecture decisions and
[`docs/LOC_BY_LANGUAGE.md`](docs/LOC_BY_LANGUAGE.md) for the per-language record.

## Develop

```bash
PYTHONPATH=src python3 -m pytest tests/property -q   # fast core proofs
python3 tools/loc_report.py                          # refresh the LOC record
```

Requires Python ≥ 3.12 and `cryptography`. The hash-critical canonical encoder is
hand-rolled (zero external surface). License: Apache-2.0.
