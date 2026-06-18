"""``knitweb`` — the command-line node + wallet, the runnable face of the MVP (M2).

Turns the proven library into something a user can actually run:

  * ``knitweb wallet --out w.cbor``        create a persisted wallet
  * ``knitweb address --wallet w.cbor``    show its PLS address + public key
  * ``knitweb balance --wallet w.cbor``    show its balance
  * ``knitweb node --wallet w.cbor --listen 127.0.0.1:9100``
                                            run a node daemon (serves feed sync +
                                            accepts incoming Knits over the wire)
  * ``knitweb pay --wallet w.cbor --peer 127.0.0.1:9100 --to <pubhex> --amount 5``
                                            send PLS to a running peer

Wallet state is persisted with :mod:`knitweb.store` (canonical-CBOR snapshot of the
account key + ledger braid), so balances and nonces survive between invocations and
across restarts. Networking is the merged stdlib-asyncio P2P node
(:class:`knitweb.p2p.node.AsyncioP2PNode`) — no heavy dependencies.

The command bodies are plain functions (``cmd_*``) so they are unit-testable without
spawning a subprocess; ``main`` is just argparse wiring over them.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
import re
import time
from urllib.error import URLError
from urllib.request import urlopen

from .. import sdk, store
from ..core import canonical
from ..edge.runtime import EdgeBundle
from ..ledger.node import AccountNode
from ..p2p.node import AsyncioP2PNode, PeerAddress

__all__ = [
    "main", "cmd_wallet_new", "cmd_address", "cmd_balance", "cmd_pay", "run_node",
    "cmd_identity_create", "cmd_page_publish", "cmd_peer_status", "cmd_host_status",
    "cmd_compile", "cmd_verify_bundle", "cmd_edge_load",
]


def _read_key(key_arg: str) -> str:
    """A private key given inline as hex, or as a path to a wallet/hex file."""
    import os
    if os.path.isfile(key_arg):
        try:
            return store.load_node(key_arg).priv      # a persisted wallet
        except Exception:
            return open(key_arg).read().strip()        # a raw hex file
    return key_arg.strip()


def _parse_addr(s: str) -> tuple[str, int]:
    host, _, port = s.rpartition(":")
    if not host or not port.isdigit():
        raise ValueError(f"address must be HOST:PORT, got {s!r}")
    return host, int(port)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _pulse_home() -> str:
    return os.environ.get("PULSE_HOME", os.path.join(os.path.expanduser("~"), ".pulse"))


def _default_identity_path() -> str:
    return os.path.join(_pulse_home(), "identity.cbor")


def _default_pages_path() -> str:
    return os.path.join(_pulse_home(), "pages")


def _identity_view(node: AccountNode, path: str, *, created: bool) -> dict:
    return {
        "kind": "identity",
        "version": 1,
        "createdAt": _iso_now() if created else None,
        "publicKey": node.pub,
        "address": node.address,
        "balance": node.balance("PLS"),
        "path": os.path.abspath(path),
        "created": created,
    }


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "page"


def _print_record(record: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(record, indent=2, sort_keys=True))
        return
    kind = record.get("kind")
    if kind == "identity":
        print(f"{record['address']}\nidentity: {record['path']}")
    elif kind == "page":
        print(f"published {record['cid']}\npage: {record['path']}")
    elif kind == "host-status":
        print(f"{record['address']} {record.get('listen') or 'offline'} ({record['pages']} pages)")
    elif kind == "peer-status":
        print(f"{record['peer']}: {record['status']}")
    else:
        print(json.dumps(record, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# Command bodies (testable)
# ---------------------------------------------------------------------------

def cmd_wallet_new(path: str, genesis: int = 0, network: int = 1) -> AccountNode:
    """Create a wallet and persist it to ``path``.

    ``genesis`` seeds a starting PLS balance for **local/dev/testing only** — the
    native PLS base layer has no premine; real balances come from PoUW rewards.
    """
    balances = {"PLS": genesis} if genesis else None
    node = AccountNode(genesis_balances=balances, network=network)
    store.save_node(node, path)
    return node


def cmd_address(path: str) -> tuple[str, str]:
    """Return (address, public_key_hex) for the wallet at ``path``."""
    node = store.load_node(path)
    return node.address, node.pub


def cmd_balance(path: str, symbol: str = "PLS") -> int:
    """Return the wallet's integer balance for ``symbol``."""
    return store.load_node(path).balance(symbol)


