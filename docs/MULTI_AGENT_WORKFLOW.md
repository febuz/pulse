# Multi-agent workflow — keeping concurrent agents off each other's toes

Several autonomous agents (a local Opus loop, a cloud routine, occasional helpers)
build Knitweb in parallel. Without discipline they clobber each other — duplicate
modules, force-pushed branches, lost work. This is the protocol every agent (and
human) follows so concurrent work stays mutually exclusive.

## 1. Claim a lease before editing

Mutual exclusion is enforced with the shared coordination tool:

```bash
COORD=~/.claude/coordination/coord.py
CLAUDE_AGENT_ID=<your-id> python3 $COORD claim knitweb/<lane> --note "what you're doing"
#   exit 0 = the lane is yours;  exit 1 = held by another agent -> pick a different lane
# ... do the work, push, open the PR ...
python3 $COORD release knitweb/<lane>
```

- **Lanes** scope the lease to disjoint areas so two agents *can* work at once:
  `knitweb/ledger`, `knitweb/feed`, `knitweb/pouw`, `knitweb/edge`, `knitweb/core`,
  `knitweb/docs`. Use a lane when your files don't overlap another's; use the bare
  `knitweb` only when unsure.
- Leases have a 1 h TTL (a crash never blocks forever). Re-claim to refresh on long
  sessions; `release` when done.
- `python3 $COORD status` shows who holds what. Never `--force` past a live lease
  unless you've confirmed the holder stopped.

## 2. One isolated branch per increment, off `main`

- **Never edit on `main` and never push to `main` directly.** `main` is the merge
  target; pushes to it bypass review (and are blocked for autonomous agents).
- Branch each increment off the *current* `main`: `git checkout main && git checkout
  -b <type>/<slug>`. Keep increments independent so they don't need each other to
  merge (e.g. `ledger-network-id`, `phase3-feed-core`, `harden/canonical-decode-strict`).
- If you must build on unmerged work, stack on *your own* branch and say so in the
  PR; rebase when the parent merges. Don't stack on another agent's open branch.

## 3. One reviewable PR per increment

- Every increment ships as a PR with: what/why, the proof (tests + green count),
  and explicit **review asks for Codex** (the equal-level reviewer).
- Implement agreed review feedback as the leading engineer; push back with reasoning
  when you disagree. Be harmonious — accept what's possible, give benefit of the doubt.
- Refresh `tools/loc_report.py` → `docs/LOC_BY_LANGUAGE.md` and keep the full suite
  green (`PYTHONPATH=src pytest -q`) in every PR.

## 4. Don't pile up unbounded unreviewed work

A handful of independent open PRs is fine and good for throughput. But before adding
*another* change on top of an unreviewed **foundational** PR (core encoding, signing,
ledger), prefer to wait for review or pick an orthogonal lane — otherwise review and
rebase debt compounds. When in doubt, open the orthogonal PR rather than the stacked one.

## 5. Stay aware of the others

- `git fetch` and check `git branch -r` + `gh pr list` before starting, so you build
  on the latest `main` and don't duplicate an in-flight branch.
- The local loop is re-armed via `/loop` (session cron, every 12 h); a cloud routine
  may also be building. Assume you are *not* alone on the repo.
