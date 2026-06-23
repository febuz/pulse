"""Tests for fabric.subscription.in_subscription_scope (IL-100)."""
from __future__ import annotations

import pytest

from knitweb.fabric.subscription import in_subscription_scope


# ---------------------------------------------------------------------------
# None subscription — pass-everything
# ---------------------------------------------------------------------------


def test_none_subscription_always_true():
    assert in_subscription_scope({"kind": "chemistry-node"}, None) is True


def test_none_subscription_empty_record():
    assert in_subscription_scope({}, None) is True


# ---------------------------------------------------------------------------
# kind / scope / domain / namespace scalar fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["kind", "scope", "domain", "namespace"])
def test_scalar_field_match(field):
    record = {field: "chemistry"}
    assert in_subscription_scope(record, ("chemistry",)) is True


@pytest.mark.parametrize("field", ["kind", "scope", "domain", "namespace"])
def test_scalar_field_no_match(field):
    record = {field: "finance"}
    assert in_subscription_scope(record, ("chemistry",)) is False


def test_any_scalar_field_sufficient():
    record = {"kind": "unrelated", "domain": "chemistry"}
    assert in_subscription_scope(record, ("chemistry",)) is True


def test_no_overlap_returns_false():
    record = {"kind": "finance-node", "scope": "vbank", "domain": "finance", "namespace": "v1"}
    assert in_subscription_scope(record, ("chemistry",)) is False


# ---------------------------------------------------------------------------
# tags list
# ---------------------------------------------------------------------------


def test_tags_list_match():
    record = {"kind": "node", "tags": ["chemistry", "lab"]}
    assert in_subscription_scope(record, ("chemistry",)) is True


def test_tags_tuple_match():
    record = {"tags": ("niche-tag",)}
    assert in_subscription_scope(record, ("niche-tag",)) is True


def test_tags_set_match():
    record = {"tags": {"alpha", "beta"}}
    assert in_subscription_scope(record, ("beta",)) is True


def test_tags_no_match():
    record = {"tags": ["unrelated"]}
    assert in_subscription_scope(record, ("chemistry",)) is False


def test_tags_non_string_entries_skipped():
    record = {"tags": [None, 42, "chemistry"]}
    assert in_subscription_scope(record, ("chemistry",)) is True


# ---------------------------------------------------------------------------
# Subscription set with multiple entries
# ---------------------------------------------------------------------------


def test_multi_subscription_any_match():
    record = {"kind": "vbank-node"}
    assert in_subscription_scope(record, ("chemistry", "vbank-node", "finance")) is True


def test_multi_subscription_no_match():
    record = {"kind": "unknown"}
    assert in_subscription_scope(record, ("chemistry", "vbank-node")) is False


# ---------------------------------------------------------------------------
# Empty record
# ---------------------------------------------------------------------------


def test_empty_record_non_none_subscription():
    assert in_subscription_scope({}, ("chemistry",)) is False


# ---------------------------------------------------------------------------
# Non-string field values are ignored (no crash)
# ---------------------------------------------------------------------------


def test_non_string_kind_ignored():
    record = {"kind": 42}
    assert in_subscription_scope(record, ("42",)) is False


def test_none_field_value_ignored():
    record = {"kind": None, "scope": None}
    assert in_subscription_scope(record, ("chemistry",)) is False


# ---------------------------------------------------------------------------
# Behavioural equivalence with the old retrieve._in_scope
# ---------------------------------------------------------------------------


def _old_in_scope(record: dict, scope: tuple | None) -> bool:
    """Original private implementation from retrieve.py — kept for reference."""
    if scope is None:
        return True
    values = {record.get("kind"), record.get("scope"), record.get("domain"), record.get("namespace")}
    if any(v in scope for v in values if isinstance(v, str)):
        return True
    tags = record.get("tags")
    if isinstance(tags, (list, tuple, set)):
        if any(str(tag) in scope for tag in tags):
            return True
    return False


_EQUIVALENCE_CASES = [
    ({"kind": "chemistry-node"}, ("chemistry-node",)),
    ({"kind": "finance"}, ("chemistry-node",)),
    ({"tags": ["a", "b"]}, ("b",)),
    ({"domain": "x"}, ("x", "y")),
    ({}, ("anything",)),
    ({"kind": "k"}, None),
    ({"tags": [1, None, "ok"]}, ("ok",)),
]


@pytest.mark.parametrize("record,scope", _EQUIVALENCE_CASES)
def test_equivalence_with_old_impl(record, scope):
    assert in_subscription_scope(record, scope) == _old_in_scope(record, scope)
