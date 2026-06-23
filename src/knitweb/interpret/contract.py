"""Stage-tagging contract for the Interpretation Lobe pipeline.

Every record that crosses a stage boundary is stamped once with ``_stage``.
The stamp is IMMUTABLE: re-tagging a record that already has ``_stage`` raises
:class:`ImmutableStageError`, preventing accidental double-processing.
"""

from __future__ import annotations

__all__ = ["STAGES", "ImmutableStageError", "tag_stage"]

STAGES: frozenset[str] = frozenset({
    "RETRIEVE",
    "DISTILL",
    "GATE",
    "COMPILE",
    "SERVE",
    "SETTLEMENT",
    "MINING",
    "FEEDBACK",
})


class ImmutableStageError(ValueError):
    """Raised when attempting to re-tag a record that already has ``_stage``."""


def tag_stage(record: dict, stage: str) -> dict:
    """Return a shallow copy of ``record`` with ``_stage`` set to ``stage``.

    Raises :class:`ValueError` for an unknown stage and
    :class:`ImmutableStageError` if ``_stage`` is already present.
    """
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; valid stages: {sorted(STAGES)}")
    if "_stage" in record:
        raise ImmutableStageError(
            f"record already tagged as {record['_stage']!r}; cannot re-tag as {stage!r}"
        )
    return {**record, "_stage": stage}
