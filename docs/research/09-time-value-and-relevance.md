# Paper 9 — Time, Relevance, and Value: one geometric law for votes, money, and assets

**Status:** Research note v0.1
**Scope:** Why a vote's *relevance*, fiat's *purchasing power*, an asset's *book value*,
and a token's *emission* are all the **same shape** — geometric change in time — how that
shape is **linear in log space** (the "log price" observation), and how Knitweb encodes it as
an **exact integer ratio per Pulse beat** so it stays on the float-free value path.

> **Vocabulary & discipline.** Knitweb is a *web*, never a "network". Money and state are
> integers (PLS-wei); **no floats anywhere near hashing, balances, or canonical encoding**
> (`core.canonical` rejects them). Logarithms are therefore an *analytics lens*, never a
> canonical/value-path artifact — see §6–§7. External chains (Bitcoin/Ethereum) are named only
> in comparison, which the vocabulary rule permits.

This note generalises the recency-weighting already shipped in `govern/tally.py` (more recent
votes weigh exponentially more — Paper-adjacent: `docs/GOVERNANCE_VOTEBANK.md`) into a single
economic primitive that also expresses inflation, depreciation, and emission.

---

## 1. The one shape: geometric change in time

Take any quantity whose *significance per unit time changes by a constant proportion*. Call the
per-beat factor `r` and the starting amount `v₀`. After `t` beats:

```
V(t) = v₀ · r^t
```

That single law, with different `r`, is every concept in this note:

| Phenomenon | What decays/grows | Per-beat factor `r` |
|---|---|---|
| **Vote relevance** (recency weighting) | a vote's influence | `r < 1` (older ⇒ less) |
| **Fiat purchasing power** under inflation | what one unit *buys* | `r = 1/(1+i) < 1` |
| **Asset book value** (declining-balance depreciation) | what an asset is *worth on the books* | `r = 1 − d < 1` |
| **Nominal price / token stock / emission** | a *level* that compounds up | `r > 1` |
| **Discount factor** (present value) | the weight on a future cash flow | `r = 1/(1+k) < 1` |

`r < 1` is **decay** (relevance, purchasing power, book value); `r > 1` is **growth** (price,
supply stock). They are the same function read in two directions — and that symmetry is the
whole point: *the engine that down-weights stale votes is the engine that discounts future
money and the engine that depreciates a worn asset.*

## 2. Relevance decay **is** discounting

The governance tally weights a vote cast `age` beats ago by `r^age` with `r = num/den < 1`
(`govern.tally.Decay`). Read economically, that is exactly a **discount factor**: a belief, like
a cash flow, is worth less the further it sits from "now". "Recent votes matter exponentially
more" and "near cash flows are worth exponentially more than distant ones" are *one sentence*.
So the governance layer is not a special case — it is the first consumer of a general
time-value primitive.

## 3. Fiat: inflation as purchasing-power decay

Fiat money has no supply cap; central banks expand the money base, and consumer prices rise.
Inflation is usually quoted as a *price index* (CPI) that **grows** geometrically, `P(t) =
P₀·(1+i)^t`; the **purchasing power** of one unit is its reciprocal, which **decays**
geometrically, `1/(1+i)^t`. Two readings of one `r`:

- **Nominal vs real.** A nominal amount `A` at beat `t` is worth `A/(1+i)^t` in beat-0 units.
  This is identical in form to discounting (§2) — inflation *is* a discount rate on money's
  usefulness.
- **Why fiat is the `r<1` case par excellence.** With positive structural inflation, holding
  cash is a guaranteed geometric loss of relevance — the same curve as a vote going stale.

## 4. Assets: straight-line vs declining-balance depreciation

Accounting offers two depreciation shapes, and the contrast is exactly linear-vs-geometric:

- **Straight-line** writes off an *equal amount* each period — a **linear** decline. Good for
  assets that yield evenly (buildings, furniture).
- **Declining-balance** writes off a *fixed percentage of the remaining book value* each period
  — `BookValue(t) = cost·(1−d)^t`, an **exponential/geometric decay** that approaches but never
  reaches zero. Highest expense in year one; suited to fast-obsoleting assets (hardware,
  vehicles). To fully recover cost, practice imposes a **salvage floor** or switches to
  straight-line near the end.

