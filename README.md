# Pulse

Pulse is a peer-to-peer web for people, places, devices, and services across multiple worlds.

It starts on Earth, but the design assumes future links to the Moon and Mars:

- local-first by default
- useful while offline
- tolerant of long delays
- readable and operable by ordinary users
- hospitable to providers that contribute storage, routing, relay, compute, or availability

Pulse is not a platform-first system. It is a web: addresses, pages, identities, links, peers,
replication, routing, hosting, and user agents.

## Repository Shape

```text
pulse/
  apps/
    browser/            user-facing Pulse web client
    provider-console/   dashboard for hosts and providers
  docs/
    architecture.md
    vision.md
    worlds.md
    provider-hosts.md
  examples/
    hello-peer/
  hosts/
    provider/
  packages/
    address/
    core/
    identity/
    protocol/
    routing/
    sdk-js/
    storage/
    sync/
  peer/
    configs/
  services/
    bootstrap/
    directory/
    relay/
  specs/
    address.md
    peer-session.md
    sync.md
  tools/
    cli/
  worlds/
    earth/
    moon/
    mars/
```

## Roles

- **User**: browses, publishes, follows, saves, and shares.
- **Peer**: any device participating in Pulse.
- **Host**: a peer that stays available and serves content for others.
- **Provider**: an operator that offers capacity such as relay, storage, discovery, or regional presence.
- **World profile**: operational settings for Earth, Moon, Mars, or other environments.

## First Milestones

1. Define Pulse addresses and identity rules.
2. Build a local peer that can publish and fetch a page.
3. Add relay and directory services for easier peer discovery.
4. Build the browser app for users.
5. Build the provider console for hosts.
6. Validate world profiles: Earth, Moon, Mars.

## Design Rules

- The web remains usable without a central service.
- A peer should be able to carry its own identity and content.
- Publishing should be understandable to non-technical users.
- Providers help the network, but users should not depend on a single provider.
- Long-delay links are normal, not exceptional.
