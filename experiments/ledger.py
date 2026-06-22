"""Experiment ledger — a thread-safe SQLite record of every proof run.

Adapted from numerai-signals' stealth_annual_reports/ledger.py (resumable,
WAL-journaled audit ledger). Here it records, for each phase proof:

    phase, git_sha, fixture_hash, status, detail, duration_ms, timestamp

This is the backbone of the proofs-first culture: every phase closes with a
runnable proof whose outcome is written here (and, when available, mirrored to
MLflow). The ledger has no heavy dependencies so it works in the minimal core
environment; MLflow logging is best-effort and optional.
"""

from __future__ import annotations

import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_DEFAULT_DB = Path(__file__).resolve().parent / "knitweb_experiments.sqlite"
_LOCK = threading.Lock()


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


@contextmanager
def _connect(db_path: Path):
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(db_path: Path | str = _DEFAULT_DB) -> None:
    """Create the ledger table if it does not exist."""
    db_path = Path(db_path)
    with _LOCK, _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS experiment_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                phase TEXT NOT NULL,
                git_sha TEXT NOT NULL,
                fixture_hash TEXT,
                status TEXT NOT NULL,
                detail TEXT,
                duration_ms INTEGER
            )
            """
        )


def record(
    phase: str,
    status: str,
    detail: str = "",
    fixture_hash: str = "",
    duration_ms: int = 0,
    db_path: Path | str = _DEFAULT_DB,
    mlflow_experiment: str | None = "knitweb",
) -> int:
    """Append one proof-run row; returns its row id. Mirrors to MLflow if present."""
    db_path = Path(db_path)
    init(db_path)
    ts = time.time()
    sha = _git_sha()
    with _LOCK, _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO experiment_runs
                (ts, phase, git_sha, fixture_hash, status, detail, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (ts, phase, sha, fixture_hash, status, detail, int(duration_ms)),
        )
        row_id = cur.lastrowid

    # Best-effort MLflow mirror — never fail the run if MLflow is unavailable.
    if mlflow_experiment:
        try:
            import mlflow  # type: ignore

            mlflow.set_experiment(mlflow_experiment)
            with mlflow.start_run(run_name=f"{phase}:{sha}"):
                mlflow.log_params({"phase": phase, "git_sha": sha})
                mlflow.log_metric("duration_ms", int(duration_ms))
                mlflow.set_tags({"status": status, "fixture_hash": fixture_hash})
        except Exception:
            pass

    return int(row_id)


def history(db_path: Path | str = _DEFAULT_DB) -> list[tuple]:
    """Return all recorded runs (most recent first)."""
    db_path = Path(db_path)
    init(db_path)
    with _LOCK, _connect(db_path) as conn:
        return conn.execute(
            "SELECT id, ts, phase, git_sha, status, detail, duration_ms "
            "FROM experiment_runs ORDER BY id DESC"
        ).fetchall()
