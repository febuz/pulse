---
name: knitweb_project
description: "Knitweb — pure-Python P2P crypto (token PLS, coin Fiber); identity, branding decision, build conventions"
metadata: 
  node_type: memory
  type: project
  originSessionId: 8d21cbd9-9428-4ebc-9656-ca03e23c35d3
---

**Knitweb** = pure-Python P2P crypto "web" (never "network"/"net") at
`/media/knight2/EDS2/projects/knitweb/`, repo `github.com/febuz/knitweb` (private).
JS prototype at `…/knitnet/` is reference only.

Three-layer brand architecture (do not collapse): **coin/brand = Fiber**,
**token/activity unit = PLS ("pulses")** (what users spend), **protocol/platform =
Knitweb**. FBR is reserved (possible regional token), not the launch token.
Vocabulary fixed: Web · Loom · Knit · Pulse · Fiber.
**"Loom" is internal-architecture vocabulary only** (the protocol/validation layer);
it is NOT a project/brand name — as a brand it's a deprecated working codename
*superseded by Knitweb* (owner steer 2026-06-17). The "Pulse / Loom" research
dossier is pre-rename; cite it but never brand anything "Loom"/"Pulse" standalone.

**Account decision (owner-confirmed 2026-06-17):** build under ONE `knitweb` org
now; defensively reserve `pulse` + `fiber` handles; split to a separate
token/foundation org only when governance/legal/community structure demands it.
Token repos live under `knitweb` (`knitweb/pls-*`, `knitweb/fiber-*`) until then.
Rationale in `docs/IDENTITY_AND_ACCOUNTS.md`.

**Crypto:** secp256k1 ECDSA + SHA-256; hand-rolled deterministic float-free CBOR +
CIDv1 (`core/canonical.py`); integers only (PLS-wei). Account-based ledger with
nonce replay-protection; Knits bind a `network` id (EIP-155 anti-replay).

**Build conventions** (see `docs/MULTI_AGENT_WORKFLOW.md`): multiple agents work
this repo in parallel — claim a lease per lane via `~/.claude/coordination/coord.py`
(`knitweb/<lane>`), one isolated branch per increment off `main`, NEVER push to
`main` (review-gated), one reviewable PR each with proofs + "review asks for Codex"
(equal-level reviewer). Don't stack on unreviewed foundational PRs.
Tests: `PYTHONPATH=src pytest -q`. Refresh `tools/loc_report.py` per PR.
Research corpus: ~190 crypto repos at `/media/knight2/EDS2/crypto-networks-repos*/`
— lessons distilled in `docs/CRYPTO_CORPUS_STUDY.md`. Loop re-armed via `/loop 900m`
(session cron, every 12h).
