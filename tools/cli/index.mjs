#!/usr/bin/env node
import { createHash, randomBytes } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

const home = process.env.PULSE_HOME || join(process.env.HOME || ".", ".pulse");
const defaultIdentity = join(home, "identity.json");
const defaultPages = join(home, "pages");

function usage() {
  return `Pulse CLI

Commands:
  pulse identity create [--out PATH] [--genesis N] [--force] [--json]
  pulse page publish --title TITLE (--body TEXT | --file PATH) [--out DIR] [--json]
  pulse peer status [--peer URL_OR_ID] [--json]
  pulse host status [--identity PATH] [--listen HOST:PORT] [--json]
`;
}

function parse(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!arg.startsWith("--")) {
      out._.push(arg);
      continue;
    }
    const key = arg.slice(2);
    if (["force", "json"].includes(key)) {
      out[key] = true;
    } else {
      out[key] = argv[++i];
    }
  }
  return out;
}

function ensureDir(path) {
  mkdirSync(path, { recursive: true });
}

function sha256(text) {
  return createHash("sha256").update(text).digest("hex");
}

function addressFromPublicKey(publicKey) {
  return `pulse1${sha256(publicKey).slice(0, 32)}`;
}

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

function writeJson(path, value) {
  ensureDir(dirname(path));
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
}

function identityOutput(record, path, created) {
  return {
    kind: "identity",
    version: record.version,
    createdAt: record.createdAt,
    publicKey: record.publicKey,
    address: record.address,
    balance: record.balance || 0,
    path,
    created
  };
}

function print(value, asJson) {
  if (asJson) {
    console.log(JSON.stringify(value, null, 2));
    return;
  }
  if (value.kind === "identity") {
    console.log(`${value.address}\nidentity: ${value.path}`);
  } else if (value.kind === "page") {
    console.log(`published ${value.cid}\npage: ${value.path}`);
  } else if (value.kind === "host-status") {
    console.log(`${value.address} ${value.listen || "offline"} (${value.pages} pages)`);
  } else if (value.kind === "peer-status") {
    console.log(`${value.peer}: ${value.status}`);
  } else {
    console.log(JSON.stringify(value, null, 2));
  }
}

function identityCreate(args) {
  const path = resolve(args.out || defaultIdentity);
  if (existsSync(path) && !args.force) {
    return identityOutput(readJson(path), path, false);
  }
  const secret = randomBytes(32).toString("hex");
  const publicKey = sha256(`pulse-public:${secret}`);
  const record = {
    kind: "identity",
    version: 1,
    createdAt: new Date().toISOString(),
    secret,
    publicKey,
    address: addressFromPublicKey(publicKey),
    balance: Number.parseInt(args.genesis || "0", 10) || 0
  };
  writeJson(path, record);
  return identityOutput(record, path, true);
}

function loadIdentity(path = defaultIdentity) {
  const full = resolve(path);
  if (!existsSync(full)) {
    return identityCreate({ out: full });
  }
  return { ...readJson(full), path: full, created: false };
}

function slugify(title) {
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return slug || "page";
}

function pagePublish(args) {
  if (!args.title) throw new Error("page publish requires --title");
  if (!args.body && !args.file) throw new Error("page publish requires --body or --file");
  const identity = loadIdentity(args.identity || defaultIdentity);
  const body = args.body ?? readFileSync(resolve(args.file), "utf8");
  const cid = `pulse:${sha256(`${identity.address}\0${args.title}\0${body}`)}`;
  const dir = resolve(args.out || defaultPages);
  const path = join(dir, `${slugify(args.title)}-${cid.slice(-10)}.json`);
  const record = {
    kind: "page",
    version: 1,
    title: args.title,
    body,
    author: identity.address,
    cid,
    publishedAt: new Date().toISOString()
  };
  writeJson(path, record);
  return { ...record, path };
}

async function peerStatus(args) {
  const peer = args.peer || "local";
  if (/^https?:\/\//.test(peer)) {
    try {
      const res = await fetch(peer);
      return { kind: "peer-status", peer, status: res.ok ? "reachable" : "error", httpStatus: res.status };
    } catch (err) {
      return { kind: "peer-status", peer, status: "unreachable", error: err.message };
    }
  }
  return { kind: "peer-status", peer, status: "unknown", note: "no peer transport configured yet" };
}

function hostStatus(args) {
  const identity = loadIdentity(args.identity || defaultIdentity);
  const pagesDir = resolve(args.pages || defaultPages);
  let pages = 0;
  if (existsSync(pagesDir)) {
    pages = readdirSync(pagesDir).filter((name) => name.endsWith(".json")).length;
  }
  return {
    kind: "host-status",
    address: identity.address,
    identity: identity.path,
    listen: args.listen || null,
    balance: identity.balance || 0,
    pages
  };
}

async function main() {
  const args = parse(process.argv.slice(2));
  const [area, command] = args._;
  let result;
  if (area === "identity" && command === "create") {
    result = identityCreate(args);
  } else if (area === "page" && command === "publish") {
    result = pagePublish(args);
  } else if (area === "peer" && command === "status") {
    result = await peerStatus(args);
  } else if (area === "host" && command === "status") {
    result = hostStatus(args);
  } else {
    console.error(usage());
    process.exitCode = 2;
    return;
  }
  print(result, args.json);
}

main().catch((err) => {
  console.error(`pulse: ${err.message}`);
  process.exit(1);
});
