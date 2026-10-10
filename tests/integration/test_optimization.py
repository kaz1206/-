"""P3 optimization flow against the fake terminal (P3_PLAN §7, C40-C49)."""

import json
import textwrap

import pytest
from typer.testing import CliRunner

from robustlab.cli.main import app
from robustlab.optimization import run_optimization
from robustlab.single_backtest import EXIT_GUARD, EXIT_OK, EXIT_RUNTIME, EXIT_USER
from robustlab.storage.db import Database


@pytest.fixture
def opt(env, monkeypatch):
    cfg = env.req.parent.parent
    (cfg / "strategies" / "smoke3.yaml").write_text(textwrap.dedent("""
        strategy_name: RL_SmokeTest
        expert: RobustLab\\RL_SmokeTest
        telemetry_version: "3"
        inputs:
          - {name: InpFastPeriod, type: int}
          - {name: InpSlowPeriod, type: int}
          - {name: InpLots, type: double}
    """))

    def study(name="s.yaml", max_passes=2000, fast=(5, 1, 7), slow=(20, 10, 40), period=("2022-01-03", "2023-01-02")):
        p = cfg / name
        p.write_text(textwrap.dedent(f"""
            study_id: test_grid
            strategy_file: strategies/smoke3.yaml
            symbol: EURUSD
            timeframe: H1
            from_date: {period[0]}
            to_date: {period[1]}
            param_space:
              InpFastPeriod: {{start: {fast[0]}, step: {fast[1]}, stop: {fast[2]}}}
              InpSlowPeriod: {{start: {slow[0]}, step: {slow[1]}, stop: {slow[2]}}}
            fixed: {{InpLots: 0.01}}
            tester: {{model: OHLC_M1, deposit: 10000, currency: USD, leverage: 100}}
            max_passes_per_job: {max_passes}
        """))
        return p

    def run(path=None, mode="ok", rerun=False):
        monkeypatch.setenv("FAKE_MODE", "ok")
        monkeypatch.setenv("FAKE_OPT_MODE", mode)
        return run_optimization(path or study(), env.terminal_cfg, env.partition, env.ws, rerun=rerun)

    env.study, env.run_opt = study, run
    return env


def q(env, sql, params=()):
    db = Database(env.ws / "robustlab.sqlite")
    try:
        return [dict(r) for r in db.all(sql, params)]
    finally:
        db.close()


def test_full_grid_is_ingested_with_trials_and_cleanup(opt):
    out = opt.run_opt()
    assert out.exit_code == EXIT_OK and out.status == "SUCCEEDED", out
    assert out.details["expected_passes"] == 9 and out.details["chunks"] == 1
    passes = q(opt, "SELECT * FROM pass_result")
    assert len(passes) == 9
    # every pass is a distinct OPTIMIZATION candidate with an OPT_PASS run
    assert len({p["candidate_id"] for p in passes}) == 9
    assert {r["origin"] for r in q(opt, "SELECT origin FROM candidate")} == {"OPTIMIZATION"}
    assert {r["source"] for r in q(opt, "SELECT source FROM run")} == {"OPT_PASS"}
    m = json.loads(passes[0]["metrics_json"])
    assert m["days"] == 3 and m["equity_max_dd"] == 7.5 and "daily_sharpe" in m
    # pass metrics agree with the XML for profit and trades
    for p in passes:
        assert abs(float(p["xml_profit"]) - json.loads(p["metrics_json"])["net_profit"]) < 0.005
    ledger = q(opt, "SELECT * FROM trial_ledger")
    assert len(ledger) == 1 and ledger[0]["n_trials_raw"] == 9 and ledger[0]["strategy_family"] == "RL_SmokeTest"
    job = q(opt, "SELECT * FROM opt_job")[0]
    assert job["received_passes"] == 9 and job["observed_build"] == 6230 and job["daily_content_hash"]
    assert json.loads(job["tester_log_json"]) == {"started": True, "finished_passes": 9, "cache_hit": False,
                                                  "local": 9, "remote": 0, "cloud": 0}
    roles = {r["role"] for r in q(opt, "SELECT role FROM opt_job_artifact")}
    assert {"ini", "set", "frames_passes", "frames_pdaily", "frames_fm_done", "report", "log_segment",
            "daily_canonical_csv", "daily_parquet"} <= roles
    assert not list(opt.common.glob("RL_*")) and not list(opt.data.glob("RL_*_report*"))
    # the optimization .set marks the axes with Y and keeps the rest fixed (X9)
    set_sha = q(opt, "SELECT sha256 FROM opt_job_artifact WHERE role = 'set'")[0]["sha256"]
    text = (opt.ws / "artifacts" / set_sha[:2] / set_sha).read_bytes()[2:].decode("utf-16-le")
    assert "InpFastPeriod=5||5||1||7||Y" in text and "InpLots=0.01||0.01||0.1||0.01||N" in text


