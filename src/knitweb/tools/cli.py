"""knitweb.tools.cli — the **Pulse operator CLI** (pure-Python).

Create and inspect a Pulse **identity wallet**, and run operator actions (node daemon, pay,
balance, address). An app's host bootstrap calls :func:`cmd_identity_create` +
:func:`cmd_host_status`; the same module is the ``python -m knitweb.tools.cli`` command line.

(This replaces the JS Pulse CLI — Pulse is pure-Python.)
"""

from __future__ import annotations

import argparse
import json
import os

from .. import store
from ..ledger.node import AccountNode

__all__ = ["cmd_identity_create", "cmd_host_status", "main"]


def cmd_identity_create(wallet_path: str, *, genesis: int = 0, network: int = 1) -> dict:
    """Create the Pulse identity wallet if absent, else reuse it.

    Returns ``{created, address, wallet}``. ``genesis`` seeds a starting PLS balance for
    **local/dev only** (the native PLS layer has no premine).
    """
    if os.path.exists(wallet_path):
        node = store.load_node(wallet_path)
        return {"created": False, "address": node.address, "wallet": wallet_path}
    os.makedirs(os.path.dirname(wallet_path) or ".", exist_ok=True)
    node = AccountNode(genesis_balances={"PLS": genesis} if genesis else None, network=network)
    store.save_node(node, wallet_path)
    return {"created": True, "address": node.address, "wallet": wallet_path}


def cmd_host_status(wallet_path: str, *, listen: str | None = None) -> dict:
    """Status of the Pulse host identity: account address + PLS balance."""
    node = store.load_node(wallet_path)
    return {"account": {"address": node.address, "balance_pls": node.balance("PLS")},
            "wallet": wallet_path, "listen": listen}


_OPERATOR = ("address", "balance", "node", "pay", "wallet")


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    # operator commands (node daemon / pay / balance / address / wallet) delegate verbatim
    if argv and argv[0] in _OPERATOR:
        from ..app import cli as appcli
        return appcli.main(argv)

    ap = argparse.ArgumentParser(prog="knitweb-pulse", description="Pulse operator CLI (pure-Python)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("identity", help="manage the Pulse identity wallet")
    pis = pi.add_subparsers(dest="sub", required=True)
    pic = pis.add_parser("create", help="create/reuse the identity wallet")
    pic.add_argument("--wallet", required=True)
    pic.add_argument("--genesis", type=int, default=0)
    ph = sub.add_parser("host", help="Pulse host status")
    phs = ph.add_subparsers(dest="sub", required=True)
    phc = phs.add_parser("status")
    phc.add_argument("--wallet", required=True)
    phc.add_argument("--listen", default=None)

    args = ap.parse_args(argv)
    if args.cmd == "identity":
        print(json.dumps(cmd_identity_create(args.wallet, genesis=args.genesis)))
        return 0
    if args.cmd == "host":
        print(json.dumps(cmd_host_status(args.wallet, listen=args.listen)))
        return 0
    return 1


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
