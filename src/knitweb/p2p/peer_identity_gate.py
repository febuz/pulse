"""Identity-keyed connection gate — bind a proven crypto identity to the ban ledger.

The reputation layer (:mod:`knitweb.p2p.reputation`) is the *consequence* for
misbehavior, but today every offense and every ban is keyed on the **carrier
endpoint** — a ``tcp:<ip>`` socket address or a ``relay:<mailbox>``. That keying
has two well-known failure modes, both of which production P2P stacks (Bitcoin
Core's ban-score / ``nMisbehavior``, libp2p's connection gating keyed on a
Noise-proven ``PeerId``) avoid by keying on a *proven* identity rather than a
socket:

  * **Sybil ban-evasion.** An attacker who is banned simply rotates its source
    IP (or its relay mailbox) and reconnects under a fresh carrier key with a
    clean score. The ban never follows it.
  * **Collateral NAT/relay bans.** An honest peer sharing a NAT egress IP or a
    relay mailbox with a misbehaving neighbour is banned for the neighbour's
    offense, because the ledger cannot tell the two apart by carrier alone.

:mod:`knitweb.p2p.identity` already mints and verifies a challenge-response (and
a no-round-trip piggyback) proof of control of a node's secp256k1 key, and its
``verify_*`` functions already return the *proven* compressed pubkey — but that
pubkey is wired into no consequence loop. This module is that loop: it runs the
proof check **at connection setup** and emits a :class:`GateVerdict` whose
reputation key is ``node:<pubkey>`` when (and only when) the proof verifies,
falling back to the carrier key otherwise. A ban then follows the *identity*: a
Sybil that rotates IPs keeps presenting the same proven pubkey (or it cannot
prove a fresh identity at all), so the ban it earned still bites; and an honest
peer behind a shared NAT is judged on its own key, not its neighbour's carrier.

Design constraints (CLAUDE.md):
  * **Pure stdlib**, no new deps; crypto goes through :mod:`knitweb.core.crypto`
    via :mod:`knitweb.p2p.identity` (secp256k1 + SHA-256).
  * **Integer-only** policy; **no wall-clock and no randomness** baked in — the
    verifier's ``now`` is injected, and the challenge nonce source is injectable,
    so two honest nodes observing the same inputs reach the same verdict.
  * **Touches no canonical/signed-record bytes.** The gate reads identity proofs
    (which sign a domain-tagged ephemeral nonce, never a Knit) and an integer ban
    ledger; it never encodes, hashes, or signs a value record, so no Knit's CID
    can change because of anything here. (A property test asserts this.)
  * **Bounded + deterministic + deadlock-free.** Every function is a pure
    in-memory transition; there is no socket I/O and no ``await`` here, so the
    gate cannot stall a handshake. Wiring it into a live transport is a separate
    step — this module is the adoptable primitive.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Union

from . import identity
from .identity import Challenge, PiggybackProof, Proof
from .reputation import Offense, PeerReputation

__all__ = [
    "IdentitySource",
    "GateDecision",
    "GateVerdict",
    "PeerIdentityGate",
]


class IdentitySource(Enum):
    """How a verdict's reputation key was derived — for metrics/observability."""

    #: A challenge/response :class:`~knitweb.p2p.identity.Proof` verified; the key
    #: is the proven ``node:<pubkey>``. Strongest: bound to a live, server-issued
    #: nonce, so it is not even replayable within a freshness window.
    CHALLENGE = "challenge"
    #: A no-round-trip :class:`~knitweb.p2p.identity.PiggybackProof` verified
    #: (signature good *and* timestamp fresh); the key is ``node:<pubkey>``.
    PIGGYBACK = "piggyback"
    #: No proof was presented, or the presented proof failed to verify / was
    #: stale; the verdict falls back to the carrier (``tcp:``/``relay:``) key, so
    #: every pre-gate peer keeps its existing behavior unchanged.
    CARRIER = "carrier"


class GateDecision(Enum):
    """What the caller should do with the connection."""

    ACCEPT = "accept"   #: identity resolved and not banned — proceed
    REJECT = "reject"   #: the resolved identity is banned — refuse/disconnect


