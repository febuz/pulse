"""Durable persistence for Knitweb node state (M3).

A running node must survive a restart with its ledger and feed intact — otherwise
balances and history evaporate on every crash. This module snapshots the two state
chains to disk and restores them, using the project's own **canonical CBOR**
(`core.canonical`) as the on-disk format: zero extra dependencies, and because the
encoding is deterministic the restored objects reproduce **byte-identical CIDs**.

What it persists:
  * a :class:`~knitweb.ledger.braid.Braid` — an account's hash-chained ledger history;
  * a :class:`~knitweb.fabric.feed.Feed` — an author's signed append-only log;
  * an :class:`~knitweb.ledger.node.AccountNode` — key + network + braid together.

Writes are atomic (write a temp file, then ``os.replace``) so a crash mid-write can
never corrupt an existing snapshot. Restoring a Braid replays every Fiber through
``Braid.weave``, so a tampered snapshot fails its invariants on load rather than
silently corrupting state.

Security note: ``save_node`` writes the account private key in clear text (file mode
0600). That is fine for local/dev MVP nodes; production key custody (encryption /
external signer) is out of scope here.
"""

from __future__ import annotations

import os
from typing import Any

from .core import canonical
from .fabric.feed import Feed
from .ledger.braid import Braid
from .ledger.fiber import Fiber
from .ledger.node import AccountNode

__all__ = [
    "save_braid",
    "load_braid",
    "save_feed",
    "load_feed",
    "save_node",
    "load_node",
    "StoreError",
]

_VERSION = 1


class StoreError(ValueError):
    """Raised when a snapshot is malformed or fails its integrity check on load."""


# ---------------------------------------------------------------------------
# Atomic canonical-CBOR file I/O
# ---------------------------------------------------------------------------

def _write_atomic(path: str, record: dict) -> None:
    data = canonical.encode(record)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)  # atomic on POSIX


def _read(path: str) -> Any:
    with open(path, "rb") as fh:
        return canonical.decode(fh.read())


def _fiber_from_record(rec: dict) -> Fiber:
    try:
        return Fiber(
            owner=rec["owner"],
            seq=rec["seq"],
            balances=dict(rec["balances"]),
            nonce=rec["nonce"],
            prev=rec["prev"],
            knit=rec["knit"],
        )
    except (KeyError, TypeError) as exc:
        raise StoreError(f"malformed fiber record: {exc}") from exc


# ---------------------------------------------------------------------------
# Braid (ledger chain)
# ---------------------------------------------------------------------------

def save_braid(braid: Braid, path: str) -> None:
    """Snapshot a Braid (its full Fiber chain) to ``path``."""
    _write_atomic(path, {
        "kind": "braid-snapshot",
        "v": _VERSION,
        "fibers": [f.to_record() for f in braid.fibers],
    })


def load_braid(path: str) -> Braid:
    """Restore a Braid from ``path``, replaying every Fiber through its invariants."""
    rec = _read(path)
    if not isinstance(rec, dict) or rec.get("kind") != "braid-snapshot":
        raise StoreError("not a braid snapshot")
    fibers = rec.get("fibers") or []
    if not fibers:
        raise StoreError("braid snapshot has no genesis fiber")
    braid = Braid(_fiber_from_record(fibers[0]))
    for frec in fibers[1:]:
        braid.weave(_fiber_from_record(frec))  # re-checks seq/link/nonce/spent-knit
    return braid


# ---------------------------------------------------------------------------
# Feed (signed append-only log)
# ---------------------------------------------------------------------------

def save_feed(feed: Feed, path: str) -> None:
    """Snapshot a Feed's entries + fork counter to ``path`` (the private key is
    NOT stored — supply it on load)."""
    _write_atomic(path, {
        "kind": "feed-snapshot",
        "v": _VERSION,
        "fork": feed.fork,
        "entries": list(feed.entries),
    })


def load_feed(priv_hex: str, path: str) -> Feed:
    """Restore a Feed under ``priv_hex`` from ``path``."""
    rec = _read(path)
    if not isinstance(rec, dict) or rec.get("kind") != "feed-snapshot":
        raise StoreError("not a feed snapshot")
    feed = Feed(priv_hex, fork=rec.get("fork", 0))
    for entry in rec.get("entries") or []:
        feed.append(entry)
    return feed


# ---------------------------------------------------------------------------
# AccountNode (key + network + braid)
# ---------------------------------------------------------------------------

def save_node(node: AccountNode, path: str) -> None:
    """Snapshot a full AccountNode (key, network, braid) to ``path`` (mode 0600).

    WARNING: writes the private key in clear text — local/dev MVP use only.
    """
    _write_atomic(path, {
        "kind": "node-snapshot",
        "v": _VERSION,
        "priv": node.priv,
        "pub": node.pub,
        "network": node.network,
        "fibers": [f.to_record() for f in node.braid.fibers],
    })


def load_node(path: str) -> AccountNode:
    """Restore an AccountNode from ``path`` with its ledger history intact."""
    rec = _read(path)
    if not isinstance(rec, dict) or rec.get("kind") != "node-snapshot":
        raise StoreError("not a node snapshot")
    node = AccountNode(priv=rec["priv"], pub=rec["pub"], network=rec.get("network", 1))
    fibers = rec.get("fibers") or []
    if not fibers:
        raise StoreError("node snapshot has no genesis fiber")
    braid = Braid(_fiber_from_record(fibers[0]))
    if braid.owner != node.pub:
        raise StoreError("snapshot braid owner does not match node key")
    for frec in fibers[1:]:
        braid.weave(_fiber_from_record(frec))
    node.braid = braid
    return node
