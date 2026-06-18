# Identity & accounts — brand architecture and the account decision

Open question raised for the project: *eventually this needs its own account — should
that be named after **Pulse**, **Knitweb**, or should there be **two separate
accounts**?* This doc records the brand architecture first (so the naming follows
from it) and then a recommendation.

## Brand architecture (already established)

The project deliberately runs three distinct layers of identity — they are not
synonyms and should not be collapsed:

| Layer | Name | What it is | Audience |
|---|---|---|---|
| **Coin / brand** | **Fiber** | the headline asset + public brand ("a top-10 P2P web") | markets, press, listings |
| **Token / activity unit** | **PLS** ("pulses") | what users *spend* for activity (compute, storage, queries) | wallet users, app devs |
| **Protocol / platform** | **Knitweb** | the codebase + the woven Web/Loom/Knit/Pulse fabric | engineers, node operators |

Vocabulary is fixed: **Web · Loom · Knit · Pulse · Fiber** (never "network"/"net").
"Knitweb" is the engineering home; "Fiber" is the coin; "PLS/Pulse" is the metered
unit. FBR is reserved (possible separate-region token), not the launch token.

## The account decision

### Options

1. **Single org `knitweb`** — everything under the protocol name (current state:
   `github.com/febuz/knitweb`).
2. **Single org `pulse`** — name the home after the token/activity.
3. **Two separate orgs** — e.g. `knitweb` (protocol/foundation) **and** `pulse`
   (token + apps + community), repos split by concern.

### Recommendation — **one home now (`knitweb`), reserve the rest, split later**

- **Make `knitweb` the GitHub org / engineering home.** The codebase, protocol
  spec, SDK, and node software live here. This is where contributors, stars, CI,
  and issues concentrate; fragmenting that early hurts a young project.
- **Keep token/app identity as products *under* that org**, not a separate account:
  `knitweb/pls-*` (wallet, token tooling), `knitweb/fiber-*` (brand site, listings
  collateral). One discovery surface, one contributor graph.
- **Defensively reserve the handles `pulse` and `fiber` now** (GitHub org +
  domains + socials) so they are not squatted — this is cheap and reversible, and
  squatting is the real risk, not "wrong name."
- **Split into a second org (`pulse` or a `fiber-foundation`) only when a concrete
  trigger appears:** a separate legal/foundation entity for the token, independent
  governance, a distinct community team, or a regulatory boundary between the
  protocol and the asset. At that point move token-specific repos out; the protocol
  stays in `knitweb`.

**Why not name the single home `pulse`?** Pulse is the *unit of activity*, the most
likely thing to be renamed or regionalised (FBR is already reserved as a possible
regional token). Anchoring the permanent engineering identity to the most volatile
layer is the riskiest of the three. Knitweb (the protocol) is the stable noun.

**Why not two accounts now?** Premature. Two orgs doubles the maintenance (CI,
secrets, permissions, dependency bumps) and halves visibility, for a separation
that has no concrete driver yet. Keep the *option* open by reserving names; take it
when a real boundary exists.

### Bottom line

> **Build under `knitweb`. Reserve `pulse` and `fiber`. Split to a token/foundation
> org only when governance, legal, or community structure actually demands it.**

This keeps the door open to all three end-states while paying the lowest cost today
— the "accept what is possible, split when justified" path.

## Decision (owner-confirmed, 2026-06-17)

**DECIDED: build under `knitweb` now, reserve `pulse` + `fiber`, split to a separate
token/foundation org only when governance/legal/community structure demands it.**

The project owner confirmed the recommendation above. Concrete follow-ups:
- Engineering home stays/becomes the `knitweb` org (currently `github.com/febuz/knitweb`).
- Reserve the `pulse` and `fiber` handles (GitHub org + domains + socials) defensively.
- Token/app repos live *under* `knitweb` (`knitweb/pls-*`, `knitweb/fiber-*`) until a
  real boundary (legal entity, independent governance, distinct community) appears.
