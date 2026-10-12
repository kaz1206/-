"""Study A pipeline equivalence (SPEC §3.5): the SAME synthetic rounds through the production CLI pipeline
(optimize -> filter -> robust -> stats -> select -> validate lock/run, on the fake terminal replaying the series) and
through the in-memory study-A path must give identical decisions, reason codes and values, P4 to P8a.

Rounds (fixed rule, SPEC §3.5): equivalence base seed; the first 3 H0 rounds with at least one P4 pass and the first
3 E1 rounds with at least one selected candidate (so P8a runs). Slow (minutes): runs when RL_STUDYA_EQUIVALENCE=1.
RL_STUDYA_EQUIV_OUT=<file> writes the comparison report.
"""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

import pytest

from robustlab.filtering.run import run_filter
from robustlab.optimization import run_optimization
from robustlab.robustness.run import run_robust
from robustlab.selection.run import run_select
from robustlab.stat_eval.run import run_stats
from robustlab.storage.db import Database
from robustlab.studya import evaluate as E
from robustlab.validation import run as vr

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "configs" / "study_a.yaml"
E1_SCALE = 2.5e-4          # any edge that produces selections; the equivalence does not depend on its size
STAT_SEED = 20261183
MAX_SCAN = 40

pytestmark = pytest.mark.skipif(os.environ.get("RL_STUDYA_EQUIVALENCE") != "1",
                                reason="slow study-A equivalence check (set RL_STUDYA_EQUIVALENCE=1)")


def _series_doc(st, p) -> tuple[dict, dict, dict]:
    cells = [(L, H) for L in st.lookbacks for H in st.holds]
    starts = {name: f"{st.days[w.s_start // st.spd]:%Y-%m-%d}" for name, w in st.windows.items()}
    d_res = {c: E.run_cell(st, p, st.windows["D"], *c) for c in cells}
    v_res = {(c, n): E.run_cell(st, p, st.windows[n], *c) for c in cells for n in ("V0", "V1")}
    doc = {"axes": ["InpLookback", "InpHoldBars"], "cells": {}}
    for c in cells:
        key = f"{c[0]}_{c[1]}"
        doc["cells"][key] = {}
        for n, r in (("D", d_res[c]), ("V0", v_res[(c, "V0")]), ("V1", v_res[(c, "V1")])):
            doc["cells"][key][starts[n]] = {"stats": r.stats, "tracking": r.tracking, "daily": r.daily,
                                            "deals": r.deals}
    return doc, d_res, v_res


def _workspace_files(cfg_dir: Path, tag: str) -> tuple[Path, Path]:
    (cfg_dir / "strategies").mkdir(exist_ok=True)
    (cfg_dir / "strategies" / "tsmom.yaml").write_text(textwrap.dedent("""
        strategy_name: RL_TSMom
        expert: RobustLab\\RL_TSMom
        telemetry_version: "3"
        inputs:
          - {name: InpLookback, type: int}
          - {name: InpHoldBars, type: int}
          - {name: InpLots, type: double}
    """))
    study = cfg_dir / f"study_{tag}.yaml"
    study.write_text(textwrap.dedent(f"""
        study_id: studya_equiv_{tag}
        strategy_file: strategies/tsmom.yaml
        symbol: EURUSD
        timeframe: H4
        from_date: 2001-01-02
        to_date: 2014-01-01
        param_space:
          InpLookback: {{start: 126, step: 126, stop: 1512}}
          InpHoldBars: {{start: 6, step: 6, stop: 30}}
        fixed: {{InpLots: 0.10}}
        tester: {{model: OHLC_M1, execution_delay_ms: 0, deposit: 10000, currency: USD, leverage: 100}}
        max_passes_per_job: 2000
        coverage_tolerance_days: 7
    """))
    plan = cfg_dir / f"plan_{tag}.yaml"
    plan.write_text(textwrap.dedent("""
        label: studya_equiv
        strategy_file: strategies/tsmom.yaml
        periods:
          - {name: V0, from: 2016-01-11, to: 2017-12-22}
          - {name: V1, from: 2019-07-08, to: 2019-12-20}
    """))
    return study, plan