def cmd_identity_create(
    path: str | None = None,
    *,
    genesis: int = 0,
    network: int = 1,
    force: bool = False,
) -> dict:
    """Create or reuse the default Pulse identity wallet.

    This is the pure-Python compatibility surface for ``pulse identity create``.
    It persists a real :class:`AccountNode` wallet but returns only public fields,
    so subprocess callers never receive a private key on stdout.
    """
    out = os.path.abspath(path or _default_identity_path())
    if os.path.exists(out) and not force:
        return _identity_view(store.load_node(out), out, created=False)
    node = cmd_wallet_new(out, genesis=genesis, network=network)
    return _identity_view(node, out, created=True)


def cmd_page_publish(
    *,
    title: str,
    body: str,
    identity_path: str | None = None,
    out_dir: str | None = None,
) -> dict:
    """Publish a small local Pulse page and return its content id + path."""
    identity = store.load_node(os.path.abspath(identity_path or _default_identity_path()))
    published_at = _iso_now()
    record = {
        "kind": "page",
        "version": 1,
        "title": title,
        "body": body,
        "author": identity.address,
        "publishedAt": published_at,
    }
    cid = canonical.cid(record)
    record["cid"] = cid
    directory = os.path.abspath(out_dir or _default_pages_path())
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{_slugify(title)}-{cid[-10:]}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.chmod(path, 0o600)
    return {**record, "path": path}


def cmd_peer_status(peer: str | None = None) -> dict:
    """Return a best-effort status for a peer identifier or URL."""
    peer_id = peer or "local"
    if peer_id.startswith(("http://", "https://")):
        try:
            with urlopen(peer_id, timeout=3) as res:
                return {
                    "kind": "peer-status",
                    "peer": peer_id,
                    "status": "reachable",
                    "httpStatus": res.status,
                }
        except URLError as exc:
            return {
                "kind": "peer-status",
                "peer": peer_id,
                "status": "unreachable",
                "error": str(exc.reason),
            }
        except OSError as exc:
            return {
                "kind": "peer-status",
                "peer": peer_id,
                "status": "unreachable",
                "error": str(exc),
            }
    return {
        "kind": "peer-status",
        "peer": peer_id,
        "status": "unknown",
        "note": "no peer transport configured for this identifier",
    }


def cmd_host_status(
    *,
    identity_path: str | None = None,
    listen: str | None = None,
    pages_dir: str | None = None,
) -> dict:
    """Return local host status for the Pulse CLI compatibility surface."""
    wallet = os.path.abspath(identity_path or _default_identity_path())
    if not os.path.exists(wallet):
        identity = cmd_identity_create(wallet, genesis=0)
    else:
        identity = _identity_view(store.load_node(wallet), wallet, created=False)
    pages_path = os.path.abspath(pages_dir or _default_pages_path())
    pages = 0
    if os.path.isdir(pages_path):
        pages = len([name for name in os.listdir(pages_path) if name.endswith(".json")])
    return {
        "kind": "host-status",
        "address": identity["address"],
        "identity": wallet,
        "listen": listen,
        "balance": identity["balance"],
        "pages": pages,
    }


async def cmd_pay(
    path: str,
    peer: tuple[str, int],
    to_pub: str,
    amount: int,
    symbol: str = "PLS",
    timestamp: int | None = None,
) -> str:
    """Send ``amount`` ``symbol`` to ``to_pub`` via a running peer; persist; return knit id."""
    node = store.load_node(path)
    p2p = AsyncioP2PNode(account=node)
    ts = int(time.time()) if timestamp is None else timestamp
    knit = await p2p.send_knit(PeerAddress(*peer), to_pub, symbol, amount, ts)
    store.save_node(node, path)  # apply_sent mutated the braid — persist it
    return knit.id


def _autosave_once(node: AccountNode, path: str, last_cid: str | None) -> str:
    """Persist ``node`` iff its braid head changed since ``last_cid``; return the new head.

    The braid head CID advances whenever a Knit is applied (sent or received), so this
    snapshots exactly when state changed and is a no-op otherwise. Pure + deterministic —
    the daemon's autosave loop is just this called on a timer.
    """
    head = node.braid.head.cid
    if head != last_cid:
        store.save_node(node, path)
    return head


async def _autosave_loop(
    node: AccountNode, path: str, stop: "asyncio.Event", poll_s: float
) -> None:
    """Snapshot the node whenever its state changes, until ``stop`` is set."""
    last = node.braid.head.cid
    while not stop.is_set():
        last = _autosave_once(node, path, last)
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_s)
        except asyncio.TimeoutError:
            pass


