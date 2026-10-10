"""Compute metrics from a job's stored artifacts and save them append-only (P2_PLAN §2.1-6).

Metrics never require re-running a backtest: everything is read back from the
content-addressed artifacts of a SUCCEEDED job.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import robustlab
from robustlab.metrics import compare, engine
from robustlab.mt5 import telemetry_reader as tr
from robustlab.storage.artifacts import ArtifactStore
from robustlab.storage.db import Database, now_iso


class MetricsError(RuntimeError):
    pass


@dataclass
class StoredMetrics:
    job_id: str
    version: str
    created: bool  # False if this version had already been computed
    metrics: dict
    unavailable: dict
    checks: list[dict]


def _artifact_text(db: Database, store: ArtifactStore, job_id: str, role: str) -> str | None:
    row = db.one("SELECT sha256 FROM job_artifact WHERE job_id = ? AND role = ?", (job_id, role))
    return store.read(row["sha256"]).decode("utf-8-sig") if row else None


def load(db: Database, job_id: str, version: str | None = None) -> StoredMetrics | None:
    version = version or engine.METRIC_DEF_VERSION
    row = db.one("SELECT * FROM run_metrics WHERE job_id = ? AND metric_def_version = ?", (job_id, version))
    if row is None:
        return None
    checks = [dict(r) for r in db.all(
        "SELECT name, ours, mt5, diff, status FROM metric_check WHERE job_id = ? AND metric_def_version = ? "
        "ORDER BY name", (job_id, version))]
    return StoredMetrics(job_id, row["metric_def_version"], False, json.loads(row["metrics_json"]),
                         json.loads(row["unavailable_json"]), checks)


def compute_and_store(db: Database, store: ArtifactStore, job_id: str) -> StoredMetrics:
    job = db.one("SELECT status FROM job WHERE job_id = ?", (job_id,))
    if job is None:
        raise MetricsError(f"unknown job {job_id}")
    if job["status"] != "SUCCEEDED":
        raise MetricsError(f"job {job_id} is {job['status']}; metrics are computed for SUCCEEDED jobs only")
    existing = load(db, job_id)
    if existing is not None:
        return existing

    deals_text = _artifact_text(db, store, job_id, "telemetry_deals")
    env_text = _artifact_text(db, store, job_id, "telemetry_env")
    stats_text = _artifact_text(db, store, job_id, "telemetry_stats")
    if deals_text is None or env_text is None or stats_text is None:
        raise MetricsError(f"job {job_id} lacks telemetry artifacts")
    env = json.loads(env_text)
    mt5 = json.loads(stats_text).get("values", {})
    deals = tr.parse_csv_text(deals_text, tr.DEALS_COLUMNS, "deals")
    daily_text = _artifact_text(db, store, job_id, "telemetry_daily")
    daily = tr.parse_csv_text(daily_text, tr.DAILY_COLUMNS, "daily") if daily_text else None

    result = engine.compute(deals, initial_deposit=float(env["initial_balance"]), daily=daily,
                            tracked=env.get("tracking"))
    checks = compare.compare(result.metrics, mt5)
    version = engine.METRIC_DEF_VERSION
    with db.tx():
        db.insert("run_metrics", {
            "job_id": job_id, "metric_def_version": version,
            "metrics_json": json.dumps(result.metrics, sort_keys=True),
            "unavailable_json": json.dumps(result.unavailable, sort_keys=True),
            "telemetry_version": str(env.get("telemetry_version", "1")),
            "code_version": robustlab.__version__, "computed_at": now_iso(),
        })
        for c in checks:
            db.insert("metric_check", {
                "job_id": job_id, "metric_def_version": version, "name": c["name"],
                "ours": None if c["ours"] is None else str(c["ours"]),
                "mt5": None if c["mt5"] is None else str(c["mt5"]),
                "diff": c["diff"], "status": c["status"],
            })
        for per in result.periods:
            db.insert("period_performance", {"job_id": job_id, "metric_def_version": version, **per})
    return StoredMetrics(job_id, version, True, result.metrics, result.unavailable, checks)
