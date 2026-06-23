"""Fabric-wide subscription-scope filter (IL-100).

Any fabric module that needs to check whether a record falls within a
node operator's subscribed knitweb scope should use
:func:`in_subscription_scope` from here instead of re-implementing the
convention locally.

Background
----------
The *subscription scope* governs which settlement-class records a node
operator replicates (IL-115).  A record is *in scope* when any of its
canonical scope fields (``kind``, ``scope``, ``domain``, ``namespace``)
or its ``tags`` list overlaps with the operator's subscribed set.  A
``None`` scope means "subscribe to everything" (no filter applied).

The function was originally a private helper ``_in_scope`` inside
``interpret/retrieve.py``.  Promoting it here lets the settlement layer
(#IL-115), the working-memory backend (#IL-116), and any future modules
reuse the same rule without drift.
"""

from __future__ import annotations

__all__ = ["in_subscription_scope"]

# Keys whose string values are checked against the subscription set.
_SCOPE_FIELDS: tuple[str, ...] = ("kind", "scope", "domain", "namespace")


def in_subscription_scope(
    record: dict,
    subscription: tuple[str, ...] | None,
) -> bool:
    """Return ``True`` when *record* falls within *subscription*.

    Parameters
    ----------
    record:
        Any fabric record dict (node, relation, or boundary record).
    subscription:
        A tuple of scope strings (``kind`` / ``scope`` / ``domain`` /
        ``namespace`` values, or ``tags`` entries).  ``None`` means
        *subscribe to everything*: the function always returns ``True``.

    Returns
    -------
    bool
        ``True`` when the record is within the subscription scope;
        ``False`` when it is definitively out-of-scope.

    Examples
    --------
    >>> in_subscription_scope({"kind": "chemistry-node"}, ("chemistry-node",))
    True
    >>> in_subscription_scope({"kind": "finance-node"}, ("chemistry-node",))
    False
    >>> in_subscription_scope({"kind": "any"}, None)   # unfiltered
    True
    >>> in_subscription_scope({"tags": ["chemistry", "lab"]}, ("chemistry",))
    True
    """
    if subscription is None:
        return True

    # Fast path: check scalar scope fields first (single dict lookup each).
    for field in _SCOPE_FIELDS:
        value = record.get(field)
        if isinstance(value, str) and value in subscription:
            return True

    # Slow path: check tags list.
    tags = record.get("tags")
    if isinstance(tags, (list, tuple, set)):
        subscription_set = set(subscription)
        for tag in tags:
            if isinstance(tag, str) and tag in subscription_set:
                return True

    return False
