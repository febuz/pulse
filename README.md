# Knitweb

A peer-to-peer crypto **web** whose pay-token is **PLS** ("pulses") and whose value
unit of connections is counted with **Fiber**. (The ticker **FBR** is reserved for a possible later
regional token — it is not the active token.) Knitweb is a credibly-neutral DePIN
where p2p web-workers ("spiders") sell **verifiable GPU compute** and weave a
knowledge + resource **fabric** — a *Synaptic Web* whose
verified relations compile to edge-executable bytecode (see
[`docs/SYNAPTIC_WEB.md`](docs/SYNAPTIC_WEB.md)). Vocabulary is **Web · Loom · Knit
· Pulse · Fiber** — never "network"/"net". It complements
[OriginTrail](https://github.com/origintrail)'s Decentralised Knowledge Graph and
anchors/bridges to the major blockchains.

The active implementation is pure Python. Legacy JavaScript material is archival
only and is not the runtime path for the protocol, CLI, or Molgang integration.

## What makes it unique — and sound

- **Resource coordination, not consensus-only.** PLS is an *access right* to real
  hardware capacity (GPU compute first), not a speculative instrument.
- **Spiders weave the fabric.** P2P workers crawl the Web to find funded demand,
  perform useful work (GPU jobs, validation, curation), and earn PLS.
- **Proof-of-Useful-Work with sampled re-execution.** A fraction of every
  worker's proofs are independently re-run by peers; mismatches are slashed.
- **No founder premine.** PLS genesis is `mintable=false`, `premine=0`. Founders
  earn PLS like anyone and monetize only via side projects and the *first*
  user-issued ERC20-like tokens on the fabric.
- **Tiny deterministic surface.** Money and state are integers (wei-style base
  units), encoded as float-free canonical CBOR, so every client agrees byte-for-byte.

## The seven core primitives

`Blob` (account balance state) · `Fiber` (content-addressed **account-state
commitment**) · `Loom` (validation) · `Knit` (two-party transfer) · `Braid` (local
history) · **`Web`** (the woven global graph) · **`Pulse`** (the web's heartbeat;
useful work is paid in **PLS**). Workers are **spiders**.

> *Fiber* is the brand coin, but the `Fiber` **primitive** is an immutable snapshot
> of one account's state (a link in its `Braid`) — it is never itself transferred.
> The transferable value is an integer balance of a *symbol* (native symbol = `PLS`)
> moved between accounts by a `Knit`.

## Architecture (layers)

| Layer | Module | Language |
|---|---|---|
| L0 core | crypto (secp256k1 ECDSA + SHA-256), canonical CBOR, CID | Python |
| L1 ledger | blob / fiber / loom / knit / braid / node (integer PLS-wei balances) | Python |
| L2 p2p | asyncio signed-feed sync + static peers; py-libp2p/DHT optional later | Python |
| L3 fabric | Web + items + agent / scorer / masterdata | Python |
| L4 pouw | proof-of-useful-work, sampled re-execution | Python |
| L5 looms | finance / operational / supply-chain / chemistry | Python |
| L6 token | PLS pay-token + Fiber value unit + user tokens + anchors | Python |

## Status

Implemented and property-tested end to end across **L0–L6**:
- **L0/L3** core crypto, float-free canonical CBOR + CIDv1, Pulse, Web;
- **L1** the integer settlement ledger (blob/fiber/loom/knit/braid/node, network-id replay protection);
- **L2** stdlib-`asyncio` signed-feed replication, conflict quarantine, two-party Knit handshakes, and peer-exchange discovery;
- **L4** proof-of-useful-work — sampled re-execution, commit-before-sample challenge, tolerance digests, escrow + a compute guardrail;
- **L5** four domain looms (finance, operational, supply-chain, chemistry);
- **L6** the PLS token (demand-gated bounded mint), plus OriginTrail anchoring and a provenance walker over the Web.

A `knitweb` CLI runs a node, pays PLS, and compiles / verifies / edge-loads signed
bytecode; node state persists across restarts. ~250 property/interop/loom proofs green.
See [`docs/`](docs/) for the language-architecture decisions and
[`docs/research/08-knitweb.md`](docs/research/08-knitweb.md) for the KnitWeb concept
paper (knitweb beside blockchain/hashgraph; the pulses/draft compute layer; the
blockchain + hashgraph + knitweb cooperation; the OriginTrail interlock). Run
`python3 tools/loc_report.py` for the per-language LOC record (generated on demand,
not version-controlled).

Feature descriptions are tracked in [`docs/FEATURES.md`](docs/FEATURES.md).
Hard engineering requirements are tracked in
[`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md).

## Develop

```bash
PYTHONPATH=src python3 -m pytest tests/property -q   # fast core proofs
python3 tools/loc_report.py                          # print the LOC record (untracked)
```

Requires Python ≥ 3.12 and `cryptography`. The hash-critical canonical encoder is
hand-rolled (zero external surface). License: Apache-2.0.

## Repo, org & package names

The canonical repository is **`github.com/knitweb/pulse`** and the Python package
is **`knitweb`** (`pip install knitweb`, `import knitweb`). The repository keeps
the name `pulse` while the package and protocol/brand are *Knitweb*: *Pulse*/PLS
is the pay-token, *Knitweb* is the protocol/brand.
