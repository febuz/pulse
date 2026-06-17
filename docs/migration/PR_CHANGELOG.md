# Knitweb — plain PR changelog (#1–#48)

A flat, per-PR record of everything delivered for this project while it lived in
**`github.com/febuz/pulse`** (its pre-migration home). It spans **multiple chat/agent
sessions** — most PRs were authored by the autonomous build loop (`febuz`), reviewed
PR-per-increment. Each entry says **what** landed and **why we decided** to build it that
way (the decision/context), not just the diff.

Conventions:
- **State** is the GitHub state on `febuz/pulse` at migration time.
- **Superseded/closed** PRs are kept here on purpose — they record dead-ends and
  consolidations so the history reads honestly.
- Layer tags (L0–L6) map to the architecture: L0 core · L1 ledger · L2 p2p · L3 fabric ·
  L4 PoUW · L5 looms · L6 token.
- The single hard invariant across every PR: **no change to any canonical/signed-record
  field, key, or value** (CIDs and signatures are sacrosanct). The `network` id field in a
  signed `Knit` is the one allowed technical use of the word "network" — it is hash-critical
  and never renamed.

---

## Foundations (L0–L1): the deterministic settlement core

**#1 — Phase 1: settlement core (blob/fiber/loom/knit/braid/node)** · MERGED
The first landing: the integer, account-based L1 ledger and the seven primitives. Decision:
build money/state as integers (wei-style base units) over a hand-rolled float-free canonical
CBOR + CIDv1 so every client agrees byte-for-byte — no consensus needed for agreement on
*bytes*. (Titled "FBR settlement core" — pre-dates the PLS/Fiber naming split; FBR was later
reserved-not-active and PLS became the pay-token.)

**#9 — harden(core): strict canonical CBOR decode + crypto-corpus study** · MERGED
Closed a soundness gap: the decoder must *reject* non-canonical / truncated / trailing-byte
input, not just encode canonically. Reason: if two clients could accept different byte
encodings of "the same" record, CID agreement breaks. Also seeded `CRYPTO_CORPUS_STUDY.md`
(the backlog of prior-art hardening items, "CCS").

**#11 — bind a network id into every signed Knit (EIP-155 anti-replay)** · MERGED
Added the `network` id to the signed `Knit.to_record`. Reason: cross-web replay protection —
a transfer signed for one web can't be replayed on another (the EIP-155 idea). Decision:
this field is part of the hash-critical signed bytes and is **never** renamed (it survives
even the `loom→knitweb` rename untouched).

**#22 — versioned address/key scheme byte under `pls1`** · MERGED
Put a version byte in the address/key scheme. Reason (CCS §3): a soft-fork hedge so a future
post-quantum signature scheme can migrate addresses without a hard break.

**#35 — harden(core): validate pulse and fabric integer fields** · MERGED
Boundary validation on integer fields in Pulse/fabric records — reject malformed integers at
the edge so the deterministic core can trust internal values.

**#39 — randomized fuzz tests for the canonical CBOR layer** · MERGED
Fuzz the encoder/decoder round-trip. Reason: the canonical layer is the single most
load-bearing piece (every CID/signature depends on it), so it earns property + fuzz coverage.

**#12 — docs: identity/account decision + multi-agent workflow** · MERGED
Recorded the account/identity model and `MULTI_AGENT_WORKFLOW.md` (one branch + one
reviewable PR per increment, disjoint lanes). Reason: multiple agents build in parallel;
without a written lane protocol they collide (this had already caused a duplicate module).

---

## Fabric (L3): the woven knowledge + resource graph

**#3 — Phase 2: KnowledgeItem, ResourceItem, FabricCheckpoint** · MERGED
The typed items that ride on the Web graph and the checkpoint primitive over them.

**#8 — Edge + AR-glass interface + geohash spatial binding** · MERGED
Spatial index + an edge/AR consume-side interface; geohash binding so items can be located.
Context: the "Synaptic Web" vision — verified relations are meant to be consumed at the edge.

**#10 — signed append-only feed core (Phase 3 prep, no networking)** · MERGED
A Hypercore-style signed append-only feed, deliberately split from networking. Decision:
land the *data structure* and its signatures first; wire transport later (kept reviewable).

**#46 — provenance queries over the Web (origin + processing closure)** · MERGED
A provenance walker: given an item, traverse to its origin and its processing closure.
Reason: provenance is the product — "where did this come from and what was done to it" — and
it's what the OriginTrail interlock anchors.

**#5 — fabric attestation + merkle-leaf fix (addresses #3 review)** · CLOSED
Attestation + a merkle-leaf correctness fix from #3's review. Closed: folded forward into the
signed-feed/attestation work that landed via #10 rather than as its own merge.

---

## P2P (L2): replication without heavy dependencies

**#14 — docs: dependency readiness — Phase 3 on stdlib asyncio, not py-libp2p** · MERGED
The pivotal L2 decision. Context: `py-libp2p` was blocked by PEP-668 (externally-managed
env) and pulled a heavy dependency surface. Decision: build the L2 MVP on **stdlib
`asyncio`**; keep py-libp2p/DHT as an *optional* later backend, not a core requirement
(credible neutrality = tiny dependency surface).

