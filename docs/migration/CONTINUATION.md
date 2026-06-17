# Continuing Knitweb from another location / server

Everything an agent or human needs to pick up the work elsewhere. (Knitweb-only context;
no unrelated-project or credential material is included by design.)

## 1. Get the code

Pick whichever is available from the new machine:

```bash
# A) After the mirror push has run — clone the new home (preferred):
git clone git@github.com:knitweb/knitweb.git && cd knitweb

# B) Before migration, or offline — restore from the backup bundle
#    (copy it off this server first; it lives at
#     /media/knight2/EDS2/backups/knitweb-crypto-mainline.bundle):
git clone knitweb-crypto-mainline.bundle knitweb && cd knitweb
#    The bundle includes `main` + every feature branch incl. `fix/consistency-pass-01`.

# C) Fallback — the old repo (history identical to the bundle's main):
git clone git@github.com:febuz/pulse.git knitweb && cd knitweb
```

Then: `PYTHONPATH=src python3 -m pytest -q` should be green (~255). Python ≥ 3.12 + `cryptography`
are the only runtime needs (the hash-critical canonical encoder is hand-rolled — zero external surface).

## 2. What to do next

Follow `MIGRATION.md`'s runbook (Steps 0–5). In short: mirror-push → repoint remotes → open PR #1
(`fix/consistency-pass-01`) → re-open the 3 drafts → ship PR #2 (`loom→knitweb` rename) → cut over.
Beyond the migration, the live backlog is in **`docs/ROADMAP.md`** (e.g. provenance `derived-from`
links across the looms, per-epoch mint wiring, partial-range Merkle proofs, optional py-libp2p/DHT).

## 3. Working model (how this project is built)

- **PR-per-increment.** Branch off current `main`; build the smallest *proven* increment; open one
  reviewable PR with: what/why, the proof (`pytest -q` + green count), and explicit review asks for the
  equal-level reviewer (Codex). Implement agreed feedback; push back with reasoning when you disagree.
- **Multi-agent coordination.** Several agents share the repo. Claim a lease per lane before editing —
  `python3 ~/.claude/coordination/coord.py claim knitweb/<lane> --note "..."` (exit 0 = yours; 1 = held;
  pick another lane). `release` when done. When two agents share one working tree, **use a `git worktree`
  per agent** so `git checkout` can't clobber the other (this bit us — see `docs/MULTI_AGENT_WORKFLOW.md`).
- **Proofs-first.** Every increment ships a runnable test. No feature is "done" without green proofs.

## 4. Hard invariants (do NOT violate)

- **Canonical bytes are sacred.** All hashing/signing goes through `core.canonical.encode` (float-free
  deterministic CBOR) + `core.canonical.cid` (CIDv1 dag-cbor sha2-256). **Never change a field name/key/
  value inside a `to_record()`/signed record** — it changes every CID and signature. (This is why the
  `loom→knitweb` rename was verified safe: no record `kind`/field contains "loom".)
- **Integers only.** Money + state are integer PLS-wei; no floats anywhere near hashing/balances/canonical.
- **Crypto** = secp256k1 ECDSA + SHA-256 via `cryptography`. No Ed25519/BLAKE2b on the value path.
- **No premine.** PLS mint is demand-gated + bounded (escrow + optional `max_supply`); founders earn like anyone.
- **Vocabulary:** a *web*, never a "network"/"net". The ONE allowed technical use of "network" is the
  `network` **id field** in a signed `Knit` (EIP-155-style chain id) — hash-critical, never rename it.
  (After PR #2 the brand term `Loom` becomes `Knitweb`.)

## 5. Key reference docs (in the repo)

- `docs/migration/MIGRATION.md` + `MIGRATION_PLAN.md` — this migration.
- `docs/ROADMAP.md` — backlog, layer status, the `loom→knitweb` rename mandate.
- `docs/CRYPTO_CORPUS_STUDY.md` — design lessons (PoUW economics, append-only feeds, encoding/PQ).
- `docs/DEPENDENCY_READINESS.md` — why P2P is stdlib-asyncio (py-libp2p PEP-668-blocked) + PoUW CPU-first.
- `docs/PROOF_OF_USEFUL_WORK.md` — the economic-security model + threat table.
- `docs/SYNAPTIC_WEB.md` — the USP: OriginTrail relations → signed edge bytecode.
- `CLAUDE.md` (root; snapshot here as `CLAUDE.snapshot.md`) — the project agent-guide.
