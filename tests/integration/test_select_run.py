"""P7 end to end on a fake round with P4 passes: filter -> robustness -> statistics -> selection (P7_PLAN §6.2)."""

import json
import random
from pathlib import Path

from typer.testing import CliRunner

from robustlab.cli.main import app
from robustlab.filtering.run import run_filter
from robustlab.robustness.run import run_robust
from robustlab.selection import core
from robustlab.selection.run import run_select
from robustlab.stat_eval.run import run_stats
from robustlab.storage.db import Database

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "configs" / "strategy_profiles.yaml"
SEL_PROFILES = ROOT / "configs" / "selection_profiles.yaml"


def q(env, sql, params=()):
    db = Database(env.ws / "robustlab.sqlite")
    try:
        return [dict(r) for r in db.all(sql, params)]
    finally:
        db.close()


def pipeline(opt, monkeypatch, fast=(10, 5, 40), slow=(20, 5, 60), mode="mixed"):
    monkeypatch.setenv("FAKE_OPT_DAYS", "120")
    monkeypatch.setenv("FAKE_OPT_EDGE", "1")
    out = opt.run_opt(opt.study(fast=fast, slow=slow), mode=mode)
    assert out.status == "SUCCEEDED", out
    rid = out.details["round_id"]
    f = run_filter(opt.ws, rid, PROFILES, "intraday")
    assert f.status == "DONE"
    r = run_robust(opt.ws, rid, f.filter_id)
    assert r.status == "DONE"
    s = run_stats(opt.ws, rid, f.filter_id, reps=200)
    assert s.status == "DONE", s
    return rid, f.filter_id, r.robust_id, s.stat_id, f.summary


def decisions(opt, sel_id):
    return {d["candidate_id"]: d | {"reasons": json.loads(d["reason_codes_json"]),
                                    "warnings": json.loads(d["warning_codes_json"]),
                                    "details": json.loads(d["details_json"])}
            for d in q(opt, "SELECT * FROM decision WHERE sel_id = ?", (sel_id,))}


def test_selection_end_to_end(opt, monkeypatch):
    rid, fid, rb, st, fsum = pipeline(opt, monkeypatch)
    assert fsum["passed"] > 0
    before_filter = q(opt, "SELECT COUNT(*) AS n FROM decision WHERE filter_id = ?", (fid,))[0]["n"]
    out = run_select(opt.ws, rid, fid, rb, st, SEL_PROFILES)
    assert out.status == "DONE", out
    s = out.summary
    ds = decisions(opt, out.sel_id)
    # one decision per P4 pass, nothing for P4 failures
    p4_pass = {r["candidate_id"] for r in q(opt, "SELECT candidate_id FROM decision WHERE filter_id = ? AND "
                                                 "stage = 'FILTER' AND outcome = 'PASS'", (fid,))}
    assert set(ds) == p4_pass and s["eligible_p4_pass"] == len(p4_pass)
    # selection decisions never show up under the filter id (P4-P6 queries stay unchanged)
    assert q(opt, "SELECT COUNT(*) AS n FROM decision WHERE filter_id = ?", (fid,))[0]["n"] == before_filter
    assert all(d["filter_id"] is None and d["stage"] == "SELECTION" for d in ds.values())
    # budget and one per region
    sel = [c for c, d in ds.items() if d["outcome"] == "SELECTED"]
    assert 1 <= len(sel) <= 5 and len(sel) == s["selected_count"]
    regions = [d["details"]["region_id"] for c, d in ds.items() if d["outcome"] == "SELECTED"]
    assert len(regions) == len(set(regions))
    # every outcome has its reason; DSR never decides (R4)
    for d in ds.values():
        assert (d["outcome"] == "SELECTED") == (d["reasons"] == [])
        assert not set(d["reasons"]) & core.DSR_CODES
        assert d["component_name"] == "selection" and d["component_version"].startswith("region_pareto_v1")
        assert d["details"]["neighbourhood"]["p5"] is not None and d["details"]["neighbourhood"]["r3"] is not None
    # gate values and reasons agree for every decision (a bankrupt neighbour always fails, R3)
    for d in ds.values():
        gates = {g["gate"]: g for g in d["details"]["gates"]}
        assert len(gates) == 5
        if gates["neighbour_ruin"]["value"]:
            assert "NEIGHBORHOOD_UNSTABLE" in d["reasons"]
        for g in gates.values():
            if g["status"] == "FAIL":
                assert g["code"] in d["reasons"]
    assert s["reason_counts"].get("REDUNDANT_IN_REGION", 0) > 0 and s["outcomes"].get("FAIL", 0) > 0
    # selected order = integrated rank
    ranks = [ds[c]["details"]["rank"]["selected_rank"] for c in (x["candidate_id"] for x in s["selected"])]
    assert ranks == list(range(1, len(ranks) + 1))
    assert "smooth chance mound" in " ".join(s["known_limitations"])
    # idempotent; another profile is a revision
    again = run_select(opt.ws, rid, fid, rb, st, SEL_PROFILES)
    assert again.status == "ALREADY_DONE" and again.sel_id == out.sel_id
    alt = opt.ws.parent / "sel.yaml"
    alt.write_text(SEL_PROFILES.read_text().replace("budget: 5", "budget: 1"))
    rev = run_select(opt.ws, rid, fid, rb, st, alt)
    assert rev.status == "DONE" and rev.sel_id != out.sel_id and rev.summary["revision_no"] == 1
    assert rev.summary["selected_count"] == 1
    assert rev.summary["selected"][0]["candidate_id"] == s["selected"][0]["candidate_id"]
    assert all("SELECTION_REVISED" in d["warnings"] for d in decisions(opt, rev.sel_id).values())


