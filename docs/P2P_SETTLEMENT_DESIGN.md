# Design — P2P-distributed crowdfunding settlement

Status: **design / RFC** (no code). The merged crowdfunding stack produces a signed, audited
`crowdfunding-settlement` instruction and a *local* executor (`execute_settlement`) that moves
PLS escrow→payee when **both accounts are in one process**. This document designs the
**distributed** execution — escrow and payees on different nodes — which is the remaining real
piece. The MVP owner decisions are fixed below, so this RFC can move from blocked design to
implementation-ready design.

## The constraint that shapes everything

knitweb `Knit` transfers are **dual-signed** (sender proposes + receiver accepts; see
`ledger/node.py` `propose`/`accept`/`apply_sent`/`apply_received`) and applied **per-account in
nonce order**. Two consequences:

1. An escrow **cannot unilaterally push** funds to a payee — the payee must co-sign acceptance.
   So settlement is inherently a set of two-party handshakes, not a batch the escrow applies alone.
2. All payouts from one escrow share a single nonce sequence — the escrow must apply them in a
   **deterministic order, one at a time** (the settlement's sorted entries give that order).

Together these mean a robust distributed settlement must tolerate **payees being offline** and
must be **idempotent/resumable**.

## Two models

### Model A — escrow-push (both online, simplest)
The escrow drives, in settlement-entry order:
1. Authority publishes the signed `crowdfunding-settlement` (already built) to the fabric.
2. For entry *i* (payee, amount), escrow `propose`s a sender-signed Knit at its current nonce and
   sends it to the payee over the wire (`p2p/node.py`).
3. The payee **validates the proposal against the published settlement** — the Knit's
   `(to_pub, amount)` must match a settlement entry whose `payee == self`, and the settlement must
   `audit_settlement` — then `accept`s (receiver-signs) and returns it.
4. Escrow `apply_sent`, advances nonce, payee `apply_received`; move to entry *i+1*.

Idempotency: the escrow persists `(settlement_cid → next_entry_index)`; on restart it resumes.
A payee accepts a given `(settlement_cid, entry)` at most once (dedup). This generalizes the
in-process `applied`-set guard already shipped.

**Weakness:** a single offline/unresponsive payee stalls its entry. Acceptable for release
(one beneficiary, presumably online) but poor for refunds (many pledgers, often offline).

### Model B — payee-pull / claim (robust to offline payees) — recommended for refunds
Invert the handshake: the **payee initiates**.
1. Authority publishes the settlement; escrow funds remain in the escrow account.
2. Any payee, whenever online, builds a **claim**: it `propose`s the *receive* by presenting the
   settlement + its entry, and the **escrow** (a service that stays online) verifies the entry,
   co-signs as sender, and the transfer completes. The escrow enforces **claim-once** per
   `(settlement_cid, entry_cid)`.
3. Unclaimed funds sit in escrow until claimed (or a documented expiry/forfeiture policy fires).

This removes the "all payees online at once" requirement and matches how real crowdfunding
refunds work (claim/refund-on-demand). Release (goal met) can use Model A (single beneficiary).

## Safety properties (both models)

- **Conservation** — enforced by the ledger (dual-signed Knits, no overdraft); the published
  settlement bounds the total, and per-entry amounts are committed in `settlement_root`.
- **No double-pay** — `(settlement_cid, entry)` is applied at most once (escrow-side dedup, the
  distributed form of the shipped `applied` set); the ledger's per-account nonce prevents Knit
  replay.
- **Auditable** — anyone can reconcile the applied Knits' Braids against the signed settlement
  (`settlement_entries` recomputes the expected `(payee, amount)` set; `settlement_root` commits it).
- **Authorisation** — a payee only ever co-signs/claims an entry addressed to itself in a settlement
  that `audit_settlement` passes; the escrow only signs entries present in the audited settlement.

## Reuses (no new heavy deps)

`ledger/node.py` (propose/accept/apply, two-party transfer), `p2p/node.py` + `p2p/wire.py`
(signed-frame transport), `fabric/feed.py` (publish the settlement as a signed feed entry),
`crowdfunding/campaign.py` (`settlement_entries`, `audit_settlement`). The escrow's
`(settlement_cid → progress)` and `claimed` sets persist via `store.py`.

## Owner decisions for the MVP

1. **Custody** — use a neutral protocol escrow service account for the MVP. The escrow signs only
   entries that are present in an audited settlement; campaign authorities do not directly custody
   refund or release funds once pledged.
2. **Refund model** — use Model B (payee-pull / claim) for refunds. Refund recipients claim when
   they are online; the system does not require all payees to be reachable at once.
3. **Liveness/forfeiture** — unclaimed refunds remain claimable indefinitely for the MVP. Expiry,
   forfeiture, return-to-treasury, or pool redistribution are explicit future policy extensions,
   not default settlement behavior.
4. **Fees** — no protocol or relayer fee in the MVP settlement path. Fees can be added later as
   explicit audited settlement entries, so they are visible in `settlement_root` and do not change
   the core refund/release handshake.

## Phasing

- **Phase 1** — neutral protocol escrow service + Model A escrow-push for successful release
  payouts, with a persisted resume cursor and claim-once dedup.
- **Phase 2** — Model B refund claim endpoint + wire messages over `p2p`, with an offline-payee
  test proving refunds remain claimable when recipients come online later.
- **Phase 3** — optional policy extensions: expiry/forfeiture, return-to-treasury, redistribution
  pools, and protocol/relayer fees as explicit audited settlement entries.
