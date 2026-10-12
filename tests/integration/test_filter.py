"""P4 initial filter over an optimization round from the fake terminal (P4_PLAN §7)."""

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from robustlab.cli.main import app
from robustlab.filtering.run import run_filter
from robustlab.storage.db import Database

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "configs" / "strategy_profiles.yaml"


def q(env, sql, params=()):
    db = Database(env.ws / "robustlab.sqlite")
    try:
        return [dict(r) for r in db.all(sql, params)]
    finally:
        db.close()


def test_filter_round_records_every_gate_and_is_idempotent(opt):
    out = opt.run_opt()
    rid = out.details["round_id"]
    f = run_filter(opt.ws, rid, PROFILES, "intraday")
    assert f.exit_code == 0 and f.status == "DONE", f
    s = f.summary
    assert s["candidates"] == 9 and s["passed"] + s["failed"] == 9
    decisions = q(opt, "SELECT * FROM decision WHERE filter_id = ?", (f.filter_id,))
    assert len(decisions) == 9 and len(q(opt, "SELECT * FROM evaluation WHERE filter_id = ?", (f.filter_id,))) == 9
    for d in decisions:
        details = json.loads(d["details_json"])
        assert len(details["gates"]) == 9  # all gates, even after a failure
        assert d["component_name"] == "initial_filter" and d["component_version"] == "f1"
        assert details["profile_hash"] and details["conditions"]["deposit"] == 10000
        assert details["conditions"]["leverage"] == 100 and details["conditions"]["fixed_inputs"] == {"InpLots": "0.01"}
        # the fake round has only 3 days of data: nothing may pass
        assert d["outcome"] == "FAIL" and "INSUFFICIENT_DATA" in json.loads(d["reason_codes_json"])
    assert s["reason_counts"]["INSUFFICIENT_DATA"] == 9
    assert {r["code"] for r in q(opt, "SELECT code FROM reason_code")} >= {"LOW_PSR", "BANKRUPTCY"}
    # same round + same profile -> the existing result
    again = run_filter(opt.ws, rid, PROFILES, "intraday")
    assert again.status == "ALREADY_DONE" and again.filter_id == f.filter_id
    assert len(q(opt, "SELECT * FROM decision")) == 9


def test_changing_a_threshold_is_a_new_execution_and_keeps_the_old_one(opt, tmp_path):
    rid = opt.run_opt().details["round_id"]
    first = run_filter(opt.ws, rid, PROFILES, "intraday")
    loose = tmp_path / "profiles.yaml"
    loose.write_text(PROFILES.read_text().replace("min_days: 60", "min_days: 2", 1))
    second = run_filter(opt.ws, rid, loose, "intraday")
    assert second.status == "DONE" and second.filter_id != first.filter_id
    assert second.summary["filter_runs_on_this_round"] == 2
    assert len(q(opt, "SELECT * FROM decision")) == 18  # old decisions kept (append-only)
    assert "INSUFFICIENT_DATA" not in second.summary["reason_counts"]


def test_incomplete_round_and_unknown_profile_are_refused(opt):
    out = opt.run_opt(mode="missing_pass")
    rid = q(opt, "SELECT round_id FROM optimization_round")[0]["round_id"]
    assert out.status == "PARTIAL"
    f = run_filter(opt.ws, rid, PROFILES, "intraday")
    assert f.exit_code == 1 and f.status == "ROUND_NOT_READY"
    f = run_filter(opt.ws, rid, PROFILES, "no_such_profile")
    assert f.exit_code == 1 and f.status == "CONFIG_INVALID"


def test_cli_filter_run_show_and_why_not(opt, monkeypatch):
    rid = opt.run_opt().details["round_id"]
    runner = CliRunner()
    res = runner.invoke(app, ["filter", "run", rid, "--profile", "intraday", "--profiles", str(PROFILES),
                              "--workspace", str(opt.ws)])
    assert res.exit_code == 0, res.output
    assert "INSUFFICIENT_DATA: 9" in res.output and "not a forecast of profit" in res.output
    fid = next(line.split(": ")[1] for line in res.output.splitlines() if line.startswith("filter_id:"))
    show = runner.invoke(app, ["filter", "show", fid, "--workspace", str(opt.ws)])
    assert show.exit_code == 0 and "candidates: 9" in show.output
    cd = q(opt, "SELECT candidate_id FROM decision LIMIT 1")[0]["candidate_id"]
    why = runner.invoke(app, ["why-not", cd, "--workspace", str(opt.ws)])
    assert why.exit_code == 0, why.output
    for gate in ("bankruptcy", "stop_out", "margin_level", "max_positions", "trades", "days", "expectancy", "psr",
                 "trade_frequency"):
        assert gate in why.output
    assert "outcome: FAIL" in why.output and "recorded, not filtered (C51)" in why.output
    assert runner.invoke(app, ["why-not", "cd_unknown", "--workspace", str(opt.ws)]).exit_code == 1


def test_v3_database_with_p3_data_migrates_to_v4_unchanged(opt):
    opt.run_opt()
    path = opt.ws / "robustlab.sqlite"
    tables = ("candidate", "run", "opt_job", "pass_result", "trial_ledger", "optimization_round")
    con = sqlite3.connect(path)
    before = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    for t in ("decision", "evaluation", "filter_execution", "reason_code"):
        con.execute(f"DROP TABLE {t}")
    con.execute("UPDATE schema_version SET version = 3")
    con.commit()
    con.close()
    db = Database(path)
    try:
        assert db.one("SELECT version FROM schema_version")["version"] == 11
        after = {t: [tuple(r) for r in db.all(f"SELECT * FROM {t} ORDER BY 1")] for t in tables}
    finally:
        db.close()
    assert after == before
