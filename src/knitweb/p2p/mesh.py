"""Gossipsub eager-push mesh + lazy IHAVE/IWANT gossip + integer peer-score.

The merged :mod:`knitweb.p2p.inventory` relay already replaced blind flooding
with a lazy ``inv -> getdata`` announce: only CIDs travel, bodies move exactly
once per peer that lacks them. What it does *not* do is decide **which** peers a
node should eagerly forward to, nor bound that fan-out. A node that simply
announces to *every* connected peer still pays O(peers) per record — and an
adversary who opens many cheap connections can dominate a victim's view.

libp2p **gossipsub v1.1** solves exactly this with a two-tier dissemination
graph, ported here faithfully but minimally to the knitweb wire:

  * **Eager-push mesh.** For each topic a node keeps a bounded set of *mesh*
    peers it forwards new message-ids to immediately. The mesh is maintained
    around three integer degree parameters during a caller-driven heartbeat:
    ``D`` (target degree), ``D_low`` (graft up to ``D`` when below) and
    ``D_high`` (prune down to ``D`` when above). Joining/leaving the mesh is the
    ``GRAFT`` / ``PRUNE`` control exchange.
  * **Lazy IHAVE/IWANT gossip.** Peers *not* in the mesh learn what a node holds
    through periodic ``IHAVE`` digests carrying only message-ids; a peer replies
    ``IWANT`` for ids it lacks. Crucially the body is **never** sent here — an
    ``IWANT`` resolves to the existing :class:`knitweb.p2p.inventory.InventoryRelay`
    ``getdata`` path, so this module composes with inventory without duplicating
    body transfer (and without editing any core file).
  * **Integer peer-score.** A compact, INTEGER-ONLY score per peer drives mesh
    membership: *time in mesh* (capped), *first-message deliveries* and an
    *invalid-message penalty*, combined as a weighted integer sum. The score is
    used solely to pick GRAFT/PRUNE candidates (graft the highest-scoring
    eligible peers, prune the lowest-scoring excess) and to **refuse grafting a
    peer whose score is negative** — gossipsub's core sybil/DoS lever.

Scope is the tractable v1.1 core. Deliberately **out of scope**: flood-publish
fan-out, the full score params P3/P5/P6/P7, opportunistic grafting, PX-on-prune,
and second-based backoff timers. None are needed to prove the bounded-mesh +
lazy-fetch property and all would pull in wall-clock or float math.

Determinism & purity. Every method is socket-free and side-effect-free beyond
this object's own state. The heartbeat epoch is an integer the *caller* ticks
(:meth:`Gossipsub.heartbeat`); peer selection randomness is an **injected**
``random.Random`` (or any object with ``.sample``/``.shuffle``); there is no
wall-clock and no floating-point anywhere — degrees, scores and epoch deltas are
all ``int``. Two nodes replaying the same control sequence with the same seed
evolve byte-identical mesh state. The four mesh control frames
(``mesh-graft`` / ``mesh-prune`` / ``mesh-ihave`` / ``mesh-iwant``) go through
:func:`knitweb.p2p.wire.write_frame_bytes` / :func:`~knitweb.p2p.wire.read_frame_bytes`,
so they share framing with every other knitweb message and carry no body bytes —
preserving signed-record byte-identity because no record is ever re-encoded here.
"""

from __future__ import annotations

import random as _random_mod
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Mapping, Optional, Tuple

from . import wire

__all__ = [
    "MeshError",
    "GRAFT",
    "PRUNE",
    "IHAVE",
    "IWANT",
    "MAX_IDS_PER_FRAME",
    "ScoreParams",
    "PeerScore",
    "build_graft_frame",
    "parse_graft_frame",
    "build_prune_frame",
    "parse_prune_frame",
    "build_ihave_frame",
    "parse_ihave_frame",
    "build_iwant_frame",
    "parse_iwant_frame",
    "Gossipsub",
]

