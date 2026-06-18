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
    "verify_anchor",
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


def verify_anchor(receipt: AnchorReceipt, against) -> dict:
    """Verify a provenance anchor and report *what* it covers, as a structured result.

    This is the provenance-explorer primitive (issue #13): given an
    :class:`AnchorReceipt` and the thing it claims to anchor — either the live
    :class:`~knitweb.fabric.web.Web` or a :class:`FabricCheckpoint` — answer the two
    questions an independent auditor cares about:

      1. *Is the receipt authentic?* — the notary address derives from its key and the
         signature checks (delegates to :meth:`AnchorReceipt.verify`).
      2. *Does it still cover the current state?* — the receipt's ``state_root`` matches
         the root we recompute now. Passing a ``Web`` re-derives the **live** root via
         :func:`~knitweb.fabric.items.web_state_root`, so a web mutated after anchoring
         no longer verifies; passing a ``FabricCheckpoint`` compares the recorded root
         (and also binds ``epoch``/``beat_cid``, like :func:`verify_receipt`).

    Returns a dict ``{"verified", "state_root", "covered_root", "signature_ok",
    "root_match", "target", "external_ref"}``. ``state_root`` is the root we computed
    from ``against`` (the ground truth); ``covered_root`` is what the receipt claims.
    ``verified`` is ``True`` only when both the signature and the roots agree — never
    raises on a mismatch, so the explorer can render a red/green result either way.
    """
    # Lazy import: items.py imports fabric.web which we don't want on the module path,
    # and this keeps the signed/crypto path free of fabric dependencies.
    from ..fabric.items import web_state_root
    from ..fabric.web import Web

    signature_ok = receipt.verify()

    if isinstance(against, FabricCheckpoint):
        current_root = against.state_root
        root_match = (
            receipt.state_root == against.state_root
            and receipt.epoch == against.epoch
            and receipt.beat_cid == against.beat_cid
        )
    elif isinstance(against, Web):
        current_root = web_state_root(against)
        root_match = receipt.state_root == current_root
    else:
        raise TypeError("against must be a Web or a FabricCheckpoint")

    return {
        "verified": bool(signature_ok and root_match),
        "state_root": current_root,
        "covered_root": receipt.state_root,
        "signature_ok": signature_ok,
        "root_match": root_match,
        "target": receipt.target,
        "external_ref": receipt.external_ref,
    }
