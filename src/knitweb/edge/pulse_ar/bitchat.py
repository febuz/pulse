"""bitchat — a BLE mesh transport for exchanging Pulse AR observations.

Smartglasses are often off-grid: no cell, no Wi-Fi, just a cluster of nearby
devices. bitchat carries signed observation envelopes between them over
Bluetooth Low Energy exactly the way the bitchat BLE mesh chat protocol carries
messages — so a wearer's YOLO/LLM observations reach the peers around them with no
infrastructure at all.

Three properties, kept minimal but faithful:

  * **Fragmentation.** A BLE characteristic write is tiny (~180 bytes here), so a
    payload is split into ``BitchatFrame`` fragments and reassembled by
    ``(msg_id, index, total)`` at the far end.
  * **TTL-bounded store-and-forward flood.** Every node relays a fragment onward to
    its other peers, decrementing a hop ``ttl``; a message therefore crosses
    multiple hops without any routing table, and ``ttl`` bounds how far it travels.
  * **Dedup.** Each node relays a given ``(msg_id, index)`` exactly once, so a
    cyclic topology can never start a broadcast storm — the flood always
    terminates, independent of ``ttl``.

The payload is opaque bytes: bitchat neither signs nor inspects it. Authenticity
is the envelope's job (:class:`~knitweb.edge.pulse_ar.observation.SignedObservation`
carries its own signature), so the mesh can be dumb and the trust decision happens
where the data is consumed — verify-before-trust.

The in-memory :class:`MeshNode` links deliver synchronously for tests and demos; a
real BLE driver overrides :meth:`MeshNode._broadcast` to write the frame bytes to a
GATT characteristic instead of calling a peer directly. The relay/dedup/TTL logic
is transport-agnostic and unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from ...core import canonical, crypto

__all__ = ["BitchatFrame", "fragment", "Reassembler", "MeshNode", "MAX_TTL", "DEFAULT_MTU"]

MAX_TTL = 7          # bitchat-style hop budget (matches its 7-hop default)
DEFAULT_MTU = 180    # conservative BLE characteristic payload, bytes


# ---------------------------------------------------------------------------
# Wire fragment
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BitchatFrame:
    """One BLE-sized fragment of a message.

    ``msg_id`` content-addresses the *whole* payload (``sha256(origin | payload)``),
    so every fragment of one message shares it and dedup/reassembly are exact.
    """

    msg_id: str        # hex sha256 of (origin | payload) — binds all fragments
    origin: str        # device id (PLS address) that first published the message
    ttl: int           # remaining hop budget
    index: int         # fragment index in [0, total)
    total: int         # number of fragments in the message
    chunk: bytes       # this fragment's slice of the payload

    def to_bytes(self) -> bytes:
        """Canonical bytes actually written to the BLE characteristic."""
        return canonical.encode({
            "id": self.msg_id,
            "origin": self.origin,
            "ttl": self.ttl,
            "index": self.index,
            "total": self.total,
            "chunk": self.chunk,
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> "BitchatFrame":
        m = canonical.decode(data)
        return cls(
            msg_id=m["id"], origin=m["origin"], ttl=m["ttl"],
            index=m["index"], total=m["total"], chunk=m["chunk"],
        )


def _msg_id(origin: str, payload: bytes) -> str:
    return crypto.sha256_hex(origin.encode("utf-8") + b"|" + payload)


def fragment(payload: bytes, origin: str, ttl: int, mtu: int = DEFAULT_MTU) -> list[BitchatFrame]:
    """Split ``payload`` into BLE-sized fragments sharing one content-addressed id."""
    if mtu <= 0:
        raise ValueError("mtu must be positive")
    if ttl <= 0:
        raise ValueError("ttl must be positive")
    mid = _msg_id(origin, payload)
    chunks = [payload[i:i + mtu] for i in range(0, len(payload), mtu)] or [b""]
    total = len(chunks)
    return [
        BitchatFrame(msg_id=mid, origin=origin, ttl=ttl, index=i, total=total, chunk=c)
        for i, c in enumerate(chunks)
    ]


# ---------------------------------------------------------------------------
# Reassembly
# ---------------------------------------------------------------------------

class Reassembler:
    """Collects fragments per ``msg_id`` and yields the payload once complete."""

    def __init__(self) -> None:
        self._parts: dict[str, dict[int, bytes]] = {}
        self._totals: dict[str, int] = {}

    def add(self, frame: BitchatFrame) -> bytes | None:
        """Add a fragment; return the reassembled payload when the last one arrives."""
        parts = self._parts.setdefault(frame.msg_id, {})
        self._totals[frame.msg_id] = frame.total
        parts[frame.index] = frame.chunk
        if len(parts) < frame.total:
            return None
        payload = b"".join(parts[i] for i in range(frame.total))
        # Only trust a reassembly whose content matches the id it travelled under.
        if _msg_id(frame.origin, payload) != frame.msg_id:
            del self._parts[frame.msg_id]
            del self._totals[frame.msg_id]
            return None
        del self._parts[frame.msg_id]
        del self._totals[frame.msg_id]
        return payload


# ---------------------------------------------------------------------------
# Mesh node
# ---------------------------------------------------------------------------

class MeshNode:
    """A device on the bitchat BLE mesh: publish, relay, and deliver payloads."""

    def __init__(self, device_id: str, mtu: int = DEFAULT_MTU) -> None:
        self.device_id = device_id
        self.mtu = mtu
        self._peers: list["MeshNode"] = []
        self._reasm = Reassembler()
        self._relayed: set[tuple[str, int]] = set()   # (msg_id, index) seen on the wire
        self._delivered: set[str] = set()              # msg_ids handed to on_message
        self._on_message: Callable[[bytes, str], None] | None = None
        # Diagnostics for proofs: how many fragments this node put on the wire.
        self.relay_count = 0

    # -- topology ----------------------------------------------------------

    def connect(self, other: "MeshNode") -> None:
        """Form a bidirectional BLE link with ``other`` (idempotent)."""
        if other is self:
            raise ValueError("a node cannot link to itself")
        if other not in self._peers:
            self._peers.append(other)
        if self not in other._peers:
            other._peers.append(self)

    def on_message(self, callback: Callable[[bytes, str], None]) -> None:
        """Register ``callback(payload, origin)`` for fully reassembled messages."""
        self._on_message = callback

    # -- publish -----------------------------------------------------------

    def publish(self, payload: bytes, ttl: int = MAX_TTL) -> str:
        """Fragment ``payload`` and flood it to the mesh. Returns the message id."""
        frames = fragment(payload, self.device_id, ttl, self.mtu)
        mid = frames[0].msg_id
        # The origin already holds the content: mark it so a relayed copy that loops
        # back is not re-delivered or re-broadcast.
        self._delivered.add(mid)
        for f in frames:
            self._relayed.add((f.msg_id, f.index))
        for f in frames:
            self._broadcast(f, exclude=None)
        return mid

    # -- receive + relay ---------------------------------------------------

    def deliver(self, frame: BitchatFrame, sender: "MeshNode | None" = None) -> None:
        """Handle a fragment arriving over a BLE link: reassemble, deliver, relay."""
        key = (frame.msg_id, frame.index)
        if key in self._relayed:
            return                       # already saw this exact fragment — drop (no storm)
        self._relayed.add(key)

        # Local reassembly + one-shot delivery.
        payload = self._reasm.add(frame)
        if payload is not None and frame.msg_id not in self._delivered:
            self._delivered.add(frame.msg_id)
            if self._on_message is not None:
                self._on_message(payload, frame.origin)

        # Store-and-forward flood: pass it on, one hop poorer, never back to sender.
        if frame.ttl > 1:
            self._broadcast(replace(frame, ttl=frame.ttl - 1), exclude=sender)

    def _broadcast(self, frame: BitchatFrame, exclude: "MeshNode | None") -> None:
        """Emit ``frame`` to every peer but ``exclude``.

        A real BLE driver overrides this to write ``frame.to_bytes()`` to a GATT
        characteristic; the in-memory mesh calls the neighbour directly.
        """
        for peer in self._peers:
            if peer is exclude:
                continue
            self.relay_count += 1
            peer.deliver(frame, sender=self)
