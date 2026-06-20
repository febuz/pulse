"""vBank (minimal stub) — a vote is impossible without a personhood ticket.

This is *not* the full vBank app (that is Step 5 of the adoption roadmap). It is the
smallest domain knitweb that proves the personhood foundation is consumed as a gate rather
than bolted on: :meth:`VbankKnitweb.emit` refuses to produce a ballot unless it is handed
a :class:`~knitweb.personhood.gate.PersonhoodTicket` that matches the ballot's scope, voter,
and nullifier. The dependency points one way — vbank imports ``personhood``, never the
reverse.

A ballot record carries the **scope nullifier** (the one-person-one-vote dedup key) and the
voter's **pairwise** address, but **no identity** — the same anti-PII property the anchor
enforces. The ballot is signed by the holder's pairwise key, so the *content* signature is
decoupled from the *authorisation* ticket (the receipt-freeness / ZK seam).
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core import canonical, crypto
from ...fabric.attest import Attestation, attest
from ...fabric.web import Web
from ...personhood.gate import PersonhoodTicket
from .liquid import (
    DELEGATION_KIND,
    LIQUID_RESULT_KIND,
    Delegation,
    audit_liquid_result,
    certify_liquid_result,
    collect_delegations,
    delegation_map,
    emit_delegation,
    liquid_result_record,
    resolve_liquid,
    verify_liquid_result,
)
from .poll import (
    POLL_KIND,
    RESULT_KIND,
    Poll,
    VbankPoll,
    audit_result,
    collect_polls,
    is_poll_open,
    poll_status,
    verify_result,
)
from .ranked import (
    RANKED_BALLOT_KIND,
    RANKED_RESULT_KIND,
    RankedBallot,
    audit_ranked_result,
    certify_ranked_result,
    collect_ranked_ballots,
    emit_ranked_ballot,
    instant_runoff,
    ranked_result_record,
    verify_ranked_result,
)
from .tally import BALLOT_KIND, TALLY_KIND, collect_ballots, tally

__all__ = [
    "Ballot", "VbankKnitweb", "tally", "collect_ballots", "BALLOT_KIND", "TALLY_KIND",
    "Poll", "VbankPoll", "POLL_KIND", "RESULT_KIND", "verify_result", "audit_result",
    "collect_polls", "poll_status", "is_poll_open",
    "Delegation", "emit_delegation", "collect_delegations", "delegation_map",
    "resolve_liquid", "liquid_result_record", "certify_liquid_result",
    "verify_liquid_result", "audit_liquid_result",
    "DELEGATION_KIND", "LIQUID_RESULT_KIND",
    "RankedBallot", "emit_ranked_ballot", "collect_ranked_ballots", "instant_runoff",
    "ranked_result_record", "certify_ranked_result", "verify_ranked_result", "audit_ranked_result",
    "RANKED_BALLOT_KIND", "RANKED_RESULT_KIND",
]


@dataclass(frozen=True)
class Ballot:
    """One vote: an integer ``choice`` in a poll, cast by a scope-pairwise voter."""

    scope: str
    poll_id: str
    choice: int          # option index (integer-only, canonical-safe)
    voter: str           # pls1 address of the holder's pairwise key
    scope_nullifier: str # one-person-one-vote dedup key (no identity)
    seq: int = 0         # re-vote counter; the highest seq for a nullifier wins in the tally
    cast_at: int = 0     # epoch seconds the ballot was cast; counted only inside the poll window

    def __post_init__(self) -> None:
        for name, value in (("choice", self.choice), ("seq", self.seq), ("cast_at", self.cast_at)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"ballot {name} must be an int")
        if self.seq < 0:
            raise ValueError("ballot seq must be >= 0")
        if self.cast_at < 0:
            raise ValueError("ballot cast_at must be >= 0")


class VbankKnitweb:
    """Emits signed ballots — but only against a valid personhood ticket."""

    KIND = BALLOT_KIND

    def __init__(self, scope: str) -> None:
        if not scope:
            raise ValueError("scope must be a non-empty string")
        self.scope = scope

    def _check_ticket(self, ballot: Ballot, ticket: PersonhoodTicket) -> None:
        if ballot.scope != self.scope:
            raise ValueError(f"ballot scope {ballot.scope!r} != knitweb scope {self.scope!r}")
        if not isinstance(ticket, PersonhoodTicket):
            raise TypeError("a PersonhoodTicket is required to cast a ballot")
        if ticket.scope != ballot.scope:
            raise ValueError("ticket scope does not match the ballot")
        if ticket.scope_nullifier != ballot.scope_nullifier:
            raise ValueError("ticket nullifier does not authorise this ballot")
        if ticket.holder_pairwise != ballot.voter:
            raise ValueError("ticket holder does not match the ballot voter")

    def to_record(self, ballot: Ballot, ticket: PersonhoodTicket) -> dict:
        """Build the integer-only ballot record (gated on a matching ticket)."""
        self._check_ticket(ballot, ticket)
        record = {
            "kind": self.KIND,
            "scope": ballot.scope,
            "poll_id": ballot.poll_id,
            "choice": ballot.choice,
            "actor": ballot.voter,
            "scope_nullifier": ballot.scope_nullifier,
            "seq": ballot.seq,
            "cast_at": ballot.cast_at,
        }
        # The record has a fixed key set (no caller-supplied keys), so the load-bearing
        # checks are a valid PLS author address + canonical encodability (rejects floats /
        # non-deterministic content). Kept self-contained on committed core primitives.
        if not crypto.is_valid_address(record["actor"]):
            raise ValueError("ballot actor must be a current PLS address")
        canonical.encode(record)
        return record

    def emit(self, ballot: Ballot, ticket: PersonhoodTicket, voter_priv: str) -> Attestation:
        """Validate the ticket, then sign the ballot with the holder's pairwise key.

        The pairwise public key must match ``ballot.voter`` (``attest`` enforces this), so a
        ballot is bound to the same scope identity the ticket authorised.
        """
        record = self.to_record(ballot, ticket)
        return attest(record, voter_priv, author_field="actor")

    def weave(
        self, ballot: Ballot, ticket: PersonhoodTicket, voter_priv: str, web: Web
    ) -> tuple[str, Attestation]:
        """Emit a gated ballot and weave it into ``web``; return (cid, attestation)."""
        att = self.emit(ballot, ticket, voter_priv)
        return web.weave(att.record), att
