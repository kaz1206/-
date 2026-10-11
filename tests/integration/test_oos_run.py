"""P8b end to end on fake data: P8a pass -> manifest -> confirmed one-shot run -> burned (P8B_PLAN §6.2)."""

import json
import textwrap

from typer.testing import CliRunner

from integration.test_validate_run import COST, plan_file, q, selected
from robustlab.cli.main import app
from robustlab.oos.manifest import lock, run
from robustlab.single_backtest import run_backtest
from robustlab.validation.run import lock as vlock
from robustlab.validation.run import run as vrun

HOLDOUT = ("2024-07-01", "2026-07-01")


def p8a_pass(opt, monkeypatch):
    rid, sl, cd = selected(opt, monkeypatch)
    vp = vlock(opt.ws, sl, plan_file(opt), COST, opt.partition)
    r = vrun(opt.ws, vp.plan_id, opt.terminal_cfg, opt.partition)
    assert r.summary["results"][0]["outcome"] == "PASS"
    return vp.plan_id, cd


def test_one_shot_oos(opt, monkeypatch):
    plan_id, cd = p8a_pass(opt, monkeypatch)
    monkeypatch.setenv("FAKE_TRADES_MAP", f"{HOLDOUT[0]}=4")
    out = lock(opt.ws, plan_id, opt.partition, COST)
    assert out.status == "LOCKED" and out.summary["holdout_state"] == "SEALED"
    mid = out.manifest_id
    band = out.summary["results"][0]["band_lower"]
    assert lock(opt.ws, plan_id, opt.partition, COST).status == "MANIFEST_EXISTS"
    # without the confirmation nothing happens and the holdout stays sealed
    nc = run(opt.ws, mid, opt.terminal_cfg, opt.partition, confirmed=False)
    assert nc.status == "NOT_CONFIRMED" and q(opt, "SELECT * FROM holdout_state") == []
    r = run(opt.ws, mid, opt.terminal_cfg, opt.partition, confirmed=True)
    assert r.status == "DONE", r
    res = r.summary["results"][0]
    assert r.summary["holdout_state"] == "BURNED"
    assert res["band_lower"] == band  # the band was fixed at lock time
    assert res["outcome"] == "PASS" and res["reasons"] == [] and "NOT proof" in res["interpretation"]
    d = q(opt, "SELECT * FROM decision WHERE manifest_id = ?", (mid,))
    assert len(d) == 1 and d[0]["stage"] == "OOS"
    log = q(opt, "SELECT * FROM holdout_access_log WHERE approved = 1")
    assert any(row["reason"] == f"OOS: manifest {mid}" for row in log)
    # never again
    assert run(opt.ws, mid, opt.terminal_cfg, opt.partition, confirmed=True).status == "ALREADY_RUN"
    assert lock(opt.ws, plan_id, opt.partition, COST).status == "HOLDOUT_BURNED"
    req = opt.req.parent / "h.yaml"
    req.write_text(textwrap.dedent(f"""
        strategy_file: ../strategies/smoke3.yaml
        symbol: EURUSD
        timeframe: H1
        from_date: {HOLDOUT[0]}
        to_date: 2024-09-01
        params: {{InpFastPeriod: 25, InpSlowPeriod: 45, InpLots: 0.01}}
        tester: {{model: OHLC_M1, deposit: 10000, currency: USD, leverage: 100}}
    """))
    direct = run_backtest(req, opt.terminal_cfg, opt.partition, opt.ws)
    assert direct.status == "GUARD_REJECTED" and direct.exit_code == 3  # ordinary runs never reach the holdout
    show = CliRunner().invoke(app, ["oos", "show", mid, "--workspace", str(opt.ws)])
    assert show.exit_code == 0 and "BURNED" in show.output and "NOT error rates for this EA" in show.output
    assert len(q(opt, "SELECT * FROM oos_access_log WHERE manifest_id = ?", (mid,))) == 1


