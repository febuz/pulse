"""Compute guardrail — bound heavy/GPU jobs so one box is never oversubscribed.

The box running a spider is shared (display, browser, other agents, the ledger),
so a runaway or concurrent heavy job starves everything. This is the single
chokepoint ``CLAUDE.md``'s *compute guardrail* refers to: take a slot before a
heavy/GPU job, release it after. ``max_concurrent`` defaults to 1 — one heavy job
at a time — which is the safe default on a shared host.

Pure stdlib (a bounded semaphore); **no GPU/driver dependencies** — the actual
compute (wgpu/Julia/etc.) runs in a worker process, off this and off the
settlement path. This module only decides *whether a slot is free*.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

__all__ = ["GpuScheduler", "SchedulerBusy"]


class SchedulerBusy(RuntimeError):
    """Raised when no compute slot is free and the caller asked not to block."""


class GpuScheduler:
    """A bounded gate over heavy/GPU jobs (default: a single concurrent job)."""

    def __init__(self, max_concurrent: int = 1) -> None:
        if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int):
            raise TypeError("max_concurrent must be a non-bool int")
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self.max_concurrent = max_concurrent
        self._sem = threading.BoundedSemaphore(max_concurrent)
        self._lock = threading.Lock()
        self._active = 0

    @property
    def active(self) -> int:
        """Number of slots currently held."""
        return self._active

    @contextmanager
    def slot(self, block: bool = True, timeout: "float | None" = None):
        """Hold a compute slot for the duration of the ``with`` block.

        With ``block=False`` (or a ``timeout`` that elapses) and no free slot,
        raises :class:`SchedulerBusy` instead of running — so a caller can shed load
        rather than oversubscribe the box.
        """
        if not self._sem.acquire(blocking=block, timeout=timeout):
            raise SchedulerBusy(f"all {self.max_concurrent} compute slot(s) busy")
        with self._lock:
            self._active += 1
        try:
            yield self
        finally:
            with self._lock:
                self._active -= 1
            self._sem.release()
