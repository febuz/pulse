# Legacy Weaving Flows

This guide extracts useful product flows from the historical Knitweb weaving app
into the current Python peer-to-peer web implementation.

The old app is not the runtime path. It is source material for a first useful
Pulse object workflow: create a visual object locally, edit it, preview it, save
revisions, publish it, and ask a host or provider to keep it available.

## Extracted Concepts

- **Draft object**: a local object that can be edited before publishing.
- **Source asset**: an imported image, generated drawing, or blank grid.
- **Normalized grid**: a stable cell layout derived from rows, columns, crop,
  rotation, and scaling settings.
- **Palette**: a bounded set of colors or materials chosen by the user.
- **Region**: a selectable group of cells that can be edited together.
- **Preview**: a read-only rendering that validates the object before publishing
  or handoff.
- **Handoff recipe**: optional metadata for a tool, device, service, or provider
  workflow.
- **Hosted copy**: a selected object revision kept available by a host or
  provider.

## Browser Flow

1. Create a local identity or open an existing one.
2. Start a draft object from an image, a blank grid, or a saved object.
3. Crop, rotate, and set row/column dimensions.
4. Choose a palette and generate the normalized grid.
5. Edit cells by region, rectangle, or freehand selection.
6. Preview the result without leaving the local draft.
7. Save a local revision with object metadata and source assets.
8. Publish the selected revision to a Pulse address.
9. Choose whether a host or provider should keep a copy available.
10. Keep working offline and sync later when a peer or host is reachable.

The browser should treat publishing as a user-visible action. Generating a grid,
editing a draft, and previewing a result should not require a provider.

## Provider And Host Flow

1. The browser asks a selected host or provider to mirror an object revision.
2. The host stores the object manifest, source assets, normalized grid, preview
   assets, and revision metadata.
3. The host publishes availability metadata so other peers can discover a copy.
4. The host participates in sync when the owner or another authorized peer
   reconnects.
5. A provider console shows hosted objects, storage use, sync activity, and policy
   status.
6. Optional handoff services can be exposed later, but only behind explicit user
   policy.

Provider-host support should work for textile objects, pages, media, and future
object types. The weaving flow is a concrete first case, not a special
architecture.

## Package Sketch

- `manifest`: object id, owner identity, address intent, object type, revision id
- `source`: original asset references and import settings
- `grid`: rows, columns, cell data, palette references, region metadata
- `preview`: rendered assets for fast display
- `history`: local revision metadata and conflict hints
- `handoff`: optional target-specific recipes, disabled by default

The package should be append-friendly so sync can move revisions in batches and
resolve conflicts locally.

## Non-Goals

- No legacy Electron runtime in the active apps.
- No direct dependency on old browser libraries.
- No provider requirement for local editing or previewing.
- No device-control workflow in the first browser milestone.
- No central service assumption for publishing or discovery.

## First Implementation Checks

- A user can create and edit a draft without network access.
- A saved draft has enough metadata to be reopened and rendered.
- Publishing produces a stable addressable object revision.
- A host can mirror a selected revision without becoming the owner.
- Sync can move object revisions without assuming low latency.
