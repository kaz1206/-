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

SCHEMA_VERSION = 11  # 2: P2 metrics; 3: P3 optimization; 4: P4 filter/evaluation/decision; 5: P5 robustness;
#                     6: P6 statistics; 7: P7 selection; 8: P8a validation; 9: P8b OOS; 10: P9 risk; 11: P10 null

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
    "filter_execution",
    "evaluation",
    "decision",
    "reason_code",
    "robustness_execution",
    "parameter_region",
    "statistics_execution",
    "selection_execution",
    "validation_plan",
    "validation_run",
    "period_exposure",
    "validation_access_log",
    "oos_manifest",
    "holdout_state",
    "oos_run",
    "oos_access_log",
    "risk_execution",
    "risk_result",
    "null_execution",
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

CREATE TABLE IF NOT EXISTS reason_code (
    code TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS filter_execution (
    filter_id TEXT PRIMARY KEY,
    round_id TEXT NOT NULL REFERENCES optimization_round(round_id),
    profile_name TEXT NOT NULL,
    profile_hash TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    component_name TEXT NOT NULL,
    component_version TEXT NOT NULL,
    input_job_ids_json TEXT NOT NULL,
    conditions_json TEXT NOT NULL,
    n_candidates INTEGER NOT NULL,
    n_pass INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS filter_execution_round ON filter_execution(round_id);

CREATE TABLE IF NOT EXISTS evaluation (
    evaluation_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidate(candidate_id),
    stage TEXT NOT NULL,
    evaluator_name TEXT NOT NULL,
    evaluator_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    input_run_ids_json TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    values_json TEXT NOT NULL,
    filter_id TEXT REFERENCES filter_execution(filter_id),
    created_at TEXT NOT NULL,
    robust_id TEXT,
    stat_id TEXT,
    plan_id TEXT,
    manifest_id TEXT
);
CREATE INDEX IF NOT EXISTS evaluation_candidate ON evaluation(candidate_id);

CREATE TABLE IF NOT EXISTS robustness_execution (
    robust_id TEXT PRIMARY KEY,
    round_id TEXT NOT NULL REFERENCES optimization_round(round_id),
    filter_id TEXT NOT NULL REFERENCES filter_execution(filter_id),
    score_name TEXT NOT NULL,
    component_name TEXT NOT NULL,
    component_version TEXT NOT NULL,
    config_json TEXT NOT NULL,
    axis_sensitivity_json TEXT NOT NULL,
    status_counts_json TEXT NOT NULL,
    n_candidates INTEGER NOT NULL,
    n_regions INTEGER NOT NULL,
    regions_reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parameter_region (
    region_id TEXT PRIMARY KEY,
    robust_id TEXT NOT NULL REFERENCES robustness_execution(robust_id),
    round_id TEXT NOT NULL REFERENCES optimization_round(round_id),
    method TEXT NOT NULL,
    method_version TEXT NOT NULL,
    member_count INTEGER NOT NULL,
    medoid_candidate_id TEXT NOT NULL REFERENCES candidate(candidate_id),
    members_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS statistics_execution (
    stat_id TEXT PRIMARY KEY,
    round_id TEXT NOT NULL REFERENCES optimization_round(round_id),
    filter_id TEXT NOT NULL REFERENCES filter_execution(filter_id),
    strategy_family TEXT NOT NULL,
    component_name TEXT NOT NULL,
    component_version TEXT NOT NULL,
    config_json TEXT NOT NULL,
    sample_role TEXT NOT NULL,
    from_date TEXT NOT NULL,
    to_date TEXT NOT NULL,
    n_trials INTEGER NOT NULL,
    n_trials_round INTEGER NOT NULL,
    var_sr REAL,
    var_sr_count INTEGER NOT NULL,
    var_sr_round REAL,
    var_sr_round_count INTEGER NOT NULL,
    excluded_counts_json TEXT NOT NULL,
    sr0 REAL,
    sr0_round REAL,
    n_eff_reference_json TEXT NOT NULL,
    family_inputs_json TEXT NOT NULL,
    bootstrap_reps INTEGER NOT NULL,
    seed INTEGER NOT NULL,
    bootstrap_targets_json TEXT NOT NULL,
    arch_version TEXT NOT NULL,
    known_limitations_json TEXT NOT NULL,
    n_candidates INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS statistics_execution_round ON statistics_execution(round_id);

CREATE TABLE IF NOT EXISTS selection_execution (
    sel_id TEXT PRIMARY KEY,
    round_id TEXT NOT NULL REFERENCES optimization_round(round_id),
    filter_id TEXT NOT NULL REFERENCES filter_execution(filter_id),
    robust_id TEXT NOT NULL REFERENCES robustness_execution(robust_id),
    stat_id TEXT NOT NULL REFERENCES statistics_execution(stat_id),
    profile_name TEXT NOT NULL,
    profile_hash TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    selector_name TEXT NOT NULL,
    selector_version TEXT NOT NULL,
    budget INTEGER NOT NULL,
    n_candidates INTEGER NOT NULL,
    n_eligible INTEGER NOT NULL,
    n_gate_pass INTEGER NOT NULL,
    n_representatives INTEGER NOT NULL,
    n_selected INTEGER NOT NULL,
    selected_json TEXT NOT NULL,
    outcome_reason TEXT,
    revision_no INTEGER NOT NULL,
    known_limitations_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS selection_execution_round ON selection_execution(round_id);

CREATE TABLE IF NOT EXISTS validation_plan (
    plan_id TEXT PRIMARY KEY,
    sel_id TEXT NOT NULL REFERENCES selection_execution(sel_id),
    round_id TEXT NOT NULL REFERENCES optimization_round(round_id),
    strategy_family TEXT NOT NULL,
    label TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    strategy_path TEXT NOT NULL,
    cost_json TEXT NOT NULL,
    cost_hash TEXT NOT NULL,
    constants_json TEXT NOT NULL,
    candidates_json TEXT NOT NULL,
    diagnostic INTEGER NOT NULL,
    revision_no INTEGER NOT NULL,
    exposure_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS validation_plan_sel ON validation_plan(sel_id);

CREATE TABLE IF NOT EXISTS validation_run (
    plan_id TEXT NOT NULL REFERENCES validation_plan(plan_id),
    candidate_id TEXT NOT NULL REFERENCES candidate(candidate_id),
    period_name TEXT NOT NULL,
    from_date TEXT NOT NULL,
    to_date TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    status TEXT NOT NULL,
    run_id TEXT,
    job_id TEXT,
    reused INTEGER NOT NULL,
    job_created_at TEXT,
    message TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (plan_id, candidate_id, period_name, attempt_no)
);

CREATE TABLE IF NOT EXISTS period_exposure (
    strategy_family TEXT NOT NULL,
    from_date TEXT NOT NULL,
    to_date TEXT NOT NULL,
    source TEXT NOT NULL,
    plan_id TEXT,
    candidate_id TEXT,
    run_id TEXT,
    seen_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS period_exposure_family ON period_exposure(strategy_family);

CREATE TABLE IF NOT EXISTS validation_access_log (
    plan_id TEXT NOT NULL,
    candidate_id TEXT,
    action TEXT NOT NULL,
    accessed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oos_manifest (
    manifest_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES validation_plan(plan_id),
    round_id TEXT NOT NULL REFERENCES optimization_round(round_id),
    strategy_family TEXT NOT NULL,
    candidates_json TEXT NOT NULL,
    bands_json TEXT NOT NULL,
    criteria_json TEXT NOT NULL,
    partition_hash TEXT NOT NULL,
    holdout_from TEXT NOT NULL,
    holdout_to TEXT NOT NULL,
    cost_json TEXT NOT NULL,
    cost_hash TEXT NOT NULL,
    rehearsal INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS holdout_state (
    partition_hash TEXT NOT NULL,
    state TEXT NOT NULL,
    manifest_id TEXT NOT NULL,
    changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oos_run (
    manifest_id TEXT NOT NULL REFERENCES oos_manifest(manifest_id),
    candidate_id TEXT NOT NULL REFERENCES candidate(candidate_id),
    attempt_no INTEGER NOT NULL,
    status TEXT NOT NULL,
    run_id TEXT,
    job_id TEXT,
    message TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (manifest_id, candidate_id, attempt_no)
);

CREATE TABLE IF NOT EXISTS oos_access_log (
    manifest_id TEXT NOT NULL,
    action TEXT NOT NULL,
    accessed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_execution (
    risk_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES validation_plan(plan_id),
    round_id TEXT NOT NULL REFERENCES optimization_round(round_id),
    symbol TEXT NOT NULL,
    candidates_json TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    profile_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    equity REAL NOT NULL,
    equity_overridden INTEGER NOT NULL,
    component_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_result (
    risk_id TEXT NOT NULL REFERENCES risk_execution(risk_id),
    candidate_id TEXT NOT NULL REFERENCES candidate(candidate_id),
    status TEXT NOT NULL,
    seed INTEGER NOT NULL,
    values_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (risk_id, candidate_id)
);

CREATE TABLE IF NOT EXISTS null_execution (
    null_id TEXT PRIMARY KEY,
    archetype TEXT NOT NULL,
    mode TEXT NOT NULL,
    kind TEXT NOT NULL,
    rounds INTEGER NOT NULL,
    seed INTEGER NOT NULL,
    evidence_seeding TEXT NOT NULL,
    config_json TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    profiles_hash TEXT NOT NULL,
    component_name TEXT NOT NULL,
    component_version TEXT NOT NULL,
    verdict TEXT NOT NULL,
    criterion_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decision (
    decision_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidate(candidate_id),
    stage TEXT NOT NULL,
    outcome TEXT NOT NULL,
    component_name TEXT NOT NULL,
    component_version TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    warning_codes_json TEXT NOT NULL,
    details_json TEXT NOT NULL,
    filter_id TEXT REFERENCES filter_execution(filter_id),
    created_at TEXT NOT NULL,
    sel_id TEXT,
    plan_id TEXT,
    manifest_id TEXT,
    risk_id TEXT
);
CREATE INDEX IF NOT EXISTS decision_candidate ON decision(candidate_id);
CREATE INDEX IF NOT EXISTS decision_filter ON decision(filter_id);

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
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(evaluation)")}
        if "robust_id" not in cols:  # v4 -> v5 adds one nullable column (existing rows unchanged)
            self.conn.execute("ALTER TABLE evaluation ADD COLUMN robust_id TEXT")
        if "stat_id" not in cols:  # v5 -> v6 adds one nullable column (existing rows unchanged)
            self.conn.execute("ALTER TABLE evaluation ADD COLUMN stat_id TEXT")
        if "plan_id" not in cols:  # v7 -> v8
            self.conn.execute("ALTER TABLE evaluation ADD COLUMN plan_id TEXT")
        if "manifest_id" not in cols:  # v8 -> v9
            self.conn.execute("ALTER TABLE evaluation ADD COLUMN manifest_id TEXT")
        dcols = {r["name"] for r in self.conn.execute("PRAGMA table_info(decision)")}
        if "sel_id" not in dcols:  # v6 -> v7 adds one nullable column (existing rows unchanged)
            self.conn.execute("ALTER TABLE decision ADD COLUMN sel_id TEXT")
        if "plan_id" not in dcols:  # v7 -> v8
            self.conn.execute("ALTER TABLE decision ADD COLUMN plan_id TEXT")
        if "manifest_id" not in dcols:  # v8 -> v9
            self.conn.execute("ALTER TABLE decision ADD COLUMN manifest_id TEXT")
        if "risk_id" not in dcols:  # v9 -> v10
            self.conn.execute("ALTER TABLE decision ADD COLUMN risk_id TEXT")
        with self.tx():
            row = self.conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                self.conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
                # every step so far only adds tables (created above by the idempotent DDL)
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
