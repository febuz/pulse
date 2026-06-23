"""Pluggable recognition resolver — maps physical inputs to Web node CIDs.

Three backends:
  * ``marker``        — exact deterministic match (QR code, ArUco, datamatrix)
  * ``scene_semantic``— label-to-class mapping (object detection labels → CID)
  * ``embedding``     — nearest-neighbour in a prebuilt embedding index

All backends return a :class:`RecognitionResult` with a ``resolver_key`` (a CID
or ``None``) and a ``confidence`` float in ``[0.0, 1.0]``.  Only the ``marker``
backend is exact (confidence always 1.0 or 0.0); the probabilistic backends
MUST surface confidence so the caller can gate durable binding on a user/agent
confirmation step.

Invariant: no recognition code touches ``core/`` or ``ledger/`` — recognition is
a client-side / edge-side adapter.  The durable binding (anchor → knit-id) is a
separate settlement-class operation handled upstream.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

__all__ = [
    "RecognitionResult",
    "RecognitionBackend",
    "MarkerBackend",
    "SceneSemanticBackend",
    "EmbeddingBackend",
    "recognize",
    "CONFIDENCE_EXACT",
    "CONFIDENCE_NONE",
]

CONFIDENCE_EXACT: float = 1.0
CONFIDENCE_NONE: float = 0.0


@dataclass(frozen=True)
class RecognitionResult:
    """Output of a recognition attempt.

    ``resolver_key`` is a Web node CID when recognition succeeded, or ``None``
    when no match was found.  ``confidence`` is in ``[0.0, 1.0]``; exact
    backends always emit 1.0 or 0.0; probabilistic backends may emit any value.
    Callers MUST NOT make a durable binding when ``confidence < 1.0`` without a
    user/agent confirmation step.
    """

    resolver_key: Optional[str]
    confidence: float
    backend: str

    def __post_init__(self) -> None:
        if not isinstance(self.confidence, (int, float)):
            raise TypeError("confidence must be a float")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence!r}")
        if self.resolver_key is not None and not isinstance(self.resolver_key, str):
            raise TypeError("resolver_key must be a str CID or None")
        if not self.resolver_key and self.resolver_key is not None:
            raise ValueError("resolver_key must be a non-empty str or None")

    @property
    def resolved(self) -> bool:
        return self.resolver_key is not None

    @property
    def requires_confirmation(self) -> bool:
        """True when the caller must get user/agent confirmation before durable binding."""
        return self.resolved and self.confidence < CONFIDENCE_EXACT


class RecognitionBackend(ABC):
    """Abstract base for pluggable recognition backends."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def recognize(self, input_data: object) -> RecognitionResult: ...


class MarkerBackend(RecognitionBackend):
    """Exact, deterministic backend for physical markers (QR, ArUco, datamatrix).

    Looks up the marker payload in a pre-registered ``{marker_id: cid}`` table.
    Confidence is always 1.0 (found) or 0.0 (not found) — no probabilistic step.
    """

    def __init__(self, registry: dict[str, str]) -> None:
        if not isinstance(registry, dict):
            raise TypeError("registry must be a dict[str, str]")
        for k, v in registry.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise TypeError("registry keys and values must be str")
        self._registry: dict[str, str] = dict(registry)

    @property
    def name(self) -> str:
        return "marker"

    def recognize(self, input_data: object) -> RecognitionResult:
        """``input_data`` is the decoded marker string (e.g. the QR payload)."""
        if not isinstance(input_data, str):
            raise TypeError("MarkerBackend expects a str marker payload")
        cid = self._registry.get(input_data)
        return RecognitionResult(
            resolver_key=cid,
            confidence=CONFIDENCE_EXACT if cid is not None else CONFIDENCE_NONE,
            backend=self.name,
        )

    def register(self, marker_id: str, cid: str) -> None:
        if not isinstance(marker_id, str) or not isinstance(cid, str):
            raise TypeError("marker_id and cid must be str")
        self._registry[marker_id] = cid


class SceneSemanticBackend(RecognitionBackend):
    """Label-to-CID mapping for scene-semantic / object-detection outputs.

    Maps a label string (e.g. ``"leaching_pot"``) to a CID using a pre-built
    ``{label: (cid, confidence)}`` table.  Confidence is set by the caller when
    building the table — it reflects the classifier's class-level reliability,
    not per-image score.
    """

    def __init__(self, label_map: dict[str, tuple[str, float]]) -> None:
        for label, (cid, conf) in label_map.items():
            if not isinstance(label, str):
                raise TypeError("labels must be str")
            if not isinstance(cid, str) or not cid:
                raise TypeError("CIDs must be non-empty str")
            if not (0.0 <= conf <= 1.0):
                raise ValueError(f"confidence for {label!r} must be in [0.0, 1.0]")
        self._map: dict[str, tuple[str, float]] = dict(label_map)

    @property
    def name(self) -> str:
        return "scene_semantic"

    def recognize(self, input_data: object) -> RecognitionResult:
        """``input_data`` is a label string from an object detector."""
        if not isinstance(input_data, str):
            raise TypeError("SceneSemanticBackend expects a str label")
        match = self._map.get(input_data)
        if match is None:
            return RecognitionResult(
                resolver_key=None, confidence=CONFIDENCE_NONE, backend=self.name
            )
        cid, conf = match
        return RecognitionResult(resolver_key=cid, confidence=conf, backend=self.name)


class EmbeddingBackend(RecognitionBackend):
    """Nearest-neighbour embedding backend (probabilistic).

    Accepts a query embedding vector and searches a pre-indexed set of
    ``(embedding, cid)`` pairs using cosine similarity.  Always emits a
    ``confidence < 1.0`` so the caller knows confirmation is required before
    any durable binding.

    For testing/stub purposes the index is a plain list; production integrations
    should subclass and override ``_cosine_similarity`` with a vectorised impl.
    """

    def __init__(
        self,
        index: list[tuple[list[float], str]],
        *,
        threshold: float = 0.5,
    ) -> None:
        if not (0.0 <= threshold < 1.0):
            raise ValueError("threshold must be in [0.0, 1.0)")
        self._index = list(index)
        self.threshold = threshold

    @property
    def name(self) -> str:
        return "embedding"

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            raise ValueError("embedding dimension mismatch")
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)

    def recognize(self, input_data: object) -> RecognitionResult:
        """``input_data`` is a list[float] query embedding."""
        if not isinstance(input_data, (list, tuple)):
            raise TypeError("EmbeddingBackend expects a list[float] embedding")
        query = list(input_data)
        best_cid: Optional[str] = None
        best_score = -1.0
        for embedding, cid in self._index:
            score = self._cosine_similarity(query, embedding)
            if score > best_score:
                best_score = score
                best_cid = cid
        if best_score < self.threshold:
            return RecognitionResult(
                resolver_key=None, confidence=best_score, backend=self.name
            )
        # Cap at just below 1.0 — embedding is probabilistic by nature.
        confidence = min(best_score, 0.999)
        return RecognitionResult(
            resolver_key=best_cid, confidence=confidence, backend=self.name
        )


def recognize(input_data: object, backend: RecognitionBackend) -> RecognitionResult:
    """Top-level entry point: dispatch to the given backend and return its result.

    The caller is responsible for:
      * choosing the backend appropriate to the input type,
      * checking ``result.requires_confirmation`` before any durable binding.

    No recognition code reaches ``core/`` or ``ledger/``.
    """
    if not isinstance(backend, RecognitionBackend):
        raise TypeError("backend must be a RecognitionBackend")
    return backend.recognize(input_data)
