# Migration prep — `febuz/pulse` → `github.com/knitweb/knitweb`

## Context

The crypto project's git history is clean, but its *identity* is tangled: it lives in
repo **`febuz/pulse`** while the Python package, brand, and (now) the GitHub org are all
**`knitweb`**. `febuz/knitweb` was renamed to `pulse` and the old name now redirects
oddly; the local `origin` still points at the stale `febuz/knitweb`. We are giving the
project its own home — the **`knitweb` GitHub org already exists** — by moving it to
**`github.com/knitweb/knitweb`**, finally aligning **org = repo = package = `knitweb`**
(resolves inconsistency #24) and giving a clean base for the consistency pass + the
`loom → knitweb` rename.

**Owner decisions (made):**
- **Method = fresh mirror push** to a new `knitweb/knitweb` (NOT a GitHub transfer). Issues/PRs
  are *not* carried; the 3 open drafts are re-pushed + re-opened; old URLs do **not** auto-redirect,
  so every remote/link must be repointed.
- **Rename = literal `loom → knitweb` everywhere**, including the core `Loom` primitive (accept the
  `knitweb`-inside-`knitweb` overload).

## Current state (verified)

- `febuz/pulse` `main` = `0e164dd` (#46); ~30 branches; **3 open drafts**: #32 `pouw/dispute-window`,
  #36 `docs/research-gate-finance`, #41 `claude/pulse-knitweb-init` (finance settlement links).
- `github.com/knitweb` = **Organization (exists)**; `knitweb/knitweb` repo = **does not exist yet**.
- Full history safe-bundled: `/media/knight2/EDS2/backups/knitweb-crypto-mainline.bundle` (81 MB, verified).
- Local remotes: `origin → git@github.com:febuz/knitweb.git` (stale/redirecting), `pulse → febuz/pulse` (canonical).
- In-flight (uncommitted, worktree `…/tmp/knitweb-consistency`, branch `fix/consistency-pass-01`):
  the **consistency-pass PR #1** (≈85% done — PLS/Fiber/network/status edits + `pouw/scheduler.py` added).

## Hard invariant (unchanged)

No canonical/**signed-record** field/key/value changes. Verified: **no record `kind`/field contains
"loom"** (kinds are `reaction-knowledge`/`supplychain-process`/`capacity-allocation`/`journal-entry`/
`invoice`; `Knit.to_record` has the `network` id field but no "loom"). Therefore the `loom→knitweb`
rename is **identifier + docstring + filename only — zero CID/signature impact**. The `network` id
field stays (the one allowed technical "network" use).

## Phase 1 — Freeze & inventory (do first; mostly done)

1. **Freeze new feature work above #40** while migrating (the 3 drafts + cleanup PRs are the only in-flight work).
2. Backup bundle exists ✓. Refresh it once more at cutover (`git bundle create … --all`).
3. Snapshot the branch + PR inventory of `febuz/pulse` (for re-creating the 3 drafts on the new repo).

## Phase 2 — Create & seed `knitweb/knitweb` (owner + agent)

1. **Owner action:** create empty repo `knitweb/knitweb` in the org (no README/license, to allow a clean push),
   grant the working account push access.
2. Add the remote and **mirror-push** the canonical history + branches:
   `git remote add knitweb git@github.com:knitweb/knitweb.git` then `git push knitweb --mirror`
   (pushes `main` @ `0e164dd` + all branches/tags). Confirm `main` is the default branch on the new repo.
3. **Re-open the 3 drafts** as fresh PRs on `knitweb/knitweb` (their branches arrive via the mirror):
   #32 dispute-window, #36 docs-research-gate, #41 finance-settlement-links — re-create with their
   prior descriptions; note they were re-homed (no transfer redirect).

## Phase 3 — Reconcile remotes, metadata & tooling (agent)

1. **Local remotes:** set `origin → git@github.com:knitweb/knitweb.git`; drop/retire the stale
   `febuz/knitweb` and `pulse` remotes (keep `pulse` read-only briefly as a fallback, then remove).
2. **Metadata → new home:** `pyproject.toml` `[project.urls]` (Homepage/Repository/Documentation) →
   `https://github.com/knitweb/knitweb` + `/tree/main/docs`; `authors` = real maintainer;
   bump `version` 0.0.1 → 0.6.0 (+ `CHANGELOG.md`).
3. **Doc/agent refs:** repoint `febuz/pulse`/`febuz/knitweb` mentions in `README.md`, `CLAUDE.md`,
   `docs/*` to `knitweb/knitweb`; update the README "repo vs package" note to say they now match.
4. **Coordination + loop:** the `~/.claude/coordination` lease names already use `knitweb/<lane>` (fine);
   update the session cron/`/loop` prompt and any scripts that reference `febuz/pulse`.

## Phase 4 — Land the cleanup PRs on the new home (agent, in order)

1. **PR #1 — consistency pass** (`fix/consistency-pass-01`, in-flight): finish the remaining edits
   (08-knitweb.md FBR-active→reserved + Yarn/stitch non-normative note + L2/L6 tables; README status ✓;
   pyproject metadata; `docs/ROADMAP.md` TODO now recording the **decided** `loom→knitweb` rename).
   Set `[project.urls]` to `knitweb/knitweb`. Keep `tests/property` green. Ship as PR #1 on the new repo.
2. **PR #2 — `loom → knitweb` literal rename** (dedicated): mechanical repo-wide sweep —
   `ledger/loom.py → ledger/knitweb.py`, `Loom`/`LoomError` → `Knitweb`/`KnitwebError`,
   `looms/ → knitwebs/`, `*Loom` classes → `*Knitweb`, the `loom` pytest marker → `knitweb`, and all
   prose. **Do NOT touch any `to_record`/record `kind`** (none contain "loom" — re-verify with a grep
   gate in the PR). Run the full suite; CIDs/signatures must be byte-identical before/after (add a quick
   assertion that a sample record's `cid` is unchanged). Update `import` sites for the
   `knitweb.ledger.knitweb` submodule overload.

## Risks

- **No auto-redirect** (mirror, not transfer): stale links/clones to `febuz/pulse` break — must repoint
  every remote/CI/doc ref; announce the new URL.
- **Multi-agent coordination:** Codex/cloud agents still push to `febuz/pulse`; the cutover must be
  announced so all agents switch to `knitweb/knitweb` (else work splits across two repos).
- **Rename overload:** `knitweb.ledger.knitweb.Knitweb` is confusing but owner-accepted; the grep gate
  (no signed-record "loom") keeps it safe.
- **In-flight worktree:** the consistency-pass branch lives in a local worktree off `pulse/main`; re-base
  it onto `knitweb/main` after the mirror so PR #1 targets the new repo.

## Verification

- `git clone git@github.com:knitweb/knitweb.git` fresh → `PYTHONPATH=src pytest -q` all green (~250).
- `git ls-remote knitweb` shows `main` @ `0e164dd` + all branches; default branch = `main`.
- `pip install -e .` exposes the `knitweb` CLI; `knitweb compile … && knitweb edge-load …` round-trips.
- After PR #2: `grep -rn -i loom src/ tests/` returns nothing; a sample reaction/knit record's `cid`
  matches its pre-rename value (signed-byte invariant held).
- 3 drafts re-opened on the new repo; `febuz/pulse` archived/retired with a pointer to the new home.
