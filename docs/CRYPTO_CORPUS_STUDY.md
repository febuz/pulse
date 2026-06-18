# Crypto-corpus study — design lessons for Knitweb

A read-only survey of ~190 crypto repositories on EDS2
(`/media/knight2/EDS2/crypto-networks-repos*/repos/`) mined for patterns that
pressure-test Knitweb's specific design decisions. Three clusters were studied:
DePIN proof models, append-only signed feeds + P2P, and canonical
encoding / ledger model / post-quantum. Each finding is mapped to a concrete
Knitweb action. Findings drive the backlog in `PLAN`/`CLAUDE.md`.

---

## 1. DePIN work-proof / escrow / slashing → `pouw/`

Knitweb's model: optimistic **sampled re-execution** + PLS escrow, slash on
digest mismatch, no minting yet. Lessons from networks that actually ship this:

| Network | What it proves / how it pays | Lesson for Knitweb |
|---|---|---|
| **Akash** (`x/escrow`,`x/market`) | No compute proof — marketplace; per-block streaming escrow (`accountSettleFullBlocks`) | Adopt streaming escrow **but gate each tranche on a digest match**, not elapsed time — Akash's missing verification is exactly our hole to fill |
| **Filecoin** (`wdpost_run.go`,`miner.go`) | 48 deadlines/period, beacon-seeded sampled challenges; **declared faults cheap, detected faults expensive** | Copy the **declared-vs-detected asymmetry**: let a worker self-declare "can't finish slot X" for a small fee; reserve full slash for verifier-detected silent mismatch |
| **Livepeer** (`pm/recipient.go`,`verification`) | Sampled recompute + **winning-ticket** probabilistic micropayments (`H(sig,rand) < winProb`); redemption delay | Adopt **winning-ticket escrow** — settle only a random ~1/N of jobs on-chain (cuts verifier cost); add a redemption/dispute window |
| **EigenLayer/EigenDA** (`AllocationManager.sol`,`aggregation.go`) | 14-day withdrawal delay > dispute window; slashing reaches **queued withdrawals**; k-of-n BLS quorum + KZG length-proof | **Escrow release delay must strictly exceed the dispute window**, slashing must reach pending withdrawals; use a **k-of-n verifier quorum**, not one oracle |
| **Chutes/Targon** (`cfsv_wrapper.py`,`validator.md`) | Fresh per-challenge **salts** + GraVal device binding; Targon sidesteps GPU non-determinism via **attested TEEs** | **Raw-float digest equality breaks under GPU non-determinism** — pin determinism + compare **tolerance/quantized digests**, or fall back to hardware attestation; **salt every challenge** |
| **Arweave** (`ar_poa.erl`) | SPoRA: challenge unforgeable without the real packed chunk; reward-only, no slash | Borrow "challenge input must be unforgeable without doing the real work"; reward-only is **insufficient** for us since we hold collateral |

**Top actions (ranked):**
1. **Solve digest-determinism first** — exact-match on raw float digests silently
   slashes honest workers. Pin determinism (fixed seeds, deterministic kernels,
   pinned driver/lib) + tolerance/quantized digests; attest when unpinnable.
   *Existential — without it the proof model misfires.*
2. **Salt every challenge + commit-before-sample** — worker commits a digest over
   the full output at submit; verifier samples fresh-salted random indices against
   that fixed commitment. Defeats precompute + retroactive work-swap.
3. **Release delay > dispute window; slash pending withdrawals** —
   `slashable_until = submit_beat + dispute_window`; no withdraw before re-exec.
4. **k-of-n verifier quorum** (~55% confirm, tolerate ~33% adversary) — stops a
   single corrupt verifier from stealing stake or whitewashing fraud.
5. **Streaming/probabilistic escrow + declared-fault asymmetry** — settle ~1/N
   jobs; self-declared incapacity = small fee; size collateral ≥ one settlement
   window's payout-at-risk so faked-digest batches are never net-profitable.

---

## 2. Append-only signed feeds + P2P → `p2p/` (Phase 3)

Knitweb Phase 3: a signed append-only feed per peer + local index, two Python
nodes replicating a feed and settling a Knit over the wire, guarding feed
equivocation. The first transport is stdlib `asyncio` with static peers because
py-libp2p is not installable on this box without breaking the system Python
policy; py-libp2p/DHT stays a later backend.

**Hypercore (Holepunch Pear) is almost exactly this design.**
*Read: `lib/verifier.js`,`caps.js`,`merkle-tree.js`,`core.js`,`replicator.js`.*
- **Sign the tree state, not each entry.** `caps.treeSignable` signs a fixed
  struct `TREE_ns ‖ manifestHash ‖ rootHash ‖ length(u64) ‖ fork(u64)`. Gives
  O(1)-signature verification of *any partial slice* (a peer fetches blocks
  900–910 of a million and verifies them against one signature via sibling hashes).
- **Equivocation = two valid signatures at the same `length` with different
  roots.** `_checkIfConflict` → `core.checkConflict`: request the peer's signed
  prefix proof at `min(length)`; if both verify but roots differ → proof of
  equivocation → freeze + quarantine the feed.
- **`fork` counter** in the signable separates a legitimate truncate+reappend
  from an attack (so equivocation detection never false-positives).
- **Discovery via `discoveryKey = hash(ns ‖ feedKey)`** — find peers on a topic
  without leaking the verification key.

