# Archived branches — the story of each file

This documents the **11 numbered `archive/NN-*` branches** that were pushed to
`febuz/pulse` purely as a **data-safety measure** — so the local disk could be discarded
without losing any branch ref that only existed locally. They are *not* a backlog and have
**no open PRs**.

## What these branches are

They are the **original source branches** of work that was developed locally and (for all but
one) **squash-merged into `main`** through the PRs in [`PR_CHANGELOG.md`](PR_CHANGELOG.md).
Because a squash-merge rewrites the commits into a single new commit on `main`, the original
branch tips never become reachable from `main` — so git reports them as "local-only" forever
even though their *content* is already on `main`. We numbered and pushed them so that
provenance (the real authoring commits, including reviewer-requested hardening commits) is
preserved online.

**Merge status at a glance** (verified by checking whether each file exists on `main`):

| Branch | PR | Status | Content on `main`? |
|---|---|---|---|
| `archive/01-anchor-origintrail` | #40 | MERGED | ✅ yes |
| `archive/02-anchor-origintrail-pulse` | #40 (dup) | MERGED | ✅ yes (parallel-agent duplicate) |
| `archive/03-app-edge-cli` | #44 | MERGED | ✅ yes |
| `archive/04-app-synaptic-cli` | #43 | MERGED | ✅ yes |
| `archive/05-fabric-provenance` | #46 | MERGED | ✅ yes |
| `archive/06-integration-check` | #20-era | MERGED | ✅ yes (no delta vs `main`) |
| `archive/07-looms-finance-operational` | #30 | MERGED | ✅ yes |
| `archive/08-p2p-discovery` | #45 | MERGED | ✅ yes |
| `archive/09-test-canonical-fuzz` | #39 | MERGED | ✅ yes |
| `archive/10-test-origintrail-symbiosis` | #42 | MERGED | ✅ yes |
| `archive/11-token-loomtoken` | — | **NEVER MERGED** | ❌ **no — owner-rejected** |

The only branch whose code is **not** on `main` is `archive/11-token-loomtoken` (see below);
it is the genuine "kept for the record but deliberately not shipped" branch.

---

## archive/01-anchor-origintrail → PR #40 (MERGED)

The OriginTrail anchor backend — publishing checkpoint roots to OriginTrail's DKG.

- **`src/knitweb/anchor/origintrail.py`** — the concrete backend behind the pluggable anchor
  interface from #28. Takes a signed checkpoint receipt and publishes its root to the DKG; this
  is the "write" half of the OriginTrail symbiosis.
- **`tests/property/test_anchor_origintrail.py`** — proves a checkpoint root round-trips
  through the backend and that the receipt's signed bytes are preserved.
- **`docs/LOC_BY_LANGUAGE.md`** — incidental churn in the auto-generated LOC report (this file
  was later made generated-on-demand / untracked by #29; its presence here is pre-#29 noise).

## archive/02-anchor-origintrail-pulse → PR #40 (duplicate, MERGED)

A **parallel-agent duplicate** of the same anchor-origintrail feature, cut on a different
`main` base and pushed via the `pulse` remote (hence the `-pulse` suffix). Against its own
merge-base it adds the **same** two files as #01 — `src/knitweb/anchor/origintrail.py` and
`tests/property/test_anchor_origintrail.py` — minus the LOC-report churn. It is the artifact of
two agents independently landing #40; kept to show the coordination history (and why the
lease-per-lane protocol exists).

## archive/03-app-edge-cli → PR #44 (MERGED)

The **consume side** of the synaptic-compiler USP: load a signed bytecode bundle at the edge,
verifying before trusting.

- **`src/knitweb/app/cli.py`** (+36 lines) — adds the `edge-load` subcommand: verify the
  bundle's signatures *before* loading/executing it. Decision: an edge/AR device never runs
  unsigned or unverified bytes.
- **`tests/property/test_cli_edge_load.py`** — proves a tampered bundle is rejected and a valid
  one loads.

## archive/04-app-synaptic-cli → PR #43 (MERGED)

The **produce side** of the same USP: expose the synaptic compiler on the CLI.

- **`src/knitweb/app/cli.py`** (+61 lines) — adds `compile` (relations → signed edge bytecode)
  and `verify-bundle` (re-check a produced bundle).
- **`tests/property/test_cli_compile.py`** — proves compile→verify round-trips and produces
  stable, signed output.

## archive/05-fabric-provenance → PR #46 (MERGED)

Provenance traversal over the Web graph.

- **`src/knitweb/fabric/provenance.py`** — a walker that, given an item, returns its **origin**
  and its **processing closure** (everything that fed into it). Reason: "where did this come
  from and what was done to it" is the product the OriginTrail interlock anchors.
