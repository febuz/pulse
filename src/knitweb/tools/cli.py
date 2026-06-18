"""knitweb.tools.cli — the **Pulse operator CLI** (pure-Python).

Create and inspect a Pulse **identity wallet**, and run operator actions (node daemon, pay,
balance, address). An app's host bootstrap calls :func:`cmd_identity_create` +
:func:`cmd_host_status`; the same module is the ``python -m knitweb.tools.cli`` command line.

Read commands round out the operator workflow:

  * ``version``        print the installed ``knitweb`` package version
  * ``world status``   ledger size + state root (head CID) + balance for a persisted wallet

(This replaces the JS Pulse CLI — Pulse is pure-Python.)
"""

from __future__ import annotations

import argparse
import json
import os

from .. import store
from ..ledger.node import AccountNode

__all__ = [
    "cmd_identity_create",
    "cmd_host_status",
    "cmd_version",
    "cmd_world_status",
    "package_version",
    "main",
]


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


def package_version() -> str:
    """Return the ``knitweb`` package version.

    Prefers the installed distribution metadata (so an editable/wheel install reports
    its real version), and falls back to the in-tree ``knitweb.__version__`` when the
    package is run straight from ``src`` without being installed. Pure + no heavy deps.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("knitweb")
        except PackageNotFoundError:
            pass
    except ImportError:  # pragma: no cover - importlib.metadata is stdlib on 3.12
        pass
    from .. import __version__

    return __version__


def cmd_version() -> dict:
    """Return ``{name, version}`` for the running Pulse/knitweb build."""
    return {"name": "knitweb", "version": package_version()}


def cmd_world_status(wallet_path: str) -> dict:
    """Read-only view of the persisted ledger world a wallet commits to.

    Returns the account ``address`` plus the local braid's ``size`` (number of fibers,
    i.e. how many state commitments deep it is), its ``state_root`` (the head fiber CID
    — a content-addressed commitment to the whole account state), the head ``seq`` /
    ``nonce``, and the ``balance_pls``. This is the ``world status`` / ``peers``-style
    read that lets an operator inspect a wallet's chain without running a daemon.
    """
    node = store.load_node(wallet_path)
    head = node.braid.head
    return {
        "wallet": wallet_path,
        "address": node.address,
        "size": len(node.braid.fibers),
        "state_root": head.cid,
        "seq": head.seq,
        "nonce": head.nonce,
        "balance_pls": node.balance("PLS"),
    }


_OPERATOR = ("address", "balance", "node", "pay", "wallet")


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    # operator commands (node daemon / pay / balance / address / wallet) delegate verbatim
    if argv and argv[0] in _OPERATOR:
        from ..app import cli as appcli
        return appcli.main(argv)

    ap = argparse.ArgumentParser(
        prog="knitweb-pulse",
        description="Pulse operator CLI (pure-Python).",
        epilog=(
            "operator passthrough (delegated to knitweb.app.cli):\n"
            "  address  balance  node  pay  wallet\n\n"
            "examples:\n"
            "  knitweb-pulse identity create --wallet ./id.cbor\n"
            "  knitweb-pulse host status --wallet ./id.cbor\n"
            "  knitweb-pulse world status --wallet ./id.cbor\n"
            "  knitweb-pulse version\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    pi = sub.add_parser("identity", help="manage the Pulse identity wallet")
    pis = pi.add_subparsers(dest="sub", required=True, metavar="subcommand")
    pic = pis.add_parser("create", help="create/reuse the identity wallet")
    pic.add_argument("--wallet", required=True)
    pic.add_argument("--genesis", type=int, default=0)
    ph = sub.add_parser("host", help="Pulse host identity status")
    phs = ph.add_subparsers(dest="sub", required=True, metavar="subcommand")
    phc = phs.add_parser("status", help="show host identity address + balance")
    phc.add_argument("--wallet", required=True)
    phc.add_argument("--listen", default=None)
    pw = sub.add_parser("world", help="inspect a persisted ledger world")
    pws = pw.add_subparsers(dest="sub", required=True, metavar="subcommand")
    pwc = pws.add_parser("status", help="show ledger size + state root + balance")
    pwc.add_argument("--wallet", required=True)
    sub.add_parser("version", help="print the knitweb package version")

    args = ap.parse_args(argv)
    if args.cmd == "identity":
        print(json.dumps(cmd_identity_create(args.wallet, genesis=args.genesis)))
        return 0
    if args.cmd == "host":
        print(json.dumps(cmd_host_status(args.wallet, listen=args.listen)))
        return 0
    if args.cmd == "world":
        print(json.dumps(cmd_world_status(args.wallet)))
        return 0
    if args.cmd == "version":
        print(json.dumps(cmd_version()))
        return 0
    return 1


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