Declining-balance is `V(t)=v₀·r^t` with `r = 1−d`. It is the *same primitive* as vote-relevance
decay — only the interpretation ("book value" vs "influence") and the floor differ. Knitweb's
`Decay` already floors to 0 past a `horizon`; a salvage floor is the economic twin of that
cut-off.

## 5. Crypto: monetary policy as programmable time-value

Crypto makes the `r` **explicit and on-chain** — that is its novelty over fiat. Three regimes:

- **Bitcoin — predictable scarcity.** Supply is capped (21M), and the issuance *flow* halves
  on a schedule (≈ every four years): the inflation rate itself decays geometrically by exactly
  ½ each halving. "Stock-to-flow" (existing stock ÷ annual new flow) is the scarcity ratio that
  rises as flow halves; by 2025 it's treated as a conceptual scarcity frame, not a price oracle.
- **Ethereum — demand-responsive net supply.** Proof-of-stake cut issuance > 85%, and EIP-1559
  *burns* base fees. Net supply change = issuance − burn: deflationary under heavy use, mildly
  inflationary when activity is low. Staking adds a ~4–6%/yr yield (a *growth* `r>1` on a stake).
  So ETH's effective `r` is not a constant — it is a *function of demand*.
- **Knitweb PLS — demand-gated, bounded, no premine.** Native PLS is minted *only* as a bounded
  reward for verified useful work (`token/mint.py`): mint ≤ escrow consumed, and ≤ an optional
  `max_supply`. There is no time-based emission curve today. The natural next step (already
  flagged in `CLAUDE.md` as "planned, not implemented") is to **bind a mint cap to a Pulse
  Beat/epoch** — i.e. give PLS an explicit per-epoch `r` for emission, exactly the geometric
  primitive of §1, so issuance has a *known, auditable* time-shape like Bitcoin's, while staying
  demand-gated like Ethereum's. This note's primitive is what that wiring should reuse.

The throughline: **fiat hides `r` (and lets it drift); crypto publishes `r`.** Knitweb's
contribution is to publish `r` as an *exact integer ratio* with no floating-point ambiguity.

## 6. The log-price observation: constant growth + inflation ⇒ a straight line

Take logs of the one law:

```
V(t) = v₀ · r^t        ⇒        log V(t) = log v₀ + t · log r
```

**Geometric in price becomes linear in log-price.** Constant growth (or constant inflation, or
constant decay) is a *straight line* in log space with slope `log r`. This is why finance works
in **log returns**: the log return of consecutive periods is `log(Pₜ/Pₜ₋₁)`, and these are
**additive** across time — the log return over many beats is just the sum of the per-beat log
returns, and continuous compounding is the `r = e^{g}` limit. Simple (arithmetic) returns are
*multiplicative* and don't add up: +20% then −16.7% is back to start, yet naive addition reads
+3.3%. Logs remove that distortion and are closer to normally distributed, which is why models
live in log space.

Two consequences that matter for this project:

1. **Inflation-adjustment is subtraction in log space.** Real log-price = nominal log-price −
   inflation log-line. If nominal value compounds at `log r_nom` and prices at `log(1+i)`, the
   *real* slope is just `log r_nom − log(1+i)`. Two straight lines, subtracted. This is the
   precise sense of the prompt's "log price works with constant growth and inflation": under
   constant rates, nominal growth, inflation, and real growth are three parallel-ish lines and
   you move between them by adding/subtracting slopes.
2. **Relevance, value, and supply share an axis.** Plot vote-relevance decay, purchasing-power
   decay, book-value depreciation, and token-stock growth on a log axis and they are all
   **straight lines** — downward-sloping for the `r<1` family, upward for `r>1`. The governance
   recency-decay and a token's emission curve are *the same line with opposite sign of slope*.

## 7. Keeping it on Knitweb's float-free value path

Logs are real-valued and irrational — they **cannot** touch the canonical/value path
(`core.canonical` rejects floats; balances are PLS-wei integers). So the division of labour is:

