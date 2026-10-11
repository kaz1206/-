"""P6 end to end on fake rounds (P6_PLAN §2.5-§2.6, §4.4, §5, §8).

- every candidate gets DSR values or a null with a reason; bankrupt / idle candidates are kept out of V but in N
- N and V use the family scope; a rerun or an overlapping round never counts a configuration twice
- the bootstrap runs on the requested candidates, values are labelled DISCOVERY_IN_SAMPLE
- statistics never write a decision (approval condition 1: DSR does not decide pass/fail)
"""

import json
from pathlib import Path

from typer.testing import CliRunner

from robustlab.cli.main import app
from robustlab.filtering.run import run_filter
from robustlab.stat_eval.run import expand_round, run_stats
from robustlab.storage.db import Database

PROFILES = Path(__file__).resolve().parents[2] / "configs" / "strategy_profiles.yaml"


def q(env, sql, params=()):
    db = Database(env.ws / "robustlab.sqlite")
    try:
        return [dict(r) for r in db.all(sql, params)]
    finally:
        db.close()


def mixed_round(opt, monkeypatch, days=120, **grid):
    monkeypatch.setenv("FAKE_OPT_DAYS", str(days))
    out = opt.run_opt(opt.study(**(grid or {"fast": (20, 10, 40), "slow": (20, 10, 40)})), mode="mixed")
    assert out.status == "SUCCEEDED", out
    rid = out.details["round_id"]
    f = run_filter(opt.ws, rid, PROFILES, "intraday")
    assert f.status == "DONE"
    return rid, f.filter_id


def vals(opt, stat_id):
    return {e["candidate_id"]: json.loads(e["values_json"]) for e in
            q(opt, "SELECT candidate_id, values_json FROM evaluation WHERE stat_id = ?", (stat_id,))}


def by_params(opt, rid):
    out = {}
    for r in q(opt, "SELECT c.candidate_id, c.params_json FROM candidate c JOIN pass_result p USING (candidate_id) "
                    "JOIN opt_job j USING (job_id) WHERE j.round_id = ?", (rid,)):
        p = json.loads(r["params_json"])
        out[(p["InpFastPeriod"], p["InpSlowPeriod"])] = r["candidate_id"]
    return out


def test_every_candidate_gets_dsr_or_a_reason(opt, monkeypatch):
    rid, fid = mixed_round(opt, monkeypatch)
    cds = by_params(opt, rid)
    ok, broke = cds[(20, 30)], cds[(20, 40)]
    decisions_before = q(opt, "SELECT COUNT(*) AS n FROM decision")[0]["n"]
    r = run_stats(opt.ws, rid, fid, [ok, broke], reps=200)
    assert r.status == "DONE", r
    s = r.summary
    # N: the 9 requested configurations (same as the trial ledger); V: the 2 OK candidates only
    assert s["n_trials"] == s["n_trials_round"] == 9 == s["family_inputs"]["trial_ledger_sum"]
    assert s["var_sr_count"] == 2 and s["excluded"]["family"] == {"BANKRUPTCY": 1, "NO_TRADES": 6}
    assert s["sr0"] is not None and s["sr0"] > 0
    v = vals(opt, r.stat_id)
    assert len(v) == 9
    for x in v.values():
        assert x["sample_role"] == "DISCOVERY_IN_SAMPLE" and x["period"] == {"from": "2022-01-03", "to": "2023-01-02"}
        assert x["counted_in_n"] is True and x["dsr"]["usage"] == "WARNING_AND_RANKING_ONLY"
        for k in ("dsr", "dsr_conservative"):
            assert x["dsr"][k] is not None or x["dsr"].get("reason") or x["dsr"].get(f"{k}_reason")
    # bankrupt: null with the reason, not in V, bootstrap refused with the reason
    b = v[broke]
    assert b["sharpe"] is None and b["dsr"]["dsr"] is None and "bankrupt" in b["dsr"]["reason"]
    assert b["in_var_sr"] is False and b["bootstrap"]["status"] == "NOT_COMPUTED"
    # OK candidate: all variants, the bootstrap SE feeds the conservative DSR
    o = v[ok]
    assert o["bootstrap"]["status"] == "OK" and o["bootstrap_target_reasons"] == ["REQUESTED"]
    d = o["dsr"]
    assert d["dsr_conservative_components"] == ["dsr", "dsr_ar1", "dsr_bootstrap_se", "dsr_newey_west"]
    assert d["dsr_conservative"] == min(d["dsr"], d["dsr_ar1"], d["dsr_newey_west"], d["dsr_bootstrap_se"])
    assert set(d["dependence"]["acf"]) == {"1", "2", "3", "4", "5"}
    assert o["dsr_round_scope"]["dsr"] == d["dsr"]  # one round: family scope == round scope
    assert o["bootstrap"]["seed"] != s["seed"]      # per-candidate seed derived from the run seed
    # idle candidate: no bootstrap (not a target), DSR null with the zero-variance reason
    idle = v[cds[(30, 30)]]
    assert idle["bootstrap"] is None and "zero variance" in idle["dsr"]["reason"]
    # condition 1: statistics write no decision
    assert q(opt, "SELECT COUNT(*) AS n FROM decision")[0]["n"] == decisions_before
    # the expanded grid reproduces the candidate ids P3 stored
    rnd = q(opt, "SELECT * FROM optimization_round WHERE round_id = ?", (rid,))[0]
    assert set(cds.values()) == expand_round(rnd)
    # idempotent; another seed is another execution
    again = run_stats(opt.ws, rid, fid, [broke, ok], reps=200)
    assert again.status == "ALREADY_DONE" and again.stat_id == r.stat_id
    other = run_stats(opt.ws, rid, fid, [ok, broke], reps=200, seed=7)
    assert other.status == "DONE" and other.stat_id != r.stat_id
    assert vals(opt, other.stat_id)[ok]["dsr"]["dsr"] == d["dsr"]  # only the bootstrap depends on the seed


