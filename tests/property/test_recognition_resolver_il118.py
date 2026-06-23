"""IL-118 — Pluggable recognition resolver → content-address.

Tests for all three ACs:

AC1 — recognize(input) -> RecognitionResult with resolver_key + confidence;
      three backends: marker / scene_semantic / embedding
AC2 — resolver_key resolves to a CID or None; no recognition code in core/ledger
AC3 — probabilistic backends surface confidence < 1.0 and require_confirmation
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

from knitweb.edge.recognize import (
    CONFIDENCE_EXACT,
    CONFIDENCE_NONE,
    EmbeddingBackend,
    MarkerBackend,
    RecognitionBackend,
    RecognitionResult,
    SceneSemanticBackend,
    recognize,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_CID_A = "bafyreiabc123"
_FAKE_CID_B = "bafyreixyz789"


def _marker_backend() -> MarkerBackend:
    return MarkerBackend({"QR-001": _FAKE_CID_A, "QR-002": _FAKE_CID_B})


def _scene_backend() -> SceneSemanticBackend:
    return SceneSemanticBackend({
        "leaching_pot": (_FAKE_CID_A, 0.85),
        "furnace": (_FAKE_CID_B, 0.70),
    })


def _embedding_backend() -> EmbeddingBackend:
    index = [
        ([1.0, 0.0, 0.0], _FAKE_CID_A),
        ([0.0, 1.0, 0.0], _FAKE_CID_B),
    ]
    return EmbeddingBackend(index, threshold=0.5)


# ---------------------------------------------------------------------------
# AC1 — RecognitionResult shape
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_recognition_result_has_resolver_key_and_confidence():
    r = RecognitionResult(resolver_key=_FAKE_CID_A, confidence=1.0, backend="marker")
    assert r.resolver_key == _FAKE_CID_A
    assert r.confidence == 1.0
    assert r.backend == "marker"


@pytest.mark.property
def test_recognition_result_none_resolver_key():
    r = RecognitionResult(resolver_key=None, confidence=0.0, backend="marker")
    assert r.resolver_key is None
    assert not r.resolved


@pytest.mark.property
def test_recognition_result_confidence_bounds_enforced():
    with pytest.raises(ValueError):
        RecognitionResult(resolver_key=None, confidence=1.5, backend="x")
    with pytest.raises(ValueError):
        RecognitionResult(resolver_key=None, confidence=-0.1, backend="x")


@pytest.mark.property
def test_recognition_result_empty_string_resolver_key_raises():
    with pytest.raises(ValueError):
        RecognitionResult(resolver_key="", confidence=1.0, backend="x")


@pytest.mark.property
def test_recognition_result_is_frozen():
    r = RecognitionResult(resolver_key=_FAKE_CID_A, confidence=0.9, backend="emb")
    with pytest.raises(Exception):
        r.confidence = 1.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AC1 — MarkerBackend (exact, deterministic)
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_marker_backend_name():
    assert _marker_backend().name == "marker"


@pytest.mark.property
def test_marker_backend_known_marker_returns_cid():
    result = _marker_backend().recognize("QR-001")
    assert result.resolver_key == _FAKE_CID_A
    assert result.confidence == CONFIDENCE_EXACT
    assert result.resolved


@pytest.mark.property
def test_marker_backend_unknown_marker_returns_none():
    result = _marker_backend().recognize("QR-UNKNOWN")
    assert result.resolver_key is None
    assert result.confidence == CONFIDENCE_NONE
    assert not result.resolved


@pytest.mark.property
def test_marker_backend_is_deterministic():
    b = _marker_backend()
    assert b.recognize("QR-001") == b.recognize("QR-001")


@pytest.mark.property
def test_marker_backend_wrong_type_raises():
    with pytest.raises(TypeError):
        _marker_backend().recognize(12345)


@pytest.mark.property
def test_marker_backend_register_adds_entry():
    b = MarkerBackend({})
    b.register("NEW-QR", _FAKE_CID_A)
    assert b.recognize("NEW-QR").resolver_key == _FAKE_CID_A


# ---------------------------------------------------------------------------
# AC1 — SceneSemanticBackend
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_scene_semantic_backend_name():
    assert _scene_backend().name == "scene_semantic"


@pytest.mark.property
def test_scene_semantic_known_label_returns_cid():
    result = _scene_backend().recognize("leaching_pot")
    assert result.resolver_key == _FAKE_CID_A
    assert result.confidence == pytest.approx(0.85)


@pytest.mark.property
def test_scene_semantic_unknown_label_returns_none():
    result = _scene_backend().recognize("unknown_object")
    assert result.resolver_key is None
    assert result.confidence == CONFIDENCE_NONE


@pytest.mark.property
def test_scene_semantic_wrong_type_raises():
    with pytest.raises(TypeError):
        _scene_backend().recognize(["a", "b"])


@pytest.mark.property
def test_scene_semantic_confidence_out_of_range_raises():
    with pytest.raises((ValueError, Exception)):
        SceneSemanticBackend({"x": (_FAKE_CID_A, 1.5)})


# ---------------------------------------------------------------------------
# AC1 — EmbeddingBackend (nearest-neighbour)
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_embedding_backend_name():
    assert _embedding_backend().name == "embedding"


@pytest.mark.property
def test_embedding_backend_exact_match_resolves():
    result = _embedding_backend().recognize([1.0, 0.0, 0.0])
    assert result.resolver_key == _FAKE_CID_A
    assert result.confidence > 0.5


@pytest.mark.property
def test_embedding_backend_below_threshold_returns_none():
    result = _embedding_backend().recognize([0.0, 0.0, 1.0])
    assert result.resolver_key is None


@pytest.mark.property
def test_embedding_backend_wrong_type_raises():
    with pytest.raises(TypeError):
        _embedding_backend().recognize("not an embedding")


@pytest.mark.property
def test_embedding_backend_dimension_mismatch_raises():
    with pytest.raises(ValueError):
        _embedding_backend().recognize([1.0, 0.0])  # wrong dim


# ---------------------------------------------------------------------------
# AC2 — resolver_key is a CID or None; no recognition in core/ or ledger/
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_recognize_top_level_dispatches_to_backend():
    backend = _marker_backend()
    result = recognize("QR-001", backend)
    assert result.resolver_key == _FAKE_CID_A


@pytest.mark.property
def test_recognize_rejects_non_backend():
    with pytest.raises(TypeError):
        recognize("data", object())  # type: ignore[arg-type]


@pytest.mark.property
def test_all_backends_are_subclasses_of_recognition_backend():
    for cls in (MarkerBackend, SceneSemanticBackend, EmbeddingBackend):
        assert issubclass(cls, RecognitionBackend)


@pytest.mark.property
def test_no_recognition_in_core_package():
    """recognize() must not live in core/ — it is a client-side adapter."""
    import knitweb.core as core_pkg
    core_path = core_pkg.__path__
    for finder, name, _ in pkgutil.walk_packages(core_path, prefix="knitweb.core."):
        mod = importlib.import_module(name)
        assert not hasattr(mod, "recognize"), (
            f"recognition interface found in core module {name} — must not be there"
        )


@pytest.mark.property
def test_no_recognition_in_ledger_package():
    """recognize() must not live in ledger/ — it is a client-side adapter."""
    import knitweb.ledger as ledger_pkg
    ledger_path = ledger_pkg.__path__
    for finder, name, _ in pkgutil.walk_packages(ledger_path, prefix="knitweb.ledger."):
        mod = importlib.import_module(name)
        assert not hasattr(mod, "recognize"), (
            f"recognition interface found in ledger module {name} — must not be there"
        )


# ---------------------------------------------------------------------------
# AC3 — probabilistic backends surface confidence and require confirmation
# ---------------------------------------------------------------------------

@pytest.mark.property
def test_marker_exact_match_does_not_require_confirmation():
    result = _marker_backend().recognize("QR-001")
    assert not result.requires_confirmation


@pytest.mark.property
def test_scene_semantic_requires_confirmation_when_confidence_below_1():
    result = _scene_backend().recognize("leaching_pot")
    assert result.confidence < CONFIDENCE_EXACT
    assert result.requires_confirmation


@pytest.mark.property
def test_embedding_resolved_always_requires_confirmation():
    result = _embedding_backend().recognize([1.0, 0.0, 0.0])
    assert result.resolved
    assert result.confidence < CONFIDENCE_EXACT
    assert result.requires_confirmation


@pytest.mark.property
def test_unresolved_result_does_not_require_confirmation():
    result = _embedding_backend().recognize([0.0, 0.0, 1.0])
    assert not result.resolved
    assert not result.requires_confirmation


@pytest.mark.property
def test_embedding_confidence_capped_below_1():
    """Embedding backend must never emit confidence == 1.0 (it is probabilistic)."""
    result = _embedding_backend().recognize([1.0, 0.0, 0.0])
    assert result.confidence < CONFIDENCE_EXACT