- **`tests/property/test_provenance.py`** — proves the closure is complete and acyclic on
  sample webs.

## archive/06-integration-check → #20-era MVP (MERGED, no delta)

The MVP integration-check branch. Its three-dot diff against `main` is **empty** — every
commit is already an ancestor of `main`. Kept only as a named ref pointing at the
fully-merged integration milestone (`examples/mvp_demo.py` and friends from #20). Nothing to
recover; it documents that the MVP acceptance run landed cleanly.

## archive/07-looms-finance-operational → PR #30 (MERGED)

The consolidation that resolved the finance/operational loom duplication (superseded #25's
operational and #27). Three commits, including a **reviewer-requested** hardening pass:

- **`src/knitweb/looms/finance/__init__.py`** (new, 148 lines) — the surviving finance loom:
  signed double-entry records.
- **`src/knitweb/looms/operational/__init__.py`** (rewritten, +189/−118) — the
  **multi-resource** operational loom; pricing moved onto `ResourceItem`, no over-allocation.
- **`tests/looms/test_finance.py`** / **`tests/looms/test_operational.py`** — the proofs for
  both looms (multi-resource allocation, double-entry balance).
- Commit history worth keeping: `feat: finance + operational (supersedes #27)` →
  `harden: bind actors and canonical allocation records` → `docs: capacity-only scope
  (Codex review)`. The last two are the equal-level reviewer (Codex) loop in action.

## archive/08-p2p-discovery → PR #45 (MERGED)

- **`src/knitweb/p2p/discovery.py`** — peer-exchange discovery so a node grows its peer set
  instead of relying on a static list. Reason: a "web" must be able to expand past
  hand-configured peers.
- **`tests/property/test_p2p_discovery.py`** — proves peers propagate and the set converges.

## archive/09-test-canonical-fuzz → PR #39 (MERGED, follow-up to #9)

- **`tests/property/test_canonical_fuzz.py`** — randomized fuzz of the canonical-CBOR
  encode/decode round-trip. Tests only, no source change. Reason: the canonical layer carries
  every CID and signature, so it earns property *and* fuzz coverage on top of #9's strict
  decode.

## archive/10-test-origintrail-symbiosis → PR #42 (MERGED, the USP)

- **`tests/property/test_origintrail_symbiosis.py`** — the end-to-end proof of the unique
  selling point: read assets from OriginTrail → compile to signed edge bytecode → anchor the
  result back. Tests only; it ties #40's backend, the compiler, and the anchor receipts into
  one round-trip.

## archive/11-token-loomtoken → **NEVER MERGED (owner-rejected)**

The only branch whose code is **absent from `main`**. It implemented user-issued fixed-supply
tokens on the multi-asset ledger — and was then dropped by explicit owner decision: *"Maak
geen loomtoken."*

- **`src/knitweb/token/loomtoken.py`** (new, 114 lines) — `LoomToken`: a user-issued,
  fixed-supply genesis asset on the multi-asset ledger (Phase 6 experiment). **Not on `main`.**
- **`src/knitweb/token/__init__.py`** (+14/−... ) — the export wiring that would have surfaced
  `LoomToken`. **Not on `main`.**
- **`tests/property/test_loomtoken.py`** (new, 91 lines) — its proofs (fixed-supply genesis,
  no re-mint). **Not on `main`.**
- **`docs/LOC_BY_LANGUAGE.md`** — incidental pre-#29 LOC churn.

**Why it was rejected and why it's still archived:** the user decided against a "LoomToken"
product, and the name collides with the `loom→knitweb` rename anyway (it would fold into
`KnitwebToken` or simply not exist). The branch is preserved so the *idea and its working
implementation* aren't lost — if a user-token primitive is ever wanted, this is the starting
point — but it must **not** be revived under the `LoomToken` name. The native pay-token
**PLS** (#17) is the only token on `main`.

---

## How to inspect any of these

```bash
git fetch origin                                  # or: git fetch pulse
git log   origin/archive/11-token-loomtoken       # the authoring commits
git diff  origin/main...origin/archive/11-token-loomtoken   # what the branch adds vs main
git show  origin/archive/11-token-loomtoken:src/knitweb/token/loomtoken.py
```

(Three-dot `main...branch` shows what the branch adds relative to the merge-base — the right
view for "what's in this branch." Two-dot `main..branch` is misleading here because several
branches were cut from older `main` tips.)

> Note: these `archive/*` branches live in `febuz/pulse`. Whether they are mirrored into
> `knitweb/knitweb` is a migration choice — see [`MIGRATION_PLAN.md`](MIGRATION_PLAN.md). The
> mirror push (`git push --mirror`) would carry them; a selective push would not. Either way
> the authoritative merged history is on `main`.
