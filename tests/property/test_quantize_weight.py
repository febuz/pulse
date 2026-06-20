"""Tests for deterministic distillation relation weighting."""

import pytest

from knitweb.interpret.quantize import quantize_weight


def test_quantize_weight_is_deterministic_and_bounded():
    first = quantize_weight(reputation=11, recency=0.4, pouw_score=3.2)
    second = quantize_weight(reputation=11, recency=0.4, pouw_score=3.2)

    assert first == second
    assert 0 <= first <= 255
    assert second == first


def test_quantize_weight_rejects_invalid_inputs():
    with pytest.raises(TypeError, match="reputation must be an int"):
        quantize_weight(reputation=True, recency=1, pouw_score=1)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="recency must be a number"):
        quantize_weight(reputation=1, recency="1", pouw_score=1)  # type: ignore[arg-type]


def test_quantize_weight_bounds_can_be_lowered():
    small = quantize_weight(reputation=100, recency=1, pouw_score=100, max_weight=16)
    assert small == 16