@dataclass(frozen=True)
class GateVerdict:
    """The gate's ruling on one connection attempt.

    Immutable and integer/str/enum-only. ``rep_key`` is the key the caller must
    use for *all* subsequent reputation accounting on this connection — that is
    the whole point: penalties land on the proven identity when one was proven,
    so a later ban follows the identity rather than the rotating carrier.
    """

    #: ``node:<pubkey>`` when an identity was proven, else the carrier key.
    rep_key: str
    #: How ``rep_key`` was derived (proven identity vs. carrier fallback).
    source: IdentitySource
    #: ACCEPT or REJECT, computed against the ban ledger keyed on ``rep_key``.
    decision: GateDecision
    #: The proven compressed secp256k1 pubkey hex, or ``None`` if unproven.
    pubkey: Optional[str]
    #: The carrier key the connection arrived on (always present, for audit).
    carrier_key: str

    @property
    def accepted(self) -> bool:
        return self.decision is GateDecision.ACCEPT

    @property
    def proven(self) -> bool:
        """True iff a crypto identity was proven (not a carrier fallback)."""
        return self.source is not IdentitySource.CARRIER


def _require_carrier(carrier_key: str) -> None:
    if not isinstance(carrier_key, str) or not carrier_key:
        raise TypeError("carrier_key must be a non-empty str")


