"""P5 end to end on a fake round with idle and bankrupt passes (P5_PLAN §7, checks 1 and 2)."""

import json
from pathlib import Path

from typer.testing import CliRunner

from robustlab.cli.main import app
from robustlab.filtering.run import run_filter
from robustlab.robustness.run import run_robust
from robustlab.storage.db import Database

PROFILES = Path(__file__).resolve().parents[2] / "configs" / "strategy_profiles.yaml"


def q(env, sql, params=()):
    db = Database(env.ws / "robustlab.sqlite")
    try:
        return [dict(r) for r in db.all(sql, params)]
    finally:
        db.close()


def mixed_round(opt):
    # fast 20..40 x slow 20..40: fast >= slow trades nothing, (fast 20, slow 40) goes bankrupt
    out = opt.run_opt(opt.study(fast=(20, 10, 40), slow=(20, 10, 40)), mode="mixed")
    assert out.status == "SUCCEEDED", out
    rid = out.details["round_id"]
    f = run_filter(opt.ws, rid, PROFILES, "intraday")
    assert f.status == "DONE" and f.summary["passed"] == 0
    return rid, f.filter_id


def test_every_candidate_is_evaluated_and_minus_inf_causes_are_kept(opt):
    rid, fid = mixed_round(opt)
    r = run_robust(opt.ws, rid, fid)
    assert r.status == "DONE", r
    vals = {e["candidate_id"]: json.loads(e["values_json"]) for e in
            q(opt, "SELECT candidate_id, values_json FROM evaluation WHERE robust_id = ?", (r.robust_id,))}
    # check 1: 0 filter passes, yet every candidate (9) has robustness values
    assert len(vals) == 9 == r.summary["candidates"]
    assert r.summary["regions"] == [] and r.summary["regions_reason"] == "NO_FILTER_PASS"
    assert {v["region_status"] for v in vals.values()} == {"NOT_FILTER_PASS"}
    # check 2: causes kept apart, counts consistent with the grid
    by_index = {tuple(v["grid_index"]): v for v in vals.values()}
    assert r.summary["score_status_counts"] == {"BANKRUPTCY": 1, "NO_TRADES": 6, "OK": 2}
    assert by_index[(0, 2)]["score_status"] == "BANKRUPTCY"      # fast 20, slow 40
    assert by_index[(1, 1)]["score_status"] == "NO_TRADES"       # fast 30, slow 30
    assert by_index[(0, 1)]["score_status"] == "OK"              # fast 20, slow 30
    status = {k: v["score_status"] for k, v in by_index.items()}
    for (i, j), v in by_index.items():
        want = {}
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                n = (i + di, j + dj)
                if (di, dj) != (0, 0) and n in status:
                    want[status[n]] = want.get(status[n], 0) + 1
        assert v["neighbour_status_counts"] == want, (i, j)
        for key in ("robust_score", "peak_gap", "peak_ratio"):
            assert v[key] is not None or v[f"{key}_reason"], (i, j, key)
    # stored evaluations carry the method and version
    v = by_index[(0, 1)]
    assert v["config"]["quantile_method"] == "inverted_cdf" and v["version"] == "g1" and v["score_name"] == "daily_sharpe"
    assert v["subperiod"]["years"] == 2  # the fake period ends on 2023-01-01
    # idempotent
    again = run_robust(opt.ws, rid, fid)
    assert again.status == "ALREADY_DONE" and again.robust_id == r.robust_id
    assert len(q(opt, "SELECT * FROM evaluation WHERE robust_id = ?", (r.robust_id,))) == 9


def test_wrong_round_is_refused(opt):
    _, fid = mixed_round(opt)
    assert run_robust(opt.ws, "or_other", fid).status == "FILTER_ROUND_MISMATCH"


def test_cli_run_show_map(opt):
    rid, fid = mixed_round(opt)
    runner = CliRunner()
    res = runner.invoke(app, ["robust", "run", rid, "--filter", fid, "--workspace", str(opt.ws)])
    assert res.exit_code == 0, res.output
    assert "NO_FILTER_PASS" in res.output and "BANKRUPTCY" in res.output and "no pass/fail" in res.output
    rb = next(line.split(": ")[1] for line in res.output.splitlines() if line.startswith("robust_id:"))
    m = runner.invoke(app, ["robust", "map", rb, "--workspace", str(opt.ws)])
    assert m.exit_code == 0, m.output
    rows = [line for line in m.output.splitlines() if "|" in line]
    assert len(rows) == 3 and "B" in m.output and "_" in m.output
    cd = q(opt, "SELECT candidate_id FROM evaluation WHERE robust_id = ? LIMIT 1", (rb,))[0]["candidate_id"]
    one = runner.invoke(app, ["robust", "show", rb, "--candidate", cd, "--workspace", str(opt.ws)])
    assert one.exit_code == 0 and "score_status:" in one.output and "neighbour_status_counts:" in one.output
