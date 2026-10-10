"""All branches of the P1 flow against a fake terminal (P1_PLAN §12)."""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from robustlab.single_backtest import (
    EXIT_GUARD,
    EXIT_OK,
    EXIT_RUNTIME,
    EXIT_USER,
)
from robustlab.storage.db import Database

FAKE = Path(__file__).with_name("fake_terminal.py")


def jobs(e):
    db = Database(e.ws / "robustlab.sqlite")
    try:
        return [dict(r) for r in db.all("SELECT * FROM job ORDER BY job_id")]
    finally:
        db.close()


def roles(e, job_id):
    db = Database(e.ws / "robustlab.sqlite")
    try:
        return {r["role"] for r in db.all("SELECT role FROM job_artifact WHERE job_id = ?", (job_id,))}
    finally:
        db.close()


def test_success_is_stored_and_cleaned_up(env):
    out = env.run()
    assert out.exit_code == EXIT_OK and out.status == "SUCCEEDED", out
    j = jobs(env)[0]
    assert j["status"] == "SUCCEEDED" and j["deals_count"] == 65 and j["observed_build"] == 6230
    assert j["net_profit_mt5"] == "9.69" and j["net_profit_sum"] == "9.69"
    r = roles(env, j["job_id"])
    assert {"ini", "set", "telemetry_deals", "telemetry_done", "report", "log_segment",
            "deals_canonical_csv", "deals_parquet"} <= r
    # files created outside the workspace were archived and removed
    assert not list(env.common.glob("RL_*")) and not list(env.data.glob("RL_*_report*"))
    assert not list((env.data / "MQL5" / "Profiles" / "Tester").glob("rl_*.set"))
    # no credentials in any stored text artifact
    for p in (env.ws / "artifacts").rglob("*"):
        if p.is_file():
            assert b"Password" not in p.read_bytes() and b"Login=" not in p.read_bytes()


def test_idempotent_and_rerun_compares_reproducibility(env):
    first = env.run()
    again = env.run()
    assert again.exit_code == EXIT_OK and again.status == "ALREADY_SUCCEEDED" and again.job_id == first.job_id
    rerun = env.run(rerun=True)
    assert rerun.status == "SUCCEEDED" and rerun.details["attempt"] == 2
    assert any(w.startswith("REPRO_MATCH") for w in rerun.details["warnings"])
    assert len(jobs(env)) == 2


@pytest.mark.parametrize(
    "mode, status",
    [
        ("no_start", "FAILED_TO_START"),
        ("hang", "TIMED_OUT"),
        ("crash", "TELEMETRY_MISSING"),
        ("bad_profit", "QUARANTINED"),
        ("no_success", "QUARANTINED"),
    ],
)
def test_failures_are_never_stored_as_success(env, mode, status):
    out = env.run(mode)
    assert out.exit_code == EXIT_RUNTIME and out.status == status, out
    j = jobs(env)[0]
    assert j["status"] == status and j["deals_content_hash"] is None
    assert "deals_parquet" not in roles(env, j["job_id"])
    # the killed process tree is gone
    if j["pid"]:
        import psutil
        assert not psutil.pid_exists(j["pid"]) or psutil.Process(j["pid"]).status() == psutil.STATUS_ZOMBIE


def test_quarantined_keeps_raw_telemetry(env):
    env.run("bad_profit")
    j = jobs(env)[0]
    assert {"telemetry_deals", "telemetry_stats", "telemetry_done"} <= roles(env, j["job_id"])
    assert "STAT_PROFIT" in j["error_detail"]


def test_holdout_request_is_rejected_without_launching(env, tmp_path):
    bad = env.req.with_name("holdout.yaml")
    bad.write_text(env.req.read_text().replace("2023-01-09", "2024-06-25").replace("2023-01-13", "2024-07-05"))
    out = env.run(req=bad)
    assert out.exit_code == EXIT_GUARD and out.status == "GUARD_REJECTED"
    assert jobs(env) == []  # no job, no ini, MT5 never started
    db = Database(env.ws / "robustlab.sqlite")
    assert db.one("SELECT approved FROM holdout_access_log")["approved"] == 0
    db.close()


def test_partition_change_blocks_all_runs(env):
    assert env.run().status == "SUCCEEDED"
    env.partition.write_text("holdout_from: 2024-01-01\nholdout_to: 2026-06-30\nembargo_days: 10\n")
    out = env.run(rerun=True)
    assert out.exit_code == EXIT_GUARD and "PARTITION_CHANGED" in out.message


def test_preflight_refuses_when_terminal_running(env):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", str(env.data / "terminal64.exe")])
    try:
        out = env.run()
    finally:
        p.kill()
        p.wait()
    assert out.exit_code == EXIT_USER and out.status == "PREFLIGHT_FAILED" and "already running" in out.message
    assert jobs(env) == []