def test_band_failure_is_reported_as_a_result_not_as_proof(opt, monkeypatch):
    plan_id, cd = p8a_pass(opt, monkeypatch)
    monkeypatch.setenv("FAKE_SCALE_MAP", f"{HOLDOUT[0]}=-1")
    out = lock(opt.ws, plan_id, opt.partition, COST)
    r = run(opt.ws, out.manifest_id, opt.terminal_cfg, opt.partition, confirmed=True)
    res = r.summary["results"][0]
    assert res["outcome"] == "FAIL"
    assert {"OOS_BELOW_PREDICTION_BAND", "OOS_LOSS", "OOS_COST_FRAGILE"} <= set(res["reasons"])
    assert "NOT proof that an edge disappeared" in res["interpretation"]
    assert "winner's curse" in res["interpretation"]


def test_failure_burns_and_only_the_same_spec_is_retried(opt, monkeypatch):
    plan_id, cd = p8a_pass(opt, monkeypatch)
    out = lock(opt.ws, plan_id, opt.partition, COST)
    monkeypatch.setenv("FAKE_MODE", "crash")
    r = run(opt.ws, out.manifest_id, opt.terminal_cfg, opt.partition, confirmed=True)
    assert r.status == "INCOMPLETE" and r.summary["holdout_state"] == "BURNED"
    monkeypatch.setenv("FAKE_MODE", "ok")
    r = run(opt.ws, out.manifest_id, opt.terminal_cfg, opt.partition, confirmed=True)
    assert r.status == "DONE"
    rows = q(opt, "SELECT attempt_no, status FROM oos_run WHERE manifest_id = ? ORDER BY attempt_no",
             (out.manifest_id,))
    assert [x["attempt_no"] for x in rows] == [1, 2] and rows[1]["status"] == "SUCCEEDED"
    specs = q(opt, "SELECT DISTINCT j.spec_hash FROM job j JOIN oos_run o ON o.job_id = j.job_id WHERE o.manifest_id = ?",
              (out.manifest_id,))
    assert len(specs) == 1


def test_inputs_are_checked(opt, monkeypatch):
    rid, sl, cd = selected(opt, monkeypatch)
    diag = vlock(opt.ws, sl, plan_file(opt), COST, opt.partition, cd)
    vrun(opt.ws, diag.plan_id, opt.terminal_cfg, opt.partition)
    assert lock(opt.ws, diag.plan_id, opt.partition, COST).status == "INPUT_INVALID"   # diagnostic never feeds OOS
    assert lock(opt.ws, diag.plan_id, opt.partition, COST, cd).status == "INPUT_INVALID"  # production: no rehearsal
    assert lock(opt.ws, "vp_unknown", opt.partition, COST).status == "INPUT_INVALID"


def test_rehearsal_workspace(opt, monkeypatch):
    # a rehearsal partition pinned from the start (as configs/rehearsal/data_partition.yaml)
    text = opt.partition.read_text()
    opt.partition.write_text(text + "rehearsal: true\n")
    rid, sl, cd = selected(opt, monkeypatch)
    diag = vlock(opt.ws, sl, plan_file(opt), COST, opt.partition, cd)
    vrun(opt.ws, diag.plan_id, opt.terminal_cfg, opt.partition)
    out = lock(opt.ws, diag.plan_id, opt.partition, COST, cd)
    assert out.status == "LOCKED" and out.summary["rehearsal"] is True
    r = run(opt.ws, out.manifest_id, opt.terminal_cfg, opt.partition, confirmed=True)
    assert r.status == "DONE" and r.summary["results"][0]["outcome"].startswith("REHEARSAL")
    assert q(opt, "SELECT COUNT(*) AS n FROM decision WHERE manifest_id = ?", (out.manifest_id,))[0]["n"] == 0
    assert run(opt.ws, out.manifest_id, opt.terminal_cfg, opt.partition, confirmed=True).status == "ALREADY_RUN"
    assert json.loads(q(opt, "SELECT bands_json FROM oos_manifest")[0]["bands_json"])[cd]["version"] == "band1"