def test_second_run_is_idempotent_and_rerun_deletes_cache_and_matches(opt):
    path = opt.study()
    first = opt.run_opt(path)
    again = opt.run_opt(path)
    assert again.status == "ALREADY_SUCCEEDED"
    # the fake left a cache file; without deleting it, a rerun would be a cache hit (X6, C48)
    assert list((opt.data / "Tester" / "cache").glob("RL_SmokeTest.*.opt"))
    rerun = opt.run_opt(path, rerun=True)
    assert rerun.status == "SUCCEEDED", rerun
    j = rerun.details["jobs"][0]
    assert j["cache_deleted"] and any(w.startswith("REPRO_MATCH") for w in j["warnings"])
    # re-running the same chunk adds no trials (C44)
    assert sum(r["n_trials_raw"] for r in q(opt, "SELECT n_trials_raw FROM trial_ledger")) == 9
    assert first.details["round_id"] == rerun.details["round_id"]


def test_chunking_splits_along_the_first_axis(opt):
    out = opt.run_opt(opt.study(max_passes=6))
    assert out.status == "SUCCEEDED", out
    assert out.details["chunks"] == 2
    assert [j["expected"] for j in out.details["jobs"]] == [6, 3]
    assert len(q(opt, "SELECT * FROM pass_result")) == 9
    assert sum(r["n_trials_raw"] for r in q(opt, "SELECT n_trials_raw FROM trial_ledger")) == 9


@pytest.mark.parametrize(("mode", "status", "error"), [
    ("missing_pass", "PARTIAL", "MISSING_PASSES"),
    ("cache_hit", "QUARANTINED", "CACHE_HIT"),
    ("cloud", "QUARANTINED", "AGENT_POLICY_VIOLATION"),
    ("xml_mismatch", "QUARANTINED", "XML_MISMATCH"),
    ("xml_bad_header", "QUARANTINED", "XML_UNREADABLE"),
    ("no_frames", "TELEMETRY_MISSING", "TELEMETRY_MISSING"),
    ("bad_daily", "QUARANTINED", "PASS_CHECK"),
])
def test_failures_are_never_succeeded(opt, mode, status, error):
    out = opt.run_opt(mode=mode)
    assert out.exit_code == EXIT_RUNTIME and out.status == status, out
    job = q(opt, "SELECT * FROM opt_job")[0]
    assert job["status"] == status and job["error_class"] == error
    assert q(opt, "SELECT * FROM pass_result") == []
    # requested trials are counted even when the job fails (C44)
    assert q(opt, "SELECT n_trials_raw FROM trial_ledger")[0]["n_trials_raw"] == 9


def test_holdout_and_config_errors(opt):
    out = opt.run_opt(opt.study(period=("2024-06-01", "2024-08-01")))
    assert out.exit_code == EXIT_GUARD and out.status == "GUARD_REJECTED"
    out = opt.run_opt(opt.study(slow=(20, 7, 40)))
    assert out.exit_code == EXIT_USER and "not a multiple of step" in out.message
    assert q(opt, "SELECT * FROM opt_job") == []


def test_telemetry_v2_strategy_is_refused(opt):
    path = opt.study()
    path.write_text(path.read_text().replace("strategies/smoke3.yaml", "strategies/smoke.yaml"))
    out = opt.run_opt(path)
    assert out.exit_code == EXIT_USER and out.status == "PREFLIGHT_FAILED" and "telemetry_version '3'" in out.message


def test_cli_optimize_show_trials_and_jobs_list(opt, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "ok")
    monkeypatch.setenv("FAKE_OPT_MODE", "ok")
    runner = CliRunner()
    path = opt.study()
    res = runner.invoke(app, ["optimize", "run", str(path), "--terminal", str(opt.terminal_cfg),
                              "--partition", str(opt.partition), "--workspace", str(opt.ws), "--json"])
    assert res.exit_code == 0, res.output
    round_id = json.loads(res.output)["round_id"]
    show = runner.invoke(app, ["optimize", "show", round_id, "--workspace", str(opt.ws)])
    assert show.exit_code == 0 and "passes_stored=9" in show.output and "trials_in_family: 9" in show.output
    trials = runner.invoke(app, ["trials", "show", "RL_SmokeTest", "--workspace", str(opt.ws)])
    assert "total trials (raw N): 9" in trials.output
    lst = runner.invoke(app, ["jobs", "list", "--workspace", str(opt.ws)])
    assert "chunk=0" in lst.output and "passes=9/9" in lst.output


def test_cli_optimize_pass_by_candidate(opt, monkeypatch):
    out = opt.run_opt()
    cd = q(opt, "SELECT candidate_id FROM pass_result ORDER BY pass_no LIMIT 1")[0]["candidate_id"]
    res = CliRunner().invoke(app, ["optimize", "pass", out.details["round_id"], cd, "--workspace", str(opt.ws)])
    assert res.exit_code == 0 and "pass 0" in res.output and "net_profit=" in res.output
