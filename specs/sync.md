# Sync

Pulse sync is local-first and delay-tolerant.

The sync layer should support:

- object version metadata
- batched updates
- interrupted transfers
- local conflict handling
- selective hosting
- delayed delivery between worlds

The first implementation can start with append-only object revisions and a simple conflict policy.

