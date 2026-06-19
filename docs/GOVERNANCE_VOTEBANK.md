# Governance — the VoteBank, demographic supply, and recency-weighted voting

This is the governance layer (`src/knitweb/govern/`): how the collective makes decisions
without letting any operator mint governance weight out of thin air. It answers three
questions, each as a small, integer-only, float-free module that changes no signed record.

> **TL;DR.** Votes are capped by **real registered people** (national identity *or* a
> "freedom freeport" on-ramp), per world, plus that year's expected births. A **VoteBank**
> holds that supply in treasury and issues it **one-vote-per-person** with no premine. When
> agents vote, **more recent votes weigh exponentially more** than older ones — computed in
> exact integer arithmetic.

## 1. Demographic vote supply (`govern/registry.py`)

The principle is **one vote per registered person**, counted **per world** (earth, moon, …).
The max vote supply, summed over every world, is:

```
max_vote_supply = Σ_world ( registered_persons(world) + expected_births(world, year) )
```

So *1,000,000 registered inhabitants on the moon ⇒ 1,000,000 votes for the moon, plus the
moon's expected births for the year*. The birth allowance lets people born (and registering)
mid-year still receive a vote without re-capping the supply.

A person registers **once**, by one of two paths — and **both count toward the cap**:

| Path | `RegistrationKind` | Identity inputs | Use |
|---|---|---|---|
| **National** | `NATIONAL` | a national-registry id | citizens with state identity |
| **Freedom freeport** | `FREEPORT` | **IMEI + email** + an **ad-hoc proof of identity** | the unbanked / stateless / sovereign |

**One vote per person, worldwide.** The registry de-duplicates on a `subject` digest, so the
same human cannot register twice (e.g. on two worlds, or twice through the freeport) to double
their vote. National `subject` is derived from the national id; freeport `subject` from the
(IMEI, email) pair.

**Privacy.** Raw PII is **never stored**. A `Registration` keeps only content-addressed
digests — a `subject` (dedup key) and a `proof` (evidence the identity/ad-hoc proof was
presented). Each registration is itself content-addressed (`.cid`) for audit.

## 2. The VoteBank (`govern/votebank.py`)

The `VoteBank` "keeps the vote supply in treasury" and issues it, mirroring the discipline of
the native-PLS `Treasury`:

- **No premine.** A fresh bank has issued nothing; the whole supply sits in the bank
  (`treasury_remaining = max_vote_supply − issued`).
- **Demographically bounded.** It can never issue past the registry's `max_vote_supply`.
- **One vote per person.** A `subject` draws its single vote at most once (anti-replay).
- **Auditable.** Every draw is a content-addressed `VoteIssuance`.

There is intentionally **no** raw, ungated way to mint a vote — `issue()` requires the person
to be registered first.

## 3. Recency-weighted tally (`govern/tally.py`)

*When agents vote, more recent votes weigh exponentially more.* A vote's weight decays
**geometrically with its age** (Pulse beats before the tally instant `now`). Because the
project bans floats anywhere near value math, this is an integer **compound decay**:

```
weight(age) = scale;   repeat age times:   weight = weight * num // den
```

with `0 ≤ num < den` (strict shrink). A vote at `now` carries full `scale`; one beat older is
worth `num/den` of it, two beats `(num/den)²`, i.e. a true exponential. Past an optional
`horizon` the weight is 0 (very old votes stop counting, which also bounds the work). The
default halves a vote's weight each beat (`num/den = 1/2`).

The tally enforces **one vote per subject**, rejects votes "from the future" (`beat > now`),
sums the weighted votes per choice, and returns a deterministic winner (ties break to the
lexicographically smallest choice so every honest node agrees). It is **advisory/pure** — it
only counts votes upstream produced (e.g. drawn from the `VoteBank`).

## 4. Crowdfunding on the votebank (`govern/crowdfund.py`)

The same one-person-one-vote rule, applied to *funding*: **one person, one backing**. Ordinary
token crowdfunding is plutocratic (most capital wins); votebank crowdfunding measures **breadth
of real backers** alongside capital, so a whale can register once like everyone else but cannot
manufacture support.

A `Campaign` (bound to a `VoteBank` for its registry) succeeds only when it clears **both**:

- a capital **`goal`** (sum of PLS-wei pledged), and
- a **`min_backers`** breadth threshold (distinct registered backers — national *or* freeport),

by its **`deadline`**. Settlement is **all-or-nothing**: met ⇒ the escrow releases to the
beneficiary; not met ⇒ every backer is refunded. Nothing is minted (no premine — the pool is
exactly what was pledged), and like `pouw/dispute.py` this is **advisory integer accounting**:
`resolve()` returns who is owed what in PLS-wei; the caller moves it with Knits.

`Campaign.momentum(now, decay)` reuses the governance tally so **recent backing weighs
exponentially more** — a campaign gaining backers *now* reads hotter than a stalled one —
without affecting the all-or-nothing settlement.

## Why these choices

- **Anchored supply, not fiat.** Tying the cap to registered humans + births is what keeps
  governance credibly neutral — nobody can inflate their weight without real people behind it,
  exactly as native PLS has no premine.
- **Freeport inclusion.** A web that excludes the unbanked/stateless isn't credibly neutral;
  the IMEI+email+ad-hoc-proof on-ramp lets them register and counts them in the cap.
- **Integer exponential.** Recency weighting *has* to be float-free to stay on the project's
  deterministic, cross-node-reproducible value path; the compound `*num//den` per beat is
  exact and bounded.

> **The same shape, generalised.** This recency decay is one instance of a single geometric
> time-value law that also governs fiat inflation (purchasing-power decay), declining-balance
> depreciation, discounting, and token emission — all linear in log space. See
> `docs/research/09-time-value-and-relevance.md` for the crypto/economic treatment and the
> proposed shared integer primitive.

## Proofs

`tests/property/test_govern_votebank.py` — no premine; national + freeport both count;
one-vote-per-person dedup across worlds and across the freeport pair; moon supply =
persons + expected births; issuance never exceeds the cap; geometric weight decay; horizon
cut-off; recent votes win; one-vote-per-subject and future-vote rejection in the tally; and a
full register → issue → recency-weighted vote loop.

`tests/property/test_govern_crowdfund.py` — no premine/conservation; only registered people may
back; one backing per person (no whale stuffing); capital-met-but-breadth-missing expires;
underfunded refunds everyone; funded releases all to beneficiary; resolve idempotent and closes
pledging; freeport backers count for breadth; momentum weights recent backing more.
