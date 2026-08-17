"""Regression database (SQLite).

Holds regression history, per-run results, failure signatures and the
reproducibility record for every run. This is the substrate the
regression-intelligence layer (clustering, seed effectiveness, trends) is built
on, so the schema is normalised from the start rather than being a log dump.

SQLite now, by design: a single file that a laptop and a CI runner can both
open, with a schema that maps cleanly onto Postgres when a farm needs it.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_info (
    version     INTEGER NOT NULL,
    created_utc TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS regression (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    project      TEXT    NOT NULL,
    tier         TEXT    NOT NULL,
    started_utc  TEXT    NOT NULL,
    finished_utc TEXT,
    git_commit   TEXT,
    git_branch   TEXT,
    git_dirty    INTEGER,
    backend      TEXT,
    backend_version TEXT,
    frontend_version TEXT,
    uvm_version  TEXT,
    host         TEXT,
    total        INTEGER DEFAULT 0,
    passed       INTEGER DEFAULT 0,
    failed       INTEGER DEFAULT 0,
    not_verified INTEGER DEFAULT 0,
    blocked      INTEGER DEFAULT 0,
    status       TEXT    DEFAULT 'RUNNING'
);

CREATE TABLE IF NOT EXISTS run (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    regression_id  INTEGER NOT NULL REFERENCES regression(id) ON DELETE CASCADE,
    test           TEXT    NOT NULL,
    uvm_testname   TEXT,
    tier           TEXT,
    seed           INTEGER NOT NULL,
    status         TEXT    NOT NULL,
    returncode     INTEGER,
    duration_s     REAL,
    timed_out      INTEGER DEFAULT 0,
    failure_signature TEXT,
    reasons        TEXT,
    counters       TEXT,
    log_path       TEXT,
    wave_path      TEXT,
    coverage_path  TEXT,
    repro_path     TEXT,
    started_utc    TEXT
);

CREATE INDEX IF NOT EXISTS idx_run_regression ON run(regression_id);
CREATE INDEX IF NOT EXISTS idx_run_status     ON run(status);
CREATE INDEX IF NOT EXISTS idx_run_signature  ON run(failure_signature);
CREATE INDEX IF NOT EXISTS idx_run_test_seed  ON run(test, seed);

CREATE TABLE IF NOT EXISTS failure_cluster (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    signature     TEXT UNIQUE NOT NULL,
    first_seen_utc TEXT NOT NULL,
    last_seen_utc  TEXT NOT NULL,
    occurrences   INTEGER DEFAULT 0,
    example_run   INTEGER REFERENCES run(id),
    triage_state  TEXT DEFAULT 'NEW',
    notes         TEXT
);
"""


