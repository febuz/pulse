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
import time

from .. import store
from ..ledger.node import AccountNode
from ..p2p.node import AsyncioP2PNode, PeerAddress

__all__ = ["main", "cmd_wallet_new", "cmd_address", "cmd_balance", "cmd_pay", "run_node"]


def _parse_addr(s: str) -> tuple[str, int]:
    host, _, port = s.rpartition(":")
    if not host or not port.isdigit():
        raise ValueError(f"address must be HOST:PORT, got {s!r}")
    return host, int(port)


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


async def run_node(
    path: str,
    listen: tuple[str, int],
    *,
    ready: "asyncio.Event | None" = None,
    stop: "asyncio.Event | None" = None,
) -> AsyncioP2PNode:
    """Run a node daemon: serve feed sync + accept incoming Knits until ``stop``.

    ``ready``/``stop`` events make the daemon drivable from tests; the CLI passes a
    never-set ``stop`` so it runs until interrupted. State is persisted on shutdown.
    """
    node = store.load_node(path)
    p2p = AsyncioP2PNode(account=node, host=listen[0], port=listen[1])
    await p2p.start()
    print(f"knitweb node {node.address} listening on {p2p.host}:{p2p.port}")
    if ready is not None:
        ready.set()
    try:
        await (stop.wait() if stop is not None else asyncio.Event().wait())
    finally:
        store.save_node(node, path)  # persist any received Knits
        await p2p.stop()
    return p2p


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="knitweb", description="Knitweb node + PLS wallet")
    sub = p.add_subparsers(dest="cmd", required=True)

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
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "wallet":
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
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
