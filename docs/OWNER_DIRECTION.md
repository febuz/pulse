# Owner Direction

Knitweb/Pulse is a peer-to-peer web and fabric. Product language should make that
clear on first read.

## Product Frame

- Say **web**, **fabric**, **peer**, **host**, **provider**, **spider**, **Pulse**,
  **Fiber**, **Knit**, and **Knitweb**.
- Describe Pulse/PLS as an activity or accounting unit for useful work.
- Describe Fiber as an account-state commitment and value unit.
- Keep the core promise practical: local-first objects, verifiable work, sync,
  provenance, hosting, relay, and long-delay operation.

## Avoid In Front-Door Prose

- blockchain, chain, mining, miner, or mainnet as product identity
- speculative asset framing
- founder allocation framing
- claims that a provider owns user objects
- reviving the old JavaScript prototype as the runtime path

## Allowed Technical Exceptions

Some existing code and research notes still use older terms because they name
landed modules, compare external systems, or protect signed-byte compatibility.
Do not rename hash-critical fields casually. When in doubt, document the exception
instead of changing the signed surface.

The front-door docs and package metadata are stricter: they must present the
project as a peer-to-peer web/fabric first.

## Review Rule

If review finds direction drift, fix that before merging feature work.
