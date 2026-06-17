# Knitweb — P2P crypto web layer

Knitweb is a Python + TypeScript implementation of a P2P credibly-neutral DePIN.
Spiders (participants) sell verifiable GPU compute and weave a knowledge + resource
fabric, complementing [OriginTrail](https://github.com/origintrail)'s DKG.

## Tokens

| Token | Role |
|-------|------|
| **PLS (Pulse)** | Pay-token. Spend for activity (compute, relay, storage). Earned via proof-of-useful-work. No premine. |
| **Fiber (FBR)** | Free silk-tier token. Earned by posting confirmed knots and validating. Deflates via 90-day burn. |

## Architecture

```
Fiber (node/vertex)  — 256-bit addr = SHA-256(did)
  ↕  Dot (edge/arc)  — 256-bit addr = SHA-256(sorted(src,dst)+type), undirected
Knot (content unit)  — 2-line post, 256-bit addr = SHA-256(canonical JSON)
```

Three independent 256-bit address spaces → theoretical max **3 × 2²⁵⁶** elements.

### Layers

- **Silk** — free tier, FBR token, posts are real (not testnet)
- **VPC mainnet** — premium tier, PLS token, locked stakes for risk-knots

### Core primitives

| Primitive | Description |
|-----------|-------------|
| `Fiber` | Graph node — a spider/participant, addressed by SHA-256(DID) |
| `Dot` | Graph edge — undirected connection between fibers/knots |
| `Knot` | Content unit — max 2 lines, content-addressed |
| `FBRLedger` | Silk token wallet + validation/burn logic |
| `KnitweaveGraph` | Coordinates Fiber/Dot/Knot registries + FBRLedger |
| `RiskKnotLedger` | FBR staking on uncertain claims (yes/no + resolution) |
| `PulseWalletStore` | PLS wallets with lock/unlock/slash for risk-knot stakes |
| `RiskKnotStore` | TypeScript risk-knot staking (open/stake/vote/_resolve) |

## Validation & rewards

1. Spider posts a knot (2 lines max)
2. 3 unique validators confirm it → knot confirmed
3. Poster earns `FBR_POSTER_REWARD = 5 µFBR`; each validator earns `FBR_VALIDATOR_REWARD = 2 µFBR`
4. Wallets inactive 90 days → balance burned (deflation)

## Risk-Knots

Uncertain claims can be staked on YES/NO:

| Level | FBR lock | PLS lock |
|-------|----------|----------|
| L1 | 5 µFBR | 10 µPLS |
| L2 | 50 µFBR | 100 µPLS |
| L3 | 500 µFBR | 1 000 µPLS |

Resolution fires when ≥ 5 votes with ≥ 2/3 consensus. Correct stakers earn
`stake × multiplier + share of losing pool`. 10% of losing pool is burned.

## Layout

```
knitweb/                    Python package
  addressing.py             addr256(), 256-bit address space
  fiber.py                  Fiber + FiberRegistry
  dot.py                    Dot + DotRegistry
  knot.py                   Knot + KnotRegistry
  fbr.py                    FBRWallet + FBRLedger
  graph.py                  KnitweaveGraph
  market.py                 MarketCap (3×2^256 bounds)
  risk.py                   RiskKnotLedger
src/integrations/lightrag/  TypeScript
  pulse.ts                  PulseWalletStore, KnotValidationStore, PulseEngine
  risk-knot.ts              RiskKnotStore
tests/python/               Python tests (96 total)
tests/unit/                 TypeScript tests (57 total)
```

## Running the tests

```bash
# Python
pip install -r requirements.txt
python -m pytest tests/python/ -v

# TypeScript (from repo root, requires jest configured)
npx jest tests/unit/pulse.test.ts tests/unit/riskKnot.test.ts
```
