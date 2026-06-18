# World Profiles

Pulse is built for more than one world.

The first supported profiles are:

- Earth: low-latency, dense connectivity, many providers.
- Moon: intermittent high-delay links, strong local caching.
- Mars: long-delay links, delayed sync, high local autonomy.

Profiles are not separate networks. They are operating modes for the same web.

## Design Implications

- Peers must work without immediate confirmation from a distant service.
- Direct interactive flows should have local alternatives.
- Sync should batch changes and tolerate long gaps.
- Directories can be local, regional, or world-specific.
- Hosts should advertise custody windows and availability expectations.

