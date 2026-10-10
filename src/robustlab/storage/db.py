"""SQLite storage (ARCHITECTURE §5, P1_PLAN §5, P3_PLAN §3.5).

Research-result tables are append-only, enforced by triggers. Only `job` and
`opt_job` (operational state) may be updated.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 3  # 2: P2 metrics tables; 3: P3 optimization tables (optimization_round ... trial_ledger)

_APPEND_ONLY = (
    "partition_registry",
    "holdout_access_log",
    "strategy_version",
    "candidate",
    "run",
    "artifact",
    "job_artifact",
    "mt5_reported_metrics",
    "run_metrics",
    "metric_check",
    "period_performance",
    "optimization_round",
    "opt_job_artifact",
    "pass_result",
    "trial_ledger",
)

_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS partition_registry (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    partition_hash TEXT NOT NULL,
    partition_json TEXT NOT NULL,
    registered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS holdout_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_at TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    from_date TEXT NOT NULL,
    to_date TEXT NOT NULL,
    partition_hash TEXT NOT NULL,
    approved INTEGER NOT NULL,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_version (
    strategy_version_id TEXT PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    expert TEXT NOT NULL,
    ex5_sha256 TEXT NOT NULL,
    mq5_sha256 TEXT,
    source_available INTEGER NOT NULL,
    telemetry_version TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    registered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate (
    candidate_id TEXT PRIMARY KEY,
    strategy_version_id TEXT NOT NULL REFERENCES strategy_version(strategy_version_id),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    params_json TEXT NOT NULL,
    origin TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run (
    run_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidate(candidate_id),
    from_date TEXT NOT NULL,
    to_date TEXT NOT NULL,
    period_semantics TEXT NOT NULL,
    tester_settings_json TEXT NOT NULL,
    tester_settings_hash TEXT NOT NULL,
    partition_hash TEXT NOT NULL,
    cost_scenario TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job (
    job_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES run(run_id),
    spec_hash TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_class TEXT,
    error_detail TEXT,
    terminal_path TEXT NOT NULL,
    pid INTEGER,
    exit_code INTEGER,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    observed_build INTEGER,
    observed_env_json TEXT,
    deals_count INTEGER,
    deals_content_hash TEXT,
    net_profit_mt5 TEXT,
    net_profit_sum TEXT,
    warnings_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS job_run ON job(run_id);

CREATE TABLE IF NOT EXISTS artifact (
    sha256 TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    rel_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_artifact (
    job_id TEXT NOT NULL REFERENCES job(job_id),
    role TEXT NOT NULL,
    name TEXT NOT NULL,
    sha256 TEXT NOT NULL REFERENCES artifact(sha256),
    PRIMARY KEY (job_id, role, name)
);

CREATE TABLE IF NOT EXISTS run_metrics (
    job_id TEXT NOT NULL REFERENCES job(job_id),
    metric_def_version TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    unavailable_json TEXT NOT NULL,
    telemetry_version TEXT NOT NULL,
    code_version TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (job_id, metric_def_version)
);

CREATE TABLE IF NOT EXISTS metric_check (
    job_id TEXT NOT NULL REFERENCES job(job_id),
    metric_def_version TEXT NOT NULL,
    name TEXT NOT NULL,
    ours TEXT,
    mt5 TEXT,
    diff REAL,
    status TEXT NOT NULL,
    PRIMARY KEY (job_id, metric_def_version, name)
);

CREATE TABLE IF NOT EXISTS period_performance (
    job_id TEXT NOT NULL REFERENCES job(job_id),
    metric_def_version TEXT NOT NULL,
    bucket_type TEXT NOT NULL,
    bucket_key TEXT NOT NULL,
    net_profit REAL NOT NULL,
    trades INTEGER NOT NULL,
    balance_max_dd REAL NOT NULL,
    PRIMARY KEY (job_id, metric_def_version, bucket_type, bucket_key)
);

CREATE TABLE IF NOT EXISTS optimization_round (
    round_id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL,
    study_hash TEXT NOT NULL,
    strategy_version_id TEXT NOT NULL REFERENCES strategy_version(strategy_version_id),
    strategy_family TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    from_date TEXT NOT NULL,
    to_date TEXT NOT NULL,
    period_semantics TEXT NOT NULL,
    param_space_json TEXT NOT NULL,
    fixed_json TEXT NOT NULL,
    tester_settings_json TEXT NOT NULL,
    tester_settings_hash TEXT NOT NULL,
    partition_hash TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    expected_passes INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opt_job (
    job_id TEXT PRIMARY KEY,
    round_id TEXT NOT NULL REFERENCES optimization_round(round_id),
    chunk_index INTEGER NOT NULL,
    spec_hash TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_class TEXT,
    error_detail TEXT,
    terminal_path TEXT NOT NULL,
    pid INTEGER,
    exit_code INTEGER,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    observed_build INTEGER,
    expected_passes INTEGER NOT NULL,
    received_passes INTEGER,
    cache_deleted_json TEXT,
    tester_log_json TEXT,
    daily_content_hash TEXT,
    warnings_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS opt_job_round ON opt_job(round_id, chunk_index);

CREATE TABLE IF NOT EXISTS opt_job_artifact (
    job_id TEXT NOT NULL REFERENCES opt_job(job_id),
    role TEXT NOT NULL,
    name TEXT NOT NULL,
    sha256 TEXT NOT NULL REFERENCES artifact(sha256),
    PRIMARY KEY (job_id, role, name)
);

CREATE TABLE IF NOT EXISTS pass_result (
    job_id TEXT NOT NULL REFERENCES opt_job(job_id),
    pass_no INTEGER NOT NULL,
    run_id TEXT NOT NULL REFERENCES run(run_id),
    candidate_id TEXT NOT NULL REFERENCES candidate(candidate_id),
    metric_def_version TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    unavailable_json TEXT NOT NULL,
    mt5_stats_json TEXT NOT NULL,
    xml_profit TEXT NOT NULL,
    xml_trades INTEGER NOT NULL,
    daily_rows INTEGER NOT NULL,
    PRIMARY KEY (job_id, pass_no)
);
CREATE INDEX IF NOT EXISTS pass_result_run ON pass_result(run_id);

CREATE TABLE IF NOT EXISTS trial_ledger (
    round_id TEXT NOT NULL REFERENCES optimization_round(round_id),
    chunk_index INTEGER NOT NULL,
    strategy_family TEXT NOT NULL,
    study_id TEXT NOT NULL,
    first_job_id TEXT NOT NULL,
    n_trials_raw INTEGER NOT NULL,
    n_trials_effective INTEGER,
    method TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (round_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS mt5_reported_metrics (
    job_id TEXT NOT NULL REFERENCES job(job_id),
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (job_id, name)
);
"""


