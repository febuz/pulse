"""Region-aware bootstrap registry for Knitweb relay discovery.

Nodes announce their relay URL, pubkey, and region on startup; peers query the
registry to find the nearest relays.  All timestamps are integer milliseconds
(no float); all sorting is purely by integer comparison.

The backing store is an optional JSON file; the registry works fully in-memory
when ``path`` is omitted (useful for tests and serverless tabs).
"""

from __future__ import annotations

import json
import time
from typing import Any

__all__ = ["BootstrapRegistry"]


def _ms_now() -> int:
    return int(time.time() * 1000)


class BootstrapRegistry:
    """In-memory relay registry with optional JSON persistence.

    Parameters
    ----------
    path:
        Path to the backing JSON file.  The file is read once on construction
        (if it exists) and written on every :meth:`announce` call.  Pass
        ``None`` (default) for a pure in-memory registry.
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = path
        self._entries: list[dict[str, Any]] = []
        if path is not None:
            try:
                with open(path, encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, list):
                    self._entries = [
                        e for e in loaded
                        if isinstance(e, dict)
                        and isinstance(e.get("last_seen_ms"), int)
                    ]
            except (OSError, json.JSONDecodeError):
                pass

    def announce(self, relay_url: str, pubkey: str, region: str) -> None:
        """Register or refresh a relay entry.  Stamps ``last_seen_ms`` as an integer."""
        relay_url = relay_url.rstrip("/")
        for entry in self._entries:
            if entry.get("relay_url") == relay_url and entry.get("pubkey") == pubkey:
                entry["last_seen_ms"] = _ms_now()
                entry["region"] = region
                self._persist()
                return
        self._entries.append({
            "region": region,
            "relay_url": relay_url,
            "pubkey": pubkey,
            "last_seen_ms": _ms_now(),
        })
        self._persist()

    def nearest_relays(self, region: str, n: int = 3) -> list[str]:
        """Return up to ``n`` relay URLs nearest to ``region``.

        Nearness is determined first by longest common prefix with ``region``,
        then by ``last_seen_ms`` descending (freshest first).  All comparisons
        are purely integer or string — no float arithmetic.
        """
        def _score(entry: dict) -> tuple[int, int]:
            r = entry.get("region", "")
            # Longer shared prefix = better match
            prefix_len = 0
            for a, b in zip(region, r):
                if a == b:
                    prefix_len += 1
                else:
                    break
            return (prefix_len, int(entry.get("last_seen_ms", 0)))

        sorted_entries = sorted(self._entries, key=_score, reverse=True)
        return [e["relay_url"] for e in sorted_entries[:n]]

    def prune(self, older_than_ms: int) -> int:
        """Remove entries not seen within the last ``older_than_ms`` milliseconds.

        Returns the number of entries removed.
        """
        cutoff = _ms_now() - older_than_ms
        before = len(self._entries)
        self._entries = [
            e for e in self._entries
            if int(e.get("last_seen_ms", 0)) >= cutoff
        ]
        removed = before - len(self._entries)
        if removed:
            self._persist()
        return removed

    def all_entries(self) -> list[dict[str, Any]]:
        return list(self._entries)

    def _persist(self) -> None:
        if self._path is None:
            return
        try:
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(self._entries, fh, indent=2)
        except OSError:
            pass
