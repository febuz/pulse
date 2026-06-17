# Competing-environment study — value settlement & on-ledger accounting (finance loom)

> **Research gate for an essential backlog item** (B12, the finance loom). Per the
> build gate in [`ROADMAP.md`](../ROADMAP.md), an essential feature must first survey how
> existing blockchain / DePIN environments solve the same problem, so Knitweb
> **adopts or bridges rather than rebuilds** — we don't build what already exists or
> isn't needed. Method: knowledge synthesis + the offline corpus survey in
> [`CRYPTO_CORPUS_STUDY.md`](../CRYPTO_CORPUS_STUDY.md) (same offline discipline);
> live-web citations can extend it later.

## 1. The question

Before building a finance loom, what does "finance" need to add that Knitweb's
existing primitives don't already provide — and which competing designs are worth
adopting versus which are out of scope?

## 2. What Knitweb already has (must not be rebuilt)

- **Value movement** — `ledger/knit.py` (two-party dual-signed transfer) + `ledger/braid.py`
  (per-account hash-chained history, spent-knit double-spend guard). Conservation and
  nonce replay-protection are already proven.
- **Issuance** — `token/mint.py`: demand-gated, bounded, no-premine PLS via verified PoUW.
- **Settlement gating** — `pouw/escrow.py` + `pouw/dispute.py` (#32): pay-on-verified-work,
  dispute window + slashing.
- **Provenance / attribution** — `fabric/attest.py` (signed, content-addressed records);
  priced offers in `fabric/items.py` (`ResourceItem`).

⇒ Raw payment, issuance, escrow, and provenance are **done**. A finance loom must not
reinvent any of them.

## 3. Competing environments

| Environment | Value movement | Accounting / audit layer | Invoicing / streaming | Lesson |
|---|---|---|---|---|
| Bitcoin (UTXO) | tx inputs/outputs | none on-chain; wallets reconstruct | none | accounting is an off-chain *view* over a settled ledger |
| Ethereum (account) | tx + ERC-20 transfers | **events/logs**, indexed off-chain (The Graph) | none native | emit auditable events; keep bookkeeping out of consensus |
| Hyperledger Fabric/Besu | token chaincode | explicit double-entry possible (permissioned) | custom | double-entry works, but in an app layer — not core |
| Request Network | settles on ETH/stablecoins | on-chain **signed invoice** records (content-addressed) | invoices (not streams) | closest prior art for a *signed, content-addressed financial record* |
| Superfluid / Sablier | streaming / vesting transfers | stream state | **streaming payments** | powerful but heavy; defer until a real streaming need |
| Akash | per-block escrow lease settlement | lease/escrow accounts | lease ≈ implicit invoice | usage→settlement; accounting off-chain |
| Livepeer | probabilistic micropayment tickets | ticket redemption | tickets | micropayment accounting lives with the work layer |
| Helium | data-credit burn | burn records | none | meter→burn, minimal accounting |
| OriginTrail | n/a (knowledge graph) | signed provenance assertions | n/a | audit = signed provenance — our attestation model |

## 4. Gap analysis

Knitweb can already *move* and *issue* PLS and *gate* it on verified work. What it lacks
is an **auditable accounting record** tying those movements into balanced books a peer
can verify — e.g. *"this allocation (operational loom) was the priced offer
(`ResourceItem`) settled by this Knit/escrow, and the books balance."*

The durable pattern across the field: **settle on a minimal ledger; record a signed,
content-addressed accounting entry over it; index/aggregate off-chain.** Double-entry
(debits == credits) is the universal soundness gate — nobody reinvents it, they just
sign it. Request Network is the nearest "signed on-ledger financial record" prior art;
streaming/invoice objects (Superfluid, Sablier) are heavier and not yet needed.

## 5. Decision — build / adopt / bridge

| Component | Verdict | Why |
|---|---|---|
| Value transfer | **reuse** `Knit`/`Braid` | already proven |
| Issuance | **reuse** `token/mint` | already proven |
| Escrow + dispute | **reuse** `pouw/escrow` + #32 | already proven |
| Double-entry invariant | **build** (thin) | universal gate; trivial, integer-only, signed |
| Journal entry that references a settlement/offer CID | **build** (thin) | the missing audit link (operational alloc + ResourceItem price + Knit settlement) |
| Invoices as first-class objects | **defer** | Request-style; only if a real billing need appears |
| Streaming / vesting payments | **out of scope** | Superfluid-class; no current need |
| Allowances / ERC-20 approvals | **defer** | belongs to user LoomTokens (L6), not this loom |

## 6. Minimal finance-loom scope (what to actually build)

A `looms/finance` plugin that signs **double-entry journal entries** over the *existing*
settlement primitives:

- `LedgerEntry(postings, memo, actor)` where `sum(posting amounts) == 0` (debits ==
  credits), integer-only, single-currency, canonical posting sort → order-independent
  CID, signed via `fabric.attest`, woven into the Web.
- An optional `settles` reference: the CID of the Knit/escrow settlement and/or the
  priced `ResourceItem` the entry accounts for — closing the
  operational→offer→settlement audit loop (this also satisfies **B13**).
- **Not** built: payment execution (Knit already does it), issuance (`token/mint`),
  invoices, streams, allowances.

Net-new code is only the double-entry audit record + its settlement reference; every
value-moving primitive is reused. The gate's purpose — *don't build what isn't needed* —
is met.

## 7. Status

Report complete → **unblocks B12 (finance loom)** at the minimal scope above. The earlier
#16 / #30 finance looms had the double-entry core right, but #30 also rebuilt the
operational loom (already merged via #25); this scopes finance down to the missing audit
layer + the settlement-reference link.