def _triggers() -> str:
    out = []
    for t in _APPEND_ONLY:
        for op in ("UPDATE", "DELETE"):
            out.append(
                f"CREATE TRIGGER IF NOT EXISTS {t}_no_{op.lower()} BEFORE {op} ON {t} "
                f"BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END;"
            )
    return "\n".join(out)


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, isolation_level=None)  # explicit transactions only
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        # executescript() commits implicitly, so the idempotent DDL runs outside tx().
        self.conn.executescript(_DDL + _triggers())
        with self.tx():
            row = self.conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                self.conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] in (1, 2):
                # v1 -> v2 -> v3 only add tables (created above by the idempotent DDL)
                self.conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
            elif row["version"] != SCHEMA_VERSION:
                raise RuntimeError(f"unsupported schema version {row['version']} (expected {SCHEMA_VERSION})")

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        if self.conn.in_transaction:
            yield self.conn
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    # --- generic helpers -------------------------------------------------
    def insert(self, table: str, row: dict[str, Any], *, if_absent: bool = False) -> None:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        verb = "INSERT OR IGNORE" if if_absent else "INSERT"
        with self.tx():
            self.conn.execute(f"{verb} INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    # --- job / opt_job (the only mutable tables) ------------------------
    def update_job(self, job_id: str, *, table: str = "job", **fields: Any) -> None:
        if table not in ("job", "opt_job"):
            raise ValueError(f"{table} is not a mutable table")
        if not fields:
            return
        for k, v in list(fields.items()):
            if isinstance(v, (list, dict)):
                fields[k] = json.dumps(v, ensure_ascii=False, sort_keys=True)
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.tx():
            cur = self.conn.execute(f"UPDATE {table} SET {sets} WHERE job_id = ?", (*fields.values(), job_id))
            if cur.rowcount != 1:
                raise KeyError(job_id)
