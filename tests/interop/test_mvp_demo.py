"""The MVP acceptance test: the end-to-end demo must run green.

Runs examples/mvp_demo.py as a subprocess (it is self-asserting) and confirms the
full loop — genesis → p2p payment → PoUW bounded mint → persistence → checkpoint —
completes. A regression here means the integrated MVP is broken.
"""

import os
import pathlib
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.mark.interop
def test_mvp_demo_runs_end_to_end():
    env = {**os.environ, "PYTHONPATH": str(_ROOT / "src")}
    result = subprocess.run(
        [sys.executable, str(_ROOT / "examples" / "mvp_demo.py")],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "MVP verified" in result.stdout
