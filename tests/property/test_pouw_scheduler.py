"""Proofs for the compute guardrail: a bounded gate over heavy/GPU jobs."""

import pytest

from knitweb.pouw.scheduler import GpuScheduler, SchedulerBusy


@pytest.mark.property
def test_single_slot_serializes_and_tracks_active():
    sched = GpuScheduler(max_concurrent=1)
    assert sched.active == 0
    with sched.slot():
        assert sched.active == 1
        # no free slot -> a non-blocking acquire sheds load instead of oversubscribing
        with pytest.raises(SchedulerBusy):
            with sched.slot(block=False):
                pass
    assert sched.active == 0
    # slot is free again after the block exits
    with sched.slot(block=False):
        assert sched.active == 1


@pytest.mark.property
def test_capacity_bounds_concurrency():
    sched = GpuScheduler(max_concurrent=2)
    with sched.slot(), sched.slot():
        assert sched.active == 2
        with pytest.raises(SchedulerBusy):
            with sched.slot(block=False):
                pass
    assert sched.active == 0


@pytest.mark.property
def test_invalid_capacity_rejected():
    for bad in (0, -1, 1.5, True):
        with pytest.raises((ValueError, TypeError)):
            GpuScheduler(max_concurrent=bad)  # type: ignore[arg-type]