**libp2p Go** — signed **Envelopes** (`domain ‖ type ‖ payload`, monotonic Seq);
Kademlia DHT; gossipsub `StrictSign` + message-ID dedup; Noise+Yamux transport.
**IPFS Kubo** — CIDv1 Merkle-DAG; Bitswap `want-have`/`want-block` range sync;
**IPNS** = signed, seq-numbered mutable pointer (older seq rejected) — the model
for a feed head.

**Top actions (Phase 3):**
1. **Sign the Merkle tree state** — signed payload = domain-separated canonical-CBOR
   `KNIT_TREE ‖ feedManifestHash ‖ rootHash ‖ length ‖ fork`.
2. **`check_conflict`** — on verify mismatch, request signed prefix proof at
   min(length); both valid + different roots ⇒ persist `frozen` conflict, quarantine.
3. **Explicit `fork` counter** in the signable.
4. **Wire = `request{block?,hash?,range}` → `data{…, merkle_nodes}`** — ship sibling
   hashes so partial sync self-verifies; negotiate ranges with bitfields.
5. **Discovery = `discoveryKey = SHA256(ns ‖ feedPubKey)`** on py-libp2p Kademlia as
   a signed Envelope (`feedID → latest CID + seq`); gossipsub `StrictSign` for fan-out.

**Verdict:** mirror Hypercore's *design* natively in Python (secp256k1/SHA-256/CBOR
instead of ed25519/blake2b). The installable MVP uses stdlib `asyncio` transport
with static peers; py-libp2p/DHT/pubsub is a later backend once the dependency path
is sanctioned. Don't reimplement a DHT.

---

## 3. Canonical encoding / ledger / post-quantum → `core/`

**Encoding.** Knitweb's float-free dag-cbor sits inside the safe envelope that
RLP / SSZ / Borsh / Cosmos-ADR-027 define — but those buy determinism through
*schema rigidity*, while CBOR buys it through *encoder discipline*, concentrating
risk on **map-key ordering** (Borsh never sorts; we must, every time).
- Ethereum **RLP** rejects non-canonical ints on decode (`ErrCanonInt`).
- Cosmos **ADR-027** is the strongest written spec: minimal-length varints,
  ascending tag order, omit defaults, **maps forbidden**, no floats.
- Solana/NEAR **Borsh**, Ethereum **SSZ** — determinism is structural (fixed schema).

→ **ACTION (shipped on `harden/canonical-decode-strict`):** make `decode` *reject*
non-canonical input, not just `encode` canonically — non-minimal int/length heads,
unsorted/duplicate map keys, indefinite-length, trailing bytes. Adopt ADR-027 as
the conformance baseline; fuzz `decode(encode(x))==x` and `encode(decode(b))==b`
as invariants. **Never** rely on Python `dict` insertion order — sort encoded keys.

**Ledger.** Knitweb's account+nonce model is directly validated by **Ethereum**
(`state_transition.go`: exact-match `tx.nonce == account.nonce`, EIP-2681 overflow
guard, EIP-155 chainID-in-signature). UTXO (Bitcoin `coins.cpp`), key-images
(Monero), nullifiers (Zcash) give double-spend protection without per-account state
but add privacy plumbing.
→ **ACTION:** adopt exact-match nonce + overflow guard + **chainID bound into the
signed payload** (no cross-fork/testnet replay); separate pending (contiguous) vs
queued (gapped) in the mempool; increment nonce **atomically** with the debit;
**revert nonce on reorg** with state.

**Addresses + post-quantum.** Knitweb's `pls1`+base32(sha256²(pubkey)[:20]) is a
sound hash-of-pubkey address (cf. Bitcoin RIPEMD160(SHA256), Ethereum
Keccak256(pub)[12:]). The **signature** layer is the liability: secp256k1 ECDSA is
Shor-breakable once a pubkey is revealed (every spend reveals it →
harvest-now-decrypt-later). **QRL** uses stateful **XMSS** (catastrophic on OTS
reuse — wrong tradeoff for user wallets); **Mina** uses Schnorr/Pallas (still
classical).
→ **ACTION:** add an explicit **key/address scheme version byte** under `pls1` in
v1 so a PQ algorithm (prefer stateless **SPHINCS+** / ML-DSA, *not* stateful XMSS
for user keys) can land via soft-fork later. Treat secp256k1 as a deprecation-track
primitive, not a permanent foundation. Consider hybrid (secp256k1 + PQ co-sig) for
high-value accounts.

---

## Backlog seeded by this study

- [x] **canonical decode strictness** — `harden/canonical-decode-strict` (this PR)
- [ ] **versioned address/key scheme** — version byte under `pls1` (cheap, do before mainnet)
- [ ] **chainID in signed Knit payload** — anti cross-fork replay (Phase 1 follow-up)
- [ ] **pouw digest-determinism** — tolerance/quantized digests + per-challenge salt (Phase 4, existential)
- [ ] **pouw dispute window + k-of-n verifier quorum + collateral sizing** (Phase 4)
- [x] **Phase 3 asyncio MVP** = Hypercore-style signed Merkle-tree state + fork counter + `check_conflict`, full-feed sync over stdlib `asyncio`, static peers, two-party Knit wire handshake
- [ ] **Phase 3 optional backend** = partial-range Merkle proofs + DHT/pubsub backend once py-libp2p has a sanctioned install path
