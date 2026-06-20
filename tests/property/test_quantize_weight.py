"""Tests for deterministic distillation relation weighting."""

import ast
import inspect

import pytest

from knitweb.interpret import quantize
from knitweb.interpret.quantize import quantize_weight


def _recompute(reputation, recency, pouw_score, max_weight=255):
    """Independent integer-only recomputation of the shipped derivation.

    Mirrors ``(6000*rep + 600*recency_milli + 7*pouw_milli) // 10000`` using only
    integer arithmetic, so a test asserting ``quantize_weight == _recompute`` fails
    the moment the implementation reintroduces a float intermediate that perturbs a
    boundary value.
    """
    rep = max(0, reputation)
    recency_milli = int(recency * 1000)
    if recency_milli < 0:
        recency_milli = 0
    if recency_milli > 1000:
        recency_milli = 1000
    pouw_milli = int(pouw_score * 1000)
    if pouw_milli < 0:
        pouw_milli = 0
    blended = (6000 * rep + 600 * recency_milli + 7 * pouw_milli) // 10000
    if blended < 0:
        return 0
    if blended > max_weight:
        return max_weight
    return blended


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


# --- #133: integer-only derivation pinning -------------------------------------

# Representative (reputation, recency, pouw_score) -> deterministic integer weight.
# These pin the float-free derivation; each value is the exact integer-rational
# floor of ``0.6*rep + 60*recency + 0.7*pouw``.
PINNED_CASES = [
    ((11, 0.4, 3.2), 32),
    ((100, 1, 100), 190),
    ((0, 0, 0), 0),
    ((50, 0.5, 50), 95),
    ((5, 0.999, 1.5), 63),
    ((255, 1.0, 255.0), 255),  # clamped to default max_weight
]


@pytest.mark.parametrize("inputs,expected", PINNED_CASES)
def test_quantize_weight_pins_integer_outputs(inputs, expected):
    rep, recency, pouw = inputs
    assert quantize_weight(reputation=rep, recency=recency, pouw_score=pouw) == expected


@pytest.mark.parametrize("inputs,expected", PINNED_CASES)
def test_quantize_weight_matches_independent_integer_recomputation(inputs, expected):
    rep, recency, pouw = inputs
    result = quantize_weight(reputation=rep, recency=recency, pouw_score=pouw)
    assert result == _recompute(rep, recency, pouw)
    assert isinstance(result, int)


def test_quantize_weight_result_is_int_not_bool():
    result = quantize_weight(reputation=11, recency=0.4, pouw_score=3.2)
    assert type(result) is int
    assert not isinstance(result, bool)


def test_quantize_weight_value_path_is_float_free():
    """AST guard: the derivation must carry no float literal, no float() call, and
    no true division.  This is the load-bearing assertion behind #133: any float
    intermediate reintroduced on the value path trips one of these checks."""
    source = inspect.getsource(quantize.quantize_weight)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        assert not (
            isinstance(node, ast.Constant) and isinstance(node.value, float)
        ), "float literal on the quantize_weight value path"
        assert not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "float"
        ), "float() call on the quantize_weight value path"
        assert not (
            isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
        ), "true division on the quantize_weight value path"