def test_short_series_gets_no_interval(opt, monkeypatch):
    rid, fid = mixed_round(opt, monkeypatch, days=3)
    ok = by_params(opt, rid)[(20, 30)]
    r = run_stats(opt.ws, rid, fid, [ok], reps=200)
    assert r.status == "DONE"
    b = vals(opt, r.stat_id)[ok]["bootstrap"]
    assert b["status"] == "NOT_COMPUTED" and "fewer than 30" in b["reason"]


def test_family_scope_counts_each_configuration_once(opt, monkeypatch):
    rid, fid = mixed_round(opt, monkeypatch)
    # an overlapping second round of the same family: fast 30..50 x slow 20..40 shares 6 configurations
    monkeypatch.setenv("FAKE_OPT_DAYS", "120")
    out2 = opt.run_opt(opt.study(name="s2.yaml", fast=(30, 10, 50), slow=(20, 10, 40)), mode="mixed")
    assert out2.status == "SUCCEEDED"
    r = run_stats(opt.ws, rid, fid, reps=200)
    s = r.summary
    assert s["n_trials_round"] == 9 and s["n_trials"] == 12          # 9 + 9 - 6 shared
    assert s["family_inputs"]["trial_ledger_sum"] == 18               # the ledger counts requests, N does not
    # a rerun of the first round adds jobs but no new (configuration, period)
    before = (s["n_trials"], s["var_sr_count"], s["family_inputs"]["distinct_config_periods"])
    assert opt.run_opt(opt.study(fast=(20, 10, 40), slow=(20, 10, 40)), mode="mixed", rerun=True).status == "SUCCEEDED"
    r2 = run_stats(opt.ws, rid, fid, reps=200)
    assert r2.status == "DONE" and r2.stat_id != r.stat_id            # family inputs changed (new job)
    s2 = r2.summary
    assert (s2["n_trials"], s2["var_sr_count"], s2["family_inputs"]["distinct_config_periods"]) == before


def test_inputs_are_checked(opt, monkeypatch):
    rid, fid = mixed_round(opt, monkeypatch, days=3)
    assert run_stats(opt.ws, "or_other", fid).status == "FILTER_ROUND_MISMATCH"
    assert run_stats(opt.ws, rid, "fl_unknown").status == "UNKNOWN_FILTER"
    assert run_stats(opt.ws, rid, fid, ["cd_not_here"]).status == "INPUT_INVALID"
    assert run_stats(opt.ws, rid, fid, reps=10).status == "INPUT_INVALID"


def test_cli_run_and_show(opt, monkeypatch):
    rid, fid = mixed_round(opt, monkeypatch)
    ok = by_params(opt, rid)[(20, 30)]
    runner = CliRunner()
    res = runner.invoke(app, ["stats", "run", rid, "--filter", fid, "--candidate", ok, "--reps", "200",
                              "--workspace", str(opt.ws)])
    assert res.exit_code == 0, res.output
    for text in ("N (family", "V[SR]", "SR0:", "DSR conservative", "N_eff", "DISCOVERY_IN_SAMPLE",
                 "known limitations", "AR(2)", "warnings and ranking only"):
        assert text in res.output, text
    st = next(line.split(": ")[1] for line in res.output.splitlines() if line.startswith("stat_id:"))
    one = runner.invoke(app, ["stats", "show", st, "--candidate", ok, "--workspace", str(opt.ws)])
    assert one.exit_code == 0, one.output
    assert "sharpe 90% CI" in one.output and "resampled max DD" in one.output and "dsr_conservative:" in one.output
    summary = runner.invoke(app, ["stats", "show", st, "--workspace", str(opt.ws), "--json"])
    assert summary.exit_code == 0 and json.loads(summary.output)["stat_id"] == st
