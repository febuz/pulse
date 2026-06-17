"""Anchoring — pin fabric checkpoint roots to an external target, with a signed receipt.

Knitweb checkpoints (``fabric.items.FabricCheckpoint``) already chain internally via
Pulse beats. *Anchoring* is the complementary, outward step: committing a checkpoint's
``state_root`` to an external system (Ethereum, Bitcoin, OriginTrail, …) so the fabric's
state is auditable by parties who don't run a node. Each anchoring produces an
:class:`AnchorReceipt` — a notary-signed attestation binding ``(state_root, epoch,
beat_cid, target, external_ref)`` — that anyone can verify offline against the checkpoint.

The external target is a pluggable :class:`AnchorBackend`; this module ships the
in-process :class:`LocalAnchorBackend` (a self-anchor whose "external ref" is a content
id), so the receipt machinery is fully provable today without any external chain. Real
backends (an Ethereum tx, an OriginTrail Knowledge Asset) implement the same ``submit``
shape and slot in unchanged.

Everything on the signed path is integer/string only (canonical-CBOR friendly); no
external dependency touches the hash/signature path.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import canonical, crypto
from ..fabric.items import FabricCheckpoint

__all__ = [
    "AnchorBackend",
    "LocalAnchorBackend",
    "AnchorReceipt",
    "Notary",
    "verify_receipt",
]


def _require_int(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int")


def _require_str(name: str, value: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str")


class AnchorBackend:
    """Abstract external-anchor target. Subclasses commit a root and return a ref."""

    target = "abstract"

    def submit(self, state_root: str, timestamp: int) -> str:  # pragma: no cover
        raise NotImplementedError


class LocalAnchorBackend(AnchorBackend):
    """A self-anchor: the 'external reference' is a deterministic content id.

    Useful for tests and single-operator deployments — it proves the receipt flow
    end-to-end without an external chain. The ref is reproducible, so a verifier can
    recompute it from the root + timestamp.
    """

    target = "local"

    def submit(self, state_root: str, timestamp: int) -> str:
        _require_str("state_root", state_root)
        _require_int("timestamp", timestamp)
        return canonical.cid(
            {"kind": "local-anchor", "state_root": state_root, "timestamp": timestamp}
        )


@dataclass(frozen=True)
class AnchorReceipt:
    """A notary-signed proof that a checkpoint root was anchored to ``target``."""

    state_root: str
    epoch: int
    beat_cid: str
    target: str
    external_ref: str
    notary: str        # PLS address of the notary
    timestamp: int
    notary_pub: str    # compressed secp256k1 public key (hex)
    sig: str           # DER signature (hex) over to_record()

    def __post_init__(self) -> None:
        _require_str("state_root", self.state_root)
        _require_int("epoch", self.epoch)
        _require_str("beat_cid", self.beat_cid)
        _require_str("target", self.target)
        _require_str("external_ref", self.external_ref)
        _require_str("notary", self.notary)
        _require_int("timestamp", self.timestamp)
        _require_str("notary_pub", self.notary_pub)
        _require_str("sig", self.sig)

    def to_record(self) -> dict:
        """The signed payload (signature + notary_pub are not part of it)."""
        return {
            "kind": "anchor-receipt",
            "state_root": self.state_root,
            "epoch": self.epoch,
            "beat_cid": self.beat_cid,
            "target": self.target,
            "external_ref": self.external_ref,
            "notary": self.notary,
            "timestamp": self.timestamp,
        }

    @property
    def cid(self) -> str:
        return canonical.cid(self.to_record())

    def verify(self) -> bool:
        """True iff the notary address derives from the key and the signature checks."""
        if self.notary != crypto.address(self.notary_pub):
            return False
        return crypto.verify(self.notary_pub, canonical.encode(self.to_record()), self.sig)


class Notary:
    """An anchoring authority: holds a key, anchors checkpoints, signs receipts."""

    def __init__(self, notary_priv: str) -> None:
        self._priv = notary_priv
        self.pub = crypto.public_from_private(notary_priv)
        self.address = crypto.address(self.pub)

    def anchor(
        self,
        checkpoint: FabricCheckpoint,
        backend: AnchorBackend,
        timestamp: int,
    ) -> AnchorReceipt:
        """Anchor ``checkpoint`` via ``backend`` and return a signed receipt."""
        _require_int("timestamp", timestamp)
        external_ref = backend.submit(checkpoint.state_root, timestamp)
        receipt = AnchorReceipt(
            state_root=checkpoint.state_root,
            epoch=checkpoint.epoch,
            beat_cid=checkpoint.beat_cid,
            target=backend.target,
            external_ref=external_ref,
            notary=self.address,
            timestamp=timestamp,
            notary_pub=self.pub,
            sig="",
        )
        sig = crypto.sign(self._priv, canonical.encode(receipt.to_record()))
        return AnchorReceipt(**{**receipt.__dict__, "sig": sig})


def verify_receipt(receipt: AnchorReceipt, checkpoint: FabricCheckpoint) -> bool:
    """True iff ``receipt`` is a valid notary signature AND binds *this* checkpoint.

    Checks (all deterministic): the receipt's signature is valid for its notary key,
    and its ``(state_root, epoch, beat_cid)`` match the checkpoint — so a receipt can
    neither be forged nor re-pointed at a different checkpoint.
    """
    if not receipt.verify():
        return False
    return (
        receipt.state_root == checkpoint.state_root
        and receipt.epoch == checkpoint.epoch
        and receipt.beat_cid == checkpoint.beat_cid
    )
