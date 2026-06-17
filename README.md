# Knitweb — P2P crypto web layer (Silk tier)

Knitweb is a Python implementation of a P2P credibly-neutral DePIN. Spiders
(participants) weave a knowledge + resource fabric and earn the pay-token by
posting and validating content, complementing
[OriginTrail](https://github.com/origintrail)'s DKG.

## Token

**PLS ("pulses") is the single pay-token.** You spend PLS for activity and earn it
via proof-of-useful-work — posting confirmed knots and validating. No premine; PLS
circulates or burns (90-day inactivity sweep). The ticker **FBR is reserved** for a
possible separate/regional token later and is never the active token (see
`CLAUDE.md`).

## Architecture

```
Fiber (node/vertex)  — 256-bit addr = SHA-256(did)
  ↕  Dot (edge/arc)  — 256-bit addr = SHA-256(sorted(src,dst)+type), undirected
Knot (content unit)  — 2-line post, 256-bit addr = SHA-256(canonical JSON)
```

Three independent 256-bit address spaces → theoretical max **3 × 2²⁵⁶** elements.

### Tiers

- **Silk** — free tier; posts are real (not testnet). Denominated in **PLS**.
- **VPC mainnet** — premium tier; PLS stakes locked for risk-knots.

### Core primitives

| Primitive | Description |
|-----------|-------------|
| `Fiber` | Graph node — a spider/participant, addressed by SHA-256(DID) |
| `Dot` | Graph edge — undirected connection between fibers/knots |
| `Knot` | Content unit — max 2 lines, content-addressed |
| `PulseLedger` | PLS wallet + validation/burn logic (Silk tier) |
| `KnitweaveGraph` | Coordinates Fiber/Dot/Knot registries + PulseLedger |
| `RiskKnotLedger` | PLS staking on uncertain claims (yes/no + resolution) |

## Validation & rewards

1. Spider posts a knot (2 lines max)
2. 3 unique validators confirm it → knot confirmed
3. Poster earns `PULSE_POSTER_REWARD = 5 µPLS`; each validator earns `PULSE_VALIDATOR_REWARD = 2 µPLS`
4. Wallets inactive 90 days → balance burned (deflation)

## Risk-Knots

Uncertain claims can be staked on YES/NO:

| Level | PLS lock |
|-------|----------|
| L1 | 5 µPLS |
| L2 | 50 µPLS |
| L3 | 500 µPLS |

Resolution fires when ≥ 5 votes with ≥ 2/3 consensus. Correct stakers earn
`stake × multiplier + share of losing pool`. 10% of losing pool is burned.

## Layout

```
knitweb/                    Python package (Silk tier)
  addressing.py             addr256(), 256-bit address space
  fiber.py                  Fiber + FiberRegistry
  dot.py                    Dot + DotRegistry
  knot.py                   Knot + KnotRegistry
  pulse.py                  PulseWallet + PulseLedger
  graph.py                  KnitweaveGraph
  market.py                 MarketCap (3×2^256 bounds)
  risk.py                   RiskKnotLedger
tests/python/               Python tests
```

## Running the tests

```bash
pip install -r requirements.txt
python -m pytest tests/python/ -v
```
