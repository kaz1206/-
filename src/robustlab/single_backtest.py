"""P1 single backtest flow (P1_PLAN §3). This module only sequences the other modules."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

from robustlab.config.loader import (
    ConfigError,
    LoadedRequest,
    load_partition,
    load_request,
    load_terminal_config,
)
from robustlab.core import ids
from robustlab.core.models import JobStatus, TerminalConfig
from robustlab.core.params import ParamError, normalize_params
from robustlab.metrics import store as metrics_store
from robustlab.mt5 import ini_builder, telemetry_reader, terminal
from robustlab.oos import holdout_guard
from robustlab.storage.artifacts import ArtifactStore
from robustlab.storage.db import Database, now_iso

EXIT_OK, EXIT_USER, EXIT_RUNTIME, EXIT_GUARD = 0, 1, 2, 3
ABANDON_PREPARING_AFTER = dt.timedelta(hours=1)


@dataclass
class Outcome:
    exit_code: int
    status: str
    message: str = ""
    run_id: str | None = None
    job_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class Workspace:
    def __init__(self, root: Path):
        # Absolute, because the terminal runs with its own folder as the working directory and
        # would resolve a relative /config path there (found in the first hardware E2E run).
        root = root.resolve()
        self.root = root
        self.db = Database(root / "robustlab.sqlite")
        self.store = ArtifactStore(root / "artifacts", self.db)
        self.jobs_dir = root / "jobs"

    def close(self) -> None:
        self.db.close()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sweep_abandoned(db: Database) -> list[str]:
    """Mark jobs left RUNNING/PREPARING by a crashed rlab process as ABANDONED (P1_PLAN §6)."""
    changed = []
    now = dt.datetime.now(dt.UTC)
    for row in db.all("SELECT job_id, status, pid, created_at FROM job WHERE status IN ('RUNNING','PREPARING')"):
        stale = False
        if row["status"] == "RUNNING":
            stale = row["pid"] is None or not psutil.pid_exists(row["pid"])
        else:
            stale = now - dt.datetime.fromisoformat(row["created_at"]) > ABANDON_PREPARING_AFTER
        if stale:
            db.update_job(row["job_id"], status=JobStatus.ABANDONED.value, finished_at=now_iso(),
                          error_class="ABANDONED", error_detail="rlab exited while the job was in progress")
            changed.append(row["job_id"])
    return changed


def _preflight(cfg: TerminalConfig, lr: LoadedRequest) -> tuple[list[str], Path, Path | None]:
    problems = []
    data_dir = cfg.resolved_data_dir
    ex5 = data_dir / "MQL5" / "Experts" / (lr.strategy.expert.replace("\\", "/") + ".ex5")
    mq5 = (lr.strategy_path.parent / lr.strategy.mq5_path).resolve() if lr.strategy.mq5_path else None
    if not cfg.terminal_path.is_file():
        problems.append(f"terminal not found: {cfg.terminal_path}")
    if not ex5.is_file():
        problems.append(f"compiled EA not found: {ex5} (copy mql5/ into the terminal and compile)")
    if mq5 is not None and not mq5.is_file():
        problems.append(f"EA source not found: {mq5}")
    if not cfg.common_files_dir.is_dir():
        problems.append(f"common files folder not found: {cfg.common_files_dir}")
    if cfg.terminal_path.is_file():
        running = terminal.find_running(cfg.terminal_path)
        if running:
            problems.append(f"the terminal is already running (pid {running}); close it first")
    return problems, ex5, mq5


def _register(db: Database, lr: LoadedRequest, normalized: dict, ex5: Path, mq5: Path | None,
              partition_hash: str, terminal_path: Path) -> tuple[str, str, str, dict]:
    st, rq = lr.strategy, lr.request
    ex5_sha = _sha_file(ex5)
    mq5_sha = _sha_file(mq5) if mq5 else None
    sv = ids.strategy_version_id(ex5_sha, mq5_sha, st.telemetry_version)
    cd = ids.candidate_id(sv, rq.symbol, rq.timeframe, normalized)
    # "requested tester settings" (P1_PLAN §2.3); the observed build is recorded per job instead (C19)
    settings = rq.tester.model_dump(mode="json") | {"terminal_path": str(terminal_path)}
    settings_hash = ids.tester_settings_hash(settings)
    rn = ids.run_id(cd, rq.from_date, rq.to_date, settings_hash)
    now = now_iso()
    with db.tx():
        db.insert("strategy_version", {
            "strategy_version_id": sv, "strategy_name": st.strategy_name, "expert": st.expert,
            "ex5_sha256": ex5_sha, "mq5_sha256": mq5_sha, "source_available": int(mq5 is not None),
            "telemetry_version": st.telemetry_version,
            "inputs_json": json.dumps([i.model_dump(mode="json") for i in st.inputs]), "registered_at": now,
        }, if_absent=True)
        db.insert("candidate", {
            "candidate_id": cd, "strategy_version_id": sv, "symbol": rq.symbol, "timeframe": rq.timeframe,
            "params_json": ids.canonical_json(normalized), "origin": "MANUAL", "created_at": now,
        }, if_absent=True)
        db.insert("run", {
            "run_id": rn, "candidate_id": cd, "from_date": rq.from_date.isoformat(),
            "to_date": rq.to_date.isoformat(), "period_semantics": "HALF_OPEN_SERVER_TIME",
            "tester_settings_json": ids.canonical_json(settings), "tester_settings_hash": settings_hash,
            "partition_hash": partition_hash, "cost_scenario": "BASE", "source": "SINGLE_TEST",
            "created_at": now,
        }, if_absent=True)
    return sv, cd, rn, settings


def _repro_warnings(db: Database, run_id: str, job_id: str, build: int | None, content_hash: str) -> list[str]:
    prev = db.one(
        "SELECT job_id, observed_build, deals_content_hash FROM job WHERE run_id = ? AND status = 'SUCCEEDED' "
        "AND job_id <> ? ORDER BY job_id DESC LIMIT 1",
        (run_id, job_id),
    )
    if prev is None:
        return []
    if prev["observed_build"] != build:
        return [f"REPRO_NOT_COMPARABLE_BUILD_CHANGED: {prev['job_id']} build {prev['observed_build']} -> {build}"]
    if prev["deals_content_hash"] == content_hash:
        return [f"REPRO_MATCH: deals identical to {prev['job_id']}"]
    return [f"REPRO_MISMATCH: deals differ from {prev['job_id']} with the same build"]


def run_backtest(
    request_path: Path,
    terminal_config_path: Path,
    partition_path: Path,
    workspace_root: Path,
    *,
    rerun: bool = False,
) -> Outcome:
    # 1. configuration
    try:
        cfg = load_terminal_config(terminal_config_path)
        partition, partition_hash = load_partition(partition_path)
        lr = load_request(request_path)
        normalized = normalize_params(lr.request.params, lr.strategy.inputs)
    except (ConfigError, ParamError) as e:
        return Outcome(EXIT_USER, "CONFIG_INVALID", str(e))

    ws = Workspace(workspace_root)
    try:
        return _run(ws, cfg, partition, partition_hash, lr, normalized, rerun)
    finally:
        ws.close()


def _run(ws: Workspace, cfg: TerminalConfig, partition, partition_hash: str, lr: LoadedRequest,
         normalized: dict, rerun: bool) -> Outcome:
    db, rq, st = ws.db, lr.request, lr.strategy
    sweep_abandoned(db)

    # 2. preflight (MT5 is not started on failure)
    problems, ex5, mq5 = _preflight(cfg, lr)
    if problems:
        return Outcome(EXIT_USER, "PREFLIGHT_FAILED", "; ".join(problems))

    # 3. Holdout Guard
    decision = holdout_guard.evaluate(db, request_hash=lr.request_hash, from_date=rq.from_date,
                                      to_date=rq.to_date, partition=partition, partition_hash=partition_hash)
    if not decision.approved:
        return Outcome(EXIT_GUARD, "GUARD_REJECTED", decision.reason)

    # 4. identities
    sv, cd, rn, settings = _register(db, lr, normalized, ex5, mq5, partition_hash, cfg.terminal_path)

    # 5. idempotency
    done = db.one("SELECT job_id FROM job WHERE run_id = ? AND status = 'SUCCEEDED' ORDER BY job_id DESC LIMIT 1", (rn,))
    if done and not rerun:
        return Outcome(EXIT_OK, "ALREADY_SUCCEEDED", "a successful result already exists (use --rerun to repeat)",
                       rn, done["job_id"])

    # 6. job
    job_id = ids.new_job_id()
    attempt = db.one("SELECT COUNT(*) AS n FROM job WHERE run_id = ?", (rn,))["n"] + 1
    spec = {
        "strategy_version_id": sv, "candidate_id": cd, "run_id": rn, "expert": st.expert,
        "symbol": rq.symbol, "timeframe": rq.timeframe, "from_date": rq.from_date.isoformat(),
        "to_date": rq.to_date.isoformat(), "params": normalized, "tester": settings,
        "coverage_tolerance_days": rq.coverage_tolerance_days, "telemetry_version": st.telemetry_version,
    }
    db.insert("job", {
        "job_id": job_id, "run_id": rn, "spec_hash": ids.sha256_hex(ids.canonical_json(spec)),
        "spec_json": ids.canonical_json(spec), "attempt_no": attempt, "status": JobStatus.PREPARING.value,
        "terminal_path": str(cfg.terminal_path), "created_at": now_iso(),
    })
    try:
        return _execute(ws, cfg, lr, normalized, job_id, attempt, sv, cd, rn, settings)
    except Exception as e:  # never leave a job RUNNING, never treat it as a success
        db.update_job(job_id, status=JobStatus.INTERNAL_ERROR.value, finished_at=now_iso(),
                      error_class=JobStatus.INTERNAL_ERROR.value, error_detail=f"{type(e).__name__}: {e}")
        return Outcome(EXIT_RUNTIME, JobStatus.INTERNAL_ERROR.value, f"{type(e).__name__}: {e}", rn, job_id)


def _execute(ws: Workspace, cfg: TerminalConfig, lr: LoadedRequest, normalized: dict, job_id: str,
             attempt: int, sv: str, cd: str, rn: str, settings: dict) -> Outcome:
    db, store, rq, st = ws.db, ws.store, lr.request, lr.strategy
    job_dir = ws.jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # 7. .set and ini (F1)
    data_dir = cfg.resolved_data_dir
    set_name = f"rl_{job_id}.set"
    set_bytes = ini_builder.encode_set(ini_builder.build_set_text(st.inputs, normalized, job_id))
    set_path = data_dir / "MQL5" / "Profiles" / "Tester" / set_name
    set_path.parent.mkdir(parents=True, exist_ok=True)
    set_path.write_bytes(set_bytes)
    ini_text = ini_builder.build_ini_text(
        expert=st.expert, set_filename=set_name, symbol=rq.symbol, timeframe=rq.timeframe,
        from_date=rq.from_date, to_date=rq.to_date, tester=rq.tester, job_id=job_id,
    )
    ini_path = job_dir / "job.ini"
    ini_path.write_bytes(ini_text.encode("ascii"))

    # 8.–11. launch and wait
    db.update_job(job_id, status=JobStatus.RUNNING.value, started_at=now_iso())
    outcome = terminal.run_terminal(
        cfg.terminal_path, data_dir, ini_path, start_timeout_sec=cfg.start_timeout_sec,
        run_timeout_sec=cfg.run_timeout_sec, poll_interval_sec=cfg.poll_interval_sec,
        on_launch=lambda pid: db.update_job(job_id, pid=pid),
    )

    # 12.–13. collect and validate
    exp = telemetry_reader.Expectation(
        job_id=job_id, telemetry_version=st.telemetry_version, symbol=rq.symbol, timeframe=rq.timeframe,
        from_date=rq.from_date, to_date=rq.to_date, tester=rq.tester,
        coverage_tolerance_days=rq.coverage_tolerance_days,
    )
    problems: list[str] = []
    warnings: list[str] = []
    if outcome.updated:  # G1
        builds = " -> ".join(str(b) for b in outcome.builds) or "unknown"
        warnings.append(f"TERMINAL_UPDATED: MT5 updated itself during this job (builds {builds})")
    tel = None
    if outcome.kind is terminal.TerminalOutcomeKind.FAILED_TO_START:
        status = JobStatus.FAILED_TO_START
        problems.append(f"the terminal log never showed {terminal.START_PATTERN!r}")
    elif outcome.kind is terminal.TerminalOutcomeKind.TIMED_OUT:
        status = JobStatus.TIMED_OUT
        problems.append(f"no result within {cfg.run_timeout_sec} s")
    else:
        tel = telemetry_reader.validate(cfg.common_files_dir, exp)
        status = tel.status
        problems += tel.problems
        if status is JobStatus.SUCCEEDED and not outcome.succeeded_line:
            status = JobStatus.QUARANTINED
            problems.append(f"the terminal log did not report {terminal.SUCCESS_PATTERN!r}")

    # 14. artifacts first, then one DB transaction
    roles: list[tuple[str, str, str]] = [
        ("ini", "job.ini", store.put_bytes(ini_text.encode("ascii"))),
        ("set", set_name, store.put_bytes(set_bytes)),
    ]
    tel_files = telemetry_reader.locate(cfg.common_files_dir, job_id)
    for role, path in tel_files.items():
        if path.is_file():
            roles.append((f"telemetry_{role}", path.name, store.put_file(path)))
    report_files = sorted(data_dir.glob(f"{ini_builder.report_name(job_id)}*"))
    for path in report_files:
        roles.append(("report", path.name, store.put_file(path)))
    for path, data in outcome.log_segments.items():  # F8: contains login and IP; stored, never printed
        roles.append(("log_segment", str(path.relative_to(data_dir)).replace("\\", "/"), store.put_bytes(data)))

    fields: dict[str, Any] = {
        "status": status.value, "finished_at": now_iso(), "exit_code": outcome.exit_code,
        "error_class": None if status is JobStatus.SUCCEEDED else status.value,
        "error_detail": "; ".join(problems) or None,
    }
    if tel and tel.env:
        fields["observed_build"] = tel.env.get("terminal_build")
        fields["observed_env_json"] = ids.canonical_json(tel.env)
    if tel and tel.stats:
        fields["net_profit_mt5"] = str(tel.stats.get("values", {}).get("profit"))
    if tel and tel.net_profit_sum is not None:
        fields["net_profit_sum"] = str(tel.net_profit_sum)
    if tel and status is JobStatus.SUCCEEDED:
        roles.append(("deals_canonical_csv", "deals.csv", store.put_bytes(tel.canonical_deals)))
        roles.append(("deals_parquet", "deals.parquet",
                      store.put_bytes(telemetry_reader.deals_parquet_bytes(tel.deals, tel.deals_content_hash))))
        fields["deals_count"] = len(tel.deals)
        fields["deals_content_hash"] = tel.deals_content_hash
        warnings += _repro_warnings(db, rn, job_id, fields.get("observed_build"), tel.deals_content_hash)
    fields["warnings_json"] = warnings

    with db.tx():
        for role, name, sha in roles:
            db.insert("job_artifact", {"job_id": job_id, "role": role, "name": name, "sha256": sha})
        if tel and tel.stats:
            for k, v in sorted(tel.stats.get("values", {}).items()):
                db.insert("mt5_reported_metrics", {"job_id": job_id, "name": k, "value": str(v)})
        db.update_job(job_id, **fields)

    # P2: metrics from the archived artifacts. A failure here never revokes the job's success.
    metric_summary = None
    if status is JobStatus.SUCCEEDED:
        try:
            sm = metrics_store.compute_and_store(db, store, job_id)
            counts: dict[str, int] = {}
            for c in sm.checks:
                counts[c["status"]] = counts.get(c["status"], 0) + 1
            metric_summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        except Exception as e:  # noqa: BLE001 - recorded as a warning on the job
            warnings.append(f"METRICS_FAILED: {type(e).__name__}: {e}")
            db.update_job(job_id, warnings_json=warnings)

    # cleanup of the files this job created outside the workspace (all archived above)
    for path in [set_path, *[p for p in tel_files.values() if p.is_file()], *report_files]:
        path.unlink(missing_ok=True)

    code = EXIT_OK if status is JobStatus.SUCCEEDED else EXIT_RUNTIME
    return Outcome(code, status.value, "; ".join(problems), rn, job_id, {
        "attempt": attempt, "strategy_version_id": sv, "candidate_id": cd,
        "deals": fields.get("deals_count"), "net_profit_mt5": fields.get("net_profit_mt5"),
        "net_profit_sum": fields.get("net_profit_sum"), "observed_build": fields.get("observed_build"),
        "deals_content_hash": fields.get("deals_content_hash"), "warnings": warnings,
        "terminal_seconds": outcome.seconds, "metric_checks": metric_summary,
    })
