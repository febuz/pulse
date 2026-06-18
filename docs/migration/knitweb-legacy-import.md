# Knitweb Legacy Import

Date: 2026-06-18

Source reviewed:

- `/home/knight2/knitweb/Oude_repo_weefapparaat/`
- `/home/knight2/knitweb/Pulse and Loom_ A Textile-Metaphor P2P Knowledge Fabric — Technical, Literature, and Branding Dossier.pdf`
- `/home/knight2/knitweb/knitweb_pulse.mp4`

Imported:

- `legacy/weaving-app/`
- `docs/media/pulse-and-loom-dossier.pdf`
- `docs/media/knitweb-pulse.mp4`

Excluded:

- `node_modules/` from the old Knitweb app
- `.Rhistory`
- old Git metadata

Review notes:

- The old app is preserved as legacy material, not as current Pulse architecture.
- Pulse remains framed as a P2P web: peers, hosts, providers, addresses, routing, sync, relay, directory, and world profiles.
- The imported old app contains third-party browser libraries and historical implementation details. New Pulse code should not copy those patterns blindly.
- The old setup scripts reference Electron `v0.31.0`; they are retained only as historical context and are not supported installation paths.
- The dossier and video are retained as migration context and product inspiration.

Next migration steps:

1. Extract user flows from the old pattern editor into `docs/guides/`.
2. Decide which weaving concepts belong in `apps/browser`.
3. Define provider-host flows for storing and sharing textile/web objects.
4. Keep the current Pulse protocol work separate from legacy UI code.
