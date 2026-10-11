"""P8a end to end on fake data: select -> lock -> Discovery rerun + validation periods -> decision (P8_PLAN §8.2)."""

import json
import textwrap
from pathlib import Path

from typer.testing import CliRunner

from integration.test_select_run import SEL_PROFILES, pipeline
from robustlab.cli.main import app
from robustlab.selection.run import run_select
from robustlab.storage.db import Database, now_iso
from robustlab.validation.run import lock, run

ROOT = Path(__file__).resolve().parents[2]
COST = ROOT / "configs" / "cost_stress.yaml"
V0 = ("2021-01-04", "2021-12-20")
V1 = ("2023-01-16", "2023-06-30")


def q(env, sql, params=()):
    db = Database(env.ws / "robustlab.sqlite")
    try:
        return [dict(r) for r in db.all(sql, params)]
    finally:
        db.close()


def plan_file(opt, name="plan.yaml", v0=V0, v1=V1):
    p = opt.req.parent.parent / name
    p.write_text(textwrap.dedent(f"""
        label: {name}
        strategy_file: strategies/smoke3.yaml
        periods:
          - {{name: V0, from: {v0[0]}, to: {v0[1]}}}
          - {{name: V1, from: {v1[0]}, to: {v1[1]}}}
    """))
    return p


def selected(opt, monkeypatch):
    rid, fid, rb, st, _ = pipeline(opt, monkeypatch)
    sel = run_select(opt.ws, rid, fid, rb, st, SEL_PROFILES)
    assert sel.summary["selected_count"] >= 1
    monkeypatch.setenv("FAKE_SINGLE_SYNTH", "mixed")
    monkeypatch.setenv("FAKE_DISCOVERY_FROM", "2022-01-03")
    monkeypatch.setenv("FAKE_TELEMETRY_VERSION", "3")
    monkeypatch.setenv("FAKE_MODE", "ok")
    return rid, sel.sel_id, sel.summary["selected"][0]["candidate_id"]


def test_validation_end_to_end(opt, monkeypatch):
    rid, sl, cd = selected(opt, monkeypatch)
    out = lock(opt.ws, sl, plan_file(opt), COST, opt.partition)
    assert out.status == "LOCKED", out
    pid = out.plan_id
    assert lock(opt.ws, sl, plan_file(opt), COST, opt.partition).status == "ALREADY_LOCKED"
    r = run(opt.ws, pid, opt.terminal_cfg, opt.partition)
    assert r.status == "DONE", r
    res = r.summary["results"][0]
    assert res["outcome"] == "PASS" and res["reasons"] == [] and res["reproducibility"]["match"] is True
    assert res["p_value"] < 0.05 and res["n_days"] == sum(p["days"] for p in res["periods"].values())
    # parameters are the candidate's, exactly
    cand = json.loads(q(opt, "SELECT params_json FROM candidate WHERE candidate_id = ?", (cd,))[0]["params_json"])
    for row in q(opt, "SELECT r.candidate_id FROM validation_run v JOIN run r USING (run_id) WHERE v.plan_id = ?",
                 (pid,)):
        assert row["candidate_id"] == cd
    assert cand
    # each validation period is its own single test from the deposit; exposure recorded; decision written
    runs = q(opt, "SELECT * FROM validation_run WHERE plan_id = ?", (pid,))
    assert sorted(x["period_name"] for x in runs) == ["DISCOVERY", "V0", "V1"]
    exp = q(opt, "SELECT * FROM period_exposure WHERE plan_id = ?", (pid,))
    assert sorted((e["from_date"], e["to_date"]) for e in exp) == [V0, V1]
    d = q(opt, "SELECT * FROM decision WHERE plan_id = ?", (pid,))
    assert len(d) == 1 and d[0]["stage"] == "VALIDATION" and d[0]["filter_id"] is None
    assert "rank" not in d[0]["details_json"]  # pass / fail only, no ordering (ARCHITECTURE §6.2)
    # idempotent
    assert run(opt.ws, pid, opt.terminal_cfg, opt.partition).status == "ALREADY_EVALUATED"
    # every look at the results is logged
    runner = CliRunner()
    before = len(q(opt, "SELECT * FROM validation_access_log WHERE plan_id = ?", (pid,)))
    show = runner.invoke(app, ["validate", "show", pid, "--workspace", str(opt.ws)])
    assert show.exit_code == 0 and "PASS" in show.output and "known limitation" in show.output
    assert len(q(opt, "SELECT * FROM validation_access_log WHERE plan_id = ?", (pid,))) == before + 1
    # a second plan for the same selection is a revision; the Holm family does not shrink
    out2 = lock(opt.ws, sl, plan_file(opt, "plan2.yaml", v1=("2023-01-16", "2023-05-31")), COST, opt.partition)
    assert out2.status == "LOCKED" and out2.summary["revision_no"] == 1
    r2 = run(opt.ws, out2.plan_id, opt.terminal_cfg, opt.partition)
    assert r2.status == "DONE"
    d2 = q(opt, "SELECT warning_codes_json FROM decision WHERE plan_id = ?", (out2.plan_id,))[0]
    assert "VALIDATION_REVISED" in json.loads(d2["warning_codes_json"])
    assert r2.summary["plans_that_saw_each_period"]["V0"] == 2


