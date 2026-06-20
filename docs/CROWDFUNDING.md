# Crowdfunding — privacy-preserving fundraising on the personhood foundation

The crowdfunding L5 application (`knitweb.knitwebs.crowdfunding`) is the second consumer of
`knitweb.personhood`, alongside vBank voting (`VBANK.md`). It lets a community raise funds where
every pledge is backed by a revocable proof of unique-EU-personhood — so a campaign can prove
each pledge came from a distinct verified natural person (anti-sybil / light-KYC for
Reg. (EU) 2020/1503) **without any identity on the fabric**.

Scope: this models **donation/reward** fundraising (integer ``amount`` in PLS-wei). Investment
or lending flows need regulatory review and are out of scope.

## The end-to-end flow

```
authority defines a campaign ──▶ crowdfunding-campaign (signed: goal, window)
pledger enrols once ──▶ personhood gate ──▶ PersonhoodTicket (scoped nullifier, no PII)
pledger pledges (repeatable) ──▶ CrowdfundingKnitweb.emit (gated, signed by the pairwise key)
                                 └▶ crowdfunding-pledge ──▶ web.weave(...)
tally time ──▶ collect_pledges(web, scope) ──▶ certify_outcome(campaign, pledges)
            └▶ crowdfunding-outcome (signed: total_raised, goal_met, counts, pledge_root)
anyone ──▶ verify_outcome / audit_outcome  (independently recompute + check the signature)
```

Runnable demo: `PYTHONPATH=src python examples/crowdfunding_demo.py` (exit 0 ⇒ works).

## Record kinds (integer/bytes/bool, canonical CBOR, signatures outside the record)

**`crowdfunding-campaign`** — signed by the campaign **authority**:
`kind, scope (campaign id), goal (PLS-wei, > 0), opens_at, closes_at, beneficiary (pls1 addr
funds release to on success, optional), authority`.

**`crowdfunding-pledge`** — gated by a personhood ticket, signed by the pledger's pairwise key
(`actor`): `kind, scope, amount (PLS-wei, > 0), actor, scope_nullifier, pledged_at`. **No
identity** — only the scoped nullifier + the per-scope pairwise address.

**`crowdfunding-outcome`** — signed by the defining authority: `kind, scope, campaign_cid,
authority, goal, total_raised, goal_met, pledger_count (distinct nullifiers), pledge_count
(in-window pledges), pledge_root`.

**`crowdfunding-settlement`** — signed by the defining authority: `kind, scope, campaign_cid,
outcome_cid, authority, mode (release|refund), total_amount, entry_count, settlement_root
(Merkle over per-payee (pledge_cid, payee, amount))`.

## Properties

- **One verified person, many pledges** — unlike a vote, pledges are *not* deduped on the
  nullifier; ``total_raised`` sums all in-window pledges, ``pledge_count`` counts them, and
  ``pledger_count`` reports the distinct verified people behind them.
- **Pledging window** — only pledges with `opens_at <= pledged_at < closes_at` are counted.
- **Goal** — ``goal_met`` is ``total_raised >= goal``.
- **Deterministic + order-independent** — same pledge set ⇒ same outcome CID.
- **Public audit trail** — ``pledge_root`` (Merkle over counted pledge CIDs) + ``verify_outcome``
  let anyone recompute the outcome from the campaign + pledges; ``audit_outcome`` adds the
  signature check.
- **All-or-nothing settlement** — a campaign declares a ``beneficiary``; ``settle()`` recomputes +
  matches the certified outcome, then signs a ``crowdfunding-settlement`` instructing **release**
  to the beneficiary if the goal was met or **refund** to each pledger if not (per-payee amounts
  committed in a ``settlement_root``; ``verify_settlement``/``audit_settlement`` check it). It is
  the deterministic instruction a payout layer executes — wiring it to actual ledger ``Knit``
  transfers (pledge-time PLS escrow + a per-payee accept handshake) is a designed future step.
- **Zero PII on the fabric** — enforced by the personhood layer.

## API surface (`knitweb.knitwebs.crowdfunding`)

- `CrowdfundingCampaign(authority_priv, scope)` — `define(Campaign)`,
  `certify_outcome(campaign_record, pledges)`, `weave_outcome(campaign_record, pledges, web)`.
- `CrowdfundingKnitweb(scope)` — `emit(pledge, ticket, pledger_priv)`,
  `weave(pledge, ticket, pledger_priv, web)`.
- `collect_pledges(web, scope)` — read woven pledges back out.
- `verify_outcome(...)` / `audit_outcome(...)` — independent audit.
- `CrowdfundingCampaign.settle(outcome_record, campaign_record, pledges)` — sign the all-or-nothing
  settlement; `verify_settlement(...)` / `audit_settlement(...)` — independent audit.

## Trust model

Inherits the personhood foundation's posture (trusted-RP now, ZK seam later; zero PII; race-free
revocation). The **campaign authority** is trusted to include the correct pledge set when
certifying, but the outcome is independently recomputable (`verify_outcome`) and the counted set
is committed (`pledge_root`), so a dishonest certification is detectable by any auditor.

## Run

```bash
PYTHONPATH=src python examples/crowdfunding_demo.py                          # the whole loop
PYTHONPATH=src python -m pytest tests/property/test_crowdfunding_*.py -q     # the test suite
```
