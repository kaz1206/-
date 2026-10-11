"""P9 end to end on fake data: P8a pass -> risk run -> allocation decision (P9_PLAN §7.2)."""

import json

from typer.testing import CliRunner

from integration.test_oos_run import p8a_pass
from integration.test_validate_run import COST, plan_file, q, selected
from robustlab.cli.main import app
from robustlab.risk.run import run
from robustlab.validation.run import lock as vlock
from robustlab.validation.run import run as vrun

PROFILES_YAML = """
profiles:
  default:
    n_paths: 2000
    mu_lcb_reps: 200
"""


def snapshot(tmp, equity=10000.0, vmin=0.01, step=0.01, margin=1100.0, so_mode=0, so=50.0, name="snap.json"):
    p = tmp / name
    p.write_text(json.dumps({
        "version": "snap1", "symbol": "EURUSD", "taken_at": "2026-10-11T00:00:00+00:00",
        "account": {"currency": "USD", "leverage": 100, "margin_so_mode": so_mode, "margin_so_call": 100.0,
                    "margin_so_so": so, "equity": equity, "margin_mode": 2},
        "symbol_info": {"volume_min": vmin, "volume_step": step, "volume_max": 100.0, "trade_contract_size": 100000.0,
                        "currency_profit": "USD", "currency_margin": "EUR", "digits": 5, "point": 0.00001},
        "price": {"bid": 1.1, "ask": 1.1001}, "margin_per_lot": margin, "margin_buy": margin, "margin_sell": margin,
        "stress_width": 0.0001, "stress_cost_per_lot": 10.0}))
    return p


def profiles(tmp):
    p = tmp / "risk_profiles.yaml"
    p.write_text(PROFILES_YAML)
    return p


def test_allocation_end_to_end(opt, monkeypatch, tmp_path):
    plan_id, cd = p8a_pass(opt, monkeypatch)
    out = run(opt.ws, plan_id, snapshot(tmp_path, equity=1_000_000.0), profiles(tmp_path))
    assert out.status == "DONE", out
    r = out.summary["results"][0]
    assert r["eligibility"] == "ELIGIBLE" and r["confidence_weight"] == 0.5 and r["ramp_up"] == 0.25
    assert r["outcome"] == "ALLOCATED", r.get("outcome")
    assert r["lot"] <= r["lot_target"] + 1e-12 and r["lot_raw"] <= r["limits"]["safe"] + 1e-12
    assert abs(r["lot_target"] - r["lot_raw"] * 0.5 * 0.25) < 1e-12
    assert round(r["lot"] / 0.01, 9) == int(round(r["lot"] / 0.01))  # on the step grid
    # every limited scenario was re-checked at the final lot, all within the limits
    assert set(r["at_final_lot"]) == {"S0", "S1b", "S2", "S3", "S4", "S5", "S6", "S63", "SC"}
    assert "dd" not in r["at_final_lot"]["SC"] and "S1a" not in r["at_final_lot"]
    assert r["scenarios"]["S1a"]["role"] == "reference"
    d = q(opt, "SELECT * FROM decision WHERE risk_id = ?", (out.risk_id,))
    assert len(d) == 1 and d[0]["stage"] == "ALLOCATION" and d[0]["outcome"] == "ALLOCATED"
    assert d[0]["plan_id"] == plan_id and d[0]["filter_id"] is None and d[0]["component_version"].startswith("p9-")
    # the same input gives the same execution
    again = run(opt.ws, plan_id, snapshot(tmp_path, equity=1_000_000.0), profiles(tmp_path))
    assert again.status == "ALREADY_DONE" and again.risk_id == out.risk_id
    # a small account: the target lot is below the minimum lot -> INFEASIBLE, never rounded up
    small = run(opt.ws, plan_id, snapshot(tmp_path, equity=1_000_000.0), profiles(tmp_path), equity=200.0)
    rs = small.summary["results"][0]
    assert rs["outcome"] == "INFEASIBLE_MIN_LOT" and rs["lot"] == 0.0 and 0 < rs["lot_target"] < 0.01
    assert rs["sizing_notes"]["equity_needed_for_min_lot"] > 200.0
    ds = q(opt, "SELECT * FROM decision WHERE risk_id = ?", (small.risk_id,))
    assert ds[0]["outcome"] == "NOT_ALLOCATED" and json.loads(ds[0]["reason_codes_json"]) == ["INFEASIBLE_MIN_LOT"]
    assert "EQUITY_OVERRIDDEN" in json.loads(ds[0]["warning_codes_json"])
    # the screen keeps the fixed texts
    show = CliRunner().invoke(app, ["risk", "show", small.risk_id, "--workspace", str(opt.ws)])
    assert show.exit_code == 0 and "not rounded up" in show.output and "NOT evidence of an edge" in show.output
    assert "Simulation result" in show.output and "NOT been sufficiently verified" in show.output


