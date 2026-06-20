"""Crowdfunding campaign lifecycle — signed definitions + independently-audited outcomes.

The mirror of vBank's poll/result for fundraising. A campaign **authority** defines a campaign
(a funding goal + a window), pledgers contribute gated, signed pledges (see the package's
``CrowdfundingKnitweb``), and the authority certifies an outcome: how much was raised in-window,
whether the goal was met, and how many distinct verified people pledged — all attributable,
deterministic, and independently auditable via a ``pledge_root`` over the counted pledges.

Unlike a vote, pledges are **not** deduped on the nullifier (a person may pledge repeatedly);
the nullifier is kept so the campaign can still prove every pledge came from a distinct verified
EU natural person and count them, without any identity on the fabric.

Scope note: this models donation/reward fundraising (integer ``amount`` in PLS-wei). Investment
or lending flows need regulatory review (Reg. (EU) 2020/1503) and are out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from ...core import canonical, crypto
from ...fabric.attest import Attestation, attest
from ...fabric.web import Web

__all__ = [
    "PLEDGE_KIND",
    "CAMPAIGN_KIND",
    "OUTCOME_KIND",
    "SETTLEMENT_KIND",
    "Campaign",
    "CrowdfundingCampaign",
    "verify_outcome",
    "audit_outcome",
    "verify_settlement",
    "audit_settlement",
    "settlement_entries",
    "collect_pledges",
]

PLEDGE_KIND = "crowdfunding-pledge"
CAMPAIGN_KIND = "crowdfunding-campaign"
OUTCOME_KIND = "crowdfunding-outcome"
SETTLEMENT_KIND = "crowdfunding-settlement"


@dataclass(frozen=True)
class Campaign:
    """A campaign definition: a funding goal (PLS-wei) and a pledging window for one ``scope``."""

    scope: str         # campaign id
    goal: int          # PLS-wei target, strictly positive
    opens_at: int      # epoch seconds (inclusive)
    closes_at: int     # epoch seconds (exclusive)
    beneficiary: str = ""  # pls1 address funds are released to if the goal is met (required to settle a success)

    def __post_init__(self) -> None:
        for name, value in (("goal", self.goal), ("opens_at", self.opens_at),
                            ("closes_at", self.closes_at)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"campaign {name} must be an int")
        if self.goal <= 0:
            raise ValueError("campaign goal must be strictly positive")
        if self.closes_at <= self.opens_at:
            raise ValueError("closes_at must be after opens_at")
        if not self.scope:
            raise ValueError("scope must be non-empty")
        if self.beneficiary and not crypto.is_valid_address(self.beneficiary):
            raise ValueError("beneficiary must be empty or a current PLS address")


class CrowdfundingCampaign:
    """A campaign authority: defines campaigns and certifies their outcomes."""

    def __init__(self, authority_priv: str, scope: str) -> None:
        if not scope:
            raise ValueError("scope must be a non-empty string")
        self._priv = authority_priv
        self.authority_pub = crypto.public_from_private(authority_priv)
        self.authority = crypto.address(self.authority_pub)
        self.scope = scope

    def define(self, campaign: Campaign) -> Attestation:
        """Build and sign a ``crowdfunding-campaign`` definition record."""
        if campaign.scope != self.scope:
            raise ValueError(f"campaign scope {campaign.scope!r} != authority scope {self.scope!r}")
        record = {
            "kind": CAMPAIGN_KIND,
            "scope": campaign.scope,
            "goal": campaign.goal,
            "opens_at": campaign.opens_at,
            "closes_at": campaign.closes_at,
            "beneficiary": campaign.beneficiary,
            "authority": self.authority,
        }
        canonical.encode(record)
        return attest(record, self._priv, author_field="authority")

    def certify_outcome(self, campaign_record: dict, pledges: list[dict]) -> Attestation:
        """Aggregate in-window pledges and sign the outcome (deterministic; see verify_outcome)."""
        if campaign_record.get("authority") != self.authority:
            raise ValueError("only the defining authority may certify this campaign's outcome")
        record = _outcome_record(campaign_record, pledges, self.authority)
        return attest(record, self._priv, author_field="authority")

    def weave_outcome(self, campaign_record: dict, pledges: list[dict], web: Web) -> tuple[str, Attestation]:
        """Certify and weave an outcome into ``web``; return (cid, attestation)."""
        att = self.certify_outcome(campaign_record, pledges)
        return web.weave(att.record), att

    def settle(self, outcome_record: dict, campaign_record: dict, pledges: list[dict]) -> Attestation:
        """Sign the all-or-nothing settlement instruction for a certified outcome.

        If the goal was met the mode is ``release`` (every counted pledge pays the campaign's
        ``beneficiary``); otherwise ``refund`` (each pledge returns to its pledger). The result
        is deterministic and independently checkable (:func:`verify_settlement`); it is the
        instruction a payout layer would execute — it does not itself move PLS.
        """
        if campaign_record.get("authority") != self.authority:
            raise ValueError("only the defining authority may settle this campaign")
        record = _settlement_record(outcome_record, campaign_record, pledges, self.authority)
        return attest(record, self._priv, author_field="authority")


def _in_window_pledges(campaign_record: dict, pledges: list[dict]) -> List[dict]:
    """Validate pledges against a campaign and return those cast inside its window."""
    if campaign_record.get("kind") != CAMPAIGN_KIND:
        raise ValueError(f"not a {CAMPAIGN_KIND}: {campaign_record.get('kind')!r}")
    scope = campaign_record["scope"]
    opens_at = campaign_record["opens_at"]
    closes_at = campaign_record["closes_at"]
    in_window: List[dict] = []
    for pledge in pledges:
        if pledge.get("kind") != PLEDGE_KIND:
            raise ValueError(f"not a {PLEDGE_KIND}: {pledge.get('kind')!r}")
        if pledge.get("scope") != scope:
            raise ValueError("pledge scope does not match the campaign")
        pledged_at = pledge.get("pledged_at")
        if not isinstance(pledged_at, int) or isinstance(pledged_at, bool):
            raise ValueError("pledge pledged_at must be an int")
        if not (opens_at <= pledged_at < closes_at):
            continue  # outside the pledging window -> does not count
        amount = pledge.get("amount")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ValueError("pledge amount must be a positive int")
        in_window.append(pledge)
    return in_window


def _outcome_record(campaign_record: dict, pledges: list[dict], authority_addr: str) -> dict:
    """The deterministic ``crowdfunding-outcome`` record for (campaign, pledges) — pure, unsigned."""
    in_window = _in_window_pledges(campaign_record, pledges)
    goal = campaign_record["goal"]
    total_raised = sum(p["amount"] for p in in_window)
    pledger_nullifiers = {p["scope_nullifier"] for p in in_window}
    included_cids = sorted(canonical.cid(p) for p in in_window)
    pledge_root = crypto.merkle_root(
        [crypto.sha256(cid.encode("utf-8")) for cid in included_cids]
    ).hex()

    record = {
        "kind": OUTCOME_KIND,
        "scope": campaign_record["scope"],
        "campaign_cid": canonical.cid(campaign_record),
        "authority": authority_addr,
        "goal": goal,
        "total_raised": total_raised,
        "goal_met": total_raised >= goal,
        "pledger_count": len(pledger_nullifiers),
        "pledge_count": len(included_cids),
        "pledge_root": pledge_root,
    }
    canonical.encode(record)
    return record


def verify_outcome(outcome_record: dict, campaign_record: dict, pledges: list[dict]) -> bool:
    """True iff ``outcome_record`` is exactly what an honest authority certifies from
    ``campaign_record`` + ``pledges`` (independent recomputation; not a signature check)."""
    if not isinstance(outcome_record, dict) or not isinstance(campaign_record, dict):
        return False
    if outcome_record.get("kind") != OUTCOME_KIND:
        return False
    if campaign_record.get("authority") != outcome_record.get("authority"):
        return False
    try:
        expected = _outcome_record(campaign_record, pledges, outcome_record["authority"])
    except (ValueError, KeyError, TypeError):
        return False
    return expected == outcome_record


def audit_outcome(outcome_att: Attestation, campaign_record: dict, pledges: list[dict]) -> bool:
    """Full audit: the outcome is validly authority-signed AND recomputes from the pledges."""
    return (
        outcome_att.verify(author_field="authority")
        and verify_outcome(outcome_att.record, campaign_record, pledges)
    )


def settlement_entries(outcome_record: dict, campaign_record: dict,
                       pledges: list[dict]) -> tuple[str, list[tuple[str, str, int]]]:
    """Return ``(mode, entries)`` where each entry is ``(pledge_cid, payee, amount)``.

    ``mode`` is ``release`` (payee = the campaign beneficiary) when the goal was met, else
    ``refund`` (payee = the pledge's own pledger). Entries are sorted for determinism. Requires
    the supplied ``outcome_record`` to be the honest outcome of these pledges. This is the
    payout plan an executor turns into ledger transfers (see :mod:`...settlement`).
    """
    if outcome_record.get("kind") != OUTCOME_KIND:
        raise ValueError(f"not a {OUTCOME_KIND}: {outcome_record.get('kind')!r}")
    if _outcome_record(campaign_record, pledges, outcome_record.get("authority")) != outcome_record:
        raise ValueError("outcome record does not match the pledges")

    in_window = _in_window_pledges(campaign_record, pledges)
    mode = "release" if outcome_record["goal_met"] else "refund"
    beneficiary = campaign_record.get("beneficiary", "")
    if mode == "release" and not beneficiary:
        raise ValueError("campaign has no beneficiary; cannot release a met goal")

    entries = []
    for pledge in in_window:
        payee = beneficiary if mode == "release" else pledge["actor"]
        entries.append((canonical.cid(pledge), payee, pledge["amount"]))
    entries.sort()
    return mode, entries


def _settlement_record(outcome_record: dict, campaign_record: dict, pledges: list[dict],
                       authority_addr: str) -> dict:
    """The deterministic ``crowdfunding-settlement`` record — pure, unsigned.

    Recomputes the outcome from the pledges and requires the supplied ``outcome_record`` to
    match it, so a settlement is always consistent with the certified outcome.
    """
    mode, entries = settlement_entries(outcome_record, campaign_record, pledges)
    total = sum(amount for _cid, _payee, amount in entries)
    settlement_root = crypto.merkle_root(
        [crypto.sha256(canonical.encode([cid, payee, amount])) for cid, payee, amount in entries]
    ).hex()

    record = {
        "kind": SETTLEMENT_KIND,
        "scope": campaign_record["scope"],
        "campaign_cid": canonical.cid(campaign_record),
        "outcome_cid": canonical.cid(outcome_record),
        "authority": authority_addr,
        "mode": mode,
        "total_amount": total,
        "entry_count": len(entries),
        "settlement_root": settlement_root,
    }
    canonical.encode(record)
    return record


def verify_settlement(settlement_record: dict, outcome_record: dict, campaign_record: dict,
                      pledges: list[dict]) -> bool:
    """True iff ``settlement_record`` is exactly the honest settlement for this
    (outcome, campaign, pledges) — independent recomputation; not a signature check."""
    if not isinstance(settlement_record, dict) or not isinstance(campaign_record, dict):
        return False
    if settlement_record.get("kind") != SETTLEMENT_KIND:
        return False
    if campaign_record.get("authority") != settlement_record.get("authority"):
        return False
    try:
        expected = _settlement_record(outcome_record, campaign_record, pledges,
                                      settlement_record["authority"])
    except (ValueError, KeyError, TypeError):
        return False
    return expected == settlement_record


def audit_settlement(settlement_att: Attestation, outcome_record: dict, campaign_record: dict,
                     pledges: list[dict]) -> bool:
    """Full audit: the settlement is validly authority-signed AND recomputes from the pledges."""
    return (
        settlement_att.verify(author_field="authority")
        and verify_settlement(settlement_att.record, outcome_record, campaign_record, pledges)
    )


def collect_pledges(web: Web, scope: str) -> List[dict]:
    """Read every ``crowdfunding-pledge`` record for ``scope`` out of a woven Web (CID order)."""
    found = [
        record
        for record in web.nodes.values()
        if record.get("kind") == PLEDGE_KIND and record.get("scope") == scope
    ]
    found.sort(key=canonical.cid)
    return found
