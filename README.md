# Knitweb

Knitweb is a peer-to-peer **web** for verifiable compute, traceable knowledge,
and shared resource coordination. Peers, hosts, providers, and p2p web-workers
("spiders") weave a knowledge + resource **fabric**: a *Synaptic Web* whose
verified relations compile to edge-executable bytecode (see
[`docs/SYNAPTIC_WEB.md`](docs/SYNAPTIC_WEB.md)). Vocabulary is **Web · Knit
· Pulse · Fiber · knitweb** — never "network"/"net". It complements
[OriginTrail](https://github.com/origintrail)'s Decentralised Knowledge Graph for
heavy artifact provenance.

The active implementation is pure Python. Legacy JavaScript material remains
reference-only and is not the runtime path for the protocol, CLI, or Molgang
integration. Owner direction is tracked in
[`docs/OWNER_DIRECTION.md`](docs/OWNER_DIRECTION.md).

## What makes it unique — and sound

- **Resource coordination, not consensus-only.** Pulse activity accounting is tied
  to real hardware capacity (GPU compute first), not speculation.
- **Spiders weave the fabric.** P2P workers crawl the Web to find funded demand,
  perform useful work such as GPU jobs, validation, curation, relay, and storage.
- **Proof-of-Useful-Work with sampled re-execution.** A fraction of every
  worker's proofs are independently re-run by peers; mismatches are penalized.
- **No privileged genesis allocation.** Founders participate like other web
  workers and should not receive special launch balances.
- **Tiny deterministic surface.** Money and state are integers (wei-style base
  units), encoded as float-free canonical CBOR, so every client agrees byte-for-byte.

## The seven core primitives

`Blob` (account balance state) · `Fiber` (content-addressed **account-state
commitment**) · `Knitweb` (validation) · `Knit` (two-party transfer) · `Braid` (local
history) · **`Web`** (the woven global graph) · **`Pulse`** (the web's heartbeat;
useful work is paid in **PLS**). Workers are **spiders**.

> `Fiber` is an immutable snapshot of one account's state (a link in its `Braid`).
> It is never itself transferred. Activity is represented as integer balances of
> a symbol such as `PLS`, moved between accounts by a `Knit`.

## Architecture (layers)

| Layer | Module | Language |
|---|---|---|
| L0 core | crypto (secp256k1 ECDSA + SHA-256), canonical CBOR, CID | Python |
| L1 ledger | blob / fiber / knitweb / knit / braid / node (integer PLS-wei balances) | Python |
| L2 p2p | asyncio signed-feed sync + static peers; py-libp2p/DHT optional later | Python |
| L3 fabric | Web + items + agent / scorer / masterdata | Python |
| L4 pouw | proof-of-useful-work, sampled re-execution | Python |
| L5 knitwebs | finance / operational / supply-chain / chemistry | Python |
| L6 accounting | PLS activity accounting + Fiber value unit + anchors | Python |

## Status

Implemented and property-tested end to end across **L0–L6**:
- **L0/L3** core crypto, float-free canonical CBOR + CIDv1, Pulse, Web;
- **L1** the integer settlement ledger (blob/fiber/knitweb/knit/braid/node, network-id replay protection);
- **L2** stdlib-`asyncio` signed-feed replication, conflict quarantine, two-party Knit handshakes, and peer-exchange discovery;
- **L4** proof-of-useful-work — sampled re-execution, commit-before-sample challenge, tolerance digests, escrow + a compute guardrail;
- **L5** four domain knitwebs (finance, operational, supply-chain, chemistry);
- **L6** PLS activity accounting, plus OriginTrail anchoring and a provenance walker over the Web.

A `knitweb` CLI runs a node, pays PLS, and compiles / verifies / edge-loads signed
bytecode; node state persists across restarts. ~250 property/interop/knitweb proofs green.
See [`docs/`](docs/) for the language-architecture decisions and
[`docs/research/08-knitweb.md`](docs/research/08-knitweb.md) for the KnitWeb concept
paper (the coined word knitweb, the pulses/draft compute layer, and the
OriginTrail interlock). Run
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
is the activity unit, *Knitweb* is the protocol/brand.