def _production(env, monkeypatch, tag: str, doc: dict) -> dict:
    cfg_dir = env.req.parent.parent
    series = cfg_dir / f"series_{tag}.json"
    series.write_text(json.dumps(doc))
    monkeypatch.setenv("FAKE_SERIES_FILE", str(series))
    monkeypatch.setenv("FAKE_MODE", "ok")
    study, plan = _workspace_files(cfg_dir, tag)
    (env.data / "MQL5" / "Experts" / "RobustLab" / "RL_TSMom.ex5").write_bytes(b"tsmom-ex5")
    ws = env.ws.parent / f"ws_{tag}"
    o = run_optimization(study, env.terminal_cfg, env.partition, ws)
    assert o.status == "SUCCEEDED", o
    rid = o.details["round_id"]
    f = run_filter(ws, rid, ROOT / "configs" / "strategy_profiles.yaml", "swing_trend")
    r = run_robust(ws, rid, f.filter_id)
    s = run_stats(ws, rid, f.filter_id, seed=STAT_SEED)
    sel = run_select(ws, rid, f.filter_id, r.robust_id, s.stat_id, ROOT / "configs" / "selection_profiles.yaml")
    assert "DONE" in (f.status, r.status, s.status, sel.status) and sel.status == "DONE", (f, r, s, sel)
    db = Database(ws / "robustlab.sqlite")
    try:
        out = {"cd": {}, "p4": {}, "p5": {}, "p6": {}, "p7": {}, "p8a": {}, "plan_id": None}
        for row in db.all("SELECT candidate_id, params_json FROM candidate"):
            pr = json.loads(row["params_json"])
            out["cd"][(int(pr["InpLookback"]), int(pr["InpHoldBars"]))] = row["candidate_id"]
        for row in db.all("SELECT candidate_id, outcome, reason_codes_json FROM decision WHERE filter_id = ?",
                          (f.filter_id,)):
            out["p4"][row["candidate_id"]] = (row["outcome"], json.loads(row["reason_codes_json"]))
        for row in db.all("SELECT candidate_id, values_json FROM evaluation WHERE robust_id = ?", (r.robust_id,)):
            v = json.loads(row["values_json"])
            out["p5"][row["candidate_id"]] = (v.get("robust_score"), v.get("region_status"))
        for row in db.all("SELECT candidate_id, values_json FROM evaluation WHERE stat_id = ?", (s.stat_id,)):
            v = json.loads(row["values_json"])
            b = v.get("bootstrap") or {}
            dd = b["stationary"]["max_dd_distribution"]["p95"] if b.get("status") == "OK" else None
            out["p6"][row["candidate_id"]] = ((v.get("dsr") or {}).get("dsr_conservative"), dd)
        stx = db.one("SELECT n_trials, var_sr FROM statistics_execution WHERE stat_id = ?", (s.stat_id,))
        out["n_trials"], out["var_sr"] = stx["n_trials"], stx["var_sr"]
        for row in db.all("SELECT candidate_id, outcome, reason_codes_json, details_json FROM decision WHERE sel_id = ?",
                          (sel.sel_id,)):
            det = json.loads(row["details_json"])
            out["p7"][row["candidate_id"]] = (row["outcome"], json.loads(row["reason_codes_json"]),
                                              det["rank"]["selected_rank"])
        selected = json.loads(db.one("SELECT selected_json FROM selection_execution WHERE sel_id = ?",
                                     (sel.sel_id,))["selected_json"])
    finally:
        db.close()
    if selected:
        lk = vr.lock(ws, sel.sel_id, plan, ROOT / "configs" / "cost_stress.yaml", env.partition)
        assert lk.status == "LOCKED", lk
        rn = vr.run(ws, lk.plan_id, env.terminal_cfg, env.partition)
        assert rn.status == "DONE", rn
        out["plan_id"] = lk.plan_id
        db = Database(ws / "robustlab.sqlite")
        try:
            for row in db.all("SELECT candidate_id, values_json FROM evaluation WHERE plan_id = ?", (lk.plan_id,)):
                v = json.loads(row["values_json"])
                gates = {g["code"]: g["status"] for g in v["gates"]}
                out["p8a"][row["candidate_id"]] = {"p": v["evidence"]["p"] if "evidence" in v else None,
                                                   "gates": gates}
            for row in db.all("SELECT candidate_id, outcome, reason_codes_json FROM decision WHERE plan_id = ?",
                              (lk.plan_id,)):
                out["p8a"].setdefault(row["candidate_id"], {})["decision"] = (row["outcome"],
                                                                              json.loads(row["reason_codes_json"]))
        finally:
            db.close()
    return out