def test_missing_ex5_and_bad_params(env):
    bad = env.req.with_name("bad.yaml")
    bad.write_text(env.req.read_text().replace("InpLots: 0.01", "InpLot: 0.01"))
    out = env.run(req=bad)
    assert out.exit_code == EXIT_USER and out.status == "CONFIG_INVALID"
    (env.data / "MQL5" / "Experts" / "RobustLab" / "RL_SmokeTest.ex5").unlink()
    out = env.run()
    assert out.exit_code == EXIT_USER and "compiled EA not found" in out.message


def test_abandoned_jobs_are_marked(env):
    assert env.run().status == "SUCCEEDED"
    db = Database(env.ws / "robustlab.sqlite")
    jid = jobs(env)[0]["job_id"]
    db.conn.execute("UPDATE job SET status='RUNNING', pid=999999999 WHERE job_id=?", (jid,))
    db.close()
    env.run()  # any invocation sweeps
    assert jobs(env)[0]["status"] == "ABANDONED"


def test_research_tables_stay_append_only(env):
    env.run()
    db = Database(env.ws / "robustlab.sqlite")
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("UPDATE run SET from_date='2000-01-01'")
    db.close()


def test_unexpected_error_is_recorded_not_left_running(env):
    (env.data / "terminal64.exe").chmod(0o644)  # launching now raises PermissionError
    out = env.run()
    assert out.exit_code == EXIT_RUNTIME and out.status == "INTERNAL_ERROR"
    j = jobs(env)[0]
    assert j["status"] == "INTERNAL_ERROR" and "PermissionError" in j["error_detail"]


def test_relative_workspace_path_works(env, monkeypatch):
    # Regression (first hardware E2E): a relative workspace made the /config path relative, and the
    # terminal (working directory = its own folder) could not find the ini -> FAILED_TO_START.
    monkeypatch.chdir(env.ws.parent)
    monkeypatch.setenv("FAKE_MODE", "ok")
    from robustlab.single_backtest import run_backtest
    out = run_backtest(env.req, env.terminal_cfg, env.partition, Path(env.ws.name))
    assert out.status == "SUCCEEDED", out


# --- G1/G2: LiveUpdate handoff (observed on hardware, 2026-10-10) ---------------------------
def _terminal_processes(env):
    from robustlab.mt5.terminal import find_running
    return find_running(env.data / "terminal64.exe")


def test_liveupdate_handoff_is_followed_to_the_relaunched_terminal(env):
    out = env.run("update")
    assert out.status == "SUCCEEDED", out
    assert out.details["observed_build"] == 6251
    assert any(w.startswith("TERMINAL_UPDATED") and "6230 -> 6251" in w for w in out.details["warnings"])
    assert _terminal_processes(env) == []


def test_liveupdate_without_relaunch_fails_to_start(env):
    out = env.run("update_none")
    assert out.status == "FAILED_TO_START"
    assert any(w.startswith("TERMINAL_UPDATED") for w in jobs_warnings(env))


def test_hanging_relaunched_terminal_is_killed_before_returning(env):
    out = env.run("update_hang")
    assert out.status == "TIMED_OUT"
    assert _terminal_processes(env) == []  # G2: nothing of this terminal is left running


def jobs_warnings(env):
    import json
    return json.loads(jobs(env)[0]["warnings_json"])


# --- P2: telemetry v2 and metrics --------------------------------------------------------------
def _v2_request(env, version="2"):
    s = (env.req.parent.parent / "strategies" / "smoke.yaml")
    s2 = s.with_name(f"smoke{version}.yaml")
    s2.write_text(s.read_text().replace('telemetry_version: "1"', f'telemetry_version: "{version}"'))
    r2 = env.req.with_name(f"r{version}.yaml")
    r2.write_text(env.req.read_text().replace("smoke.yaml", f"smoke{version}.yaml"))
    return r2


def _db(env):
    return Database(env.ws / "robustlab.sqlite")


@pytest.mark.parametrize("version", ["2", "3"])
def test_v2_job_gets_metrics_that_match_mt5(env, monkeypatch, version):
    monkeypatch.setenv("FAKE_TELEMETRY_VERSION", version)
    out = env.run(req=_v2_request(env, version))
    assert out.status == "SUCCEEDED", out
    assert "MISMATCH" not in (out.details["metric_checks"] or "") and "MATCH" in out.details["metric_checks"]
    db = _db(env)
    try:
        checks = {r["name"]: r["status"] for r in db.all("SELECT name, status FROM metric_check")}
        assert checks["equity_max_dd"] == "MATCH" and checks["recovery_factor"] == "MATCH"
        assert checks["daily_sharpe"] == "NOT_COMPARABLE"
        assert db.one("SELECT COUNT(*) AS n FROM period_performance")["n"] >= 2
        assert db.one("SELECT 1 FROM job_artifact WHERE role = 'telemetry_daily'") is not None
    finally:
        db.close()


def test_v1_job_gets_trade_metrics_and_reasoned_nulls(env):
    out = env.run()
    assert out.status == "SUCCEEDED"
    db = _db(env)
    try:
        checks = {r["name"]: r["status"] for r in db.all("SELECT name, status FROM metric_check")}
        assert checks["net_profit"] == "MATCH" and checks["equity_max_dd"] == "OURS_MISSING"
    finally:
        db.close()
