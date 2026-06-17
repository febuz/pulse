# Knitweb migration handoff — `febuz/pulse` → `github.com/knitweb/knitweb`

> **This branch (`docs/pulse-migration-handoff`) is the migration record left in the OLD
> `pulse` repo. It is deliberately NOT migrated to `knitweb/knitweb`.** It exists so the
> migration can be finished from another machine/server with zero context loss. Everything
> needed to continue is here, in `docs/migration/`:
> - `MIGRATION.md` (this file) — the runbook + status,
> - `MIGRATION_PLAN.md` — the full approved migration plan,
> - `CLAUDE.snapshot.md` — the project agent-guide (`CLAUDE.md`) as of the freeze,
> - `PROJECT_MEMORY.md` — durable project facts/decisions,
> - `CONTINUATION.md` — the working model + hard invariants for whoever picks this up.

## What is being migrated, and why

The crypto's git history is clean but its identity was tangled: code in repo **`febuz/pulse`**
while package + brand + org are all **`knitweb`** (and `febuz/knitweb` was renamed to `pulse`,
its old name now redirecting oddly). We are giving it its own home — the **`knitweb` GitHub org
exists** — at **`github.com/knitweb/knitweb`**, aligning **org = repo = package = `knitweb`**.

## Owner decisions (locked)

| Decision | Value |
|---|---|
| New home | `github.com/knitweb/knitweb` (org `knitweb` exists; repo created, empty) |
| Method | **Fresh mirror push** (NOT a GitHub transfer). Issues/PRs not carried; 3 drafts re-opened; **no auto-redirect** → repoint every remote/link |
| Active token | **PLS** ("pulses"). Ticker **FBR is reserved and NOT active**. |
| `Fiber` | brand coin name, but the `Fiber` *primitive* = immutable account-state commitment (a `Braid` link), never itself transferred; value moves as a `symbol` balance via `Knit` |
| Rename | **`loom` → `knitweb` literally, repo-wide** incl. the core `Loom` primitive (accept `knitweb.ledger.knitweb.Knitweb` overload). Dedicated PR, NOT the consistency pass. |
| LoomToken | dropped ("Maak geen loomtoken"); `token-loomtoken` branch is local-only, never merged |
| Freeze | **freeze all new feature work above PR #40** during the migration |

## Current state (at handoff)

- **`febuz/pulse` `main` = `fa0e511`** (#32 dispute-window merged); ~39 branches.
- **`knitweb/knitweb`** = created, **EMPTY** (mirror push not yet run).
- **Backup bundle** (full history incl. `fix/consistency-pass-01`):
  `/media/knight2/EDS2/backups/knitweb-crypto-mainline.bundle` (≈81 MB, `git bundle verify` OK)
  — **on this server only; copy it off-box to continue elsewhere without GitHub.**
- **Local bare mirror staged**: `/media/knight2/EDS2/tmp/pulse-mirror.git` (39 branches) — push source.
- **PR #1 (consistency pass) READY**: branch `fix/consistency-pass-01`, 3 commits, **255/255 tests green**,
  all 25 inconsistencies (A–J) fixed. In the bundle. Not yet pushed (waits for the new repo).

## The runbook — finish in order

**Step 0 (gated on owner — the auto-mode guardrail blocks the agent from bulk-pushing to a
brand-new remote).** Run the mirror push:
```bash
git -C /media/knight2/EDS2/tmp/pulse-mirror.git push --mirror git@github.com:knitweb/knitweb.git
# (from another server: clone the bundle or febuz/pulse first — see CONTINUATION.md)
```
Confirm `knitweb/knitweb` default branch = `main` @ the `fa0e511` lineage.

> ⚠️ `--mirror` copies ALL branches, including THIS `docs/pulse-migration-handoff` branch.
> To keep it pulse-only as intended, delete it on `knitweb/knitweb` after the push
> (`git push knitweb :docs/pulse-migration-handoff`), or push a curated branch set instead.

**Step 1 — remotes & metadata** (agent): set local `origin → git@github.com:knitweb/knitweb.git`;
retire stale `febuz/knitweb`/`pulse`; `pyproject [project.urls]` already point to `knitweb/knitweb`
(in PR #1); repoint remaining `febuz/pulse` doc refs.

**Step 2 — PR #1**: rebase `fix/consistency-pass-01` onto the new `main`; open it. (Conflicts likely
only in docs/`pyproject` from `fa0e511`'s newer commits; resolve prose, keep tests green.)

**Step 3 — re-open the 3 drafts** on the new repo (their branches arrive via the mirror):
#32 `pouw/dispute-window` (note: #32 already merged to `fa0e511` — verify), #36 `docs/research-gate-finance`,
#41 `claude/pulse-knitweb-init` (finance settlement links). Re-create with prior descriptions.

**Step 4 — PR #2: the `loom → knitweb` literal rename** over the COMPLETE current code:
`ledger/loom.py → ledger/knitweb.py`, `LoomError → KnitwebError`, `looms/ → knitwebs/`,
`*Loom` classes → `*Knitweb`, the `loom` pytest marker → `knitweb`, `tests/looms/ → tests/knitwebs/`,
all prose, and the brand vocab `Web · Loom · Knit · Pulse · Fiber` → `Web · Knitweb · Knit · Pulse · Fiber`.
**Hard gate:** `grep -rn -i loom src/ tests/` returns nothing AND a sample reaction/knit record's `cid`
is byte-identical before/after (no signed-record `kind`/field contains "loom" — verified: kinds are
`reaction-knowledge`/`supplychain-process`/`capacity-allocation`/`journal-entry`/`invoice`). Full suite green.

**Step 5 — cutover**: announce the new URL to all agents (Codex/cloud) so work stops splitting across
two repos; archive/retire `febuz/pulse` with a pointer to `knitweb/knitweb` (and to this branch).

## The 25 inconsistencies (A–J) — fixed in PR #1

A FBR→PLS staleness (crypto/pulse/web docstrings, test_crypto, 08-knitweb) · B Fiber-as-coin →
state-commitment + unified vocabulary + Yarn/stitch marked non-normative · C "network" prose →
web/fabric (canonical/loom/braid/node/store/PROOF) + the `network`-id-field carve-out · D
`pouw/scheduler.py` added (compute guardrail) + epoch-mint claim reconciled · E/I README status +
08 layer tables (L1 incl. node, L2 = asyncio, L6 = PLS+Fiber+user-tokens+anchors) + FBR-wei→PLS-wei ·
F/J `pyproject` version 0.6.0 + author + `[project.urls]` → knitweb/knitweb + CHANGELOG + repo/package note ·
#23 `loom→knitweb` rename recorded as the dedicated PR #2 (see `docs/ROADMAP.md`).

See `MIGRATION_PLAN.md` for the full plan and `CONTINUATION.md` for how to resume from elsewhere.
