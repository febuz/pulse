"""OriginTrail anchor backend — publish a checkpoint root as a DKG Knowledge Asset.

This is the USP-aligned :class:`~knitweb.anchor.AnchorBackend`: it anchors a fabric
checkpoint to **OriginTrail** by publishing the ``state_root`` as a Knowledge Asset on
the Decentralized Knowledge Graph and returning its UAL (Universal Asset Locator) as the
receipt's ``external_ref``. This closes the loop with the synaptic compiler, which already
*reads* OriginTrail assets (``synaptic.origintrail``) — now the fabric also *writes* its
state back to the DKG, so a checkpoint is independently verifiable against OriginTrail.

The transport here is **in-process** (published assertions are kept in ``published``); a
production backend POSTs the identical assertion to a DKG node and gets a UAL back. The
returned UAL is **content-derived** — it commits the ``state_root`` via a CIDv1 assertion
id — so a verifier recomputes and checks it offline and can resolve the assertion back,
with no trust in the publisher. Integer/string-only; no external dependency on the path.
"""

from __future__ import annotations

from ..core import canonical
from . import AnchorBackend

__all__ = ["OriginTrailAnchorBackend", "assertion", "assertion_id", "ual", "DKG_NAMESPACE"]

DKG_NAMESPACE = "did:dkg:knitweb"


def assertion(state_root: str, timestamp: int) -> dict:
    """The OriginTrail-style Knowledge Asset assertion committing a checkpoint root."""
    return {
        "@context": "https://schema.org",
        "@type": "KnitwebCheckpointAnchor",
        "stateRoot": state_root,
        "anchoredAt": timestamp,
    }


def assertion_id(state_root: str, timestamp: int) -> str:
    """Content id (CIDv1) of the assertion — the DKG assertion identity."""
    return canonical.cid(assertion(state_root, timestamp))


def ual(state_root: str, timestamp: int) -> str:
    """The Universal Asset Locator a verifier can recompute from the root + time."""
    return f"{DKG_NAMESPACE}/{assertion_id(state_root, timestamp)}"


class OriginTrailAnchorBackend(AnchorBackend):
    """Anchors a checkpoint root to OriginTrail as a Knowledge Asset.

    ``submit`` publishes the assertion and returns its content-derived UAL; ``resolve``
    fetches the published assertion back by UAL (proving the round-trip). Swap the
    in-process store for a DKG client to go live — the UAL contract is unchanged.
    """

    target = "origintrail"

    def __init__(self) -> None:
        self.published: dict[str, dict] = {}

    def submit(self, state_root: str, timestamp: int) -> str:
        u = ual(state_root, timestamp)
        self.published[u] = assertion(state_root, timestamp)
        return u

    def resolve(self, asset_ual: str) -> dict | None:
        """Return the published assertion for ``asset_ual`` (None if unknown)."""
        return self.published.get(asset_ual)