async def run_node(
    path: str,
    listen: tuple[str, int],
    *,
    ready: "asyncio.Event | None" = None,
    stop: "asyncio.Event | None" = None,
    autosave_poll_s: float = 2.0,
) -> AsyncioP2PNode:
    """Run a node daemon: serve feed sync + accept incoming Knits until ``stop``.

    ``ready``/``stop`` events make the daemon drivable from tests; the CLI passes a
    never-set ``stop`` so it runs until interrupted. State is persisted **continuously**
    — an autosave loop snapshots the node whenever a Knit changes its braid head, so a
    crash loses at most ``autosave_poll_s`` of activity rather than everything since
    startup — plus a final snapshot on clean shutdown.
    """
    node = store.load_node(path)
    p2p = AsyncioP2PNode(account=node, host=listen[0], port=listen[1])
    await p2p.start()
    print(f"knitweb node {node.address} listening on {p2p.host}:{p2p.port}")
    if ready is not None:
        ready.set()
    stop = stop or asyncio.Event()
    saver = asyncio.create_task(_autosave_loop(node, path, stop, autosave_poll_s))
    try:
        await stop.wait()
    finally:
        saver.cancel()
        store.save_node(node, path)  # final snapshot on clean shutdown
        await p2p.stop()
    return p2p


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

def cmd_compile(asset_path: str, originator_priv: str, out_path: str) -> tuple[str, str]:
    """Resolve an OriginTrail asset JSON and compile it to *signed* edge bytecode.

    Writes the bytecode to ``out_path`` and the originator signature to
    ``out_path + ".sig"``. Returns ``(digest_hex, signature_hex)``. This is the
    synaptic-compiler USP exposed for scripting: provenance-verified knowledge in,
    ultralight signed bytecode out for an edge device.
    """
    with open(asset_path) as fh:
        asset = json.load(fh)
    data, sig = sdk.compile_asset(asset, originator_priv)
    with open(out_path, "wb") as fh:
        fh.write(data)
    with open(out_path + ".sig", "w") as fh:
        fh.write(sig)
    return sdk.decode_bundle(data)["asset_cid"], sig


def cmd_verify_bundle(bundle_path: str, sig_hex: str, originator_pub: str) -> bool:
    """Verify a signed bytecode bundle against the claimed originator key (offline)."""
    with open(bundle_path, "rb") as fh:
        data = fh.read()
    return sdk.verify_bundle(originator_pub, data, sig_hex)


