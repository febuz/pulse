# Pulse CLI

Command-line tool for developers, hosts, and operators. The CLI is dependency-free Node.js
so it can run from a checkout before the rest of the Pulse packages exist.

Initial commands:

```bash
node tools/cli/index.mjs identity create --json
node tools/cli/index.mjs page publish --title "Hello Pulse" --body "First page" --json
node tools/cli/index.mjs peer status --peer local --json
node tools/cli/index.mjs host status --listen 127.0.0.1:8765 --json
```