# Control frame kinds, namespaced under ``mesh-`` so they never collide with the
# ``inv-*`` inventory kinds or the ``fabric-record`` / ``equivocation-report``
# record kinds already on the wire.
GRAFT = "mesh-graft"
PRUNE = "mesh-prune"
IHAVE = "mesh-ihave"
IWANT = "mesh-iwant"

# Bound the message-ids a single IHAVE/IWANT frame may carry, mirroring
# inventory.MAX_CIDS_PER_FRAME: a control digest is a list of fixed-width ids, so
# bounding it keeps frames small and stops a peer from forcing an unbounded
# allocation. wire.MAX_FRAME_BYTES is the hard backstop.
MAX_IDS_PER_FRAME = 50_000


class MeshError(ValueError):
    """Raised for malformed or unsafe mesh frames / arguments."""


# ---------------------------------------------------------------------------
# validation helpers (integer-only, float-free, deterministic)
# ---------------------------------------------------------------------------

def _check_int(value, name: str, *, min_value: Optional[int] = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise MeshError(f"{name} must be int")
    if min_value is not None and value < min_value:
        raise MeshError(f"{name} must be >= {min_value}")
    return value


def _check_str(value, name: str) -> str:
    if not isinstance(value, str):
        raise MeshError(f"{name} must be str")
    if not value:
        raise MeshError(f"{name} must be non-empty")
    return value


def _check_id_list(ids: Iterable[str], name: str = "ids") -> List[str]:
    out: List[str] = []
    for item in ids:
        out.append(_check_str(item, f"{name} entry"))
    if len(out) > MAX_IDS_PER_FRAME:
        raise MeshError(f"too many {name} in one frame: {len(out)} > {MAX_IDS_PER_FRAME}")
    return out


# ---------------------------------------------------------------------------
# Integer peer-score (the v1.1 subset that drives mesh membership)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScoreParams:
    """INTEGER weights + caps for :class:`PeerScore`.

    All fields are ``int``. The score is a weighted integer sum:

        score = w_time   * min(epochs_in_mesh, time_cap)
              + w_first  * min(first_message_deliveries, first_cap)
              + w_invalid * invalid_message_deliveries        (w_invalid <= 0)

    Defaults mirror the *sign and intent* of gossipsub's P1 (time in mesh, small
    positive, capped), P2 (first deliveries, positive, **capped** — gossipsub's
    FirstMessageDeliveriesCap) and P4 (invalid messages, negative quadratic-ish
    penalty — here a linear integer penalty). BOTH positive terms are capped so the
    weighted sum is bounded: without ``first_cap`` a peer that is merely first to
    deliver many message-ids accrues unbounded positive score and could offset any
    amount of invalid-message penalty, never losing mesh standing. We keep the
    magnitudes small integers so the weighted sum never overflows any practical
    bound and stays trivially deterministic.
    """

    w_time: int = 1
    time_cap: int = 100
    w_first: int = 2
    first_cap: int = 100
    w_invalid: int = -50

    def __post_init__(self) -> None:
        _check_int(self.w_time, "w_time")
        _check_int(self.time_cap, "time_cap", min_value=0)
        _check_int(self.w_first, "w_first")
        _check_int(self.first_cap, "first_cap", min_value=0)
        _check_int(self.w_invalid, "w_invalid")
        if self.w_invalid > 0:
            raise MeshError("w_invalid must be <= 0 (it is a penalty)")


@dataclass
class PeerScore:
    """Mutable per-peer score counters; :meth:`value` is the integer score.

    Counters are plain integers updated by the mesh as events happen:

      * :attr:`epochs_in_mesh` — incremented once per heartbeat the peer is in
        the mesh (gossipsub's "time in mesh"); contributes only up to
        ``time_cap`` so a long-lived honest peer cannot accrue unbounded score.
      * :attr:`first_message_deliveries` — incremented when this peer is the
        *first* to deliver a message-id we had not seen (rewards useful peers);
        contributes only up to ``first_cap`` so a peer cannot mine unbounded
        positive score from first-deliveries and outweigh its invalid-message
        penalty (gossipsub's FirstMessageDeliveriesCap).
      * :attr:`invalid_message_deliveries` — incremented when this peer delivers
        a message the caller rejected as invalid (the dominant negative term;
        enough invalids drive the score negative and bar (re-)grafting).
    """

    epochs_in_mesh: int = 0
    first_message_deliveries: int = 0
    invalid_message_deliveries: int = 0

    def value(self, params: ScoreParams) -> int:
        """Return the weighted INTEGER score under ``params`` (no floats)."""
        capped_time = self.epochs_in_mesh
        if capped_time > params.time_cap:
            capped_time = params.time_cap
        capped_first = self.first_message_deliveries
        if capped_first > params.first_cap:
            capped_first = params.first_cap
        return (
            params.w_time * capped_time
            + params.w_first * capped_first
            + params.w_invalid * self.invalid_message_deliveries
        )


# ---------------------------------------------------------------------------
# control frame codec — message-ids only, never bodies
# ---------------------------------------------------------------------------

def build_graft_frame(topic: str) -> bytes:
    """Build a ``GRAFT`` frame: 'add me to your mesh for this topic'."""
    return wire.write_frame_bytes({"kind": GRAFT, "topic": _check_str(topic, "topic")})


def parse_graft_frame(frame: bytes) -> str:
    """Parse a ``GRAFT`` frame, returning the topic."""
    msg = wire.read_frame_bytes(frame)
    if msg.get("kind") != GRAFT:
        raise MeshError("not a mesh-graft frame")
    return _check_str(msg.get("topic"), "topic")


def build_prune_frame(topic: str) -> bytes:
    """Build a ``PRUNE`` frame: 'remove me from your mesh for this topic'."""
    return wire.write_frame_bytes({"kind": PRUNE, "topic": _check_str(topic, "topic")})


def parse_prune_frame(frame: bytes) -> str:
    """Parse a ``PRUNE`` frame, returning the topic."""
    msg = wire.read_frame_bytes(frame)
    if msg.get("kind") != PRUNE:
        raise MeshError("not a mesh-prune frame")
    return _check_str(msg.get("topic"), "topic")


def build_ihave_frame(topic: str, msg_ids: Iterable[str]) -> bytes:
    """Build an ``IHAVE`` frame: a digest of message-ids we hold for a topic.

    Carries **only** message-ids (canonical CIDs), never bodies — the lazy half
    of gossipsub. A peer that lacks an id replies ``IWANT``.
    """
    return wire.write_frame_bytes(
        {"kind": IHAVE, "topic": _check_str(topic, "topic"), "ids": _check_id_list(msg_ids)}
    )


def parse_ihave_frame(frame: bytes) -> Tuple[str, List[str]]:
    """Parse an ``IHAVE`` frame, returning ``(topic, message_ids)``."""
    msg = wire.read_frame_bytes(frame)
    if msg.get("kind") != IHAVE:
        raise MeshError("not a mesh-ihave frame")
    ids = msg.get("ids")
    if not isinstance(ids, list):
        raise MeshError("ihave ids must be a list")
    return _check_str(msg.get("topic"), "topic"), _check_id_list(ids)


def build_iwant_frame(msg_ids: Iterable[str]) -> bytes:
    """Build an ``IWANT`` frame: message-ids we lack and want fetched.

    The reply to an ``IWANT`` is **not** a body on this path: the node resolves
    each id to the existing :class:`knitweb.p2p.inventory.InventoryRelay`
    ``getdata`` exchange. Only ids travel here.
    """
    return wire.write_frame_bytes({"kind": IWANT, "ids": _check_id_list(msg_ids)})


def parse_iwant_frame(frame: bytes) -> List[str]:
    """Parse an ``IWANT`` frame, returning the wanted message-ids."""
    msg = wire.read_frame_bytes(frame)
    if msg.get("kind") != IWANT:
        raise MeshError("not a mesh-iwant frame")
    ids = msg.get("ids")
    if not isinstance(ids, list):
        raise MeshError("iwant ids must be a list")
    return _check_id_list(ids)


# ---------------------------------------------------------------------------
# Gossipsub — the per-node mesh state machine
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MeshParams:
    """INTEGER degree parameters for a topic mesh.

    ``d_low <= d <= d_high`` and all are non-negative ints. ``d`` is the target
    degree the heartbeat steers toward; below ``d_low`` it grafts up to ``d``,
    above ``d_high`` it prunes down to ``d``.
    """

    d: int = 6
    d_low: int = 4
    d_high: int = 12

    def __post_init__(self) -> None:
        _check_int(self.d, "d", min_value=0)
        _check_int(self.d_low, "d_low", min_value=0)
        _check_int(self.d_high, "d_high", min_value=0)
        if not (self.d_low <= self.d <= self.d_high):
            raise MeshError("require d_low <= d <= d_high")


class Gossipsub:
    """Per-node gossipsub mesh + lazy gossip + integer peer-score.

    The node adopts this by, per topic:

      * tracking which peers are *connected and subscribed* to the topic
        (:meth:`add_peer` / :meth:`remove_peer`), independent of mesh membership;
      * ticking :meth:`heartbeat` once per integer epoch — it returns the
        ``GRAFT`` / ``PRUNE`` control frames to send so the mesh degree stays in
        ``[d_low, d_high]`` (steering toward ``d``);
      * forwarding a newly-accepted message-id to mesh peers via
        :meth:`publish` / :meth:`forward` (eager push of *ids*; bodies ride the
        inventory getdata path);
      * answering inbound control with :meth:`on_graft` / :meth:`on_prune` /
        :meth:`on_ihave` (-> an ``IWANT`` frame for missing ids) / :meth:`on_iwant`
        (-> the message-ids to resolve through inventory getdata);
      * feeding delivery outcomes to :meth:`record_delivery` (first-delivery
        reward) and :meth:`record_invalid` (penalty) to evolve peer scores.

    Socket-free: every method consumes/produces ``bytes`` frames or plain Python
    values. Randomness for peer selection is the injected ``rng``; the only
    notion of time is the integer ``epoch`` advanced by :meth:`heartbeat`.
    """

    def __init__(
        self,
        *,
        params: MeshParams | None = None,
        score_params: ScoreParams | None = None,
        rng: "_random_mod.Random | None" = None,
        seed: int | None = None,
        seen_cap: int = 100_000,
    ) -> None:
        self.params = params if params is not None else MeshParams()
        self.score_params = score_params if score_params is not None else ScoreParams()
        if rng is not None:
            self._rng = rng
        else:
            # Deterministic when a seed is given; otherwise a fresh Random. We
            # never read a wall-clock — the caller injects entropy explicitly.
            self._rng = _random_mod.Random(seed)
        self._seen_cap = _check_int(seen_cap, "seen_cap", min_value=1)
        self.epoch: int = 0
        # topic -> set of connected+subscribed peer ids (mesh candidates).
        self._topic_peers: Dict[str, set] = {}
        # topic -> set of mesh peer ids (the eager-push fan-out).
        self._mesh: Dict[str, set] = {}
        # peer id -> PeerScore.
        self._scores: Dict[str, PeerScore] = {}
        # topic -> ordered set of message-ids we hold (for IHAVE digests), bounded.
        self._have: Dict[str, "OrderedDict[str, None]"] = {}
        # global bounded seen-set of message-ids (first-delivery detection).
        self._seen: "OrderedDict[str, None]" = OrderedDict()

    # -- peer / topic membership -----------------------------------------

    def _score(self, peer: str) -> PeerScore:
        s = self._scores.get(peer)
        if s is None:
            s = PeerScore()
            self._scores[peer] = s
        return s

    def score_of(self, peer: str) -> int:
        """Return the current INTEGER score of ``peer`` (0 if unknown)."""
        _check_str(peer, "peer")
        s = self._scores.get(peer)
        return 0 if s is None else s.value(self.score_params)

    def add_peer(self, topic: str, peer: str) -> None:
        """Register ``peer`` as connected and subscribed to ``topic``.

        This makes it a *mesh candidate*; it does not put it in the mesh. The
        heartbeat grafts candidates into the mesh as degree requires.
        """
        _check_str(topic, "topic")
        _check_str(peer, "peer")
        self._topic_peers.setdefault(topic, set()).add(peer)
        self._score(peer)  # ensure a score entry exists

    def remove_peer(self, topic: str, peer: str) -> None:
        """Drop ``peer`` from a topic (e.g. disconnect/unsubscribe)."""
        _check_str(topic, "topic")
        _check_str(peer, "peer")
        self._topic_peers.get(topic, set()).discard(peer)
        self._mesh.get(topic, set()).discard(peer)

    def mesh_peers(self, topic: str) -> List[str]:
        """Return a sorted snapshot of the mesh peers for ``topic``."""
        return sorted(self._mesh.get(topic, set()))

    def topic_peers(self, topic: str) -> List[str]:
        """Return a sorted snapshot of all candidate peers for ``topic``."""
        return sorted(self._topic_peers.get(topic, set()))

    def mesh_degree(self, topic: str) -> int:
        """Return the current integer mesh degree for ``topic``."""
        return len(self._mesh.get(topic, set()))

    # -- heartbeat: maintain degree within [d_low, d_high] ---------------

    def heartbeat(self, topics: Optional[Iterable[str]] = None) -> Dict[str, List[bytes]]:
        """Advance one integer epoch and rebalance every topic's mesh.

        Returns a map ``{peer: [control frames]}`` the caller should send: a
        ``GRAFT`` frame to each newly-grafted peer and a ``PRUNE`` frame to each
        newly-pruned peer. After this call every maintained topic mesh has degree
        in ``[d_low, d_high]`` whenever enough non-negative-scored candidates
        exist (and never exceeds ``d_high`` regardless).

        Peer selection:
          * **graft** — when degree < ``d_low``, graft eligible candidates
            (non-negative score, not already meshed) up to ``d``, preferring the
            highest-scoring; ties broken by the injected RNG for unbiased spread.
          * **prune** — when degree > ``d_high``, prune the excess down to ``d``,
            dropping the lowest-scoring mesh peers first.
        """
        self.epoch += 1
        if topics is None:
            topics = set(self._mesh) | set(self._topic_peers)
        out: Dict[str, List[bytes]] = {}

        for topic in topics:
            _check_str(topic, "topic")
            mesh = self._mesh.setdefault(topic, set())
            # 1. time-in-mesh score accrual for current members.
            for peer in mesh:
                self._score(peer).epochs_in_mesh += 1

            degree = len(mesh)
            if degree < self.params.d_low:
                grafted = self._select_graft(topic, mesh, want=self.params.d - degree)
                for peer in grafted:
                    mesh.add(peer)
                    out.setdefault(peer, []).append(build_graft_frame(topic))
            elif degree > self.params.d_high:
                pruned = self._select_prune(mesh, drop=degree - self.params.d)
                for peer in pruned:
                    mesh.discard(peer)
                    out.setdefault(peer, []).append(build_prune_frame(topic))

        return out

    def _eligible_candidates(self, topic: str, mesh: set) -> List[str]:
        """Candidate peers eligible to graft: subscribed, not meshed, score >= 0."""
        candidates = self._topic_peers.get(topic, set()) - mesh
        return [p for p in candidates if self.score_of(p) >= 0]

    def _select_graft(self, topic: str, mesh: set, *, want: int) -> List[str]:
        if want <= 0:
            return []
        eligible = self._eligible_candidates(topic, mesh)
        if not eligible:
            return []
        # Shuffle first (deterministic via injected RNG) so equal-score peers are
        # picked without insertion bias, then stable-sort by descending score so
        # higher-scoring peers win. Refuse negative scores (already filtered).
        self._rng.shuffle(eligible)
        eligible.sort(key=lambda p: self.score_of(p), reverse=True)
        return eligible[:want]

    def _select_prune(self, mesh: set, *, drop: int) -> List[str]:
        if drop <= 0:
            return []
        members = list(mesh)
        self._rng.shuffle(members)
        # Lowest-scoring first: prune the least valuable mesh peers.
        members.sort(key=lambda p: self.score_of(p))
        return members[:drop]

    # -- inbound control: GRAFT / PRUNE ----------------------------------

    def on_graft(self, peer: str, frame: bytes) -> "bytes | None":
        """Handle a peer's ``GRAFT`` (it wants into our mesh for the topic).

        Accept iff the peer is a known candidate, the mesh is not already at
        ``d_high``, and the peer's score is **non-negative** — refusing a
        negative-scored peer is gossipsub's primary spam/sybil defence. On
        refusal we return a ``PRUNE`` frame to bounce it; on accept, ``None``.
        """
        _check_str(peer, "peer")
        topic = parse_graft_frame(frame)
        mesh = self._mesh.setdefault(topic, set())
        if peer in mesh:
            return None  # idempotent
        known = peer in self._topic_peers.get(topic, set())
        if (
            not known
            or self.score_of(peer) < 0
            or len(mesh) >= self.params.d_high
        ):
            return build_prune_frame(topic)
        mesh.add(peer)
        return None

    def on_prune(self, peer: str, frame: bytes) -> None:
        """Handle a peer's ``PRUNE`` (it removed us from its mesh)."""
        _check_str(peer, "peer")
        topic = parse_prune_frame(frame)
        self._mesh.get(topic, set()).discard(peer)

    # -- eager push of message-ids ---------------------------------------

    def _remember_have(self, topic: str, msg_id: str) -> None:
        have = self._have.setdefault(topic, OrderedDict())
        if msg_id in have:
            have.move_to_end(msg_id)
        else:
            have[msg_id] = None
            if len(have) > self._seen_cap:
                have.popitem(last=False)

    def _mark_seen(self, msg_id: str) -> bool:
        """Mark a message-id globally seen; return True iff newly seen."""
        if msg_id in self._seen:
            self._seen.move_to_end(msg_id)
            return False
        self._seen[msg_id] = None
        if len(self._seen) > self._seen_cap:
            self._seen.popitem(last=False)
        return True

    def publish(self, topic: str, msg_id: str, *, exclude: Iterable[str] = ()) -> List[str]:
        """Eagerly push ``msg_id`` to our mesh peers for ``topic``.

        Returns the sorted list of mesh peers to forward the *id* to (the body
        rides the inventory getdata path). ``exclude`` drops peers (e.g. the
        peer we received the id from). Records the id as held for future IHAVE
        digests and marks it seen.
        """
        _check_str(topic, "topic")
        _check_str(msg_id, "msg_id")
        self._mark_seen(msg_id)
        self._remember_have(topic, msg_id)
        excluded = set(exclude)
        targets = self._mesh.get(topic, set()) - excluded
        return sorted(targets)

    # ``forward`` is the same operation for a relayed (not locally-originated)
    # id; kept as a named alias so node code reads clearly.
    forward = publish

    # -- lazy gossip: IHAVE / IWANT --------------------------------------

    def build_ihave(self, topic: str, *, limit: int | None = None) -> "bytes | None":
        """Build an ``IHAVE`` digest of ids we hold for ``topic`` (or ``None``).

        Returns ``None`` when we hold nothing for the topic. ``limit`` caps the
        digest size (most-recent ids first); ``None`` means up to
        :data:`MAX_IDS_PER_FRAME`.
        """
        _check_str(topic, "topic")
        have = self._have.get(topic)
        if not have:
            return None
        ids = list(reversed(have))  # most-recent first
        cap = MAX_IDS_PER_FRAME if limit is None else _check_int(limit, "limit", min_value=0)
        ids = ids[:cap]
        if not ids:
            return None
        return build_ihave_frame(topic, ids)

    def on_ihave(self, peer: str, frame: bytes) -> "bytes | None":
        """Handle an inbound ``IHAVE``; reply ``IWANT`` for ids we lack.

        An id is wanted iff it is not in our global seen-set. Wanting marks
        nothing seen — seen is set only on actual delivery (:meth:`record_delivery`)
        so two IHAVEs from different peers both surface a still-missing id, but
        the inventory getdata dedup prevents a double body fetch. Returns
        ``None`` when we already hold everything advertised.

        A **negative-scored peer is refused lazy gossip** just as :meth:`on_graft`
        refuses it the mesh — we ignore its IHAVE and emit no IWANT, so a peer we
        have already penalised cannot keep inducing IWANT/body-fetch work
        (gossipsub's gossip-threshold gate). Unknown/fresh peers score ``0`` and
        are served normally.
        """
        _check_str(peer, "peer")
        if self.score_of(peer) < 0:
            return None
        _topic, ids = parse_ihave_frame(frame)
        want: List[str] = []
        local: set = set()
        for mid in ids:
            if mid in self._seen or mid in local:
                continue
            local.add(mid)
            want.append(mid)
        if not want:
            return None
        return build_iwant_frame(want)

    def on_iwant(self, peer: str, frame: bytes) -> List[str]:
        """Handle an inbound ``IWANT``; return the ids to resolve via inventory.

        Returns only ids we actually hold (across any topic), sorted. The caller
        feeds these into :meth:`knitweb.p2p.inventory.InventoryRelay.on_getdata`
        (or builds a getdata frame) — the body transfer is *that* module's job,
        never this one's, so no record bytes are duplicated or re-encoded here.

        Mirrors :meth:`on_graft` / :meth:`on_ihave`: a **negative-scored peer is
        not served** — we return an empty list so a penalised peer cannot keep
        pulling ids (and the bodies they resolve to) out of us. Unknown/fresh
        peers score ``0`` and are served normally.
        """
        _check_str(peer, "peer")
        if self.score_of(peer) < 0:
            return []
        wanted = parse_iwant_frame(frame)
        held: set = set()
        for have in self._have.values():
            held.update(have)
        return sorted(mid for mid in set(wanted) if mid in held)

    # -- delivery outcomes feed the score --------------------------------

    def record_delivery(self, peer: str, topic: str, msg_id: str) -> bool:
        """Note that ``peer`` delivered ``msg_id``; reward first deliveries.

        Returns ``True`` iff this was the *first* time we saw ``msg_id`` (in
        which case ``peer`` earns a first-message-delivery point). Subsequent
        deliveries of the same id earn nothing — only the peer that got it to us
        first is rewarded, exactly as gossipsub's P2 intends. Records the id as
        held for IHAVE digests.
        """
        _check_str(peer, "peer")
        _check_str(topic, "topic")
        _check_str(msg_id, "msg_id")
        first = self._mark_seen(msg_id)
        self._remember_have(topic, msg_id)
        if first:
            self._score(peer).first_message_deliveries += 1
        return first

    def record_invalid(self, peer: str, msg_id: str) -> int:
        """Penalise ``peer`` for delivering an invalid message; return new score.

        The caller decides validity (signature / CID mismatch / policy); this
        just applies the integer penalty. Enough invalids drive the score
        negative, after which the peer is refused (re-)grafting by
        :meth:`heartbeat` and :meth:`on_graft`.
        """
        _check_str(peer, "peer")
        _check_str(msg_id, "msg_id")
        self._score(peer).invalid_message_deliveries += 1
        return self.score_of(peer)