class PeerIdentityGate:
    """Connection gate that keys the ban ledger on a proven crypto identity.

    The gate owns the binding between an identity proof and a
    :class:`~knitweb.p2p.reputation.PeerReputation` ledger. It does **not** own
    the ledger's storage — the same ledger can be shared with the rest of the
    node — it only decides *which key* a connection is judged and penalized under,
    and offers convenience accessors that route through that key.

    Determinism: the verifier clock is injected per call (``now``); the
    challenge-nonce source is injectable at construction (defaults to the
    identity module's CSPRNG). No method consults a wall-clock or RNG implicitly,
    so a replayed input stream yields a replayed verdict stream.
    """

    def __init__(
        self,
        reputation: PeerReputation,
        *,
        proof_window_s: int = identity.DEFAULT_PROOF_WINDOW_S,
        nonce_source: Optional[Callable[[], bytes]] = None,
        seen_proof_cap: int = identity.DEFAULT_SEEN_PROOF_CAP,
    ) -> None:
        if not isinstance(reputation, PeerReputation):
            raise TypeError("reputation must be a PeerReputation")
        if not isinstance(proof_window_s, int) or isinstance(proof_window_s, bool):
            raise TypeError("proof_window_s must be int")
        if proof_window_s < 0:
            raise ValueError("proof_window_s must be >= 0")
        self._rep = reputation
        self._window = proof_window_s
        self._nonce_source = nonce_source
        # Replay-within-window cache (#90): a verbatim PiggybackProof is accepted at
        # most once. Bounded + integer-only; the clock is injected per resolve().
        self._seen = identity.SeenProofCache(capacity=seen_proof_cap)

    # ── Connection setup ──────────────────────────────────────────────────────

    def new_challenge(self) -> Challenge:
        """Mint a fresh server challenge to send to a dialing peer.

        Uses the injected ``nonce_source`` when provided (deterministic tests),
        otherwise the identity module's 32-byte CSPRNG nonce.
        """
        if self._nonce_source is None:
            return identity.issue_challenge()
        nonce = self._nonce_source()
        return identity.issue_challenge(nonce=nonce)

    def resolve(
        self,
        carrier_key: str,
        *,
        challenge: Optional[Challenge] = None,
        proof: Optional[Union[Proof, PiggybackProof]] = None,
        now: Optional[int] = None,
        binding: bytes = b"",
    ) -> GateVerdict:
        """Resolve the reputation key for a connection and rule ACCEPT/REJECT.

        ``carrier_key`` is the endpoint the connection arrived on (e.g.
        ``tcp:1.2.3.4`` or ``relay:<mailbox>``) — always the fallback identity.

        Identity resolution, strongest first:

          * if ``proof`` is a challenge :class:`~knitweb.p2p.identity.Proof` and a
            ``challenge`` is supplied, verify it against that exact challenge;
          * if ``proof`` is a :class:`~knitweb.p2p.identity.PiggybackProof`,
            verify its signature, freshness against the injected ``now`` (required
            for a piggyback proof), and its ``binding`` against the connection/body
            ``binding`` the verifier expects (#90), then check it against the
            seen-proof cache so a verbatim replay within the window is rejected.

        On a verified (and first-seen) proof the verdict is keyed on
        ``node:<pubkey>`` with the matching :class:`IdentitySource`; otherwise it
        falls back to ``carrier_key`` with :data:`IdentitySource.CARRIER`. Either
        way the ban ledger is consulted on the *resolved* key to compute the
        decision.

        ``binding`` (#90) is the connection/body context the verifier requires the
        piggyback proof to commit to: a captured proof lifted onto a different
        connection or first-message body carries a different binding and is
        rejected to carrier fallback. Default empty binding preserves the unbound
        behaviour for callers that do not bind.

        Never raises on a bad/forged/stale/replayed proof — a failed proof is simply
        a carrier fallback (defence-in-depth: a malformed proof must not be a DoS
        lever against the handshake).
        """
        _require_carrier(carrier_key)

        rep_key = carrier_key
        source = IdentitySource.CARRIER
        pubkey: Optional[str] = None

        if isinstance(proof, Proof) and challenge is not None:
            proven = identity.verify_proof(challenge, proof)
            if proven is not None:
                pubkey = proven
                rep_key = identity.node_peer_id(proven)
                source = IdentitySource.CHALLENGE
        elif isinstance(proof, PiggybackProof):
            if now is None:
                raise ValueError("now is required to verify a PiggybackProof")
            if not isinstance(now, int) or isinstance(now, bool):
                raise TypeError("now must be int")
            resolved = identity.verify_id_proof(
                proof, now=now, window=self._window, binding=binding
            )
            # Accept at most once within the window: a verbatim replay of an
            # already-seen proof is treated as unproven (carrier fallback), so a
            # captured honest proof cannot be re-presented to blame node:<pubkey>.
            if resolved is not None and self._seen.check_and_record(
                proof, now=now, window=self._window
            ):
                # verify_id_proof already returns the namespaced node:<pubkey>.
                rep_key = resolved
                pubkey = proof.pubkey
                source = IdentitySource.PIGGYBACK

        decision = (
            GateDecision.REJECT
            if self._rep.is_banned(rep_key)
            else GateDecision.ACCEPT
        )
        return GateVerdict(
            rep_key=rep_key,
            source=source,
            decision=decision,
            pubkey=pubkey,
            carrier_key=carrier_key,
        )

    # ── Consequence — routes through the verdict's resolved key ────────────────

    def penalize(
        self, verdict: GateVerdict, offense: Union[Offense, int]
    ) -> GateVerdict:
        """Charge ``offense`` against ``verdict.rep_key`` and re-rule the decision.

        This is the load-bearing step: because ``rep_key`` is the proven
        ``node:<pubkey>`` whenever a proof verified, the penalty (and therefore
        any resulting ban) attaches to the *identity*, so a Sybil that rotates its
        carrier cannot shed it. Returns a fresh verdict reflecting the post-charge
        decision (same key/source/pubkey/carrier).
        """
        now_banned = self._rep.penalize(verdict.rep_key, offense)
        return GateVerdict(
            rep_key=verdict.rep_key,
            source=verdict.source,
            decision=GateDecision.REJECT if now_banned else GateDecision.ACCEPT,
            pubkey=verdict.pubkey,
            carrier_key=verdict.carrier_key,
        )

    def is_banned(self, verdict: GateVerdict) -> bool:
        """True iff the verdict's resolved identity is currently banned."""
        return self._rep.is_banned(verdict.rep_key)

    def score(self, verdict: GateVerdict) -> int:
        """Current misbehavior score for the verdict's resolved identity."""
        return self._rep.score(verdict.rep_key)

    @property
    def reputation(self) -> PeerReputation:
        """The underlying ban ledger (shared with the rest of the node)."""
        return self._rep

    @property
    def proof_window_s(self) -> int:
        return self._window