def test_losing_validation_fails_with_insufficient_evidence(opt, monkeypatch):
    rid, sl, cd = selected(opt, monkeypatch)
    monkeypatch.setenv("FAKE_VAL_SCALE", "-1")
    out = lock(opt.ws, sl, plan_file(opt), COST, opt.partition)
    r = run(opt.ws, out.plan_id, opt.terminal_cfg, opt.partition)
    res = r.summary["results"][0]
    assert res["outcome"] == "FAIL"
    assert {"VALIDATION_PERIOD_LOSS", "VALIDATION_INSUFFICIENT_EVIDENCE", "COST_FRAGILE"} <= set(res["reasons"])
    assert "REPRODUCIBILITY_MISMATCH" not in res["reasons"]
    show = CliRunner().invoke(app, ["validate", "show", out.plan_id, "--workspace", str(opt.ws)])
    assert "does NOT mean the candidate has no edge" in show.output


def test_reproducibility_mismatch(opt, monkeypatch):
    rid, sl, cd = selected(opt, monkeypatch)
    monkeypatch.setenv("FAKE_DISCOVERY_FROM", "1999-01-01")  # the Discovery rerun now gets the scaled result
    monkeypatch.setenv("FAKE_VAL_SCALE", "0.5")
    out = lock(opt.ws, sl, plan_file(opt), COST, opt.partition)
    r = run(opt.ws, out.plan_id, opt.terminal_cfg, opt.partition)
    assert "REPRODUCIBILITY_MISMATCH" in r.summary["results"][0]["reasons"]


def test_lock_refusals(opt, monkeypatch):
    rid, sl, cd = selected(opt, monkeypatch)
    # touches the Discovery embargo (Discovery starts 2022-01-03, embargo 10 days)
    assert lock(opt.ws, sl, plan_file(opt, "a.yaml", v0=("2021-06-01", "2021-12-30")), COST,
                opt.partition).status == "INPUT_INVALID"
    # touches the holdout (2024-07-01, embargo 10 days)
    out = lock(opt.ws, sl, plan_file(opt, "b.yaml", v1=("2024-01-15", "2024-06-25")), COST, opt.partition)
    assert out.status == "GUARD_REJECTED" and out.exit_code == 3
    # no extra cost for the symbol
    bad = opt.ws.parent / "cost.yaml"
    bad.write_text("symbols:\n  XAUUSD: {extra_price_per_round_trip: 0.3}\n")
    assert lock(opt.ws, sl, plan_file(opt), bad, opt.partition).status == "INPUT_INVALID"
    # a period seen before the round was created is contaminated
    db = Database(opt.ws / "robustlab.sqlite")
    try:
        fam = db.one("SELECT strategy_family FROM optimization_round WHERE round_id = ?", (rid,))["strategy_family"]
        db.insert("period_exposure", {"strategy_family": fam, "from_date": "2021-03-01", "to_date": "2021-04-01",
                                      "source": "VALIDATION_RUN", "plan_id": "vp_old", "candidate_id": None,
                                      "run_id": None, "seen_at": "2000-01-01T00:00:00+00:00", "recorded_at": now_iso()})
    finally:
        db.close()
    assert lock(opt.ws, sl, plan_file(opt), COST, opt.partition).status == "VALIDATION_CONTAMINATED"