**#15 — p2p: asyncio feed sync and Knit handshake** · MERGED
The L2 MVP that #14 chose: signed-feed replication + a two-party Knit handshake over asyncio.

**#45 — peer-exchange discovery — grow the web beyond static peers** · MERGED
Peer-exchange so a node discovers peers instead of relying only on a static list. Reason:
a "web" that can't grow past hand-configured peers isn't a web.

---

## PoUW (L4): the DePIN heart — useful work you can verify

**#7 — Phase 4 core: PoUW synaptic job (sampled re-execution + escrow settlement)** · MERGED
The first PoUW landing: a job whose proof is checked by sampled re-execution, settled through
escrow. Decision: trust comes from *re-running a fraction of work*, not from raw hashing.

**#24 — PoUW determinism foundations: tolerance digest + commit-before-sample** · MERGED
Made re-execution verdicts deterministic and unbiased (CCS §1): a tolerance digest (for
floating/iterative work that isn't bit-identical) and commit-before-sample (the verifier
commits to *which* samples before seeing them, so a worker can't target the sampled subset).

**#32 — PoUW dispute window: settlement timing + slashing (Sprint 2)** · MERGED
`slashable_until = submit_beat + dispute_window`; release-delay must exceed the window, and a
slash reaches pending withdrawals. Reason: payout can't be final before peers have had time
to dispute it. (Was an open draft through the migration-prep phase; since merged.)

---

## Looms (L5): domain validators gated by conservation invariants

**#13 — chemistry domain loom (signed, conservation-checked reactions)** · MERGED
First domain loom: signed reaction-knowledge records that must conserve mass/atoms. Decision:
a "loom" is a domain validator — it only admits records that satisfy a physical/economic
conservation invariant, so the fabric stays sound per domain.

**#23 — supply-chain loom (signed, mass-conserving process events)** · MERGED
Supply-chain process events that conserve mass across a transformation step.

**#25 — operational loom (signed capacity allocations, no over-allocation)** · MERGED
Capacity allocations that can't over-allocate a resource. (Its operational logic was later
replaced by the multi-resource version in #30.)

**#30 — finance loom + multi-resource operational loom (supersedes #27, replaces #25's operational)** · MERGED
The consolidation that resolved the finance/operational duplication. Decision: one finance
loom (double-entry) + a *multi-resource* operational loom, with pricing moved onto
`ResourceItem`. This is the surviving L5 finance+operational implementation.

**#31 — harden(looms): tighten supply-chain record invariants** · MERGED
**#33 — harden(looms): tighten chemistry record invariants** · MERGED
Two follow-ups that tightened the admit-checks on the already-merged looms — closing edge
cases where a malformed-but-superficially-valid record could slip in.

### Finance-loom dead-ends (consolidated into #30)
**#16 — Finance Loom: signed invoices + double-entry** · CLOSED — first finance attempt; superseded by #30.
**#27 — Phase 5 finance + operational looms** · CLOSED — combined attempt; superseded by #30 (which won the dedup).
**#37 — finance loom: double-entry journal over existing settlement (B12)** · CLOSED — re-cut of the journal idea; folded, not merged.
**#41 — finance settlement audit-link `LedgerEntry.settles` (B13)** · CLOSED — audit-link refinement; folded into the settlement model rather than landing standalone.

---

## Token & economics (L6): PLS, no premine

**#17 — demand-gated bounded PLS minting via PoUW (M4)** · MERGED
The economic engine: PLS is minted only against verified useful work, bounded by escrow + a
max supply. Decisions: **no founder premine** (genesis `mintable=false`, `premine=0`), and
**anti-replay on the proof digest** so the same proof can't mint twice. PLS is an *access
right* to real hardware capacity, not a speculative instrument.

**#28 — notary-signed checkpoint anchor receipts + pluggable backend** · MERGED
The `anchor/` layer: checkpoints get notary-signed receipts, with a pluggable backend.
Decision: keep the anchor backend abstract so the same receipts can target a local store or
an external chain.

**#40 — OriginTrail anchor backend — publish checkpoint roots to the DKG** · MERGED
The concrete backend for #28: publish checkpoint roots to OriginTrail's Decentralised
Knowledge Graph. Context: the project complements OriginTrail rather than competing with it.

**#42 — OriginTrail symbiosis round-trip — the USP, end to end** · MERGED
Proved the unique selling point end to end: read assets from OriginTrail → compile to signed
edge bytecode → anchor the result back. The read↔write symbiosis is the differentiator.

---

## App, persistence & CLI: making it runnable

**#18 — durable node persistence — state survives restart (M3)** · MERGED
`store.py`: canonical-CBOR-backed durable persistence so a node's state survives a restart.

**#19 — runnable `knitweb` node + wallet CLI (M2)** · MERGED
`app/cli.py`: the first runnable surface — start a node, hold a wallet, pay PLS.

**#20 — end-to-end MVP acceptance demo (M5)** · MERGED
`examples/mvp_demo.py`: the integration capstone tying M2–M4 into one acceptance run.

**#38 — daemon auto-persist — close the node crash-gap** · MERGED
**#47 — daemon auto-persist (post-MVP hardening)** · MERGED
Auto-persist on the running daemon so an unexpected crash doesn't lose state between manual
saves. #47 is the post-MVP hardening pass over the same crash-gap #38 first addressed.

**#43 — CLI compile + verify-bundle — expose the synaptic compiler (USP)** · MERGED
**#44 — CLI edge-load — verify-before-trust bundle loading (edge/AR consume side)** · MERGED
The two CLI halves of the synaptic-compiler USP: compile relations → signed bytecode bundle
(#43), and load a bundle *verifying signatures before trusting it* (#44). Decision:
consumption is verify-before-trust — an edge device never executes unsigned/unverified bytes.

---

## Hardening & housekeeping

**#6 — fix(fabric): import Beat directly, drop forward-ref string + noqa** · MERGED
Small import/typing cleanup.

**#34 — harden(anchor): validate signed receipt boundaries** · MERGED
Boundary validation on anchor receipts — reject malformed receipts at the edge.

**#26 — docs: PoUW security theory + consolidated roadmap/backlog** · MERGED
Wrote `PROOF_OF_USEFUL_WORK.md` (the L4 security theory) and consolidated the live roadmap +
backlog. Reason: the PoUW economics are subtle enough to need a written threat model before
implementing the dispute/quorum/escrow sprints.

**#29 — make LOC_BY_LANGUAGE.md generated-on-demand (untracked), not maintained per-PR** · MERGED
Stopped version-controlling an auto-generated LOC report; gitignored it and made it a `tools/`
script. Reason: a regenerated file in every PR is pure merge-conflict churn with no review
value (this exact file later caused the only conflict in a cherry-pick).

---

## Closed early-naming / SDK experiments (kept for honesty)

**#2 — Fiber Synaptic Compiler — relations → signed edge bytecode + OriginTrail symbiosis** · CLOSED
The compiler/symbiosis idea, attempted too early. Closed and re-landed properly once the
canonical + fabric foundations existed — it shipped as #42 (symbiosis) + #43/#44 (CLI compile
/ edge-load).

**#4 — SDK facade: Wallet + end-to-end synaptic demo** · CLOSED
An early SDK/demo facade. Superseded by the real CLI (#19) + the MVP demo (#20).

**#21 — knitweb Python package — Fiber/Dot/Knot/FBR/Risk graph layer** · CLOSED
An early package-naming experiment with primitives `Dot`/`Knot`/`FBR`/`Risk`. Closed: the
canonical primitive names (Blob/Fiber/Loom/Knit/Braid/Web/Pulse) and the PLS pay-token /
FBR-reserved split won out instead.

---

## Migration & the in-flight cleanup (this chat's work)

**#48 — docs(migration): pulse-only handoff — finish the knitweb migration from anywhere** · OPEN
A handoff branch carrying `docs/migration/{MIGRATION, MIGRATION_PLAN, CONTINUATION,
CLAUDE.snapshot, PROJECT_MEMORY}.md` + the plan/claude snapshots, so the migration can be
finished from another machine. **Decision: this branch stays in `febuz/pulse` and is
intentionally NOT migrated** — it's the breadcrumb that points at the new home.

**Consistency pass (lands as PR #1 on `knitweb/knitweb`)** · in-flight (`fix/consistency-pass-01`)
25 naming/vocabulary/stale-doc/metadata fixes in 10 clusters (A–J): PLS/Fiber vocabulary in
docstrings; "network" prose → "web" (logic untouched); README status + layer tables
de-staled; `pyproject` version 0.0.1→0.6.0, real author, `[project.urls]`→`knitweb/knitweb`;
a compute-guardrail `pouw/scheduler.py`; `CHANGELOG.md`. Hard constraint honored: **zero
signed-record field/key/value changes**; full suite green.

**`loom → knitweb` literal rename (planned as PR #2 on `knitweb/knitweb`)** · planned
Owner-decided 2026-06-17: rename `loom` everywhere, including the core `Loom` primitive
(`ledger/loom.py`→`ledger/knitweb.py`, `Loom`/`LoomError`→`Knitweb`/`KnitwebError`, the `loom`
pytest marker, all prose), accepting the `knitweb.ledger.knitweb.Knitweb` overload. Verified
**signed-record-safe**: no record `kind`/field contains "loom" (kinds are
`reaction-knowledge`/`supplychain-process`/`capacity-allocation`/`journal-entry`/`invoice`),
so it's identifier/docs-only with **zero CID/signature impact**; the PR must assert a sample
record's `cid` is byte-identical before/after. `LoomToken` is dropped ("Maak geen loomtoken").

---

## Repository move (context)

The whole history above was built in **`github.com/febuz/pulse`**. It is migrating to
**`github.com/knitweb/knitweb`** (fresh mirror push, not a GitHub transfer) to align
**org = repo = package = `knitweb`**. PRs/issues do not carry over; the open draft(s) are
re-opened on the new repo; `febuz/pulse` is retired with a pointer (the #48 handoff) to the
new home. *Pulse*/PLS remains the pay-token name; *Knitweb* is the protocol/brand.
