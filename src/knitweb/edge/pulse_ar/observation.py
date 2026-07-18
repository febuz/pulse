"""Pulse AR object observations — the WHAT / WHO / WHERE / HOW / DEVICE record.

A vision model in a pair of smartglasses (a YOLO detection head, refined by a CNN
and normalised by an LLM) turns a camera frame into **object observations**. Each
observation is a content-addressed, signed claim about one real-world object:

  * **WHAT**   — the object's class ``label`` + a ``taxonomy`` id + the detection
                 ``confidence_bps`` (basis points, an integer 0..10000).
  * **WHO**    — its ``owner`` and its ``maker`` (both PLS addresses): provenance
                 of the physical thing, not of the media that describes it.
  * **WHERE**  — a ``geohash`` cell + integer ``alt_band`` (reuses fabric.spatial),
                 so "what's near me?" stays a string-prefix test with no floats.
  * **HOW**    — the physical dimensions in integer millimetres (w × h × d).
  * **DEVICE** — the observing / exchanging device (a PLS address).

Every field near the hash is an integer or a string — never a float — so an
observation round-trips through canonical CBOR to a deterministic CID and can be
signed with the device key (secp256k1 ECDSA + SHA-256). Peers that receive it over
the bitchat BLE mesh verify that signature **before** trusting it: a
:class:`SignedObservation` binds the signature to the ``device`` address it claims,
so a forged or relabelled observation is refused. Acting on a bad observation is a
safety problem in the physical world, not a UI glitch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core import canonical, crypto

__all__ = ["Detection", "ObjectObservation", "SignedObservation", "CONF_FULL"]

CONF_FULL = 10000  # confidence is integer basis points; 10000 bps == 1.0


def _require_int(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int (no floats near the hash)")


def _require_bbox(bbox: tuple[int, int, int, int]) -> None:
    if len(bbox) != 4:
        raise ValueError("bbox must be (x, y, w, h)")
    for i, c in enumerate(bbox):
        _require_int(f"bbox[{i}]", c)
        if c < 0:
            raise ValueError("bbox components must be non-negative pixels")


# ---------------------------------------------------------------------------
# Detection — the raw vision output for one object (what a YOLO head emits)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Detection:
    """One raw detected box before the CNN/LLM refine it into an observation."""

    label: str
    confidence_bps: int                       # 0..10000 basis points (integer)
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)  # x, y, w, h in source pixels

    def __post_init__(self) -> None:
        _require_int("confidence_bps", self.confidence_bps)
        if not 0 <= self.confidence_bps <= CONF_FULL:
            raise ValueError("confidence_bps must be in [0, 10000]")
        _require_bbox(self.bbox)


# ---------------------------------------------------------------------------
# ObjectObservation — the canonical WHAT/WHO/WHERE/HOW/DEVICE record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ObjectObservation:
    """A content-addressed claim about one physical object seen by a device.

    Grouped exactly as the vision→fabric contract reads it: ``what`` (class),
    ``who`` (owner + maker), ``where`` (geohash cell), ``how`` (integer mm
    dimensions), and the observing ``device``. The record is float-free, so its
    CID is deterministic across every peer.
    """

    # -- WHAT (required) ---------------------------------------------------
    label: str
    taxonomy: str            # class / taxonomy id (OTKG/WordNet id, or a fiber CID)
    confidence_bps: int
    # -- WHERE (required) --------------------------------------------------
    geohash: str
    # -- DEVICE (required) -------------------------------------------------
    device: str              # PLS address of the observing / exchanging device
    # -- WHO ---------------------------------------------------------------
    owner: str = ""          # PLS address of the owner ("" = unknown)
    maker: str = ""          # PLS address of the maker / creator ("" = unknown)
    # -- WHERE (altitude) --------------------------------------------------
    alt_band: int = 0        # integer altitude band (0 = ground / unknown)
    # -- HOW (integer millimetres) ----------------------------------------
    width_mm: int = 0
    height_mm: int = 0
    depth_mm: int = 0
    # -- provenance --------------------------------------------------------
    observed_at: int = 0     # integer epoch (Pulse beat or unix seconds)
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)  # vision box in source pixels
    fiber_cid: str = ""      # optional link to the fabric knowledge fiber for WHAT

    def __post_init__(self) -> None:
        _require_int("confidence_bps", self.confidence_bps)
        if not 0 <= self.confidence_bps <= CONF_FULL:
            raise ValueError("confidence_bps must be in [0, 10000]")
        for name in ("alt_band", "width_mm", "height_mm", "depth_mm", "observed_at"):
            _require_int(name, getattr(self, name))
        for name in ("width_mm", "height_mm", "depth_mm"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative millimetres")
        _require_bbox(self.bbox)

    # -- canonical form ----------------------------------------------------

    def to_record(self) -> dict:
        """Nested, float-free record: exactly the WHAT/WHO/WHERE/HOW/DEVICE shape."""
        return {
            "kind": "object-observation",
            "what": {
                "label": self.label,
                "taxonomy": self.taxonomy,
                "confidence_bps": self.confidence_bps,
            },
            "who": {"owner": self.owner, "maker": self.maker},
            "where": {"geohash": self.geohash, "alt_band": self.alt_band},
            "how": {
                "width_mm": self.width_mm,
                "height_mm": self.height_mm,
                "depth_mm": self.depth_mm,
            },
            "device": self.device,
            "observed_at": self.observed_at,
            "bbox": list(self.bbox),
            "fiber_cid": self.fiber_cid,
        }

    def canonical_bytes(self) -> bytes:
        """The exact bytes that get signed (and hashed for the CID)."""
        return canonical.encode(self.to_record())

    @property
    def cid(self) -> str:
        return canonical.cid(self.to_record())

    @classmethod
    def from_record(cls, record: dict) -> "ObjectObservation":
        """Reconstruct an observation from its canonical record (inverse of to_record)."""
        if record.get("kind") != "object-observation":
            raise ValueError("not an object-observation record")
        what, who = record["what"], record["who"]
        where, how = record["where"], record["how"]
        return cls(
            label=what["label"],
            taxonomy=what["taxonomy"],
            confidence_bps=what["confidence_bps"],
            geohash=where["geohash"],
            device=record["device"],
            owner=who["owner"],
            maker=who["maker"],
            alt_band=where["alt_band"],
            width_mm=how["width_mm"],
            height_mm=how["height_mm"],
            depth_mm=how["depth_mm"],
            observed_at=record["observed_at"],
            bbox=tuple(record["bbox"]),
            fiber_cid=record["fiber_cid"],
        )


# ---------------------------------------------------------------------------
# SignedObservation — verify-before-trust envelope for the bitchat mesh
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SignedObservation:
    """An observation plus the originator key + signature that authorise it.

    :meth:`verify` is deliberately strict: the signature must be valid **and** the
    signing key must hash to the ``device`` address the observation claims. That
    binding stops a peer relabelling someone else's observation as its own device,
    or forging a ``device`` field it does not hold the key for.
    """

    observation: ObjectObservation
    pubkey: str
    signature: str

    _WIRE_KIND = "pulse-ar-observation/1"

    # -- construction ------------------------------------------------------

    @classmethod
    def sign(cls, observation: ObjectObservation, priv_hex: str, pub_hex: str) -> "SignedObservation":
        """Sign ``observation`` with the device key.

        The observation's ``device`` must equal ``address(pub_hex)`` — you can only
        sign observations attributed to your own device.
        """
        if observation.device != crypto.address(pub_hex):
            raise ValueError("observation.device must be the signer's own PLS address")
        sig = crypto.sign(priv_hex, observation.canonical_bytes())
        return cls(observation=observation, pubkey=pub_hex, signature=sig)

    # -- verify-before-trust ----------------------------------------------

    def verify(self) -> bool:
        """True iff the signature is valid *and* the signer owns the claimed device."""
        try:
            device_ok = crypto.address(self.pubkey) == self.observation.device
        except (ValueError, TypeError):
            return False
        if not device_ok:
            return False
        return crypto.verify(self.pubkey, self.observation.canonical_bytes(), self.signature)

    # -- wire form (carried as the bitchat payload) -----------------------

    def to_wire(self) -> bytes:
        """Canonical bytes for the whole envelope — the bitchat mesh payload."""
        return canonical.encode({
            "kind": self._WIRE_KIND,
            "obs": self.observation.to_record(),
            "pub": self.pubkey,
            "sig": self.signature,
        })

    @classmethod
    def from_wire(cls, data: bytes) -> "SignedObservation":
        """Decode an envelope received over the mesh (does *not* verify — caller must)."""
        env = canonical.decode(data)
        if not isinstance(env, dict) or env.get("kind") != cls._WIRE_KIND:
            raise ValueError("not a Pulse AR observation envelope")
        return cls(
            observation=ObjectObservation.from_record(env["obs"]),
            pubkey=env["pub"],
            signature=env["sig"],
        )