def test_margin_budget(opt, monkeypatch, tmp_path):
    plan_id, cd = p8a_pass(opt, monkeypatch)
    out = run(opt.ws, plan_id, snapshot(tmp_path, equity=1_000_000.0, margin=1e9), profiles(tmp_path))
    r = out.summary["results"][0]
    assert r["outcome"] == "MARGIN_BUDGET_EXCEEDED" and r["binding_constraint"] == "margin" and r["lot"] == 0.0


def test_diagnostic_and_failed_candidates(opt, monkeypatch, tmp_path):
    rid, sl, cd = selected(opt, monkeypatch)
    monkeypatch.setenv("FAKE_VAL_SCALE", "-1")
    vp = vlock(opt.ws, sl, plan_file(opt), COST, opt.partition)
    vrun(opt.ws, vp.plan_id, opt.terminal_cfg, opt.partition)
    out = run(opt.ws, vp.plan_id, snapshot(tmp_path, equity=1_000_000.0), profiles(tmp_path))
    r = out.summary["results"][0]
    assert r["eligibility"] == "DIAGNOSTIC" and r["outcome"] == "DIAGNOSTIC"
    assert "lot" not in r and "lot_safe" not in r and "allowed_lot" not in r["scenarios"]["S0"]
    assert r["scenarios"]["S0"]["per_lot"]["max_dd_per_lot_q99"] > 0
    assert q(opt, "SELECT COUNT(*) AS n FROM decision WHERE risk_id = ?", (out.risk_id,))[0]["n"] == 0
    show = CliRunner().invoke(app, ["risk", "show", out.risk_id, "--workspace", str(opt.ws)])
    assert "DIAGNOSTIC: not a candidate for allocation" in show.output


def test_oos_fail_blocks_allocation(opt, monkeypatch, tmp_path):
    from robustlab.oos.manifest import lock, run as orun

    plan_id, cd = p8a_pass(opt, monkeypatch)
    monkeypatch.setenv("FAKE_SCALE_MAP", "2024-07-01=-1")
    m = lock(opt.ws, plan_id, opt.partition, COST)
    assert orun(opt.ws, m.manifest_id, opt.terminal_cfg, opt.partition, confirmed=True).status == "DONE"
    out = run(opt.ws, plan_id, snapshot(tmp_path, equity=1_000_000.0), profiles(tmp_path))
    r = out.summary["results"][0]
    assert r["outcome"] == "NOT_ELIGIBLE_OOS_FAILED" and "lot" not in r
    d = q(opt, "SELECT * FROM decision WHERE risk_id = ?", (out.risk_id,))
    assert d[0]["outcome"] == "NOT_ALLOCATED"


def test_input_checks(opt, monkeypatch, tmp_path):
    plan_id, cd = p8a_pass(opt, monkeypatch)
    bad = snapshot(tmp_path, name="bad.json")
    doc = json.loads(bad.read_text())
    doc["account"]["login"] = 123
    bad.write_text(json.dumps(doc))
    assert run(opt.ws, plan_id, bad, profiles(tmp_path)).status == "INPUT_INVALID"
    doc = json.loads(snapshot(tmp_path, name="x.json").read_text())
    doc["symbol"] = "XAUUSD"
    (tmp_path / "x.json").write_text(json.dumps(doc))
    assert run(opt.ws, plan_id, tmp_path / "x.json", profiles(tmp_path)).status == "INPUT_INVALID"
    assert run(opt.ws, "vp_none", snapshot(tmp_path), profiles(tmp_path)).status == "INPUT_INVALID"