def cmd_edge_load(bundle_path: str, originator_pub: str | None = None,
                  sig_hex: str | None = None) -> dict:
    """Load a bytecode bundle on the edge (verify-before-trust) and summarise it.

    The AR/edge consume side: if ``originator_pub`` + ``sig_hex`` are given, the
    originator signature is checked first (a bad signature raises ``EdgeVerifyError``
    — the bundle is refused before any relation is trusted). Returns the verified
    relations as the compact ``subject -> {source_type: [objects]}`` feature view a
    humanoid's inner model / AR overlay consumes.
    """
    with open(bundle_path, "rb") as fh:
        data = fh.read()
    bundle = EdgeBundle.load(data, originator_pub, sig_hex)
    return {
        "asset_cid": bundle.asset_cid,
        "originator": bundle.originator,
        "verified": originator_pub is not None and sig_hex is not None,
        "relations": len(bundle),
        "features": bundle.to_feature_dict(),
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="knitweb", description="Knitweb node + PLS wallet")
    sub = p.add_subparsers(dest="cmd", required=True)

    identity = sub.add_parser("identity", help="manage a Pulse identity wallet")
    identity_sub = identity.add_subparsers(dest="identity_cmd", required=True)
    identity_create = identity_sub.add_parser("create", help="create or reuse an identity")
    identity_create.add_argument("--out", default=None)
    identity_create.add_argument("--genesis", type=int, default=0)
    identity_create.add_argument("--network", type=int, default=1)
    identity_create.add_argument("--force", action="store_true")
    identity_create.add_argument("--json", action="store_true")

    page = sub.add_parser("page", help="publish and inspect Pulse pages")
    page_sub = page.add_subparsers(dest="page_cmd", required=True)
    page_publish = page_sub.add_parser("publish", help="publish a local page")
    page_publish.add_argument("--title", required=True)
    page_publish.add_argument("--body")
    page_publish.add_argument("--file")
    page_publish.add_argument("--identity", default=None)
    page_publish.add_argument("--out", default=None)
    page_publish.add_argument("--json", action="store_true")

    peer = sub.add_parser("peer", help="inspect Pulse peers")
    peer_sub = peer.add_subparsers(dest="peer_cmd", required=True)
    peer_status = peer_sub.add_parser("status", help="show peer reachability")
    peer_status.add_argument("--peer", default=None)
    peer_status.add_argument("--json", action="store_true")

    host = sub.add_parser("host", help="inspect a Pulse host")
    host_sub = host.add_subparsers(dest="host_cmd", required=True)
    host_status = host_sub.add_parser("status", help="show local host status")
    host_status.add_argument("--identity", default=None)
    host_status.add_argument("--listen", default=None)
    host_status.add_argument("--pages", default=None)
    host_status.add_argument("--json", action="store_true")

    w = sub.add_parser("wallet", help="create a new persisted wallet")
    w.add_argument("--out", required=True)
    w.add_argument("--genesis", type=int, default=0, help="dev/test only: seed PLS")
    w.add_argument("--network", type=int, default=1)

    a = sub.add_parser("address", help="show a wallet's address + public key")
    a.add_argument("--wallet", required=True)

    b = sub.add_parser("balance", help="show a wallet's balance")
    b.add_argument("--wallet", required=True)
    b.add_argument("--symbol", default="PLS")

    n = sub.add_parser("node", help="run a node daemon")
    n.add_argument("--wallet", required=True)
    n.add_argument("--listen", default="127.0.0.1:9100")

    pay = sub.add_parser("pay", help="send PLS to a running peer")
    pay.add_argument("--wallet", required=True)
    pay.add_argument("--peer", required=True)
    pay.add_argument("--to", required=True, help="recipient public-key hex")
    pay.add_argument("--amount", type=int, required=True)
    pay.add_argument("--symbol", default="PLS")

    c = sub.add_parser("compile", help="compile an OriginTrail asset to signed edge bytecode")
    c.add_argument("--asset", required=True, help="path to the OriginTrail asset JSON")
    c.add_argument("--key", required=True, help="originator private key (hex or wallet/hex file)")
    c.add_argument("--out", required=True, help="output path for the bytecode bundle")

    v = sub.add_parser("verify-bundle", help="verify a signed bytecode bundle offline")
    v.add_argument("--bundle", required=True)
    v.add_argument("--sig", required=True, help="originator signature hex")
    v.add_argument("--originator", required=True, help="originator public-key hex")

    el = sub.add_parser("edge-load", help="load + verify a bundle on the edge, show its relations")
    el.add_argument("--bundle", required=True)
    el.add_argument("--originator", help="originator public-key hex (omit to load unverified)")
    el.add_argument("--sig", help="originator signature hex (omit to load unverified)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "identity":
        if args.identity_cmd == "create":
            record = cmd_identity_create(
                args.out, genesis=args.genesis, network=args.network, force=args.force
            )
            _print_record(record, args.json)
    elif args.cmd == "page":
        if args.page_cmd == "publish":
            if bool(args.body) == bool(args.file):
                raise SystemExit("page publish requires exactly one of --body or --file")
            if args.file:
                with open(args.file, encoding="utf-8") as fh:
                    body = fh.read()
            else:
                body = args.body
            record = cmd_page_publish(
                title=args.title,
                body=body,
                identity_path=args.identity,
                out_dir=args.out,
            )
            _print_record(record, args.json)
    elif args.cmd == "peer":
        if args.peer_cmd == "status":
            _print_record(cmd_peer_status(args.peer), args.json)
    elif args.cmd == "host":
        if args.host_cmd == "status":
            record = cmd_host_status(
                identity_path=args.identity,
                listen=args.listen,
                pages_dir=args.pages,
            )
            _print_record(record, args.json)
    elif args.cmd == "wallet":
        node = cmd_wallet_new(args.out, genesis=args.genesis, network=args.network)
        print(f"created wallet {node.address}\n  public key: {node.pub}\n  saved to: {args.out}")
    elif args.cmd == "address":
        addr, pub = cmd_address(args.wallet)
        print(f"{addr}\n{pub}")
    elif args.cmd == "balance":
        print(cmd_balance(args.wallet, args.symbol))
    elif args.cmd == "node":
        asyncio.run(run_node(args.wallet, _parse_addr(args.listen)))
    elif args.cmd == "pay":
        knit_id = asyncio.run(
            cmd_pay(args.wallet, _parse_addr(args.peer), args.to, args.amount, args.symbol)
        )
        print(f"paid; knit {knit_id}")
    elif args.cmd == "compile":
        asset_cid, sig = cmd_compile(args.asset, _read_key(args.key), args.out)
        print(f"compiled asset {asset_cid}\n  bundle: {args.out}\n  signature: {args.out}.sig")
    elif args.cmd == "verify-bundle":
        ok = cmd_verify_bundle(args.bundle, args.sig, args.originator)
        print("valid" if ok else "INVALID")
        return 0 if ok else 1
    elif args.cmd == "edge-load":
        info = cmd_edge_load(args.bundle, args.originator, args.sig)
        tag = "verified" if info["verified"] else "UNVERIFIED"
        print(f"loaded {info['asset_cid']} from {info['originator']} [{tag}], "
              f"{info['relations']} relations")
        print(json.dumps(info["features"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
