"""All branches of the P1 flow against a fake terminal (P1_PLAN §12)."""

import sqlite3
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from robustlab.single_backtest import EXIT_GUARD, EXIT_OK, EXIT_RUNTIME, EXIT_USER, run_backtest
from robustlab.storage.db import Database

FAKE = Path(__file__).with_name("fake_terminal.py")


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "mt5"
    (data / "MQL5" / "Experts" / "RobustLab").mkdir(parents=True)
    (data / "MQL5" / "Experts" / "RobustLab" / "RL_SmokeTest.ex5").write_bytes(b"ex5-binary")
    # the fake takes its data dir from argv[0], so it is linked into the fake terminal folder
    (data / "fake_terminal.py").symlink_to(FAKE)
    term = data / "terminal64.exe"
    term.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{data / "fake_terminal.py"}" "$@"\n')
    term.chmod(term.stat().st_mode | stat.S_IEXEC)
    common = tmp_path / "common"
    common.mkdir()
    monkeypatch.setenv("FAKE_COMMON_DIR", str(common))
    cfg = tmp_path / "configs"
    (cfg / "strategies").mkdir(parents=True)
    (cfg / "requests").mkdir()
    (cfg / "terminal.yaml").write_text(textwrap.dedent(f"""
        terminal_path: {term}
        common_files_dir: {common}
        start_timeout_sec: 2
        run_timeout_sec: 4
        poll_interval_sec: 0.05
    """))
    (cfg / "partition.yaml").write_text("holdout_from: 2024-07-01\nholdout_to: 2026-06-30\nembargo_days: 10\n")
    (cfg / "strategies" / "smoke.yaml").write_text(textwrap.dedent("""
        strategy_name: RL_SmokeTest
        expert: RobustLab\\RL_SmokeTest
        telemetry_version: "1"
        inputs:
          - {name: InpFastPeriod, type: int}
          - {name: InpSlowPeriod, type: int}
          - {name: InpLots, type: double}
    """))
    req = cfg / "requests" / "r.yaml"
    req.write_text(textwrap.dedent("""
        strategy_file: ../strategies/smoke.yaml
        symbol: EURUSD
        timeframe: H1
        from_date: 2023-01-09
        to_date: 2023-01-13
        params: {InpFastPeriod: 12, InpSlowPeriod: 48, InpLots: 0.01}
        tester: {model: REAL_TICKS, deposit: 10000, currency: USD, leverage: 100}
    """))

    class E:
        pass

    e = E()
    e.data, e.common, e.req, e.ws = data, common, req, tmp_path / "workspace"
    e.terminal_cfg, e.partition = cfg / "terminal.yaml", cfg / "partition.yaml"

    def run(mode="ok", rerun=False, req=None):
        monkeypatch.setenv("FAKE_MODE", mode)
        return run_backtest(req or e.req, e.terminal_cfg, e.partition, e.ws, rerun=rerun)

    e.run = run
    return e


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
