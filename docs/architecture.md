# Architecture

Pulse is organized around peers and web objects.

## Core Concepts

- **Peer**: a device or service that speaks Pulse.
- **Identity**: a portable user or service identity.
- **Address**: a stable Pulse name or locator.
- **Web object**: a page, profile, media item, collection, or app resource.
- **Relay**: helps peers connect when direct paths are unavailable.
- **Directory**: helps peers discover addresses, providers, and public entry points.
- **Host**: keeps selected web objects available.
- **World profile**: environment settings for latency, sync cadence, custody, and relay strategy.

## Layers

1. Address and identity.
2. Peer session and capability exchange.
3. Object storage and local cache.
4. Sync and replication.
5. Routing and relay.
6. User agents: browser, provider console, CLI.

## Earth, Moon, Mars

Pulse should not assume constant low latency. A world profile can tune:

- retry windows
- sync batching
- relay preference
- custody periods
- conflict handling
- local directory policy

## Repository Modules

- `packages/address`: parse and format Pulse addresses.
- `packages/identity`: portable identity primitives.
- `packages/protocol`: message schemas and protocol constants.
- `packages/routing`: peer routing and relay selection.
- `packages/storage`: local object store and cache interfaces.
- `packages/sync`: replication and conflict handling.
- `packages/sdk-js`: app developer SDK.