def test_no_candidates_and_diagnostic_mode(opt, monkeypatch):
    from robustlab.filtering.run import run_filter
    from robustlab.robustness.run import run_robust
    from robustlab.stat_eval.run import run_stats

    monkeypatch.setenv("FAKE_OPT_DAYS", "120")
    o = opt.run_opt(opt.study(fast=(20, 10, 40), slow=(20, 10, 40)), mode="mixed")
    rid = o.details["round_id"]
    f = run_filter(opt.ws, rid, ROOT / "configs" / "strategy_profiles.yaml", "intraday")
    rb = run_robust(opt.ws, rid, f.filter_id)
    st = run_stats(opt.ws, rid, f.filter_id, reps=200)
    sel = run_select(opt.ws, rid, f.filter_id, rb.robust_id, st.stat_id, SEL_PROFILES)
    assert lock(opt.ws, sel.sel_id, plan_file(opt), COST, opt.partition).status == "NO_CANDIDATES"
    cd = q(opt, "SELECT p.candidate_id FROM pass_result p JOIN candidate c USING (candidate_id) "
                "WHERE c.params_json LIKE '%\"InpFastPeriod\":20,%' AND c.params_json LIKE '%\"InpSlowPeriod\":30%'")[0]
    monkeypatch.setenv("FAKE_SINGLE_SYNTH", "mixed")
    monkeypatch.setenv("FAKE_DISCOVERY_FROM", "2022-01-03")
    monkeypatch.setenv("FAKE_TELEMETRY_VERSION", "3")
    out = lock(opt.ws, sel.sel_id, plan_file(opt), COST, opt.partition, cd["candidate_id"])
    assert out.status == "LOCKED" and out.summary["diagnostic"] is True
    r = run(opt.ws, out.plan_id, opt.terminal_cfg, opt.partition)
    assert r.status == "DONE" and r.summary["results"][0]["outcome"].startswith("DIAGNOSTIC")
    assert q(opt, "SELECT COUNT(*) AS n FROM decision WHERE plan_id = ?", (out.plan_id,))[0]["n"] == 0
    assert {e["source"] for e in q(opt, "SELECT source FROM period_exposure WHERE plan_id = ?", (out.plan_id,))} == \
        {"DIAGNOSTIC"}


def test_technical_failure_is_retried_with_the_same_spec(opt, monkeypatch):
    rid, sl, cd = selected(opt, monkeypatch)
    out = lock(opt.ws, sl, plan_file(opt), COST, opt.partition)
    monkeypatch.setenv("FAKE_MODE", "crash")
    r = run(opt.ws, out.plan_id, opt.terminal_cfg, opt.partition)
    assert r.status == "INCOMPLETE" and r.exit_code == 2
    monkeypatch.setenv("FAKE_MODE", "ok")
    r = run(opt.ws, out.plan_id, opt.terminal_cfg, opt.partition)
    assert r.status == "DONE"
    rows = q(opt, "SELECT period_name, attempt_no, status FROM validation_run WHERE plan_id = ? ORDER BY period_name, "
                  "attempt_no", (out.plan_id,))
    assert any(x["attempt_no"] == 2 and x["status"] == "SUCCEEDED" for x in rows)
    assert any(x["attempt_no"] == 1 and x["status"] != "SUCCEEDED" for x in rows)
    specs = q(opt, "SELECT DISTINCT j.spec_hash, r.from_date FROM job j JOIN run r USING (run_id) JOIN validation_run v "
                   "ON v.run_id = r.run_id WHERE v.plan_id = ?", (out.plan_id,))
    assert len(specs) == len({s["from_date"] for s in specs})  # one job spec per period, retries included