def _compare(prod: dict, mem: dict) -> list[str]:
    diffs = []
    rows = {r["candidate_id"]: r for r in mem["rows"]}
    for cd, r in rows.items():
        if prod["p4"][cd] != (r["p4"]["outcome"], r["p4"]["reasons"]):
            diffs.append(f"P4 {cd}: {prod['p4'][cd]} vs {r['p4']['outcome'], r['p4']['reasons']}")
        rs, st_ = prod["p5"][cd]
        if st_ != r["p5"]["region_status"] or (rs is None) != (r["p5"]["robust_score"] is None) or (
                rs is not None and abs(rs - r["p5"]["robust_score"]) > 1e-12):
            diffs.append(f"P5 {cd}: {prod['p5'][cd]} vs {r['p5']}")
        dsr_p, dd_p = prod["p6"][cd]
        dsr_m, dd_m = r["p6"]["dsr_conservative"], r["p6"]["max_dd_p95"]
        if (dsr_p is None) != (dsr_m is None) or (dsr_p is not None and round(dsr_p, 9) != round(dsr_m, 9)):
            diffs.append(f"P6 DSR {cd}: {dsr_p} vs {dsr_m}")
        if (dd_p is None) != (dd_m is None) or (dd_p is not None and round(dd_p, 2) != round(dd_m, 2)):
            diffs.append(f"P6 DD {cd}: {dd_p} vs {dd_m}")
        if cd in prod["p7"] or "p7" in r:
            m7 = (r["p7"]["outcome"], r["p7"]["reasons"], r["p7"]["rank"]) if "p7" in r else None
            if prod["p7"].get(cd) != m7:
                diffs.append(f"P7 {cd}: {prod['p7'].get(cd)} vs {m7}")
        if cd in prod["p8a"] or "p8a" in r:
            p = prod["p8a"].get(cd, {})
            m = r.get("p8a")
            if m is None or p.get("decision") != (m["outcome"], m["reasons"]) or p.get("p") is None or \
                    abs(p["p"] - m["p"]) > 1e-12:
                diffs.append(f"P8a {cd}: {p} vs {m}")
    return diffs


def test_production_and_in_memory_paths_agree(env, monkeypatch):
    st = E.setup(CFG)
    report = {"stat_seed": STAT_SEED, "e1_scale": E1_SCALE, "rounds": []}
    for kind, edge, need in (("H0", None, "p4_pass"), ("E1", "E1", "selected")):
        found = 0
        for i in range(MAX_SCAN):
            if found == 3:
                break
            p = E.round_path(st, 20261181, i, edge, E1_SCALE if edge else 0.0, 20261182 if edge else None)
            doc, d_res, v_res = _series_doc(st, p)
            probe = E.evaluate_round(st, p, E.synthetic_ids(st, "equiv-probe", i), n_trials=60, var_sr=None,
                                     d_results=d_res, v_results=v_res)
            if probe[need] == 0:
                continue
            found += 1
            prod = _production(env, monkeypatch, f"{kind}{i}", doc)
            rid = E.RoundIds(prod["cd"], STAT_SEED, prod["plan_id"] or "vp_none")
            mem = E.evaluate_round(st, p, rid, n_trials=prod["n_trials"], var_sr=prod["var_sr"], d_results=d_res,
                                   v_results=v_res)
            diffs = _compare(prod, mem)
            report["rounds"].append({"kind": kind, "round": i, "p4_pass": mem["p4_pass"], "selected": mem["selected"],
                                     "p8a_pass": mem["p8a_pass"], "n_trials": prod["n_trials"],
                                     "candidates_compared": len(mem["rows"]), "differences": diffs})
        assert found == 3, f"only {found} {kind} rounds qualified in {MAX_SCAN}"
    out = os.environ.get("RL_STUDYA_EQUIV_OUT")
    if out:
        Path(out).write_text(json.dumps(report, indent=1))
    bad = [r for r in report["rounds"] if r["differences"]]
    assert not bad, json.dumps(bad, indent=1)[:4000]