def test_dsr_changes_only_reorder(opt, monkeypatch):
    """Changing every DSR value cannot turn a FAIL into a pass or back (R4); only the order may change."""
    from robustlab.selection import run as srun

    rid, fid, rb, st, _ = pipeline(opt, monkeypatch)
    out = run_select(opt.ws, rid, fid, rb, st, SEL_PROFILES)
    base = {c: d["outcome"] == "FAIL" for c, d in decisions(opt, out.sel_id).items()}
    real_load = srun._load

    def scrambled(*a):
        inp = real_load(*a)
        rng = random.Random(3)
        for v in inp["p6"].values():
            if v.get("dsr", {}).get("dsr_conservative") is not None:
                v["dsr"]["dsr_conservative"] = rng.random()
        return inp

    monkeypatch.setattr(srun, "_load", scrambled)
    alt = opt.ws.parent / "sel2.yaml"
    alt.write_text(SEL_PROFILES.read_text().replace("description: \"", "description: \"x "))
    out2 = run_select(opt.ws, rid, fid, rb, st, alt)
    assert out2.status == "DONE"
    assert {c: d["outcome"] == "FAIL" for c, d in decisions(opt, out2.sel_id).items()} == base


def test_no_p4_pass_gives_no_eligible(opt, monkeypatch):
    monkeypatch.setenv("FAKE_OPT_DAYS", "120")
    out = opt.run_opt(opt.study(fast=(20, 10, 40), slow=(20, 10, 40)), mode="mixed")
    rid = out.details["round_id"]
    f = run_filter(opt.ws, rid, PROFILES, "intraday")
    assert f.summary["passed"] == 0
    r = run_robust(opt.ws, rid, f.filter_id)
    s = run_stats(opt.ws, rid, f.filter_id, reps=200)
    sel = run_select(opt.ws, rid, f.filter_id, r.robust_id, s.stat_id, SEL_PROFILES)
    assert sel.status == "DONE" and sel.summary["selected_count"] == 0
    assert sel.summary["outcome_reason"].startswith("NO_ELIGIBLE")
    assert q(opt, "SELECT COUNT(*) AS n FROM decision WHERE sel_id = ?", (sel.sel_id,))[0]["n"] == 0


def test_inputs_are_checked(opt, monkeypatch):
    rid, fid, rb, st, _ = pipeline(opt, monkeypatch, fast=(10, 10, 30), slow=(20, 10, 40))
    assert run_select(opt.ws, "or_other", fid, rb, st, SEL_PROFILES).status == "INPUT_INVALID"
    assert run_select(opt.ws, rid, fid, "rb_unknown", st, SEL_PROFILES).status == "INPUT_INVALID"
    assert run_select(opt.ws, rid, fid, rb, st, SEL_PROFILES, "nope").status == "CONFIG_INVALID"
    # a statistics execution without the bootstrap of a P4 pass is refused (P6 run with no P4 pass targets)
    from robustlab.selection import run as srun

    real_load = srun._load

    def no_boot(*a):
        inp = real_load(*a)
        for v in inp["p6"].values():
            v["bootstrap"] = None
        return inp

    monkeypatch.setattr(srun, "_load", no_boot)
    out = run_select(opt.ws, rid, fid, rb, st, SEL_PROFILES)
    assert out.status == "INPUT_INVALID" and "--candidate" in out.message


def test_cli_run_show_and_why_not(opt, monkeypatch):
    rid, fid, rb, st, _ = pipeline(opt, monkeypatch)
    runner = CliRunner()
    res = runner.invoke(app, ["select", "run", rid, "--filter", fid, "--robust", rb, "--stats", st,
                              "--profiles", str(SEL_PROFILES), "--workspace", str(opt.ws)])
    assert res.exit_code == 0, res.output
    for text in ("P4 pass (eligible)", "selected (integrated rank order)", "known limitations",
                 "not evidence of an edge"):
        assert text in res.output, text
    sl = next(line.split(": ")[1] for line in res.output.splitlines() if line.startswith("sel_id:"))
    cd = q(opt, "SELECT candidate_id FROM decision WHERE sel_id = ? LIMIT 1", (sl,))[0]["candidate_id"]
    one = runner.invoke(app, ["select", "show", sl, "--candidate", cd, "--workspace", str(opt.ws)])
    assert one.exit_code == 0 and "neighbourhood (R3" in one.output and "P5 standard" in one.output
    w = runner.invoke(app, ["why-not", cd, "--workspace", str(opt.ws)])
    assert w.exit_code == 0 and "selection:" in w.output and "filter:" in w.output
