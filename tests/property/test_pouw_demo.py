"""Acceptance proof: the end-to-end PoUW demo runs and settles all three outcomes correctly.

Ties select → verify → quorum → settle into one runnable flow: honest→released, fraud→slashed,
declared→refunded. This is the capstone integration test for the whole PoUW subsystem.
"""

import sys
from pathlib import Path

# the demo lives in examples/, not the package
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "examples"))

import pouw_demo  # noqa: E402


def test_demo_runs_all_three_settlement_paths():
    r = pouw_demo.run_demo()

    # 1. Honest worker: committee confirms, escrow releases (worker paid).
    h = r["honest"]
    assert h["all_confirm"] is True
    assert h["released"] is True
    assert h["status"] == "released"
    assert len(h["committee"]) == 5 and pouw_demo.WORKER not in h["committee"]

    # 2. Cheating worker: committee detects, collateral slashed, escrow refunded.
    f = r["fraud"]
    assert f["all_mismatch"] is True
    assert f["slashed"] is True
    assert f["status"] == "slashed"
    assert f["collateral_slashed"] == pouw_demo.COLLATERAL
    assert f["escrow_refunded"] == pouw_demo.ESCROW

    # 3. Declared fault: refunded with NO slash; stake returned, consumer made whole.
    d = r["declared"]
    assert d["refunded"] is True
    assert d["status"] == "refunded"
    assert d["collateral_slashed"] == 0
    assert d["collateral_returned"] == pouw_demo.COLLATERAL
    assert d["escrow_refunded"] == pouw_demo.ESCROW


def test_fraud_never_pays_the_worker():
    # The core economic claim across the demo: a cheating worker loses its stake and is paid
    # nothing; an honest one is paid; an honest self-report loses nothing.
    r = pouw_demo.run_demo()
    assert r["fraud"]["collateral_slashed"] == pouw_demo.COLLATERAL   # fraud burns the stake
    assert r["declared"]["collateral_slashed"] == 0                   # honesty is never slashed
    assert r["honest"]["status"] == "released"                        # good work is paid


def test_main_smoke(capsys):
    pouw_demo.main()
    out = capsys.readouterr().out
    assert "RELEASED" in out and "SLASHED" in out and "REFUNDED" in out
