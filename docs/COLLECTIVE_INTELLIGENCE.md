# Collective Intelligence — the purpose of the web

> The essence is **collective intelligence**: multiple humanoids that together feed
> a knowledge graph as shared memory, and project that graph — using it for their
> Augmented Reality (physical vision) and to augment their inner (software) models.

Everything in Knitweb exists to serve that loop. This doc maps the vision onto the
architecture so each component has a clear reason to exist.

## The flywheel

```
        ┌─────────────────────────────────────────────────────────┐
        │                                                         │
   perceive ──► contribute ──► SHARED MEMORY ──► compile ──► augment
   (AR/vision)   (weave verified   (the Web:        (synaptic   (each agent's
                  relations)        knowledge+        bytecode)   inner model)
                                    resource graph)                   │
        └──────────────────────────◄──────────────────────────────────┘
                          better perception next cycle
```

Many humanoids (and software agents) run this loop concurrently. Each contribution
makes the shared memory richer; the richer memory makes every agent's perception
and cognition sharper. That compounding is the collective intelligence.

## How the pieces serve it

| Vision element | Knitweb component |
|---|---|
| **The humanoids / agents** | **Spiders** — p2p web-workers (embodied or software) that crawl, contribute, and serve. |
| **Shared memory** | The **Web** — a content-addressed, signed, local-first knowledge + resource graph. Eventually consistent across peers; no central server. |
| **Co-feeding it (trustably)** | **Attestation** (`fabric/attest.py`) makes every claim *attributable* — a humanoid signs what it contributes, so the shared memory isn't polluted by anonymous garbage (validate-at-read). |
| **Ground truth** | **OriginTrail** provenance — the shared memory's facts trace to verified originators (IFRS, news, media), so agents trust what they read. |
| **Project to physical vision (AR)** | The **Fiber Synaptic Compiler** turns graph relations into ultralight signed **bytecode** streamed to AR glasses over BLE/5G/satellite — the physical-vision overlay. |
| **Augment inner (software) models** | The *same* bytecode feeds each agent's small edge ML model — relations-as-bytecode are digestible without the "context tax", so a humanoid augments its own cognition locally, in real time. |
| **Keep contributions useful** | **Proof-of-Useful-Work** (`pouw/`) — sampled re-execution verifies a contribution before it settles, so only genuinely useful, reproducible work is rewarded and enters shared memory. |
| **Meter the activity** | **PLS ("pulses")** — agents pay/earn pulses for activity; value tracks real usage, not speculation. |
| **Why many independent operators trust it** | **No premine, credible neutrality** — no founder controls the shared memory, so rival humanoids/operators can safely pool memory without ceding advantage. |
| **The rhythm** | **Pulse** beats epoch the shared memory: checkpoints anchor what the collective knew at each tick. |

## Why this shape (and not a central brain)

A central knowledge service would make every humanoid dependent on one operator and
one failure point — and no competitor would feed a rival's database. A **local-first,
signed, content-addressed web** lets mutually-distrusting agents contribute to a
*shared* memory while each keeps sovereignty over its own node. Collective
intelligence emerges precisely *because* no one owns the graph.

## What this implies for the build

- **Shared memory must be trustworthy** → attestation + provenance are not optional
  (a humanoid acting on a forged relation is a safety problem, not just a data bug).
- **Edge augmentation must be cheap** → the synaptic bytecode path (not raw graph
  shipping) is the core deliverable for embodiment.
- **Contributions must be verifiable** → PoUW sampled re-execution gates what enters
  shared memory, keeping the collective's beliefs sound.
- **Neutrality is a feature** → it's what lets many humanoids from many operators
  share one memory at all.