- **Canonical artifact = the exact integer geometric factor.** Represent `r` as a ratio
  `num/den` and apply it once per beat by integer compounding (the method `govern.tally.Decay`
  already uses), giving an exact, node-reproducible index:

  ```
  index(t):  w = scale;  repeat t times:  w = w * num // den      # r = num/den
  present_value(amount, t)      = amount * index_decay(t) // scale # discount / inflation / depreciation
  compounded_level(level0, t)   = level0 grown by num/den (num>den)# price / emission stock (r>1)
  ```

  Same arithmetic the vote tally runs — now reused for a discount factor, a CPI-style index, a
  declining-balance schedule, or a per-epoch emission cap. Deterministic across nodes, no float,
  hashable.
- **Logarithms = analytics/edge lens only.** Charting, normality assumptions, return
  aggregation, and "is this line straight?" diagnostics live in research/edge tooling (where
  floats are fine), *never* in `core`, `ledger`, or `token`. Logs *describe* the integer index;
  they never *compute* a balance.

This is the reconciliation the prompt asks for: **value-over-time is one geometric law; Knitweb
stores it as an exact integer ratio per Pulse beat, and uses logs only to look at it.**

## 8. Proposed integration (engineering)

A single primitive — call it `econ/timevalue.py`, a `Geometric(num, den, scale, horizon)` —
generalising `govern.tally.Decay` to both directions (`num<den` decay, `num>den` growth) and
exposing `factor(t)`, `present_value(amount, t)`, `compound(level, t)`. Consumers:

- **Governance** (`govern.tally`) — vote recency decay (today's `Decay` becomes the `num<den`
  case).
- **Token** (`token.mint`) — an optional per-Pulse-epoch emission/decay cap (the planned
  beat-bound mint cap), giving PLS a published, integer `r`.
- **Finance/operational knitwebs** (L5) — discounting and declining-balance depreciation for
  domain models, all integer and auditable.

Kept as a *proposal* here (no speculative code without a consumer); the math, the integer
formulation, and the float boundary above are the spec. Each landing would ship with property
proofs in the proofs-first style (e.g. `index(t)·index(s) ≈ index(t+s)` up to integer floor;
discount monotonic; growth/decay inverse).

## 9. Open research directions

- **Drift vs constant `r`.** Real inflation and demand-responsive emission (ETH) are *not*
  constant `r`. Model them as a *piecewise-geometric* index (a new ratio per epoch) — still
  float-free, still a sum of straight log-segments.
- **Coupling relevance to value.** Should a stale vote's *weight* and a stale claim's *bounty*
  decay on the *same* `r`? A shared time-value makes "freshness" a single tunable across
  governance, curation rewards, and emission.
- **Salvage floors vs horizons.** Unify the depreciation salvage floor and the tally `horizon`
  as one "minimum-relevance" parameter.
- **Stock-to-flow as a ratio of two indices.** Express scarcity as `stock(t)/flow(t)` where both
  are integer geometric indices — a clean, auditable scarcity gauge for a future PLS schedule.

---

### Sources

- Log vs simple returns (additivity, continuous compounding, the +20%/−16.7% distortion):
  [365 Financial Analyst — Logarithmic returns][log], [Moontower — Understanding Log Returns][log2].
- Declining-balance vs straight-line depreciation (geometric decay, salvage floor / switch):
  [FasterCapital — Declining Balance vs Straight Line][db].
- Crypto monetary policy (BTC halving/stock-to-flow; ETH EIP-1559 burn + PoS staking, net
  supply): [VanEck — Bitcoin vs Ethereum][btc], [Bit Digital — Ethereum's deflationary supply][eth].

[log]: https://365financialanalyst.com/knowledge-hub/corporate-finance/log-return/
[log2]: https://moontowermeta.com/understanding-log-returns/
[db]: https://fastercapital.com/content/A-Comparative-Analysis--Declining-Balance-Method-vs--Straight-Line-Method.html
[btc]: https://www.vaneck.com/us/en/blogs/digital-assets/bitcoin-vs-ethereum/
[eth]: https://bit-digital.com/blog/understanding-ethereum-deflationary-supply/
