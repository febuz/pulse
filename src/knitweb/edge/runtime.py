"""Edge runtime — the consumer side a humanoid / AR glass / IoT model runs.

This closes the collective-intelligence loop on the device. A humanoid receives a
synaptic bytecode bundle (compiled from the shared-memory Web), **verifies the
originator** before trusting it, and exposes the verified relations two ways:

  * a queryable view for **AR / physical vision** overlays (what's at this object?),
  * a compact **feature dict** to **augment the agent's inner (software) model**.

Both happen locally, with no context tax and no network round-trip — the device
only had to receive a few kilobytes of signed bytecode. An unverified or tampered
bundle is *refused*: acting on a forged relation is a safety problem, so trust is
checked before the bundle is ever read.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..synaptic import bytecode as _bc

__all__ = ["EdgeBundle", "EdgeVerifyError"]


class EdgeVerifyError(Exception):
    """Raised when a bundle fails originator verification and must not be used."""


@dataclass
class EdgeBundle:
    """A verified, decoded relation bundle ready for AR + model augmentation."""

    asset_cid: str
    originator: str
    relations: list  # list[knitweb.synaptic.bytecode.Relation]

    # -- loading (verify-before-trust) ------------------------------------

    @classmethod
    def load(
        cls,
        data: bytes,
        originator_pub: str | None = None,
        signature: str | None = None,
    ) -> "EdgeBundle":
        """Decode a bundle. If ``originator_pub`` + ``signature`` are given, the
        signature is verified first and a failure raises ``EdgeVerifyError``.

        Passing no key/signature loads *unverified* — only do that for trusted
        local data; a humanoid acting on third-party data should always verify.
        """
        if originator_pub is not None and signature is not None:
            if not _bc.verify_bundle(originator_pub, data, signature):
                raise EdgeVerifyError("originator signature invalid — refusing to load")
        decoded = _bc.decode_bundle(data)
        return cls(
            asset_cid=decoded["asset_cid"],
            originator=decoded["originator"],
            relations=decoded["relations"],
        )

    # -- AR / physical-vision queries -------------------------------------

    def query(self, subject=None, predicate=None, source_type=None):
        """Return relations matching any combination of filters (AR overlay lookup)."""
        out = []
        for r in self.relations:
            if subject is not None and r.subject != subject:
                continue
            if predicate is not None and r.predicate != predicate:
                continue
            if source_type is not None and r.source_type != source_type:
                continue
            out.append(r)
        return out

    def sources_for(self, subject: str) -> list[tuple[str, str]]:
        """(source_type, url) pairs verifying ``subject`` — what an AR glass shows."""
        return sorted(
            (r.source_type, r.obj) for r in self.relations if r.subject == subject
        )

    # -- inner-model augmentation -----------------------------------------

    def to_feature_dict(self) -> dict[str, dict[str, list[str]]]:
        """Compact ``subject -> {source_type: [objects]}`` view for an edge ML model.

        Deterministic (objects sorted) so the same bundle augments every agent's
        model identically.
        """
        feats: dict[str, dict[str, list[str]]] = {}
        for r in self.relations:
            feats.setdefault(r.subject, {}).setdefault(r.source_type, []).append(r.obj)
        for subj in feats:
            for st in feats[subj]:
                feats[subj][st] = sorted(feats[subj][st])
        return feats

    def __len__(self) -> int:
        return len(self.relations)