@dataclass
class RegressionDB:
    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(_SCHEMA)
            row = con.execute("SELECT version FROM schema_info").fetchone()
            if row is None:
                con.execute(
                    "INSERT INTO schema_info(version, created_utc) VALUES (?, ?)",
                    (SCHEMA_VERSION, _utc()),
                )
            elif row[0] != SCHEMA_VERSION:
                raise RuntimeError(
                    f"regression DB at {self.path} is schema v{row[0]}, "
                    f"this build expects v{SCHEMA_VERSION}"
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(str(self.path), timeout=60.0)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    # -- writes -----------------------------------------------------------
    def start_regression(self, **fields: Any) -> int:
        cols = [
            "name", "project", "tier", "started_utc", "git_commit", "git_branch",
            "git_dirty", "backend", "backend_version", "frontend_version",
            "uvm_version", "host",
        ]
        fields.setdefault("started_utc", _utc())
        values = [fields.get(c) for c in cols]
        with self.connect() as con:
            cur = con.execute(
                f"INSERT INTO regression ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                values,
            )
            return int(cur.lastrowid)

    def record_run(self, regression_id: int, result: Any, *, test: str,
                   uvm_testname: str | None, tier: str,
                   repro_path: Path | None = None) -> int:
        with self.connect() as con:
            cur = con.execute(
                """INSERT INTO run (regression_id, test, uvm_testname, tier, seed,
                       status, returncode, duration_s, timed_out, failure_signature,
                       reasons, counters, log_path, wave_path, coverage_path,
                       repro_path, started_utc)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    regression_id, test, uvm_testname, tier, result.seed,
                    result.status.value, result.returncode, result.duration_s,
                    int(result.timed_out), result.failure_signature or None,
                    json.dumps(result.reasons), json.dumps(result.counters),
                    str(result.log_path) if result.log_path else None,
                    str(result.wave_path) if result.wave_path else None,
                    str(result.coverage_path) if result.coverage_path else None,
                    str(repro_path) if repro_path else None,
                    _utc(),
                ),
            )
            run_id = int(cur.lastrowid)

            if result.failure_signature:
                con.execute(
                    """INSERT INTO failure_cluster
                           (signature, first_seen_utc, last_seen_utc, occurrences, example_run)
                       VALUES (?,?,?,1,?)
                       ON CONFLICT(signature) DO UPDATE SET
                           occurrences = occurrences + 1,
                           last_seen_utc = excluded.last_seen_utc""",
                    (result.failure_signature, _utc(), _utc(), run_id),
                )
            return run_id

    def finish_regression(self, regression_id: int) -> dict:
        with self.connect() as con:
            rows = con.execute(
                "SELECT status, COUNT(*) c FROM run WHERE regression_id=? GROUP BY status",
                (regression_id,),
            ).fetchall()
            counts = {r["status"]: r["c"] for r in rows}
            total = sum(counts.values())
            passed = counts.get("PASS", 0)
            failed = counts.get("FAIL", 0)
            nv = counts.get("NOT_VERIFIED", 0)
            blocked = counts.get("BLOCKED", 0) + counts.get("ERROR", 0)
            # A regression is PASS only if every run is PASS. NOT_VERIFIED and
            # BLOCKED never count toward success.
            status = "PASS" if total and passed == total else "FAIL"
            if total and failed == 0 and (nv or blocked):
                status = "NOT_VERIFIED"
            con.execute(
                """UPDATE regression SET finished_utc=?, total=?, passed=?, failed=?,
                       not_verified=?, blocked=?, status=? WHERE id=?""",
                (_utc(), total, passed, failed, nv, blocked, status, regression_id),
            )
            return {
                "total": total, "passed": passed, "failed": failed,
                "not_verified": nv, "blocked": blocked, "status": status,
            }

    # -- reads ------------------------------------------------------------
    def regression(self, regression_id: int) -> dict | None:
        with self.connect() as con:
            r = con.execute(
                "SELECT * FROM regression WHERE id=?", (regression_id,)
            ).fetchone()
            return dict(r) if r else None

    def runs(self, regression_id: int, *, status: str | None = None) -> list[dict]:
        q = "SELECT * FROM run WHERE regression_id=?"
        args: list[Any] = [regression_id]
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY test, seed"
        with self.connect() as con:
            return [dict(r) for r in con.execute(q, args).fetchall()]

    def clusters(self, *, limit: int = 50) -> list[dict]:
        with self.connect() as con:
            return [
                dict(r)
                for r in con.execute(
                    "SELECT * FROM failure_cluster ORDER BY occurrences DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            ]

    def history(self, project: str, *, limit: int = 20) -> list[dict]:
        with self.connect() as con:
            return [
                dict(r)
                for r in con.execute(
                    "SELECT * FROM regression WHERE project=? "
                    "ORDER BY id DESC LIMIT ?",
                    (project, limit),
                ).fetchall()
            ]

    def seed_effectiveness(self, *, limit: int = 20) -> list[dict]:
        """Seeds that produced unique failure signatures — the valuable ones."""
        with self.connect() as con:
            return [
                dict(r)
                for r in con.execute(
                    """SELECT seed, COUNT(DISTINCT failure_signature) unique_failures,
                              COUNT(*) runs
                       FROM run WHERE failure_signature IS NOT NULL
                       GROUP BY seed ORDER BY unique_failures DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            ]


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
