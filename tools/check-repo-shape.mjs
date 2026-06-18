import { existsSync } from "node:fs";

const required = [
  "README.md",
  "docs/vision.md",
  "docs/architecture.md",
  "docs/worlds.md",
  "specs/address.md",
  "specs/peer-session.md",
  "specs/sync.md",
  "apps/browser/README.md",
  "apps/provider-console/README.md",
  "services/relay/README.md",
  "services/directory/README.md",
  "services/bootstrap/README.md",
  "worlds/earth/profile.md",
  "worlds/moon/profile.md",
  "worlds/mars/profile.md"
];

const missing = required.filter((path) => !existsSync(path));

if (missing.length > 0) {
  console.error("Missing required Pulse repo files:");
  for (const path of missing) console.error(`- ${path}`);
  process.exit(1);
}

console.log("Pulse repo shape ok");

