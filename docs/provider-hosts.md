# Hosts And Providers

Pulse uses hosts and providers for availability.

A host keeps selected web objects available. A provider operates infrastructure for a person,
community, organization, region, or world profile.

## Provider Responsibilities

- keep content available according to a declared policy
- relay traffic when direct peer connections are not possible
- provide discovery entry points
- publish operational status
- avoid locking users into a single provider

## Host Responsibilities

- mirror selected web objects
- respect object permissions and privacy rules
- publish availability metadata
- recover cleanly after downtime

## First Provider Console

The provider console should show:

- peer identity
- hosted objects
- storage use
- relay status
- directory status
- world profile
- recent sync activity

